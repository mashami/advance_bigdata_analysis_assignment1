"""Section 4.2: storage, compression ratio, saving, cost and coverage.

Reads results/compression_chunks.csv (written by s4_1_compress.py) for the
per-chunk compression times and the original row count of each chunk.
"""
import csv
import json

from common import RESULTS, connect, print_table, write_csv

CLEANED_ROWS = 10_643_735
SIZE = """
SELECT table_bytes, index_bytes, toast_bytes, total_bytes
FROM hypertable_detailed_size(%s)
"""


def main() -> None:
    meta = json.loads((RESULTS / "compression_experiment.json").read_text())
    baseline = meta["baseline_table"]
    with open(RESULTS / "compression_chunks.csv") as handle:
        chunks = list(csv.DictReader(handle))

    rows_out, table_rows = [], []
    with connect() as conn:
        sizes = {t: conn.execute(SIZE, (t,)).fetchone()
                 for t in (baseline, "energy_time_compressed", "energy_meter_compressed")}
        stats = {t: conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE is_compressed) "
            "FROM timescaledb_information.chunks WHERE hypertable_name = %s", (t,)).fetchone()
            for t in sizes}

    base_total = sizes[baseline][3]
    mb = lambda b: round(b / 1024**2, 2)
    for table, (tbl, idx, toast, total) in sizes.items():
        mine = [c for c in chunks if c["table"] == table]
        compressed_rows = sum(int(c["rows"]) for c in mine if c["compressed"] == "True")
        cost = sum(float(c["compress_seconds"]) for c in mine if c["compress_seconds"])
        record = {
            "table": table, "data_mb": mb(tbl + toast), "index_mb": mb(idx), "total_mb": mb(total),
            "compression_ratio": round(base_total / total, 2),
            "percent_saved": round(100 * (base_total - total) / base_total, 2),
            "compression_seconds": round(cost, 3) if mine else None,
            "compressed_chunks": f"{stats[table][1]}/{stats[table][0]}",
            "rows_in_compressed_chunks_pct": round(100 * compressed_rows / CLEANED_ROWS, 2) if mine else 0.0,
        }
        rows_out.append(record)
        table_rows.append(tuple(record.values()))
    print(f"baseline = {baseline} (uncompressed, {meta['chunk_interval']} chunks); "
          f"cutoff = {meta['cutoff_cat']}")
    print("data_mb includes heap + TOAST (compressed columns live mostly in TOAST)\n")
    print_table(table_rows, list(rows_out[0].keys()))
    write_csv(RESULTS / "compression_storage.csv", rows_out)


if __name__ == "__main__":
    main()
