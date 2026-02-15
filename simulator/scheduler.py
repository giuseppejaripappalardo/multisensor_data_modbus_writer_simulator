"""
Multi-rate scheduler for sensor measurements.
"""
import time
from typing import Dict, List, Set, Tuple

from models import AppConfig, SensorConfig, RatesConfig, MEASUREMENT_TYPES
from modbus_client import ModbusClient
from simulator.generator import MultiSensorGenerator
from simulator.encoder import encode_value
from utils.logging import get_logger

logger = get_logger(__name__)


class MeasurementScheduler:
    """
    Schedules measurement updates based on per-measurement rates.
    """

    def __init__(self, rates: RatesConfig, tick_seconds: float):
        """
        Initialize the scheduler.

        Args:
            rates: Per-measurement rate configuration.
            tick_seconds: Base tick interval in seconds.
        """
        self.rates = rates
        self.tick_seconds = tick_seconds

        # Track last update time for each measurement
        self._last_update: Dict[str, float] = {}

        # Initialize all measurements as needing update
        for measurement in MEASUREMENT_TYPES:
            self._last_update[measurement] = -float("inf")

    def get_rate(self, measurement: str) -> float:
        """Get the rate in seconds for a measurement."""
        return getattr(self.rates, measurement, 1.0)

    def get_due_measurements(self, current_time: float) -> Set[str]:
        """
        Get measurements that are due for update.

        Args:
            current_time: Current simulation time in seconds.

        Returns:
            Set of measurement names that should be updated.
        """
        due = set()

        for measurement in MEASUREMENT_TYPES:
            rate = self.get_rate(measurement)
            last_update = self._last_update.get(measurement, -float("inf"))

            if (current_time - last_update) >= rate:
                due.add(measurement)

        return due

    def mark_updated(self, measurements: Set[str], current_time: float):
        """
        Mark measurements as updated at the given time.

        Args:
            measurements: Set of measurement names that were updated.
            current_time: Time at which they were updated.
        """
        for measurement in measurements:
            self._last_update[measurement] = current_time


class SensorSimulator:
    """
    Orchestrates the simulation of multiple sensors with multi-rate updates.
    """

    def __init__(
        self,
        config: AppConfig,
        modbus_client: ModbusClient,
    ):
        """
        Initialize the simulator.

        Args:
            config: Application configuration.
            modbus_client: Modbus client for writing registers.
        """
        self.config = config
        self.modbus_client = modbus_client

        # Create generators for each sensor
        self.multi_generator = MultiSensorGenerator()
        for sensor_config in config.sensors:
            self.multi_generator.add_sensor(sensor_config.id)

        # Create scheduler
        self.scheduler = MeasurementScheduler(
            rates=config.update.rates,
            tick_seconds=config.update.tick_seconds,
        )

        # Store sensor configs by ID for easy lookup
        self._sensor_configs: Dict[str, SensorConfig] = {
            s.id: s for s in config.sensors
        }

        # Track current values for each sensor
        self._current_values: Dict[str, Dict[str, float]] = {}

        # Running state
        self._running = False
        self._start_time: float = 0.0

    def start(self):
        """Start the simulation loop."""
        self._running = True
        self._start_time = time.time()

        logger.info("Starting sensor simulation")
        logger.info(f"Tick interval: {self.config.update.tick_seconds}s")
        logger.info(f"Number of sensors: {len(self.config.sensors)}")

        # Connect to Modbus server
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
        tick_interval = self.config.update.tick_seconds
        next_tick = time.time()

        while self._running:
            current_time = time.time()

            # Wait for next tick
            if current_time < next_tick:
                time.sleep(min(0.01, next_tick - current_time))
                continue

            # Calculate simulation time
            sim_time = current_time - self._start_time

            # Determine which measurements need updating
            due_measurements = self.scheduler.get_due_measurements(sim_time)

            if due_measurements:
                # Process each sensor
                for sensor_config in self.config.sensors:
                    self._update_sensor(sensor_config, sim_time, due_measurements)

                # Mark measurements as updated
                self.scheduler.mark_updated(due_measurements, sim_time)

            # Schedule next tick
            next_tick += tick_interval

            # If we've fallen behind, catch up
            if next_tick < current_time:
                next_tick = current_time + tick_interval

    def _update_sensor(
        self,
        sensor_config: SensorConfig,
        sim_time: float,
        measurements: Set[str],
    ):
        """
        Update measurements for a single sensor.

        Args:
            sensor_config: Sensor configuration.
            sim_time: Current simulation time.
            measurements: Set of measurements to update.
        """
        sensor_id = sensor_config.id
        generator = self.multi_generator.get_generator(sensor_id)

        if generator is None:
            logger.error(f"No generator for sensor {sensor_id}")
            return

        # Generate new values for due measurements
        raw_values = {}
        encoded_values = {}
        registers_to_write = {}

        for measurement in measurements:
            raw_value = generator.generate_single(measurement, sim_time)
            encoded_value = encode_value(measurement, raw_value)

            raw_values[measurement] = raw_value
            encoded_values[measurement] = encoded_value

            # Get register address
            address = sensor_config.get_register_address(measurement)
            registers_to_write[address] = encoded_value

        # Store current values
        if sensor_id not in self._current_values:
            self._current_values[sensor_id] = {}
        self._current_values[sensor_id].update(raw_values)

        # Write to Modbus in contiguous blocks
        success = self.modbus_client.write_register_blocks(registers_to_write)

        # Log the update
        self._log_update(sensor_id, raw_values, encoded_values, registers_to_write, success)

    def _log_update(
        self,
        sensor_id: str,
        raw_values: Dict[str, float],
        encoded_values: Dict[str, int],
        registers: Dict[int, int],
        success: bool,
    ):
        """Log measurement update."""
        # Format raw values
        raw_str = ", ".join(
            f"{k}={v:.2f}" for k, v in sorted(raw_values.items())
        )

        # Format encoded values
        encoded_str = ", ".join(
            f"{k}={v}" for k, v in sorted(encoded_values.items())
        )

        # Group registers into blocks for logging
        blocks = self._get_register_blocks(registers)
        blocks_str = ", ".join(
            f"[{start}:{start + count - 1}]" if count > 1 else f"[{start}]"
            for start, count in blocks
        )

        status = "OK" if success else "FAILED"

        logger.info(
            f"[{sensor_id}] {status} | "
            f"raw: {raw_str} | "
            f"scaled: {encoded_str} | "
            f"blocks: {blocks_str}"
        )

    def _get_register_blocks(self, registers: Dict[int, int]) -> List[Tuple[int, int]]:
        """Get contiguous blocks as (start, count) tuples."""
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
    """
    Run the sensor simulation.

    Args:
        config: Application configuration.
    """
    # Create Modbus client
    modbus_client = ModbusClient(config.modbus)

    # Create and start simulator
    simulator = SensorSimulator(config, modbus_client)
    simulator.start()

