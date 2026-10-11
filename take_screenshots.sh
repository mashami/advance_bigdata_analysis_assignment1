#!/bin/bash
# Guided re-run of the whole assignment for the report screenshots.
#
#   ./take_screenshots.sh              # start from the beginning (RECREATES the database)
#   ./take_screenshots.sh 3_2_timings  # resume from a given screenshot
#
# For every screenshot the script runs the real command, then pauses. Make sure
# the Terminal window, the menu-bar clock and the Dock are visible, press
# Cmd+Shift+3 (full screen), wait for the thumbnail to disappear, then press
# Enter. The newest screenshot is moved to report/screenshots/<name>.png.
# Type s + Enter to skip a screenshot, r + Enter to retake it.
set -u
cd "$(dirname "$0")"
set -a; [ -f .env ] && source .env; set +a
PY=.venv/bin/python
PSQL="psql -q -P pager=off -d energy_grid"
OUT=report/screenshots
mkdir -p "$OUT" logs

SHOTDIR=$(defaults read com.apple.screencapture location 2>/dev/null || echo "$HOME/Desktop")
SHOTDIR="${SHOTDIR/#\~/$HOME}"
START="${1:-}"
STARTED=$([ -z "$START" ] && echo 1 || echo 0)

header() {   # header NAME "what this screenshot shows"
  clear
  printf '\033[1;33m=== Screenshot %s: %s ===\033[0m   %s\n\n' "$1" "$2" "$(date '+%a %d %b %Y %H:%M:%S %Z')"
}

run() {      # run "command": print it like a prompt, then execute it
  printf '\033[1;32m$ %s\033[0m\n' "$1"
  eval "$1"
}

capture() {  # capture NAME
  local name=$1 marker newest answer
  while true; do
    marker=$(mktemp); sleep 1
    printf '\n\033[1;36m>> Press Cmd+Shift+3 now for %s.png, then Enter (s = skip): \033[0m' "$name"
    read -r answer
    [ "$answer" = "s" ] && { rm -f "$marker"; echo "skipped $name"; return; }
    sleep 2
    newest=$(find "$SHOTDIR" -maxdepth 1 -name '*.png' -newer "$marker" -print0 2>/dev/null \
             | xargs -0 ls -t 2>/dev/null | head -1)
    rm -f "$marker"
    if [ -n "$newest" ]; then
      mv "$newest" "$OUT/$name.png"
      printf '\033[1;32msaved %s/%s.png\033[0m  (Enter = next, r = retake) ' "$OUT" "$name"
      read -r answer
      [ "$answer" = "r" ] && continue
      return
    fi
    printf '\033[1;31mno new screenshot found in %s - try again\033[0m\n' "$SHOTDIR"
  done
}

step() {     # step NAME: decides whether this step runs (resume support)
  [ "$STARTED" = 1 ] && return 0
  [ "$1" = "$START" ] && { STARTED=1; return 0; }
  return 1
}

pilot() {    # pilot MODE: subscriber in background, publisher in foreground
  $PY src/s1_3_subscriber.py --truncate --idle 8 --mode "$1" > logs/subscriber_screen.out 2>&1 &
  SUB_PID=$!
  sleep 3
}

# ---------------------------------------------------------------- Section 1
if step 1_1_broker; then
  echo "This step DROPS and recreates the energy_grid database. Ctrl+C to stop, Enter to continue."; read -r
  header 1_1_broker "EMQX and TimescaleDB containers running"
  run "docker compose up -d && docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'"
  run "docker exec emqx emqx ctl status"
  capture 1_1_broker
fi
if step 1_1_database; then
  header 1_1_database "database, extension, session time zone"
  run "psql -q -d postgres -f sql/00_create_database.sql"
  run "$PSQL -f sql/01_setup.sql"
  capture 1_1_database
fi
if step 1_2_tables; then
  header 1_2_tables "three tables, column types, 1-day chunk interval"
  run "$PSQL -f sql/02_tables.sql"
  capture 1_2_tables
