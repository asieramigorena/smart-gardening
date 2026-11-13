#!/bin/bash
# Runs node code with the project's virtual environment, regardless of where it
# is called from (cron, systemd or a terminal).
#   ./run.sh -m gardening_node.check_sensors -n 5 --led
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
[ -x .venv/bin/python ] || { echo "Environment missing: run ./setup.sh" >&2; exit 1; }
exec .venv/bin/python -u "$@"
