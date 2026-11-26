# Server stack

What runs on the EC2 instance created by `infra/main`: a Docker Compose project with PostgreSQL,
the database migrations and the ingestion service. Grafana and the decision service will join
the same file (placeholders in `docker-compose.yml`).

```
infra/server/
  docker-compose.yml
  scripts/
    deploy.sh                 # clone/update the repo, fetch secrets, docker compose up
    fetch-secrets.sh          # SSM -> /opt/smart-gardening/.env (mode 600)
    backup.sh                 # pg_dump -> S3 backups bucket
    install-backup-timer.sh   # daily systemd timer for backup.sh
```

| Service | What it does | Memory limit |
|---|---|---|
| `postgres` | PostgreSQL 17, data in the `postgres-data` volume, port on `127.0.0.1:5432` only | 512 MB |
| `migrate` | `dbmate up` with `DB/migrations`, then exits | 64 MB |
| `db-roles` | Sets the `app`/`grafana` passwords from SSM, then exits | 64 MB |
| `ingest` | SQS → PostgreSQL (`DB/ingest`) | 128 MB |

Start order: `postgres` (healthy) → `migrate` → `db-roles` → `ingest`. All containers share
the `smart-gardening-backend` network, where the database is `postgres:5432`.

On the server:

| Path | Content |
|---|---|
| `/opt/smart-gardening/repo` | Clone of this repository (managed by `deploy.sh`; do not edit files there) |
| `/opt/smart-gardening/.env` | Secrets and settings from SSM (mode 600, never committed) |

The `.env` holds `AWS_REGION`, `POSTGRES_PASSWORD`, `DB_APP_PASSWORD`, `DB_GRAFANA_PASSWORD`,
`GRAFANA_ADMIN_PASSWORD`, `IOT_ENDPOINT`, `INGEST_QUEUE_URL` and `INGEST_DLQ_URL`. The instance
role reads them from SSM; it cannot read the devices' private keys and does not need them.

All the commands below run **on the server** in an SSM shell
(`aws ssm start-session --target <instance_id>`, see [`../README.md`](../README.md)), unless
they say otherwise.

## First deployment

```sh
# 1. Get the repository (git may be missing on a fresh instance)
sudo dnf install -y git
sudo git clone https://github.com/asieramigorena/smart-gardening.git /opt/smart-gardening/repo

# 2. Deploy a branch (main = production; dev to try changes). Installs the AWS CLI and jq if
#    missing, writes /opt/smart-gardening/.env and starts the stack.
sudo /opt/smart-gardening/repo/infra/server/scripts/deploy.sh main

# 3. Check: postgres healthy, migrate and db-roles "Exited (0)", ingest healthy after ~1 min
cd /opt/smart-gardening/repo/infra/server
sudo docker compose --env-file /opt/smart-gardening/.env ps -a
sudo docker compose --env-file /opt/smart-gardening/.env logs migrate db-roles ingest

# 4. Daily backups
sudo /opt/smart-gardening/repo/infra/server/scripts/install-backup-timer.sh
```

To check the whole chain without the Pi, publish a reading with the simulator from your laptop
([`tools/simulator`](../../tools/simulator/README.md)) and look for it in the database:

```sh
sudo docker compose --env-file /opt/smart-gardening/.env exec postgres \
  psql -U postgres -d smart_gardening -c 'SELECT device_id, ts, soil_moisture_raw FROM readings ORDER BY id DESC LIMIT 5'
```

## Updating

After a merge, deploy the branch again. It fast-forwards the clone, refreshes the `.env` from
SSM, applies new migrations and rebuilds/restarts what changed:

```sh
sudo /opt/smart-gardening/repo/infra/server/scripts/deploy.sh main
```

The deploy fails (and changes nothing) if someone edited files in `/opt/smart-gardening/repo`.
Messages that arrive while `ingest` restarts wait in the queue (4-day retention).

