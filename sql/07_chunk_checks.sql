-- Section 3.1 evidence: chunk interval, chunk count, chunk date range, indexes, data checks.
-- psql -d energy_grid -f sql/07_chunk_checks.sql
-- Also used for 4.1 (the compressed tables are included when they exist).
\timing on

SELECT d.hypertable_name, d.time_interval AS chunk_interval,
       count(c.chunk_name)  AS chunks,
       min(c.range_start)   AS first_chunk_start,
       max(c.range_end)     AS last_chunk_end
FROM timescaledb_information.dimensions d
JOIN timescaledb_information.chunks c USING (hypertable_schema, hypertable_name)
WHERE d.hypertable_name LIKE 'energy_%'
GROUP BY d.hypertable_name, d.time_interval
ORDER BY d.time_interval;

SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE tablename IN ('energy_readings', 'energy_readings_3h', 'energy_readings_week',
                    'energy_time_compressed', 'energy_meter_compressed')
ORDER BY tablename, indexname;

-- Data checks: must agree exactly on counts/dates and within 0.001 on sums.
DROP TABLE IF EXISTS tmp_checks;
CREATE TEMP TABLE tmp_checks (tbl text, rows bigint, meters bigint, first_event timestamptz,
                              last_event timestamptz, sum_power_kw float8, sum_energy_kwh float8);
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['energy_readings', 'energy_readings_3h', 'energy_readings_week',
                           'energy_time_compressed', 'energy_meter_compressed'] LOOP
    IF to_regclass(t) IS NOT NULL THEN
      EXECUTE format('INSERT INTO tmp_checks SELECT %L, count(*), count(DISTINCT meter_id),
                      min(event_time), max(event_time), sum(power_kw), sum(energy_kwh) FROM %I', t, t);
    END IF;
  END LOOP;
END $$;

SELECT c.*,
       c.rows = 10643735 AND c.meters = 4000                       AS counts_ok,
       c.first_event = b.first_event AND c.last_event = b.last_event AS dates_ok,
       abs(c.sum_power_kw - b.sum_power_kw)     AS power_diff,
       abs(c.sum_energy_kwh - b.sum_energy_kwh) AS energy_diff,
       abs(c.sum_power_kw - b.sum_power_kw) <= 0.001
         AND abs(c.sum_energy_kwh - b.sum_energy_kwh) <= 0.001     AS sums_ok
FROM tmp_checks c CROSS JOIN (SELECT * FROM tmp_checks WHERE tbl = 'energy_readings') b
ORDER BY c.tbl;
