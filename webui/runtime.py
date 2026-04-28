"""
Runtime: glues together AppConfig, multiple EmbeddedModbusServer instances
and one SensorSimulator per server.

A single Runtime instance owns:
  - the live AppConfig
  - one EmbeddedModbusServer per ServerConfig (started/stopped on demand)
  - one SensorSimulator per ServerConfig
  - a ring buffer of last updates per (server_id, sensor_id, measurement)

Live edits to the configuration are reconciled per-server:
  - new servers are created (and auto-started if `auto_start` is set)
  - removed servers are stopped and dropped
  - existing servers are either restarted (if structural change) or live-updated
"""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import yaml

from models import AppConfig, ServerConfig
from modbus_server import EmbeddedModbusServer
from simulator.scheduler import EmbeddedServerSink, SensorSimulator
from utils.logging import get_logger

logger = get_logger(__name__)


class Runtime:
    """Stateful container shared by the FastAPI handlers."""

    def __init__(self, config: AppConfig, persist_path: Optional[Path] = None):
        self._config = config
        self._persist_path = persist_path
        # RLock so replace_config (holding the lock) can call lifecycle methods
        # that also acquire it. All public mutators take this lock to keep
        # _servers / _simulators / _config in sync.
        self._lock = threading.RLock()
        self._servers: Dict[str, EmbeddedModbusServer] = {}
        self._simulators: Dict[str, SensorSimulator] = {}
        # Latest values keyed by (server_id, sensor_id, measurement_name).
        self._latest: Dict[Tuple[str, str, str], dict] = {}
        self._events: Deque[dict] = deque(maxlen=200)

        # Pre-build server objects for every configured server (not started).
        for sc in self._config.servers:
            self._servers[sc.id] = EmbeddedModbusServer(sc)

        # Auto-start servers that requested it.
        for sc in self._config.servers:
            if sc.auto_start:
                try:
                    self.start_server(sc.id)
                except Exception as e:
                    logger.error(f"auto_start failed for server '{sc.id}': {e}")

    # ---- config access ---------------------------------------------------

    @property
    def config(self) -> AppConfig:
        return self._config

    def replace_config(self, new_config: AppConfig) -> None:
        """
        Atomically swap the configuration and reconcile per-server state.

        The whole reconciliation runs under self._lock so it cannot interleave
        with start_server/stop_server/another replace_config on the same
        Runtime — internal helpers called below do NOT re-acquire the lock,
        so there is no deadlock.

        For each server:
          - removed -> stop simulator and server, drop both
          - new     -> build server (auto-start if requested)
          - kept    -> if running and structural change -> restart (keeping
                       simulator state); else live-update fault rules and
                       (if simulator running) reload its config
        """
        with self._lock:
            old_ids = {sc.id for sc in self._config.servers}
            new_ids = {sc.id for sc in new_config.servers}
            new_by_id: Dict[str, ServerConfig] = {sc.id: sc for sc in new_config.servers}
            self._config = new_config

            # Remove servers that are gone.
            for sid in old_ids - new_ids:
                try:
                    self._stop_simulator_locked(sid)
                    server = self._servers.pop(sid, None)
                    if server is not None:
                        server.stop()
                    # Purge latest values for that server.
                    self._latest = {
                        k: v for k, v in self._latest.items() if k[0] != sid
                    }
                except Exception as e:
                    logger.error(f"replace_config: stop of '{sid}' failed: {e}")

            # Add brand-new servers; build object always, auto-start if requested.
            for sid in new_ids - old_ids:
                sc = new_by_id[sid]
                self._servers[sid] = EmbeddedModbusServer(sc)
                if sc.auto_start:
                    try:
                        self.start_server(sid)
                    except Exception as e:
                        logger.error(f"auto_start failed for server '{sid}': {e}")

            # Reconcile kept servers.
            for sid in old_ids & new_ids:
                sc = new_by_id[sid]
                server = self._servers.get(sid)
                if server is None:
                    # Defensive: shouldn't happen, but rebuild.
                    self._servers[sid] = EmbeddedModbusServer(sc)
                    continue

                if server.is_running():
                    if server.needs_restart_for(sc):
                        logger.info(f"Server '{sid}' needs restart for new config")
                        was_simulating = (
                            sid in self._simulators and self._simulators[sid].is_running()
                        )
                        if was_simulating:
                            self._stop_simulator_locked(sid)
                        server.stop()
                        new_server = EmbeddedModbusServer(sc)
                        new_server.start()
                        self._servers[sid] = new_server
                        if was_simulating:
                            self._spawn_simulator(sid)
                    else:
                        server.config = sc
                        server.reload_rules(sc.sensors)
                        sim = self._simulators.get(sid)
                        if sim is not None and sim.is_running():
                            sim.reload_config(sc, tick_seconds=new_config.tick_seconds)
                else:
                    # Server stopped: rebuild so a future start uses fresh config.
                    self._servers[sid] = EmbeddedModbusServer(sc)

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

    # ---- per-server lifecycle ------------------------------------------

    def _server_or_404(self, server_id: str) -> EmbeddedModbusServer:
        server = self._servers.get(server_id)
        if server is None:
            raise KeyError(f"Unknown server '{server_id}'")
        return server

    def _server_config_or_404(self, server_id: str) -> ServerConfig:
        sc = self._config.find_server(server_id)
        if sc is None:
            raise KeyError(f"Unknown server '{server_id}'")
        return sc

    def is_server_running(self, server_id: str) -> bool:
        server = self._servers.get(server_id)
        return bool(server is not None and server.is_running())

    def is_simulator_running(self, server_id: str) -> bool:
        sim = self._simulators.get(server_id)
        return bool(sim is not None and sim.is_running())

    def start_server(self, server_id: str) -> None:
        with self._lock:
            server = self._server_or_404(server_id)
            if server.is_running():
                return
            server.start()
            time.sleep(0.1)

    def stop_server(self, server_id: str) -> None:
        with self._lock:
            # Stop simulator first to avoid writes against a dead sink.
            self._stop_simulator_locked(server_id)
            server = self._servers.get(server_id)
            if server is not None:
                server.stop()

    def kick_server(self, server_id: str) -> None:
        with self._lock:
            server = self._server_or_404(server_id)
            if not server.is_running():
                raise RuntimeError(f"Server '{server_id}' is not running")
            server.kick()

    def start_simulator(self, server_id: str) -> bool:
        with self._lock:
            self._server_config_or_404(server_id)  # 404 check
            if server_id in self._simulators and self._simulators[server_id].is_running():
                return True
            # Ensure the embedded server is up.
            if not self.is_server_running(server_id):
                self.start_server(server_id)
            self._spawn_simulator(server_id)
            return self._simulators[server_id].is_running()

    def stop_simulator(self, server_id: str) -> None:
        with self._lock:
            self._stop_simulator_locked(server_id)

    def _spawn_simulator(self, server_id: str) -> None:
        sc = self._server_config_or_404(server_id)
        server = self._server_or_404(server_id)
        sink = EmbeddedServerSink(server)
        sim = SensorSimulator(
            sc, sink,
            tick_seconds=self._config.tick_seconds,
            on_update=lambda sensor_id, updates, _sid=server_id: self._record_update(_sid, sensor_id, updates),
        )
        self._simulators[server_id] = sim
        sim.start()

    def _stop_simulator_locked(self, server_id: str) -> None:
        sim = self._simulators.pop(server_id, None)
        if sim is not None:
            sim.stop()

    # ---- bulk lifecycle ------------------------------------------------

    def start_all_servers(self) -> None:
        for sid in list(self._servers.keys()):
            try:
                self.start_server(sid)
            except Exception as e:
                logger.error(f"start_all: '{sid}' failed: {e}")

    def stop_all_servers(self) -> None:
        for sid in list(self._servers.keys()):
            self.stop_server(sid)

    def start_all_simulators(self) -> None:
        for sid in list(self._servers.keys()):
            try:
                self.start_simulator(sid)
            except Exception as e:
                logger.error(f"start_all_simulators: '{sid}' failed: {e}")

    def stop_all_simulators(self) -> None:
        for sid in list(self._simulators.keys()):
            self.stop_simulator(sid)

    # ---- spike injection -----------------------------------------------

    def inject_spike(
        self, server_id: str, sensor_id: str, measurement_name: str,
        value: float, duration_seconds: float,
    ) -> None:
        sim = self._simulators.get(server_id)
        if sim is None or not sim.is_running():
            raise RuntimeError(f"Simulator for server '{server_id}' is not running")
        sim.inject_spike(sensor_id, measurement_name, value, duration_seconds)

    def clear_spike(
        self, server_id: str, sensor_id: str, measurement_name: str,
    ) -> bool:
        sim = self._simulators.get(server_id)
        if sim is None:
            return False
        return sim.clear_spike(sensor_id, measurement_name)

    # ---- snapshots for the UI ------------------------------------------

    def server_status(self, server_id: str) -> dict:
        sc = self._server_config_or_404(server_id)
        server = self._servers.get(server_id)
        running = bool(server is not None and server.is_running())
        slaves: List[dict] = []
        if server is not None:
            sizes = server.slave_sizes()
            for uid in sorted(sizes.keys()):
                slaves.append({
                    "unit_id": uid,
                    "sizes": {rt.value: sz for rt, sz in sizes[uid].items()},
                    "registers": sum(sizes[uid].values()),
                })
        return {
            "id": sc.id,
            "label": sc.label,
            "description": sc.description,
            "host": sc.host,
            "port": sc.port,
            "default_unit_id": sc.default_unit_id,
            "auto_start": sc.auto_start,
            "running": running,
            "simulator_running": self.is_simulator_running(server_id),
            "sensor_count": len(sc.sensors),
            "slaves": slaves,
        }

    def all_servers_status(self) -> List[dict]:
        return [self.server_status(sc.id) for sc in self._config.servers]

    def latest_values(self) -> List[dict]:
        return list(self._latest.values())

    def recent_events(self, limit: int = 50) -> List[dict]:
        return list(self._events)[:limit]

    def slave_dump(self) -> List[dict]:
        """
        Per-server snapshot of every (unit_id, register_type) data block,
        for the debug view. Each entry includes the sensor/measurement
        layout that owns each address.
        """
        out: List[dict] = []
        for sc in self._config.servers:
            server = self._servers.get(sc.id)
            if server is None or not server.is_running():
                continue
            all_sizes = server.slave_sizes()
            slaves: List[dict] = []
            for uid in server.supported_unit_ids():
                sizes = all_sizes.get(uid, {})
                spaces = []
                for rt, size in sizes.items():
                    values = server.read_block(uid, rt, 0, size)
                    spaces.append({
                        "register_type": rt.value,
                        "size": size,
                        "registers": values,
                    })
                sensors_for_uid = []
                for sensor in sc.sensors:
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
                    sensors_for_uid.append({
                        "id": sensor.id,
                        "base_address": sensor.base_address,
                        "byte_order": sensor.byte_order,
                        "word_order": sensor.word_order,
                        "measurements": measurements,
                    })
                slaves.append({
                    "unit_id": uid,
                    "spaces": spaces,
                    "sensors": sensors_for_uid,
                })
            out.append({
                "server_id": sc.id,
                "host": sc.host,
                "port": sc.port,
                "slaves": slaves,
            })
        return out

    def _record_update(
        self, server_id: str, sensor_id: str, updates: List[dict],
    ) -> None:
        ts = time.time()
        for u in updates:
            key = (server_id, sensor_id, u["name"])
            entry = {
                "server_id": server_id,
                "sensor_id": sensor_id,
                "timestamp": ts,
                **u,
            }
            self._latest[key] = entry
            self._events.appendleft(entry)

    def shutdown(self) -> None:
        try:
            self.stop_all_simulators()
        finally:
            self.stop_all_servers()
