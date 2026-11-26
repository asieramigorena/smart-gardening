-- Zone for the test readings of tools/e2e_check, so they never mix with a real zone. It has no
-- valve: never send irrigation commands to it, and exclude it from Grafana panels and from the
-- decision service (see DB/README.md).
--
-- The e2e-check device is registered here (or moved here, if an earlier run of the check
-- registered it in another zone): the ingestion service only sets the zone of a device that
-- has none, so it would never move it by itself.

-- migrate:up
INSERT INTO zones (id, name) VALUES ('e2e-test', 'End-to-end test zone') ON CONFLICT DO NOTHING;
INSERT INTO devices (id, zone_id) VALUES ('e2e-check', 'e2e-test')
ON CONFLICT (id) DO UPDATE SET zone_id = EXCLUDED.zone_id;

-- migrate:down
DELETE FROM readings WHERE zone_id = 'e2e-test';
DELETE FROM devices WHERE id = 'e2e-check' AND zone_id = 'e2e-test'
  AND NOT EXISTS (SELECT FROM readings WHERE device_id = 'e2e-check')
  AND NOT EXISTS (SELECT FROM irrigation_events WHERE device_id = 'e2e-check');
UPDATE devices SET zone_id = NULL WHERE zone_id = 'e2e-test';
DELETE FROM zones WHERE id = 'e2e-test';
