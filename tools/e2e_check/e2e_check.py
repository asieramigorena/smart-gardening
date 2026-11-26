"""End-to-end check of the smart gardening chain, run from a laptop before a demo.

Steps (each prints OK, WARN, FAIL or SKIP):

  a. Server   the project's EC2 instance is Online in SSM.
  b. Readings latest reading of each device in PostgreSQL; FAIL if the newest one (from a real
              device) is older than --max-age-min.
  c. Ingest   publishes a test reading (device e2e-check, zone e2e-test) through IoT Core and
              waits for it in `readings`.
  d. Command  (only with --command) publishes a 10 s irrigation command and waits for
              irrigation_started and irrigation_finished in `irrigation_events`.
  e. Queues   messages in the ingestion queue and the DLQ; WARN if the DLQ is not empty.

PostgreSQL is reached through an SSM tunnel to localhost:15432 (not 5432, which a PostgreSQL
installed on the laptop often holds) that must already be open; the script prints the command
if it is not. The `app` password is read from SSM at run time and
is never written anywhere.

Exit code: 0 if no step failed (warnings allowed), 1 otherwise.

Examples:
  python tools/e2e_check/e2e_check.py
  python tools/e2e_check/e2e_check.py --skip-publish          # read-only
  python tools/e2e_check/e2e_check.py --command --device pi-01 --zone bed-1   # --zone: step d only
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

TOOLS_DIR = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = TOOLS_DIR.parent / "docs" / "contracts"
sys.path.insert(0, str(TOOLS_DIR / "simulator"))
import simulate  # noqa: E402  (tools/simulator/simulate.py)

PROJECT = "smart-gardening"
SERVER_NAME_TAG = "smart-gardening-server"
APP_PASSWORD_PARAMETER = "/smart-gardening/db/app_password"
ENDPOINT_PARAMETER = simulate.ENDPOINT_PARAMETER
QUEUE_URL_PARAMETER = "/smart-gardening/ingest/queue_url"
DLQ_URL_PARAMETER = "/smart-gardening/ingest/dlq_url"
DB_NAME = "smart_gardening"
DB_USER = "app"
DEFAULT_DB_PORT = 15432
TEST_DEVICE = "e2e-check"
TEST_ZONE = "e2e-test"  # DB/migrations/20261126110000_add_e2e_test_zone.sql; no valve
MAX_PAYLOAD_BYTES = 8 * 1024  # docs/contracts/README.md, rule 6
COMMAND_DURATION_S = 10
COMMAND_TTL = timedelta(minutes=2)
CLOCK_SKEW_WARN_S = 120  # received_at - ts above this suggests the device clock is off

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"


@dataclass
class StepResult:
    name: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)


# --- helpers ----------------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_ms(ts: datetime) -> str:
    """UTC timestamp with milliseconds, as allowed by the contract (e.g. 2026-11-19T10:15:00.250Z)."""
    ts = ts.astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_age(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


def wait_for(predicate: Callable[[], object], timeout: float, interval: float = 2.0,
             clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep) -> object:
    """Call predicate until it returns something truthy or timeout seconds pass. Returns its last value."""
    deadline = clock() + timeout
    while True:
        value = predicate()
        if value or clock() >= deadline:
            return value
        sleep(interval)


def tunnel_command(profile: str | None, region: str, instance_id: str | None, port: int) -> str:
    profile_arg = f" --profile {profile}" if profile else ""
    return (f"aws ssm start-session{profile_arg} --region {region} --target {instance_id or '<instance_id>'} "
            "--document-name AWS-StartPortForwardingSession "
            f"--parameters '{{\"portNumber\":[\"5432\"],\"localPortNumber\":[\"{port}\"]}}'")


# --- contract ---------------------------------------------------------------------------------

class Contract:
    """Validation against docs/contracts (the source of truth), with the rules JSON Schema cannot express."""

    def __init__(self, contracts_dir: Path = CONTRACTS_DIR):
        from jsonschema import Draft202012Validator, FormatChecker
        from referencing import Registry, Resource

        schemas = {kind: json.loads((contracts_dir / f"{kind}.schema.json").read_text())
                   for kind in ("readings", "commands", "events")}
        registry = Registry().with_resources(
            (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
        )
        self._validators = {kind: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
                            for kind, schema in schemas.items()}

    def errors(self, kind: str, payload: dict) -> list[str]:
        """Every reason why payload breaks the contract of `kind` (empty list = valid)."""
        errors = []
        for error in self._validators[kind].iter_errors(payload):
            where = "/".join(str(part) for part in error.absolute_path) or "(root)"
            errors.append(f"{where}: {error.message}")
        size = len(json.dumps(payload, separators=(",", ":")).encode())
        if size > MAX_PAYLOAD_BYTES:
            errors.append(f"payload is {size} bytes, the limit is {MAX_PAYLOAD_BYTES}")
        if kind == "commands" and not errors and parse_utc(payload["expires_at"]) <= parse_utc(payload["issued_at"]):
            errors.append("expires_at must be later than issued_at")
        return errors


def make_command(zone: str, now: datetime, duration_s: int = COMMAND_DURATION_S,
                 ttl: timedelta = COMMAND_TTL) -> dict:
    now = now.astimezone(timezone.utc).replace(microsecond=0)
    return {
        "schema_version": 1,
        "command_id": str(uuid.uuid4()),
        "action": "irrigate",
        "zone": zone,
        "duration_s": duration_s,
        "issued_at": simulate.iso(now),
        "expires_at": simulate.iso(now + ttl),
    }


def make_test_reading(device: str, zone: str, now: datetime, rng: random.Random) -> dict:
    """A simulator reading with a millisecond timestamp, so (device_id, ts) is unique per run."""
    reading = simulate.make_reading(device, zone, now, rng)
    reading["ts"] = iso_ms(now)
    return reading


# --- database ---------------------------------------------------------------------------------

class DatabaseUnavailable(Exception):
    pass


class Database:
    """The few queries the check needs. Autocommit, so every poll sees the latest rows and now()."""

    def __init__(self, host: str, port: int, password: str):
        import psycopg

        try:
            self.conn = psycopg.connect(host=host, port=port, dbname=DB_NAME, user=DB_USER,
                                        password=password, connect_timeout=5, autocommit=True,
                                        application_name="e2e_check")
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailable(str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)) from exc

    def latest_readings(self) -> list[dict]:
        """Last reading of each device: ts, age of received_at (server clock) and received_at - ts."""
        rows = self.conn.execute(
            """SELECT DISTINCT ON (device_id)
                      device_id, zone_id, ts,
                      extract(epoch FROM now() - received_at)::float AS age_s,
                      extract(epoch FROM received_at - ts)::float AS skew_s
               FROM readings
               ORDER BY device_id, received_at DESC"""
        ).fetchall()
        keys = ("device_id", "zone_id", "ts", "age_s", "skew_s")
        return [dict(zip(keys, row)) for row in rows]

    def reading_exists(self, device_id: str, ts: datetime) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM readings WHERE device_id = %s AND ts = %s", (device_id, ts)
        ).fetchone() is not None

    def events_for(self, command_id: str) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT type, ts, duration_s, reason FROM irrigation_events WHERE command_id = %s",
            (command_id,),
        ).fetchall()
        return {row[0]: {"ts": row[1], "duration_s": row[2], "reason": row[3]} for row in rows}

    def close(self) -> None:
        self.conn.close()


# --- steps ------------------------------------------------------------------------------------

def check_server(ec2, ssm, instance_id: str | None = None) -> tuple[StepResult, str | None]:
    """a. The project's instance is registered and Online in SSM. Returns the instance ID too."""
    name = "a. Server"
    if not instance_id:
        reservations = ec2.describe_instances(Filters=[
            {"Name": "tag:Name", "Values": [SERVER_NAME_TAG]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ])["Reservations"]
        ids = [i["InstanceId"] for r in reservations for i in r["Instances"]]
        if not ids:
            return StepResult(name, FAIL, f"no running instance tagged Name={SERVER_NAME_TAG}",
                              ["Is it stopped? Check the EC2 console or `terraform output instance_id`."]), None
        if len(ids) > 1:
            return StepResult(name, FAIL, f"{len(ids)} running instances tagged Name={SERVER_NAME_TAG}",
                              ["Pass the right one with --instance-id."]), None
        instance_id = ids[0]

    info = ssm.describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
    )["InstanceInformationList"]
    if not info:
        return StepResult(name, FAIL, f"{instance_id} is not registered in SSM",
                          ["The SSM agent may still be starting (2-3 min after boot) or the instance role is missing."]), instance_id
    ping = info[0].get("PingStatus", "unknown")
    if ping != "Online":
        last = info[0].get("LastPingDateTime")
        detail = [f"Last ping: {last}"] if last else []
        return StepResult(name, FAIL, f"{instance_id} is {ping} in SSM", detail), instance_id
    return StepResult(name, OK, f"{instance_id} is Online in SSM"), instance_id


