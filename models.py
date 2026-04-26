"""
Data models for the sensor simulator.

Supports all standard Modbus data types with configurable endianness per sensor.
"""
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class DataType(str, Enum):
    """Supported Modbus data types."""
    UINT16 = "uint16"
    INT16 = "int16"
    UINT32 = "uint32"
    INT32 = "int32"
    FLOAT32 = "float32"
    FLOAT64 = "float64"


# Number of 16-bit registers required for each data type
REGISTERS_PER_TYPE: Dict[DataType, int] = {
    DataType.UINT16: 1,
    DataType.INT16: 1,
    DataType.UINT32: 2,
    DataType.INT32: 2,
    DataType.FLOAT32: 2,   # 2 x 16-bit = 32 bit
    DataType.FLOAT64: 4,   # 4 x 16-bit = 64 bit
}


class ModbusConfig(BaseModel):
    """Modbus TCP connection configuration."""
    host: str = "127.0.0.1"
    port: int = 502
    unit_id: int = 1
    connect_timeout_ms: int = 3000
    write_timeout_ms: int = 1000
    max_retry_attempts: int = 3
    backoff_seconds: List[float] = Field(default_factory=lambda: [1.0, 2.0, 5.0])


class MeasurementConfig(BaseModel):
    """Configuration for a single measurement within a sensor."""
    name: str
    offset: int
    data_type: DataType = DataType.UINT16
    scale: float = 1.0
    min_value: float = 0.0
    max_value: float = 65535.0
    update_rate: float = 1.0

    @property
    def register_count(self) -> int:
        """Number of 16-bit registers used by this measurement."""
        return REGISTERS_PER_TYPE[self.data_type]


class SensorConfig(BaseModel):
    """Configuration for a single sensor."""
    id: str
    base_address: int = 0
    byte_order: Literal["big", "little"] = "big"
    word_order: Literal["big", "little"] = "big"
    measurements: List[MeasurementConfig] = Field(default_factory=list)

    def get_measurement(self, name: str) -> Optional[MeasurementConfig]:
        """Find a measurement by name."""
        for m in self.measurements:
            if m.name == name:
                return m
        return None

    def get_register_address(self, measurement_name: str) -> int:
        """Get the absolute register address for a measurement."""
        m = self.get_measurement(measurement_name)
        if m is None:
            raise ValueError(f"Unknown measurement: {measurement_name}")
        return self.base_address + m.offset


class AppConfig(BaseModel):
    """Complete application configuration."""
    modbus: ModbusConfig = Field(default_factory=ModbusConfig)
    sensors: List[SensorConfig] = Field(default_factory=list)
    tick_seconds: float = 1.0
    log_level: str = "INFO"

    @field_validator("sensors", mode="before")
    @classmethod
    def validate_sensors(cls, v):
        if v is None:
            return []
        return v
