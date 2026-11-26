"""DHT11 reader (Grove Temperature & Humidity, blue) for the Raspberry Pi 5.

One-wire protocol: the host pulls the line low for >18 ms and releases it; the
sensor answers with 40 bits encoded in the length of the high pulse (~27 µs = 0,
~70 µs = 1). Edges are timestamped with lgpio (nanosecond resolution), because
polling the pin from Python is not precise enough.
"""
import time

_RP1_LABEL = "pinctrl-rp1"
_BIT_THRESHOLD_NS = 50_000  # high pulse > 50 µs -> bit 1


class DHTError(Exception):
    pass


def _open_gpio_chip():
    """On the Pi 5 the 40-pin header lives on the pinctrl-rp1 chip (its number
    changes with the kernel), so it is looked up by label."""
    import lgpio  # only on the Pi; imported here so decoding works anywhere

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
    import lgpio

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


def frame_from_edges(edges):
    """The 5 bytes of a frame (4 data + checksum) from the captured edges."""
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
    frame = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, 40, 8)]
    if (sum(frame[:4]) & 0xFF) != frame[4]:
        raise DHTError(f"bad checksum {frame}")
    return frame


def decode(frame):
    """(temperature °C, humidity %) from a DHT11 frame."""
    # Integer part + tenths (always 0 on older DHT11); bit 7 of the temperature
    # tenths byte means below zero.
    humidity = frame[0] + frame[1] / 10
    temperature = frame[2] + (frame[3] & 0x7F) / 10
    if frame[3] & 0x80:
        temperature = -temperature
    # A bit error can pass the 8-bit checksum; out-of-range values must not be published
    if not (0 <= humidity <= 100 and -20 <= temperature <= 60):
        raise DHTError(f"implausible reading: {temperature} °C, {humidity} % ({frame})")
    return round(temperature, 1), round(humidity, 1)


def read(pin, retries=5):
    """Read (temperature °C, humidity %) from the DHT11 on the given BCM pin.

    The DHT11 occasionally fails on timing, so reads are retried (it needs >=1 s
    between readings).
    """
    last_error = None
    for attempt in range(retries):
        if attempt:
            time.sleep(2)
        try:
            return decode(frame_from_edges(_capture_edges(pin)))
        except DHTError as exc:
            last_error = exc
    raise DHTError(f"{retries} failed attempts; last: {last_error}")
