"""
Realistic sensor data generator.

Provides specific patterns for known measurement types (temperature, humidity, etc.)
and a generic sinusoidal pattern for custom/unknown measurement types.
"""
import hashlib
import math
import random
from typing import Dict, List, Optional

from models import MeasurementConfig
from utils.clamp import clamp
from utils.logging import get_logger

logger = get_logger(__name__)


class SensorGenerator:
    """
    Generates realistic sensor values for a single sensor.

    Each instance has its own RNG seed for reproducibility.
    Known measurement types use specific patterns with correlations.
    Unknown types use a generic sinusoidal + noise pattern.
    """

    def __init__(
        self,
        sensor_id: str,
        measurements: List[MeasurementConfig],
        seed: Optional[int] = None,
    ):
        self.sensor_id = sensor_id
        self._measurements = {m.name: m for m in measurements}

        if seed is None:
            hash_value = hashlib.md5(sensor_id.encode()).hexdigest()
            seed = int(hash_value[:8], 16)

        self.rng = random.Random(seed)

        # State for correlated values
        self._state: Dict[str, float] = {}

        # PM peak event tracking
        self._pm_peak_active = False
        self._pm_peak_start_time = 0.0
        self._pm_peak_duration = 0.0
        self._next_pm_peak_time = self.rng.uniform(60, 120)

        logger.debug(f"Initialized generator for '{sensor_id}' (seed={seed})")

    def generate(self, name: str, time_seconds: float) -> float:
        """Generate a value for the named measurement."""
        config = self._measurements.get(name)
        if config is None:
            logger.warning(f"Unknown measurement '{name}' for sensor '{self.sensor_id}'")
            return 0.0

        generators = {
            "temperature": self._gen_temperature,
            "humidity": self._gen_humidity,
            "co2": self._gen_co2,
            "tvoc": self._gen_tvoc,
            "pm25": self._gen_pm25,
            "pm10": self._gen_pm10,
            "lux": self._gen_lux,
            "noise": self._gen_noise,
            # Boolean / discrete patterns
            "alarm_active": self._gen_boolean_rare,
            "motor_run": self._gen_boolean_periodic,
            "presence": self._gen_boolean_periodic,
            "limit_switch": self._gen_boolean_periodic,
            # Boolean su registri 16-bit (UINT16 con valori 0/1)
            "bool_flag_hr": self._gen_boolean_periodic,
            "bool_flag_ir": self._gen_boolean_periodic,
            # Counters
            "uptime_seconds": self._gen_uptime,
        }

        gen_fn = generators.get(name)
        if gen_fn:
            value = gen_fn(time_seconds)
        else:
            value = self._gen_generic(config, time_seconds)

        value = clamp(value, config.min_value, config.max_value)
        self._state[name] = value
        return value

    def generate_all(self, time_seconds: float) -> Dict[str, float]:
        """Generate all measurement values (dependency-ordered)."""
        names = self._dependency_order()
        return {name: self.generate(name, time_seconds) for name in names}

    def _dependency_order(self) -> List[str]:
        """Order measurements so dependencies come first (co2 before tvoc, etc.)."""
        priorities = {"co2": 0, "pm25": 0, "tvoc": 1, "pm10": 1}
        names = list(self._measurements.keys())
        names.sort(key=lambda n: priorities.get(n, 0))
        return names

    # -- Known measurement patterns --

    def _gen_temperature(self, t: float) -> float:
        return 24.0 + math.sin(t / 120.0) * 2.0 + self.rng.gauss(0, 0.1)

    def _gen_humidity(self, t: float) -> float:
        return 55.0 + math.sin(t / 180.0) * 7.0 + self.rng.gauss(0, 0.3)

    def _gen_co2(self, t: float) -> float:
        return 520.0 + max(0, math.sin(t / 90.0)) * 300.0 + self.rng.gauss(0, 20)

    def _gen_tvoc(self, t: float) -> float:
        co2 = self._state.get("co2", 520.0)
        return 150.0 + (co2 - 400.0) * 0.4 + self.rng.gauss(0, 25)

    def _gen_pm25(self, t: float) -> float:
        base = 12.0 + self.rng.gauss(0, 2)
        peak = 0.0

        if self._pm_peak_active:
            elapsed = t - self._pm_peak_start_time
            if elapsed > self._pm_peak_duration:
                self._pm_peak_active = False
                self._next_pm_peak_time = t + self.rng.uniform(60, 120)
            else:
                center = self._pm_peak_duration / 2
                intensity = 50 + self.rng.uniform(0, 30)
                sigma = self._pm_peak_duration / 4
                peak = intensity * math.exp(
                    -((elapsed - center) ** 2) / (2 * sigma ** 2)
                )
        elif t >= self._next_pm_peak_time:
            self._pm_peak_active = True
            self._pm_peak_start_time = t
            self._pm_peak_duration = self.rng.uniform(10, 20)

        return base + peak

    def _gen_pm10(self, t: float) -> float:
        pm25 = self._state.get("pm25", 12.0)
        return pm25 + self.rng.uniform(3, 20)

    def _gen_lux(self, t: float) -> float:
        sin_norm = (math.sin(t / 300.0) + 1) / 2
        return 50.0 + sin_norm * 800.0 + self.rng.gauss(0, 10)

    def _gen_noise(self, t: float) -> float:
        return 38.0 + math.sin(t / 20.0) * 3.0 + self.rng.gauss(0, 1)

    # -- Boolean / counter patterns --

    def _gen_boolean_rare(self, t: float) -> float:
        """Mostly 0, with rare ON pulses (~5% of the time)."""
        return 1.0 if self.rng.random() < 0.05 else 0.0

    def _gen_boolean_periodic(self, t: float) -> float:
        """Square wave with ~30s period."""
        return 1.0 if int(t / 15.0) % 2 == 0 else 0.0

    def _gen_uptime(self, t: float) -> float:
        """Monotonic counter: seconds since simulator start."""
        return float(int(t))

    # -- Generic pattern for custom measurements --

    def _gen_generic(self, config: MeasurementConfig, t: float) -> float:
        """Sinusoidal + noise pattern based on configured range."""
        mid = (config.min_value + config.max_value) / 2
        amplitude = (config.max_value - config.min_value) * 0.15
        noise_std = amplitude * 0.05
        # Derive a unique period from the measurement name
        period = 120.0 + (hash(config.name) % 180)
        return mid + math.sin(t / period) * amplitude + self.rng.gauss(0, noise_std)
