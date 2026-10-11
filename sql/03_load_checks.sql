-- Section 2.1.5  psql -d energy_grid -f sql/03_load_checks.sql
\timing on
SELECT count(*)                 AS rows,
       count(DISTINCT meter_id) AS distinct_meters,
       min(event_time)          AS earliest_event,
       max(event_time)          AS latest_event
FROM energy_readings;

-- Size across ALL chunks. table_bytes = heap data, index_bytes = both indexes,
-- toast_bytes is shown for completeness.
SELECT pg_size_pretty(table_bytes)  AS table_data,
       pg_size_pretty(index_bytes)  AS indexes,
       pg_size_pretty(toast_bytes)  AS toast,
       pg_size_pretty(total_bytes)  AS total,
       round(table_bytes / 1024.0^2, 1) AS table_mb,
       round(index_bytes / 1024.0^2, 1) AS index_mb,
       round(total_bytes / 1024.0^2, 1) AS total_mb
FROM hypertable_detailed_size('energy_readings');

SELECT count(*) AS chunks FROM show_chunks('energy_readings');
