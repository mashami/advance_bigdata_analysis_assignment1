-- Section 4.1 evidence: settings, paused policies, compressed-chunk lists.
-- psql -d energy_grid -f sql/09_compression_checks.sql
-- (Data checks for the compressed tables: run sql/07_chunk_checks.sql again.)

-- Compression / columnstore settings per table
SELECT * FROM timescaledb_information.hypertable_compression_settings
WHERE hypertable::text LIKE 'energy_%compressed';

-- Policies (scheduled = false while paused)
SELECT job_id, hypertable_name, proc_name, scheduled, config
FROM timescaledb_information.jobs
WHERE proc_name = 'policy_compression';

-- Compressed chunk ranges must be identical in both tables
SELECT range_start, range_end,
       bool_or(is_compressed) FILTER (WHERE hypertable_name = 'energy_time_compressed')  AS time_compressed,
       bool_or(is_compressed) FILTER (WHERE hypertable_name = 'energy_meter_compressed') AS meter_compressed
FROM timescaledb_information.chunks
WHERE hypertable_name IN ('energy_time_compressed', 'energy_meter_compressed')
GROUP BY range_start, range_end
ORDER BY range_start;

SELECT hypertable_name, count(*) AS chunks, count(*) FILTER (WHERE is_compressed) AS compressed_chunks
FROM timescaledb_information.chunks
WHERE hypertable_name IN ('energy_time_compressed', 'energy_meter_compressed')
GROUP BY hypertable_name;
