"""Node config: a misconfigured node must fail with a clear message."""
import tempfile
import unittest
from pathlib import Path

from gardening_node.config import ConfigError, load_config

ENV = "IOT_ENDPOINT=abc123-ats.iot.eu-south-2.amazonaws.com\nTHING_NAME=pi-01\nZONE=bed-1\n"


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, text):
        (self.dir / "node.env").write_text(text)

    def test_topic_is_the_contract_readings_topic(self):
        # The device policy only allows smart-gardening/<thing>/readings
        self._write(ENV)
        self.assertEqual(load_config(self.dir, environ={}).topic, "smart-gardening/pi-01/readings")

    def test_certificates_default_to_the_names_installed_by_infra(self):
        # infra/README.md: certs/<device>.cert.pem, certs/<device>.private.key, AmazonRootCA1.pem
        self._write(ENV)
        cfg = load_config(self.dir, environ={})
        self.assertEqual(cfg.cert, self.dir / "certs/pi-01.cert.pem")
        self.assertEqual(cfg.key, self.dir / "certs/pi-01.private.key")
        self.assertEqual(cfg.root_ca, self.dir / "certs/AmazonRootCA1.pem")

    def test_unprovisioned_node_reports_every_missing_value(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.dir, environ={})
        for key in ("IOT_ENDPOINT", "THING_NAME", "ZONE"):
            self.assertIn(key, str(ctx.exception))

    def test_environment_overrides_the_file(self):
        self._write(ENV)
        self.assertEqual(load_config(self.dir, environ={"ZONE": "lawn-2"}).zone, "lawn-2")

    def test_zone_outside_the_contract_pattern_is_rejected(self):
        # Contract: ^[a-z0-9][a-z0-9_-]{0,31}$ (lowercase, no topic separators)
        for zone in ("Bed-1", "bed/1", "-bed", "b" * 33):
            with self.subTest(zone=zone):
                self._write(ENV.replace("bed-1", zone))
                with self.assertRaises(ConfigError):
                    load_config(self.dir, environ={})

    def test_device_id_with_a_topic_separator_is_rejected(self):
        self._write(ENV.replace("pi-01", "pi/01"))
        with self.assertRaises(ConfigError):
            load_config(self.dir, environ={})

    def test_read_interval_defaults_to_300(self):
        self._write(ENV)
        self.assertEqual(load_config(self.dir, environ={}).read_interval_s, 300)

    def test_read_interval_from_file_and_env_override(self):
        self._write(ENV + "READ_INTERVAL_S=60\n")
        self.assertEqual(load_config(self.dir, environ={}).read_interval_s, 60)
        self.assertEqual(load_config(self.dir, environ={"READ_INTERVAL_S": "10"}).read_interval_s, 10)

    def test_read_interval_below_minimum_or_non_integer_is_rejected(self):
        for bad in ("9", "0", "-5", "abc"):
            with self.subTest(read_interval_s=bad):
                self._write(ENV + f"READ_INTERVAL_S={bad}\n")
                with self.assertRaises(ConfigError):
                    load_config(self.dir, environ={})

    def test_adc_address_defaults_to_0x08(self):
        self._write(ENV)
        self.assertEqual(load_config(self.dir, environ={}).adc_address, 0x08)

    def test_adc_address_accepts_hex_decimal_and_env_override(self):
        self._write(ENV + "GROVE_ADC_ADDRESS=0x04\n")
        self.assertEqual(load_config(self.dir, environ={}).adc_address, 0x04)
        self.assertEqual(load_config(self.dir, environ={"GROVE_ADC_ADDRESS": "4"}).adc_address, 4)

    def test_adc_address_invalid_or_out_of_range_is_rejected(self):
        for bad in ("0x00", "0x02", "0x78", "0x80", "bad"):
            with self.subTest(adc_address=bad):
                self._write(ENV + f"GROVE_ADC_ADDRESS={bad}\n")
                with self.assertRaises(ConfigError):
                    load_config(self.dir, environ={})

    def test_soil_calibration_defaults_to_none(self):
        self._write(ENV)
        cfg = load_config(self.dir, environ={})
        self.assertIsNone(cfg.soil_raw_dry)
        self.assertIsNone(cfg.soil_raw_wet)

    def test_soil_calibration_accepts_increasing_and_decreasing_pairs(self):
        self._write(ENV + "SOIL_RAW_DRY=0\nSOIL_RAW_WET=2800\n")
        cfg = load_config(self.dir, environ={})
        self.assertEqual((cfg.soil_raw_dry, cfg.soil_raw_wet), (0, 2800))
        cfg_env = load_config(
            self.dir, environ={"SOIL_RAW_DRY": "3200", "SOIL_RAW_WET": "1200"}
        )
        self.assertEqual((cfg_env.soil_raw_dry, cfg_env.soil_raw_wet), (3200, 1200))

    def test_soil_calibration_rejects_partial_equal_or_invalid_values(self):
        cases = (
            "SOIL_RAW_DRY=100\n",
            "SOIL_RAW_WET=2000\n",
            "SOIL_RAW_DRY=1500\nSOIL_RAW_WET=1500\n",
            "SOIL_RAW_DRY=-1\nSOIL_RAW_WET=2000\n",
            "SOIL_RAW_DRY=100\nSOIL_RAW_WET=4096\n",
            "SOIL_RAW_DRY=abc\nSOIL_RAW_WET=2000\n",
        )
        for extra in cases:
            with self.subTest(extra=extra.strip()):
                self._write(ENV + extra)
                with self.assertRaises(ConfigError):
                    load_config(self.dir, environ={})


if __name__ == "__main__":
    unittest.main()
