# Message contract

This folder is the **source of truth** for the MQTT messages exchanged between the devices
(Raspberry Pi), AWS IoT Core and the server. If the code and this contract disagree, the code is
wrong. Any change here needs the agreement of everyone who produces or consumes the message.

| File | What it is |
|---|---|
| `readings.schema.json` | Sensor readings, device → cloud |
| `commands.schema.json` | Irrigation commands, cloud → device |
| `events.schema.json` | What happened to a command, device → cloud |
| `examples/valid/`, `examples/invalid/` | Example payloads; the name prefix selects the schema |
| `test_contracts.py` | Checks the schemas and that every example passes or fails as expected |

The schemas use JSON Schema draft 2020-12.

## Topics

| Topic | Direction | Publisher | Subscriber | Schema |
|---|---|---|---|---|
| `smart-gardening/{device_id}/readings` | device → cloud | Pi | IoT rule → SQS → ingestion | `readings` |
| `smart-gardening/{device_id}/commands` | cloud → device | decision service | Pi | `commands` |
| `smart-gardening/{device_id}/events` | device → cloud | Pi | IoT rule → SQS → ingestion | `events` |

- `{device_id}` is the IoT thing name of the device (e.g. `pi-01`), which is also its MQTT
  client ID. The IoT policy only lets a device use its own topics.
- All messages are published and subscribed with **QoS 1** (at least once). Consumers must
  therefore tolerate duplicates: readings are deduplicated by `(device_id, ts)`, commands and
  events by `command_id`.
- Payloads are UTF-8 JSON objects.

## Common fields

- `schema_version`: integer, currently `1`. Bumped on any breaking change (removing or renaming a
  field, changing a type or a range). Unknown fields are rejected (`additionalProperties: false`),
  so adding a field also means updating the schema first.
- Timestamps (`ts`, `issued_at`, `expires_at`): ISO 8601 / RFC 3339 **in UTC with a trailing `Z`**,
  optionally with up to 6 fractional digits: `2026-11-19T10:15:00Z`, `2026-11-19T10:15:00.250Z`.
  Offsets such as `+01:00` are rejected. Devices must keep their clock in sync (NTP).
- `zone`: lowercase key of the irrigation zone (`^[a-z0-9][a-z0-9_-]{0,31}$`). Each zone has one valve.
- `device_id`: `^[A-Za-z0-9_-]{1,64}$`.

## readings

Sent periodically by each device.

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | `1` | yes | |
| `device_id` | string | yes | The topic wins if they differ (see rules) |
| `zone` | string | yes | |
| `ts` | UTC timestamp | yes | When the reading was taken |
| `soil_moisture_raw` | integer 0-4095 or `null` | yes (key) | Raw 12-bit ADC value, uncalibrated |
| `soil_moisture` | number 0-100 or `null` | no | % — only sent when the sensor has been calibrated |
| `air_temp` | number -40-80 or `null` | no | °C |
| `air_humidity` | number 0-100 or `null` | no | % relative humidity |
| `light` | number 0-100 or `null` | no | % of the sensor range |

A sensor that fails to read is sent as `null`, never as a made-up value such as `0` or `-1`.
`soil_moisture_raw` is the one key that must always be present (as `null` if it fails).

```json
{
  "schema_version": 1,
  "device_id": "pi-01",
  "zone": "bed-1",
  "ts": "2026-11-19T10:15:00Z",
  "soil_moisture_raw": 2310,
  "soil_moisture": 41.5,
  "air_temp": 14.2,
  "air_humidity": 63.0,
  "light": 72
}
```

## commands

Sent by the decision service to start irrigating one zone.

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | `1` | yes | |
| `command_id` | UUID (lowercase) | yes | Unique per command; echoed in every event about it |
| `action` | `"irrigate"` | yes | The only action in v1 |
| `zone` | string | yes | |
| `duration_s` | integer 1-900 | yes | Seconds the valve stays open (max 15 min) |
| `issued_at` | UTC timestamp | yes | |
| `expires_at` | UTC timestamp | yes | Must be later than `issued_at` |

