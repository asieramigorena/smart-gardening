"""Settings, all from environment variables (set by infra/server/docker-compose.yml)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    region: str
    queue_url: str
    dlq_url: str
    database_url: str
    contracts_dir: Path
    health_file: Path
    max_messages: int = 10
    wait_seconds: int = 20

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        missing = [
            name
            for name in ("AWS_REGION", "INGEST_QUEUE_URL", "INGEST_DLQ_URL", "DATABASE_URL")
            if not env.get(name)
        ]
        if missing:
            raise ConfigError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            region=env["AWS_REGION"],
            queue_url=env["INGEST_QUEUE_URL"],
            dlq_url=env["INGEST_DLQ_URL"],
            database_url=env["DATABASE_URL"],
            contracts_dir=Path(env.get("CONTRACTS_DIR", "/app/contracts")),
            health_file=Path(env.get("HEALTH_FILE", "/tmp/ingest-heartbeat")),
        )