def wrong_server(db_error: str) -> bool:
    """The port answered but it is not our database (typically a PostgreSQL installed on the laptop)."""
    return any(text in db_error for text in ("password authentication failed", "does not exist"))


def check_readings(db: Database | None, max_age_min: float, test_device: str,
                   db_error: str | None = None, db_port: int = DEFAULT_DB_PORT,
                   tunnel: Callable[[int], str] = lambda port: "", test_zone: str = TEST_ZONE) -> StepResult:
    """b. Latest reading per device; FAIL if the newest real one is older than max_age_min.

    Readings of the test device or in the test zone are listed but do not count.
    """
    name = "b. Readings"
    if db is None:
        if db_error and wrong_server(db_error):
            other = 25432 if db_port == DEFAULT_DB_PORT else DEFAULT_DB_PORT
            return StepResult(name, FAIL, f"localhost:{db_port} is not the project database ({db_error})", [
                f"Something else listens on port {db_port} (a local PostgreSQL?). Tunnel to another local port:",
                f"  {tunnel(other)}",
                f"and run this script with --db-port {other}.",
            ])
        return StepResult(name, FAIL, f"cannot connect to PostgreSQL ({db_error})", [
            "Open the SSM tunnel in another terminal and keep it running:",
            f"  {tunnel(db_port)}",
            "(if a local PostgreSQL already uses that port, tunnel to another one and pass --db-port)",
        ])
    rows = db.latest_readings()
    def is_test(r: dict) -> bool:
        return r["device_id"] == test_device or r["zone_id"] == test_zone

    real = [r for r in rows if not is_test(r)]
    details = []
    for r in sorted(rows, key=lambda r: r["age_s"]):
        line = (f"{r['device_id']:<12} zone {r['zone_id']:<8} ts {r['ts'].astimezone(timezone.utc):%Y-%m-%d %H:%M:%S}Z"
                f"  arrived {format_age(r['age_s'])} ago")
        if is_test(r):
            line += "  (test reading, not counted)"
        elif abs(r["skew_s"]) > CLOCK_SKEW_WARN_S:
            line += f"  (ts is {format_age(abs(r['skew_s']))} off the arrival time: device clock?)"
        details.append(line)
    if not real:
        return StepResult(name, FAIL, "no readings from any device", details)
    newest = min(real, key=lambda r: r["age_s"])
    age = f"newest from {newest['device_id']}, {format_age(newest['age_s'])} ago"
    if newest["age_s"] > max_age_min * 60:
        details.append("Is the Pi on and online? `journalctl -u smart-gardening-node` on the Pi; "
                       "plan B: tools/simulator (see docs/runbooks/demo.md).")
        return StepResult(name, FAIL, f"{age} (limit {max_age_min:g} min)", details)
    return StepResult(name, OK, f"{age} (limit {max_age_min:g} min)", details)


