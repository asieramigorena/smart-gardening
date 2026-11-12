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

echo "1/4  System packages"
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

echo "2/4  I2C"
if [ ! -e /dev/i2c-1 ]; then
    sudo raspi-config nonint do_i2c 0
    echo "     I2C enabled. Reboot the Pi (sudo reboot) and run ./setup.sh again" >&2
    exit 2
fi
echo "     /dev/i2c-1 available"

echo "3/4  Virtual environment in node/$VENV (sees the system GPIO packages)"
[ -d "$VENV" ] || python3 -m venv --system-site-packages "$VENV"

echo "4/4  Python dependencies"
"$VENV/bin/pip" install --quiet --upgrade pip
# --no-deps: grove.py would pull the classic RPi.GPIO and rpi_ws281x, which do not work
# on the Pi 5 (GPIO is provided by python3-rpi-lgpio, installed with apt).
"$VENV/bin/pip" install --quiet --no-deps -r requirements.txt

echo
echo "Installation complete. Check the sensors with:  ./run.sh -m gardening_node.check_sensors"
