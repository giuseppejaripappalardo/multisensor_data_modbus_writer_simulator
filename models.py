"""
Data models for the sensor simulator.

Supports all standard Modbus data types with configurable endianness per sensor,
plus fault-injection (latency, per-measurement and per-sensor errors) for
realistic testing of clients.
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


# Number of 16-bit registers required for each data type.
REGISTERS_PER_TYPE: Dict[DataType, int] = {
    DataType.UINT16: 1,
    DataType.INT16: 1,
    DataType.UINT32: 2,
    DataType.INT32: 2,
    DataType.FLOAT32: 2,
    DataType.FLOAT64: 4,
}


class ModbusConfig(BaseModel):
    """Modbus TCP client connection configuration."""
    host: str = "127.0.0.1"
    port: int = 502
    unit_id: int = 1
    connect_timeout_ms: int = 3000
    write_timeout_ms: int = 1000
    max_retry_attempts: int = 3
    backoff_seconds: List[float] = Field(default_factory=lambda: [1.0, 2.0, 5.0])


class ServerConfig(BaseModel):
    """Embedded Modbus TCP server configuration."""
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=502, ge=1, le=65535)
    # Default unit_id pre-filled when creating a new sensor in the UI.
    default_unit_id: int = Field(default=1, ge=0, le=247)
    # Minimum number of holding registers allocated per unit_id. The actual
    # size is max(register_count_min, highest_address_used + 1) so reads
    # past the configured range return ILLEGAL DATA ADDRESS, just like
    # a real Modbus device.
    register_count_min: int = Field(default=16, ge=1)


class MeasurementFault(BaseModel):
    """
    Per-measurement fault injection.

    Errors are evaluated when the client *reads* the registers backing this
    measurement: a slave-exception (illegal data address) is returned for the
    affected addresses, leaving the rest of the sensor reachable.
    """
    error_rate: float = 0.0          # 0..1 probability per read
    error_code: int = 2              # Modbus exception code (2 = ILLEGAL DATA ADDRESS)
    frozen: bool = False             # If True, value is no longer updated by the simulator
    drop_writes: bool = False        # If True, simulator does not write this measurement at all


class SensorFault(BaseModel):
    """
    Per-sensor fault injection.

    Affects every measurement for that sensor:
      - latency_ms: artificial response delay applied by the server when the
        client touches addresses that belong to this sensor.
      - offline / error_rate: simulate a slave that does not respond at all.
    """
    latency_ms: int = 0              # Extra latency on read for this sensor
    offline: bool = False            # Sensor is fully unreachable (gateway target failed)
    error_rate: float = 0.0          # Probability of returning an exception per read
    error_code: int = 11             # Modbus exception code (11 = GATEWAY TARGET DEVICE FAILED)


class MeasurementConfig(BaseModel):
    """Configuration for a single measurement within a sensor."""
    name: str
    offset: int = Field(ge=0)
    data_type: DataType = DataType.UINT16
    scale: float = 1.0
    min_value: float = 0.0
    max_value: float = 65535.0
    update_rate: float = Field(default=1.0, gt=0)
    unit: Optional[str] = None
    fault: MeasurementFault = Field(default_factory=MeasurementFault)

    @property
    def register_count(self) -> int:
        """Number of 16-bit registers used by this measurement."""
        return REGISTERS_PER_TYPE[self.data_type]


class SensorConfig(BaseModel):
    """Configuration for a single sensor."""
    id: str
    # Modbus slave / device id this sensor exposes itself as. Sensors with
    # different unit_ids share the same TCP listener but live in independent
    # register spaces (gateway-style: one server, many slaves).
    unit_id: int = Field(default=1, ge=0, le=247)
    base_address: int = Field(default=0, ge=0)
    byte_order: Literal["big", "little"] = "big"
    word_order: Literal["big", "little"] = "big"
    # Minimum interval (seconds) between Modbus writes for this sensor.
    # The effective rate of each measurement is max(measurement.update_rate,
    # sensor.write_rate_seconds). Use 60 to write at most once a minute, 1
    # for the legacy "as fast as the measurement asks" behaviour.
    write_rate_seconds: float = Field(default=1.0, gt=0)
    measurements: List[MeasurementConfig] = Field(default_factory=list)
    fault: SensorFault = Field(default_factory=SensorFault)

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

    def address_range(self) -> Optional[range]:
        """Return the absolute register range covered by this sensor, or None if empty."""
        if not self.measurements:
            return None
        starts = [self.base_address + m.offset for m in self.measurements]
        ends = [
            self.base_address + m.offset + m.register_count
            for m in self.measurements
        ]
        return range(min(starts), max(ends))


class AppConfig(BaseModel):
    """Complete application configuration."""
    modbus: ModbusConfig = Field(default_factory=ModbusConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    sensors: List[SensorConfig] = Field(default_factory=list)
    tick_seconds: float = 1.0
    log_level: str = "INFO"

    @field_validator("sensors", mode="before")
    @classmethod
    def validate_sensors(cls, v):
        if v is None:
            return []
        return v