fi
if step 1_2_meters; then
  header 1_2_meters "4,000 rows in meters"
  run "$PY src/s1_2_load_meters.py"
  run "$PSQL -c 'SELECT count(*) AS meters FROM meters;'"
  capture 1_2_meters
fi
if step 1_3_fault_metrics; then
  header 1_3_fault_metrics "first pilot design (per-message commit) loses messages"
  run "docker restart emqx >/dev/null && sleep 8 && echo 'EMQX restarted (metrics reset)'"
  pilot per-message
  run "$PY src/s1_3_publisher.py"
  wait $SUB_PID
  run "tail -9 logs/subscriber_screen.out"
  run "docker exec emqx emqx ctl broker metrics | grep -E 'messages.received|messages.delivered|delivery.dropped.queue_full'"
  capture 1_3_fault_metrics
fi
if step 1_3_publisher; then
  header 1_3_publisher "fixed pilot: publisher acknowledged count"
  pilot batched
  run "$PY src/s1_3_publisher.py"
  capture 1_3_publisher
fi
if step 1_3_subscriber; then
  header 1_3_subscriber "fixed pilot: subscriber received count"
  [ -n "${SUB_PID:-}" ] && wait $SUB_PID
  run "cat logs/subscriber_screen.out"
  capture 1_3_subscriber
fi
if step 1_3_database; then
  header 1_3_database "four pilot counts, stored rows and sample rows"
  run "$PY src/s1_3_pilot_report.py"
  capture 1_3_database
fi

# ---------------------------------------------------------------- Section 2
if step 2_1_generation; then
  header 2_1_generation "full capture to CSV and COPY load (about 4 minutes)"
  run "$PY src/s2_1_capture_load.py 2>&1 | grep -v 'loaded .*% of CSV'"
  echo; echo "Scroll so the generation lines are visible for 2_1_generation."
  capture 2_1_generation
  echo "Now make the load lines and the count check visible for 2_1_load."
  capture 2_1_load
fi
if step 2_1_checks; then
  header 2_1_checks "meters, date range, table/index/total size"
  run "$PSQL -f sql/03_load_checks.sql"
  capture 2_1_checks
fi
if step 2_2_quality; then
  header 2_2_quality "data-quality diagnostics before cleaning (about 2 minutes)"
  run "$PSQL -f sql/04_quality.sql 2>&1 | grep -vE '^(SELECT|DROP|ANALYZE|NOTICE)'"
  capture 2_2_quality
fi
if step 2_3_cleaning; then
  header 2_3_cleaning "duplicate removal, before and after"
  run "$PSQL -f sql/05_clean.sql 2>&1 | grep -vE 'NOTICE'"
  capture 2_3_cleaning
fi

# ---------------------------------------------------------------- Section 3
if step 3_1_chunks; then
  header 3_1_chunks "3-hour and weekly hypertables: chunks, indexes, data checks"
  run "$PSQL -f sql/06_chunk_tables.sql > /dev/null && echo 'energy_readings_3h and energy_readings_week created and loaded'"
  run "$PSQL -f sql/07_chunk_checks.sql 2>&1 | grep -v NOTICE"
  capture 3_1_chunks
fi
if step 3_2_timings; then
  header 3_2_timings "Q1-Q5 on three chunk intervals (1 warm-up + 5 runs)"
  run "$PY src/s3_2_benchmark.py --tag chunks"
  capture 3_2_timings
fi
if step 3_2_plan; then
  header 3_2_plan "execution plan with actual timing and buffers, per configuration (Q2)"
  for t in energy_readings_3h energy_readings energy_readings_week; do
    run "grep -E 'Limit|Sort |HashAggregate|GroupAggregate|Append|Buffers|Execution Time' results/plans/chunks_Q2_$t.txt | head -8; echo \"chunks scanned: \$(grep -cE 'on _hyper_[0-9]+_[0-9]+_chunk ' results/plans/chunks_Q2_$t.txt)\""
  done
  capture 3_2_plan
