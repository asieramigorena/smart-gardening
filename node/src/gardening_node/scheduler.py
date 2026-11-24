"""Node service: one reading every hour with APScheduler (--once, --dry-run)."""
import argparse
import signal
import socket
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from . import contract, sensors
from .config import ConfigError, load_config
from .publisher import Publisher

# The service must not be restarted in a loop for a config error (see setup.sh)
EX_CONFIG = 78
JITTER_S = 60  # spread the nodes over a minute instead of all connecting at :00


def take_reading(device_id, zone):
    errors = {}
    values = sensors.read_all(errors)
    for fields, error in errors.items():
        print(f"sensor {fields} failed: {error}", file=sys.stderr)
    return contract.build_reading(device_id, zone, datetime.now(timezone.utc), values)


def publish_reading(publisher, validator):
    cfg = publisher.cfg
    body = take_reading(cfg.thing_name, cfg.zone)
    try:
        payload = contract.encode(body, validator)
    except contract.ContractError as exc:  # rule 7: never send a message that breaks the contract
        print(f"reading not sent, it breaks the contract: {exc}", file=sys.stderr)
        return False
    if publisher.send(payload):
        print("sent", cfg.topic, payload.decode())
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="publish one reading and exit")
    mode.add_argument("--dry-run", action="store_true", help="print one reading, no network")
    args = parser.parse_args()

    try:
        validator = contract.readings_validator()
        cfg = load_config()
    except (contract.ContractError, ConfigError) as exc:
        if not (args.dry_run and isinstance(exc, ConfigError)):
            print(exc, file=sys.stderr)
            return EX_CONFIG
        cfg = None

    if args.dry_run:
        device_id, zone = (cfg.thing_name, cfg.zone) if cfg else (socket.gethostname(), "unprovisioned")
        body = take_reading(device_id, zone)
        print(f"topic: {cfg.topic if cfg else '(node not provisioned)'}")
        try:
            print(contract.encode(body, validator).decode())
            print("valid against docs/contracts/readings.schema.json")
        except contract.ContractError as exc:
            print(body)
            print(f"INVALID: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        publisher = Publisher(cfg)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return EX_CONFIG
    publisher.start()

    if args.once:
        try:
            ok = publish_reading(publisher, validator)
            if not ok:
                print("reading not delivered", file=sys.stderr)
            return 0 if ok else 1
        finally:
            publisher.stop()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        publish_reading, "cron", args=[publisher, validator], minute=0, jitter=JITTER_S,
        next_run_time=datetime.now(),  # also once at start (publisher.start() already waited for the connection)
        coalesce=True, misfire_grace_time=600, max_instances=1,
    )
    # systemd stops the service with SIGTERM: leave through a clean disconnect
    signal.signal(signal.SIGTERM, lambda *_: scheduler.shutdown(wait=False))
    print(f"publishing every hour to {cfg.topic}")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        pass
    finally:
        publisher.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
