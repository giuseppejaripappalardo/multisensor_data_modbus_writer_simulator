"""
Round-trip tests for encode_value/decode_value across all Modbus data types
and all byte_order/word_order combinations.

Run from the project root:
    python -m pytest tests/
or:
    python tests/test_encoder.py
"""
import os
import sys
import struct
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models import DataType  # noqa: E402
from simulator.encoder import encode_value, decode_value  # noqa: E402


ENDIAN_COMBOS = [
    ("big", "big"),
    ("big", "little"),
    ("little", "big"),
    ("little", "little"),
]


class EndiannessTests(unittest.TestCase):
    """Assert that encode_value -> decode_value is an identity function."""

    def assert_roundtrip(self, value, data_type, scale, lo, hi):
        for byte_order, word_order in ENDIAN_COMBOS:
            with self.subTest(
                value=value, data_type=data_type.value,
                byte_order=byte_order, word_order=word_order,
            ):
                regs = encode_value(
                    value, data_type, scale=scale,
                    min_value=lo, max_value=hi,
                    byte_order=byte_order, word_order=word_order,
                )
                decoded = decode_value(
                    regs, data_type, scale=scale,
                    byte_order=byte_order, word_order=word_order,
                )
                if data_type == DataType.FLOAT32:
                    # Float32 has ~7 decimal digits of precision; use relative tolerance.
                    rel_tol = 1e-5 * max(abs(value), 1.0)
                    self.assertLessEqual(abs(decoded - value), rel_tol)
                elif data_type == DataType.FLOAT64:
                    rel_tol = 1e-12 * max(abs(value), 1.0)
                    self.assertLessEqual(abs(decoded - value), rel_tol)
                else:
                    self.assertEqual(decoded, value)

    def test_uint16_roundtrip(self):
        for v in [0, 1, 253, 12345, 65535]:
            self.assert_roundtrip(v, DataType.UINT16, scale=1.0, lo=0, hi=65535)

    def test_uint16_with_scale(self):
        # 25.3 with scale 10 -> 253 stored, 253/10 = 25.3 decoded
        for v in [0.0, 25.3, 100.0]:
            self.assert_roundtrip(v, DataType.UINT16, scale=10.0, lo=0.0, hi=6553.5)

    def test_int16_roundtrip(self):
        for v in [-32768, -1, 0, 1, 32767]:
            self.assert_roundtrip(v, DataType.INT16, scale=1.0, lo=-32768, hi=32767)

    def test_int16_negative_with_scale(self):
        for v in [-40.0, -10.5, 0.0, 25.3, 80.0]:
            self.assert_roundtrip(v, DataType.INT16, scale=10.0, lo=-40.0, hi=80.0)

    def test_uint32_roundtrip(self):
        for v in [0, 1, 65536, 1234567, 0xFFFFFFFF]:
            self.assert_roundtrip(v, DataType.UINT32, scale=1.0, lo=0, hi=0xFFFFFFFF)

    def test_int32_roundtrip(self):
        for v in [-2147483648, -1, 0, 1, 2147483647]:
            self.assert_roundtrip(
                v, DataType.INT32, scale=1.0, lo=-2147483648, hi=2147483647
            )

    def test_float32_roundtrip(self):
        for v in [0.0, 1.0, -1.0, 25.3, 3.14159, -123.456, 1e20]:
            self.assert_roundtrip(v, DataType.FLOAT32, scale=1.0, lo=-1e30, hi=1e30)

    def test_float64_roundtrip(self):
        for v in [0.0, 1.0, -1.0, 25.3, 3.14159265358979, -123.456789, 1e100]:
            self.assert_roundtrip(v, DataType.FLOAT64, scale=1.0, lo=-1e200, hi=1e200)

    # ------------------------------------------------------------------
    # Cross-check: register layout matches expected byte pattern AABBCCDD.
    # ------------------------------------------------------------------

    def test_byte_pattern_uint32(self):
        """0xAABBCCDD must encode to known register layouts for each combo."""
        # struct.pack(">I", 0xAABBCCDD) -> b'\xAA\xBB\xCC\xDD'
        # big/big   -> [0xAABB, 0xCCDD]
        # big/little -> [0xCCDD, 0xAABB]
        # little/big -> [0xBBAA, 0xDDCC]
        # little/little -> [0xDDCC, 0xBBAA]
        cases = {
            ("big", "big"):       [0xAABB, 0xCCDD],
            ("big", "little"):    [0xCCDD, 0xAABB],
            ("little", "big"):    [0xBBAA, 0xDDCC],
            ("little", "little"): [0xDDCC, 0xBBAA],
        }
        for (bo, wo), expected in cases.items():
            with self.subTest(byte_order=bo, word_order=wo):
                regs = encode_value(
                    0xAABBCCDD, DataType.UINT32, scale=1.0,
                    min_value=0, max_value=0xFFFFFFFF,
                    byte_order=bo, word_order=wo,
                )
                self.assertEqual(regs, expected)

    def test_byte_pattern_uint16(self):
        """0xAABB must encode as [0xAABB] big and [0xBBAA] little."""
        regs_big = encode_value(
            0xAABB, DataType.UINT16, scale=1.0, min_value=0, max_value=0xFFFF,
            byte_order="big", word_order="big",
        )
        self.assertEqual(regs_big, [0xAABB])

        regs_little = encode_value(
            0xAABB, DataType.UINT16, scale=1.0, min_value=0, max_value=0xFFFF,
            byte_order="little", word_order="big",
        )
        self.assertEqual(regs_little, [0xBBAA])

    def test_float32_known_pattern(self):
        """Float 25.3 must encode/decode consistently across combos."""
        packed_be = struct.pack(">f", 25.3)
        regs_bb = [(packed_be[0] << 8) | packed_be[1],
                   (packed_be[2] << 8) | packed_be[3]]
        out = encode_value(
            25.3, DataType.FLOAT32, scale=1.0, min_value=-1e30, max_value=1e30,
            byte_order="big", word_order="big",
        )
        self.assertEqual(out, regs_bb)


if __name__ == "__main__":
    unittest.main(verbosity=2)
