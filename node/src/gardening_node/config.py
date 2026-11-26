"""Node config: /etc/smart-gardening/node.env and certs/, written by provision.sh."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_CONFIG_DIR = "/etc/smart-gardening"
DEFAULT_READ_INTERVAL_S = 300
MIN_READ_INTERVAL_S = 10
DEFAULT_ADC_ADDRESS = 0x08
MIN_ADC_ADDRESS = 0x03
MAX_ADC_ADDRESS = 0x77
MIN_SOIL_RAW = 0
MAX_SOIL_RAW = 4095
REQUIRED = ("IOT_ENDPOINT", "THING_NAME", "ZONE")
OPTIONAL_PATHS = ("IOT_CERT", "IOT_KEY", "IOT_ROOT_CA")
OPTIONAL_SETTINGS = ("READ_INTERVAL_S", "GROVE_ADC_ADDRESS", "SOIL_RAW_DRY", "SOIL_RAW_WET")
# Patterns of docs/contracts/README.md ("Common fields")
_PATTERNS = {
    "THING_NAME": re.compile(r"[A-Za-z0-9_-]{1,64}"),  # device_id
    "ZONE": re.compile(r"[a-z0-9][a-z0-9_-]{0,31}"),
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class NodeConfig:
    endpoint: str
    thing_name: str
    zone: str
    cert: Path
    key: Path
    root_ca: Path
    read_interval_s: int = DEFAULT_READ_INTERVAL_S
    adc_address: int = DEFAULT_ADC_ADDRESS
    soil_raw_dry: int | None = None
    soil_raw_wet: int | None = None

    @property
    def topic(self):
        """Readings topic of the contract: smart-gardening/{device_id}/readings."""
        return f"smart-gardening/{self.thing_name}/readings"


def _parse_read_interval(raw):
    if not raw:
        return DEFAULT_READ_INTERVAL_S
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"READ_INTERVAL_S={raw!r} must be an integer") from exc
    if value < MIN_READ_INTERVAL_S:
        raise ConfigError(f"READ_INTERVAL_S={value} must be at least {MIN_READ_INTERVAL_S}")
    return value


def _parse_adc_address(raw):
    if not raw:
        return DEFAULT_ADC_ADDRESS
    try:
        value = int(raw, 0)
    except ValueError as exc:
        raise ConfigError(f"GROVE_ADC_ADDRESS={raw!r} must be an integer (e.g. 0x08)") from exc
    if not (MIN_ADC_ADDRESS <= value <= MAX_ADC_ADDRESS):
        raise ConfigError(
            f"GROVE_ADC_ADDRESS={hex(value)} must be between {hex(MIN_ADC_ADDRESS)} and {hex(MAX_ADC_ADDRESS)}"
        )
    return value


def _parse_soil_calibration(raw_dry, raw_wet):
    if not raw_dry and not raw_wet:
        return None, None
    if not raw_dry or not raw_wet:
        raise ConfigError("SOIL_RAW_DRY and SOIL_RAW_WET must both be set")
    parsed = {}
    for name, raw in (("SOIL_RAW_DRY", raw_dry), ("SOIL_RAW_WET", raw_wet)):
        try:
            value = int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name}={raw!r} must be an integer") from exc
        if not (MIN_SOIL_RAW <= value <= MAX_SOIL_RAW):
            raise ConfigError(f"{name}={value} must be between {MIN_SOIL_RAW} and {MAX_SOIL_RAW}")
        parsed[name] = value
    dry, wet = parsed["SOIL_RAW_DRY"], parsed["SOIL_RAW_WET"]
    if dry == wet:
        raise ConfigError("SOIL_RAW_DRY and SOIL_RAW_WET must differ")
    return dry, wet


def config_dir():
    return Path(os.environ.get("SMART_GARDENING_CONFIG_DIR", DEFAULT_CONFIG_DIR)).expanduser()


def load_config(directory=None, environ=None):
    """Build the NodeConfig from node.env plus environment overrides."""
    directory = Path(directory).expanduser() if directory else config_dir()
    environ = os.environ if environ is None else environ
    env_file = directory / "node.env"

    values = dict(dotenv_values(env_file))
    values.update(
        {k: environ[k] for k in (*REQUIRED, *OPTIONAL_PATHS, *OPTIONAL_SETTINGS) if environ.get(k)}
    )

    missing = [k for k in REQUIRED if not values.get(k)]
    if missing:
        raise ConfigError(
            f"missing {', '.join(missing)} (looked in {env_file} and the environment); "
            "provision the node first"
        )
    for key, pattern in _PATTERNS.items():
        if not pattern.fullmatch(values[key]):
            raise ConfigError(f"{key}={values[key]!r} does not match {pattern.pattern} (docs/contracts)")

    read_interval_s = _parse_read_interval(values.get("READ_INTERVAL_S"))
    adc_address = _parse_adc_address(values.get("GROVE_ADC_ADDRESS"))
    soil_raw_dry, soil_raw_wet = _parse_soil_calibration(
        values.get("SOIL_RAW_DRY"), values.get("SOIL_RAW_WET")
    )

    thing = values["THING_NAME"]
    defaults = {
        "IOT_CERT": f"certs/{thing}.cert.pem",
        "IOT_KEY": f"certs/{thing}.private.key",
        "IOT_ROOT_CA": "certs/AmazonRootCA1.pem",
    }
    paths = {k: Path(values.get(k) or directory / defaults[k]).expanduser() for k in OPTIONAL_PATHS}
    return NodeConfig(
        endpoint=values["IOT_ENDPOINT"],
        thing_name=thing,
        zone=values["ZONE"],
        cert=paths["IOT_CERT"],
        key=paths["IOT_KEY"],
        root_ca=paths["IOT_ROOT_CA"],
        read_interval_s=read_interval_s,
        adc_address=adc_address,
        soil_raw_dry=soil_raw_dry,
        soil_raw_wet=soil_raw_wet,
    )
