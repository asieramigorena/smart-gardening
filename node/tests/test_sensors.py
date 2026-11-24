"""sensors.read_all(): a failing sensor must not lose the rest of the reading."""
import json
import unittest
from unittest import mock

from gardening_node import sensors


def _broken():
    raise OSError("I2C bus error")


def _with_readers(readers):
    return mock.patch.object(sensors, "READERS", readers)


class ReadAllTest(unittest.TestCase):
    def test_failing_sensor_is_null_and_others_are_kept(self):
        errors = {}
        with _with_readers({("soil_moisture_raw",): _broken, ("light",): lambda: 56.4}):
            reading = sensors.read_all(errors)
        self.assertEqual(reading, {"soil_moisture_raw": None, "light": 56.4})
        self.assertIn("I2C bus error", errors["soil_moisture_raw"])

    def test_multi_value_sensor_maps_values_to_fields_in_order(self):
        with _with_readers({("air_temp", "air_humidity"): lambda: (24.0, 34.0)}):
            reading = sensors.read_all()
        self.assertEqual(reading, {"air_temp": 24.0, "air_humidity": 34.0})

    def test_failing_multi_value_sensor_nulls_all_its_fields(self):
        errors = {}
        with _with_readers({("air_temp", "air_humidity"): _broken}):
            reading = sensors.read_all(errors)
        self.assertEqual(reading, {"air_temp": None, "air_humidity": None})
        self.assertIn("air_temp,air_humidity", errors)

    def test_wrong_number_of_values_is_an_error_not_a_shifted_reading(self):
        errors = {}
        with _with_readers({("air_temp", "air_humidity"): lambda: (24.0,)}):
            reading = sensors.read_all(errors)
        self.assertEqual(reading, {"air_temp": None, "air_humidity": None})
        self.assertTrue(errors)

    def test_every_real_field_is_present_even_if_all_sensors_fail(self):
        # The server expects these fields in every message, with or without a hat
        all_broken = {fields: _broken for fields in sensors.READERS}
        with _with_readers(all_broken):
            reading = sensors.read_all()
        self.assertEqual(
            set(reading), {"soil_moisture_raw", "light", "air_temp", "air_humidity"})
        self.assertTrue(all(v is None for v in reading.values()))
        self.assertEqual(json.loads(json.dumps(reading)), reading)


if __name__ == "__main__":
    unittest.main()
