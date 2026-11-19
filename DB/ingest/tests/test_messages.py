"""Parsing and validation of SQS bodies. Run from the repo root:

    python -m unittest discover -s DB/ingest/tests -t DB/ingest
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from ingest.messages import Event, InvalidMessage, Reading, parse

from .helpers import CONTRACT, CONTRACTS_DIR, REAL_SQS_BODY, enrich, example


class RealBodyTests(unittest.TestCase):
    def test_real_sqs_body_is_a_reading(self):
        message = parse(REAL_SQS_BODY, CONTRACT)
        self.assertIsInstance(message, Reading)
        self.assertEqual(message.device_id, "pi-01")
        self.assertEqual(message.zone, "bed-1")
        self.assertEqual(message.ts, datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(message.soil_moisture_raw, 2310)
        self.assertIsNone(message.air_temp)

    def test_received_at_milliseconds_become_utc_datetime(self):
        message = parse(REAL_SQS_BODY, CONTRACT)
        self.assertEqual(message.received_at, datetime.fromtimestamp(1795103249.322, tz=timezone.utc))
        self.assertEqual(message.received_at.utcoffset().total_seconds(), 0)

    def test_raw_keeps_the_whole_body(self):
        message = parse(REAL_SQS_BODY, CONTRACT)
        self.assertEqual(message.raw, json.loads(REAL_SQS_BODY))


class ContractExampleTests(unittest.TestCase):
    """Every contract example for readings/events gives the same verdict once enriched."""

    def test_valid_examples_parse(self):
        for path in sorted((CONTRACTS_DIR / "examples" / "valid").glob("*.json")):
            kind = path.name.split("-", 1)[0]
            if kind == "commands":
                continue
            with self.subTest(example=path.name):
                message = parse(enrich(json.loads(path.read_text()), kind), CONTRACT)
                self.assertIsInstance(message, Reading if kind == "readings" else Event)

    def test_invalid_examples_are_rejected(self):
        for path in sorted((CONTRACTS_DIR / "examples" / "invalid").glob("*.json")):
            kind = path.name.split("-", 1)[0]
            if kind == "commands":
                continue
            with self.subTest(example=path.name):
                with self.assertRaises(InvalidMessage):
                    parse(enrich(json.loads(path.read_text()), kind), CONTRACT)


class EventTests(unittest.TestCase):
    def test_event_gets_device_id_from_the_rule(self):
        body = enrich(example("valid", "events-command-rejected-busy.json"), "events", device_id="pi-07")
        message = parse(body, CONTRACT)
        self.assertIsInstance(message, Event)
        self.assertEqual(message.device_id, "pi-07")
        self.assertEqual(message.type, "command_rejected")
        self.assertEqual(message.reason, "busy")
        self.assertIsNone(message.duration_s)

    def test_event_without_device_id_is_invalid(self):
        body = json.loads(enrich(example("valid", "events-irrigation-started.json"), "events"))
        del body["device_id"]
        with self.assertRaisesRegex(InvalidMessage, "device_id"):
            parse(json.dumps(body), CONTRACT)


class EnvelopeTests(unittest.TestCase):
    def body(self, **changes) -> str:
        data = json.loads(REAL_SQS_BODY)
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        return json.dumps(data)

    def assertInvalid(self, body: str, pattern: str):
        with self.assertRaisesRegex(InvalidMessage, pattern):
            parse(body, CONTRACT)

    def test_missing_kind(self):
        self.assertInvalid(self.body(kind=None), "kind")

    def test_unknown_kind(self):
        self.assertInvalid(self.body(kind="commands"), "kind")

    def test_missing_received_at(self):
        self.assertInvalid(self.body(received_at=None), "received_at")

    def test_received_at_not_an_integer(self):
        self.assertInvalid(self.body(received_at="2026-11-19T18:00:00Z"), "received_at")
        self.assertInvalid(self.body(received_at=1795103249.5), "received_at")
        self.assertInvalid(self.body(received_at=True), "received_at")

    def test_unknown_field_in_payload(self):
        self.assertInvalid(self.body(battery=80), "schema")

    def test_not_json(self):
        self.assertInvalid("not json", "JSON")

    def test_not_an_object(self):
        self.assertInvalid("[1, 2]", "object")

    def test_payload_over_8_kb(self):
        self.assertInvalid(self.body(zone="x" * 9000), "bytes")


if __name__ == "__main__":
    unittest.main()
