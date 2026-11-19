-- Core schema. Zone and device IDs follow docs/contracts (readings.schema.json $defs).

-- migrate:up
CREATE TABLE zones (
  id         text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_-]{0,31}$'),
  name       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE devices (
  id         text PRIMARY KEY CHECK (id ~ '^[A-Za-z0-9_-]{1,64}$'),
  -- NULL only for a device first seen through an event (events carry no zone); its first
  -- reading fills it in.
  zone_id    text REFERENCES zones (id),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- One row per sensor reading. Duplicates (QoS 1 redelivery, SQS retries) are dropped by the
-- UNIQUE constraint.
CREATE TABLE readings (
  id                bigserial PRIMARY KEY,
  device_id         text NOT NULL REFERENCES devices (id),
  zone_id           text NOT NULL REFERENCES zones (id),
  ts                timestamptz NOT NULL,          -- when the device took the reading
  received_at       timestamptz NOT NULL,          -- when IoT Core received it
  soil_moisture_raw integer CHECK (soil_moisture_raw BETWEEN 0 AND 4095),
  soil_moisture     numeric,                       -- %, only from calibrated sensors
  air_temp          numeric,                       -- degrees Celsius
  air_humidity      numeric,                       -- %
  light             numeric,                       -- % of the sensor range
  raw               jsonb NOT NULL,                -- message as received from SQS
  UNIQUE (device_id, ts)
);

CREATE INDEX readings_zone_ts_idx ON readings (zone_id, ts DESC);

-- Weather forecasts per zone, written by the decision service.
CREATE TABLE forecasts (
  id                 bigserial PRIMARY KEY,
  zone_id            text NOT NULL REFERENCES zones (id),
  source             text NOT NULL,                -- e.g. 'open-meteo'
  fetched_at         timestamptz NOT NULL,         -- when the forecast was downloaded
  valid_at           timestamptz NOT NULL,         -- the hour the forecast is for
  precipitation_mm   numeric,
  precipitation_prob numeric CHECK (precipitation_prob BETWEEN 0 AND 100),
  temp               numeric,                      -- degrees Celsius
  et0                numeric,                      -- reference evapotranspiration, mm
  UNIQUE (zone_id, source, fetched_at, valid_at)
);

CREATE INDEX forecasts_zone_valid_idx ON forecasts (zone_id, valid_at DESC);

-- Every irrigation decision of the decision service, including "do not irrigate".
CREATE TABLE decisions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  zone_id       text NOT NULL REFERENCES zones (id),
  decided_at    timestamptz NOT NULL DEFAULT now(),
  irrigate      boolean NOT NULL,
  duration_s    integer CHECK (duration_s BETWEEN 1 AND 900),
  water_l       numeric CHECK (water_l >= 0),
  reason        text,
  model_version text,
  command_id    uuid UNIQUE,                       -- set when a command was published
  CHECK (irrigate OR command_id IS NULL)
);

CREATE INDEX decisions_zone_decided_idx ON decisions (zone_id, decided_at DESC);

-- Events reported by the devices about commands (docs/contracts/events.schema.json).
CREATE TABLE irrigation_events (
  id          bigserial PRIMARY KEY,
  command_id  uuid NOT NULL,
  device_id   text NOT NULL REFERENCES devices (id),
  type        text NOT NULL CHECK (type IN ('irrigation_started', 'irrigation_finished', 'command_rejected')),
  ts          timestamptz NOT NULL,
  duration_s  integer CHECK (duration_s BETWEEN 0 AND 900),
  reason      text CHECK (reason IN ('expired', 'duplicate', 'invalid', 'busy')),
  received_at timestamptz NOT NULL,
  raw         jsonb NOT NULL,
  UNIQUE (command_id, type)
);

CREATE INDEX irrigation_events_device_ts_idx ON irrigation_events (device_id, ts DESC);

-- migrate:down
DROP TABLE irrigation_events;
DROP TABLE decisions;
DROP TABLE forecasts;
DROP TABLE readings;
DROP TABLE devices;
DROP TABLE zones;
