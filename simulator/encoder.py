"""
Value encoder for Modbus registers.

Supports all standard Modbus data types (uint16, int16, uint32, int32, float32, float64)
with configurable byte order and word order (endianness).

Endianness combinations:
  byte_order="big",  word_order="big"    -> AB CD  (Big Endian, standard Modbus)
  byte_order="big",  word_order="little" -> CD AB  (Word-swapped)
  byte_order="little", word_order="big"  -> BA DC  (Byte-swapped)
  byte_order="little", word_order="little" -> DC BA (Little Endian)
"""
import struct
from typing import List

from models import DataType
from utils.clamp import clamp


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
    Encode a raw measurement value into one or more Modbus register values.

    Args:
        raw_value: Value in real units.
        data_type: Target Modbus data type.
        scale: Multiplier applied before encoding (e.g., 10 for 1 decimal place).
        min_value: Minimum allowed value (before scaling).
        max_value: Maximum allowed value (before scaling).
        byte_order: Byte order within each 16-bit register ("big" or "little").
        word_order: Word order for multi-register types ("big" or "little").

    Returns:
        List of uint16 register values.
    """
    clamped = clamp(raw_value, min_value, max_value)
    scaled = clamped * scale

    if data_type == DataType.UINT16:
        int_val = clamp(int(round(scaled)), 0, 0xFFFF) & 0xFFFF
        if byte_order == "little":
            int_val = ((int_val & 0xFF) << 8) | ((int_val >> 8) & 0xFF)
        return [int_val]

    elif data_type == DataType.INT16:
        int_val = clamp(int(round(scaled)), -32768, 32767)
        if int_val < 0:
            int_val += 0x10000
        int_val &= 0xFFFF
        if byte_order == "little":
            int_val = ((int_val & 0xFF) << 8) | ((int_val >> 8) & 0xFF)
        return [int_val]

    elif data_type == DataType.UINT32:
        int_val = clamp(int(round(scaled)), 0, 0xFFFFFFFF)
        packed = struct.pack(">I", int_val)
        return _bytes_to_registers(packed, byte_order, word_order)

    elif data_type == DataType.INT32:
        int_val = clamp(int(round(scaled)), -2147483648, 2147483647)
        packed = struct.pack(">i", int_val)
        return _bytes_to_registers(packed, byte_order, word_order)

    elif data_type == DataType.FLOAT32:
        packed = struct.pack(">f", float(scaled))
        return _bytes_to_registers(packed, byte_order, word_order)

    elif data_type == DataType.FLOAT64:
        packed = struct.pack(">d", float(scaled))
        return _bytes_to_registers(packed, byte_order, word_order)

    else:
        raise ValueError(f"Unsupported data type: {data_type}")


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
        scale: Divisor applied after decoding.
        byte_order: Byte order within each 16-bit register.
        word_order: Word order for multi-register types.

    Returns:
        Decoded value in real units.
    """
    packed = _registers_to_bytes(registers, byte_order, word_order)

    if data_type == DataType.UINT16:
        value = struct.unpack(">H", packed)[0]
    elif data_type == DataType.INT16:
        value = struct.unpack(">h", packed)[0]
    elif data_type == DataType.UINT32:
        value = struct.unpack(">I", packed)[0]
    elif data_type == DataType.INT32:
        value = struct.unpack(">i", packed)[0]
    elif data_type == DataType.FLOAT32:
        value = struct.unpack(">f", packed)[0]
    elif data_type == DataType.FLOAT64:
        value = struct.unpack(">d", packed)[0]
    else:
        raise ValueError(f"Unsupported data type: {data_type}")

    return value / scale if scale != 0 else value


def _bytes_to_registers(packed: bytes, byte_order: str, word_order: str) -> List[int]:
    """
    Convert packed big-endian bytes into 16-bit register values
    applying byte_order and word_order.
    """
    # Split into 16-bit words (big-endian byte order within each word)
    words = []
    for i in range(0, len(packed), 2):
        words.append((packed[i] << 8) | packed[i + 1])

    # Swap bytes within each word if little-endian byte order
    if byte_order == "little":
        words = [((w & 0xFF) << 8) | ((w >> 8) & 0xFF) for w in words]

    # Reverse word order if little-endian word order
    if word_order == "little":
        words = words[::-1]

    return words


def _registers_to_bytes(registers: List[int], byte_order: str, word_order: str) -> bytes:
    """
    Convert 16-bit register values back to big-endian packed bytes,
    reversing byte_order and word_order.
    """
    regs = list(registers)

    # Reverse word order
    if word_order == "little":
        regs = regs[::-1]

    # Reverse byte swap within each word
    if byte_order == "little":
        regs = [((r & 0xFF) << 8) | ((r >> 8) & 0xFF) for r in regs]

    # Reconstruct big-endian bytes
    packed = b""
    for r in regs:
        packed += struct.pack(">H", r & 0xFFFF)

    return packed
