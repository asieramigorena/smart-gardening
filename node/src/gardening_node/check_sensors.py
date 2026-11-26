"""Quick check of the node's sensors.

Takes several readings with sensors.read_all() (the same reading the node
publishes) and prints the result per sensor. A failing sensor does not stop
the others.

Usage (from node/):
    ./run.sh -m gardening_node.check_sensors            # 5 readings, 1 s apart
    ./run.sh -m gardening_node.check_sensors -n 10      # 10 readings
    ./run.sh -m gardening_node.check_sensors --led      # also blink the LED
"""
import argparse
import time

from . import sensors


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", "--samples", type=int, default=5)
    parser.add_argument("-i", "--interval", type=float, default=1.0)
    parser.add_argument(
        "--adc-address",
        type=lambda s: int(s, 0),
        default=sensors.DEFAULT_ADC_ADDRESS,
        help="I2C address of the Grove hat ADC (default: 0x08)",
    )
    parser.add_argument("--led", action="store_true", help="blink the LED")
    args = parser.parse_args()

    readings = []
    errors = {}
    for i in range(args.samples):
        readings.append(sensors.read_all(errors, adc_address=args.adc_address))
        if i < args.samples - 1:
            time.sleep(args.interval)

    print(f"{'sensor':32} {'status':8} readings")
    for fields in sensors.READERS:
        name = ",".join(fields)
        if name in errors:
            print(f"{name:32} {'FAIL':8} {errors[name]}")
        else:
            values = [r[fields[0]] if len(fields) == 1 else tuple(r[f] for f in fields) for r in readings]
            print(f"{name:32} {'OK':8} {values}")

    if args.led:
        print("LED (D16): blinking...")
        sensors.led_blink()

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
