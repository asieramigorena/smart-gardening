"""store() with a fake connection (no database): which statements run for each case."""

from __future__ import annotations

import contextlib
import unittest

from ingest.messages import parse
from ingest.store import SELECT_ZONE, UPSERT_DEVICE_WITH_ZONE, UnknownZone, store

from .helpers import CONTRACT, enrich, example

KNOWN_ZONES = {"bed-1"}


class FakeCursor:
    """Answers the zone lookup from KNOWN_ZONES and every other statement with one row."""

    def __init__(self):
        self.executed: list[str] = []
        self._row = None

    def execute(self, sql, params):
        self.executed.append(sql)
        if sql == SELECT_ZONE:
            self._row = (1,) if params["zone"] in KNOWN_ZONES else None
        elif sql == UPSERT_DEVICE_WITH_ZONE:
            self._row = (True,)  # inserted: the device was new
        else:
            self._row = (1,)

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self):
        self.cur = FakeCursor()
        self.committed = False

    @contextlib.contextmanager
    def transaction(self):
        yield  # an exception propagates without commit, as with psycopg
        self.committed = True

    @contextlib.contextmanager
    def cursor(self):
        yield self.cur


def reading(zone: str, device_id: str = "pi-02"):
    payload = {**example("valid", "readings-full.json"), "zone": zone}
    return parse(enrich(payload, "readings", device_id=device_id), CONTRACT)


class ReadingZoneTests(unittest.TestCase):
    def test_new_device_in_existing_zone_is_registered_and_stored(self):
        conn = FakeConnection()
        self.assertTrue(store(conn, reading("bed-1")))
        self.assertTrue(conn.committed)
        self.assertEqual(len(conn.cur.executed), 3)  # zone lookup, device upsert, reading insert
        self.assertIn("INSERT INTO devices", conn.cur.executed[1])
        self.assertIn("INSERT INTO readings", conn.cur.executed[2])

    def test_unknown_zone_is_rejected_without_writing(self):
        conn = FakeConnection()
        with self.assertRaises(UnknownZone) as ctx:
            store(conn, reading("bed1"))  # typo of bed-1
        self.assertFalse(conn.committed)
        self.assertEqual(conn.cur.executed, [SELECT_ZONE])  # nothing inserted
        self.assertTrue(str(ctx.exception).startswith("unknown_zone"))
        self.assertIn("'bed1'", str(ctx.exception))
        self.assertTrue(all("INSERT INTO zones" not in sql for sql in conn.cur.executed))

    def test_event_does_not_need_a_zone(self):
        conn = FakeConnection()
        event = parse(enrich(example("valid", "events-irrigation-started.json"), "events"), CONTRACT)
        self.assertTrue(store(conn, event))
        self.assertNotIn(SELECT_ZONE, conn.cur.executed)


if __name__ == "__main__":
    unittest.main()
