"""Section 2.1: capture the complete stream to CSV, then bulk-load it with COPY.

    python src/s2_1_capture_load.py            # generate CSV + load
    python src/s2_1_capture_load.py --skip-generate   # reload an existing CSV

Route: generator -> CSV file (written progressively, never held in memory)
       -> PostgreSQL COPY FROM STDIN into the empty energy_readings hypertable,
       streamed in 8 MB blocks inside one transaction.
"""
import argparse
import csv
import json
import time

from common import DATA, RESULTS, cat_text, connect
import smart_meter_simulator as sim

CSV_PATH = DATA / "energy_readings.csv"
PROGRESS_EVERY = 1_000_000
BLOCK = 8 * 1024 * 1024


def generate(fields: list[str]) -> dict:
    started_cat, t0 = cat_text(), time.perf_counter()
    consumed = 0
    with open(CSV_PATH, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for record in sim.iter_readings():          # one dictionary at a time
            writer.writerow([record[f] for f in fields])
            consumed += 1
            if consumed % PROGRESS_EVERY == 0:
                print(f"  [{cat_text()}] generated {consumed:,} "
                      f"({time.perf_counter() - t0:,.1f} s)", flush=True)
    seconds = time.perf_counter() - t0
    print(f"[{cat_text()}] generation finished: {consumed:,} dictionaries in {seconds:,.1f} s")
    return {"generation_started_cat": started_cat, "generation_finished_cat": cat_text(),
            "dictionaries_consumed": consumed, "generation_seconds": round(seconds, 3),
            "generation_t0": t0}


def count_csv_rows() -> int:
    with open(CSV_PATH, "rb") as handle:
        lines = sum(block.count(b"\n") for block in iter(lambda: handle.read(BLOCK), b""))
    return lines - 1  # header


def load() -> dict:
    with connect() as conn:
        existing = conn.execute("SELECT count(*) FROM energy_readings").fetchone()[0]
        if existing:
            raise SystemExit(f"energy_readings already holds {existing:,} rows; "
                             "rerun sql/02_tables.sql to start empty")
        size = CSV_PATH.stat().st_size
        sent = 0
        first_write_cat, t0 = cat_text(), time.perf_counter()
        with conn.cursor() as cur:
            with cur.copy("COPY energy_readings (meter_id, event_time, received_time, power_kw,"
                          " voltage_v, current_a, frequency_hz, energy_kwh)"
                          " FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
                with open(CSV_PATH, "rb") as handle:
                    for block in iter(lambda: handle.read(BLOCK), b""):
                        copy.write(block)
                        sent += len(block)
                        if sent // BLOCK % 16 == 0:
                            print(f"  [{cat_text()}] loaded {sent / size:6.1%} of CSV", flush=True)
        conn.commit()
        commit_time = time.perf_counter()
        committed_cat = cat_text()
        rows = conn.execute("SELECT count(*) FROM energy_readings").fetchone()[0]
    seconds = commit_time - t0
    print(f"[{committed_cat}] load committed: {rows:,} rows in {seconds:,.1f} s")
    return {"load_first_write_cat": first_write_cat, "load_committed_cat": committed_cat,
            "load_seconds": round(seconds, 3), "committed_rows": rows,
            "commit_perf_counter": commit_time}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-generate", action="store_true")
    args = parser.parse_args()

    info = sim.dataset_info()
    expected = info["expected_counts"]["emitted_readings"]
    print(f"dataset_info(): planned_readings={info['planned_readings']:,} "
          f"expected_counts={json.dumps(info['expected_counts'])}")

    result = {"route": "generator -> CSV file -> COPY FROM STDIN (single transaction)"}
    if not args.skip_generate:
        result.update(generate(info["reading_fields"]))
    result["csv_data_rows"] = count_csv_rows()
    result["csv_bytes"] = CSV_PATH.stat().st_size
    print(f"CSV data rows: {result['csv_data_rows']:,} ({result['csv_bytes'] / 1e9:.2f} GB)")
    result.update(load())

    if "generation_t0" in result:
        result["total_elapsed_seconds"] = round(
            result.pop("commit_perf_counter") - result.pop("generation_t0"), 3)
    result.pop("commit_perf_counter", None)
    result["expected_emitted_readings"] = expected
    checks = {
        "dictionaries_consumed": result.get("dictionaries_consumed"),
        "csv_data_rows": result["csv_data_rows"],
        "committed_energy_readings_rows": result["committed_rows"],
    }
    print("\nCount check against expected_counts.emitted_readings =", f"{expected:,}")
    for name, value in checks.items():
        if value is not None:
            print(f"  {name:32s} {value:>12,}  {'OK' if value == expected else 'MISMATCH'}")
    for key in ("generation_seconds", "load_seconds", "total_elapsed_seconds"):
        if key in result:
            print(f"  {key:32s} {result[key]:>12,.1f}")
    (RESULTS / "ingestion.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