def check_ingest_path(publisher, db: Database | None, contract: Contract, device: str, zone: str,
                      timeout: float, now: Callable[[], datetime] = utc_now,
                      waiter: Callable[..., object] | None = None, rng: random.Random | None = None) -> StepResult:
    """c. A test reading published through IoT Core shows up in `readings`."""
    name = "c. Ingest path"
    if db is None:
        return StepResult(name, FAIL, "not run: no database connection to verify it")
    reading = make_test_reading(device, zone, now(), rng or random.Random())
    errors = contract.errors("readings", reading)
    if errors:
        return StepResult(name, FAIL, "test reading breaks the contract (not sent)", errors)
    topic = f"smart-gardening/{device}/readings"
    publisher.publish(topic, json.dumps(reading, separators=(",", ":")).encode())
    ts = parse_utc(reading["ts"])
    started = time.monotonic()
    found = (waiter or wait_for)(lambda: db.reading_exists(device, ts), timeout)
    elapsed = time.monotonic() - started
    detail = [f"published to {topic} with ts {reading['ts']}"]
    if not found:
        detail.append("Check the DLQ (step e) and the ingestion logs: "
                      "`docker compose --env-file /opt/smart-gardening/.env logs --since=10m ingest` on the server.")
        return StepResult(name, FAIL, f"test reading not in the database after {timeout:g} s", detail)
    return StepResult(name, OK, f"test reading stored in {elapsed:.0f} s", detail)


