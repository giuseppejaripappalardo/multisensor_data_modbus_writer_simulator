"""
Data models for the sensor simulator.

Supports all standard Modbus data types with configurable endianness per sensor,
plus fault-injection (latency, per-measurement and per-sensor errors) for
realistic testing of clients.
"""
from enum import Enum
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator


class DataType(str, Enum):
    """Supported Modbus data types (for register-based measurements)."""
    UINT16 = "uint16"
    INT16 = "int16"
    UINT32 = "uint32"
    INT32 = "int32"
    FLOAT32 = "float32"
    FLOAT64 = "float64"
    BOOL = "bool"  # Single bit; only valid for COIL / DISCRETE_INPUT


class RegisterType(str, Enum):
    """
    Modbus address spaces.

    Each space is independently addressed:
      - COIL              -> 1 bit, R/W (FC 01 read, FC 05/15 write)
      - DISCRETE_INPUT    -> 1 bit, R/O (FC 02 read)
      - INPUT_REGISTER    -> 16 bit, R/O (FC 04 read)
      - HOLDING_REGISTER  -> 16 bit, R/W (FC 03 read, FC 06/16 write)

    The simulator always writes on the server side regardless of R/W policy
    (it owns the device); the policy applies to *clients* connecting to it.
    """
    COIL = "coil"
    DISCRETE_INPUT = "discrete_input"
    INPUT_REGISTER = "input_register"
    HOLDING_REGISTER = "holding_register"


# Number of 16-bit registers required for each data type.
# For BOOL the unit is "1 bit" but we still account it as 1 slot in the
# coil/discrete_input address space.
REGISTERS_PER_TYPE: Dict[DataType, int] = {
    DataType.UINT16: 1,
    DataType.INT16: 1,
    DataType.UINT32: 2,
    DataType.INT32: 2,
    DataType.FLOAT32: 2,
    DataType.FLOAT64: 4,
    DataType.BOOL: 1,
}


def is_bit_space(register_type: "RegisterType") -> bool:
    """True if the address space stores 1-bit values (coil / discrete input)."""
    return register_type in (RegisterType.COIL, RegisterType.DISCRETE_INPUT)


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

    Read-side faults (server -> client) are evaluated when the client reads
    the registers backing this measurement: a slave-exception is returned
    for the affected addresses, leaving the rest of the sensor reachable.

    Write-side faults (simulator -> registers) alter the value the
    simulator writes, so reads still succeed but the client sees corrupted
    or unrealistic data.
    """
    # Read-side
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)  # per-read failure probability
    error_code: int = 2                                     # exception (2 = ILLEGAL DATA ADDRESS)

    # Write-side (simulator)
    frozen: bool = False             # value no longer updated by the simulator
    drop_writes: bool = False        # simulator does NOT write this measurement at all
    bit_flip_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    """Per-write probability of flipping a single random bit in the encoded
    register payload before writing. Useful to test client-side CRC / sanity
    checks. Independent of error_rate."""

    drift_per_second: float = 0.0
    """Linear drift applied to the generated value, in real units per second.
    Positive drifts the value upward; negative downward. The drift accumulates
    until clamped by min_value/max_value."""


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
    register_type: RegisterType = RegisterType.HOLDING_REGISTER
    data_type: DataType = DataType.UINT16
    scale: float = 1.0
    min_value: float = 0.0
    max_value: float = 65535.0
    update_rate: float = Field(default=1.0, gt=0)
    unit: Optional[str] = None
    fault: MeasurementFault = Field(default_factory=MeasurementFault)

    @model_validator(mode="after")
    def _align_data_type_to_register_type(self) -> "MeasurementConfig":
        """
        Keep `data_type` consistent with `register_type`:

          - coil / discrete_input  -> force BOOL (1 bit)
          - other spaces           -> coerce BOOL to UINT16

        Coercion (rather than raise) keeps PATCH-style updates ergonomic:
        the UI can flip register_type without first resetting data_type.
        Runs as model_validator (not field_validator) so it triggers even
        when data_type was left at its default.
        """
        if self.register_type in (RegisterType.COIL, RegisterType.DISCRETE_INPUT):
            if self.data_type != DataType.BOOL:
                self.data_type = DataType.BOOL
        else:
            if self.data_type == DataType.BOOL:
                self.data_type = DataType.UINT16
        return self

    @property
    def register_count(self) -> int:
        """Number of 16-bit registers used by this measurement (1 for bit types)."""
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

    def address_ranges_by_type(self) -> Dict["RegisterType", range]:
        """
        Return the absolute address range covered by this sensor,
        broken down per RegisterType (one range per address space used).
        """
        by_type: Dict["RegisterType", Tuple[int, int]] = {}
        for m in self.measurements:
            start = self.base_address + m.offset
            end = start + m.register_count
            cur = by_type.get(m.register_type)
            if cur is None:
                by_type[m.register_type] = (start, end)
            else:
                by_type[m.register_type] = (min(cur[0], start), max(cur[1], end))
        return {rt: range(s, e) for rt, (s, e) in by_type.items()}


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
