#!/usr/bin/env bash
# Install a systemd timer that runs backup.sh every day at 03:00 UTC (plus up to 30 min of
# random delay). Safe to run again.
#
#   sudo /opt/smart-gardening/repo/infra/server/scripts/install-backup-timer.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

cat >/etc/systemd/system/smart-gardening-backup.service <<'UNIT'
[Unit]
Description=Smart gardening: dump PostgreSQL to S3
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/opt/smart-gardening/repo/infra/server/scripts/backup.sh
UNIT

cat >/etc/systemd/system/smart-gardening-backup.timer <<'UNIT'
[Unit]
Description=Daily smart gardening database backup

[Timer]
OnCalendar=*-*-* 03:00:00 UTC
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now smart-gardening-backup.timer
systemctl list-timers smart-gardening-backup.timer --no-pager
