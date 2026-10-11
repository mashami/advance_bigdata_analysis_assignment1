"""Sections 3.2 and 4.3: time Q1-Q5 on several tables (warm cache).

    python src/s3_2_benchmark.py --tag chunks
    python src/s3_2_benchmark.py --tag compression --tables energy_readings \
        energy_time_compressed energy_meter_compressed

Client: psycopg 3 (Python), one autocommit connection.
Timer:  time.perf_counter(), from cursor.execute() (statement submission) until
        fetchall() has returned the last result row.
Per query and table: 1 warm-up + 5 measured runs, sequential, no restarts.
After timing, EXPLAIN (ANALYZE, BUFFERS) plans are saved to results/plans/.
"""
import argparse
import re
import statistics
import time
from pathlib import Path

from psycopg import sql

from common import MACHINE, RESULTS, ROOT, cat_text, connect, print_table, write_csv

DEFAULT_TABLES = ["energy_readings_3h", "energy_readings", "energy_readings_week"]
MEASURED_RUNS = 5
TOLERANCE = 0.001


def load_queries() -> dict[str, str]:
    text = (ROOT / "sql" / "08_workloads.sql").read_text()
    parts = re.split(r"^-- name: (\w+)\s*$", text, flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def for_table(query: str, table: str) -> str:
    return query.replace(':"tbl"', sql.Identifier(table).as_string(None))


def same_result(a: list, b: list) -> tuple[bool, float]:
    """Rows must match pairwise; numbers within TOLERANCE, other values exactly."""
    if len(a) != len(b):
        return False, float("inf")
    worst = 0.0
    for row_a, row_b in zip(a, b):
        for x, y in zip(row_a, row_b):
            if isinstance(x, float) or isinstance(y, float):
                worst = max(worst, abs(float(x) - float(y)))
            elif x != y:
                return False, float("inf")
    return worst <= TOLERANCE, worst


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="chunks")
    parser.add_argument("--tables", nargs="+", default=DEFAULT_TABLES)
    parser.add_argument("--queries", nargs="+", default=["Q1", "Q2", "Q3", "Q4", "Q5"])
    parser.add_argument("--no-plans", action="store_true")
    args = parser.parse_args()

    queries = load_queries()
    runs, results = [], {}
    conn = connect(autocommit=True)
    cur = conn.cursor()
    app = f"benchmark-{args.tag}"
    cur.execute("SELECT set_config('application_name', %s, false)", (app,))
    for table in args.tables:
        cur.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(table)))

    session_start_cat, session_t0 = cat_text(), time.perf_counter()
    print(f"[{session_start_cat}] benchmark '{args.tag}' on {MACHINE}; warm cache; "
          f"client=psycopg {conn.info.server_version}, timer=time.perf_counter")
    for qname in args.queries:
        for table in args.tables:
            text = for_table(queries[qname], table)
            for run in range(MEASURED_RUNS + 1):          # run 0 = warm-up
                start_cat = cat_text()
                t0 = time.perf_counter()
                cur.execute(text)
                rows = cur.fetchall()
                ms = (time.perf_counter() - t0) * 1000
                runs.append({"tag": args.tag, "query": qname, "table": table,
                             "run": "warm-up" if run == 0 else run, "start_cat": start_cat,
                             "end_cat": cat_text(), "duration_ms": round(ms, 3),
                             "rows_returned": len(rows), "machine": MACHINE})
            results[(qname, table)] = rows
            measured = [r["duration_ms"] for r in runs[-MEASURED_RUNS:]]
            print(f"  {qname} {table:26s} median {statistics.median(measured):10.1f} ms")
    session_seconds = time.perf_counter() - session_t0
    session_end_cat = cat_text()

    # Correctness: every table must return the same result as the first table.
    print("\nResult check (vs first table, tolerance 0.001):")
    checks = []
    for qname in args.queries:
        ref = results[(qname, args.tables[0])]
        for table in args.tables[1:]:
            ok, diff = same_result(ref, results[(qname, table)])
            checks.append((qname, table, len(results[(qname, table)]), diff, "MATCH" if ok else "DIFF"))
    print_table(checks, ["query", "table", "rows", "max_abs_diff", "status"])

    summary = []
    for qname in args.queries:
        for table in args.tables:
            ms = [r["duration_ms"] for r in runs if r["query"] == qname and r["table"] == table
                  and r["run"] != "warm-up"]
            summary.append({"tag": args.tag, "query": qname, "table": table,
                            "median_ms": round(statistics.median(ms), 3),
                            "min_ms": round(min(ms), 3), "max_ms": round(max(ms), 3)})
    print("\nWarm-cache timings (5 measured runs after 1 warm-up):")
    print_table([tuple(s.values())[1:] for s in summary], ["query", "table", "median_ms", "min_ms", "max_ms"])
    print(f"\nsession: {session_start_cat} -> {session_end_cat} = {session_seconds:,.1f} s "
          f"(first warm-up submission to last measured result)")

    write_csv(RESULTS / f"benchmark_{args.tag}_runs.csv", runs)
    write_csv(RESULTS / f"benchmark_{args.tag}_summary.csv", summary)
    (RESULTS / f"benchmark_{args.tag}_session.txt").write_text(
        f"machine={MACHINE}\nclient=psycopg 3 (Python)\ntimer=time.perf_counter\n"
        f"start_cat={session_start_cat}\nend_cat={session_end_cat}\n"
        f"session_seconds={session_seconds:.3f}\n")

    if not args.no_plans:
        plans = RESULTS / "plans"
        plans.mkdir(exist_ok=True)
        for qname in args.queries:
            for table in args.tables:
                cur.execute("EXPLAIN (ANALYZE, BUFFERS, TIMING) " + for_table(queries[qname], table))
                plan = "\n".join(r[0] for r in cur.fetchall())
                Path(plans / f"{args.tag}_{qname}_{table}.txt").write_text(plan)
        print(f"execution plans written to {plans}")
    conn.close()


if __name__ == "__main__":
    main()
