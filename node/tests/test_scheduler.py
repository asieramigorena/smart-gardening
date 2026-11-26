"""Scheduler clock checks and publishing guards."""
import subprocess
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from gardening_node import contract, scheduler


class ClockCheckTest(unittest.TestCase):
    def test_synced_clock_with_valid_year_returns_true_without_warning(self):
        ok = subprocess.CompletedProcess([], 0, stdout="yes\n", stderr="")
        with mock.patch.object(scheduler.subprocess, "run", return_value=ok):
            with self.assertNoLogs(scheduler.logger, level="WARNING"):
                self.assertTrue(scheduler.check_clock(datetime(2026, 11, 26, tzinfo=timezone.utc)))

    def test_unsynced_clock_with_valid_year_warns_and_returns_true(self):
        unsynced = subprocess.CompletedProcess([], 0, stdout="no\n", stderr="")
        with mock.patch.object(scheduler.subprocess, "run", return_value=unsynced):
            with self.assertLogs(scheduler.logger, level="WARNING") as logs:
                self.assertTrue(scheduler.check_clock(datetime(2026, 11, 26, tzinfo=timezone.utc)))
        self.assertTrue(any("NTP" in msg for msg in logs.output))

    def test_missing_timedatectl_warns_and_returns_true(self):
        with mock.patch.object(scheduler.subprocess, "run", side_effect=FileNotFoundError("no timedatectl")):
            with self.assertLogs(scheduler.logger, level="WARNING") as logs:
                self.assertTrue(scheduler.check_clock(datetime(2026, 11, 26, tzinfo=timezone.utc)))
        self.assertTrue(any("NTP" in msg for msg in logs.output))

    def test_year_before_2026_returns_false_with_error(self):
        ok = subprocess.CompletedProcess([], 0, stdout="yes\n", stderr="")
        with mock.patch.object(scheduler.subprocess, "run", return_value=ok):
            with self.assertLogs(scheduler.logger, level="ERROR") as logs:
                self.assertFalse(scheduler.check_clock(datetime(2020, 1, 1, tzinfo=timezone.utc)))
        self.assertTrue(any("2020" in msg for msg in logs.output))

    def test_publish_reading_skips_send_when_clock_is_invalid(self):
        cfg = SimpleNamespace(
            thing_name="pi-01", zone="bed-1", topic="smart-gardening/pi-01/readings",
            adc_address=0x08, soil_raw_dry=None, soil_raw_wet=None,
        )
        publisher = SimpleNamespace(cfg=cfg, send=mock.Mock())
        validator = contract.readings_validator()
        with mock.patch.object(scheduler, "check_clock", return_value=False):
            self.assertFalse(scheduler.publish_reading(publisher, validator))
        publisher.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
