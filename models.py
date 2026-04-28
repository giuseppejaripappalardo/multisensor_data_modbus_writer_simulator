"""
Data models for the sensor simulator.

Supports all standard Modbus data types with configurable endianness per sensor,
plus fault-injection (latency, per-measurement and per-sensor errors) for
realistic testing of clients.

Topology
--------

Each AppConfig holds a list of ServerConfig (each is an independent Modbus TCP
listener on its own host:port). Each ServerConfig owns a list of SensorConfig.
Sensors with different unit_id within the same server live in independent
register spaces (gateway-style: one server, many slaves).

Legacy YAML files using a single top-level `server` and a flat top-level
`sensors` list are migrated transparently at load time (see AppConfig._migrate_legacy).
"""
import re
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple

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

# Wildcard hosts: a server bound to any of these on a given port collides
# with any other server on the same port.
_WILDCARD_HOSTS = {"0.0.0.0", "::", ""}

# Allowed identifier pattern for server.id (used in URL paths).
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def is_bit_space(register_type: "RegisterType") -> bool:
    """True if the address space stores 1-bit values (coil / discrete input)."""
    return register_type in (RegisterType.COIL, RegisterType.DISCRETE_INPUT)


class ModbusConfig(BaseModel):
    """Modbus TCP client connection configuration (legacy CLI path)."""
    host: str = "127.0.0.1"
    port: int = 502
    unit_id: int = 1
    connect_timeout_ms: int = 3000
    write_timeout_ms: int = 1000
    max_retry_attempts: int = 3
    backoff_seconds: List[float] = Field(default_factory=lambda: [1.0, 2.0, 5.0])


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

    @model_validator(mode="after")
    def _reject_saturating_range(self) -> "MeasurementConfig":
        """
        Reject combinations of (data_type, scale, min_value, max_value) that
        would silently saturate the register at simulation time.

        The encoder (simulator/encoder.py) clamps to the data_type's integer
        range after multiplying by scale, which means out-of-range values
        become 0xFFFF / 0x0000 / etc. without any error — clients see
        garbage data. We catch the misconfiguration up-front instead.

        Float types are skipped (FLOAT32/FLOAT64 represent any real number)
        and bit-space types are skipped (single bit, no range concern).
        """
        if is_bit_space(self.register_type):
            return self
        int_ranges = {
            DataType.UINT16: (0, 65_535),
            DataType.INT16:  (-32_768, 32_767),
            DataType.UINT32: (0, 4_294_967_295),
            DataType.INT32:  (-2_147_483_648, 2_147_483_647),
        }
        if self.data_type not in int_ranges:
            return self
        lo, hi = int_ranges[self.data_type]
        max_scaled = self.max_value * self.scale
        min_scaled = self.min_value * self.scale
        if max_scaled > hi:
            raise ValueError(
                f"Measurement '{self.name}': max_value {self.max_value} × scale "
                f"{self.scale} = {max_scaled:g} satura {self.data_type.value} "
                f"(range [{lo}, {hi}]). Riduci lo scale o usa un data_type più ampio."
            )
        if min_scaled < lo:
            raise ValueError(
                f"Measurement '{self.name}': min_value {self.min_value} × scale "
                f"{self.scale} = {min_scaled:g} satura {self.data_type.value} "
                f"(range [{lo}, {hi}]). Riduci lo scale o usa un data_type signed/più ampio."
            )
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