There is no `device_id` field: the target is the device in the topic.

```json
{
  "schema_version": 1,
  "command_id": "3f6c2a1e-8b4d-4c7a-9e21-5d0b7f9a1c34",
  "action": "irrigate",
  "zone": "bed-1",
  "duration_s": 120,
  "issued_at": "2026-11-19T10:30:00Z",
  "expires_at": "2026-11-19T10:35:00Z"
}
```

## events

Sent by the device for every command it receives.

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | `1` | yes | |
| `command_id` | UUID | yes | Of the command the event is about |
| `type` | `irrigation_started` \| `irrigation_finished` \| `command_rejected` | yes | |
| `ts` | UTC timestamp | yes | When it happened |
| `duration_s` | integer 0-900 | in `irrigation_started` and `irrigation_finished` | Started: planned duration. Finished: seconds the valve was actually open (shorter if interrupted) |
| `reason` | `expired` \| `duplicate` \| `invalid` \| `busy` | only and always in `command_rejected` | |

Normal flow: `irrigation_started` → `irrigation_finished`. A rejected command produces a single
`command_rejected` and nothing else.

```json
{
  "schema_version": 1,
  "command_id": "3f6c2a1e-8b4d-4c7a-9e21-5d0b7f9a1c34",
  "type": "irrigation_finished",
  "ts": "2026-11-19T10:32:02Z",
  "duration_s": 120
}
```

```json
{
  "schema_version": 1,
  "command_id": "9a1b7c2d-0e3f-4a5b-8c6d-7e8f9a0b1c2d",
  "type": "command_rejected",
  "ts": "2026-11-19T10:31:00Z",
  "reason": "busy"
}
```

## Rules

1. **The topic's `device_id` wins.** IoT Core authenticates the device and only lets it publish on
   its own topics, so the `{device_id}` in the topic is trusted and the one in the payload is not.
   The IoT rule adds `device_id` (from the topic), `kind` (`readings` or `events`) and
   `received_at` (server time, ms since epoch) to the message it sends to SQS, overwriting any
   `device_id` in the payload.
2. **Expired commands are rejected.** If the device's clock is past `expires_at` when the command
   arrives, it does not irrigate and publishes `command_rejected` with `reason: expired`.
3. **Repeated commands are rejected.** QoS 1 can deliver the same message twice. The device
   remembers the `command_id`s it has seen (at least until they expire) and answers a repeat with
   `command_rejected`, `reason: duplicate`, without irrigating again.
4. **One command at a time per valve.** While a zone is being irrigated, a new command for the
   same zone is rejected with `reason: busy`; it is not queued. Different zones are independent.
5. **Invalid commands are rejected** with `reason: invalid` (fails the schema, unknown zone,
   `expires_at` not after `issued_at`...). If the payload is so broken that it has no valid
   `command_id`, the device cannot reference it, so it only logs it locally.
6. **Maximum payload: 8 KB** (8192 bytes of UTF-8 JSON). Producers must not send more and
   consumers may drop anything larger.
7. **Validate before sending and after receiving.** Producers validate against the schema;
   consumers validate too and treat a failing message as invalid (devices: rule 5; ingestion:
   leave it to the dead-letter queue / log it, never store it half-parsed).

Rule 6 and the `expires_at > issued_at` check cannot be expressed in JSON Schema;
`test_contracts.py` applies them on top of the schema, and producers/consumers must too.

## Changing the contract

1. Edit the schema and this README in the same PR.
2. Add a valid example for every new case and an invalid one for every new rule. Each invalid
   example must break exactly one rule (its name says which).
3. Breaking change → bump `schema_version` (`const`) and coordinate the rollout of devices and server.
4. Run the test:

   ```sh
   pip install -r docs/contracts/requirements.txt
   python -m unittest discover -s docs/contracts -p "test_*.py"
   ```

CI runs the same test whenever `docs/contracts/**` changes (`Python / contracts`, see
[`docs/ci.md`](../ci.md)).
