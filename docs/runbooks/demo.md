# Demo runbook (MVP, 11 December)

What to check before the demo, what to show and in which order, and what to do when something
breaks in front of the audience. Every command runs **from a laptop** with the team AWS profile
unless it says "on the server" (an SSM shell, see [`infra/README.md`](../../infra/README.md#3-open-a-shell-on-the-server))
or "on the Pi".

```sh
export AWS_PROFILE=smart-gardening AWS_REGION=eu-south-2
python -m venv .venv && . .venv/bin/activate
pip install -r tools/e2e_check/requirements.txt -r tools/simulator/requirements.txt

# Instance ID, used below as <instance_id>
INSTANCE=$(aws ec2 describe-instances \
  --filters Name=tag:Name,Values=smart-gardening-server Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
```

On the server, the commands use this alias:

```sh
cd /opt/smart-gardening/repo/infra/server
alias dc='sudo docker compose --env-file /opt/smart-gardening/.env'
```

## Tunnels

Nothing on the server is reachable from the Internet; PostgreSQL and Grafana are reached through
SSM port forwarding. Keep one terminal open per tunnel.

```sh
# PostgreSQL -> localhost:15432 (not 5432, so it never collides with a PostgreSQL on the laptop)
aws ssm start-session --target "$INSTANCE" --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["5432"],"localPortNumber":["15432"]}'

# Grafana -> http://localhost:3000 (user admin)
aws ssm start-session --target "$INSTANCE" --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3000"],"localPortNumber":["3000"]}'

# Grafana admin password
aws ssm get-parameter --name /smart-gardening/grafana/admin_password --with-decryption \
  --query Parameter.Value --output text
```

15432 is the end-to-end check's default port. If you have to use another one, pass `--db-port`.

## The end-to-end check

[`tools/e2e_check`](../../tools/e2e_check/README.md) checks the whole chain in about 10 s and
prints OK / WARN / FAIL per step: server online in SSM, latest reading per device, a test reading
through IoT Core → SQS → ingestion → PostgreSQL, (optionally) an irrigation command and its
events, and the queues. Exit code 0 = no failures.

```sh
python tools/e2e_check/e2e_check.py                 # full check (publishes a test reading)
python tools/e2e_check/e2e_check.py --skip-publish  # read-only
python tools/e2e_check/e2e_check.py --command       # + 10 s irrigation of bed-1 by pi-01
```

The test reading is sent as device `e2e-check` in zone `e2e-test`, a test-only zone with no
valve, so it never mixes with `bed-1`. Grafana panels and the decision service must exclude
that zone (see [`DB/README.md`](../../DB/README.md)); if a panel shows `e2e-test`, add
`zone_id <> 'e2e-test'` to its query.

## Before the demo

Do the whole list **the day before** (time to fix things) and again **one hour before**.

| # | Check | How | Expected |
|---|---|---|---|
| 1 | Pi powered, online and running the node | On the Pi: `systemctl status smart-gardening-node`, `journalctl -u smart-gardening-node -n 20` | `active (running)`, a reading published every interval, no errors |
| 2 | Pi clock in sync | On the Pi: `timedatectl` | `System clock synchronized: yes`. If not: `sudo systemctl restart systemd-timesyncd` and check again after a minute. A wrong clock makes readings land in the wrong place in Grafana and makes commands arrive already expired |
| 3 | Server stack healthy | On the server: `dc ps -a` | `postgres` and `ingest` `(healthy)`, `migrate` and `db-roles` `Exited (0)`, `grafana` and `decision` up (once they are in the stack) |
| 4 | Latest reading is recent | `e2e_check.py --skip-publish` step b | OK; newest reading of `pi-01` a few minutes old at most. The line also warns if the device clock is off |
| 5 | Grafana opens | Grafana tunnel, browse to `http://localhost:3000`, log in | Dashboard with data from the last hour |
| 6 | DLQ empty | `e2e_check.py` step e | OK (`DLQ 0`). Otherwise see [Messages in the DLQ](#messages-in-the-dlq) |
| 7 | Whole chain green | `e2e_check.py` (and `--command` once the Pi executes commands) | `RESULT: OK`, exit code 0 |
| 8 | Laptop ready | Session Manager plugin installed, AWS profile works (`aws sts get-caller-identity`), repo on `main` or the demo branch, virtualenv active | |

Also have at hand: the Pi's power supply and a spare Ethernet cable or a phone hotspot the Pi
already knows, and a glass of water / a dry and a wet pot for the soil sensor.

**Emptying the DLQ** (only after looking at the messages: they are evidence of a problem):

```sh
DLQ=$(aws ssm get-parameter --name /smart-gardening/ingest/dlq_url --query Parameter.Value --output text)
aws sqs receive-message --queue-url "$DLQ" --max-number-of-messages 10 \
  --message-attribute-names All --visibility-timeout 30 \
  --query 'Messages[].{error: MessageAttributes.error.StringValue, body: Body}'
aws sqs purge-queue --queue-url "$DLQ"     # deletes all of them; allowed once every 60 s
```

Messages that failed because of a database error can be replayed instead of purged: see
"Dead-letter queue" in [`infra/server/README.md`](../../infra/server/README.md#dead-letter-queue).

## During the demo

Open before starting (one terminal each):

1. PostgreSQL tunnel (15432).
2. Grafana tunnel (3000), browser on the dashboard, logged in.
3. SSM shell on the server with `dc logs -f --tail=20 ingest` (shows `stored` for each reading).
4. Free terminal in the repo with the virtualenv active and `AWS_PROFILE` / `AWS_REGION` exported.

Order:

1. **Architecture** (1 min): Pi → MQTT/TLS with a device certificate → IoT Core → rule → SQS
   (+ DLQ) → ingestion → PostgreSQL → Grafana; decision service → IoT Core → Pi. No open ports
   on the server; access only through SSM.
2. **The Pi and its sensors**: show the hardware and the node logs (`journalctl -u smart-gardening-node -f`).
3. **Live data in Grafana**: soil moisture, temperature, humidity and light. Water the pot or
   cover the light sensor and wait for the next reading (logs of terminal 3, then Grafana).
4. **Decisions**: the decision service's latest decisions (Grafana panel, or the `decisions`
   table) and why it decided to water or not.
5. **Irrigation**: a decision that waters, or a manual command:

   ```sh
   python tools/e2e_check/e2e_check.py --command --device pi-01 --zone bed-1
   ```

   The valve opens for 10 s; step d prints `irrigation_started` and `irrigation_finished`, which
   also appear in `irrigation_events` / Grafana.
6. **Robustness**: send a message that breaks the contract and show that it ends up in the DLQ
   with its reason, not in the database:

   ```sh
   python tools/simulator/simulate.py --invalid      # terminal 3 logs "invalid message"
   python tools/e2e_check/e2e_check.py --skip-publish   # step e: WARN, DLQ 1
   ```

   Purge the DLQ afterwards (see above).
7. **End-to-end check**: run `e2e_check.py` and show `RESULT: OK`.

## Plan B

| Problem | Symptom | What to do |
|---|---|---|
| [Pi offline](#pi-offline) | Step b FAIL, no new points in Grafana | Simulator |
| [Messages in the DLQ](#messages-in-the-dlq) | Step e WARN | Look, then purge or replay |
| [Ingestion down](#ingestion-down) | Step c FAIL, ingest queue growing | Logs, restart |
| [Grafana does not open](#grafana-does-not-open) | Browser error on `localhost:3000` | Restart it; show the data with `psql` |
| [Decision service does not irrigate](#decision-service-does-not-irrigate) | No new decision or command | Manual command with `--command` |

### Pi offline

1. Quick fixes (2 min max): power, network (switch to the hotspot), `sudo systemctl restart smart-gardening-node`,
   `journalctl -u smart-gardening-node -n 50` (a certificate or endpoint error means a config problem,
   not something to fix live).
2. Otherwise, **use the simulator** as `pi-01`. It publishes contract-valid readings through
   IoT Core with your AWS credentials, so the rest of the chain is exactly the same. Say that the
   data is simulated.

   ```sh
   python tools/simulator/simulate.py --device pi-01 --zone bed-1 --count 0 --interval 30   # Ctrl+C to stop
   ```

   Stop it if the Pi comes back. It does not disconnect the Pi (it uses HTTPS, not an MQTT
   client ID), but both would write readings for `pi-01`.
3. Commands cannot be shown without the Pi: show the command being published (step d prints its
   `command_id`) and explain the events the Pi would send.

### Messages in the DLQ

Look at them first (command in [Before the demo](#before-the-demo)), then by their `error`
attribute:

- `unknown_zone: ...`: a node with a wrong `ZONE` (e.g. `bed1`). Fix the node config; purge.
- Other contract errors: the producer sends something the contract does not allow. Fix the
  producer after the demo; purge.
- No `error` attribute: the database failed 5 times. Check `dc logs postgres ingest`, fix it,
  then replay (see `infra/server/README.md`).

During the demo a DLQ warning is not blocking: the data that matters is in the database.

### Ingestion down

On the server:

```sh
dc ps -a                              # ingest unhealthy / restarting / exited?
dc logs --tail=100 ingest postgres    # "database unavailable", credentials, permissions...
dc restart ingest
```

If it does not recover, redeploy (`sudo /opt/smart-gardening/repo/infra/server/scripts/deploy.sh dev`,
or `main` after the release). Nothing is lost: messages wait in the queue (4 days) and are stored
when ingestion is back. Check with `e2e_check.py` step e (ingest queue going down to 0) and step c.

### Grafana does not open

1. Is the tunnel still running? Reopen it. Is port 3000 used by something else on the laptop?
   Use `"localPortNumber":["3001"]` and `http://localhost:3001`.
2. On the server: `dc ps grafana`, `dc logs --tail=50 grafana`, `dc restart grafana`.
3. Otherwise, **show the data straight from the database** (PostgreSQL tunnel):

   ```sh
   export PGPASSWORD=$(aws ssm get-parameter --name /smart-gardening/db/grafana_password \
     --with-decryption --query Parameter.Value --output text)     # read-only user
   psql "postgresql://grafana@localhost:15432/smart_gardening"
   ```

   ```sql
   -- Latest reading of each device
   SELECT DISTINCT ON (device_id) device_id, zone_id, ts, now() - ts AS age,
          soil_moisture_raw, soil_moisture, air_temp, air_humidity, light
   FROM readings ORDER BY device_id, ts DESC;

   -- Last hour of pi-01
   SELECT ts, soil_moisture_raw, air_temp, air_humidity, light
   FROM readings WHERE device_id = 'pi-01' AND ts > now() - interval '1 hour' ORDER BY ts;

   -- Latest decisions and the events of their commands
   SELECT d.decided_at, d.zone_id, d.irrigate, d.duration_s, d.reason, e.type, e.ts
   FROM decisions d LEFT JOIN irrigation_events e USING (command_id)
   ORDER BY d.decided_at DESC LIMIT 10;
   ```

### Decision service does not irrigate

1. On the server: `dc ps decision`, `dc logs --tail=50 decision`. It may simply have decided
   not to water (look at the latest `decisions` row and its `reason`): that is a valid result to
   show.
2. **Send a manual command** with the end-to-end check (validated against the contract, 10 s,
   expires in 2 min):

   ```sh
   python tools/e2e_check/e2e_check.py --command --device pi-01 --zone bed-1
   ```

   If step d fails with `rejected (reason: busy)`, the zone is already being watered: wait 10 s
   and retry. `reason: expired` means the Pi's clock is wrong (check 2 of the list). Missing
   events: look for the command in `journalctl -u smart-gardening-node -n 50` on the Pi.
