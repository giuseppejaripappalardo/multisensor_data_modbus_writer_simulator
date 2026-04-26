"""
Sensor simulation orchestrator with per-measurement update rates.
"""
import time
from typing import Dict, List, Tuple

from models import AppConfig, SensorConfig, MeasurementConfig
from modbus_client import ModbusClient
from simulator.generator import SensorGenerator
from simulator.encoder import encode_value, decode_value
from utils.logging import get_logger

logger = get_logger(__name__)


class SensorSimulator:
    """
    Orchestrates the simulation of multiple sensors.

    Each sensor has its own generator and per-measurement update rate scheduling.
    """

    def __init__(self, config: AppConfig, modbus_client: ModbusClient):
        self.config = config
        self.modbus_client = modbus_client
        self._running = False
        self._start_time = 0.0

        # Create a generator per sensor
        self._generators: Dict[str, SensorGenerator] = {}
        # Track last update time: sensor_id -> measurement_name -> time
        self._last_update: Dict[str, Dict[str, float]] = {}

        for sensor in config.sensors:
            self._generators[sensor.id] = SensorGenerator(
                sensor.id, sensor.measurements
            )
            self._last_update[sensor.id] = {
                m.name: -float("inf") for m in sensor.measurements
            }

    def start(self):
        """Start the simulation loop."""
        self._running = True
        self._start_time = time.time()

        logger.info("Starting sensor simulation")
        logger.info(f"Tick interval: {self.config.tick_seconds}s")
        logger.info(f"Sensors: {len(self.config.sensors)}")

        if not self.modbus_client.connect():
            logger.error("Failed to connect to Modbus server")
            return

        try:
            self._run_loop()
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        finally:
            self.stop()

    def stop(self):
        """Stop the simulation."""
        self._running = False
        self.modbus_client.disconnect()
        logger.info("Simulation stopped")

    def _run_loop(self):
        """Main simulation loop."""
        tick = self.config.tick_seconds
        next_tick = time.time()

        while self._running:
            now = time.time()
            if now < next_tick:
                time.sleep(min(0.01, next_tick - now))
                continue

            sim_time = now - self._start_time

            for sensor in self.config.sensors:
                self._update_sensor(sensor, sim_time)

            next_tick += tick
            if next_tick < now:
                next_tick = now + tick

    def _update_sensor(self, sensor: SensorConfig, sim_time: float):
        """Update due measurements for a sensor and write to Modbus."""
        generator = self._generators[sensor.id]
        last_updates = self._last_update[sensor.id]

        # Collect registers to write (address -> list of uint16 values)
        register_map: Dict[int, int] = {}
        updates: List[dict] = []

        for measurement in sensor.measurements:
            last = last_updates[measurement.name]
            if (sim_time - last) < measurement.update_rate:
                continue

            # Generate raw value
            raw = generator.generate(measurement.name, sim_time)

            # Encode to register(s)
            regs = encode_value(
                raw,
                measurement.data_type,
                measurement.scale,
                measurement.min_value,
                measurement.max_value,
                sensor.byte_order,
                sensor.word_order,
            )

            # Round-trip decode to verify endianness
            roundtrip = decode_value(
                regs, measurement.data_type, scale=1.0,
                byte_order=sensor.byte_order, word_order=sensor.word_order,
            )

            # Map to absolute addresses
            base_addr = sensor.base_address + measurement.offset
            for i, val in enumerate(regs):
                register_map[base_addr + i] = val

            regs_hex = "".join(f"{r:04x}" for r in regs)
            updates.append({
                "name": measurement.name,
                "raw": raw,
                "scaled": raw * measurement.scale,
                "data_type": measurement.data_type.value,
                "regs": regs,
                "hex": regs_hex,
                "roundtrip": roundtrip,
                "byte_order": sensor.byte_order,
                "word_order": sensor.word_order,
            })

            last_updates[measurement.name] = sim_time

        if not register_map:
            return

        # Write all registers in contiguous blocks
        success = self.modbus_client.write_register_blocks(register_map)

        # Log
        self._log_update(sensor.id, updates, register_map, success)

    def _log_update(
        self,
        sensor_id: str,
        updates: List[dict],
        registers: Dict[int, int],
        success: bool,
    ):
        status = "OK" if success else "FAILED"
        blocks = self._get_register_blocks(registers)
        blocks_str = ", ".join(
            f"[{s}:{s + c - 1}]" if c > 1 else f"[{s}]"
            for s, c in blocks
        )
        logger.info(f"[{sensor_id}] {status} | blocks: {blocks_str}")
        for u in updates:
            dt = u["data_type"]
            is_float = dt in ("float32", "float64")
            if is_float:
                scaled_str = f"{u['scaled']:.6f}"
                rt_str = f"{u['roundtrip']:.6f}"
            else:
                scaled_str = f"{u['scaled']:.2f}"
                rt_str = f"{u['roundtrip']}"
            logger.info(
                f"[{sensor_id}]   {u['name']}: "
                f"raw={u['raw']:.4f}, "
                f"type={dt}, "
                f"scaled={scaled_str}, "
                f"regs={u['regs']}, "
                f"hex={u['hex']}, "
                f"roundtrip={rt_str}, "
                f"endian=byte:{u['byte_order']}/word:{u['word_order']}"
            )

    def _get_register_blocks(
        self, registers: Dict[int, int]
    ) -> List[Tuple[int, int]]:
        """Group register addresses into contiguous (start, count) blocks."""
        if not registers:
            return []

        sorted_addrs = sorted(registers.keys())
        blocks = []
        start = sorted_addrs[0]
        count = 1

        for addr in sorted_addrs[1:]:
            if addr == start + count:
                count += 1
            else:
                blocks.append((start, count))
                start = addr
                count = 1

        blocks.append((start, count))
        return blocks


def run_simulation(config: AppConfig):
    """Run the sensor simulation."""
    modbus_client = ModbusClient(config.modbus)
    simulator = SensorSimulator(config, modbus_client)
    simulator.start()
