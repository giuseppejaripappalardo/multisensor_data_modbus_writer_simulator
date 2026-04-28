"""
Sensor simulation orchestrator with per-measurement update rates.

Writes go to a RegisterSink, which can be:
  - a ModbusClient (writes over TCP to a remote slave), or
  - an EmbeddedModbusServer (writes directly to the in-process slave).

The simulator runs as a daemon thread (start/stop are non-blocking) so the
web UI and the CLI share the same engine.
"""
import threading
import time
from typing import Callable, Dict, List, Optional, Protocol, Tuple

from models import AppConfig, MeasurementConfig, SensorConfig
from simulator.encoder import decode_value, encode_value
from simulator.generator import SensorGenerator
from utils.logging import get_logger

logger = get_logger(__name__)


class RegisterSink(Protocol):
    """Anything that can accept a contiguous block of holding-register writes."""
    def write_register_blocks(self, unit_id: int, registers: Dict[int, int]) -> bool: ...


# ---------------------------------------------------------------------------
# Sink adapters
# ---------------------------------------------------------------------------

class ModbusClientSink:
    """Adapter: forward writes to a remote Modbus TCP slave."""

    def __init__(self, modbus_client):
        self._client = modbus_client

    def connect(self) -> bool:
        return self._client.connect()

    def disconnect(self) -> None:
        self._client.disconnect()

    def write_register_blocks(self, unit_id: int, registers: Dict[int, int]) -> bool:
        # Legacy ModbusClient.write_register_blocks doesn't take unit_id per call;
        # it uses the one in its config. Pass through unchanged.
        return self._client.write_register_blocks(registers)


class EmbeddedServerSink:
    """Adapter: write directly to the in-process EmbeddedModbusServer."""

    def __init__(self, server):
        self._server = server

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def write_register_blocks(self, unit_id: int, registers: Dict[int, int]) -> bool:
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
                self._server.write_holding(unit_id, start, block)
                start = addr
                block = [registers[addr]]
            expected = addr + 1
        self._server.write_holding(unit_id, start, block)
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
        config: AppConfig,
        sink: RegisterSink,
        on_update: Optional[Callable[[str, List[dict]], None]] = None,
    ):
        self.config = config
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

    def reload_config(self, config: AppConfig) -> None:
        """Replace the active configuration without restarting the loop."""
        self.config = config
        self._reset_state()

    def _reset_state(self) -> None:
        gens: Dict[str, SensorGenerator] = {}
        last: Dict[str, Dict[str, float]] = {}
        frozen: Dict[str, Dict[str, float]] = {}
        for sensor in self.config.sensors:
            gens[sensor.id] = SensorGenerator(sensor.id, sensor.measurements)
            last[sensor.id] = {m.name: -float("inf") for m in sensor.measurements}
            frozen[sensor.id] = {}
        self._generators = gens
        self._last_update = last
        self._frozen_values = frozen

    # --- internal ----------------------------------------------------

    def _run_loop(self) -> None:
        try:
            tick = max(0.05, self.config.tick_seconds)
            next_tick = time.time()
            while not self._stop_event.is_set():
                now = time.time()
                if now < next_tick:
                    self._stop_event.wait(min(0.05, next_tick - now))
                    continue
                sim_time = now - self._start_time
                for sensor in self.config.sensors:
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

        register_map: Dict[int, int] = {}
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
            #   - frozen=True   -> reuse last value (or generate one once if missing)
            #   - drop_writes   -> compute a value for the UI but skip the write
            if measurement.fault.frozen and measurement.name in frozen_values:
                raw = frozen_values[measurement.name]
            else:
                raw = generator.generate(measurement.name, sim_time)
                frozen_values[measurement.name] = raw

            regs = encode_value(
                raw,
                measurement.data_type,
                measurement.scale,
                measurement.min_value,
                measurement.max_value,
                sensor.byte_order,
                sensor.word_order,
            )
            try:
                roundtrip = decode_value(
                    regs, measurement.data_type, scale=measurement.scale,
                    byte_order=sensor.byte_order, word_order=sensor.word_order,
                )
            except Exception:
                roundtrip = float("nan")

            base_addr = sensor.base_address + measurement.offset
            if not measurement.fault.drop_writes:
                for i, val in enumerate(regs):
                    register_map[base_addr + i] = val

            updates.append({
                "name": measurement.name,
                "raw": raw,
                "scaled": raw * measurement.scale,
                "data_type": measurement.data_type.value,
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

        if register_map:
            try:
                ok = self.sink.write_register_blocks(sensor.unit_id, register_map)
            except Exception as e:
                logger.error(f"[{sensor_id}] write failed: {e}")
                ok = False
            if not ok:
                logger.warning(f"[{sensor_id}] sink write returned False")

        if updates and self.on_update:
            try:
                self.on_update(sensor_id, updates)
            except Exception as e:  # pragma: no cover
                logger.error(f"on_update callback failed: {e}")


def run_simulation(config: AppConfig) -> None:
    """Blocking entry point used by the CLI. Writes to a remote Modbus TCP slave."""
    from modbus_client import ModbusClient

    client = ModbusClient(config.modbus)
    sink = ModbusClientSink(client)
    sim = SensorSimulator(config, sink)
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
