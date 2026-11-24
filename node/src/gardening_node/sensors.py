"""Grove sensor readings for the node (Raspberry Pi 5 + Grove Base Hat).

This is the only module that knows the wiring. The rest of the code (checks,
MQTT publishing) just calls the functions defined here.

Wiring:
    Soil moisture            A0   analog
    Ambient light            A2   analog
    Temperature / humidity   D5   digital (BCM 5), DHT11/DHT22
    LED (actuator)           D16  digital (BCM 16)
"""
import os

from grove.adc import ADC

MOISTURE_CHANNEL = 0
LIGHT_CHANNEL = 2
LED_PIN = 16
DHT_PIN = 5
# Newer hats (MM32 chip) answer on 0x08; older ones (STM32) on 0x04.
ADC_ADDRESS = int(os.environ.get("GROVE_ADC_ADDRESS", "0x08"), 0)

_adc = None


def _get_adc():
    global _adc
    if _adc is None:
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

    DHT11/DHT22 on D5 (BCM 5). Returns a (temperature, humidity) tuple.
    """
    from . import dht

    return dht.read(DHT_PIN)


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
