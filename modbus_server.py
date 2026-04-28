"""
Embedded Modbus TCP server with gateway-style multi-slave support.

  * One TCP listener (host:port).
  * One ModbusSlaveContext per distinct unit_id used by the sensors.
  * Each slave context has its own holding-register data block, so two
    sensors with different unit_ids may share overlapping register addresses
    without conflict (just like a gateway aggregating multiple devices).

Fault injection is keyed on (unit_id, address):
  - per-sensor latency, offline, error_rate
  - per-measurement error_rate

Fault rules can be live-reloaded; structural changes (new unit_id appearing
or disappearing) require a server restart, which the runtime takes care of.
"""
from __future__ import annotations

import asyncio
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import ServerAsyncStop, StartAsyncTcpServer

from models import AppConfig, SensorConfig
from utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class _MeasurementBand:
    sensor_id: str
    measurement_name: str
    unit_id: int
    start: int
    end: int
    error_rate: float = 0.0


@dataclass
class _SensorBand:
    sensor_id: str
    unit_id: int
    start: int
    end: int
    latency_ms: int = 0
    offline: bool = False
    error_rate: float = 0.0


@dataclass
class FaultRules:
    """Mutable, thread-safe fault rules indexed by unit_id."""
    sensor_bands: Dict[int, List[_SensorBand]] = field(default_factory=dict)
    measurement_bands: Dict[int, List[_MeasurementBand]] = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reload(self, sensors: List[SensorConfig]) -> None:
        sb: Dict[int, List[_SensorBand]] = {}
        mb: Dict[int, List[_MeasurementBand]] = {}
        for sensor in sensors:
            r = sensor.address_range()
            if r is None:
                continue
            uid = int(sensor.unit_id)
            sb.setdefault(uid, []).append(_SensorBand(
                sensor_id=sensor.id,
                unit_id=uid,
                start=r.start,
                end=r.stop - 1,
                latency_ms=int(sensor.fault.latency_ms),
                offline=bool(sensor.fault.offline),
                error_rate=float(sensor.fault.error_rate),
            ))
            for m in sensor.measurements:
                base = sensor.base_address + m.offset
                mb.setdefault(uid, []).append(_MeasurementBand(
                    sensor_id=sensor.id,
                    measurement_name=m.name,
                    unit_id=uid,
                    start=base,
                    end=base + m.register_count - 1,
                    error_rate=float(m.fault.error_rate),
                ))
        with self._lock:
            self.sensor_bands = sb
            self.measurement_bands = mb

    def evaluate(self, unit_id: int, address: int, count: int) -> Tuple[bool, int]:
        end = address + count - 1
        latency = 0
        with self._lock:
            sensors = list(self.sensor_bands.get(unit_id, []))
            measurements = list(self.measurement_bands.get(unit_id, []))

        for sb in sensors:
            if sb.end < address or sb.start > end:
                continue
            if sb.offline:
                return True, sb.latency_ms
            latency = max(latency, sb.latency_ms)
            if sb.error_rate > 0 and self.rng.random() < sb.error_rate:
                return True, latency

        for mb in measurements:
            if mb.error_rate <= 0:
                continue
            if mb.end < address or mb.start > end:
                continue
            if self.rng.random() < mb.error_rate:
                return True, latency

        return False, latency


class _UnitDataBlock(ModbusSequentialDataBlock):
    """Holding-register block bound to a specific unit_id; consults FaultRules."""

    def __init__(self, address: int, values, rules: FaultRules, unit_id: int):
        super().__init__(address, values)
        self._rules = rules
        self._unit_id = unit_id

    def validate(self, address, count=1):  # noqa: N802
        should_fault, _ = self._rules.evaluate(self._unit_id, address, count)
        if should_fault:
            return False
        return super().validate(address, count)

    def getValues(self, address, count=1):  # noqa: N802
        _, latency = self._rules.evaluate(self._unit_id, address, count)
        if latency > 0:
            time.sleep(latency / 1000.0)
        return super().getValues(address, count)


