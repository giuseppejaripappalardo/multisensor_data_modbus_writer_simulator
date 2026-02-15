"""
Value encoder with scaling and clamping for Modbus registers.
"""
from typing import Dict, Tuple

from models import SCALING_CONFIG, VALUE_RANGES
from utils.clamp import clamp
from utils.logging import get_logger

logger = get_logger(__name__)


def encode_value(measurement: str, raw_value: float) -> int:
    """
    Encode a raw measurement value to a Modbus register value.

    Applies:
    1. Clamping to valid range
    2. Scaling factor multiplication
    3. Conversion to int16/uint16

    Args:
        measurement: Measurement type name.
        raw_value: Raw value in real units.

    Returns:
        Encoded register value (int).
    """
    # Get scaling config
    scale_factor, is_signed = SCALING_CONFIG.get(measurement, (1, False))

    # Get value range for clamping
    min_val, max_val = VALUE_RANGES.get(measurement, (0, 65535))

    # Clamp to valid range
    clamped = clamp(raw_value, min_val, max_val)

    # Apply scaling
    scaled = clamped * scale_factor

    # Convert to integer
    int_value = int(round(scaled))

    # Clamp to register range
    if is_signed:
        # int16 range: -32768 to 32767
        int_value = clamp(int_value, -32768, 32767)
        # Convert to unsigned representation for Modbus
        if int_value < 0:
            int_value = int_value + 65536
    else:
        # uint16 range: 0 to 65535
        int_value = clamp(int_value, 0, 65535)

    return int_value


def encode_measurements(measurements: Dict[str, float]) -> Dict[str, Tuple[float, int]]:
    """
    Encode multiple measurement values.

    Args:
        measurements: Dictionary of measurement name to raw value.

    Returns:
        Dictionary of measurement name to (raw_value, encoded_value) tuple.
    """
    result = {}
    for name, raw_value in measurements.items():
        encoded = encode_value(name, raw_value)
        result[name] = (raw_value, encoded)
    return result


def decode_value(measurement: str, register_value: int) -> float:
    """
    Decode a Modbus register value back to real units.

    This is useful for logging and debugging.

    Args:
        measurement: Measurement type name.
        register_value: Encoded register value.

    Returns:
        Decoded value in real units.
    """
    scale_factor, is_signed = SCALING_CONFIG.get(measurement, (1, False))

    if is_signed:
        # Convert from unsigned to signed
        if register_value > 32767:
            register_value = register_value - 65536

    return register_value / scale_factor


def get_scaling_info(measurement: str) -> Tuple[int, bool, Tuple[float, float]]:
    """
    Get scaling information for a measurement type.

    Args:
        measurement: Measurement type name.

    Returns:
        Tuple of (scale_factor, is_signed, (min_val, max_val)).
    """
    scale_factor, is_signed = SCALING_CONFIG.get(measurement, (1, False))
    value_range = VALUE_RANGES.get(measurement, (0, 65535))
    return scale_factor, is_signed, value_range

