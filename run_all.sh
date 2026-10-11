#!/bin/bash
# Run the whole assignment end to end, non-interactively (about 20-30 minutes).
#   ./run_all.sh            RECREATES the energy_grid database and runs Sections 1-8
# Output of every step goes to the screen and to logs/run_all.log.
# Dashboard timing (7.4) needs browser clicks and is not included: start the two
# dashboards and click "Run timing" on each, then run src/s7_4_summary.py.
set -eo pipefail
cd "$(dirname "$0")"
set -a; [ -f .env ] && source .env; set +a
PY=.venv/bin/python
PSQL="psql -q -P pager=off -d energy_grid"
mkdir -p logs
exec > >(tee logs/run_all.log) 2>&1

section() { printf '\n\033[1;33m===== %s =====\033[0m  %s\n' "$1" "$(date '+%Y-%m-%d %H:%M:%S %Z')"; }

section "Setup: containers"
docker compose up -d
until docker exec tsdb pg_isready -U postgres >/dev/null 2>&1; do sleep 2; done

section "1.1 Database, extension, time zone, environment"
psql -q -d postgres -f sql/00_create_database.sql
$PSQL -f sql/01_setup.sql
$PY src/s1_1_environment.py

section "1.2 Tables and meters"
$PSQL -f sql/02_tables.sql
$PY src/s1_2_load_meters.py

section "1.3 MQTT pilot"
$PY src/s1_3_subscriber.py --truncate --idle 8 > logs/subscriber.out 2>&1 &
SUB=$!
sleep 3
$PY src/s1_3_publisher.py
wait $SUB
cat logs/subscriber.out
$PY src/s1_3_pilot_report.py

section "2.1 Full capture and load"
$PY src/s2_1_capture_load.py | grep -v 'loaded .*% of CSV'
$PSQL -f sql/03_load_checks.sql

section "2.2 Data quality"
$PSQL -f sql/04_quality.sql 2>&1 | grep -vE '^(SELECT|DROP|ANALYZE)|NOTICE'

section "2.3 Cleaning"
$PSQL -f sql/05_clean.sql 2>&1 | grep -v NOTICE

section "3.1 Chunk-interval tables"
$PSQL -f sql/06_chunk_tables.sql > /dev/null
$PSQL -f sql/07_chunk_checks.sql 2>&1 | grep -v NOTICE

section "3.2 Chunk benchmark"
$PY src/s3_2_benchmark.py --tag chunks

section "4.1-4.2 Compression"
$PY src/s4_1_compress.py
$PSQL -f sql/09_compression_checks.sql
$PSQL -f sql/07_chunk_checks.sql 2>&1 | grep -v NOTICE
$PY src/s4_2_storage.py

section "4.3 Compression benchmark"
$PY src/jobs.py pause > /dev/null
$PY src/s3_2_benchmark.py --tag compression --tables energy_readings energy_time_compressed energy_meter_compressed
$PY src/s4_3_compare.py
$PY src/jobs.py resume

section "5 Analysis and anomalies"
$PY src/s5_analysis.py
$PY src/s5_4_anomaly.py

section "6 Continuous aggregates and freshness test"
$PSQL -f sql/10_caggs.sql 2>&1 | grep -v NOTICE
$PY src/s6_1_refresh.py
$PY src/s6_2_freshness.py

section "7.3 Dashboard data verification"
$PY src/s7_3_verify.py

section "8 Forecast"
$PY src/s8_forecast.py 2>/dev/null

section "Done"
echo "Results: results/   Charts: results/figures/   Log: logs/run_all.log"