def check_command(publisher, db: Database | None, contract: Contract, device: str, zone: str,
                  timeout: float, now: Callable[[], datetime] = utc_now,
                  waiter: Callable[..., object] | None = None) -> StepResult:
    """d. A 10 s irrigation command produces irrigation_started and irrigation_finished."""
    name = "d. Command"
    if db is None:
        return StepResult(name, FAIL, "not run: no database connection to verify it")
    command = make_command(zone, now())
    errors = contract.errors("commands", command)
    if errors:
        return StepResult(name, FAIL, "command breaks the contract (not sent)", errors)
    topic = f"smart-gardening/{device}/commands"
    publisher.publish(topic, json.dumps(command, separators=(",", ":")).encode())
    command_id = command["command_id"]
    detail = [f"published {COMMAND_DURATION_S} s irrigation of {zone} to {topic}, command_id {command_id}"]

    def done() -> dict | None:
        events = db.events_for(command_id)
        if "command_rejected" in events or "irrigation_finished" in events:
            return events
        return None

    events = (waiter or wait_for)(done, timeout) or db.events_for(command_id)
    if "command_rejected" in events:
        return StepResult(name, FAIL, f"the device rejected the command (reason: {events['command_rejected']['reason']})", detail)
    missing = [t for t in ("irrigation_started", "irrigation_finished") if t not in events]
    if missing:
        detail.append("Is the node running and subscribed to its /commands topic? Its clock must be in sync "
                      "(the command expires in 2 min).")
        return StepResult(name, FAIL, f"missing after {timeout:g} s: {', '.join(missing)}", detail)
    detail.append(f"irrigation_finished reports {events['irrigation_finished']['duration_s']} s of watering")
    return StepResult(name, OK, "irrigation_started and irrigation_finished received", detail)


def queue_depth(sqs, url: str) -> tuple[int, int]:
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]), int(attrs["ApproximateNumberOfMessagesNotVisible"])


def check_queues(sqs, queue_url: str, dlq_url: str, profile: str | None, region: str) -> StepResult:
    """e. Messages waiting in the ingestion queue and in the DLQ; WARN if the DLQ has any."""
    name = "e. Queues"
    q_visible, q_flight = queue_depth(sqs, queue_url)
    d_visible, d_flight = queue_depth(sqs, dlq_url)
    details = [
        f"{queue_url.rsplit('/', 1)[-1]}: {q_visible} waiting, {q_flight} in flight",
        f"{dlq_url.rsplit('/', 1)[-1]}: {d_visible} waiting, {d_flight} in flight",
    ]
    summary = f"ingest {q_visible + q_flight}, DLQ {d_visible + d_flight}"
    if d_visible + d_flight:
        profile_arg = f" --profile {profile}" if profile else ""
        details += [
            "Look at them (see 'Dead-letter queue' in infra/server/README.md to replay or purge):",
            f"  DLQ=$(aws ssm get-parameter{profile_arg} --region {region} --name {DLQ_URL_PARAMETER} "
            "--query Parameter.Value --output text)",
            f"  aws sqs receive-message{profile_arg} --region {region} --queue-url \"$DLQ\" "
            "--max-number-of-messages 10 --message-attribute-names All --visibility-timeout 30 "
            "--query 'Messages[].{error: MessageAttributes.error.StringValue, body: Body}'",
        ]
        return StepResult(name, WARN, f"{summary}: the DLQ is not empty", details)
    return StepResult(name, OK, summary, details)


# --- output -----------------------------------------------------------------------------------

def print_result(result: StepResult, out=None) -> None:
    out = out or sys.stdout
    print(f"[{result.status:^4}] {result.name}: {result.summary}", file=out, flush=True)
    for line in result.details:
        print(f"       {line}", file=out, flush=True)


def exit_code(results: list[StepResult]) -> int:
    return 1 if any(r.status == FAIL for r in results) else 0


def print_summary(results: list[StepResult], out=None) -> None:
    out = out or sys.stdout
    counts = {s: sum(r.status == s for r in results) for s in (OK, WARN, FAIL, SKIP)}
    line = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
    print("\n" + "=" * 60, file=out)
    if counts[FAIL]:
        failed = ", ".join(r.name for r in results if r.status == FAIL)
        print(f"RESULT: FAIL ({line}). Failed: {failed}", file=out)
    elif counts[WARN]:
        print(f"RESULT: OK with warnings ({line})", file=out)
    else:
        print(f"RESULT: OK ({line})", file=out)