After a password rotation in SSM (`terraform apply -replace=...`), deploy again: `db-roles`
updates the database roles and the services get the new values. The superuser password
(`POSTGRES_PASSWORD`) is only used when the volume is created: to rotate it, also run
`ALTER ROLE postgres PASSWORD '...'` by hand.

## Logs

```sh
cd /opt/smart-gardening/repo/infra/server
alias dc='sudo docker compose --env-file /opt/smart-gardening/.env'

dc ps -a                                   # state and health of every service
dc logs -f --tail=100 ingest               # follow the ingestion service
dc logs --since=1h ingest | grep '"level": "warning"'   # invalid messages, unknown devices
dc logs postgres
```

Logs rotate at 3 × 10 MB per container. The `ingest` lines are JSON: `msg` says what happened
(`stored`, `duplicate ignored`, `invalid message`, `database unavailable`...) and `reason` why.

## Dead-letter queue

Messages end up in `smart-gardening-ingest-dlq` in three ways:

| How | Recognise it | What to do |
|---|---|---|
| `ingest` rejected it (breaks the contract) | message attribute `error` with the reason | Fix the producer. The message itself is not replayable as is; delete it |
| 5 failed attempts (database error) | body = original message, no `error` attribute | Fix the cause, then replay (below) |
| The IoT rule could not send to SQS | body is an IoT error envelope (`failures`, `topic`...) | Check the rule/role; the original payload is inside |

These run **from your laptop** with your AWS profile (your user needs to receive/delete on the
DLQ and `iot:Publish` on the readings/events topics):

```sh
DLQ=$(aws ssm get-parameter --name /smart-gardening/ingest/dlq_url --query Parameter.Value --output text)
ENDPOINT=$(aws ssm get-parameter --name /smart-gardening/iot/endpoint --query Parameter.Value --output text)

# How many?
aws sqs get-queue-attributes --queue-url "$DLQ" --attribute-names ApproximateNumberOfMessages

# Look at up to 10 (they become invisible for 30 s, then come back)
aws sqs receive-message --queue-url "$DLQ" --max-number-of-messages 10 \
  --message-attribute-names All --visibility-timeout 30 \
  --query 'Messages[].{error: MessageAttributes.error.StringValue, body: Body}'
```

To **replay** messages that failed for a database reason, publish their original payload again
through IoT Core (the queue policy only accepts messages from the IoT rule, so this is the way
back in). Inserts are idempotent, so replaying something already stored is harmless; the only
change is a new `received_at`.

```sh
while msg=$(aws sqs receive-message --queue-url "$DLQ" --max-number-of-messages 1 \
              --message-attribute-names All --visibility-timeout 300 --query 'Messages[0]' --output json) \
      && [ "$msg" != "null" ]; do
  body=$(jq -r .Body <<<"$msg")
  kind=$(jq -r '.kind // empty' <<<"$body" 2>/dev/null || true)
  device=$(jq -r '.device_id // empty' <<<"$body" 2>/dev/null || true)
  if [ -n "$(jq -r '.MessageAttributes.error.StringValue // empty' <<<"$msg")" ] \
     || [[ "$kind" != readings && "$kind" != events ]] || [ -z "$device" ]; then
    echo "skipping (not replayable): $body"; continue      # stays in the DLQ
  fi
  payload=$(jq -c --arg k "$kind" 'del(.kind, .received_at) | if $k == "events" then del(.device_id) else . end' <<<"$body")
  aws iot-data publish --endpoint-url "https://$ENDPOINT" --topic "smart-gardening/$device/$kind" \
    --qos 1 --cli-binary-format raw-in-base64-out --payload "$payload" \
  && aws sqs delete-message --queue-url "$DLQ" --receipt-handle "$(jq -r .ReceiptHandle <<<"$msg")" \
  && echo "replayed $kind from $device"
done
```

To drop everything that is left (for example, invalid messages already investigated):
`aws sqs purge-queue --queue-url "$DLQ"`. Unread messages expire after 14 days anyway.

## Backups

