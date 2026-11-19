#!/usr/bin/env bash
# Dump the database (pg_dump custom format, compressed) and upload it to the backups bucket.
# Objects expire after 14 days (bucket lifecycle rule). Run daily by the systemd timer that
# install-backup-timer.sh sets up; can also be run by hand:
#
#   sudo /opt/smart-gardening/repo/infra/server/scripts/backup.sh
set -euo pipefail

BASE=/opt/smart-gardening
ENV_FILE="$BASE/.env"
COMPOSE_DIR="$BASE/repo/infra/server"

AWS_REGION=$(grep -E '^AWS_REGION=' "$ENV_FILE" | cut -d= -f2)
export AWS_REGION

# smart-gardening-backups-<account_id> (infra/main/backups.tf); override with BACKUPS_BUCKET.
if [[ -z "${BACKUPS_BUCKET:-}" ]]; then
  account=$(aws sts get-caller-identity --query Account --output text)
  BACKUPS_BUCKET="smart-gardening-backups-$account"
fi

key="postgres/smart_gardening-$(date -u +%Y%m%dT%H%M%SZ).dump"

cd "$COMPOSE_DIR"
docker compose --env-file "$ENV_FILE" exec -T postgres \
    pg_dump --username=postgres --dbname=smart_gardening --format=custom --compress=9 \
  | aws s3 cp - "s3://$BACKUPS_BUCKET/$key"

echo "backup uploaded to s3://$BACKUPS_BUCKET/$key"
