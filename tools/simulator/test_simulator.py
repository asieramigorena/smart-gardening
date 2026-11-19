"""The simulator's payloads must follow the contract (valid ones pass, invalid ones fail).

    python -m unittest discover -s tools/simulator
"""

from __future__ import annotations

import json
import random
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

import simulate

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "docs" / "contracts" / "readings.schema.json").read_text()
)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
TIMES = [datetime(2026, 11, 19, hour, 30, tzinfo=timezone.utc) for hour in range(24)]


class SimulatorPayloadTests(unittest.TestCase):
    def test_valid_readings_follow_the_contract(self):
        rng = random.Random(1)
        for calibrated in (False, True):
            for ts in TIMES * 5:
                reading = simulate.make_reading("pi-01", "bed-1", ts, rng, calibrated)
                with self.subTest(reading=reading):
                    VALIDATOR.validate(reading)
                    self.assertEqual("soil_moisture" in reading, calibrated)

    def test_every_invalid_mutation_breaks_the_contract(self):
        for name, mutate in simulate.INVALID_MUTATIONS.items():
            reading = simulate.make_reading("pi-01", "bed-1", TIMES[12], random.Random(2))
            mutate(reading)
            with self.subTest(mutation=name):
                self.assertFalse(VALIDATOR.is_valid(reading))

    def test_dry_run_sends_count_messages(self):
        # --dry-run never touches AWS.
        self.assertEqual(simulate.main(["--dry-run", "--count", "2", "--interval", "0", "--seed", "3"]), 0)


if __name__ == "__main__":
    unittest.main()
