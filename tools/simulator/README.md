# Device simulator

Publishes fake sensor readings to `smart-gardening/<device>/readings`, following
[`docs/contracts`](../../docs/contracts/README.md). Use it to test the IoT rule → SQS → ingestion
→ PostgreSQL chain without a Raspberry Pi, or to feed Grafana with data.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r tools/simulator/requirements.txt

python tools/simulator/simulate.py --dry-run --count 3            # print payloads, publish nothing
python tools/simulator/simulate.py --profile smart-gardening      # one valid reading
python tools/simulator/simulate.py --profile smart-gardening --count 0 --interval 30   # until Ctrl+C
python tools/simulator/simulate.py --profile smart-gardening --invalid   # should end up in the DLQ
python tools/simulator/simulate.py --help
```

## Modes

**`--mode iot-data` (default).** Publishes over HTTPS with your AWS credentials (`--profile` or
`AWS_PROFILE`). The endpoint comes from `--endpoint`, `IOT_ENDPOINT` or SSM
`/smart-gardening/iot/endpoint`. Your IAM user needs `iot:Publish` on
`topic/smart-gardening/*/readings` (and `ssm:GetParameter` for the endpoint).

**`--mode mqtt`.** Connects like the Pi: MQTT over TLS on port 8883 with a device certificate
and client ID = `--device`. It tests the device policy as well. Set:

| Variable | Value |
|---|---|
| `IOT_ENDPOINT` | `aws ssm get-parameter --name /smart-gardening/iot/endpoint --query Parameter.Value --output text` |
| `IOT_CERT_FILE` | Device certificate (`pi-01.cert.pem`) |
| `IOT_KEY_FILE` | Device private key (`pi-01.private.key`) |
| `IOT_CA_FILE` | `AmazonRootCA1.pem` |

How to get these files: [`infra/README.md`](../../infra/README.md#6-install-a-device-certificate-on-the-pi).
Do not run it with the same device name while the real Pi is connected: IoT Core allows one
connection per client ID and disconnects the other one.

## Notes

- Readings use the current UTC time, rounded to the second. The database ignores a second
  reading of the same device in the same second, so keep `--interval` ≥ 1.
- About 5 % of the valid readings have one sensor set to `null`, like a failing sensor.
- `--invalid` sends readings that break exactly one rule (out-of-range ADC value, missing zone,
  non-UTC timestamp, unknown field, wrong schema version). The ingestion service logs them and
  moves them to the DLQ with the reason.
- Tests: `python -m unittest discover -s tools/simulator` (needs `jsonschema`, e.g. from
  `DB/ingest/requirements.txt`).
