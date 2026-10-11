-- Section 3.2 workloads. Only the source table changes between configurations.
-- psql:   psql -d energy_grid -v tbl=energy_readings_3h -f sql/08_workloads.sql
-- Python: src/s3_2_benchmark.py splits this file on the "-- name:" markers and
--         substitutes :"tbl" with the table being measured.

-- name: Q1
-- Average power_kw per CAT hour on 21 September (session time zone = Africa/Kigali).
SELECT time_bucket('1 hour', event_time) AS hour_cat,
       avg(power_kw)                     AS avg_power_kw
FROM :"tbl"
WHERE event_time >= '2026-09-21 00:00+02' AND event_time < '2026-09-22 00:00+02'
GROUP BY hour_cat
ORDER BY hour_cat;

-- name: Q2
-- Ten highest 15-minute network totals, 22-28 September; ties broken by earliest time,
-- so LIMIT 10 always returns exactly ten rows.
SELECT event_time, sum(power_kw) AS total_power_kw
FROM :"tbl"
WHERE event_time >= '2026-09-22 00:00+02' AND event_time < '2026-09-29 00:00+02'
GROUP BY event_time
ORDER BY total_power_kw DESC, event_time ASC
LIMIT 10;

-- name: Q3
-- Total energy per meter over the full historical period.
SELECT meter_id, sum(energy_kwh) AS total_energy_kwh
FROM :"tbl"
GROUP BY meter_id
ORDER BY meter_id;

-- name: Q4
-- One meter, 22-28 September.
SELECT sum(energy_kwh) AS total_energy_kwh,
       avg(power_kw)   AS avg_power_kw,
       max(power_kw)   AS peak_power_kw
FROM :"tbl"
WHERE meter_id = 1000000000
  AND event_time >= '2026-09-22 00:00+02' AND event_time < '2026-09-29 00:00+02';

-- name: Q5
-- Full scan.
SELECT count(*)        AS row_count,
       sum(energy_kwh) AS total_energy_kwh,
       avg(power_kw)   AS avg_power_kw,
       min(power_kw)   AS min_power_kw,
       max(power_kw)   AS max_power_kw
FROM :"tbl";
