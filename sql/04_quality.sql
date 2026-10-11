-- Section 2.2  Data-quality diagnostics on energy_readings BEFORE cleaning.
-- psql -d energy_grid -f sql/04_quality.sql
\timing on

-- One row per observed (meter_id, event_time) pair.
DROP TABLE IF EXISTS tmp_pairs;
CREATE TEMP TABLE tmp_pairs AS
SELECT meter_id, event_time,
       count(*)           AS copies,
       min(received_time) AS received_time
FROM energy_readings
GROUP BY meter_id, event_time;

-- The planned grid: 4,000 registered meters x 2,688 fifteen-minute slots.
DROP TABLE IF EXISTS tmp_grid;
CREATE TEMP TABLE tmp_grid AS
SELECT m.meter_id, s.event_time
FROM meters m
CROSS JOIN generate_series(TIMESTAMPTZ '2026-09-01 00:00+02',
                           TIMESTAMPTZ '2026-09-29 00:00+02' - INTERVAL '15 minutes',
                           INTERVAL '15 minutes') AS s(event_time);
ANALYZE tmp_pairs; ANALYZE tmp_grid;

WITH counts AS (
  SELECT
    (SELECT count(*) FROM tmp_grid)                                       AS planned,
    (SELECT count(*) FROM energy_readings)                                AS emitted,
    (SELECT count(*) FROM tmp_pairs)                                      AS unique_pairs,
    (SELECT sum(copies - 1) FROM tmp_pairs)                               AS extra_copies,
    (SELECT count(*) FROM tmp_pairs
       WHERE received_time > event_time + INTERVAL '15 minutes')          AS delayed_unique,
    (SELECT count(*) FROM tmp_grid g
       WHERE NOT EXISTS (SELECT 1 FROM tmp_pairs p
                         WHERE p.meter_id = g.meter_id
                           AND p.event_time = g.event_time))              AS missing_pairs,
    (SELECT count(*) FROM tmp_pairs p
       WHERE NOT EXISTS (SELECT 1 FROM tmp_grid g
                         WHERE g.meter_id = p.meter_id
                           AND g.event_time = p.event_time))              AS off_grid_pairs
)
SELECT *,
       round(100.0 * missing_pairs  / planned,      4) AS missing_pct,
       round(100.0 * extra_copies   / unique_pairs, 4) AS duplicate_pct,
       round(100.0 * delayed_unique / unique_pairs, 4) AS delayed_pct,
       round(100.0 * unique_pairs   / planned,      4) AS completeness_pct,
       unique_pairs + extra_copies = emitted           AS check_unique_plus_extra_eq_emitted,
       planned - unique_pairs = missing_pairs          AS check_planned_minus_unique_eq_missing
FROM counts;

-- Comparison with the reference counts from dataset_info()
WITH measured AS (
  SELECT (SELECT count(*) FROM energy_readings) AS emitted,
         (SELECT count(*) FROM tmp_pairs)       AS uniq,
         (SELECT sum(copies - 1) FROM tmp_pairs) AS dup,
         (SELECT count(*) FROM tmp_pairs WHERE received_time > event_time + INTERVAL '15 minutes') AS delayed,
         10752000 - (SELECT count(*) FROM tmp_pairs) AS missing
)
SELECT entry, measured, expected, measured = expected AS match
FROM measured,
LATERAL (VALUES ('planned_readings',        10752000, 10752000),
                ('emitted_readings',        emitted,  10696848),
                ('unique_readings',         uniq,     10643735),
                ('duplicate_readings',      dup,      53113),
                ('delayed_unique_readings', delayed,  213090),
                ('missing_readings',        missing,  108265)) AS v(entry, measured, expected);

-- Arrival delay distribution of unique readings (minutes after event_time)
SELECT CASE WHEN received_time = event_time + INTERVAL '15 minutes' THEN 'on time (15 min)'
            ELSE 'delayed 30-180 min' END AS arrival,
       count(*),
       min(extract(epoch FROM received_time - event_time) / 60) AS min_minutes,
       max(extract(epoch FROM received_time - event_time) / 60) AS max_minutes
FROM tmp_pairs GROUP BY 1;

-- Five meters with the most missing readings (2,688 planned per meter)
SELECT m.meter_id, m.region, m.customer_type,
       2688 - count(p.event_time) AS missing_readings
FROM meters m LEFT JOIN tmp_pairs p USING (meter_id)
GROUP BY m.meter_id, m.region, m.customer_type
ORDER BY missing_readings DESC, m.meter_id
LIMIT 5;
