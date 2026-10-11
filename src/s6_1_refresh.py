"""Section 6.1: refresh each empty continuous aggregate over the whole period,
measure refresh seconds and materialized size, THEN add refresh policies.

    psql -d energy_grid -f sql/10_caggs.sql
    python src/s6_1_refresh.py
"""
import json
import time

from common import RESULTS, cat_text, connect, print_table, write_csv

VIEWS = ["cagg_energy_15m_region", "cagg_energy_hour_region", "cagg_energy_day_meter"]
START, END = "2026-09-01 00:00+02", "2026-09-29 00:00+02"


def main() -> None:
    conn = connect(autocommit=True)  # CALL refresh_continuous_aggregate cannot run in a transaction
    out = []
    for view in VIEWS:
        before = conn.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
        start_cat, t0 = cat_text(), time.perf_counter()
        conn.execute("CALL refresh_continuous_aggregate(%s, %s::timestamptz, %s::timestamptz)",
                     (view, START, END))
        seconds = time.perf_counter() - t0
        schema, table = conn.execute(
            "SELECT materialization_hypertable_schema, materialization_hypertable_name "
            "FROM timescaledb_information.continuous_aggregates WHERE view_name = %s",
            (view,)).fetchone()
        tbl, idx, toast, total = conn.execute(
            "SELECT table_bytes, index_bytes, toast_bytes, total_bytes "
            "FROM hypertable_detailed_size(%s)", (f"{schema}.{table}",)).fetchone()
        rows = conn.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
        out.append({"view": view, "rows_before": before, "rows_after": rows,
                    "refresh_start_cat": start_cat, "refresh_seconds": round(seconds, 3),
                    "data_mb": round((tbl + toast) / 1024**2, 2),
                    "index_mb": round(idx / 1024**2, 2), "total_mb": round(total / 1024**2, 2)})
        print(f"[{cat_text()}] refreshed {view}: {rows:,} rows in {seconds:.1f} s")

    print()
    print_table([tuple(r.values()) for r in out], list(out[0].keys()))
    write_csv(RESULTS / "s6_1_cagg_refresh.csv", out)

    # Policies only AFTER the measurements: refresh [now - 3 days, now - 1 hour] every 15 min.
    for view in VIEWS:
        conn.execute("SELECT remove_continuous_aggregate_policy(%s, if_exists => true)", (view,))
        conn.execute("SELECT add_continuous_aggregate_policy(%s, start_offset => INTERVAL '3 days',"
                     " end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '15 minutes')",
                     (view,))
    jobs = conn.execute("""
        SELECT j.job_id, j.hypertable_name, j.schedule_interval, j.config::text, j.scheduled,
               s.last_run_status, s.next_start
        FROM timescaledb_information.jobs j
        LEFT JOIN timescaledb_information.job_stats s USING (job_id)
        WHERE j.proc_name = 'policy_refresh_continuous_aggregate'
        ORDER BY j.job_id""").fetchall()
    print("\nRefresh policies:")
    print_table(jobs, ["job_id", "view", "schedule", "config", "scheduled", "last_status", "next_start"])

    print("\nSample rows:")
    for view in VIEWS:
        sample = conn.execute(f"SELECT * FROM {view} ORDER BY bucket LIMIT 3").fetchall()
        print_table(sample, ["bucket", "key", "sum_energy_kwh", "sum_power_kw", "reading_count"])
        print()
    conn.close()


if __name__ == "__main__":
    main()
