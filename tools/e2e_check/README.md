# End-to-end check

Checks the whole chain from a laptop and prints OK / WARN / FAIL / SKIP per step. Use it
before a demo ([`docs/runbooks/demo.md`](../../docs/runbooks/demo.md)) or after a deploy.

| Step | Checks | FAIL / WARN when |
|---|---|---|
| a. Server | The running instance tagged `Name=smart-gardening-server` is `Online` in SSM | Not running, not registered or not Online |
| b. Readings | Latest reading of each device and how long ago it arrived | FAIL if the newest one from a real device is older than `--max-age-min` (15), or the database is unreachable |
| c. Ingest path | Publishes a test reading (simulator payload, unique millisecond `ts`) as device `e2e-check` in zone `e2e-test` through IoT Core and waits up to `--timeout` (60 s) for it in `readings` | It does not arrive |
| d. Command | Only with `--command`: publishes a 10 s `irrigate` command (validated against `docs/contracts/commands.schema.json`, expires in 2 min) to `--device`/`--zone` (`pi-01`/`bed-1`) and waits for `irrigation_started` and `irrigation_finished` with its `command_id` | Missing events, or `command_rejected` |
| e. Queues | Messages in `smart-gardening-ingest` and its DLQ | WARN if the DLQ is not empty (prints the command to read them) |

Exit code: **0** if no step failed (WARN and SKIP are fine), **1** otherwise.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r tools/e2e_check/requirements.txt

# In another terminal, keep the PostgreSQL tunnel open (the script does not open it):
aws ssm start-session --profile smart-gardening --region eu-south-2 --target <instance_id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["5432"],"localPortNumber":["15432"]}'

python tools/e2e_check/e2e_check.py                  # full check
python tools/e2e_check/e2e_check.py --skip-publish   # read-only, publishes nothing
python tools/e2e_check/e2e_check.py --command --device pi-01 --zone bed-1   # --zone: step d only
python tools/e2e_check/e2e_check.py --help
```

The default local port is **15432**, not 5432, because a PostgreSQL installed on the laptop
usually holds 5432. If 15432 is taken too, tunnel to another port and pass `--db-port`; when the
port answers but is not the project database, the script says so ("not the project database").

## Notes

- AWS profile: `--profile`, else `AWS_PROFILE`, else `smart-gardening`. Region `eu-south-2`.
- The database user is `app`. Its password is read from SSM (`/smart-gardening/db/app_password`)
  at run time and only passed to the driver: it is never printed, written to a file or put in
  the command line. Queue URLs and the IoT endpoint also come from SSM; queue URLs are not
  printed (they contain the account ID).
- The test reading of step c is sent as device `e2e-check` (`--test-device`) in zone
  `e2e-test` (`--test-zone`), never in a real zone. That zone and device are created by the
  migration `DB/migrations/20261126110000_add_e2e_test_zone.sql`; the zone has no valve and
  Grafana and the decision service exclude it (see [`DB/README.md`](../../DB/README.md)).
  Step b lists test readings but ignores them for the freshness verdict, so a recent test
  reading cannot hide a dead Pi. `--zone` only applies to the command of step d.
- Your IAM user needs `ssm:DescribeInstanceInformation`, `ec2:DescribeInstances`,
  `ssm:GetParameter` on `/smart-gardening/*`, `sqs:GetQueueAttributes` on the two queues, and
  `iot:Publish` on `smart-gardening/*/readings` (step c) and `smart-gardening/*/commands`
  (step d).
- Tests (AWS and PostgreSQL mocked): `python -m unittest discover -s tools/e2e_check`.
