"""Unit tests of the end-to-end check. AWS and PostgreSQL are mocked.

    python -m unittest discover -s tools/e2e_check
"""

from __future__ import annotations

import io
import json
import random
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

import e2e_check as e2e

NOW = datetime(2026, 12, 11, 10, 15, 30, 250000, tzinfo=timezone.utc)
CONTRACT = e2e.Contract()


def no_wait(predicate, timeout, **_):
    """Stand-in for wait_for: a single attempt, no sleeping."""
    return predicate()


def reading_row(device: str, age_s: float, skew_s: float = 1.0, zone: str = "bed-1") -> dict:
    return {"device_id": device, "zone_id": zone, "ts": NOW - timedelta(seconds=age_s + skew_s),
            "age_s": age_s, "skew_s": skew_s}


class FakeDb:
    def __init__(self, rows=None, stored=True, events=None):
        self.rows = rows or []
        self.stored = stored
        self.events = events or {}
        self.reading_queries = []

    def latest_readings(self):
        return self.rows

    def reading_exists(self, device_id, ts):
        self.reading_queries.append((device_id, ts))
        return self.stored

    def events_for(self, command_id):
        return self.events

    def close(self):
        pass


class FakePublisher:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload):
        self.sent.append((topic, json.loads(payload)))

    def close(self):
        pass


# --- helpers ----------------------------------------------------------------------------------

class HelperTests(unittest.TestCase):
    def test_iso_ms_is_a_contract_timestamp_with_milliseconds(self):
        self.assertEqual(e2e.iso_ms(NOW), "2026-12-11T10:15:30.250Z")
        self.assertEqual(e2e.parse_utc(e2e.iso_ms(NOW)), NOW)

    def test_format_age(self):
        self.assertEqual(e2e.format_age(42), "42s")
        self.assertEqual(e2e.format_age(125), "2m 05s")
        self.assertEqual(e2e.format_age(3 * 3600 + 7 * 60), "3h 07m")
        self.assertEqual(e2e.format_age(2 * 86400 + 3600), "2d 1h")
        self.assertEqual(e2e.format_age(-5), "0s")

    def test_wait_for_retries_until_truthy(self):
        answers = iter([False, False, True])
        sleeps = []
        result = e2e.wait_for(lambda: next(answers), timeout=60, interval=2,
                              clock=lambda: 0.0, sleep=sleeps.append)
        self.assertTrue(result)
        self.assertEqual(sleeps, [2, 2])

    def test_wait_for_gives_up_at_the_timeout(self):
        clock = iter([0.0, 30.0, 61.0])
        calls = []
        result = e2e.wait_for(lambda: calls.append(1), timeout=60, clock=lambda: next(clock), sleep=lambda _: None)
        self.assertFalse(result)
        self.assertEqual(len(calls), 2)

    def test_tunnel_command_uses_the_instance_and_port(self):
        command = e2e.tunnel_command("smart-gardening", "eu-south-2", "i-0abc", 15432)
        self.assertIn("--target i-0abc", command)
        self.assertIn('"localPortNumber":["15432"]', command)
        self.assertIn("--profile smart-gardening", command)
        self.assertIn("<instance_id>", e2e.tunnel_command(None, "eu-south-2", None, 5432))


# --- contract ---------------------------------------------------------------------------------

