# Database

PostgreSQL 17 on the server, in Docker (`infra/server/docker-compose.yml`). It stores the device
readings and events (written by the ingestion service), the weather forecasts and irrigation
decisions (written by the decision service), and is read by Grafana.

```
DB/
  migrations/              # dbmate migrations, applied on every deploy
  set-role-passwords.sql   # sets the app/grafana passwords from SSM on every deploy
  ingest/                  # SQS -> PostgreSQL ingestion service (Python)
```

Connection facts:

| | Value |
|---|---|
| Host (from containers) | `postgres`, port `5432`, network `smart-gardening-backend` |
| Host (from your laptop) | `localhost:5432` through an SSM tunnel (below) |
| Database | `smart_gardening` |
| Users | `app` (read/write), `grafana` (read-only), `postgres` (superuser, migrations only) |
| Passwords | SSM `/smart-gardening/db/app_password`, `/smart-gardening/db/grafana_password`, `/smart-gardening/db/password` |

## Schema

All timestamps are `timestamptz` and stored in UTC. Zone and device IDs follow the formats of
[`docs/contracts`](../docs/contracts/README.md).

**`zones`**: `id` (text PK, e.g. `bed-1`), `name`, `created_at`.

**`devices`**: `id` (text PK, the IoT thing name, e.g. `pi-01`), `zone_id` → `zones`, `created_at`.

**`readings`**: one row per sensor reading.

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `device_id` | text → `devices` | From the MQTT topic |
| `zone_id` | text → `zones` | From the payload |
| `ts` | timestamptz | When the device took the reading |
| `received_at` | timestamptz | When IoT Core received it |
| `soil_moisture_raw` | integer, NULL | Raw ADC value 0-4095 (NULL if the sensor failed) |
| `soil_moisture` | numeric, NULL | %, only from calibrated sensors |
| `air_temp` | numeric, NULL | °C |
| `air_humidity` | numeric, NULL | % |
| `light` | numeric, NULL | % of the sensor range |
| `raw` | jsonb | The SQS message as received |

`UNIQUE (device_id, ts)` drops duplicates; index on `(zone_id, ts DESC)`.

**`forecasts`**: `id`, `zone_id` → `zones`, `source` (e.g. `open-meteo`), `fetched_at` (download
time), `valid_at` (hour the forecast is for), `precipitation_mm`, `precipitation_prob` (0-100),
`temp` (°C), `et0` (mm). `UNIQUE (zone_id, source, fetched_at, valid_at)`.

**`decisions`**: one row per decision, including "do not irrigate". `id` (uuid PK, generated),
`zone_id` → `zones`, `decided_at` (default `now()`), `irrigate` (bool), `duration_s` (1-900),
`water_l`, `reason`, `model_version`, `command_id` (uuid, unique, NULL if no command was sent).

**`irrigation_events`**: what the devices report about commands. `id`, `command_id`,
`device_id` → `devices`, `type` (`irrigation_started` | `irrigation_finished` |
`command_rejected`), `ts`, `duration_s`, `reason` (`expired` | `duplicate` | `invalid` | `busy`),
`received_at`, `raw`. `UNIQUE (command_id, type)`.

### Roles

- `app`: `SELECT, INSERT, UPDATE, DELETE` on every table, `USAGE` on sequences. Cannot change the
  schema (no `CREATE`) nor touch `schema_migrations`.
- `grafana`: `SELECT` only, and its sessions are read-only (`default_transaction_read_only`).
- Both get access to **future tables automatically** (`ALTER DEFAULT PRIVILEGES` for tables
  created by `postgres`, i.e. by migrations). Nothing to grant in new migrations.

Passwords never appear in the repo: the `db-roles` step of the compose stack sets them from SSM
on every deploy, so rotating one in SSM and redeploying is enough.

### Unknown devices and zones

A reading from a device that is not in `devices` is **not** dropped: if its zone exists, the
ingestion service creates the device and logs a warning (`unknown device registered`).
An event from an unknown device creates it without a zone; its first reading sets the zone.

