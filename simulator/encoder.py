"""
Value encoder/decoder for Modbus registers.

Supports all standard Modbus data types (uint16, int16, uint32, int32, float32, float64)
with configurable byte order and word order (endianness).

Endianness combinations (example for a 32-bit value 0xAABBCCDD):
  byte_order="big",    word_order="big"    -> AABB CCDD  (Big Endian, standard Modbus)
  byte_order="big",    word_order="little" -> CCDD AABB  (Word-swapped)
  byte_order="little", word_order="big"    -> BBAA DDCC  (Byte-swapped)
  byte_order="little", word_order="little" -> DDCC BBAA  (Little Endian)

For 16-bit types (uint16/int16) only byte_order matters; word_order is a no-op.
"""
import struct
from typing import List

from models import DataType, REGISTERS_PER_TYPE
from utils.clamp import clamp


_PACK_FORMATS = {
    DataType.UINT16:  ">H",
    DataType.INT16:   ">h",
    DataType.UINT32:  ">I",
    DataType.INT32:   ">i",
    DataType.FLOAT32: ">f",
    DataType.FLOAT64: ">d",
}

_INTEGER_RANGES = {
    DataType.UINT16: (0, 0xFFFF),
    DataType.INT16:  (-32768, 32767),
    DataType.UINT32: (0, 0xFFFFFFFF),
    DataType.INT32:  (-2147483648, 2147483647),
}


def encode_value(
    raw_value: float,
    data_type: DataType,
    scale: float = 1.0,
    min_value: float = 0.0,
    max_value: float = 65535.0,
    byte_order: str = "big",
    word_order: str = "big",
) -> List[int]:
    """
    Encode a raw measurement value into one or more 16-bit Modbus register values.

    Args:
        raw_value: Value in real units.
        data_type: Target Modbus data type.
        scale: Multiplier applied before encoding (e.g. 10 for 1 decimal).
        min_value: Minimum allowed raw value (clamped before scaling).
        max_value: Maximum allowed raw value (clamped before scaling).
        byte_order: Byte order within each 16-bit register ("big" or "little").
        word_order: Word order across registers ("big" or "little").

    Returns:
        List of uint16 register values.
    """
    if data_type not in _PACK_FORMATS:
        raise ValueError(f"Unsupported data type: {data_type}")

    clamped = clamp(raw_value, min_value, max_value)
    scaled = clamped * scale

    fmt = _PACK_FORMATS[data_type]

    if data_type in _INTEGER_RANGES:
        lo, hi = _INTEGER_RANGES[data_type]
        int_val = clamp(int(round(scaled)), lo, hi)
        packed = struct.pack(fmt, int_val)
    else:
        packed = struct.pack(fmt, float(scaled))

    return _bytes_to_registers(packed, byte_order, word_order)


def decode_value(
    registers: List[int],
    data_type: DataType,
    scale: float = 1.0,
    byte_order: str = "big",
    word_order: str = "big",
) -> float:
    """
    Decode Modbus register(s) back to a real-unit value.

    Args:
        registers: List of uint16 register values.
        data_type: Source Modbus data type.
        scale: Divisor applied after decoding (use the same scale used at encode time).
        byte_order: Byte order within each 16-bit register.
        word_order: Word order across registers.

    Returns:
        Decoded value in real units (i.e. raw decoded / scale).
    """
    if data_type not in _PACK_FORMATS:
        raise ValueError(f"Unsupported data type: {data_type}")

    expected = REGISTERS_PER_TYPE[data_type]
    if len(registers) != expected:
        raise ValueError(
            f"Wrong register count for {data_type.value}: "
            f"got {len(registers)}, expected {expected}"
        )

    packed = _registers_to_bytes(registers, byte_order, word_order)
    value = struct.unpack(_PACK_FORMATS[data_type], packed)[0]

    if scale == 0:
        return float(value)
    return value / scale


def _bytes_to_registers(packed: bytes, byte_order: str, word_order: str) -> List[int]:
    """
    Convert a big-endian packed byte string into 16-bit register values
    applying byte_order and word_order.
    """
    if len(packed) % 2 != 0:
        raise ValueError(f"Packed length must be a multiple of 2, got {len(packed)}")

    # Split into 16-bit words, big-endian within each word (we packed BE).
    words = [(packed[i] << 8) | packed[i + 1] for i in range(0, len(packed), 2)]

    # Swap bytes within each word if little-endian byte order requested.
    if byte_order == "little":
        words = [((w & 0xFF) << 8) | ((w >> 8) & 0xFF) for w in words]

    # Reverse register order if little-endian word order requested.
    if word_order == "little":
        words = words[::-1]

    return words


def _registers_to_bytes(registers: List[int], byte_order: str, word_order: str) -> bytes:
    """
    Reverse of _bytes_to_registers: rebuild the big-endian byte string from
    register values produced under the given byte_order/word_order.
    """
    regs = list(registers)

    if word_order == "little":
        regs = regs[::-1]

    if byte_order == "little":
        regs = [((r & 0xFF) << 8) | ((r >> 8) & 0xFF) for r in regs]

    out = bytearray()
    for r in regs:
        out += struct.pack(">H", r & 0xFFFF)
    return bytes(out)
