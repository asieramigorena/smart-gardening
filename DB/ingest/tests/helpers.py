from __future__ import annotations

import json
from pathlib import Path

from ingest.contract import Contract

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = REPO_ROOT / "docs" / "contracts"
CONTRACT = Contract(CONTRACTS_DIR)

# Body copied from the real ingest queue: the device payload plus what the IoT rule adds.
REAL_SQS_BODY = (
    '{"schema_version":1,"device_id":"pi-01","zone":"bed-1","ts":"2026-11-19T18:00:00Z",'
    '"soil_moisture_raw":2310,"kind":"readings","received_at":1795103249322}'
)


def enrich(payload: dict, kind: str, device_id: str = "pi-01", received_at: int = 1795103249322) -> str:
    """Build an SQS body the way the IoT topic rule does."""
    return json.dumps({**payload, "device_id": device_id, "kind": kind, "received_at": received_at})


def example(outcome: str, name: str) -> dict:
    return json.loads((CONTRACTS_DIR / "examples" / outcome / name).read_text())
