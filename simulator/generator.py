"""
Realistic sensor data generator with correlations and noise patterns.
"""
import hashlib
import math
import random
from typing import Dict, Optional

from utils.clamp import clamp
from utils.logging import get_logger

logger = get_logger(__name__)


class SensorGenerator:
    """
    Generates realistic sensor values with correlations and noise patterns.

    Each sensor instance has its own RNG seed for reproducibility and
    to ensure different sensors produce different values.
    """

    def __init__(self, sensor_id: str, seed: Optional[int] = None):
        """
        Initialize the generator for a specific sensor.

        Args:
            sensor_id: Unique sensor identifier (used for seeding).
            seed: Optional explicit seed (if None, derived from sensor_id).
        """
        self.sensor_id = sensor_id

        # Derive seed from sensor_id for reproducibility
        if seed is None:
            # Use hash to convert sensor_id string to integer seed
            hash_value = hashlib.md5(sensor_id.encode()).hexdigest()
            seed = int(hash_value[:8], 16)

        self.rng = random.Random(seed)

        # Internal state for correlated values
        self._current_co2: float = 520.0
        self._current_pm25: float = 12.0

        # Peak event tracking for PM
        self._pm_peak_active: bool = False
        self._pm_peak_start_time: float = 0.0
        self._pm_peak_duration: float = 0.0
        self._next_pm_peak_time: float = self.rng.uniform(60, 120)

        logger.debug(f"Initialized generator for sensor '{sensor_id}' with seed {seed}")

    def generate_all(self, time_seconds: float) -> Dict[str, float]:
        """
        Generate all measurement values for a given time.

        Args:
            time_seconds: Elapsed time in seconds since simulation start.

        Returns:
            Dictionary of measurement names to values.
        """
        # Generate CO2 first (needed for TVOC correlation)
        co2 = self._generate_co2(time_seconds)
        self._current_co2 = co2

        # Generate PM2.5 first (needed for PM10 correlation)
        pm25 = self._generate_pm25(time_seconds)
        self._current_pm25 = pm25

        return {
            "temperature": self._generate_temperature(time_seconds),
            "humidity": self._generate_humidity(time_seconds),
            "co2": co2,
            "tvoc": self._generate_tvoc(time_seconds),
            "pm25": pm25,
            "pm10": self._generate_pm10(time_seconds),
            "lux": self._generate_lux(time_seconds),
            "noise": self._generate_noise(time_seconds),
        }

    def generate_single(self, measurement: str, time_seconds: float) -> float:
        """
        Generate a single measurement value.

        Note: For correlated measurements (tvoc, pm10), this will use
        the last generated co2/pm25 values.

        Args:
            measurement: Measurement type name.
            time_seconds: Elapsed time in seconds.

        Returns:
            Generated measurement value.
        """
        generators = {
            "temperature": self._generate_temperature,
            "humidity": self._generate_humidity,
            "co2": self._generate_co2,
            "tvoc": self._generate_tvoc,
            "pm25": self._generate_pm25,
            "pm10": self._generate_pm10,
            "lux": self._generate_lux,
            "noise": self._generate_noise,
        }

        generator = generators.get(measurement)
        if generator is None:
            logger.warning(f"Unknown measurement type: {measurement}")
            return 0.0

        value = generator(time_seconds)

        # Update state for correlated values
        if measurement == "co2":
            self._current_co2 = value
        elif measurement == "pm25":
            self._current_pm25 = value

        return value

    def _generate_temperature(self, t: float) -> float:
        """
        Generate temperature value.

        Pattern: base 24°C, sinusoidal variation ±2°C, noise ±0.1°C
        Range: 18°C to 30°C
        """
        base = 24.0
        sin_component = math.sin(t / 120.0) * 2.0
        noise = self.rng.gauss(0, 0.1)

        value = base + sin_component + noise
        return clamp(value, 18.0, 30.0)

    def _generate_humidity(self, t: float) -> float:
        """
        Generate humidity value.

        Pattern: base 55%, sinusoidal variation ±7%, noise ±0.3%
        Range: 30% to 80%
        """
        base = 55.0
        sin_component = math.sin(t / 180.0) * 7.0
        noise = self.rng.gauss(0, 0.3)

        value = base + sin_component + noise
        return clamp(value, 30.0, 80.0)

    def _generate_co2(self, t: float) -> float:
        """
        Generate CO2 value.

        Pattern: base 520ppm, sinusoidal peaks up to +300ppm, noise ±20ppm
        Range: 400ppm to 1500ppm
        """
        base = 520.0
        # Use max(0, sin) to create peaks rather than valleys
        sin_component = max(0, math.sin(t / 90.0)) * 300.0
        noise = self.rng.gauss(0, 20)

        value = base + sin_component + noise
        return clamp(value, 400.0, 1500.0)

    def _generate_tvoc(self, t: float) -> float:
        """
        Generate TVOC value correlated with CO2.

        Pattern: 150ppb base + CO2 correlation (0.4 factor) + noise ±25ppb
        Range: 50ppb to 1200ppb
        """
        base = 150.0
        co2_correlation = (self._current_co2 - 400.0) * 0.4
        noise = self.rng.gauss(0, 25)

        value = base + co2_correlation + noise
        return clamp(value, 50.0, 1200.0)

    def _generate_pm25(self, t: float) -> float:
        """
        Generate PM2.5 value with occasional peaks.

        Pattern: base 12µg/m³, noise ±2, occasional peak events
        Range: 0 to 200µg/m³
        """
        base = 12.0
        noise = self.rng.gauss(0, 2)

        # Handle peak events
        peak_contribution = 0.0

        if self._pm_peak_active:
            # Check if peak should end
            elapsed_in_peak = t - self._pm_peak_start_time
            if elapsed_in_peak > self._pm_peak_duration:
                self._pm_peak_active = False
                self._next_pm_peak_time = t + self.rng.uniform(60, 120)
            else:
                # Gaussian-shaped peak
                peak_center = self._pm_peak_duration / 2
                peak_intensity = 50 + self.rng.uniform(0, 30)
                sigma = self._pm_peak_duration / 4
                peak_contribution = peak_intensity * math.exp(
                    -((elapsed_in_peak - peak_center) ** 2) / (2 * sigma ** 2)
                )
        else:
            # Check if it's time for a new peak
            if t >= self._next_pm_peak_time:
                self._pm_peak_active = True
                self._pm_peak_start_time = t
                self._pm_peak_duration = self.rng.uniform(10, 20)

        value = base + noise + peak_contribution
        return clamp(value, 0.0, 200.0)

    def _generate_pm10(self, t: float) -> float:
        """
        Generate PM10 value correlated with PM2.5.

        Pattern: PM2.5 + random offset (3-20µg/m³)
        Range: 0 to 300µg/m³
        """
        offset = self.rng.uniform(3, 20)
        value = self._current_pm25 + offset
        return clamp(value, 0.0, 300.0)

    def _generate_lux(self, t: float) -> float:
        """
        Generate illuminance value.

        Pattern: 50 lux base + normalized sinusoidal variation * 800, noise ±10
        Range: 0 to 2000 lux
        """
        base = 50.0
        # Normalize sin to 0-1 range for lighting pattern
        sin_normalized = (math.sin(t / 300.0) + 1) / 2
        sin_component = sin_normalized * 800.0
        noise = self.rng.gauss(0, 10)

        value = base + sin_component + noise
        return clamp(value, 0.0, 2000.0)

    def _generate_noise(self, t: float) -> float:
        """
        Generate noise level value.

        Pattern: base 38dB, sinusoidal variation ±3dB, noise ±1dB
        Range: 25dB to 75dB
        """
        base = 38.0
        sin_component = math.sin(t / 20.0) * 3.0
        noise = self.rng.gauss(0, 1)

        value = base + sin_component + noise
        return clamp(value, 25.0, 75.0)


class MultiSensorGenerator:
    """
    Manages multiple sensor generators.
    """

    def __init__(self):
        """Initialize the multi-sensor generator."""
        self._generators: Dict[str, SensorGenerator] = {}

    def add_sensor(self, sensor_id: str, seed: Optional[int] = None) -> SensorGenerator:
        """
        Add a sensor generator.

        Args:
            sensor_id: Unique sensor identifier.
            seed: Optional explicit seed.

        Returns:
            The created sensor generator.
        """
        generator = SensorGenerator(sensor_id, seed)
        self._generators[sensor_id] = generator
        return generator

    def get_generator(self, sensor_id: str) -> Optional[SensorGenerator]:
        """
        Get a sensor generator by ID.

        Args:
            sensor_id: Sensor identifier.

        Returns:
            The sensor generator, or None if not found.
        """
        return self._generators.get(sensor_id)

    def generate_all(self, time_seconds: float) -> Dict[str, Dict[str, float]]:
        """
        Generate values for all sensors.

        Args:
            time_seconds: Elapsed time in seconds.

        Returns:
            Dictionary mapping sensor_id to measurement dictionary.
        """
        result = {}
        for sensor_id, generator in self._generators.items():
            result[sensor_id] = generator.generate_all(time_seconds)
        return result

