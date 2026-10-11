-- Section 6.1  Three continuous aggregates over energy_readings, created WITH NO DATA
-- and with real-time aggregation disabled (materialized_only = true).
-- psql -d energy_grid -f sql/10_caggs.sql
-- Then run: python src/s6_1_refresh.py  (manual refresh + sizes, then policies)
DROP MATERIALIZED VIEW IF EXISTS cagg_energy_15m_region CASCADE;
DROP MATERIALIZED VIEW IF EXISTS cagg_energy_hour_region CASCADE;
DROP MATERIALIZED VIEW IF EXISTS cagg_energy_day_meter CASCADE;

-- 15-minute bucket x region (inner join to the meters metadata table)
CREATE MATERIALIZED VIEW cagg_energy_15m_region
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT time_bucket(INTERVAL '15 minutes', r.event_time) AS bucket,
       m.region,
       sum(r.energy_kwh) AS sum_energy_kwh,
       sum(r.power_kw)   AS sum_power_kw,
       count(*)          AS reading_count
FROM energy_readings r
INNER JOIN meters m ON r.meter_id = m.meter_id
GROUP BY bucket, m.region
WITH NO DATA;

-- CAT hour x region
CREATE MATERIALIZED VIEW cagg_energy_hour_region
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT time_bucket(INTERVAL '1 hour', r.event_time, 'Africa/Kigali') AS bucket,
       m.region,
       sum(r.energy_kwh) AS sum_energy_kwh,
       sum(r.power_kw)   AS sum_power_kw,
       count(*)          AS reading_count
FROM energy_readings r
INNER JOIN meters m ON r.meter_id = m.meter_id
GROUP BY bucket, m.region
WITH NO DATA;

-- CAT day x meter (time_bucket with a time zone aligns buckets to CAT midnight)
CREATE MATERIALIZED VIEW cagg_energy_day_meter
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT time_bucket(INTERVAL '1 day', event_time, 'Africa/Kigali') AS bucket,
       meter_id,
       sum(energy_kwh) AS sum_energy_kwh,
       sum(power_kw)   AS sum_power_kw,
       count(*)        AS reading_count
FROM energy_readings
GROUP BY bucket, meter_id
WITH NO DATA;

-- Evidence: definitions and materialized_only setting
SELECT view_name, materialized_only, materialization_hypertable_name
FROM timescaledb_information.continuous_aggregates ORDER BY view_name;
SELECT view_name, view_definition
FROM timescaledb_information.continuous_aggregates ORDER BY view_name;
