"""Readings message of docs/contracts, validated before it is sent (rules 6 and 7)."""
import json
import os
from datetime import timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 8 * 1024  # rule 6
# node/src/gardening_node/contract.py -> repository root
_REPO = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = Path(os.environ.get("SMART_GARDENING_CONTRACTS_DIR", _REPO / "docs" / "contracts"))


class ContractError(Exception):
    pass


def readings_validator(contracts_dir=None):
    path = Path(contracts_dir or CONTRACTS_DIR) / "readings.schema.json"
    try:
        schema = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ContractError(f"cannot load the readings schema {path}: {exc}") from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def utc_timestamp(moment):
    """RFC 3339 in UTC with a trailing Z, e.g. 2026-11-19T10:15:00Z (offsets are rejected)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_reading(device_id, zone, taken_at, sensor_values):
    """Readings body. `taken_at` is a timezone-aware datetime; `sensor_values`
    comes from sensors.read_all()."""
    return {
        "schema_version": SCHEMA_VERSION,
        "device_id": device_id,
        "zone": zone,
        "ts": utc_timestamp(taken_at),
        **sensor_values,
    }


def encode(body, validator):
    """UTF-8 JSON of a valid message. Raises ContractError instead of sending a bad one."""
    errors = sorted(validator.iter_errors(body), key=lambda e: list(e.path))
    if errors:
        raise ContractError("; ".join(e.message for e in errors))
    raw = json.dumps(body).encode()
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ContractError(f"payload is {len(raw)} bytes, the limit is {MAX_PAYLOAD_BYTES}")
    return raw