`backup.sh` runs `pg_dump --format=custom` (compressed) inside the `postgres` container and
streams it to `s3://smart-gardening-backups-<account_id>/postgres/smart_gardening-<UTC time>.dump`.
The bucket deletes objects after 14 days. The timer runs it daily at 03:00 UTC (+ up to 30 min).

```sh
systemctl list-timers smart-gardening-backup.timer   # next run
journalctl -u smart-gardening-backup.service -n 20  # last runs
sudo /opt/smart-gardening/repo/infra/server/scripts/backup.sh   # back up now
```

### Restoring a backup

This replaces the current data with the dump. Messages that arrive meanwhile wait in the queue.

```sh
cd /opt/smart-gardening/repo/infra/server
alias dc='sudo docker compose --env-file /opt/smart-gardening/.env'
BUCKET=smart-gardening-backups-$(aws sts get-caller-identity --query Account --output text)

aws s3 ls "s3://$BUCKET/postgres/"                         # pick a file
aws s3 cp "s3://$BUCKET/postgres/<file>.dump" /tmp/restore.dump

dc stop ingest                                             # (and decision/grafana when they exist)
dc exec -T postgres pg_restore -U postgres -d smart_gardening --clean --if-exists --single-transaction \
  < /tmp/restore.dump
rm /tmp/restore.dump
sudo /opt/smart-gardening/repo/infra/server/scripts/deploy.sh main   # reapplies newer migrations, starts everything
```

`--single-transaction` makes it all-or-nothing. Roles are not in the dump; they already exist
(created by the migrations) and `db-roles` resets their passwords on deploy.

## More memory

The instance is a `t3.small` (2 GB). The long-running services above are limited to ~0.65 GB, which leaves room for Grafana and
the decision service (~256 MB each, see the placeholders). If containers get OOM-killed
(`dc ps -a` shows `Exited (137)`) or `free -m` stays low, move to a `t3.medium` (4 GB):

1. In `infra/main/variables.tf`, change the default of `instance_type` to `"t3.medium"` (in a PR).
2. `terraform plan` must show `aws_instance.server` **updated in-place** (`~ instance_type`),
   never replaced. Then `terraform apply`. AWS stops and starts the instance: a few minutes of
   downtime; the disk, the database and the instance ID stay.
3. Raise the `memory` limits in `docker-compose.yml` if needed and deploy.

## Running the stack locally

See "Running it locally" in [`DB/README.md`](../../DB/README.md#running-it-locally-no-aws):
the same compose file works on a laptop with a fake `.env` (everything except `ingest`, which
needs AWS). `docker compose --env-file <file> config` validates the file.

## Grafana

Grafana is provisioned from `infra/server/grafana/provisioning/` and loads dashboards from
`infra/server/grafana/dashboards/`. Its port is published only on the server's loopback
(`127.0.0.1:3000`).

### Opening Grafana from your laptop

Open an SSM port-forwarding tunnel to the EC2 instance:

```sh
aws ssm start-session --target <instance_id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3000"],"localPortNumber":["3000"]}'
```

Then browse to `http://localhost:3000` (user `admin`, password from SSM
`/smart-gardening/grafana/admin_password`).

The admin password is only applied the first time Grafana starts with an empty `grafana-data`
volume. Changing the SSM parameter later does not change it. To rotate it, run on the server:

```sh
docker compose exec grafana grafana cli admin reset-admin-password '<new password>'
```

### Local development with sample data

To run Postgres, migrations, sample seed data (`grafana/dev-seed.sql`, never applied in
production) and Grafana locally without AWS:

```sh
docker compose -f docker-compose.dev.yml up -d
# http://localhost:3000 (admin / admin)
```

### Editing the dashboard

1. Make your changes in the Grafana UI.
2. Open **Dashboard settings -> JSON Model** (or **Share -> Export -> Save to file**) and export the JSON.
3. Save it to `infra/server/grafana/dashboards/smart-gardening.json` and commit the change.