class ServerConfig(BaseModel):
    """
    Configuration for one embedded Modbus TCP listener.

    Each server is fully independent: its own (host, port), its own slave
    contexts, its own sensors. Within a server, sensors with different
    `unit_id` are exposed as distinct slaves behind the same listener
    (gateway-style).
    """
    id: str                             # Stable identifier (used in URLs).
    label: Optional[str] = None         # Display name for the UI.
    description: Optional[str] = None
    host: str = "0.0.0.0"
    port: int = Field(default=502, ge=1, le=65535)
    # Default unit_id pre-filled when creating a new sensor in this server.
    default_unit_id: int = Field(default=1, ge=0, le=247)
    # Minimum number of registers allocated per (unit_id, register_type).
    # The actual size is max(register_count_min, highest_address_used + 1).
    register_count_min: int = Field(default=16, ge=1)
    # If true, the server is started automatically when the runtime boots.
    auto_start: bool = False
    sensors: List[SensorConfig] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _ID_RE.match(v):
            raise ValueError(
                f"Invalid server id '{v}': must match {_ID_RE.pattern} "
                "(letters, digits, underscore, dash)"
            )
        return v

    @model_validator(mode="after")
    def _validate_sensors(self) -> "ServerConfig":
        # Unique sensor id within this server.
        ids = [s.id for s in self.sensors]
        dup = _first_duplicate(ids)
        if dup is not None:
            raise ValueError(
                f"Server '{self.id}': duplicate sensor id '{dup}'"
            )
        # No address overlap within (unit_id, register_type) for this server.
        # Each measurement reserves [base_address+offset, ...+register_count).
        # Two measurements on the same (uid, rt) cannot share any address.
        # Coil 0 and HoldingRegister 0 are independent — checked per (uid, rt).
        seen: Dict[Tuple[int, RegisterType], List[Tuple[int, int, str, str]]] = {}
        for sensor in self.sensors:
            for m in sensor.measurements:
                start = sensor.base_address + m.offset
                end = start + m.register_count - 1
                key = (sensor.unit_id, m.register_type)
                bucket = seen.setdefault(key, [])
                for (s2, e2, sid2, mname2) in bucket:
                    if start <= e2 and s2 <= end:
                        raise ValueError(
                            f"Server '{self.id}': address overlap on "
                            f"unit {sensor.unit_id}/{m.register_type.value} "
                            f"between '{sensor.id}.{m.name}' [{start}..{end}] and "
                            f"'{sid2}.{mname2}' [{s2}..{e2}]"
                        )
                bucket.append((start, end, sensor.id, m.name))
        return self


class AppConfig(BaseModel):
    """Complete application configuration."""
    modbus: ModbusConfig = Field(default_factory=ModbusConfig)
    servers: List[ServerConfig] = Field(default_factory=list)
    tick_seconds: float = 1.0
    log_level: str = "INFO"

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: Any) -> Any:
        """
        Migrate legacy schema (`server: {...}` + flat `sensors: [...]`) into
        the new schema (`servers: [{... sensors: [...]}]`).

        Mixed schema (both legacy and new keys present) is rejected so the
        user notices the half-migrated YAML instead of silently picking one.
        """
        if not isinstance(data, dict):
            return data
        has_legacy_server = "server" in data and data.get("server") is not None
        has_legacy_sensors = "sensors" in data and data.get("sensors") is not None
        has_new_servers = "servers" in data and data.get("servers") is not None
        if has_new_servers and (has_legacy_server or has_legacy_sensors):
            raise ValueError(
                "Mixed configuration schema: use either legacy 'server'/'sensors' "
                "at the root or the new 'servers' list, not both."
            )
        if has_legacy_server or has_legacy_sensors:
            legacy_server = dict(data.pop("server", {}) or {})
            legacy_sensors = list(data.pop("sensors", []) or [])
            # `enabled` was a dead field on the old ServerConfig — drop it
            # so the new ServerConfig validator doesn't choke on an extra key.
            legacy_server.pop("enabled", None)
            wrapped = {
                "id": "default",
                "label": "Default server",
                **legacy_server,
                "sensors": legacy_sensors,
            }
            data["servers"] = [wrapped]
        return data

    @model_validator(mode="after")
    def _validate_servers(self) -> "AppConfig":
        # Unique server id.
        ids = [s.id for s in self.servers]
        dup = _first_duplicate(ids)
        if dup is not None:
            raise ValueError(f"Duplicate server id '{dup}'")
        # Unique (host, port) with wildcard canonicalization: 0.0.0.0/:: on
        # a given port collides with any other host on the same port.
        by_port: Dict[int, List[ServerConfig]] = {}
        for s in self.servers:
            by_port.setdefault(s.port, []).append(s)
        for port, group in by_port.items():
            if len(group) <= 1:
                continue
            wildcards = [s for s in group if s.host in _WILDCARD_HOSTS]
            if wildcards and len(group) > 1:
                w = wildcards[0]
                others = ", ".join(f"'{s.id}'" for s in group if s is not w)
                raise ValueError(
                    f"Port {port} bound by '{w.id}' on wildcard host "
                    f"'{w.host}' conflicts with: {others}"
                )
            seen: Dict[str, str] = {}
            for s in group:
                if s.host in seen:
                    raise ValueError(
                        f"Servers '{seen[s.host]}' and '{s.id}' both bind {s.host}:{port}"
                    )
                seen[s.host] = s.id
        return self

    def find_server(self, server_id: str) -> Optional[ServerConfig]:
        """Look up a server by id."""
        for s in self.servers:
            if s.id == server_id:
                return s
        return None


def _first_duplicate(items: List[str]) -> Optional[str]:
    seen: set = set()
    for x in items:
        if x in seen:
            return x
        seen.add(x)
    return None
