# Smart Energy Grid Analytics — Assignment I (October 2026)

Advanced Big Data Analytics, AUCA. Group members and contributions: see
[GUIDE.md § 9.3](GUIDE.md#93-document-contributions).

The only supplied code is `smart_meter_simulator.py` (unchanged, v3.0). Everything
else — capture, MQTT, SQL, benchmarks, analysis, dashboards, forecast — is ours.
[GUIDE.md](GUIDE.md) explains every subsection, the reasoning, and what each
screenshot must show.

## Stack

| Component | Version used | How it runs |
|---|---|---|
| PostgreSQL | 17.x | Docker container `tsdb` (`timescale/timescaledb:latest-pg17`), host port **5433** |
| TimescaleDB | 2.30.x (needs ≥ 2.10) | inside the same container |
| EMQX | 6.3.x | Docker container `emqx`, MQTT 1883, dashboard http://localhost:18083 |
| Python | 3.13 | local virtual environment `.venv` |
| Dashboards | Plotly Dash 4 | `dashboard/app.py` (Raw on :8050, Aggregate on :8051) |

## Layout

```
smart_meter_simulator.py     supplied generator (do not edit)
docker-compose.yml           EMQX + PostgreSQL/TimescaleDB
.env.example                 connection settings template (copy to .env)
sql/                         00-10 numbered SQL files (run with psql)
src/                         Python programs, prefixed by section (s1_2_..., s8_...)
dashboard/                   Dash app + panel queries for Raw/Aggregate
results/                     recorded outputs: timings, checks, metrics, plans, figures
logs/                        publisher/subscriber logs
data/                        generated CSV (git-ignored; regenerate with s2_1)
```

## Setup (once)

```bash
cp .env.example .env            # then put your own password in .env
```

```bash
set -a; source .env; set +a
```

```bash
docker compose up -d
```

```bash
/opt/homebrew/bin/python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

All commands below assume the repository root, the `.env` variables exported
(`set -a; source .env; set +a`) and `psql` on the PATH. The Python programs also
read `.env` by themselves.

## Execution order

| Step | Section | Command |
|---|---|---|
| 1 | 1.1 database + extension + time zone | `psql -d postgres -f sql/00_create_database.sql` then `psql -d energy_grid -f sql/01_setup.sql` |
| 2 | 1.1 environment record | `.venv/bin/python src/s1_1_environment.py` |
| 3 | 1.2 tables | `psql -d energy_grid -f sql/02_tables.sql` |
| 4 | 1.2 meters | `.venv/bin/python src/s1_2_load_meters.py` |
| 5 | 1.3 pilot — terminal A (start first) | `.venv/bin/python src/s1_3_subscriber.py --truncate` |
| 6 | 1.3 pilot — terminal B | `.venv/bin/python src/s1_3_publisher.py` |
| 7 | 1.3 pilot report (after the subscriber stops) | `.venv/bin/python src/s1_3_pilot_report.py` |
| 8 | 2.1 capture + load (≈ 3–4 min) | `.venv/bin/python src/s2_1_capture_load.py` |
| 9 | 2.1 database checks | `psql -d energy_grid -f sql/03_load_checks.sql` |
| 10 | 2.2 data quality | `psql -d energy_grid -f sql/04_quality.sql` |
| 11 | 2.3 cleaning | `psql -d energy_grid -f sql/05_clean.sql` |
| 12 | 3.1 chunk tables + checks | `psql -d energy_grid -f sql/06_chunk_tables.sql` then `psql -d energy_grid -f sql/07_chunk_checks.sql` |
| 13 | 3.2 benchmark | `.venv/bin/python src/s3_2_benchmark.py --tag chunks` |
| 14 | 4.1 compressed tables | `.venv/bin/python src/s4_1_compress.py` then `psql -d energy_grid -f sql/09_compression_checks.sql` and `psql -d energy_grid -f sql/07_chunk_checks.sql` |
| 15 | 4.2 storage | `.venv/bin/python src/s4_2_storage.py` |
| 16 | 4.3 benchmark + comparison | `.venv/bin/python src/s3_2_benchmark.py --tag compression --tables energy_readings energy_time_compressed energy_meter_compressed` then `.venv/bin/python src/s4_3_compare.py` then `.venv/bin/python src/jobs.py resume energy_time_compressed energy_meter_compressed` |
| 17 | 5.1–5.3 analysis | `.venv/bin/python src/s5_analysis.py` |
| 18 | 5.4 anomalies | `.venv/bin/python src/s5_4_anomaly.py` |
| 19 | 6.1 continuous aggregates | `psql -d energy_grid -f sql/10_caggs.sql` then `.venv/bin/python src/s6_1_refresh.py` |
| 20 | 6.2 freshness test | `.venv/bin/python src/s6_2_freshness.py` |
| 21 | 7.1/7.2 dashboards | `.venv/bin/python dashboard/app.py --mode raw` and (2nd terminal) `.venv/bin/python dashboard/app.py --mode aggregate` |
| 22 | 7.3 verification | `.venv/bin/python src/s7_3_verify.py` |
| 23 | 7.4 timing | `.venv/bin/python src/jobs.py pause`, click **Run timing** on http://127.0.0.1:8050 then on http://127.0.0.1:8051, `.venv/bin/python src/s7_4_summary.py`, `.venv/bin/python src/jobs.py resume` |
| 24 | 8 forecast | `.venv/bin/python src/s8_forecast.py` |

## Pilot only (9.2.4)

After setup and steps 1–4, run in two terminals:

```bash
.venv/bin/python src/s1_3_subscriber.py --truncate
```

```bash
.venv/bin/python src/s1_3_publisher.py
```

When the subscriber prints `subscriber finished` (15 s after the last message):

```bash
.venv/bin/python src/s1_3_pilot_report.py
```

All four counts must be 2,000.

## Regenerating the full dataset

The 1.16 GB CSV is not committed. `src/s2_1_capture_load.py` regenerates it
from the unchanged generator (identical for every group) and loads it into an
empty `energy_readings` (re-run `sql/02_tables.sql` + `src/s1_2_load_meters.py`
first to start clean). `--skip-generate` reloads an existing CSV.

## Secrets

Only placeholders are committed (`.env.example`). `.env` is git-ignored.
