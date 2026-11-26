-- Sample data for local Grafana development ONLY. Never applied in production.
-- Idempotent: safe to run multiple times.

INSERT INTO zones (id, name) VALUES
  ('bed-1', 'Bed 1'),
  ('bed-2', 'Bed 2')
ON CONFLICT (id) DO NOTHING;

-- pi-03 is registered but has never sent a reading (Device status shows "never" in red).
INSERT INTO devices (id, zone_id) VALUES
  ('pi-01', 'bed-1'),
  ('pi-02', 'bed-2'),
  ('pi-03', 'bed-1')
ON CONFLICT (id) DO NOTHING;

-- Readings every 5 min over the last 24h for pi-01 (bed-1, active: last reading 2 min ago)
INSERT INTO readings (device_id, zone_id, ts, received_at, soil_moisture_raw, soil_moisture, air_temp, air_humidity, light, raw)
SELECT
  'pi-01',
  'bed-1',
  t,
  t + interval '2 seconds',
  round(1800 + 400 * sin(extract(epoch FROM t) / 14400.0))::int,
  round((45 + 15 * sin(extract(epoch FROM t) / 14400.0))::numeric, 1),
  round((21 + 5 * sin(extract(epoch FROM t) / 13750.0))::numeric, 1),
  round((60 - 10 * sin(extract(epoch FROM t) / 13750.0))::numeric, 1),
  round(greatest(0, 80 * sin(extract(epoch FROM t) / 13750.0))::numeric, 1),
  '{"source":"dev-seed"}'::jsonb
FROM generate_series(now() - interval '2 minutes', now() - interval '24 hours', interval '-5 minutes') AS t
ON CONFLICT (device_id, ts) DO NOTHING;

-- Readings for pi-02 (bed-2, stale: last reading 25 min ago to test the red alert > 15 min)
INSERT INTO readings (device_id, zone_id, ts, received_at, soil_moisture_raw, soil_moisture, air_temp, air_humidity, light, raw)
SELECT
  'pi-02',
  'bed-2',
  t,
  t + interval '2 seconds',
  round(2200 + 300 * cos(extract(epoch FROM t) / 14400.0))::int,
  NULL, -- uncalibrated sensor: only in the raw (ADC) panel
  round((20 + 4 * cos(extract(epoch FROM t) / 13750.0))::numeric, 1),
  round((65 - 8 * cos(extract(epoch FROM t) / 13750.0))::numeric, 1),
  round(greatest(0, 70 * cos(extract(epoch FROM t) / 13750.0))::numeric, 1),
  '{"source":"dev-seed"}'::jsonb
FROM generate_series(now() - interval '25 minutes', now() - interval '24 hours', interval '-5 minutes') AS t
ON CONFLICT (device_id, ts) DO NOTHING;

-- Test readings in the e2e-test zone (as tools/e2e_check writes them). They must never appear in
-- any panel or variable. The zone and the e2e-check device come from the migrations.
INSERT INTO readings (device_id, zone_id, ts, received_at, soil_moisture_raw, soil_moisture, air_temp, air_humidity, light, raw)
SELECT 'e2e-check', 'e2e-test', t, t + interval '1 second', 4000, 99, 50, 99, 99, '{"source":"dev-seed"}'::jsonb
FROM generate_series(now() - interval '3 hours', now() - interval '1 minute', interval '1 hour') AS t
ON CONFLICT (device_id, ts) DO NOTHING;

-- Sample irrigation decisions (fixed UUIDs so re-running is idempotent).
-- 105 is rejected by the device (busy) and 106 never gets an event: neither counts as irrigated.
INSERT INTO decisions (id, zone_id, decided_at, irrigate, duration_s, water_l, reason, model_version, command_id) VALUES
  ('11111111-1111-1111-1111-111111111101', 'bed-1', now() - interval '20 hours', true,  300, 4.5, 'low_soil_moisture', 'v0.1', '22222222-2222-2222-2222-222222222201'),
  ('11111111-1111-1111-1111-111111111102', 'bed-2', now() - interval '16 hours', false, NULL, 0,  'rain_forecast',     'v0.1', NULL),
  ('11111111-1111-1111-1111-111111111103', 'bed-1', now() - interval '8 hours',  true,  180, 2.7, 'low_soil_moisture', 'v0.1', '22222222-2222-2222-2222-222222222202'),
  ('11111111-1111-1111-1111-111111111104', 'bed-1', now() - interval '4 hours',  false, NULL, 0,  'rain_forecast',     'v0.1', NULL),
  ('11111111-1111-1111-1111-111111111105', 'bed-2', now() - interval '2 hours',  true,  240, 3.6, 'low_soil_moisture', 'v0.1', '22222222-2222-2222-2222-222222222203'),
  ('11111111-1111-1111-1111-111111111106', 'bed-1', now() - interval '1 hour',   true,  120, 1.8, 'low_soil_moisture', 'v0.1', '22222222-2222-2222-2222-222222222204'),
  ('11111111-1111-1111-1111-111111111107', 'bed-2', now() - interval '30 hours', true,  200, 3.0, 'low_soil_moisture', 'v0.1', '22222222-2222-2222-2222-222222222205')
ON CONFLICT (id) DO NOTHING;

-- Sample irrigation events linked to the decisions above
INSERT INTO irrigation_events (command_id, device_id, type, ts, duration_s, reason, received_at, raw) VALUES
  ('22222222-2222-2222-2222-222222222201', 'pi-01', 'irrigation_started',  now() - interval '20 hours' + interval '5 seconds',  NULL, NULL,   now() - interval '20 hours' + interval '6 seconds',  '{"source":"dev-seed"}'::jsonb),
  ('22222222-2222-2222-2222-222222222201', 'pi-01', 'irrigation_finished', now() - interval '20 hours' + interval '305 seconds', 300, NULL,   now() - interval '20 hours' + interval '306 seconds', '{"source":"dev-seed"}'::jsonb),
  ('22222222-2222-2222-2222-222222222202', 'pi-01', 'irrigation_started',  now() - interval '8 hours'  + interval '5 seconds',  NULL, NULL,   now() - interval '8 hours'  + interval '6 seconds',  '{"source":"dev-seed"}'::jsonb),
  ('22222222-2222-2222-2222-222222222202', 'pi-01', 'irrigation_finished', now() - interval '8 hours'  + interval '185 seconds', 180, NULL,   now() - interval '8 hours'  + interval '186 seconds', '{"source":"dev-seed"}'::jsonb),
  ('22222222-2222-2222-2222-222222222203', 'pi-02', 'command_rejected',    now() - interval '2 hours'  + interval '3 seconds',  NULL, 'busy', now() - interval '2 hours'  + interval '4 seconds',  '{"source":"dev-seed"}'::jsonb),
  ('22222222-2222-2222-2222-222222222205', 'pi-02', 'irrigation_started',  now() - interval '30 hours' + interval '5 seconds',  NULL, NULL,   now() - interval '30 hours' + interval '6 seconds',  '{"source":"dev-seed"}'::jsonb),
  ('22222222-2222-2222-2222-222222222205', 'pi-02', 'irrigation_finished', now() - interval '30 hours' + interval '205 seconds', 200, NULL,   now() - interval '30 hours' + interval '206 seconds', '{"source":"dev-seed"}'::jsonb)
ON CONFLICT (command_id, type) DO NOTHING;