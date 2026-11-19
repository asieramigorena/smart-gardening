"""Write parsed messages to PostgreSQL. Every insert is idempotent (ON CONFLICT DO NOTHING)."""

from __future__ import annotations

import logging

import psycopg
from psycopg.types.json import Jsonb

from .messages import Event, Message, Reading

log = logging.getLogger(__name__)

INSERT_ZONE = """
INSERT INTO zones (id, name) VALUES (%(zone)s, %(zone)s)
ON CONFLICT (id) DO NOTHING
RETURNING id
"""

# Registers an unknown device. A device first seen through an event has no zone yet: its first
# reading sets it. RETURNING gives a row only when something changed.
UPSERT_DEVICE_WITH_ZONE = """
INSERT INTO devices (id, zone_id) VALUES (%(device_id)s, %(zone)s)
ON CONFLICT (id) DO UPDATE SET zone_id = EXCLUDED.zone_id WHERE devices.zone_id IS NULL
RETURNING (xmax = 0) AS inserted
"""

INSERT_DEVICE_WITHOUT_ZONE = """
INSERT INTO devices (id) VALUES (%(device_id)s)
ON CONFLICT (id) DO NOTHING
RETURNING id
"""

INSERT_READING = """
INSERT INTO readings (device_id, zone_id, ts, received_at, soil_moisture_raw, soil_moisture,
                      air_temp, air_humidity, light, raw)
VALUES (%(device_id)s, %(zone)s, %(ts)s, %(received_at)s, %(soil_moisture_raw)s, %(soil_moisture)s,
        %(air_temp)s, %(air_humidity)s, %(light)s, %(raw)s)
ON CONFLICT (device_id, ts) DO NOTHING
RETURNING id
"""

INSERT_EVENT = """
INSERT INTO irrigation_events (command_id, device_id, type, ts, duration_s, reason, received_at, raw)
VALUES (%(command_id)s, %(device_id)s, %(type)s, %(ts)s, %(duration_s)s, %(reason)s,
        %(received_at)s, %(raw)s)
ON CONFLICT (command_id, type) DO NOTHING
RETURNING id
"""


def store(conn: psycopg.Connection, message: Message) -> bool:
    """Store one message in a single transaction. Returns False if it was a duplicate."""
    with conn.transaction(), conn.cursor() as cur:
        if isinstance(message, Reading):
            _register_reading_device(cur, message)
            cur.execute(
                INSERT_READING,
                {
                    "device_id": message.device_id,
                    "zone": message.zone,
                    "ts": message.ts,
                    "received_at": message.received_at,
                    "soil_moisture_raw": message.soil_moisture_raw,
                    "soil_moisture": message.soil_moisture,
                    "air_temp": message.air_temp,
                    "air_humidity": message.air_humidity,
                    "light": message.light,
                    "raw": Jsonb(message.raw),
                },
            )
        elif isinstance(message, Event):
            cur.execute(INSERT_DEVICE_WITHOUT_ZONE, {"device_id": message.device_id})
            if cur.fetchone():
                log.warning(
                    "unknown device registered without zone",
                    extra={"fields": {"device_id": message.device_id}},
                )
            cur.execute(
                INSERT_EVENT,
                {
                    "command_id": message.command_id,
                    "device_id": message.device_id,
                    "type": message.type,
                    "ts": message.ts,
                    "duration_s": message.duration_s,
                    "reason": message.reason,
                    "received_at": message.received_at,
                    "raw": Jsonb(message.raw),
                },
            )
        else:  # pragma: no cover - parse() only returns the two types above
            raise TypeError(f"unsupported message type {type(message).__name__}")
        return cur.fetchone() is not None


def _register_reading_device(cur: psycopg.Cursor, reading: Reading) -> None:
    params = {"device_id": reading.device_id, "zone": reading.zone}
    cur.execute(INSERT_ZONE, params)
    if cur.fetchone():
        log.warning("unknown zone registered", extra={"fields": {"zone": reading.zone}})
    cur.execute(UPSERT_DEVICE_WITH_ZONE, params)
    row = cur.fetchone()
    if row:
        log.warning(
            "unknown device registered" if row[0] else "device zone set from its first reading",
            extra={"fields": params},
        )