class CommandValidationTests(unittest.TestCase):
    def test_command_follows_the_contract(self):
        command = e2e.make_command("bed-1", NOW)
        self.assertEqual(CONTRACT.errors("commands", command), [])
        self.assertEqual(command["duration_s"], 10)
        self.assertEqual(e2e.parse_utc(command["expires_at"]) - e2e.parse_utc(command["issued_at"]),
                         timedelta(minutes=2))

    def test_command_ids_are_unique(self):
        self.assertNotEqual(e2e.make_command("bed-1", NOW)["command_id"], e2e.make_command("bed-1", NOW)["command_id"])

    def test_invalid_commands_are_caught(self):
        cases = {
            "expired before issued": dict(expires_at="2026-12-11T10:00:00Z"),
            "same instant": dict(expires_at=e2e.make_command("bed-1", NOW)["issued_at"]),
            "too long": dict(duration_s=901),
            "zero duration": dict(duration_s=0),
            "bad zone": dict(zone="Bed 1"),
            "bad id": dict(command_id="not-a-uuid"),
            "unknown field": dict(device_id="pi-01"),
            "non-UTC": dict(issued_at="2026-12-11T10:15:30+01:00"),
        }
        for name, change in cases.items():
            command = e2e.make_command("bed-1", NOW)
            command.update(change)
            with self.subTest(name):
                self.assertNotEqual(CONTRACT.errors("commands", command), [])

    def test_oversized_payload_is_caught(self):
        reading = e2e.make_test_reading("e2e-check", "bed-1", NOW, random.Random(1))
        reading["device_id"] = "x" * 9000
        self.assertTrue(any("limit" in error for error in CONTRACT.errors("readings", reading)))

    def test_test_reading_follows_the_contract(self):
        rng = random.Random(4)
        for hour in range(24):
            reading = e2e.make_test_reading("e2e-check", "bed-1", NOW.replace(hour=hour), rng)
            with self.subTest(hour=hour):
                self.assertEqual(CONTRACT.errors("readings", reading), [])
                self.assertTrue(reading["ts"].endswith(".250Z"))


# --- a. server --------------------------------------------------------------------------------

def ec2_with(*ids):
    ec2 = mock.Mock()
    ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{"InstanceId": i} for i in ids]}]}
    return ec2


def ssm_with(*info):
    ssm = mock.Mock()
    ssm.describe_instance_information.return_value = {"InstanceInformationList": list(info)}
    return ssm


class ServerTests(unittest.TestCase):
    def test_online(self):
        result, instance = e2e.check_server(ec2_with("i-1"), ssm_with({"PingStatus": "Online"}))
        self.assertEqual((result.status, instance), (e2e.OK, "i-1"))

    def test_connection_lost(self):
        result, _ = e2e.check_server(ec2_with("i-1"), ssm_with({"PingStatus": "ConnectionLost"}))
        self.assertEqual(result.status, e2e.FAIL)
        self.assertIn("ConnectionLost", result.summary)

    def test_not_registered_in_ssm(self):
        result, instance = e2e.check_server(ec2_with("i-1"), ssm_with())
        self.assertEqual((result.status, instance), (e2e.FAIL, "i-1"))

    def test_no_running_instance(self):
        result, instance = e2e.check_server(ec2_with(), ssm_with())
        self.assertEqual((result.status, instance), (e2e.FAIL, None))

    def test_ambiguous_instances(self):
        result, _ = e2e.check_server(ec2_with("i-1", "i-2"), ssm_with())
        self.assertEqual(result.status, e2e.FAIL)
        self.assertIn("--instance-id", result.details[0])

    def test_explicit_instance_skips_the_lookup(self):
        ec2 = ec2_with()
        result, instance = e2e.check_server(ec2, ssm_with({"PingStatus": "Online"}), "i-9")
        self.assertEqual((result.status, instance), (e2e.OK, "i-9"))
        ec2.describe_instances.assert_not_called()


# --- b. readings ------------------------------------------------------------------------------

