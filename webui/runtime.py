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

from models import AppConfig
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
            for uid, size in sorted(self._server.slave_sizes().items()):
                slaves.append({"unit_id": uid, "registers": size})
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
        """Snapshot of all slave register banks for the debugging view."""
        if self._server is None or not self._server.is_running():
            return []
        out: List[dict] = []
        for uid in self._server.supported_unit_ids():
            size = self._server.slave_sizes().get(uid, 0)
            registers = self._server.read_holding(uid, 0, size)
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
                "size": size,
                "registers": registers,
                "sensors": sensors,
            })
        return out

    def shutdown(self) -> None:
        try:
            self.stop_simulator()
        finally:
            self.stop_server()
