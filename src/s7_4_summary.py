"""Section 7.4: summarise dashboard timings (10 measured refreshes per mode).

Reads results/dashboard_timings.csv written by dashboard/app.py; uses only the
latest complete timing run (warm-up + runs 1-10) of each mode.
"""
import pandas as pd

from common import RESULTS, print_table


def main() -> None:
    frame = pd.read_csv(RESULTS / "dashboard_timings.csv")
    metrics = [c for c in frame.columns if c.endswith("_ms")]
    rows, out, days = [], [], {}
    for mode in ("Raw", "Aggregate"):
        runs = frame[frame["mode"] == mode].reset_index(drop=True)
        warm = runs.index[runs["run"] == "warm-up"]
        if not len(warm):
            print(f"no timing run for {mode}")
            continue
        last = runs.iloc[warm[-1] + 1: warm[-1] + 11]
        assert list(last["run"].astype(str)) == [str(i) for i in range(1, 11)], "incomplete run"
        for metric in metrics:
            values = last[metric]
            record = {"mode": mode, "measure": metric.replace("_ms", ""),
                      "median_ms": round(values.median(), 1), "min_ms": round(values.min(), 1),
                      "max_ms": round(values.max(), 1)}
            out.append(record)
            rows.append(tuple(record.values()))
        print(f"{mode}: measured runs {last['recorded_cat'].iloc[0]} -> {last['recorded_cat'].iloc[-1]}")
        days[mode] = last["recorded_cat"].iloc[0][:10]
    if len(set(days.values())) > 1:
        print(f"\nWARNING: Raw and Aggregate runs come from different days {days}. "
              "Re-run 'Run timing' for the older mode so both use the same session.")
    print("\nMeasurement tool: browser performance.now() (total) + Python time.perf_counter() (panel query)")
    print_table(rows, ["mode", "measure", "median_ms", "min_ms", "max_ms"])
    pd.DataFrame(out).to_csv(RESULTS / "s7_4_dashboard_summary.csv", index=False)

    cagg = pd.read_csv(RESULTS / "s6_1_cagg_refresh.csv")
    print("\nAggregate cost from 6.1 (storage + one-off full refresh):")
    print(cagg[["view", "rows_after", "total_mb", "refresh_seconds"]].to_string(index=False))
    print(f"total: {cagg.total_mb.sum():.2f} MB, {cagg.refresh_seconds.sum():.1f} s of initial refresh")


if __name__ == "__main__":
    main()
