"""Grove sensor readings for the node (Raspberry Pi 5 + Grove Base Hat).

This is the only module that knows the wiring. The rest of the code (checks,
MQTT publishing) just calls read_all(), as in the Lab 5 sensors.py.

Wiring:
    Soil moisture            A0   analog
    Ambient light            A2   analog
    Temperature / humidity   D5   digital (BCM 5), DHT11
    LED (actuator)           D16  digital (BCM 16): irrigation indicator (not used yet)
"""
import os

MOISTURE_CHANNEL = 0
LIGHT_CHANNEL = 2
LED_PIN = 16
DHT_PIN = 5  # DHT11, the same sensor on every node
# Newer hats (MM32 chip) answer on 0x08; older ones (STM32) on 0x04.
ADC_ADDRESS = int(os.environ.get("GROVE_ADC_ADDRESS", "0x08"), 0)

_adc = None


def _get_adc():
    global _adc
    if _adc is None:
        # Imported here (like GroveLed below) so the module loads without the hat
        from grove.adc import ADC

        _adc = ADC(address=ADC_ADDRESS)
    return _adc


def read_moisture():
    """Soil moisture as the raw ADC value (0-4095).

    Uncalibrated: higher = wetter. Dry/wet thresholds will be set from real
    measurements.
    """
    return _get_adc().read_raw(MOISTURE_CHANNEL)


def read_light():
    """Ambient light as a percentage of the sensor range (0-100)."""
    return round(_get_adc().read(LIGHT_CHANNEL) / 10, 1)


def read_temperature_humidity():
    """Air temperature (°C) and relative humidity (%).

    DHT11 on D5 (BCM 5). Returns a (temperature, humidity) tuple.
    """
    from . import dht

    return dht.read(DHT_PIN)


# Fields of one reading (names of docs/contracts readings) and the function that
# measures them (one value per field). soil_moisture (calibrated %) is not sent
# until the sensor is calibrated.
READERS = {
    ("soil_moisture_raw",): read_moisture,
    ("light",): read_light,
    ("air_temp", "air_humidity"): read_temperature_humidity,
}


def read_all(errors=None):
    """One reading: a FLAT dict of numbers (no strings, no nesting), as in Lab 5.

    A sensor that fails gives None in its fields instead of losing the whole
    reading. If `errors` is a dict, the failures are added to it as
    {"field[,field]": "Error: message"}.
    """
    reading = {}
    for fields, read in READERS.items():
        try:
            result = read()
            values = (result,) if len(fields) == 1 else tuple(result)
            if len(values) != len(fields):
                raise ValueError(f"expected {len(fields)} values, got {len(values)}")
        except Exception as exc:  # a broken sensor must not stop the others
            values = (None,) * len(fields)
            if errors is not None:
                errors[",".join(fields)] = f"{type(exc).__name__}: {exc}"
        reading.update(zip(fields, values))
    return reading


def led_blink(times=3, interval=0.4):
    """Blink the LED to check the actuator."""
    import time

    from grove.grove_led import GroveLed

    led = GroveLed(LED_PIN)
    for _ in range(times):
        led.on()
        time.sleep(interval)
        led.off()
        time.sleep(interval)
