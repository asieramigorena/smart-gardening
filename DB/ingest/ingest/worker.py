"""Main loop: long-poll SQS, store each message, then delete it.

Delivery rules:
- Stored (or duplicate) -> delete from the queue, only after the commit.
- Invalid (can never be stored: fails the contract, or a reading for an unknown zone) -> copy to
  the DLQ with the reason, then delete.
- Database error -> do not delete: SQS makes it visible again after the visibility timeout and
  moves it to the DLQ after 5 receives (redrive policy in infra/main/queue.tf).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from .contract import Contract
from .messages import InvalidMessage, Message, parse
from .store import store as store_message

log = logging.getLogger(__name__)

MAX_BACKOFF_S = 60.0
REASON_ATTRIBUTE_MAX = 1024


class DatabaseUnavailable(Exception):
    pass


class Worker:
    def __init__(
        self,
        *,
        sqs: Any,
        queue_url: str,
        dlq_url: str,
        contract: Contract,
        connect: Callable[[], psycopg.Connection],
        health_file: Path | None = None,
        store: Callable[[psycopg.Connection, Message], bool] = store_message,
        sleep: Callable[[float], None] = time.sleep,
        max_messages: int = 10,
        wait_seconds: int = 20,
    ):
        self.sqs = sqs
        self.queue_url = queue_url
        self.dlq_url = dlq_url
        self.contract = contract
        self.connect = connect
        self.health_file = health_file
        self.store = store
        self.sleep = sleep
        self.max_messages = max_messages
        self.wait_seconds = wait_seconds
        self.conn: psycopg.Connection | None = None
        self.stopping = False
        self.failures = 0

    # --- lifecycle -------------------------------------------------------------------------

    def stop(self, *_: object) -> None:
        """Signal handler: finish the current message and exit."""
        if not self.stopping:
            log.info("stop requested, finishing current work")
        self.stopping = True

    def run(self) -> None:
        log.info("ingest started", extra={"fields": {"queue_url": self.queue_url}})
        try:
            while not self.stopping:
                if self.run_once():
                    self.failures = 0
                else:
                    self.backoff()
        finally:
            self.close()
            log.info("ingest stopped")

    def backoff(self) -> None:
        self.failures += 1
        delay = min(MAX_BACKOFF_S, 2.0 ** (self.failures - 1))
        log.info("retrying", extra={"fields": {"in_seconds": delay, "failures": self.failures}})
        # Sleep in small steps so SIGTERM is honoured quickly.
        end = time.monotonic() + delay
        while not self.stopping and time.monotonic() < end:
            self.sleep(min(1.0, end - time.monotonic()))

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except psycopg.Error:
                pass
            self.conn = None

    # --- one iteration ---------------------------------------------------------------------

    def run_once(self) -> bool:
        """Receive and process one batch. Returns False if the caller should back off."""
        if not self.ensure_connection():
            return False
        try:
            response = self.sqs.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=self.max_messages,
                WaitTimeSeconds=self.wait_seconds,
                MessageSystemAttributeNames=["ApproximateReceiveCount"],
            )
        except (BotoCoreError, ClientError) as exc:
            log.error("cannot receive from SQS", extra={"fields": {"error": str(exc)}})
            return False

        for message in response.get("Messages", []):
            if self.stopping:
                break  # the rest become visible again after the visibility timeout
            try:
                self.handle(message)
            except DatabaseUnavailable:
                return False
        self.heartbeat()
        return True

    def ensure_connection(self) -> bool:
        if self.conn is not None and not self.conn.closed:
            return True
        try:
            self.conn = self.connect()
        except psycopg.OperationalError as exc:
            log.error("database unavailable", extra={"fields": {"error": str(exc).strip()}})
            self.conn = None
            return False
        log.info("connected to the database")
        return True

    def handle(self, message: dict[str, Any]) -> None:
        fields = {
            "message_id": message.get("MessageId"),
            "receive_count": message.get("Attributes", {}).get("ApproximateReceiveCount"),
        }
        try:
            parsed = parse(message["Body"], self.contract)
        except InvalidMessage as exc:
            self.reject(message, fields, str(exc))
            return

        fields.update(kind=type(parsed).__name__.lower(), device_id=parsed.device_id)
        try:
            inserted = self.store(self.conn, parsed)
        except InvalidMessage as exc:  # e.g. UnknownZone: valid JSON, but it can never be stored
            self.reject(message, fields, str(exc))
            return
        except psycopg.OperationalError as exc:
            log.error("database connection lost, message left in the queue", extra={"fields": {**fields, "error": str(exc).strip()}})
            self.close()
            raise DatabaseUnavailable from exc
        except psycopg.Error as exc:
            # Not deleted: SQS retries it and, after 5 receives, moves it to the DLQ.
            log.error("database rejected message, left in the queue", extra={"fields": {**fields, "error": str(exc).strip()}})
            return

        log.info("stored" if inserted else "duplicate ignored", extra={"fields": fields})
        self.delete(message, fields)

    def reject(self, message: dict[str, Any], fields: dict[str, Any], reason: str) -> None:
        """Invalid message: log it, copy it to the DLQ with the reason, then delete it."""
        log.warning("invalid message", extra={"fields": {**fields, "reason": reason, "body": message["Body"][:500]}})
        if self.dead_letter(message, reason):
            self.delete(message, fields)

    # --- SQS helpers -----------------------------------------------------------------------

    def dead_letter(self, message: dict[str, Any], reason: str) -> bool:
        try:
            self.sqs.send_message(
                QueueUrl=self.dlq_url,
                MessageBody=message["Body"],
                MessageAttributes={
                    "error": {"DataType": "String", "StringValue": reason[:REASON_ATTRIBUTE_MAX]},
                    "source_message_id": {"DataType": "String", "StringValue": message.get("MessageId", "unknown")},
                },
            )
        except (BotoCoreError, ClientError) as exc:
            # Leave it in the queue: it will be retried and redriven to the DLQ by SQS.
            log.error("cannot send to the DLQ", extra={"fields": {"message_id": message.get("MessageId"), "error": str(exc)}})
            return False
        return True

    def delete(self, message: dict[str, Any], fields: dict[str, Any]) -> None:
        try:
            self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=message["ReceiptHandle"])
        except (BotoCoreError, ClientError) as exc:
            # Harmless: the message is redelivered and the insert is idempotent.
            log.error("cannot delete message", extra={"fields": {**fields, "error": str(exc)}})

    def heartbeat(self) -> None:
        if self.health_file is not None:
            self.health_file.touch()