class EmbeddedModbusServer:
    """
    Multi-slave Modbus TCP server. Each unit_id used by the sensors gets its
    own holding-register space.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.rules = FaultRules()
        self.rules.reload(config.sensors)

        # Build one slave context per unit_id (always include the default
        # so the server is reachable even with zero sensors).
        unit_ids = sorted({s.unit_id for s in config.sensors} | {config.server.default_unit_id})
        min_size = max(1, config.server.register_count_min)

        # Auto-size: each unit_id gets exactly max(min_size, highest_addr+1).
        per_unit_size: Dict[int, int] = {uid: min_size for uid in unit_ids}
        for s in config.sensors:
            r = s.address_range()
            if r is None:
                continue
            need = r.stop  # range.stop is one past the highest address
            per_unit_size[s.unit_id] = max(per_unit_size.get(s.unit_id, min_size), need)

        self._blocks: Dict[int, _UnitDataBlock] = {}
        slaves: Dict[int, ModbusSlaveContext] = {}
        for uid in unit_ids:
            size = per_unit_size[uid]
            block = _UnitDataBlock(0, [0] * size, self.rules, uid)
            self._blocks[uid] = block
            slaves[uid] = ModbusSlaveContext(hr=block, zero_mode=True)
        self._slaves = slaves
        self._sizes = per_unit_size
        self.server_context = ModbusServerContext(slaves=slaves, single=False)
        self._unit_ids = set(unit_ids)

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._lock = threading.Lock()

    # ---- introspection ------------------------------------------------

    def supported_unit_ids(self) -> List[int]:
        return sorted(self._unit_ids)

    def slave_sizes(self) -> Dict[int, int]:
        return dict(self._sizes)

    def needs_restart_for(self, config: AppConfig) -> bool:
        """
        True if the running server can no longer serve the new config:
          - new unit_id appeared (or one disappeared)
          - any block needs to be larger than what we currently allocate
        """
        configured = {s.unit_id for s in config.sensors} | {config.server.default_unit_id}
        if configured != self._unit_ids:
            return True
        for s in config.sensors:
            r = s.address_range()
            if r is None:
                continue
            if r.stop > self._sizes.get(s.unit_id, 0):
                return True
        return False

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            cfg = self.config.server
            # Fail-fast: probe the listening port up-front so callers see a
            # clean RuntimeError instead of a silently dead server. Without
            # this check StartAsyncTcpServer logs a warning and returns,
            # leaving _running=True with no listener.
            try:
                probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((cfg.host, cfg.port))
                probe.close()
            except OSError as e:
                raise RuntimeError(
                    f"Cannot bind Modbus server on {cfg.host}:{cfg.port}: {e}"
                ) from e
            self._running = True

        ready = threading.Event()
        bind_error: List[BaseException] = []

        def _runner():
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._serve(ready))
            except BaseException as e:  # pragma: no cover
                bind_error.append(e)
                logger.error(f"Modbus server thread crashed: {e}")
            finally:
                loop.close()
                with self._lock:
                    self._running = False
                ready.set()  # unblock the start() waiter on early failure

        self._thread = threading.Thread(
            target=_runner, name="modbus-server", daemon=True
        )
        self._thread.start()
        if not ready.wait(timeout=5.0):
            logger.error("Modbus server did not become ready within 5s")
            with self._lock:
                self._running = False
            raise RuntimeError("Modbus server did not become ready within 5s")
        # If the thread already exited (bind failed in the loop), surface it.
        if not self._running:
            err = bind_error[0] if bind_error else RuntimeError("server exited immediately")
            raise RuntimeError(f"Modbus server failed to start: {err}")

    async def _serve(self, ready: threading.Event) -> None:
        cfg = self.config.server
        sizes = ", ".join(f"u{uid}:{size}" for uid, size in sorted(self._sizes.items()))
        logger.info(
            f"Embedded Modbus TCP server listening on {cfg.host}:{cfg.port} "
            f"(slaves: {sizes})"
        )
        ready.set()
        try:
            await StartAsyncTcpServer(
                context=self.server_context,
                address=(cfg.host, cfg.port),
            )
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("Embedded Modbus TCP server stopped")

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._loop and self._loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(ServerAsyncStop(), self._loop)
                fut.result(timeout=3.0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self._loop = None

    def is_running(self) -> bool:
        return self._running

    # ---- data plane (used by the simulator) --------------------------

    def write_holding(self, unit_id: int, address: int, values: List[int]) -> None:
        slave = self._slaves.get(unit_id)
        if slave is None:
            logger.warning(
                f"Drop write: unit_id={unit_id} not registered. "
                f"Restart the server to expose this slave."
            )
            return
        slave.setValues(16, address, [v & 0xFFFF for v in values])

    def read_holding(self, unit_id: int, address: int, count: int) -> List[int]:
        block = self._blocks.get(unit_id)
        if block is None:
            return []
        return list(block.values[address:address + count])

    # ---- fault rules -------------------------------------------------

    def reload_rules(self, sensors: List[SensorConfig]) -> None:
        self.rules.reload(sensors)
