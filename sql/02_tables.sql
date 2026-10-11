-- Section 1.2  psql -d energy_grid -f sql/02_tables.sql
-- Re-runnable: drops and recreates the three base tables.
DROP TABLE IF EXISTS energy_live;
DROP TABLE IF EXISTS energy_readings CASCADE;
DROP TABLE IF EXISTS meters CASCADE;

-- Pilot MQTT stream lands here (regular PostgreSQL table, duplicates allowed).
CREATE TABLE energy_live (
    meter_id      BIGINT           NOT NULL,
    event_time    TIMESTAMPTZ      NOT NULL,
    received_time TIMESTAMPTZ      NOT NULL,
    power_kw      DOUBLE PRECISION NOT NULL,
    voltage_v     DOUBLE PRECISION NOT NULL,
    current_a     DOUBLE PRECISION NOT NULL,
    frequency_hz  DOUBLE PRECISION NOT NULL,
    energy_kwh    DOUBLE PRECISION NOT NULL
);

-- Full historical stream. No primary key: duplicates are allowed until cleaning (2.3).
CREATE TABLE energy_readings (
    meter_id      BIGINT           NOT NULL,
    event_time    TIMESTAMPTZ      NOT NULL,
    received_time TIMESTAMPTZ      NOT NULL,
    power_kw      DOUBLE PRECISION NOT NULL,
    voltage_v     DOUBLE PRECISION NOT NULL,
    current_a     DOUBLE PRECISION NOT NULL,
    frequency_hz  DOUBLE PRECISION NOT NULL,
    energy_kwh    DOUBLE PRECISION NOT NULL
);
SELECT create_hypertable('energy_readings', 'event_time',
                         chunk_time_interval    => INTERVAL '1 day',
                         create_default_indexes => FALSE);
-- The same two non-unique B-tree indexes are created on every reading hypertable (3.1).
CREATE INDEX energy_readings_time_idx       ON energy_readings (event_time DESC);
CREATE INDEX energy_readings_meter_time_idx ON energy_readings (meter_id, event_time DESC);

CREATE TABLE meters (
    meter_id      BIGINT PRIMARY KEY,
    region        TEXT             NOT NULL,
    customer_type TEXT             NOT NULL,
    base_power_kw DOUBLE PRECISION NOT NULL,
    power_factor  DOUBLE PRECISION NOT NULL
);

-- Evidence: column names and types, chunk interval
SELECT table_name, ordinal_position AS pos, column_name, data_type
FROM information_schema.columns
WHERE table_name IN ('energy_live', 'energy_readings', 'meters')
ORDER BY table_name, ordinal_position;

SELECT hypertable_name, column_name, time_interval AS chunk_interval
FROM timescaledb_information.dimensions
WHERE hypertable_name = 'energy_readings';
