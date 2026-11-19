"""Optional: run the store against a real, migrated PostgreSQL.

Skipped unless INGEST_TEST_DATABASE_URL points to a database with the migrations applied
(e.g. the local compose stack, see DB/README.md). Uses its own device and zone IDs and deletes
them afterwards.
"""

from __future__ import annotations

import os
import unittest
import uuid

from ingest.messages import parse
from ingest.store import store

from .helpers import CONTRACT, enrich, example

DATABASE_URL = os.environ.get("INGEST_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "INGEST_TEST_DATABASE_URL not set")
class StoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        self.conn = psycopg.connect(DATABASE_URL, autocommit=True)
        self.addCleanup(self.conn.close)
        suffix = uuid.uuid4().hex[:8]
        self.device = f"test-{suffix}"
        self.zone = f"test-{suffix}"
        self.addCleanup(self.cleanup)

    def cleanup(self):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM irrigation_events WHERE device_id = %s", (self.device,))
            cur.execute("DELETE FROM readings WHERE device_id = %s", (self.device,))
            cur.execute("DELETE FROM devices WHERE id = %s", (self.device,))
            cur.execute("DELETE FROM zones WHERE id = %s", (self.zone,))

    def one(self, sql, *params):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def reading(self):
        payload = {**example("valid", "readings-full.json"), "zone": self.zone}
        return parse(enrich(payload, "readings", device_id=self.device), CONTRACT)

    def test_reading_from_unknown_device_registers_device_and_zone(self):
        self.assertTrue(store(self.conn, self.reading()))
        self.assertEqual(self.one("SELECT zone_id FROM devices WHERE id = %s", self.device), (self.zone,))
        row = self.one(
            "SELECT soil_moisture_raw, air_temp::text, received_at, raw->>'kind' FROM readings WHERE device_id = %s",
            self.device,
        )
        self.assertEqual(row[0], 2310)
        self.assertEqual(row[1], "14.2")
        self.assertEqual(row[2].year, 2026)
        self.assertEqual(row[3], "readings")

    def test_reading_is_idempotent(self):
        message = self.reading()
        self.assertTrue(store(self.conn, message))
        self.assertFalse(store(self.conn, message))
        self.assertEqual(self.one("SELECT count(*) FROM readings WHERE device_id = %s", self.device), (1,))

    def test_event_is_idempotent_and_device_gets_zone_later(self):
        event = parse(enrich(example("valid", "events-irrigation-started.json"), "events", device_id=self.device), CONTRACT)
        self.cleanup_command(event.command_id)
        self.assertTrue(store(self.conn, event))
        self.assertFalse(store(self.conn, event))
        self.assertEqual(self.one("SELECT zone_id FROM devices WHERE id = %s", self.device), (None,))

        store(self.conn, self.reading())
        self.assertEqual(self.one("SELECT zone_id FROM devices WHERE id = %s", self.device), (self.zone,))

    def cleanup_command(self, command_id):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM irrigation_events WHERE command_id = %s", (command_id,))


if __name__ == "__main__":
    unittest.main()