fi

# ---------------------------------------------------------------- Section 4
if step 4_1_config; then
  header 4_1_config "compressed tables, settings, paused policies, compressed chunks"
  run "$PY src/s4_1_compress.py | tail -8"
  run "$PSQL -f sql/09_compression_checks.sql | grep -vE '^ 2026-09-(0[3-9]|1[0-9]|2[0-6])'"
  capture 4_1_config
fi
if step 4_1_checks; then
  header 4_1_checks "data checks repeated on the compressed tables"
  run "$PSQL -f sql/07_chunk_checks.sql 2>&1 | grep -v NOTICE"
  capture 4_1_checks
fi
if step 4_2_storage; then
  header 4_2_storage "storage, ratio, saving, compression time"
  run "$PY src/s4_2_storage.py"
  capture 4_2_storage
fi
if step 4_3_results; then
  header 4_3_results "Q1-Q5 on compressed tables, comparison of all five configurations"
  run "$PY src/jobs.py pause >/dev/null && echo 'background jobs paused'"
  run "$PY src/s3_2_benchmark.py --tag compression --tables energy_readings energy_time_compressed energy_meter_compressed | sed -n '/Warm-cache/,\$p'"
  run "$PY src/s4_3_compare.py"
  capture 4_3_results
fi
if step 4_3_plans; then
  header 4_3_plans "actual plans on both compressed tables (Q1)"
  for t in energy_time_compressed energy_meter_compressed; do
    run "grep -E 'ColumnarScan|DecompressChunk|Batches|Buffers|Index Cond|Execution Time' results/plans/compression_Q1_$t.txt | head -7"
  done
  capture 4_3_plans
fi
if step 4_3_policies; then
  header 4_3_policies "compression policies resumed"
  run "$PY src/jobs.py resume energy_time_compressed energy_meter_compressed | cut -c1-120"
  capture 4_3_policies
fi

# ---------------------------------------------------------------- Section 5
if step 5_1_analysis; then
  header 5_1_analysis "daily and weekly energy, top 3 demand intervals"
  run "$PY src/s5_analysis.py | sed -n '/=== 5.1/,/=== 5.2/p' | grep -v '=== 5.2'"
  run "open results/figures/s5_1_daily_weekly_energy.png"
  echo "Place the chart window next to the terminal."
  capture 5_1_analysis
fi
if step 5_2_groups; then
  header 5_2_groups "regions and customer types, per-meter energy"
  run "$PY src/s5_analysis.py | sed -n '/=== 5.2/,/=== 5.3/p' | grep -v '=== 5.3'"
  capture 5_2_groups
fi
if step 5_3_completeness; then
  header 5_3_completeness "completeness and the two 21 September totals"
  run "$PY src/s5_analysis.py | sed -n '/=== 5.3/,/Readings of 21 Sep/p'"
  capture 5_3_completeness
fi
if step 5_4_detection; then
  header 5_4_detection "anomaly rule, flagged readings, comparison with 40 events"
  run "$PY src/s5_4_anomaly.py"
  capture 5_4_detection
fi

# ---------------------------------------------------------------- Section 6
if step 6_1_views; then
  header 6_1_views "three continuous aggregate views (no data, materialized only)"
  run "$PSQL -f sql/10_caggs.sql 2>&1 | grep -v NOTICE"
  capture 6_1_views
fi
if step 6_1_refresh; then
  header 6_1_refresh "manual refresh, sizes, policies, sample rows"
  run "$PY src/s6_1_refresh.py"
  run "$PY src/jobs.py status | cut -c1-120"
  capture 6_1_refresh
fi
if step 6_2_setup; then
  header 6_2_setup "freshness test: definitions, chosen times, inserts"
  run "$PY src/s6_2_freshness.py | tee logs/freshness_screen.out | head -20"
  capture 6_2_setup
