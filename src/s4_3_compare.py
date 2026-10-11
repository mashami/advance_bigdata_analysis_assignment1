"""Section 4.3: one comparison of all five configurations + % improvement.

Combines results/benchmark_chunks_summary.csv (3.2) and
results/benchmark_compression_summary.csv (4.3). The baseline for the
improvement is the uncompressed table with the same chunk interval, measured
in the SAME session as the compressed tables (benchmark tag "compression").
improvement % = 100 * (baseline median - compressed median) / baseline median
"""
import csv
import json

from common import RESULTS, print_table, write_csv


def read(tag: str) -> list[dict]:
    with open(RESULTS / f"benchmark_{tag}_summary.csv") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    baseline = json.loads((RESULTS / "compression_experiment.json").read_text())["baseline_table"]
    chunks, comp = read("chunks"), read("compression")
    median = {(r["query"], r["table"], r["tag"]): float(r["median_ms"]) for r in chunks + comp}

    order = ["energy_readings_3h", "energy_readings", "energy_readings_week",
             "energy_time_compressed", "energy_meter_compressed"]
    rows, out = [], []
    for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        base = median[(q, baseline, "compression")]
        row = [q]
        for table in order:
            tag = "compression" if table.endswith("compressed") or table == baseline else "chunks"
            row.append(median[(q, table, tag)])
        for table in order[3:]:
            improvement = 100 * (base - median[(q, table, "compression")]) / base
            row.append(round(improvement, 1))
        rows.append(row)
        out.append(dict(zip(["query"] + [f"{t}_median_ms" for t in order]
                            + ["time_compressed_improvement_pct", "meter_compressed_improvement_pct"], row)))
    print(f"Median ms (warm cache). Baseline for improvement: {baseline} from the compression session.")
    print_table(rows, ["query", "3h", "1day", "week", "time_comp", "meter_comp",
                       "time_comp_impr_%", "meter_comp_impr_%"])
    write_csv(RESULTS / "benchmark_five_configurations.csv", out)


if __name__ == "__main__":
    main()
