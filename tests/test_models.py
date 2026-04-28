"""
Tests for the AppConfig schema: legacy migration and uniqueness validators.

Run from the project root:
    python -m unittest tests.test_models
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pydantic import ValidationError

from models import (  # noqa: E402
    AppConfig,
    DataType,
    MeasurementConfig,
    RegisterType,
    SensorConfig,
    ServerConfig,
)


class LegacyMigrationTests(unittest.TestCase):
    """Legacy YAML (single `server` + flat `sensors`) is wrapped into one ServerConfig."""

    def test_legacy_config_wrapped_into_default_server(self):
        legacy = {
            "server": {"host": "0.0.0.0", "port": 5020, "default_unit_id": 2},
            "sensors": [
                {"id": "s1", "unit_id": 1, "base_address": 0,
                 "measurements": [{"name": "t", "offset": 0}]}
            ],
            "tick_seconds": 0.5,
        }
        cfg = AppConfig(**legacy)
        self.assertEqual(len(cfg.servers), 1)
        self.assertEqual(cfg.servers[0].id, "default")
        self.assertEqual(cfg.servers[0].port, 5020)
        self.assertEqual(cfg.servers[0].default_unit_id, 2)
        self.assertEqual(len(cfg.servers[0].sensors), 1)
        self.assertEqual(cfg.servers[0].sensors[0].id, "s1")
        self.assertEqual(cfg.tick_seconds, 0.5)

    def test_legacy_with_only_sensors_still_wrapped(self):
        cfg = AppConfig(**{
            "sensors": [{"id": "lonely", "unit_id": 1,
                         "measurements": [{"name": "x", "offset": 0}]}],
        })
        self.assertEqual(len(cfg.servers), 1)
        self.assertEqual(cfg.servers[0].id, "default")
        self.assertEqual(cfg.servers[0].sensors[0].id, "lonely")

    def test_legacy_drops_unused_enabled_field(self):
        cfg = AppConfig(**{
            "server": {"enabled": True, "host": "0.0.0.0", "port": 5020},
            "sensors": [],
        })
        self.assertEqual(len(cfg.servers), 1)
        # `enabled` is dropped silently — the new ServerConfig has no such field.
        self.assertFalse(hasattr(cfg.servers[0], "enabled"))

    def test_mixed_schema_rejected(self):
        with self.assertRaisesRegex(ValidationError, "Mixed configuration schema"):
            AppConfig(**{
                "server": {"host": "0.0.0.0"},
                "servers": [{"id": "a", "host": "0.0.0.0", "port": 5020}],
            })

    def test_new_schema_passes_through_unchanged(self):
        cfg = AppConfig(**{
            "servers": [
                {"id": "a", "host": "0.0.0.0", "port": 5020,
                 "sensors": [{"id": "s1", "measurements": [{"name": "t", "offset": 0}]}]}
            ],
        })
        self.assertEqual(cfg.servers[0].id, "a")


class UniqueServerIdTests(unittest.TestCase):
    def test_duplicate_server_id_rejected(self):
        with self.assertRaisesRegex(ValidationError, "Duplicate server id 'a'"):
            AppConfig(servers=[
                ServerConfig(id="a", port=5020),
                ServerConfig(id="a", port=5021),
            ])

    def test_invalid_server_id_chars_rejected(self):
        with self.assertRaises(ValidationError):
            ServerConfig(id="bad id with spaces", port=5020)
        with self.assertRaises(ValidationError):
            ServerConfig(id="path/like", port=5020)


class UniqueHostPortTests(unittest.TestCase):
    def test_same_host_port_rejected(self):
        with self.assertRaisesRegex(ValidationError, "both bind 127.0.0.1:5020"):
            AppConfig(servers=[
                ServerConfig(id="a", host="127.0.0.1", port=5020),
                ServerConfig(id="b", host="127.0.0.1", port=5020),
            ])

    def test_wildcard_collides_with_specific_host_same_port(self):
        with self.assertRaisesRegex(ValidationError, "wildcard host"):
            AppConfig(servers=[
                ServerConfig(id="a", host="0.0.0.0", port=5020),
                ServerConfig(id="b", host="192.168.1.5", port=5020),
            ])

    def test_two_wildcards_same_port_rejected(self):
        with self.assertRaisesRegex(ValidationError, "wildcard host"):
            AppConfig(servers=[
                ServerConfig(id="a", host="0.0.0.0", port=5020),
                ServerConfig(id="b", host="0.0.0.0", port=5020),
            ])

    def test_different_ports_ok_even_with_wildcards(self):
        cfg = AppConfig(servers=[
            ServerConfig(id="a", host="0.0.0.0", port=5020),
            ServerConfig(id="b", host="0.0.0.0", port=5021),
        ])
        self.assertEqual(len(cfg.servers), 2)

    def test_specific_hosts_different_addresses_same_port_ok(self):
        cfg = AppConfig(servers=[
            ServerConfig(id="a", host="127.0.0.1", port=5020),
            ServerConfig(id="b", host="192.168.1.5", port=5020),
        ])
        self.assertEqual(len(cfg.servers), 2)


class UniqueSensorIdTests(unittest.TestCase):
    def test_duplicate_sensor_id_within_server_rejected(self):
        with self.assertRaisesRegex(ValidationError, "duplicate sensor id 'boiler'"):
            ServerConfig(id="a", port=5020, sensors=[
                SensorConfig(id="boiler", unit_id=1),
                SensorConfig(id="boiler", unit_id=2),
            ])

    def test_sensor_id_repeated_across_servers_ok(self):
        cfg = AppConfig(servers=[
            ServerConfig(id="plant_a", port=5020,
                         sensors=[SensorConfig(id="boiler", unit_id=1)]),
            ServerConfig(id="plant_b", port=5021,
                         sensors=[SensorConfig(id="boiler", unit_id=1)]),
        ])
        self.assertEqual(cfg.servers[0].sensors[0].id, "boiler")
        self.assertEqual(cfg.servers[1].sensors[0].id, "boiler")


class AddressOverlapTests(unittest.TestCase):
    def _measurement(self, name: str, offset: int,
                     dt: DataType = DataType.UINT16,
                     rt: RegisterType = RegisterType.HOLDING_REGISTER) -> MeasurementConfig:
        return MeasurementConfig(name=name, offset=offset, data_type=dt, register_type=rt)

    def test_overlap_within_same_unit_and_type_rejected(self):
        # Two sensors on unit 1, holding register, with overlapping ranges:
        #  s1 @ base 0 + measurement "a" offset 0 (UINT32 = 2 regs) -> [0, 1]
        #  s2 @ base 1 + measurement "b" offset 0 (UINT16 = 1 reg)  -> [1, 1]
        # These conflict at address 1.
        with self.assertRaisesRegex(ValidationError, "address overlap"):
            ServerConfig(id="a", port=5020, sensors=[
                SensorConfig(id="s1", unit_id=1, base_address=0,
                             measurements=[self._measurement("a", 0, DataType.UINT32)]),
                SensorConfig(id="s2", unit_id=1, base_address=1,
                             measurements=[self._measurement("b", 0)]),
            ])

    def test_overlap_across_unit_ids_ok(self):
        # Same address but different unit_id -> independent register spaces.
        cfg = ServerConfig(id="a", port=5020, sensors=[
            SensorConfig(id="s1", unit_id=1, base_address=0,
                         measurements=[self._measurement("a", 0)]),
            SensorConfig(id="s2", unit_id=2, base_address=0,
                         measurements=[self._measurement("b", 0)]),
        ])
        self.assertEqual(len(cfg.sensors), 2)

    def test_overlap_across_register_types_ok(self):
        # Same address but different register_type (holding vs coil) -> independent.
        cfg = ServerConfig(id="a", port=5020, sensors=[
            SensorConfig(id="s1", unit_id=1, base_address=0,
                         measurements=[self._measurement("a", 0)]),
            SensorConfig(id="s2", unit_id=1, base_address=0, measurements=[
                self._measurement("b", 0, DataType.BOOL, RegisterType.COIL)
            ]),
        ])
        self.assertEqual(len(cfg.sensors), 2)

    def test_adjacent_non_overlapping_ok(self):
        # s1 @ base 0 has UINT32 [0, 1]; s2 @ base 2 has UINT16 [2, 2] -> adjacent, ok.
        cfg = ServerConfig(id="a", port=5020, sensors=[
            SensorConfig(id="s1", unit_id=1, base_address=0,
                         measurements=[self._measurement("a", 0, DataType.UINT32)]),
            SensorConfig(id="s2", unit_id=1, base_address=2,
                         measurements=[self._measurement("b", 0)]),
        ])
        self.assertEqual(len(cfg.sensors), 2)


class SaturatingRangeTests(unittest.TestCase):
    """Reject combinations of (data_type, scale, range) that would saturate."""

    def test_uint16_max_saturation_rejected(self):
        with self.assertRaisesRegex(ValidationError, "satura uint16"):
            MeasurementConfig(name="v", offset=0, data_type=DataType.UINT16,
                              scale=1000.0, min_value=0.0, max_value=250.0)

    def test_int16_negative_saturation_rejected(self):
        # min -50 × scale 1000 = -50000 < -32768
        with self.assertRaisesRegex(ValidationError, "satura int16"):
            MeasurementConfig(name="v", offset=0, data_type=DataType.INT16,
                              scale=1000.0, min_value=-50.0, max_value=50.0)

    def test_uint16_negative_min_rejected(self):
        # min < 0 on UNSIGNED type
        with self.assertRaisesRegex(ValidationError, "satura uint16"):
            MeasurementConfig(name="v", offset=0, data_type=DataType.UINT16,
                              scale=1.0, min_value=-10.0, max_value=100.0)

    def test_int16_within_range_ok(self):
        # 80 × 10 = 800 < 32767, -40 × 10 = -400 > -32768
        m = MeasurementConfig(name="t", offset=0, data_type=DataType.INT16,
                              scale=10.0, min_value=-40.0, max_value=80.0)
        self.assertEqual(m.data_type, DataType.INT16)

    def test_uint16_within_range_ok(self):
        # CO2: 5000 × 1 = 5000 < 65535
        m = MeasurementConfig(name="co2", offset=0, data_type=DataType.UINT16,
                              scale=1.0, min_value=400.0, max_value=5000.0)
        self.assertEqual(m.data_type, DataType.UINT16)

    def test_uint32_large_range_ok(self):
        # uptime: 4_000_000_000 × 1 < 4_294_967_295
        m = MeasurementConfig(name="up", offset=0, data_type=DataType.UINT32,
                              scale=1.0, min_value=0.0, max_value=4_000_000_000.0)
        self.assertEqual(m.data_type, DataType.UINT32)

    def test_float32_no_range_check(self):
        # Floats represent any real number → no saturation check
        m = MeasurementConfig(name="v", offset=0, data_type=DataType.FLOAT32,
                              scale=1.0, min_value=0.0, max_value=1e9)
        self.assertEqual(m.data_type, DataType.FLOAT32)

    def test_bool_on_coil_no_range_check(self):
        m = MeasurementConfig(name="alarm", offset=0,
                              register_type=RegisterType.COIL,
                              data_type=DataType.BOOL,
                              scale=1.0, min_value=0.0, max_value=1.0)
        self.assertEqual(m.data_type, DataType.BOOL)

    def test_widening_data_type_resolves_saturation(self):
        # Same scale and range that fails on UINT16 passes on UINT32
        with self.assertRaises(ValidationError):
            MeasurementConfig(name="v", offset=0, data_type=DataType.UINT16,
                              scale=1000.0, max_value=250.0)
        m = MeasurementConfig(name="v", offset=0, data_type=DataType.UINT32,
                              scale=1000.0, max_value=250.0)
        self.assertEqual(m.data_type, DataType.UINT32)


if __name__ == "__main__":
    unittest.main()
