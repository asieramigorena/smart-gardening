# Smart Gardening — Raspberry Pi Node

Field node service running on a Raspberry Pi 5 with a Grove Base Hat. It reads soil moisture, ambient light, and air temperature/humidity periodically and publishes JSON readings to AWS IoT Core over MQTT/TLS (port 8883, mutual X.509 authentication) following [`docs/contracts/`](../docs/contracts/README.md).

## Hardware wiring

| Sensor / Actuator | Grove port | Signal / Pin | Notes |
|---|---|---|---|
| Soil moisture | `A0` | Analog (12-bit ADC) | Raw ADC value (`0–4095`); optionally calibrated to `%` |
| Ambient light | `A2` | Analog (12-bit ADC) | Percentage (`0–100`) |
| Temperature & humidity (DHT11) | `D5` | Digital (`BCM 5`) | Read via `lgpio` edge timing on `pinctrl-rp1` |
| Irrigation indicator LED | `D16` | Digital (`BCM 16`) | Checked with `check_sensors --led` |

Analog ports (`A0`, `A2`) are read over I2C from the Grove Base Hat (`0x08` on newer MM32 hats, `0x04` on older STM32 hats).

## Installation and provisioning

1. **Create the device in AWS** (from `infra/`): add the device ID to `var.devices` and apply Terraform, which creates the IoT thing, X.509 certificate, and SSM parameters. Ensure the target `zone` already exists in PostgreSQL (see [`DB/README.md`](../DB/README.md); readings for unknown zones are rejected to the DLQ).

2. **Install system dependencies and virtualenv on the Raspberry Pi** (do not run with `sudo`; it prompts when needed):

   ```bash
   git clone https://github.com/asieramigorena/smart-gardening.git
   cd smart-gardening/node
   ./setup.sh
   ```

   If I2C was disabled, `setup.sh` enables it and asks for a reboot (`sudo reboot`); run `./setup.sh` again afterwards.

3. **Provision credentials from an operator machine** (with the `smart-gardening` AWS CLI profile):

   ```bash
   ./provision.sh <ssh-host> <device-id> <zone>
   ```

   This fetches the device certificate, private key, IoT endpoint, and Amazon Root CA from SSM/Amazon Trust and writes `/etc/smart-gardening/node.env` and `/etc/smart-gardening/certs/` on the Pi, owned by the Pi user.

4. **Install and start the systemd service on the Raspberry Pi**:

   ```bash
   cd ~/smart-gardening/node
   ./setup.sh
   ```

   Verify logs with:

   ```bash
   journalctl -u smart-gardening-node -f
   ```

## Configuration (`/etc/smart-gardening/node.env`)

See [`node.env.example`](node.env.example) for a template. Environment variables with the same names override values in `node.env`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `IOT_ENDPOINT` | Yes | — | AWS IoT Core `iot:Data-ATS` endpoint |
| `THING_NAME` | Yes | — | IoT thing name / MQTT client ID (`^[A-Za-z0-9_-]{1,64}$`) |
| `ZONE` | Yes | — | Irrigation zone (`^[a-z0-9][a-z0-9_-]{0,31}$`) |
| `IOT_CERT` | No | `certs/<THING_NAME>.cert.pem` | Device certificate path (relative to config dir or absolute) |
| `IOT_KEY` | No | `certs/<THING_NAME>.private.key` | Device private key path |
| `IOT_ROOT_CA` | No | `certs/AmazonRootCA1.pem` | Root CA certificate path |
| `READ_INTERVAL_S` | No | `300` | Reading interval in seconds (minimum `10`) |
| `GROVE_ADC_ADDRESS` | No | `0x08` | Hat I2C ADC address (`0x03–0x77`; `0x04` for older STM32 hats) |
| `SOIL_RAW_DRY` | No | unset | Raw ADC value when dry (`0–4095`); must be set together with `SOIL_RAW_WET` |
| `SOIL_RAW_WET` | No | unset | Raw ADC value when wet (`0–4095`); must differ from `SOIL_RAW_DRY` |

