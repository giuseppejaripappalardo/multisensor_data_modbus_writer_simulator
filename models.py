"""
Data models for the sensor simulator.
"""
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class ModbusConfig(BaseModel):
    """Modbus TCP connection configuration."""
    host: str = "127.0.0.1"
    port: int = 502
    unit_id: int = 1
    connect_timeout_ms: int = 3000
    write_timeout_ms: int = 1000
    max_retry_attempts: int = 3
    backoff_seconds: List[float] = Field(default_factory=lambda: [1.0, 2.0, 5.0])


class RatesConfig(BaseModel):
    """Update rates for each measurement type in seconds."""
    temperature: float = 1.0
    humidity: float = 1.0
    lux: float = 1.0
    noise: float = 1.0
    co2: float = 5.0
    tvoc: float = 5.0
    pm25: float = 10.0
    pm10: float = 10.0


class UpdateConfig(BaseModel):
    """Main loop update configuration."""
    tick_seconds: float = 1.0
    rates: RatesConfig = Field(default_factory=RatesConfig)


class RegisterMap(BaseModel):
    """Mapping of measurement names to register offsets."""
    temperature: int = 0
    humidity: int = 1
    co2: int = 2
    tvoc: int = 3
    pm25: int = 4
    pm10: int = 5
    lux: int = 6
    noise: int = 7

    def to_dict(self) -> Dict[str, int]:
        """Convert register map to dictionary."""
        return {
            "temperature": self.temperature,
            "humidity": self.humidity,
            "co2": self.co2,
            "tvoc": self.tvoc,
            "pm25": self.pm25,
            "pm10": self.pm10,
            "lux": self.lux,
            "noise": self.noise,
        }


class SensorConfig(BaseModel):
    """Configuration for a single sensor."""
    id: str
    base_address: int = 0
    absolute: bool = False
    register_map: RegisterMap = Field(default_factory=RegisterMap)

    def get_register_address(self, measurement: str) -> int:
        """
        Get the actual Modbus register address for a measurement.

        Args:
            measurement: Name of the measurement (temperature, humidity, etc.)

        Returns:
            The absolute register address.
        """
        offset = getattr(self.register_map, measurement)
        if self.absolute:
            return offset
        return self.base_address + offset


class AppConfig(BaseModel):
    """Complete application configuration."""
    modbus: ModbusConfig = Field(default_factory=ModbusConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    sensors: List[SensorConfig] = Field(default_factory=list)
    log_level: str = "INFO"

    @field_validator("sensors", mode="before")
    @classmethod
    def validate_sensors(cls, v):
        """Ensure sensors is a list."""
        if v is None:
            return []
        return v


# Measurement types available in the simulator
MEASUREMENT_TYPES = [
    "temperature",
    "humidity",
    "co2",
    "tvoc",
    "pm25",
    "pm10",
    "lux",
    "noise",
]


# Scaling factors for each measurement type
# Format: (scale_factor, is_signed)
SCALING_CONFIG = {
    "temperature": (10, True),    # int16, value = °C * 10
    "humidity": (10, False),       # uint16, value = % * 10
    "noise": (10, False),          # uint16, value = dB * 10
    "co2": (1, False),             # uint16, integer
    "tvoc": (1, False),            # uint16, integer
    "pm25": (1, False),            # uint16, integer
    "pm10": (1, False),            # uint16, integer
    "lux": (1, False),             # uint16, integer
}


# Value ranges for clamping (min, max) - in real units before scaling
VALUE_RANGES = {
    "temperature": (-40.0, 80.0),
    "humidity": (0.0, 100.0),
    "noise": (0.0, 130.0),
    "co2": (0, 5000),
    "tvoc": (0, 5000),
    "pm25": (0, 500),
    "pm10": (0, 500),
    "lux": (0, 65535),
}

