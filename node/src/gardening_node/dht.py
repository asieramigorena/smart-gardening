"""DHT11 / DHT22 reader (Grove Temperature & Humidity) for the Raspberry Pi 5.

One-wire protocol: the host pulls the line low for >18 ms and releases it; the
sensor answers with 40 bits encoded in the length of the high pulse (~27 µs = 0,
~70 µs = 1). Edges are timestamped with lgpio (nanosecond resolution), because
polling the pin from Python is not precise enough.

The model is detected automatically: the DHT22 sends tenths in 16 bits; the
DHT11 sends the integer value in the first byte and 0 in the second.
"""
import time

import lgpio

_RP1_LABEL = "pinctrl-rp1"
_BIT_THRESHOLD_NS = 50_000  # high pulse > 50 µs -> bit 1


class DHTError(Exception):
    pass


def _open_gpio_chip():
    """On the Pi 5 the 40-pin header lives on the pinctrl-rp1 chip (its number
    changes with the kernel), so it is looked up by label."""
    for number in range(16):
        try:
            handle = lgpio.gpiochip_open(number)
        except Exception:
            continue
        if lgpio.gpio_get_chip_info(handle)[3] == _RP1_LABEL:
            return handle
        lgpio.gpiochip_close(handle)
    raise DHTError("Pi 5 GPIO chip (pinctrl-rp1) not found")


def _capture_edges(pin):
    """Trigger a reading and return the list of edges (level, timestamp_ns)."""
    handle = _open_gpio_chip()
    edges = []
    try:
        lgpio.gpio_claim_output(handle, pin, 1)
        time.sleep(0.05)
        lgpio.gpio_write(handle, pin, 0)
        time.sleep(0.02)  # >18 ms for the DHT11
        lgpio.gpio_free(handle, pin)
        lgpio.gpio_claim_alert(handle, pin, lgpio.BOTH_EDGES, lgpio.SET_PULL_UP)
        callback = lgpio.callback(
            handle, pin, lgpio.BOTH_EDGES,
            lambda chip, gpio, level, tick: edges.append((level, tick)),
        )
        time.sleep(0.1)
        callback.cancel()
    finally:
        try:
            lgpio.gpio_free(handle, pin)
        except Exception:
            pass
        lgpio.gpiochip_close(handle)
    return edges


def _decode(edges):
    # Length of each high pulse (rising -> falling edge). The last 40 are data;
    # the high pulse of the initial response comes before them.
    highs = []
    rise = None
    for level, tick in edges:
        if level == 1:
            rise = tick
        elif level == 0 and rise is not None:
            highs.append(tick - rise)
            rise = None
    if len(highs) < 40:
        raise DHTError(f"incomplete response ({len(highs)} pulses, expected 40)")
    bits = [1 if width > _BIT_THRESHOLD_NS else 0 for width in highs[-40:]]
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, 40, 8)]
    if (sum(data[:4]) & 0xFF) != data[4]:
        raise DHTError(f"bad checksum {data}")
    return data


def _convert(data):
    """Return (temperature °C, humidity %), interpreting the sensor model."""
    humidity = ((data[0] << 8) | data[1]) / 10
    temperature = (((data[2] & 0x7F) << 8) | data[3]) / 10
    if data[2] & 0x80:
        temperature = -temperature
    if 0 <= humidity <= 100 and -40 <= temperature <= 80:
        return temperature, humidity  # DHT22
    return float(data[2]), float(data[0])  # DHT11


def read(pin, retries=5):
    """Read (temperature °C, humidity %) from the DHT on the given BCM pin.

    DHT sensors occasionally fail on timing, so reads are retried. Leave >=2 s
    between readings (the DHT22 does not accept faster).
    """
    last_error = None
    for attempt in range(retries):
        if attempt:
            time.sleep(2)
        try:
            return _convert(_decode(_capture_edges(pin)))
        except DHTError as exc:
            last_error = exc
    raise DHTError(f"{retries} failed attempts; last: {last_error}")