Set `SMART_GARDENING_CONFIG_DIR` (default `/etc/smart-gardening`) to load configuration and certificates from a different directory during local testing.

### Soil moisture calibration

While `SOIL_RAW_DRY` and `SOIL_RAW_WET` are unset, the node sends `soil_moisture_raw` only (the contract omits `soil_moisture` until calibrated). To enable calibrated `soil_moisture` (`0.0–100.0 %`):

1. Keep the sensor probes clean and dry in the air, and read the raw ADC value with `--dry-run`:

   ```bash
   ./run.sh -m gardening_node.scheduler --dry-run
   ```

   Record `"soil_moisture_raw"` as `SOIL_RAW_DRY`.

2. Submerge only the metal probes in a glass of water (keep the upper PCB and connector dry) and run `--dry-run` again. Record `"soil_moisture_raw"` as `SOIL_RAW_WET`.

3. Add both values to `/etc/smart-gardening/node.env` and restart the service:

   ```bash
   sudo systemctl restart smart-gardening-node
   ```

   The calculation handles both sensor polarities (raw increasing or decreasing when wet) and clamps the result to `0.0–100.0`.

## Running modes

Run commands from `node/` using `./run.sh` (which uses `node/.venv`):

- **Systemd service** (production): runs `gardening_node.scheduler` continuously, takes one reading at startup and then every `READ_INTERVAL_S` seconds (with bounded jitter).
- **Single reading**:

  ```bash
  ./run.sh -m gardening_node.scheduler --once
  ```

- **Dry run** (reads sensors, builds and validates the contract JSON, no network; works even before provisioning using `device_id="unprovisioned"`):

  ```bash
  ./run.sh -m gardening_node.scheduler --dry-run
  ```

- **Sensor and LED hardware check**:

  ```bash
  ./run.sh -m gardening_node.check_sensors -n 5 --led
  ./run.sh -m gardening_node.check_sensors --adc-address 0x04
  ```

## Updating a node

```bash
cd ~/smart-gardening
git pull
cd node && ./setup.sh
```

`setup.sh` updates dependencies if needed, refreshes the systemd unit file if changed, and restarts `smart-gardening-node`.

## Offline behavior

Readings are published with MQTT **QoS 1**. If the network or broker is unreachable, `paho-mqtt` buffers up to `168` messages in memory and delivers them automatically on reconnect. Buffered messages are lost if the service process restarts while offline.

## Troubleshooting

- **Service stops immediately with exit code `78`**: configuration, schema, or certificate file error (`RestartPreventExitStatus=78` prevents restart loops). Check `journalctl -u smart-gardening-node -n 50` and verify `/etc/smart-gardening/node.env` and file permissions.
- **All analog readings (`soil_moisture_raw`, `light`) are `null`**: check `i2cdetect -y 1`. If the Grove hat appears at `0x04` instead of `0x08`, set `GROVE_ADC_ADDRESS=0x04` in `/etc/smart-gardening/node.env` and restart.
- **Repeated disconnects after connecting**: AWS IoT Core closes the TLS connection when the client ID (`THING_NAME`) does not match an attached active certificate or violates the `smart-gardening-device` IoT policy. Also verify `certs/` files are readable by the service user.
- **Clock warnings or readings skipped**: `smart-gardening-node` checks `timedatectl show -p NTPSynchronized --value` and refuses to publish if the UTC year is before `2026` to avoid corrupting deduplication timestamps. Check `timedatectl status` and network access to NTP.
- **View live logs**:

  ```bash
  journalctl -u smart-gardening-node -f
  ```

## Unit tests (without hardware)

Hardware libraries (`grove.py`, `lgpio`) are imported lazily inside sensor functions, so unit tests run on any machine with the Python dependencies installed:

```bash
PYTHONPATH=node/src python -m unittest discover -s node/tests -t node -v
```