class ReadingsTests(unittest.TestCase):
    def test_recent_reading_is_ok(self):
        result = e2e.check_readings(FakeDb([reading_row("pi-01", 120)]), 15, "e2e-check")
        self.assertEqual(result.status, e2e.OK)
        self.assertIn("pi-01", result.details[0])

    def test_threshold_is_inclusive_and_configurable(self):
        self.assertEqual(e2e.check_readings(FakeDb([reading_row("pi-01", 15 * 60)]), 15, "e2e-check").status, e2e.OK)
        self.assertEqual(e2e.check_readings(FakeDb([reading_row("pi-01", 15 * 60 + 1)]), 15, "e2e-check").status, e2e.FAIL)
        self.assertEqual(e2e.check_readings(FakeDb([reading_row("pi-01", 25 * 60)]), 30, "e2e-check").status, e2e.OK)

    def test_newest_device_decides(self):
        rows = [reading_row("pi-01", 3600), reading_row("pi-02", 60)]
        result = e2e.check_readings(FakeDb(rows), 15, "e2e-check")
        self.assertEqual(result.status, e2e.OK)
        self.assertIn("pi-02", result.summary)
        self.assertEqual(len(result.details), 2)

    def test_test_device_does_not_hide_a_dead_pi(self):
        rows = [reading_row("pi-01", 3600), reading_row("e2e-check", 10, zone="e2e-test")]
        result = e2e.check_readings(FakeDb(rows), 15, "e2e-check")
        self.assertEqual(result.status, e2e.FAIL)
        self.assertIn("pi-01", result.summary)
        self.assertTrue(any("not counted" in line for line in result.details))

    def test_any_device_in_the_test_zone_does_not_count(self):
        rows = [reading_row("pi-01", 3600), reading_row("pi-99", 10, zone="e2e-test")]
        result = e2e.check_readings(FakeDb(rows), 15, "e2e-check")
        self.assertEqual(result.status, e2e.FAIL)
        self.assertIn("pi-01", result.summary)

    def test_no_readings(self):
        self.assertEqual(e2e.check_readings(FakeDb([]), 15, "e2e-check").status, e2e.FAIL)
        self.assertEqual(e2e.check_readings(FakeDb([reading_row("e2e-check", 5)]), 15, "e2e-check").status, e2e.FAIL)

    def test_clock_skew_is_flagged(self):
        result = e2e.check_readings(FakeDb([reading_row("pi-01", 60, skew_s=-3600)]), 15, "e2e-check")
        self.assertEqual(result.status, e2e.OK)
        self.assertIn("device clock", result.details[0])

    def test_no_database_explains_the_tunnel(self):
        result = e2e.check_readings(None, 15, "e2e-check", "connection refused", 5432,
                                    lambda port: f"tunnel to {port}")
        self.assertEqual(result.status, e2e.FAIL)
        self.assertIn("connection refused", result.summary)
        self.assertIn("tunnel to 5432", "\n".join(result.details))

    def test_another_postgres_on_the_port_suggests_another_port(self):
        error = 'connection failed: ... FATAL:  password authentication failed for user "app"'
        for port, other in ((15432, 25432), (5432, 15432)):
            result = e2e.check_readings(None, 15, "e2e-check", error, port, lambda p: f"tunnel to {p}")
            with self.subTest(port=port):
                self.assertEqual(result.status, e2e.FAIL)
                self.assertIn(f"localhost:{port} is not the project database", result.summary)
                text = "\n".join(result.details)
                self.assertIn(f"tunnel to {other}", text)
                self.assertIn(f"--db-port {other}", text)


# --- c. ingest path ---------------------------------------------------------------------------

class IngestPathTests(unittest.TestCase):
    def run_step(self, db):
        publisher = FakePublisher()
        result = e2e.check_ingest_path(publisher, db, CONTRACT, "e2e-check", "bed-1", 60,
                                       now=lambda: NOW, waiter=no_wait, rng=random.Random(1))
        return result, publisher

    def test_reading_arrives(self):
        db = FakeDb(stored=True)
        result, publisher = self.run_step(db)
        self.assertEqual(result.status, e2e.OK)
        topic, payload = publisher.sent[0]
        self.assertEqual(topic, "smart-gardening/e2e-check/readings")
        self.assertEqual(payload["zone"], "bed-1")  # whatever zone the caller passes
        self.assertEqual(payload["ts"], "2026-12-11T10:15:30.250Z")
        self.assertEqual(db.reading_queries, [("e2e-check", NOW)])

    def test_reading_never_arrives(self):
        result, publisher = self.run_step(FakeDb(stored=False))
        self.assertEqual(result.status, e2e.FAIL)
        self.assertEqual(len(publisher.sent), 1)
        self.assertIn("DLQ", "\n".join(result.details))

    def test_without_database_nothing_is_published(self):
        result, publisher = self.run_step(None)
        self.assertEqual(result.status, e2e.FAIL)
        self.assertEqual(publisher.sent, [])

    def test_invalid_reading_is_not_sent(self):
        publisher = FakePublisher()
        result = e2e.check_ingest_path(publisher, FakeDb(), CONTRACT, "e2e-check", "Not A Zone", 60,
                                       now=lambda: NOW, waiter=no_wait)
        self.assertEqual(result.status, e2e.FAIL)
        self.assertEqual(publisher.sent, [])


