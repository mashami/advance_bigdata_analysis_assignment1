"""Section 6.2: freshness and delayed arrivals with a small test hypertable.

    python src/s6_2_freshness.py
Creates freshness_test (event_time, energy_kwh) and the hourly continuous
aggregate freshness_hourly (WITH NO DATA, materialized_only = true, no policy),
then walks through the four stages of the worked timeline using hours relative
to the actual test time:
    H      = start of the most recent completed CAT hour
    older  = H - 48 hours
"""
from datetime import timedelta

from common import cat_text, connect, now_cat, print_table

SETUP = """
DROP MATERIALIZED VIEW IF EXISTS freshness_hourly;
DROP TABLE IF EXISTS freshness_test;
CREATE TABLE freshness_test (event_time TIMESTAMPTZ NOT NULL, energy_kwh DOUBLE PRECISION NOT NULL);
SELECT create_hypertable('freshness_test', 'event_time', chunk_time_interval => INTERVAL '1 day');
CREATE MATERIALIZED VIEW freshness_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT time_bucket(INTERVAL '1 hour', event_time, 'Africa/Kigali') AS bucket,
       sum(energy_kwh) AS sum_energy_kwh, count(*) AS row_count
FROM freshness_test GROUP BY bucket
WITH NO DATA;
"""


def main() -> None:
    conn = connect(autocommit=True)
    for statement in filter(str.strip, SETUP.split(";")):
        conn.execute(statement)

    test_time = now_cat()
    H = test_time.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    older = H - timedelta(hours=48)
    inserts = [(older + timedelta(minutes=10), 1.0),
               (older + timedelta(minutes=25), 0.5),
               (H + timedelta(minutes=10), 2.0)]
    print(f"test time (CAT)      : {cat_text(test_time)}")
    print(f"H (last full hour)   : {H:%Y-%m-%d %H:%M %Z}")
    print(f"H - 48 h (older hour): {older:%Y-%m-%d %H:%M %Z}")
    print("chosen event_times   : " + ", ".join(f"{t:%d %b %H:%M} -> {v} kWh" for t, v in inserts))
    print("view: materialized_only =",
          conn.execute("SELECT materialized_only FROM timescaledb_information.continuous_aggregates"
                       " WHERE view_name = 'freshness_hourly'").fetchone()[0],
          "| refresh policies on this view:",
          conn.execute("SELECT count(*) FROM timescaledb_information.jobs "
                       "WHERE hypertable_name = 'freshness_hourly'").fetchone()[0])

    def show(stage: str) -> None:
        raw = conn.execute(
            "SELECT time_bucket('1 hour', event_time, 'Africa/Kigali') AS hour, sum(energy_kwh), count(*)"
            " FROM freshness_test WHERE event_time >= %s AND event_time < %s"
            "    OR event_time >= %s AND event_time < %s GROUP BY 1 ORDER BY 1",
            (older, older + timedelta(hours=1), H, H + timedelta(hours=1))).fetchall()
        agg = {r[0]: r for r in conn.execute(
            "SELECT bucket, sum_energy_kwh, row_count FROM freshness_hourly"
            " WHERE bucket IN (%s, %s) ORDER BY bucket", (older, H)).fetchall()}
        print(f"\n--- {stage}  [{cat_text()}]")
        rows = []
        for label, hour in (("older hour", older), ("hour H", H)):
            raw_row = next((r for r in raw if r[0] == hour), None)
            agg_row = agg.get(hour)
            rows.append((label, f"{hour:%d %b %H:%M}",
                         f"{raw_row[1]} kWh, {raw_row[2]} row(s)" if raw_row else "no rows",
                         f"{agg_row[1]} kWh, {agg_row[2]} row(s)" if agg_row else "no bucket returned"))
        print_table(rows, ["hour", "bucket", "raw table", "aggregate"])

    conn.execute("INSERT INTO freshness_test VALUES (%s, %s)", inserts[0])
    conn.execute("CALL refresh_continuous_aggregate('freshness_hourly', %s::timestamptz, %s::timestamptz)",
                 (older, older + timedelta(hours=1)))
    show("Stage 1: 1 kWh inserted in older hour, refreshed older hour only, real-time OFF")

    conn.execute("INSERT INTO freshness_test VALUES (%s, %s)", inserts[1])
    conn.execute("INSERT INTO freshness_test VALUES (%s, %s)", inserts[2])
    show("Stage 2: +0.5 kWh older hour, +2 kWh hour H, no refresh, real-time OFF")

    conn.execute("ALTER MATERIALIZED VIEW freshness_hourly SET (timescaledb.materialized_only = false)")
    show("Stage 3: real-time aggregation ON, still no refresh")

    for start in (older, H):
        conn.execute("CALL refresh_continuous_aggregate('freshness_hourly', %s::timestamptz, %s::timestamptz)",
                     (start, start + timedelta(hours=1)))
    show("Stage 4: both complete hours refreshed manually")

    watermark = conn.execute(
        "SELECT _timescaledb_functions.to_timestamp(_timescaledb_functions.cagg_watermark(mat_hypertable_id))"
        " FROM _timescaledb_catalog.continuous_agg WHERE user_view_name = 'freshness_hourly'").fetchone()[0]
    print(f"\nmaterialization watermark now: {watermark}")

    # Which hours could the 6.1 policy (start_offset 3 days, end_offset 1 hour) refresh right now?
    window_start, window_end = test_time - timedelta(days=3), test_time - timedelta(hours=1)
    first = window_start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    last_end = window_end.replace(minute=0, second=0, microsecond=0)
    print(f"6.1 policy window at test time: [{window_start:%d %b %H:%M}, {window_end:%d %b %H:%M})")
    print(f"  -> only whole hourly buckets inside it: {first:%d %b %H:%M} up to {last_end:%d %b %H:%M} (exclusive)")
    print(f"  older hour {older:%d %b %H:%M} refreshable: {first <= older and older + timedelta(hours=1) <= last_end}")
    print(f"  hour H     {H:%d %b %H:%M} refreshable: {first <= H and H + timedelta(hours=1) <= last_end}")
    conn.close()


if __name__ == "__main__":
    main()
