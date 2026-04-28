"""
Embedded Modbus TCP server with gateway-style multi-slave support.

  * One TCP listener (host:port).
  * One ModbusSlaveContext per distinct unit_id used by the sensors.
  * Each slave context exposes ALL FOUR Modbus address spaces:
      - coil              (FC 01 read, FC 05/15 write, 1-bit)
      - discrete input    (FC 02 read, 1-bit, R/O)
      - input register    (FC 04 read, 16-bit, R/O)
      - holding register  (FC 03 read, FC 06/16 write, 16-bit, R/W)
    Two sensors with different unit_ids share the same TCP listener but
    live in independent register spaces (gateway-style).

Fault injection is keyed on (unit_id, register_type, address):
  - per-sensor latency, offline, error_rate
  - per-measurement error_rate

Fault rules can be live-reloaded; structural changes (new unit_id appearing
or disappearing, or a new register_type used) require a server restart,
which the runtime takes care of.
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
from pymodbus.server import ModbusTcpServer

from models import RegisterType, SensorConfig, ServerConfig
from utils.logging import get_logger

logger = get_logger(__name__)


# Key used in fault tables: per (unit_id, register_type)
_BandKey = Tuple[int, RegisterType]


@dataclass
class _MeasurementBand:
    sensor_id: str
    measurement_name: str
    unit_id: int
    register_type: RegisterType
    start: int
    end: int
    error_rate: float = 0.0


@dataclass
class _SensorBand:
    sensor_id: str
    unit_id: int
    register_type: RegisterType
    start: int
    end: int
    latency_ms: int = 0
    offline: bool = False
    error_rate: float = 0.0


@dataclass
class FaultRules:
    """Mutable, thread-safe fault rules indexed by (unit_id, register_type)."""
    sensor_bands: Dict[_BandKey, List[_SensorBand]] = field(default_factory=dict)
    measurement_bands: Dict[_BandKey, List[_MeasurementBand]] = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reload(self, sensors: List[SensorConfig]) -> None:
        sb: Dict[_BandKey, List[_SensorBand]] = {}
        mb: Dict[_BandKey, List[_MeasurementBand]] = {}
        for sensor in sensors:
            uid = int(sensor.unit_id)
            ranges = sensor.address_ranges_by_type()
            if not ranges:
                continue
            for rt, r in ranges.items():
                sb.setdefault((uid, rt), []).append(_SensorBand(
                    sensor_id=sensor.id,
                    unit_id=uid,
                    register_type=rt,
                    start=r.start,
                    end=r.stop - 1,
                    latency_ms=int(sensor.fault.latency_ms),
                    offline=bool(sensor.fault.offline),
                    error_rate=float(sensor.fault.error_rate),
                ))
            for m in sensor.measurements:
                base = sensor.base_address + m.offset
                mb.setdefault((uid, m.register_type), []).append(_MeasurementBand(
                    sensor_id=sensor.id,
                    measurement_name=m.name,
                    unit_id=uid,
                    register_type=m.register_type,
                    start=base,
                    end=base + m.register_count - 1,
                    error_rate=float(m.fault.error_rate),
                ))
        with self._lock:
            self.sensor_bands = sb
            self.measurement_bands = mb

    def evaluate(
        self, unit_id: int, register_type: RegisterType,
        address: int, count: int,
    ) -> Tuple[bool, int]:
        end = address + count - 1
        latency = 0
        key: _BandKey = (unit_id, register_type)
        with self._lock:
            sensors = list(self.sensor_bands.get(key, []))
            measurements = list(self.measurement_bands.get(key, []))

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
    """
    Data block bound to a specific (unit_id, register_type); consults
    FaultRules. Used for all four address spaces — pymodbus internally
    treats coil/discrete-input as int 0/1 in the same kind of list.
    """

    def __init__(
        self, address: int, values, rules: FaultRules,
        unit_id: int, register_type: RegisterType,
    ):
        super().__init__(address, values)
        self._rules = rules
        self._unit_id = unit_id
        self._register_type = register_type

    def validate(self, address, count=1):  # noqa: N802
        should_fault, _ = self._rules.evaluate(
            self._unit_id, self._register_type, address, count
        )
        if should_fault:
            return False
        return super().validate(address, count)

    def getValues(self, address, count=1):  # noqa: N802
        _, latency = self._rules.evaluate(
            self._unit_id, self._register_type, address, count
        )
        if latency > 0:
            time.sleep(latency / 1000.0)
        return super().getValues(address, count)


class EmbeddedModbusServer:
    """
    Multi-slave Modbus TCP server. Each unit_id used by the sensors gets its
    own holding-register space.
    """

    def __init__(self, server_config: ServerConfig):
        self.config = server_config
        self.rules = FaultRules()
        self.rules.reload(server_config.sensors)

        # Build one slave context per unit_id (always include the default
        # so the server is reachable even with zero sensors).
        unit_ids = sorted(
            {s.unit_id for s in server_config.sensors}
            | {server_config.default_unit_id}
        )
        min_size = max(1, server_config.register_count_min)

        # Auto-size: each (unit_id, register_type) gets max(min_size, highest_addr+1).
        all_types: Tuple[RegisterType, ...] = (
            RegisterType.COIL,
            RegisterType.DISCRETE_INPUT,
            RegisterType.INPUT_REGISTER,
            RegisterType.HOLDING_REGISTER,
        )
        per_unit_size: Dict[int, Dict[RegisterType, int]] = {
            uid: {rt: min_size for rt in all_types} for uid in unit_ids
        }
        for s in server_config.sensors:
            for rt, r in s.address_ranges_by_type().items():
                per_unit_size[s.unit_id][rt] = max(
                    per_unit_size[s.unit_id][rt], r.stop
                )

        # Build (unit_id, register_type) -> _UnitDataBlock.
        self._blocks: Dict[Tuple[int, RegisterType], _UnitDataBlock] = {}
        slaves: Dict[int, ModbusSlaveContext] = {}
        for uid in unit_ids:
            blocks_for_uid: Dict[RegisterType, _UnitDataBlock] = {}
            for rt in all_types:
                size = per_unit_size[uid][rt]
                # Coil / discrete-input store 0/1; registers store 16-bit ints.
                # Initial values are zeros for both — pymodbus is type-agnostic.
                block = _UnitDataBlock(0, [0] * size, self.rules, uid, rt)
                self._blocks[(uid, rt)] = block
                blocks_for_uid[rt] = block
            slaves[uid] = ModbusSlaveContext(
                co=blocks_for_uid[RegisterType.COIL],
                di=blocks_for_uid[RegisterType.DISCRETE_INPUT],
                ir=blocks_for_uid[RegisterType.INPUT_REGISTER],
                hr=blocks_for_uid[RegisterType.HOLDING_REGISTER],
                zero_mode=True,
            )
        self._slaves = slaves
        self._sizes = per_unit_size
        self.server_context = ModbusServerContext(slaves=slaves, single=False)
        self._unit_ids = set(unit_ids)
        self._all_register_types = all_types

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tcp_server: Optional[ModbusTcpServer] = None
        self._running = False
        self._lock = threading.Lock()

    # ---- introspection ------------------------------------------------

    def supported_unit_ids(self) -> List[int]:
        return sorted(self._unit_ids)

    def slave_sizes(self) -> Dict[int, Dict[RegisterType, int]]:
        """Per-unit sizes for each address space."""
        return {uid: dict(types) for uid, types in self._sizes.items()}

    def slave_total_registers(self) -> Dict[int, int]:
        """Aggregate register count per unit_id (for status pills)."""
        return {uid: sum(self._sizes[uid].values()) for uid in self._sizes}

    def needs_restart_for(self, server_config: ServerConfig) -> bool:
        """
        True if the running server can no longer serve the new config:
          - host or port changed
          - new unit_id appeared (or one disappeared)
          - any (unit_id, register_type) block needs to be larger than allocated
        """
        if server_config.host != self.config.host or server_config.port != self.config.port:
            return True
        configured = (
            {s.unit_id for s in server_config.sensors}
            | {server_config.default_unit_id}
        )
        if configured != self._unit_ids:
            return True
        for s in server_config.sensors:
            sizes_for_uid = self._sizes.get(s.unit_id)
            if sizes_for_uid is None:
                return True
            for rt, r in s.address_ranges_by_type().items():
                if r.stop > sizes_for_uid.get(rt, 0):
                    return True
        return False

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            cfg = self.config
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
        cfg = self.config
        sizes = ", ".join(f"u{uid}:{size}" for uid, size in sorted(self._sizes.items()))
        logger.info(
            f"Embedded Modbus TCP server '{cfg.id}' listening on {cfg.host}:{cfg.port} "
            f"(slaves: {sizes})"
        )
        # Build a per-instance ModbusTcpServer so shutdown() targets THIS
        # listener only — the legacy ServerAsyncStop() is module-global and
        # would tear down every running server in the process.
        self._tcp_server = ModbusTcpServer(
            context=self.server_context,
            address=(cfg.host, cfg.port),
        )
        ready.set()
        try:
            await self._tcp_server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info(f"Embedded Modbus TCP server '{cfg.id}' stopped")

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False

        self._shutdown_tcp_server()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self._loop = None
        self._tcp_server = None

    def _shutdown_tcp_server(self) -> None:
        """Stop the per-instance ModbusTcpServer scheduled in self._loop."""
        tcp_server = self._tcp_server
        if tcp_server is None or self._loop is None or not self._loop.is_running():
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(tcp_server.shutdown(), self._loop)
            fut.result(timeout=3.0)
        except Exception as e:
            logger.warning(f"Server '{self.config.id}' shutdown failed: {e}")

    def is_running(self) -> bool:
        return self._running

    def kick(self) -> None:
        """
        Drop every active TCP connection without losing register state.

        Implementation: stop the async server (closes all client transports
        and the listener) and restart it. The server_context with the
        slaves/registers is reused, so no data is lost. Clients see a
        connection close (or RST depending on their socket state) and
        must reconnect — useful to test reconnect logic.
        """
        if not self._running:
            raise RuntimeError("Server is not running, cannot kick")
        self._shutdown_tcp_server()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self._loop = None
        self._tcp_server = None
        with self._lock:
            self._running = False
        # Restart on the same context: registers and slaves are preserved.
        self.start()

    # ---- data plane (used by the simulator) --------------------------

    # Modbus function codes used to address each register space when going
    # through ModbusSlaveContext.setValues (the server-side write path).
    _FC_BY_TYPE: Dict[RegisterType, int] = {
        RegisterType.COIL: 1,
        RegisterType.DISCRETE_INPUT: 2,
        RegisterType.INPUT_REGISTER: 4,
        RegisterType.HOLDING_REGISTER: 3,
    }

    def write_block(
        self, unit_id: int, register_type: RegisterType,
        address: int, values: List[int],
    ) -> None:
        """Server-side write to any of the 4 address spaces."""
        slave = self._slaves.get(unit_id)
        if slave is None:
            logger.warning(
                f"Drop write: unit_id={unit_id} not registered. "
                f"Restart the server to expose this slave."
            )
            return
        if register_type in (RegisterType.COIL, RegisterType.DISCRETE_INPUT):
            normalized = [1 if v else 0 for v in values]
        else:
            normalized = [v & 0xFFFF for v in values]
        slave.setValues(self._FC_BY_TYPE[register_type], address, normalized)

    def read_block(
        self, unit_id: int, register_type: RegisterType,
        address: int, count: int,
    ) -> List[int]:
        block = self._blocks.get((unit_id, register_type))
        if block is None:
            return []
        return list(block.values[address:address + count])

    # Backwards-compatible aliases (older callers used "holding" only).
    def write_holding(self, unit_id: int, address: int, values: List[int]) -> None:
        self.write_block(unit_id, RegisterType.HOLDING_REGISTER, address, values)

    def read_holding(self, unit_id: int, address: int, count: int) -> List[int]:
        return self.read_block(unit_id, RegisterType.HOLDING_REGISTER, address, count)

    # ---- fault rules -------------------------------------------------

    def reload_rules(self, sensors: List[SensorConfig]) -> None:
        self.rules.reload(sensors)
