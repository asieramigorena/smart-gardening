"""Entry point: python -m ingest"""

from __future__ import annotations

import logging
import os
import signal
import sys

import boto3
import psycopg
from botocore.config import Config as BotoConfig

from . import logs
from .config import Config, ConfigError
from .contract import Contract
from .worker import Worker

log = logging.getLogger("ingest")


def main() -> int:
    logs.setup(os.environ.get("LOG_LEVEL", "INFO"))
    try:
        config = Config.from_env()
    except ConfigError as exc:
        log.error(str(exc))
        return 2

    # Credentials come from the EC2 instance role (IMDSv2); no keys anywhere.
    sqs = boto3.client(
        "sqs",
        region_name=config.region,
        config=BotoConfig(retries={"mode": "standard"}, read_timeout=config.wait_seconds + 10),
    )
    worker = Worker(
        sqs=sqs,
        queue_url=config.queue_url,
        dlq_url=config.dlq_url,
        contract=Contract(config.contracts_dir),
        connect=lambda: psycopg.connect(config.database_url, autocommit=True, connect_timeout=10),
        health_file=config.health_file,
        max_messages=config.max_messages,
        wait_seconds=config.wait_seconds,
    )
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
