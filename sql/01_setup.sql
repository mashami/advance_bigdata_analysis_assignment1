-- Section 1.1  psql -d energy_grid -f sql/01_setup.sql
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Evidence for the screenshots
SELECT current_database()                 AS database,
       current_setting('TimeZone')        AS session_time_zone,
       now()                              AS now_cat;
SELECT extname, extversion FROM pg_extension WHERE extname = 'timescaledb';
SELECT version();
SELECT name, setting, unit
FROM pg_settings
WHERE name IN ('shared_buffers', 'work_mem', 'max_parallel_workers_per_gather', 'jit',
               'effective_cache_size', 'max_worker_processes', 'timescaledb.max_background_workers')
ORDER BY name;
-- CAT-midnight daily buckets: the third argument makes time_bucket align to Africa/Kigali.
SELECT time_bucket('1 day', TIMESTAMPTZ '2026-09-21 13:45+02', 'Africa/Kigali') AS cat_day_bucket;