Zones are **not** created automatically. A reading whose `zone` is not in `zones` (e.g. a typo
such as `bed1` for `bed-1` in the node's `ZONE`) is invalid: it is logged (`invalid message`)
and sent to the DLQ with the reason `unknown_zone: ...`, and nothing is written. Fix the node's
`ZONE`, or add the zone (below) if it really is a new one. Messages in the DLQ with an `error`
attribute are not replayed (see the DLQ section of [`infra/server/README.md`](../infra/server/README.md)).

### Adding a zone

Zones are created with a dbmate migration (see [Migrations](#migrations)), like the seed
`20261120000003_seed_bed1_pi01.sql` (`bed-1`, the real bed) or
`20261126110000_add_e2e_test_zone.sql` (`e2e-test`, test-only, no valve: see the Grafana and
decision service sections):

```sql
-- migrate:up
INSERT INTO zones (id, name) VALUES ('bed-2', 'Bed 2') ON CONFLICT DO NOTHING;

-- migrate:down
DELETE FROM zones WHERE id = 'bed-2';
```

The `id` must follow the contract's zone format (`^[a-z0-9][a-z0-9_-]{0,31}$`) and match the
node's `ZONE`. The next deploy applies it before the ingestion service starts. Create the zone
before provisioning a node for it; otherwise its readings go to the DLQ until it exists.
To rename a zone, use a migration too: `UPDATE zones SET name = 'Front bed' WHERE id = 'bed-2'`.

## Migrations

Managed with [dbmate](https://github.com/amacneil/dbmate). The `migrate` service of the compose
stack runs `dbmate up` on every deploy, before the ingestion service starts; migrations already
applied (table `schema_migrations`) are skipped.

To add one:

1. Create `DB/migrations/<YYYYMMDDHHMMSS>_<what>.sql` (UTC timestamp, later than the last file),
   or let dbmate name it: `docker run --rm -v "$PWD/DB/migrations:/db/migrations" ghcr.io/amacneil/dbmate:2.38.1 new add_something`.
2. Write both sections:

   ```sql
   -- migrate:up
   ALTER TABLE readings ADD COLUMN battery numeric;

   -- migrate:down
   ALTER TABLE readings DROP COLUMN battery;
   ```

3. Test it locally (below), including the rollback.
4. Never edit a migration that has been deployed: add a new one.

Changing what the devices send also needs a contract change first (`docs/contracts/`).

## Connecting from your laptop

Through an SSM tunnel (setup in [`infra/README.md`](../infra/README.md#5-connect-to-postgresql-from-your-laptop-port-forwarding)):

```sh
aws ssm start-session --target <instance_id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["5432"],"localPortNumber":["5432"]}'

# in another terminal
export PGPASSWORD=$(aws ssm get-parameter --name /smart-gardening/db/app_password \
  --with-decryption --query Parameter.Value --output text)
psql "postgresql://app@localhost:5432/smart_gardening"
```

Use `app` for day-to-day work. `postgres` is only for migrations and emergencies.

## Running it locally (no AWS)

Useful to test migrations and queries:

```sh
cd infra/server
cat > /tmp/sg.env <<'EOF'
AWS_REGION=eu-south-2
POSTGRES_PASSWORD=local-super
DB_APP_PASSWORD=local-app
DB_GRAFANA_PASSWORD=local-grafana
GRAFANA_ADMIN_PASSWORD=local-admin
IOT_ENDPOINT=unused
INGEST_QUEUE_URL=unused
INGEST_DLQ_URL=unused
POSTGRES_HOST_PORT=15432
EOF
docker compose -p sg-local --env-file /tmp/sg.env up db-roles      # postgres + migrations + roles
psql "postgresql://app:local-app@localhost:15432/smart_gardening"

# Store tests against the real database (from the repo root, in a virtualenv)
pip install -r DB/ingest/requirements.txt
INGEST_TEST_DATABASE_URL=postgresql://app:local-app@localhost:15432/smart_gardening \
  python -m unittest discover -s DB/ingest/tests -t DB/ingest

docker compose -p sg-local --env-file /tmp/sg.env down -v           # delete everything
```

`ingest` is not started locally because it needs the AWS queue. Its unit tests mock SQS and the
database: `python -m unittest discover -s DB/ingest/tests -t DB/ingest`.

## Ingestion service (`DB/ingest/`)

Long-polls the `smart-gardening-ingest` queue (10 messages, 20 s wait) with the EC2 instance role
and writes `readings` and `irrigation_events`. For each message:

1. Takes out the fields the IoT rule adds (`kind`, `received_at` in epoch ms, and `device_id`
   for events) and validates the rest against `docs/contracts/`. A missing or unknown `kind` is
   invalid.
2. Valid: `INSERT ... ON CONFLICT DO NOTHING` in one transaction → commit → delete from the queue.
   Duplicates are deleted too.
3. Invalid (fails the contract, or a reading for a zone that is not in `zones`): logged with the
   reason, copied to the DLQ with a message attribute `error` (the reason, e.g.
   `unknown_zone: 'bed1' ...`), then deleted from the queue.
4. Database error: **not** deleted. SQS redelivers it after 60 s and moves it to the DLQ after 5
   attempts. If the database is down, the service stops receiving and retries with backoff
   (1 s → 60 s).

Logs are JSON lines (`docker compose logs ingest`). The container is `unhealthy` if it has not
completed a poll for 2 minutes. It stops cleanly on `SIGTERM` (finishes the current message).

## For the decision service

The service runs as another container of the same compose stack (placeholder `decision` in
`infra/server/docker-compose.yml`) with `DATABASE_URL` for the `app` user, `AWS_REGION` and
`IOT_ENDPOINT` in its environment. The instance role lets it publish to
`smart-gardening/*/commands` and nothing else on IoT.

Read the latest data:

```sql
-- Last 24 h of readings of a zone
SELECT ts, device_id, soil_moisture_raw, soil_moisture, air_temp, air_humidity, light
FROM readings
WHERE zone_id = 'bed-1' AND ts > now() - interval '24 hours'
ORDER BY ts;

-- Most recent forecast for each upcoming hour
SELECT DISTINCT ON (valid_at) valid_at, precipitation_mm, precipitation_prob, temp, et0
FROM forecasts
WHERE zone_id = 'bed-1' AND source = 'open-meteo' AND valid_at >= now()
ORDER BY valid_at, fetched_at DESC;
```

Store forecasts with `INSERT ... ON CONFLICT (zone_id, source, fetched_at, valid_at) DO NOTHING`.

Record the decision and publish the command (format in
[`docs/contracts/README.md`](../docs/contracts/README.md#commands)). Insert first and publish
inside the same transaction: if the publish fails, the decision is rolled back.

```python
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
import psycopg

iot = boto3.client(
    "iot-data",
    region_name=os.environ["AWS_REGION"],
    endpoint_url=f"https://{os.environ['IOT_ENDPOINT']}",
)


def irrigate(conn: psycopg.Connection, device_id: str, zone_id: str, duration_s: int,
             water_l: float, reason: str, model_version: str) -> str:
    command_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    command = {
        "schema_version": 1,
        "command_id": command_id,
        "action": "irrigate",
        "zone": zone_id,
        "duration_s": duration_s,
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with conn.transaction():
        conn.execute(
            """INSERT INTO decisions (zone_id, irrigate, duration_s, water_l, reason, model_version, command_id)
               VALUES (%s, true, %s, %s, %s, %s, %s)""",
            (zone_id, duration_s, water_l, reason, model_version, command_id),
        )
        iot.publish(topic=f"smart-gardening/{device_id}/commands", qos=1,
                    payload=json.dumps(command).encode())
    return command_id
```

A "do not irrigate" decision is the same `INSERT` with `irrigate = false`, no `duration_s`
and no `command_id`. The device answers on `/events`, which ends up in `irrigation_events`:
join on `command_id` to know whether a command was executed or rejected.

**Skip the `e2e-test` zone.** It only holds the test readings of the end-to-end check
([`tools/e2e_check`](../tools/e2e_check/README.md), device `e2e-check`) and has no valve. Never
make decisions, fetch forecasts or send commands for it: if the service loops over `zones`, use
`SELECT id FROM zones WHERE id <> 'e2e-test'`.

## For Grafana

Grafana runs in the same compose stack (service `grafana` in
`infra/server/docker-compose.yml`). PostgreSQL data source:

| Setting | Value |
|---|---|
| Host | `postgres:5432` |
| Database | `smart_gardening` |
| User | `grafana` (read-only) |
| Password | `${DB_GRAFANA_PASSWORD}`: in the compose environment, from SSM `/smart-gardening/db/grafana_password` (written to `/opt/smart-gardening/.env` by `fetch-secrets.sh`) |
| TLS/SSL mode | `disable` (internal Docker network) |
| Version | 17 |

The Grafana admin password is `GRAFANA_ADMIN_PASSWORD` (SSM
`/smart-gardening/grafana/admin_password`). Provision the data source from a file
so nothing is configured by hand.

Useful queries (Grafana macros: `$__timeFilter`, `$__timeGroupAlias`):

```sql
-- Soil moisture and air temperature per zone over time (time series)
SELECT $__timeGroupAlias(ts, 5m), zone_id AS metric, avg(soil_moisture_raw) AS soil_moisture_raw
FROM readings
WHERE $__timeFilter(ts) AND zone_id <> 'e2e-test'
GROUP BY 1, 2
ORDER BY 1;

-- Last reading of each device (table / stat)
SELECT DISTINCT ON (device_id)
  device_id, zone_id, ts, now() - ts AS age, soil_moisture_raw, soil_moisture, air_temp, air_humidity, light
FROM readings
WHERE zone_id <> 'e2e-test'
ORDER BY device_id, ts DESC;

-- Decisions (table or annotations)
SELECT decided_at AS time, zone_id, irrigate, duration_s, water_l, reason, model_version
FROM decisions
WHERE $__timeFilter(decided_at)
ORDER BY decided_at DESC;

-- Irrigation events with the decision that caused them
SELECT e.ts AS time, e.device_id, e.type, e.duration_s, e.reason, d.zone_id, d.reason AS decision_reason
FROM irrigation_events e
LEFT JOIN decisions d USING (command_id)
WHERE $__timeFilter(e.ts)
ORDER BY e.ts DESC;
```

Sensor columns are NULL when a sensor failed: Grafana shows gaps, which is what we want.

**Exclude the `e2e-test` zone from every panel** (`zone_id <> 'e2e-test'`, as in the queries
above). It is a test-only zone without a valve: the end-to-end check
([`tools/e2e_check`](../tools/e2e_check/README.md)) publishes a reading there as device
`e2e-check` on every run. A zone dashboard variable should come from
`SELECT id FROM zones WHERE id <> 'e2e-test'`.

Open Grafana from your laptop (its port is published only on the server's loopback):

```sh
aws ssm start-session --target <instance_id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3000"],"localPortNumber":["3000"]}'
# then browse to http://localhost:3000 (user admin)
```
