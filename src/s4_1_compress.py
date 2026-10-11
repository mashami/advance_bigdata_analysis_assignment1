"""Section 4.1: build energy_time_compressed and energy_meter_compressed.

    python src/s4_1_compress.py                      # 1-day chunks (default)
    python src/s4_1_compress.py --interval "7 days" --source energy_readings_week

Steps
 1. Record ONE cutoff = experiment start - 48 h, used for both tables.
 2. Create both hypertables (same columns, chunk interval and indexes as the source).
 3. Compression settings: order by event_time DESC; segment by nothing (time
    strategy) or by meter_id (meter strategy).
 4. Add a 48-hour compression policy to each and pause it immediately.
 5. Copy the cleaned rows one source chunk at a time; after each chunk is loaded,
    compress it if its range end precedes the cutoff. Loading and compression are
    timed separately.
"""
import argparse
import json
import time
from datetime import timedelta

from psycopg import sql

from common import RESULTS, cat_text, connect, now_cat, print_table, write_csv

TABLES = {"energy_time_compressed": "", "energy_meter_compressed": "meter_id"}


def create(conn, table: str, segmentby: str, interval: str) -> int:
    t = sql.Identifier(table)
    conn.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(t))
    conn.execute(sql.SQL("CREATE TABLE {} (LIKE energy_readings INCLUDING DEFAULTS "
                         "INCLUDING CONSTRAINTS)").format(t))
    conn.execute("SELECT create_hypertable(%s, 'event_time', chunk_time_interval => %s::interval,"
                 " create_default_indexes => FALSE)", (table, interval))
    conn.execute(sql.SQL("CREATE INDEX {} ON {} (event_time DESC)").format(
        sql.Identifier(f"{table}_time_idx"), t))
    conn.execute(sql.SQL("CREATE INDEX {} ON {} (meter_id, event_time DESC)").format(
        sql.Identifier(f"{table}_meter_time_idx"), t))
    # segmentby '' explicitly disables segmentation (newer versions otherwise auto-pick one).
    conn.execute(sql.SQL("ALTER TABLE {} SET (timescaledb.compress, "
                         "timescaledb.compress_orderby = 'event_time DESC', "
                         "timescaledb.compress_segmentby = {})").format(t, sql.Literal(segmentby)))
    job = conn.execute("SELECT add_compression_policy(%s, compress_after => INTERVAL '48 hours')",
                       (table,)).fetchone()[0]
    conn.execute("SELECT alter_job(%s, scheduled => false)", (job,))
    return job


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", default="1 day")
    parser.add_argument("--source", default="energy_readings",
                        help="cleaned uncompressed table with the same chunk interval (baseline)")
    args = parser.parse_args()

    started = now_cat()
    cutoff = started - timedelta(hours=48)
    print(f"experiment start {cat_text(started)}  ->  cutoff (start - 48 h) {cat_text(cutoff)}")

    conn = connect(autocommit=True)
    jobs = {table: create(conn, table, seg, args.interval) for table, seg in TABLES.items()}
    print("created tables, compression settings and paused policies:", jobs)

    source_chunks = conn.execute(
        "SELECT range_start, range_end FROM timescaledb_information.chunks "
        "WHERE hypertable_name = %s ORDER BY range_start", (args.source,)).fetchall()

    log = []
    for table in TABLES:
        target = sql.Identifier(table)
        for range_start, range_end in source_chunks:
            t0 = time.perf_counter()
            with conn.transaction():
                rows = conn.execute(
                    sql.SQL("INSERT INTO {} SELECT * FROM {} WHERE event_time >= %s "
                            "AND event_time < %s ORDER BY event_time, meter_id").format(
                        target, sql.Identifier(args.source)), (range_start, range_end)).rowcount
            load_s = time.perf_counter() - t0
            chunk = conn.execute(
                "SELECT format('%%I.%%I', chunk_schema, chunk_name) FROM timescaledb_information.chunks"
                " WHERE hypertable_name = %s AND range_start = %s", (table, range_start)).fetchone()
            compress_s, compressed = None, False
            if chunk and range_end < cutoff:
                t0 = time.perf_counter()
                conn.execute("SELECT compress_chunk(%s::regclass)", (chunk[0],))
                compress_s = time.perf_counter() - t0
                compressed = True
            log.append({"table": table, "chunk": chunk[0] if chunk else None,
                        "range_start": range_start.isoformat(), "range_end": range_end.isoformat(),
                        "rows": rows, "load_seconds": round(load_s, 3),
                        "compressed": compressed,
                        "compress_seconds": round(compress_s, 3) if compress_s else None})
            print(f"  [{cat_text()}] {table:24s} {range_start:%m-%d %H:%M} rows={rows:>7,} "
                  f"load={load_s:6.2f}s compress={'%.2fs' % compress_s if compressed else '-'}")
        conn.execute(sql.SQL("ANALYZE {}").format(target))

    write_csv(RESULTS / "compression_chunks.csv", log)
    (RESULTS / "compression_experiment.json").write_text(json.dumps({
        "experiment_start_cat": cat_text(started), "cutoff_cat": cat_text(cutoff),
        "chunk_interval": args.interval, "baseline_table": args.source, "policy_jobs": jobs},
        indent=2))

    summary = []
    for table in TABLES:
        mine = [r for r in log if r["table"] == table]
        summary.append((table, len(mine), sum(r["compressed"] for r in mine),
                        sum(r["rows"] for r in mine),
                        sum(r["load_seconds"] for r in mine),
                        sum(r["compress_seconds"] or 0 for r in mine)))
    print()
    print_table(summary, ["table", "chunks", "compressed", "rows", "load_s", "compress_s"])

    a, b = ([r["range_start"] for r in log if r["table"] == t and r["compressed"]] for t in TABLES)
    print(f"\nsame compressed chunk ranges in both tables: {a == b}")
    conn.close()


if __name__ == "__main__":
    main()
