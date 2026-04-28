"""
Runtime: glues together AppConfig, EmbeddedModbusServer and SensorSimulator.

A single Runtime instance owns:
  - the live AppConfig
  - an EmbeddedModbusServer (started/stopped on demand)
  - a SensorSimulator     (started/stopped on demand)
  - a ring buffer of last updates per sensor for the UI

Live edits to the configuration are reloaded into the server's fault rules
and the simulator's generator state without dropping connections.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

import yaml

from models import AppConfig, RegisterType
from modbus_server import EmbeddedModbusServer
from simulator.scheduler import EmbeddedServerSink, SensorSimulator
from utils.logging import get_logger

logger = get_logger(__name__)


class Runtime:
    """Stateful container shared by the FastAPI handlers."""

    def __init__(self, config: AppConfig, persist_path: Optional[Path] = None):
        self._config = config
        self._persist_path = persist_path
        self._lock = threading.Lock()
        self._server: Optional[EmbeddedModbusServer] = None
        self._simulator: Optional[SensorSimulator] = None
        self._latest: Dict[str, dict] = {}
        self._events: Deque[dict] = deque(maxlen=200)

    # ---- config access ---------------------------------------------------

    @property
    def config(self) -> AppConfig:
        return self._config

    def replace_config(self, config: AppConfig) -> None:
        """
        Atomically swap the configuration and propagate to running components.

        If the new configuration introduces unit_ids unknown to the running
        server, or requires register blocks larger than what's allocated,
        the server is restarted (the simulator is paused around the bounce).
        """
        with self._lock:
            self._config = config

        if self._server is not None and self._server.is_running():
            if self._server.needs_restart_for(config):
                logger.info("Server restart required for new config")
                was_simulating = self._simulator is not None and self._simulator.is_running()
                if was_simulating:
                    self._simulator.stop()
                self._server.stop()
                self._server = EmbeddedModbusServer(config)
                self._server.start()
                if was_simulating:
                    self._simulator = SensorSimulator(
                        config, EmbeddedServerSink(self._server),
                        on_update=self._record_update,
                    )
                    self._simulator.start()
            else:
                self._server.config = config
                self._server.reload_rules(config.sensors)

        if self._simulator is not None and self._simulator.is_running():
            self._simulator.reload_config(config)

        self._save_to_disk()

    def _save_to_disk(self) -> None:
        if not self._persist_path:
            return
        try:
            data = self._config.model_dump(mode="json")
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._persist_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        except Exception as e:
            logger.error(f"Could not persist config to {self._persist_path}: {e}")

    # ---- modbus server ---------------------------------------------------

    def server_status(self) -> dict:
        running = self._server is not None and self._server.is_running()
        slaves: List[dict] = []
        if self._server is not None:
            sizes = self._server.slave_sizes()
            for uid in sorted(sizes.keys()):
                slaves.append({
                    "unit_id": uid,
                    # Per-space sizes for the UI status panel.
                    "sizes": {rt.value: sz for rt, sz in sizes[uid].items()},
                    # Aggregate count for compact display.
                    "registers": sum(sizes[uid].values()),
                })
        return {
            "running": running,
            "host": self._config.server.host,
            "port": self._config.server.port,
            "default_unit_id": self._config.server.default_unit_id,
            "slaves": slaves,
        }

    def start_server(self) -> None:
        if self._server is not None and self._server.is_running():
            return
        if self._server is not None:
            self._server.stop()
        self._server = EmbeddedModbusServer(self._config)
        try:
            self._server.start()
        except RuntimeError:
            # Failed bind / startup: drop the dead server so the next attempt
            # rebuilds it cleanly. Re-raise so the caller sees the reason.
            self._server = None
            raise
        time.sleep(0.2)

    def stop_server(self) -> None:
        # Stop simulator first to avoid writes against a dead sink.
        if self._simulator is not None and self._simulator.is_running():
            self.stop_simulator()
        if self._server is not None:
            self._server.stop()
            self._server = None

    def kick_clients(self) -> None:
        """Drop all current TCP connections (for testing client reconnect)."""
        if self._server is None or not self._server.is_running():
            raise RuntimeError("Server is not running")
        self._server.kick()

    def inject_spike(
        self, sensor_id: str, measurement_name: str,
        value: float, duration_seconds: float,
    ) -> None:
        if self._simulator is None or not self._simulator.is_running():
            raise RuntimeError("Simulator is not running")
        self._simulator.inject_spike(sensor_id, measurement_name, value, duration_seconds)

    def clear_spike(self, sensor_id: str, measurement_name: str) -> bool:
        if self._simulator is None:
            return False
        return self._simulator.clear_spike(sensor_id, measurement_name)

    # ---- simulator -------------------------------------------------------

    def simulator_status(self) -> dict:
        running = self._simulator is not None and self._simulator.is_running()
        return {"running": running}

    def start_simulator(self) -> bool:
        if self._simulator is not None and self._simulator.is_running():
            return True
        if self._server is None or not self._server.is_running():
            self.start_server()
        sink = EmbeddedServerSink(self._server)
        self._simulator = SensorSimulator(
            self._config, sink, on_update=self._record_update
        )
        return self._simulator.start()

    def stop_simulator(self) -> None:
        if self._simulator is not None:
            self._simulator.stop()

    def _record_update(self, sensor_id: str, updates: List[dict]) -> None:
        ts = time.time()
        for u in updates:
            key = f"{sensor_id}:{u['name']}"
            self._latest[key] = {
                "sensor_id": sensor_id,
                "timestamp": ts,
                **u,
            }
            self._events.appendleft({
                "sensor_id": sensor_id,
                "timestamp": ts,
                **u,
            })

    # ---- snapshots for the UI -------------------------------------------

    def latest_values(self) -> List[dict]:
        return list(self._latest.values())

    def recent_events(self, limit: int = 50) -> List[dict]:
        return list(self._events)[:limit]

    def slave_dump(self) -> List[dict]:
        """
        Snapshot of every (unit_id, register_type) data block for the debug
        view. The UI groups by unit_id and shows one table per non-empty
        address space.
        """
        if self._server is None or not self._server.is_running():
            return []
        all_sizes = self._server.slave_sizes()
        out: List[dict] = []
        for uid in self._server.supported_unit_ids():
            sizes = all_sizes.get(uid, {})
            spaces = []
            for rt, size in sizes.items():
                values = self._server.read_block(uid, rt, 0, size)
                spaces.append({
                    "register_type": rt.value,
                    "size": size,
                    "registers": values,
                })
            sensors = []
            for sensor in self._config.sensors:
                if sensor.unit_id != uid:
                    continue
                measurements = []
                for m in sensor.measurements:
                    base = sensor.base_address + m.offset
                    measurements.append({
                        "name": m.name,
                        "address": base,
                        "register_count": m.register_count,
                        "register_type": m.register_type.value,
                        "data_type": m.data_type.value,
                        "scale": m.scale,
                        "unit": m.unit,
                    })
                sensors.append({
                    "id": sensor.id,
                    "base_address": sensor.base_address,
                    "byte_order": sensor.byte_order,
                    "word_order": sensor.word_order,
                    "measurements": measurements,
                })
            out.append({
                "unit_id": uid,
                "spaces": spaces,
                "sensors": sensors,
            })
        return out

    def shutdown(self) -> None:
        try:
            self.stop_simulator()
        finally:
            self.stop_server()
