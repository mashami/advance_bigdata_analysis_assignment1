-- Section 2.3  Remove exact duplicate copies from energy_readings (repeatable).
-- psql -d energy_grid -f sql/05_clean.sql
\timing on

-- BEFORE
SELECT 'before' AS stage, count(*) AS rows, sum(energy_kwh) AS sum_energy_kwh
FROM energy_readings;

-- Duplicate pairs and whether their copies conflict in any of the six value columns.
DROP TABLE IF EXISTS tmp_dup_pairs;
CREATE TEMP TABLE tmp_dup_pairs AS
SELECT meter_id, event_time, count(*) AS copies,
       count(DISTINCT (received_time, power_kw, voltage_v, current_a,
                       frequency_hz, energy_kwh))           AS distinct_versions
FROM energy_readings
GROUP BY meter_id, event_time
HAVING count(*) > 1;

SELECT count(*)                                    AS duplicate_pairs,
       sum(copies - 1)                             AS extra_copies,
       count(*) FILTER (WHERE distinct_versions > 1) AS conflicting_pairs
FROM tmp_dup_pairs;

-- Investigate conflicts (expected: no rows). Conflicting pairs are NOT deleted below.
SELECT r.*
FROM energy_readings r
JOIN tmp_dup_pairs d USING (meter_id, event_time)
WHERE d.distinct_versions > 1
ORDER BY r.meter_id, r.event_time
LIMIT 20;

-- Delete every copy after the first among rows identical in all eight columns,
-- restricted to pairs whose copies agree. (tableoid, ctid) identifies a physical
-- row inside its chunk, so the statement works on a hypertable.
BEGIN;
WITH ranked AS (
  SELECT r.tableoid AS chunk, r.ctid,
         row_number() OVER (PARTITION BY r.meter_id, r.event_time, r.received_time, r.power_kw,
                                         r.voltage_v, r.current_a, r.frequency_hz, r.energy_kwh
                            ORDER BY r.ctid) AS copy_no
  FROM energy_readings r
  JOIN tmp_dup_pairs d USING (meter_id, event_time)
  WHERE d.distinct_versions = 1
)
DELETE FROM energy_readings e
USING ranked
WHERE e.tableoid = ranked.chunk AND e.ctid = ranked.ctid AND ranked.copy_no > 1;
COMMIT;

VACUUM ANALYZE energy_readings;

-- AFTER
SELECT 'after' AS stage, count(*) AS rows, sum(energy_kwh) AS sum_energy_kwh,
       count(*) = 10643735 AS matches_expected_unique_readings
FROM energy_readings;

SELECT count(*) AS remaining_duplicate_pairs
FROM (SELECT 1 FROM energy_readings GROUP BY meter_id, event_time HAVING count(*) > 1) d;
