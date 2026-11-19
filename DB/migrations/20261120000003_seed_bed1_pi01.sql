-- First zone and device of the MVP. New devices are also registered automatically by the
-- ingestion service when their first message arrives.

-- migrate:up
INSERT INTO zones (id, name) VALUES ('bed-1', 'Bed 1') ON CONFLICT DO NOTHING;
INSERT INTO devices (id, zone_id) VALUES ('pi-01', 'bed-1') ON CONFLICT DO NOTHING;

-- migrate:down
DELETE FROM devices WHERE id = 'pi-01';
DELETE FROM zones WHERE id = 'bed-1';