# --- d. command -------------------------------------------------------------------------------

FINISHED = {"irrigation_started": {"duration_s": 10, "reason": None},
            "irrigation_finished": {"duration_s": 10, "reason": None}}


class CommandTests(unittest.TestCase):
    def run_step(self, db, zone="bed-1"):
        publisher = FakePublisher()
        result = e2e.check_command(publisher, db, CONTRACT, "pi-01", zone, 60, now=lambda: NOW, waiter=no_wait)
        return result, publisher

    def test_started_and_finished(self):
        result, publisher = self.run_step(FakeDb(events=FINISHED))
        self.assertEqual(result.status, e2e.OK)
        topic, command = publisher.sent[0]
        self.assertEqual(topic, "smart-gardening/pi-01/commands")
        self.assertEqual(CONTRACT.errors("commands", command), [])
        self.assertEqual((command["zone"], command["duration_s"]), ("bed-1", 10))
        self.assertEqual(command["expires_at"], "2026-12-11T10:17:30Z")
        self.assertIn(command["command_id"], result.details[0])

    def test_no_events(self):
        result, _ = self.run_step(FakeDb(events={}))
        self.assertEqual(result.status, e2e.FAIL)
        self.assertIn("irrigation_started, irrigation_finished", result.summary)

    def test_only_started(self):
        result, _ = self.run_step(FakeDb(events={"irrigation_started": FINISHED["irrigation_started"]}))
        self.assertEqual(result.status, e2e.FAIL)
        self.assertIn("irrigation_finished", result.summary)
        self.assertNotIn("irrigation_started", result.summary)

    def test_rejected(self):
        result, _ = self.run_step(FakeDb(events={"command_rejected": {"duration_s": None, "reason": "busy"}}))
        self.assertEqual(result.status, e2e.FAIL)
        self.assertIn("busy", result.summary)

    def test_invalid_command_is_not_sent(self):
        result, publisher = self.run_step(FakeDb(events=FINISHED), zone="Bed 1")
        self.assertEqual(result.status, e2e.FAIL)
        self.assertEqual(publisher.sent, [])


# --- e. queues --------------------------------------------------------------------------------

QUEUE = "https://sqs.example/000000000000/smart-gardening-ingest"
DLQ = "https://sqs.example/000000000000/smart-gardening-ingest-dlq"


def sqs_with(depths):
    sqs = mock.Mock()
    sqs.get_queue_attributes.side_effect = lambda QueueUrl, AttributeNames: {"Attributes": {
        "ApproximateNumberOfMessages": str(depths[QueueUrl][0]),
        "ApproximateNumberOfMessagesNotVisible": str(depths[QueueUrl][1]),
    }}
    return sqs


