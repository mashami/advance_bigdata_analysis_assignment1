"""Section 7.3: compare the numbers behind every panel of Raw vs Aggregate.

Filters: 22 Sep 00:00 -> 29 Sep 00:00 CAT (exclusive), all regions, meter 1000000000.
Rules: identical keys and reading counts; energy diff <= 0.001 kWh;
completeness diff <= 0.000001 percentage points (before rounding).
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))
from common import CAT, RESULTS, connect, print_table, write_csv  # noqa: E402
from queries import PANELS, run_panel  # noqa: E402

START = datetime(2026, 9, 22, tzinfo=CAT)
END = datetime(2026, 9, 29, tzinfo=CAT)
METER = 1000000000
KEYS = {"hourly_regional": ["bucket", "region"], "daily_network": ["bucket"],
        "weekly_network": ["bucket"], "meter_daily": ["bucket", "meter_id"],
        "completeness": ["region"]}


def main() -> None:
    rows, out = [], []
    with connect() as conn:
        for panel in PANELS:
            raw = run_panel(conn, "raw", panel, START, END, METER)
            agg = run_panel(conn, "aggregate", panel, START, END, METER)
            raw.to_csv(RESULTS / f"s7_panel_raw_{panel}.csv", index=False)
            agg.to_csv(RESULTS / f"s7_panel_aggregate_{panel}.csv", index=False)
            merged = raw.merge(agg, on=KEYS[panel], how="outer", suffixes=("_raw", "_agg"),
                               indicator=True)
            keys_ok = (merged["_merge"] == "both").all() and len(raw) == len(agg)
            counts_ok = (merged["reading_count_raw"] == merged["reading_count_agg"]).all()
            value = "completeness_pct" if panel == "completeness" else "energy_kwh"
            limit = 0.000001 if panel == "completeness" else 0.001
            max_diff = (merged[f"{value}_raw"] - merged[f"{value}_agg"]).abs().max()
            ok = keys_ok and counts_ok and max_diff <= limit
            rows.append((panel, len(raw), len(agg), keys_ok, counts_ok, value, max_diff, limit,
                         "PASS" if ok else "FAIL"))
            out.append(dict(zip(["panel", "rows_raw", "rows_aggregate", "keys_identical",
                                 "reading_counts_identical", "compared_value", "max_abs_diff",
                                 "limit", "result"], rows[-1])))
    print(f"Raw vs Aggregate, {START:%Y-%m-%d %H:%M} to {END:%Y-%m-%d %H:%M} CAT (exclusive), meter {METER}")
    print_table(rows, ["panel", "rows_raw", "rows_agg", "keys_ok", "counts_ok", "value",
                       "max_abs_diff", "limit", "result"])
    write_csv(RESULTS / "s7_3_verification.csv", out)


if __name__ == "__main__":
    main()
