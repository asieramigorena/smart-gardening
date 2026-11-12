"""Quick check of the node's sensors.

Reads each sensor several times and prints the result. Each sensor is checked
separately: if one fails, the others are still read.

Usage (from node/):
    ./run.sh -m gardening_node.check_sensors            # 5 readings, 1 s apart
    ./run.sh -m gardening_node.check_sensors -n 10      # 10 readings
    ./run.sh -m gardening_node.check_sensors --led      # also blink the LED
"""
import argparse
import time

from . import sensors

SENSORS = {
    "moisture (A0, raw 0-4095)": sensors.read_moisture,
    "light (A2, %)": sensors.read_light,
    "temperature/humidity (D5)": sensors.read_temperature_humidity,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", "--samples", type=int, default=5)
    parser.add_argument("-i", "--interval", type=float, default=1.0)
    parser.add_argument("--led", action="store_true", help="blink the LED")
    args = parser.parse_args()

    results = {name: [] for name in SENSORS}
    errors = {}

    for i in range(args.samples):
        for name, read in SENSORS.items():
            if name in errors:
                continue
            try:
                results[name].append(read())
            except Exception as exc:  # a broken sensor must not stop the others
                errors[name] = exc
        if i < args.samples - 1:
            time.sleep(args.interval)

    print(f"{'sensor':32} {'status':8} readings")
    for name in SENSORS:
        if name in errors:
            print(f"{name:32} {'FAIL':8} {type(errors[name]).__name__}: {errors[name]}")
        else:
            print(f"{name:32} {'OK':8} {results[name]}")

    if args.led:
        print("LED (D16): blinking...")
        sensors.led_blink()

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
