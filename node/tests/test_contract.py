"""The node's readings message against the real docs/contracts schema."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from jsonschema import Draft202012Validator

from gardening_node import contract, sensors

VALIDATOR = contract.readings_validator()
TAKEN_AT = datetime(2026, 11, 19, 10, 15, tzinfo=timezone.utc)


def _broken():
    raise OSError("I2C bus error")


def _reading_with(readers):
    with mock.patch.object(sensors, "READERS", readers):
        values = sensors.read_all()
    return contract.build_reading("pi-01", "bed-1", TAKEN_AT, values)


class ReadingMessageTest(unittest.TestCase):
    def test_reading_with_every_sensor_working_follows_the_contract(self):
        readers = dict(zip(sensors.READERS, [lambda: 2310, lambda: 72.4, lambda: (14.2, 63.0)]))
        body = _reading_with(readers)
        contract.encode(body, VALIDATOR)  # raises if it breaks the contract
        self.assertEqual(body["soil_moisture_raw"], 2310)

    def test_reading_with_every_sensor_failing_still_follows_the_contract(self):
        # Contract: a failed sensor is null, and soil_moisture_raw is always present
        body = _reading_with({fields: _broken for fields in sensors.READERS})
        contract.encode(body, VALIDATOR)
        self.assertIsNone(body["soil_moisture_raw"])

    def test_timestamp_is_utc_with_z_even_from_local_time(self):
        # 11:15 in Bilbao (UTC+1) is 10:15Z; offsets like +01:00 are rejected by the contract
        bilbao = timezone(timedelta(hours=1))
        body = contract.build_reading("pi-01", "bed-1", datetime(2026, 11, 19, 11, 15, tzinfo=bilbao), {})
        self.assertEqual(body["ts"], "2026-11-19T10:15:00Z")

    def test_message_breaking_the_contract_is_not_encoded(self):
        body = contract.build_reading("pi-01", "bed-1", TAKEN_AT, {"soil_moisture_raw": 2310, "light": 120})
        with self.assertRaises(contract.ContractError):
            contract.encode(body, VALIDATOR)

    def test_payload_over_8_kb_is_not_encoded(self):
        accept_anything = Draft202012Validator({})
        with self.assertRaises(contract.ContractError):
            contract.encode({"padding": "x" * 8192}, accept_anything)

    def test_missing_schema_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(contract.ContractError):
                contract.readings_validator(empty)

    def test_soil_calibration_is_omitted_when_uncalibrated_or_raw_is_none(self):
        uncalibrated = contract.build_reading("pi-01", "bed-1", TAKEN_AT, {"soil_moisture_raw": 1500})
        self.assertNotIn("soil_moisture", uncalibrated)
        contract.encode(uncalibrated, VALIDATOR)

        failed = contract.build_reading(
            "pi-01", "bed-1", TAKEN_AT, {"soil_moisture_raw": None},
            soil_raw_dry=0, soil_raw_wet=2500,
        )
        self.assertNotIn("soil_moisture", failed)
        contract.encode(failed, VALIDATOR)

    def test_soil_calibration_interpolates_and_clamps_for_both_polarities(self):
        # Increasing raw when wet (e.g. Grove moisture sensor)
        body = contract.build_reading(
            "pi-01", "bed-1", TAKEN_AT, {"soil_moisture_raw": 1250},
            soil_raw_dry=0, soil_raw_wet=2500,
        )
        self.assertEqual(body["soil_moisture"], 50.0)
        contract.encode(body, VALIDATOR)

        # Decreasing raw when wet (capacitive sensors)
        self.assertEqual(contract.calibrate_soil_moisture(2000, 3000, 1000), 50.0)
        self.assertEqual(contract.calibrate_soil_moisture(3500, 3000, 1000), 0.0)
        self.assertEqual(contract.calibrate_soil_moisture(500, 3000, 1000), 100.0)
        self.assertEqual(contract.calibrate_soil_moisture(1333, 1000, 2000), 33.3)


if __name__ == "__main__":
    unittest.main()
