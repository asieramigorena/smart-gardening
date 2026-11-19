"""Publish fake sensor readings to AWS IoT Core, as a Raspberry Pi would.

Payloads follow docs/contracts/readings.schema.json. Two ways to publish:

  iot-data (default)  HTTPS with your AWS credentials (e.g. --profile smart-gardening).
                      Needs iot:Publish on the readings topic. No certificate.
  mqtt                MQTT over TLS with a device certificate, exactly like the Pi
                      (client ID = device name). Needs paho-mqtt and these variables:
                      IOT_ENDPOINT, IOT_CERT_FILE, IOT_KEY_FILE, IOT_CA_FILE.

Examples:
  python tools/simulator/simulate.py --dry-run --count 3
  python tools/simulator/simulate.py --profile smart-gardening --count 10 --interval 5
  python tools/simulator/simulate.py --profile smart-gardening --invalid --count 1
  python tools/simulator/simulate.py --mode mqtt --device pi-01 --count 0   # until Ctrl+C
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from typing import Callable

ENDPOINT_PARAMETER = "/smart-gardening/iot/endpoint"


# --- payloads ---------------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_reading(device: str, zone: str, ts: datetime, rng: random.Random, calibrated: bool = False) -> dict:
    """A plausible, contract-valid reading. Occasionally a sensor 'fails' and is sent as null."""
    hour = ts.hour + ts.minute / 60
    daylight = max(0.0, math.sin((hour - 6) / 12 * math.pi))  # 0 at night, 1 at 12:00 UTC
    raw = rng.randint(1800, 3000)
    reading = {
        "schema_version": 1,
        "device_id": device,
        "zone": zone,
        "ts": iso(ts),
        "soil_moisture_raw": raw,
        "air_temp": round(8 + 12 * daylight + rng.uniform(-1, 1), 1),
        "air_humidity": round(min(100.0, 85 - 30 * daylight + rng.uniform(-5, 5)), 1),
        "light": round(min(100.0, 100 * daylight + rng.uniform(0, 3)), 1),
    }
    if calibrated:
        # Fake linear calibration: 3000 (dry) -> 0 %, 1500 (wet) -> 100 %.
        reading["soil_moisture"] = round(min(100.0, max(0.0, (3000 - raw) / 15)), 1)
    if rng.random() < 0.05:
        reading[rng.choice(["air_temp", "air_humidity", "light"])] = None
    return reading


# Each one breaks exactly one rule of the contract (like docs/contracts/examples/invalid).
INVALID_MUTATIONS: dict[str, Callable[[dict], None]] = {
    "raw-out-of-range": lambda r: r.update(soil_moisture_raw=5000),
    "missing-zone": lambda r: r.pop("zone"),
    "timestamp-not-utc": lambda r: r.update(ts=r["ts"].replace("Z", "+01:00")),
    "unknown-field": lambda r: r.update(battery=87),
    "wrong-schema-version": lambda r: r.update(schema_version=2),
}


def make_invalid_reading(device: str, zone: str, ts: datetime, rng: random.Random) -> tuple[str, dict]:
    reading = make_reading(device, zone, ts, rng)
    name = rng.choice(sorted(INVALID_MUTATIONS))
    INVALID_MUTATIONS[name](reading)
    return name, reading


# --- publishers -------------------------------------------------------------------------------

class IotDataPublisher:
    def __init__(self, profile: str | None, region: str, endpoint: str | None):
        import boto3

        session = boto3.Session(profile_name=profile, region_name=region)
        if not endpoint:
            endpoint = session.client("ssm").get_parameter(Name=ENDPOINT_PARAMETER)["Parameter"]["Value"]
        self.client = session.client("iot-data", endpoint_url=f"https://{endpoint}")

    def publish(self, topic: str, payload: bytes) -> None:
        self.client.publish(topic=topic, qos=1, payload=payload)

    def close(self) -> None:
        pass


class MqttPublisher:
    def __init__(self, client_id: str):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            sys.exit("--mode mqtt needs paho-mqtt: pip install -r tools/simulator/requirements.txt")

        env = {name: os.environ.get(name) for name in ("IOT_ENDPOINT", "IOT_CERT_FILE", "IOT_KEY_FILE", "IOT_CA_FILE")}
        missing = [name for name, value in env.items() if not value]
        if missing:
            sys.exit(f"--mode mqtt needs these environment variables: {', '.join(missing)}")

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.client.tls_set(ca_certs=env["IOT_CA_FILE"], certfile=env["IOT_CERT_FILE"], keyfile=env["IOT_KEY_FILE"])
        self.client.connect(env["IOT_ENDPOINT"], 8883)
        self.client.loop_start()

    def publish(self, topic: str, payload: bytes) -> None:
        info = self.client.publish(topic, payload, qos=1)
        info.wait_for_publish(timeout=10)
        if not info.is_published():
            raise RuntimeError("PUBACK not received (check the certificate, client ID and policy)")

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


class DryRunPublisher:
    def publish(self, topic: str, payload: bytes) -> None:
        pass

    def close(self) -> None:
        pass


# --- main -------------------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["iot-data", "mqtt"], default="iot-data")
    parser.add_argument("--device", default="pi-01", help="device (thing) name, used in the topic (default: pi-01)")
    parser.add_argument("--zone", default="bed-1", help="zone key (default: bed-1)")
    parser.add_argument("--count", type=int, default=1, help="messages to send; 0 = until Ctrl+C (default: 1)")
    parser.add_argument("--interval", type=float, default=60, help="seconds between messages (default: 60)")
    parser.add_argument("--invalid", action="store_true", help="send contract-breaking readings (they should end in the DLQ)")
    parser.add_argument("--calibrated", action="store_true", help="also send soil_moisture (%%)")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"), help="AWS CLI profile (iot-data mode)")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "eu-south-2"))
    parser.add_argument("--endpoint", default=os.environ.get("IOT_ENDPOINT"),
                        help=f"IoT data endpoint (iot-data mode; default: SSM {ENDPOINT_PARAMETER})")
    parser.add_argument("--seed", type=int, help="random seed, for repeatable payloads")
    parser.add_argument("--dry-run", action="store_true", help="print the payloads without publishing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    rng = random.Random(args.seed)
    topic = f"smart-gardening/{args.device}/readings"

    if args.dry_run:
        publisher = DryRunPublisher()
    elif args.mode == "mqtt":
        publisher = MqttPublisher(client_id=args.device)
    else:
        publisher = IotDataPublisher(args.profile, args.region, args.endpoint)

    sent = 0
    try:
        while args.count == 0 or sent < args.count:
            if sent:
                time.sleep(args.interval)
            if args.invalid:
                label, payload = make_invalid_reading(args.device, args.zone, utc_now(), rng)
                label = f"invalid:{label}"
            else:
                label, payload = "valid", make_reading(args.device, args.zone, utc_now(), rng, args.calibrated)
            body = json.dumps(payload, separators=(",", ":"))
            publisher.publish(topic, body.encode())
            sent += 1
            print(f"{'(dry run) ' if args.dry_run else ''}{label} -> {topic}: {body}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        publisher.close()
    print(f"sent {sent} message(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
