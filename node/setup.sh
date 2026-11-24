#!/bin/bash
# Installs the node on a Raspberry Pi 5 with a Grove Base Hat.
# Idempotent: safe to re-run. Do not run with sudo (it asks for it when needed).
#
#   git clone https://github.com/asieramigorena/smart-gardening.git
#   cd smart-gardening/node && ./setup.sh
set -euo pipefail

cd "$(dirname "$0")"
VENV=".venv"
APT_PKGS=(i2c-tools python3-venv python3-lgpio python3-rpi-lgpio python3-smbus2 python3-numpy)

if [ "$(id -u)" -eq 0 ]; then
    echo "Do not run this script with sudo." >&2
    exit 1
fi

echo "1/5  System packages"
missing=()
for p in "${APT_PKGS[@]}"; do
    dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
done
if [ "${#missing[@]}" -gt 0 ]; then
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
else
    echo "     already installed"
fi

echo "2/5  I2C"
if [ ! -e /dev/i2c-1 ]; then
    sudo raspi-config nonint do_i2c 0
    echo "     I2C enabled. Reboot the Pi (sudo reboot) and run ./setup.sh again" >&2
    exit 2
fi
echo "     /dev/i2c-1 available"

echo "3/5  Virtual environment in node/$VENV (sees the system GPIO packages)"
[ -d "$VENV" ] || python3 -m venv --system-site-packages "$VENV"

echo "4/5  Python dependencies and the node package (editable, from src/)"
"$VENV/bin/pip" install --quiet --upgrade pip
# --no-deps: grove.py would pull the classic RPi.GPIO and rpi_ws281x, which do not work
# on the Pi 5 (GPIO is provided by python3-rpi-lgpio, installed with apt).
"$VENV/bin/pip" install --quiet --no-deps -r requirements.txt
"$VENV/bin/pip" install --quiet --no-deps -e .

echo "5/5  Node service (hourly readings with APScheduler)"
CONFIG_DIR="${SMART_GARDENING_CONFIG_DIR:-/etc/smart-gardening}"
UNIT=smart-gardening-node
if [ ! -f "$CONFIG_DIR/node.env" ]; then
    echo "     node not provisioned yet: run provision.sh from an operator machine, then ./setup.sh again"
else
    # Same unit as the Lab 5 publisher (lab5-publisher.service), filled in for this node.
    # Exit code 78 = config error: restarting would not fix it, so systemd does not retry.
    SERVICE="[Unit]
Description=Smart gardening node: hourly sensor readings to AWS IoT Core
After=network-online.target
Wants=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=$PWD
Environment=PYTHONUNBUFFERED=1
Environment=SMART_GARDENING_CONFIG_DIR=$CONFIG_DIR
ExecStart=$PWD/$VENV/bin/python -m gardening_node.scheduler
Restart=on-failure
RestartSec=10
RestartPreventExitStatus=78

[Install]
WantedBy=multi-user.target
"
    if ! printf '%s' "$SERVICE" | cmp -s - "/etc/systemd/system/$UNIT.service"; then
        printf '%s' "$SERVICE" | sudo tee "/etc/systemd/system/$UNIT.service" >/dev/null
        sudo systemctl daemon-reload
    fi
    if systemctl is-active --quiet "$UNIT"; then
        sudo systemctl restart "$UNIT"   # pick up code updated with git pull
    else
        sudo systemctl enable --now "$UNIT"
    fi
    echo "     $UNIT running (logs: journalctl -u $UNIT -f)"
fi

echo
echo "Installation complete. Check the sensors with:  ./run.sh -m gardening_node.check_sensors"
