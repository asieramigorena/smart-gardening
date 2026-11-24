"""Worker delivery rules with a mocked SQS client and store (no AWS, no database)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import psycopg
from botocore.exceptions import ClientError

from ingest.store import UnknownZone
from ingest.worker import Worker

from .helpers import CONTRACT, REAL_SQS_BODY

QUEUE = "https://sqs.example/queue"
DLQ = "https://sqs.example/dlq"


def sqs_message(body: str, n: int = 1) -> dict:
    return {
        "MessageId": f"m-{n}",
        "ReceiptHandle": f"rh-{n}",
        "Body": body,
        "Attributes": {"ApproximateReceiveCount": "1"},
    }


def client_error(operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, operation)


class WorkerTestCase(unittest.TestCase):
    def setUp(self):
        self.sqs = mock.Mock()
        self.sqs.receive_message.return_value = {"Messages": []}
        self.conn = mock.Mock(closed=False)
        self.connect = mock.Mock(return_value=self.conn)
        self.store = mock.Mock(return_value=True)
        self.sleep = mock.Mock()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.health_file = Path(self.tmp.name) / "heartbeat"
        self.worker = Worker(
            sqs=self.sqs,
            queue_url=QUEUE,
            dlq_url=DLQ,
            contract=CONTRACT,
            connect=self.connect,
            health_file=self.health_file,
            store=self.store,
            sleep=self.sleep,
        )

    def receive(self, *bodies: str) -> None:
        self.sqs.receive_message.return_value = {
            "Messages": [sqs_message(body, n) for n, body in enumerate(bodies, 1)]
        }


class ReceiveTests(WorkerTestCase):
    def test_long_polls_ten_messages_for_twenty_seconds(self):
        self.assertTrue(self.worker.run_once())
        kwargs = self.sqs.receive_message.call_args.kwargs
        self.assertEqual(kwargs["QueueUrl"], QUEUE)
        self.assertEqual(kwargs["MaxNumberOfMessages"], 10)
        self.assertEqual(kwargs["WaitTimeSeconds"], 20)

    def test_heartbeat_after_each_poll(self):
        self.worker.run_once()
        self.assertTrue(self.health_file.exists())

    def test_sqs_error_backs_off(self):
        self.sqs.receive_message.side_effect = client_error("ReceiveMessage")
        self.assertFalse(self.worker.run_once())
        self.assertFalse(self.health_file.exists())


class ValidMessageTests(WorkerTestCase):
    def test_real_body_is_stored_then_deleted(self):
        order = mock.Mock()
        order.attach_mock(self.store, "store")
        order.attach_mock(self.sqs.delete_message, "delete")
        self.receive(REAL_SQS_BODY)

        self.assertTrue(self.worker.run_once())

        self.assertEqual([c[0] for c in order.mock_calls], ["store", "delete"])
        stored = self.store.call_args.args[1]
        self.assertEqual(stored.device_id, "pi-01")
        self.sqs.delete_message.assert_called_once_with(QueueUrl=QUEUE, ReceiptHandle="rh-1")
        self.sqs.send_message.assert_not_called()

    def test_duplicate_is_deleted_too(self):
        self.store.return_value = False
        self.receive(REAL_SQS_BODY)
        self.worker.run_once()
        self.sqs.delete_message.assert_called_once()


class InvalidMessageTests(WorkerTestCase):
    def test_invalid_goes_to_dlq_with_reason_then_is_deleted(self):
        body = json.dumps({**json.loads(REAL_SQS_BODY), "kind": "commands"})
        self.receive(body)

        self.worker.run_once()

        self.store.assert_not_called()
        self.sqs.send_message.assert_called_once()
        kwargs = self.sqs.send_message.call_args.kwargs
        self.assertEqual(kwargs["QueueUrl"], DLQ)
        self.assertEqual(kwargs["MessageBody"], body)
        self.assertIn("kind", kwargs["MessageAttributes"]["error"]["StringValue"])
        self.assertEqual(kwargs["MessageAttributes"]["source_message_id"]["StringValue"], "m-1")
        self.sqs.delete_message.assert_called_once_with(QueueUrl=QUEUE, ReceiptHandle="rh-1")

    def test_not_deleted_if_dlq_send_fails(self):
        self.sqs.send_message.side_effect = client_error("SendMessage")
        self.receive("not json")
        self.worker.run_once()
        self.sqs.delete_message.assert_not_called()

    def test_unknown_zone_goes_to_dlq_with_reason_then_is_deleted(self):
        self.store.side_effect = UnknownZone("bed1")
        self.receive(REAL_SQS_BODY)

        self.assertTrue(self.worker.run_once())

        self.sqs.send_message.assert_called_once()
        kwargs = self.sqs.send_message.call_args.kwargs
        self.assertEqual(kwargs["QueueUrl"], DLQ)
        self.assertEqual(kwargs["MessageBody"], REAL_SQS_BODY)
        self.assertTrue(kwargs["MessageAttributes"]["error"]["StringValue"].startswith("unknown_zone"))
        self.sqs.delete_message.assert_called_once_with(QueueUrl=QUEUE, ReceiptHandle="rh-1")


class DatabaseErrorTests(WorkerTestCase):
    def test_connection_lost_keeps_message_and_stops_batch(self):
        self.store.side_effect = psycopg.OperationalError("server closed the connection")
        self.receive(REAL_SQS_BODY, REAL_SQS_BODY)

        self.assertFalse(self.worker.run_once())

        self.assertEqual(self.store.call_count, 1)  # second message not attempted
        self.sqs.delete_message.assert_not_called()
        self.sqs.send_message.assert_not_called()
        self.assertIsNone(self.worker.conn)
        self.conn.close.assert_called_once()

    def test_other_database_error_keeps_message_and_continues(self):
        self.store.side_effect = [psycopg.errors.DataError("bad value"), True]
        self.receive(REAL_SQS_BODY, REAL_SQS_BODY)

        self.assertTrue(self.worker.run_once())

        self.sqs.delete_message.assert_called_once_with(QueueUrl=QUEUE, ReceiptHandle="rh-2")
        self.sqs.send_message.assert_not_called()

    def test_database_down_does_not_receive(self):
        self.connect.side_effect = psycopg.OperationalError("connection refused")
        self.assertFalse(self.worker.run_once())
        self.sqs.receive_message.assert_not_called()

    def test_reconnects_after_failure(self):
        self.connect.side_effect = [psycopg.OperationalError("refused"), self.conn]
        self.assertFalse(self.worker.run_once())
        self.assertTrue(self.worker.run_once())
        self.assertEqual(self.connect.call_count, 2)


class LifecycleTests(WorkerTestCase):
    def fake_clock(self):
        clock = [0.0]
        self.worker.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        return clock, mock.patch("ingest.worker.time.monotonic", side_effect=lambda: clock[0])

    def test_backoff_grows_and_is_capped(self):
        clock, patch = self.fake_clock()
        waited = []
        with patch:
            for _ in range(8):
                start = clock[0]
                self.worker.backoff()
                waited.append(round(clock[0] - start))
        self.assertEqual(waited, [1, 2, 4, 8, 16, 32, 60, 60])

    def test_stop_interrupts_backoff(self):
        clock, patch = self.fake_clock()
        self.worker.failures = 10  # next wait would be 60 s

        def sleep(seconds):
            clock[0] += seconds
            self.worker.stop()

        self.worker.sleep = sleep
        with patch:
            self.worker.backoff()
        self.assertLessEqual(clock[0], 1.0)

    def test_stop_ends_run_loop_and_closes_connection(self):
        def receive(**_):
            self.worker.stop()  # e.g. SIGTERM during the long poll
            return {"Messages": [sqs_message(REAL_SQS_BODY)]}

        self.sqs.receive_message.side_effect = receive
        self.worker.run()

        self.sqs.receive_message.assert_called_once()
        self.store.assert_not_called()  # stop requested before handling: message stays queued
        self.conn.close.assert_called_once()

    def test_failures_reset_after_success(self):
        self.worker.failures = 5
        self.sqs.receive_message.side_effect = lambda **_: (self.worker.stop(), {"Messages": []})[1]
        self.worker.run()
        self.assertEqual(self.worker.failures, 0)


if __name__ == "__main__":
    unittest.main()
