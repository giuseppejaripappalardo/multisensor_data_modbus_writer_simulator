"""
Sensor simulation orchestrator with per-measurement update rates.

Writes go to a RegisterSink, which can be:
  - a ModbusClient (writes over TCP to a remote slave), or
  - an EmbeddedModbusServer (writes directly to the in-process slave).

The simulator runs as a daemon thread (start/stop are non-blocking) so the
web UI and the CLI share the same engine.
"""
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Protocol, Tuple

from models import (
    AppConfig,
    MeasurementConfig,
    RegisterType,
    SensorConfig,
    ServerConfig,
    is_bit_space,
)
from simulator.encoder import decode_value, encode_value
from simulator.generator import SensorGenerator
from utils.clamp import clamp
from utils.logging import get_logger


@dataclass
class _SpikeOverride:
    """One-shot value injection: replaces the generated value until expires_at."""
    value: float
    expires_at: float

logger = get_logger(__name__)


class RegisterSink(Protocol):
    """
    Anything that can accept writes to one of the four Modbus address spaces.

    The simulator partitions writes by (unit_id, register_type) and submits
    one call per partition. Each call carries an address->value map for that
    partition; the sink is responsible for grouping into contiguous blocks.
    """
    def write_register_blocks(
        self, unit_id: int, register_type: RegisterType,
        registers: Dict[int, int],
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Sink adapters
# ---------------------------------------------------------------------------

class ModbusClientSink:
    """Adapter: forward writes to a remote Modbus TCP slave (legacy CLI)."""

    def __init__(self, modbus_client):
        self._client = modbus_client

    def connect(self) -> bool:
        return self._client.connect()

    def disconnect(self) -> None:
        self._client.disconnect()

    def write_register_blocks(
        self, unit_id: int, register_type: RegisterType,
        registers: Dict[int, int],
    ) -> bool:
        # The legacy ModbusClient only writes holding registers via FC 16.
        # Coil / DI / IR writes have no equivalent over Modbus TCP from a
        # client (they would require non-standard extensions). We log and
        # drop them.
        if register_type != RegisterType.HOLDING_REGISTER:
            logger.warning(
                f"ModbusClientSink: dropping {register_type.value} write "
                f"(legacy client only supports holding registers)."
            )
            return True
        return self._client.write_register_blocks(registers)


class EmbeddedServerSink:
    """Adapter: write directly to the in-process EmbeddedModbusServer."""

    def __init__(self, server):
        self._server = server

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def write_register_blocks(
        self, unit_id: int, register_type: RegisterType,
        registers: Dict[int, int],
    ) -> bool:
        if not registers:
            return True
        sorted_addrs = sorted(registers.keys())
        start = sorted_addrs[0]
        block: List[int] = [registers[start]]
        expected = start + 1
        for addr in sorted_addrs[1:]:
            if addr == expected:
                block.append(registers[addr])
            else:
                self._server.write_block(unit_id, register_type, start, block)
                start = addr
                block = [registers[addr]]
            expected = addr + 1
        self._server.write_block(unit_id, register_type, start, block)
        return True


# ---------------------------------------------------------------------------
# Simulator engine
# ---------------------------------------------------------------------------

class SensorSimulator:
    """
    Drives N SensorGenerators on a tick loop, writing due values to a sink.
    Honors per-measurement update_rate, drop_writes and frozen flags.
    """

    def __init__(
        self,
        server_config: ServerConfig,
        sink: RegisterSink,
        tick_seconds: float = 1.0,
        on_update: Optional[Callable[[str, List[dict]], None]] = None,
    ):
        self.server_config = server_config
        self.tick_seconds = tick_seconds
        self.sink = sink
        self.on_update = on_update

        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._start_time = 0.0
        self._lock = threading.Lock()

        # Per-sensor generators and last-update bookkeeping.
        self._generators: Dict[str, SensorGenerator] = {}
        self._last_update: Dict[str, Dict[str, float]] = {}
        self._frozen_values: Dict[str, Dict[str, float]] = {}
        # drift accumulator: total real-units offset applied so far per measurement
        self._drift: Dict[str, Dict[str, float]] = {}
        # one-shot spike overrides keyed (sensor_id, measurement_name)
        self._spikes: Dict[Tuple[str, str], _SpikeOverride] = {}
        self._spike_lock = threading.Lock()
        self._rng = random.Random()
        self._reset_state()

    # --- lifecycle ---------------------------------------------------

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return True
            self._running = True
            self._stop_event.clear()

        if hasattr(self.sink, "connect") and not self.sink.connect():
            logger.error("Sink failed to connect")
            with self._lock:
                self._running = False
            return False

        self._reset_state()
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._run_loop, name="sensor-simulator", daemon=True
        )
        self._thread.start()
        logger.info("Sensor simulator started")
        return True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if hasattr(self.sink, "disconnect"):
            try:
                self.sink.disconnect()
            except Exception:
                pass
        self._thread = None
        logger.info("Sensor simulator stopped")

    def is_running(self) -> bool:
        return self._running

    # --- live config update -----------------------------------------

    def reload_config(
        self, server_config: ServerConfig, tick_seconds: Optional[float] = None,
    ) -> None:
        """Replace the active configuration without restarting the loop."""
        self.server_config = server_config
        if tick_seconds is not None:
            self.tick_seconds = tick_seconds
        self._reset_state()

    def _reset_state(self) -> None:
        # Reuse existing generators by sensor.id so monotonic counters
        # (kWh, run_time, energie integrate) and the RNG sequence persist
        # across live config reloads and stop/start cycles — real meters do
        # not reset on a config edit or power cycle.
        old_gens = self._generators
        gens: Dict[str, SensorGenerator] = {}
        last: Dict[str, Dict[str, float]] = {}
        frozen: Dict[str, Dict[str, float]] = {}
        drift: Dict[str, Dict[str, float]] = {}
        for sensor in self.server_config.sensors:
            existing = old_gens.get(sensor.id)
            if existing is not None:
                existing.update_measurements(sensor.measurements)
                gens[sensor.id] = existing
            else:
                gens[sensor.id] = SensorGenerator(sensor.id, sensor.measurements)
            last[sensor.id] = {m.name: -float("inf") for m in sensor.measurements}
            frozen[sensor.id] = {}
            drift[sensor.id] = {m.name: 0.0 for m in sensor.measurements}
        self._generators = gens
        self._last_update = last
        self._frozen_values = frozen
        self._drift = drift

    # --- one-shot fault injection --------------------------------------

    def inject_spike(
        self, sensor_id: str, measurement_name: str,
        value: float, duration_seconds: float,
    ) -> None:
        """Replace the generated value with `value` for the next duration_seconds."""
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be > 0")
        expires = time.time() + duration_seconds
        with self._spike_lock:
            self._spikes[(sensor_id, measurement_name)] = _SpikeOverride(
                value=float(value), expires_at=expires,
            )

    def clear_spike(self, sensor_id: str, measurement_name: str) -> bool:
        with self._spike_lock:
            return self._spikes.pop((sensor_id, measurement_name), None) is not None

    def _active_spike(self, sensor_id: str, measurement_name: str) -> Optional[float]:
        with self._spike_lock:
            spike = self._spikes.get((sensor_id, measurement_name))
            if spike is None:
                return None
            if time.time() >= spike.expires_at:
                del self._spikes[(sensor_id, measurement_name)]
                return None
            return spike.value

    # --- internal ----------------------------------------------------

    def _run_loop(self) -> None:
        try:
            tick = max(0.05, self.tick_seconds)
            next_tick = time.time()
            while not self._stop_event.is_set():
                now = time.time()
                if now < next_tick:
                    self._stop_event.wait(min(0.05, next_tick - now))
                    continue
                sim_time = now - self._start_time
                for sensor in self.server_config.sensors:
                    if self._stop_event.is_set():
                        break
                    self._update_sensor(sensor, sim_time)
                next_tick += tick
                if next_tick < now:
                    next_tick = now + tick
        except Exception as e:  # pragma: no cover
            logger.error(f"Simulator loop crashed: {e}", exc_info=True)
        finally:
            with self._lock:
                self._running = False

    def _update_sensor(self, sensor: SensorConfig, sim_time: float) -> None:
        sensor_id = sensor.id
        if sensor_id not in self._generators:
            return
        generator = self._generators[sensor_id]
        last_updates = self._last_update[sensor_id]
        frozen_values = self._frozen_values[sensor_id]
        drift_state = self._drift.setdefault(sensor_id, {})

        # One write map per register_type (coil / DI / IR / HR).
        register_maps: Dict[RegisterType, Dict[int, int]] = {}
        updates: List[dict] = []

        # Effective rate is the slower of the measurement-level rate and the
        # sensor-level write rate. Lets the user throttle Modbus writes to
        # e.g. once a minute even if individual measurements are faster.
        sensor_rate = max(0.0, sensor.write_rate_seconds)

        for measurement in sensor.measurements:
            effective_rate = max(measurement.update_rate, sensor_rate)
            last = last_updates.get(measurement.name, -float("inf"))
            if (sim_time - last) < effective_rate:
                continue
            last_updates[measurement.name] = sim_time

            # Compute the value:
            #   - spike active  -> override with the injected value
            #   - frozen=True   -> reuse last value (or generate one once if missing)
            #   - drop_writes   -> compute a value for the UI but skip the write
            spike = self._active_spike(sensor_id, measurement.name)
            if spike is not None:
                raw = spike
                frozen_values[measurement.name] = raw
            elif measurement.fault.frozen and measurement.name in frozen_values:
                raw = frozen_values[measurement.name]
            else:
                raw = generator.generate(measurement.name, sim_time)
                # Apply drift (cumulative real-units offset) and clamp to range.
                if measurement.fault.drift_per_second != 0.0:
                    delta = measurement.fault.drift_per_second * (sim_time - last) \
                        if last != -float("inf") else 0.0
                    drift_state[measurement.name] = drift_state.get(measurement.name, 0.0) + delta
                    raw = clamp(raw + drift_state[measurement.name],
                                measurement.min_value, measurement.max_value)
                frozen_values[measurement.name] = raw

            # Encoder bypass for bit-spaces: coil and discrete input store
            # a single 0/1 value. We treat the generated value as a boolean:
            # !=0 -> 1, 0 (or close to 0) -> 0. Scale is informational.
            if is_bit_space(measurement.register_type):
                bit_value = 0 if abs(raw) < 0.5 else 1
                regs = [bit_value]
                # Bit-flip on a coil simply toggles the bit.
                if measurement.fault.bit_flip_rate > 0 and \
                        self._rng.random() < measurement.fault.bit_flip_rate:
                    regs = [1 - regs[0]]
            else:
                regs = encode_value(
                    raw,
                    measurement.data_type,
                    measurement.scale,
                    measurement.min_value,
                    measurement.max_value,
                    sensor.byte_order,
                    sensor.word_order,
                )

                # Bit-flip injection: with bit_flip_rate probability, flip one
                # random bit somewhere in the encoded payload.
                if measurement.fault.bit_flip_rate > 0 and \
                        self._rng.random() < measurement.fault.bit_flip_rate:
                    total_bits = 16 * len(regs)
                    bit = self._rng.randrange(total_bits)
                    reg_idx = bit // 16
                    bit_idx = bit % 16
                    regs = list(regs)
                    regs[reg_idx] = (regs[reg_idx] ^ (1 << bit_idx)) & 0xFFFF
            if is_bit_space(measurement.register_type):
                roundtrip = float(regs[0])
            else:
                try:
                    roundtrip = decode_value(
                        regs, measurement.data_type, scale=measurement.scale,
                        byte_order=sensor.byte_order, word_order=sensor.word_order,
                    )
                except Exception:
                    roundtrip = float("nan")

            base_addr = sensor.base_address + measurement.offset
            if not measurement.fault.drop_writes:
                bucket = register_maps.setdefault(measurement.register_type, {})
                for i, val in enumerate(regs):
                    bucket[base_addr + i] = val

            updates.append({
                "name": measurement.name,
                "raw": raw,
                "scaled": raw * measurement.scale,
                "data_type": measurement.data_type.value,
                "register_type": measurement.register_type.value,
                "address": base_addr,
                "regs": regs,
                "hex": "".join(f"{r:04x}" for r in regs),
                "roundtrip": roundtrip,
                "byte_order": sensor.byte_order,
                "word_order": sensor.word_order,
                "unit": measurement.unit,
                "frozen": bool(measurement.fault.frozen),
                "dropped": bool(measurement.fault.drop_writes),
            })

        for rt, register_map in register_maps.items():
            if not register_map:
                continue
            try:
                ok = self.sink.write_register_blocks(sensor.unit_id, rt, register_map)
            except Exception as e:
                logger.error(f"[{sensor_id}/{rt.value}] write failed: {e}")
                ok = False
            if not ok:
                logger.warning(f"[{sensor_id}/{rt.value}] sink write returned False")

        if updates and self.on_update:
            try:
                self.on_update(sensor_id, updates)
            except Exception as e:  # pragma: no cover
                logger.error(f"on_update callback failed: {e}")


def run_simulation(config: AppConfig) -> None:
    """
    Blocking entry point used by the CLI. Writes to a single remote Modbus
    TCP slave (config.modbus). All sensors across all servers are flattened
    into one synthetic ServerConfig, since the CLI mode targets one external
    slave regardless of the embedded multi-server topology.
    """
    from modbus_client import ModbusClient

    client = ModbusClient(config.modbus)
    sink = ModbusClientSink(client)

    all_sensors: List[SensorConfig] = [
        sensor for server in config.servers for sensor in server.sensors
    ]
    synthetic = ServerConfig(
        id="cli", label="CLI flattened",
        host=config.modbus.host, port=config.modbus.port,
        sensors=all_sensors,
    )
    sim = SensorSimulator(synthetic, sink, tick_seconds=config.tick_seconds)
    if not sim.start():
        return
    try:
        # Block until Ctrl+C.
        while sim.is_running():
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        sim.stop()