fi
if step 6_2_stages; then
  header 6_2_stages "freshness test: raw vs aggregate at each stage"
  run "sed -n '/Stage 2/,\$p' logs/freshness_screen.out"
  capture 6_2_stages
fi

# ---------------------------------------------------------------- Section 7
if step 7_1_raw; then
  header 7_1_raw "Raw dashboard"
  pkill -f dashboard/app.py 2>/dev/null; sleep 1
  run "$PY dashboard/app.py --mode raw > logs/dash_raw.log 2>&1 &"
  run "$PY dashboard/app.py --mode aggregate > logs/dash_agg.log 2>&1 &"
  sleep 6
  run "open http://127.0.0.1:8050"
  echo "Wait until all five panels are drawn; keep the clock and Dock visible."
  capture 7_1_raw
fi
if step 7_2_aggregate; then
  header 7_2_aggregate "Aggregate dashboard"
  run "open http://127.0.0.1:8051"
  run "grep -n 'FROM cagg' dashboard/queries.py"
  echo "Show the Aggregate dashboard (and this terminal with the cagg queries if it fits)."
  capture 7_2_aggregate
fi
if step 7_3_paired; then
  header 7_3_paired "Raw vs Aggregate numerical check (22-29 Sep, meter 1000000000)"
  run "$PY src/s7_3_verify.py"
  echo "Put both dashboard tabs/windows side by side with this terminal if possible."
  capture 7_3_paired
fi
if step 7_4_timing; then
  header 7_4_timing "dashboard timing, 1 warm-up + 10 refreshes per mode"
  run "$PY src/jobs.py pause >/dev/null && echo 'background jobs paused'"
  run "mv results/dashboard_timings.csv results/dashboard_timings_old.csv 2>/dev/null; true"
  echo "In the browser: on the Raw tab (8050) click 'Run timing (1 warm-up + 10)'"
  echo "and wait for '-- timing run complete'; then do the same on the Aggregate tab (8051)."
  read -rp "Press Enter when BOTH runs are complete... "
  run "$PY src/s7_4_summary.py"
  capture 7_4_timing
  run "$PY src/jobs.py resume >/dev/null && echo 'background jobs resumed'"
fi

# ---------------------------------------------------------------- Section 8
if step 8_1_dataset; then
  header 8_1_dataset "hourly regional dataset, features, leakage check, splits"
  run "$PY src/s8_forecast.py 2>/dev/null | sed -n '/=== 8.1/,/=== 8.2/p' | grep -v '=== 8.2'"
  capture 8_1_dataset
fi
if step 8_2_baseline; then
  header 8_2_baseline "previous-day baseline"
  run "$PY src/s8_forecast.py 2>/dev/null | sed -n '/=== 8.2/,/=== 8.3/p' | grep -v '=== 8.3'"
  capture 8_2_baseline
fi
if step 8_3_model; then
  header 8_3_model "Ridge configurations, validation table, selected model, test predictions"
  run "$PY src/s8_forecast.py 2>/dev/null | sed -n '/=== 8.3/,/=== 8.4/p' | grep -v '=== 8.4'"
  capture 8_3_model
fi
if step 8_4_metrics; then
  header 8_4_metrics "baseline vs model metrics, peak hours, forecast plot"
  run "$PY src/s8_forecast.py 2>/dev/null | sed -n '/=== 8.4/,\$p'"
  run "open results/figures/s8_forecast_region.png"
  echo "Place the plot window next to the terminal."
  capture 8_4_metrics
fi

# ---------------------------------------------------------------- Section 9
if step 9_2_pilot; then
  header 9_2_pilot "README pilot instructions: four counts = 2,000"
  pilot batched
  run "$PY src/s1_3_publisher.py | tail -3"
  wait $SUB_PID
  run "$PY src/s1_3_pilot_report.py | head -8"
  capture 9_2_pilot
fi

echo; echo "Done. Screenshots in $OUT:"; ls "$OUT"
