"""Section 8: next-day hourly regional consumption forecast.

    python src/s8_forecast.py

8.1  dataset: one row per (region, CAT hour) = sum(energy_kwh) of cleaned readings
     splits by target timestamp: train 1-14 Sep, validation 15-21 Sep, test 22-28 Sep
     forecast origin = 00:00 CAT of the target's day; every lag source < origin
8.2  baseline: previous day's same hour (lag 24 h)
8.3  Ridge regression, 4 alpha configurations, chosen by validation MAE, frozen
8.4  MAE / RMSE overall and per region, peak-hour errors, plot
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from common import FIGURES, RESULTS, connect, print_table

SPLITS = {"train": ("2026-09-01", "2026-09-15"),
          "validation": ("2026-09-15", "2026-09-22"),
          "test": ("2026-09-22", "2026-09-29")}
# name -> lag of the LAST source hour relative to the target, in hours, and window length
LAGS = {"lag_24h": 24, "lag_48h": 48, "lag_168h": 168}
WINDOW_FEATURE = "prev_day_total"          # sum of the 24 hours before the forecast origin
NUMERIC = list(LAGS) + [WINDOW_FEATURE]
CATEGORICAL = ["region", "hour", "dow"]
LOOKBACK_HOURS = 168
CONFIGS = [{"id": "R1", "alpha": 0.1}, {"id": "R2", "alpha": 1.0},
           {"id": "R3", "alpha": 10.0}, {"id": "R4", "alpha": 100.0}]
PLOT_REGION = "Region 8"


def ts(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="Africa/Kigali")


def load_hourly() -> pd.DataFrame:
    with connect() as conn:
        cur = conn.execute("""
            SELECT time_bucket('1 hour', r.event_time, 'Africa/Kigali') AS hour_start, m.region,
                   sum(r.energy_kwh) AS energy_kwh, count(*) AS readings
            FROM energy_readings r JOIN meters m ON m.meter_id = r.meter_id
            GROUP BY 1, 2 ORDER BY 2, 1""")
        frame = pd.DataFrame(cur.fetchall(), columns=["hour_start", "region", "energy_kwh", "readings"])
    frame["hour_start"] = pd.to_datetime(frame["hour_start"], utc=True).dt.tz_convert("Africa/Kigali")
    return frame


def build_features(hourly: pd.DataFrame) -> pd.DataFrame:
    # Complete grid so an absent hour would show up as NaN instead of silently shifting lags.
    hours = pd.date_range(ts("2026-09-01"), ts("2026-09-29"), freq="h", inclusive="left")
    grid = pd.MultiIndex.from_product([sorted(hourly.region.unique()), hours],
                                      names=["region", "hour_start"])
    data = hourly.set_index(["region", "hour_start"]).reindex(grid).reset_index()
    print(f"hourly grid rows: {len(data):,}; absent region-hours: {data.energy_kwh.isna().sum()}")

    series = data.set_index(["region", "hour_start"])["energy_kwh"]
    data["origin"] = data["hour_start"].dt.normalize()          # 00:00 CAT of the target day
    data["hour"] = data["hour_start"].dt.hour
    data["dow"] = data["hour_start"].dt.dayofweek
    for name, lag in LAGS.items():
        source = data["hour_start"] - pd.Timedelta(hours=lag)
        data[f"{name}_source"] = source
        data[name] = series.reindex(list(zip(data.region, source))).to_numpy()
    daily = data.groupby(["region", "origin"])["energy_kwh"].sum(min_count=24)
    prev_origin = data["origin"] - pd.Timedelta(days=1)
    data[WINDOW_FEATURE] = daily.reindex(list(zip(data.region, prev_origin))).to_numpy()
    data[f"{WINDOW_FEATURE}_source"] = data["origin"] - pd.Timedelta(hours=1)  # last hour in window
    data["split"] = None
    for split, (lo, hi) in SPLITS.items():
        data.loc[(data.hour_start >= ts(lo)) & (data.hour_start < ts(hi)), "split"] = split
    return data


def check_leakage(data: pd.DataFrame) -> None:
    sources = [f"{n}_source" for n in LAGS] + [f"{WINDOW_FEATURE}_source"]
    rows = []
    for col in sources:
        margin = (data["origin"] - data[col]).min()
        ok = bool((data[col] < data["origin"]).all())
        rows.append((col.replace("_source", ""), str(margin), ok))
    print("\nLeakage check: every lag source timestamp must precede the forecast origin")
    print_table(rows, ["feature", "smallest origin - source gap", "all_before_origin"])
    assert all(r[2] for r in rows)


def metrics(frame: pd.DataFrame, col: str) -> tuple[float, float]:
    err = frame[col] - frame["energy_kwh"]          # predicted minus actual
    return float(err.abs().mean()), float(np.sqrt((err ** 2).mean()))


def make_model(alpha: float) -> Pipeline:
    pre = ColumnTransformer([("num", StandardScaler(), NUMERIC),
                             ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL)])
    return Pipeline([("pre", pre), ("ridge", Ridge(alpha=alpha))])


def main() -> None:
    hourly = load_hourly()
    print("=== 8.1 dataset ===")
    print(f"readings per region-hour: min {hourly.readings.min()}, median {hourly.readings.median():.0f},"
          f" max {hourly.readings.max()} (planned 400 meters x 4 = 1,600)")
    data = build_features(hourly)

    feature_rows = [(n, lag, "1 h", str(ts("2026-09-01") + pd.Timedelta(hours=lag))) for n, lag in LAGS.items()]
    feature_rows.append((WINDOW_FEATURE, "24-47 (previous CAT day)", "24 h", str(ts("2026-09-02"))))
    feature_rows += [("region", "-", "-", "calendar/key"), ("hour", "-", "-", "calendar"),
                     ("dow (day of week)", "-", "-", "calendar")]
    print("\nFeatures:")
    print_table(feature_rows, ["feature", "lag_hours", "window", "earliest usable target"])
    check_leakage(data)

    usable = data[NUMERIC].notna().all(axis=1) & data.energy_kwh.notna()
    train_all = data[data.split == "train"]
    train = data[(data.split == "train") & usable]
    val = data[(data.split == "validation") & usable]
    test = data[(data.split == "test") & usable]
    split_rows = []
    for name, frame in (("train", train), ("validation", val), ("test", test)):
        split_rows.append((name, len(data[data.split == name]), len(frame),
                           str(frame.hour_start.min()), str(frame.hour_start.max())))
    print(f"\nLookback {LOOKBACK_HOURS} h: training rows before exclusion {len(train_all):,}, "
          f"excluded {len(train_all) - len(train):,}, retained {len(train):,} "
          f"(earliest usable target {train.hour_start.min()})")
    print_table(split_rows, ["split", "rows", "usable_rows", "first_target", "last_target"])
    print("\nFeature example (Region 1, first test day 07:00-09:00):")
    example = test[(test.region == "Region 1") & test.hour.between(7, 9)].head(3)
    print(example[["hour_start", "origin", "energy_kwh", "lag_24h", "lag_24h_source", "lag_168h",
                   "lag_168h_source", WINDOW_FEATURE]].to_string(index=False))
    data.drop(columns=[c for c in data.columns if c.endswith("_source")]).to_csv(
        RESULTS / "s8_dataset.csv", index=False)

    # 8.2 baseline
    print("\n=== 8.2 previous-day baseline ===")
    test = test.copy()
    test["baseline_kwh"] = test["lag_24h"]
    b_mae, b_rmse = metrics(test, "baseline_kwh")
    print(f"test predictions: {len(test):,}  MAE {b_mae:.3f} kWh  RMSE {b_rmse:.3f} kWh")
    print(test[["region", "hour_start", "energy_kwh", "baseline_kwh"]].head(5).to_string(index=False))

    # 8.3 model selection on validation, then freeze
    print("\n=== 8.3 Ridge regression ===")
    print(f"shared predictors: numeric {NUMERIC} (standardised) + one-hot {CATEGORICAL}; "
          "preprocessing fitted on training rows only")
    table, fitted = [], {}
    for cfg in CONFIGS:
        model = make_model(cfg["alpha"]).fit(train[NUMERIC + CATEGORICAL], train["energy_kwh"])
        pred = val.assign(pred=model.predict(val[NUMERIC + CATEGORICAL]))
        mae, rmse = metrics(pred, "pred")
        table.append((cfg["id"], f"Ridge(alpha={cfg['alpha']})", len(train), len(val), mae, rmse))
        fitted[cfg["id"]] = model
    print_table(table, ["config", "settings", "train_rows", "val_rows", "val_MAE_kWh", "val_RMSE_kWh"])
    best = min(table, key=lambda r: r[4])
    print(f"selected: {best[0]} {best[1]} (lowest validation MAE) - frozen, not refitted")
    pd.DataFrame(table, columns=["config", "settings", "train_rows", "val_rows", "val_mae_kwh",
                                 "val_rmse_kwh"]).to_csv(RESULTS / "s8_validation.csv", index=False)
    model = fitted[best[0]]
    test["model_kwh"] = model.predict(test[NUMERIC + CATEGORICAL])
    m_mae, m_rmse = metrics(test, "model_kwh")
    print(f"test predictions: {len(test):,}")
    print(test[["region", "hour_start", "energy_kwh", "baseline_kwh", "model_kwh"]].head(5).to_string(index=False))

    # 8.4 evaluation
    print("\n=== 8.4 comparison on the 1,680 test observations (error = predicted - actual) ===")
    rows = [("ALL", b_mae, m_mae, b_rmse, m_rmse, 100 * (b_mae - m_mae) / b_mae)]
    for region, part in test.groupby("region"):
        bm, br = metrics(part, "baseline_kwh")
        mm, mr = metrics(part, "model_kwh")
        rows.append((region, bm, mm, br, mr, 100 * (bm - mm) / bm))
    print_table(rows, ["region", "baseline_MAE", "model_MAE", "baseline_RMSE", "model_RMSE",
                       "MAE_improvement_%"])
    pd.DataFrame(rows, columns=["region", "baseline_mae", "model_mae", "baseline_rmse", "model_rmse",
                                "mae_improvement_pct"]).to_csv(RESULTS / "s8_metrics.csv", index=False)

    by_day = test.assign(day=test.hour_start.dt.strftime("%a %d")).groupby("day", sort=False).apply(
        lambda p: pd.Series({"baseline_MAE": metrics(p, "baseline_kwh")[0],
                             "model_MAE": metrics(p, "model_kwh")[0]}), include_groups=False)
    print("\nMAE by test day (kWh) - shows where the previous-day rule breaks:")
    print(by_day.round(2).to_string())

    peaks = (test.sort_values(["region", "energy_kwh", "hour_start"], ascending=[True, False, True])
             .groupby("region").head(1))
    peaks = peaks.assign(baseline_abs_err=(peaks.baseline_kwh - peaks.energy_kwh).abs(),
                         model_abs_err=(peaks.model_kwh - peaks.energy_kwh).abs())
    print("\nPeak actual test hour per region:")
    print(peaks[["region", "hour_start", "energy_kwh", "baseline_kwh", "baseline_abs_err",
                 "model_kwh", "model_abs_err"]].to_string(index=False, float_format="%.2f"))
    peaks.to_csv(RESULTS / "s8_peak_hours.csv", index=False)
    test[["region", "hour_start", "energy_kwh", "baseline_kwh", "model_kwh"]].to_csv(
        RESULTS / "s8_test_predictions.csv", index=False)

    part = test[test.region == PLOT_REGION]
    fig, ax = plt.subplots(figsize=(14, 4.5))
    ax.plot(part.hour_start, part.energy_kwh, label="actual", color="black", linewidth=1.8)
    ax.plot(part.hour_start, part.baseline_kwh, label="previous-day baseline", linestyle="--")
    ax.plot(part.hour_start, part.model_kwh, label=f"model: {best[1]}", alpha=0.85)
    ax.set_title(f"{PLOT_REGION}: hourly consumption, test week 22-28 Sep (CAT)")
    ax.set_ylabel("Energy (kWh)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIGURES / "s8_forecast_region.png", dpi=150)
    (RESULTS / "s8_summary.json").write_text(json.dumps({
        "selected_config": best[0], "selected_settings": best[1],
        "test_predictions": len(test), "baseline_mae_kwh": b_mae, "baseline_rmse_kwh": b_rmse,
        "model_mae_kwh": m_mae, "model_rmse_kwh": m_rmse}, indent=2))
    print(f"\nplot saved: {FIGURES / 's8_forecast_region.png'}")


if __name__ == "__main__":
    main()
