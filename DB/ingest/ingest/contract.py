"""Validation against the JSON Schemas in docs/contracts (the source of truth)."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import best_match
from referencing import Registry, Resource

KINDS = ("readings", "commands", "events")


class Contract:
    def __init__(self, contracts_dir: Path):
        schemas = {
            kind: json.loads((contracts_dir / f"{kind}.schema.json").read_text()) for kind in KINDS
        }
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
        )
        self._validators = {
            kind: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
            for kind, schema in schemas.items()
        }

    def error(self, kind: str, payload: object) -> str | None:
        """Return why the payload breaks the schema of `kind`, or None if it is valid."""
        error = best_match(self._validators[kind].iter_errors(payload))
        if error is None:
            return None
        where = "/".join(str(part) for part in error.absolute_path) or "(root)"
        return f"{where}: {error.message}"
