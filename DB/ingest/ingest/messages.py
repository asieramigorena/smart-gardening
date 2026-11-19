"""Parse and validate the SQS message bodies produced by the IoT topic rule.

The rule (infra/main/iot.tf) forwards the device payload and adds three fields:
`kind` ("readings" or "events", from the topic), `received_at` (epoch milliseconds) and
`device_id` (from the topic, overwriting the payload's). Those are removed before validating
the rest against the contract, whose schemas reject unknown fields.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Union

from .contract import Contract

MAX_PAYLOAD_BYTES = 8 * 1024  # docs/contracts/README.md, rule 6
DEVICE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class InvalidMessage(Exception):
    """The message can never be stored: it goes to the DLQ."""


@dataclass(frozen=True)
class Reading:
    device_id: str
    zone: str
    ts: datetime
    received_at: datetime
    soil_moisture_raw: int | None
    soil_moisture: float | None
    air_temp: float | None
    air_humidity: float | None
    light: float | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class Event:
    device_id: str
    command_id: str
    type: str
    ts: datetime
    duration_s: int | None
    reason: str | None
    received_at: datetime
    raw: dict[str, Any]


Message = Union[Reading, Event]


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse(body: str, contract: Contract) -> Message:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise InvalidMessage(f"body is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise InvalidMessage("body is not a JSON object")

    raw = dict(data)
    kind = data.pop("kind", None)
    received_ms = data.pop("received_at", None)
    if kind not in ("readings", "events"):
        raise InvalidMessage(f"missing or unknown kind: {kind!r}")
    if not isinstance(received_ms, int) or isinstance(received_ms, bool) or received_ms <= 0:
        raise InvalidMessage(f"received_at must be epoch milliseconds, got {received_ms!r}")
    received_at = datetime.fromtimestamp(received_ms / 1000, tz=timezone.utc)

    # Readings carry device_id in the contract; events do not, so the one added by the rule is
    # taken out before validating. Either way, the value comes from the topic.
    device_id = data.get("device_id") if kind == "readings" else data.pop("device_id", None)
    if not isinstance(device_id, str) or not DEVICE_ID_RE.fullmatch(device_id):
        raise InvalidMessage(f"missing or invalid device_id: {device_id!r}")

    size = len(json.dumps(data, separators=(",", ":")).encode())
    if size > MAX_PAYLOAD_BYTES:
        raise InvalidMessage(f"payload is {size} bytes, the limit is {MAX_PAYLOAD_BYTES}")

    error = contract.error(kind, data)
    if error:
        raise InvalidMessage(f"{kind} schema: {error}")

    if kind == "readings":
        return Reading(
            device_id=device_id,
            zone=data["zone"],
            ts=parse_timestamp(data["ts"]),
            received_at=received_at,
            soil_moisture_raw=data["soil_moisture_raw"],
            soil_moisture=data.get("soil_moisture"),
            air_temp=data.get("air_temp"),
            air_humidity=data.get("air_humidity"),
            light=data.get("light"),
            raw=raw,
        )
    return Event(
        device_id=device_id,
        command_id=data["command_id"],
        type=data["type"],
        ts=parse_timestamp(data["ts"]),
        duration_s=data.get("duration_s"),
        reason=data.get("reason"),
        received_at=received_at,
        raw=raw,
    )
