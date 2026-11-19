"""Check the message contract: schemas are valid and every example has the expected outcome.

Run from the repository root:

    python -m unittest discover -s docs/contracts -p "test_*.py"

Examples live in examples/valid and examples/invalid. The file name prefix (readings-,
commands-, events-) selects the schema. Every invalid example must break exactly the rule its
name describes, so keep each one to a single defect.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

CONTRACTS_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
KINDS = ("readings", "commands", "events")
MAX_PAYLOAD_BYTES = 8 * 1024


def load_schemas() -> dict[str, dict]:
    return {kind: json.loads((CONTRACTS_DIR / f"{kind}.schema.json").read_text()) for kind in KINDS}


SCHEMAS = load_schemas()
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)
VALIDATORS = {
    kind: Draft202012Validator(schema, registry=REGISTRY, format_checker=FormatChecker())
    for kind, schema in SCHEMAS.items()
}


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate(kind: str, raw: bytes) -> None:
    """Apply the whole contract to a raw payload. Raises ValidationError if it breaks any rule."""
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValidationError(f"payload is {len(raw)} bytes, the limit is {MAX_PAYLOAD_BYTES}")
    payload = json.loads(raw)
    VALIDATORS[kind].validate(payload)
    # Cross-field rules that JSON Schema cannot express.
    if kind == "commands" and parse_utc(payload["expires_at"]) <= parse_utc(payload["issued_at"]):
        raise ValidationError("expires_at must be later than issued_at")


def examples(outcome: str) -> list[Path]:
    return sorted((EXAMPLES_DIR / outcome).glob("*.json"))


def kind_of(path: Path) -> str:
    kind = path.name.split("-", 1)[0]
    if kind not in KINDS:
        raise AssertionError(f"{path.name}: name must start with one of {KINDS}")
    return kind


class SchemaTests(unittest.TestCase):
    def test_schemas_are_valid_draft_2020_12(self):
        for kind, schema in SCHEMAS.items():
            with self.subTest(kind=kind):
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                Draft202012Validator.check_schema(schema)

    def test_every_kind_has_valid_and_invalid_examples(self):
        for outcome in ("valid", "invalid"):
            found = {kind_of(path) for path in examples(outcome)}
            self.assertEqual(found, set(KINDS), f"missing {outcome} examples")


class ExampleTests(unittest.TestCase):
    def test_valid_examples_pass(self):
        for path in examples("valid"):
            with self.subTest(example=path.name):
                validate(kind_of(path), path.read_bytes())

    def test_invalid_examples_fail(self):
        for path in examples("invalid"):
            with self.subTest(example=path.name):
                with self.assertRaises(ValidationError):
                    validate(kind_of(path), path.read_bytes())


class PayloadSizeTests(unittest.TestCase):
    def test_payload_over_8_kb_is_rejected(self):
        payload = json.loads((EXAMPLES_DIR / "valid" / "readings-full.json").read_bytes())
        raw = json.dumps(payload).encode()
        raw += b" " * (MAX_PAYLOAD_BYTES - len(raw))
        validate("readings", raw)  # exactly at the limit: accepted
        with self.assertRaises(ValidationError):
            validate("readings", raw + b" ")


if __name__ == "__main__":
    unittest.main()