# --- main -------------------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE", PROJECT),
                        help="AWS CLI profile (default: $AWS_PROFILE or smart-gardening)")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "eu-south-2"))
    parser.add_argument("--instance-id", help="server instance (default: the running one tagged Name=smart-gardening-server)")
    parser.add_argument("--db-host", default="localhost", help="local end of the SSM tunnel (default: localhost)")
    parser.add_argument("--db-port", type=int, default=DEFAULT_DB_PORT,
                        help=f"local port of the SSM tunnel (default: {DEFAULT_DB_PORT})")
    parser.add_argument("--max-age-min", type=float, default=15,
                        help="FAIL if the newest real reading is older than this (default: 15)")
    parser.add_argument("--timeout", type=float, default=60,
                        help="seconds to wait for the test reading / command events (default: 60)")
    parser.add_argument("--skip-publish", action="store_true", help="read-only: publish nothing (skips c and d)")
    parser.add_argument("--command", action="store_true",
                        help=f"also send a {COMMAND_DURATION_S} s irrigation command and wait for its events (step d)")
    parser.add_argument("--device", default="pi-01", help="device that receives the command (default: pi-01)")
    parser.add_argument("--zone", default="bed-1", help="zone of the command, step d only (default: bed-1)")
    parser.add_argument("--test-device", default=TEST_DEVICE,
                        help=f"device ID of the test reading of step c (default: {TEST_DEVICE})")
    parser.add_argument("--test-zone", default=TEST_ZONE,
                        help=f"zone of the test reading of step c, never a real one (default: {TEST_ZONE})")
    args = parser.parse_args(argv)
    if args.command and args.skip_publish:
        parser.error("--command publishes a command; it cannot be combined with --skip-publish")
    if args.max_age_min <= 0 or args.timeout <= 0:
        parser.error("--max-age-min and --timeout must be positive")
    return args


def run(args: argparse.Namespace, session=None, connect_db: Callable[[str], Database] | None = None,
        make_publisher: Callable[[str], object] | None = None, contract: Contract | None = None) -> list[StepResult]:
    """Run every step and print it as it finishes. The parameters are seams for the tests."""
    if session is None:
        import boto3

        session = boto3.Session(profile_name=args.profile, region_name=args.region)
    ssm = session.client("ssm")
    connect_db = connect_db or (lambda password: Database(args.db_host, args.db_port, password))
    make_publisher = make_publisher or (lambda endpoint: simulate.IotDataPublisher(args.profile, args.region, endpoint))
    contract = contract or Contract()
    results: list[StepResult] = []

    def step(name: str, fn: Callable[[], StepResult]) -> StepResult:
        try:
            result = fn()
        except Exception as exc:  # one broken step must not hide the others
            result = StepResult(name, FAIL, f"error: {type(exc).__name__}: {exc}")
        results.append(result)
        print_result(result)
        return result

    def parameter(name: str, decrypt: bool = False) -> str:
        return ssm.get_parameter(Name=name, WithDecryption=decrypt)["Parameter"]["Value"]

    # a. Server
    instance_id = args.instance_id

    def server() -> StepResult:
        nonlocal instance_id
        result, instance_id = check_server(session.client("ec2"), ssm, args.instance_id)
        return result

    step("a. Server", server)

    # b. Readings (also opens the connection used by c and d)
    db: Database | None = None
    db_error = None
    try:
        db = connect_db(parameter(APP_PASSWORD_PARAMETER, decrypt=True))
    except DatabaseUnavailable as exc:
        db_error = str(exc)
    except Exception as exc:
        db_error = f"{type(exc).__name__}: {exc}"
    def tunnel(port: int) -> str:
        return tunnel_command(args.profile, args.region, instance_id, port)

    step("b. Readings", lambda: check_readings(db, args.max_age_min, args.test_device, db_error,
                                               args.db_port, tunnel, args.test_zone))

    # c, d. Publish
    publisher = None
    if args.skip_publish:
        results.append(StepResult("c. Ingest path", SKIP, "--skip-publish"))
        print_result(results[-1])
    else:
        def ingest_path() -> StepResult:
            nonlocal publisher
            if db is not None:
                publisher = make_publisher(parameter(ENDPOINT_PARAMETER))
            return check_ingest_path(publisher, db, contract, args.test_device, args.test_zone, args.timeout)

        step("c. Ingest path", ingest_path)

    if args.command:
        def command() -> StepResult:
            nonlocal publisher
            if db is not None and publisher is None:
                publisher = make_publisher(parameter(ENDPOINT_PARAMETER))
            return check_command(publisher, db, contract, args.device, args.zone, args.timeout)

        step("d. Command", command)
    else:
        results.append(StepResult("d. Command", SKIP, "not requested (use --command)"))
        print_result(results[-1])

    # e. Queues
    step("e. Queues", lambda: check_queues(session.client("sqs"), parameter(QUEUE_URL_PARAMETER),
                                           parameter(DLQ_URL_PARAMETER), args.profile, args.region))

    if db is not None:
        db.close()
    if publisher is not None:
        publisher.close()
    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"smart-gardening end-to-end check: profile {args.profile}, region {args.region}, "
          f"{utc_now():%Y-%m-%d %H:%M:%S}Z\n", flush=True)
    results = run(args)
    print_summary(results)
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