class QueueTests(unittest.TestCase):
    def test_empty_dlq_is_ok(self):
        result = e2e.check_queues(sqs_with({QUEUE: (2, 1), DLQ: (0, 0)}), QUEUE, DLQ, "smart-gardening", "eu-south-2")
        self.assertEqual(result.status, e2e.OK)
        self.assertEqual(result.summary, "ingest 3, DLQ 0")

    def test_messages_in_the_dlq_warn_with_the_command(self):
        result = e2e.check_queues(sqs_with({QUEUE: (0, 0), DLQ: (3, 0)}), QUEUE, DLQ, "smart-gardening", "eu-south-2")
        self.assertEqual(result.status, e2e.WARN)
        text = "\n".join(result.details)
        self.assertIn("aws sqs receive-message", text)
        self.assertIn(e2e.DLQ_URL_PARAMETER, text)

    def test_queue_urls_are_not_printed(self):
        result = e2e.check_queues(sqs_with({QUEUE: (0, 0), DLQ: (1, 0)}), QUEUE, DLQ, None, "eu-south-2")
        text = result.summary + "\n".join(result.details)
        self.assertNotIn("000000000000", text)
        self.assertIn("smart-gardening-ingest-dlq", text)


# --- summary, exit codes and the whole run ----------------------------------------------------

class ExitCodeTests(unittest.TestCase):
    def results(self, *statuses):
        return [e2e.StepResult(f"step {i}", s, "") for i, s in enumerate(statuses)]

    def test_exit_codes(self):
        self.assertEqual(e2e.exit_code(self.results(e2e.OK, e2e.OK)), 0)
        self.assertEqual(e2e.exit_code(self.results(e2e.OK, e2e.WARN, e2e.SKIP)), 0)
        self.assertEqual(e2e.exit_code(self.results(e2e.OK, e2e.FAIL, e2e.WARN)), 1)

    def test_summary_names_the_failed_steps(self):
        out = io.StringIO()
        e2e.print_summary(self.results(e2e.OK, e2e.FAIL, e2e.WARN), out)
        self.assertIn("RESULT: FAIL", out.getvalue())
        self.assertIn("step 1", out.getvalue())
        out = io.StringIO()
        e2e.print_summary(self.results(e2e.OK, e2e.WARN), out)
        self.assertIn("RESULT: OK with warnings", out.getvalue())


