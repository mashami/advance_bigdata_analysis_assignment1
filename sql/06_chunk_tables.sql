-- Section 3.1  Two more hypertables with 3-hour and 1-week chunks, same columns/indexes.
-- psql -d energy_grid -f sql/06_chunk_tables.sql
\timing on
DROP TABLE IF EXISTS energy_readings_3h;
DROP TABLE IF EXISTS energy_readings_week;

CREATE TABLE energy_readings_3h   (LIKE energy_readings INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
CREATE TABLE energy_readings_week (LIKE energy_readings INCLUDING DEFAULTS INCLUDING CONSTRAINTS);

SELECT create_hypertable('energy_readings_3h', 'event_time',
                         chunk_time_interval => INTERVAL '3 hours', create_default_indexes => FALSE);
SELECT create_hypertable('energy_readings_week', 'event_time',
                         chunk_time_interval => INTERVAL '7 days', create_default_indexes => FALSE);

-- Copy cleaned rows in event_time order, then build the indexes once.
INSERT INTO energy_readings_3h   SELECT * FROM energy_readings ORDER BY event_time, meter_id;
INSERT INTO energy_readings_week SELECT * FROM energy_readings ORDER BY event_time, meter_id;

CREATE INDEX energy_readings_3h_time_idx         ON energy_readings_3h   (event_time DESC);
CREATE INDEX energy_readings_3h_meter_time_idx   ON energy_readings_3h   (meter_id, event_time DESC);
CREATE INDEX energy_readings_week_time_idx       ON energy_readings_week (event_time DESC);
CREATE INDEX energy_readings_week_meter_time_idx ON energy_readings_week (meter_id, event_time DESC);

VACUUM ANALYZE energy_readings;
VACUUM ANALYZE energy_readings_3h;
VACUUM ANALYZE energy_readings_week;