class ArgsTests(unittest.TestCase):
    def test_defaults(self):
        args = e2e.parse_args([])
        self.assertEqual((args.max_age_min, args.timeout, args.device, args.zone), (15, 60, "pi-01", "bed-1"))
        self.assertEqual((args.test_device, args.test_zone, args.db_port), ("e2e-check", "e2e-test", 15432))
        self.assertFalse(args.command or args.skip_publish)

    def test_command_and_skip_publish_conflict(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                e2e.parse_args(["--command", "--skip-publish"])
            with self.assertRaises(SystemExit):
                e2e.parse_args(["--max-age-min", "0"])


class FakeSession:
    """boto3.Session stand-in: a healthy server, SSM parameters and queues."""

    def __init__(self, ping="Online", dlq=(0, 0)):
        self.ssm = ssm_with({"PingStatus": ping})
        params = {e2e.APP_PASSWORD_PARAMETER: "secret", e2e.ENDPOINT_PARAMETER: "iot.example",
                  e2e.QUEUE_URL_PARAMETER: QUEUE, e2e.DLQ_URL_PARAMETER: DLQ}
        self.ssm.get_parameter.side_effect = lambda Name, WithDecryption: {"Parameter": {"Value": params[Name]}}
        self.clients = {"ssm": self.ssm, "ec2": ec2_with("i-1"), "sqs": sqs_with({QUEUE: (0, 0), DLQ: dlq})}

    def client(self, name):
        return self.clients[name]


class RunTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(e2e, "wait_for", no_wait)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_check(self, argv, session=None, db=None, connect_error=None):
        session = session or FakeSession()
        db = db if db is not None else FakeDb([reading_row("pi-01", 60)], events=FINISHED)
        publisher = FakePublisher()
        passwords = []

        def connect(password):
            passwords.append(password)
            if connect_error:
                raise e2e.DatabaseUnavailable(connect_error)
            return db

        out = io.StringIO()
        with redirect_stdout(out):
            results = e2e.run(e2e.parse_args(argv), session=session, connect_db=connect,
                              make_publisher=lambda endpoint: publisher, contract=CONTRACT)
        return results, publisher, out.getvalue(), passwords

    def statuses(self, results):
        return [r.status for r in results]

    def test_all_green(self):
        results, publisher, out, passwords = self.run_check([])
        self.assertEqual(self.statuses(results), [e2e.OK, e2e.OK, e2e.OK, e2e.SKIP, e2e.OK])
        self.assertEqual(e2e.exit_code(results), 0)
        self.assertEqual([topic for topic, _ in publisher.sent], ["smart-gardening/e2e-check/readings"])
        self.assertEqual(publisher.sent[0][1]["zone"], "e2e-test")
        self.assertEqual(passwords, ["secret"])
        self.assertNotIn("secret", out)

    def test_app_password_is_read_decrypted_from_ssm(self):
        session = FakeSession()
        self.run_check([], session=session)
        session.ssm.get_parameter.assert_any_call(Name=e2e.APP_PASSWORD_PARAMETER, WithDecryption=True)

    def test_skip_publish_publishes_nothing(self):
        results, publisher, _, _ = self.run_check(["--skip-publish"])
        self.assertEqual(self.statuses(results), [e2e.OK, e2e.OK, e2e.SKIP, e2e.SKIP, e2e.OK])
        self.assertEqual(publisher.sent, [])
        self.assertEqual(e2e.exit_code(results), 0)

    def test_with_command(self):
        results, publisher, _, _ = self.run_check(["--command", "--device", "pi-02", "--zone", "bed-2"])
        self.assertEqual(self.statuses(results), [e2e.OK] * 5)
        topic, command = publisher.sent[1]
        self.assertEqual((topic, command["zone"]), ("smart-gardening/pi-02/commands", "bed-2"))

    def test_zone_only_changes_the_command_not_the_test_reading(self):
        _, publisher, _, _ = self.run_check(["--command", "--zone", "bed-2"])
        (reading_topic, reading), (_, command) = publisher.sent
        self.assertEqual((reading_topic, reading["zone"]), ("smart-gardening/e2e-check/readings", "e2e-test"))
        self.assertEqual(command["zone"], "bed-2")

    def test_dlq_warning_still_exits_0(self):
        results, _, _, _ = self.run_check([], session=FakeSession(dlq=(2, 0)))
        self.assertEqual(results[-1].status, e2e.WARN)
        self.assertEqual(e2e.exit_code(results), 0)

    def test_no_tunnel_fails_and_explains(self):
        results, publisher, out, _ = self.run_check([], connect_error="connection refused")
        self.assertEqual(self.statuses(results), [e2e.OK, e2e.FAIL, e2e.FAIL, e2e.SKIP, e2e.OK])
        self.assertEqual(publisher.sent, [])
        self.assertIn("--target i-1", out)
        self.assertEqual(e2e.exit_code(results), 1)

    def test_offline_server_fails(self):
        results, _, _, _ = self.run_check(["--skip-publish"], session=FakeSession(ping="ConnectionLost"))
        self.assertEqual(results[0].status, e2e.FAIL)
        self.assertEqual(e2e.exit_code(results), 1)

    def test_stale_readings_fail(self):
        results, _, _, _ = self.run_check(["--skip-publish", "--max-age-min", "5"],
                                          db=FakeDb([reading_row("pi-01", 600)]))
        self.assertEqual(results[1].status, e2e.FAIL)
        self.assertEqual(e2e.exit_code(results), 1)

    def test_an_exception_in_one_step_does_not_stop_the_others(self):
        session = FakeSession()
        session.clients["sqs"].get_queue_attributes.side_effect = RuntimeError("AccessDenied")
        results, _, _, _ = self.run_check([], session=session)
        self.assertEqual(results[-1].status, e2e.FAIL)
        self.assertIn("AccessDenied", results[-1].summary)
        self.assertEqual(len(results), 5)


if __name__ == "__main__":
    unittest.main()
