"""Sections 5.1-5.3: demand patterns, regions/customer types, reporting quality.

    python src/s5_analysis.py
Uses cleaned energy_readings + meters. Days are CAT days (session TZ Africa/Kigali).
Charts -> results/figures/, tables -> results/*.csv
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from common import FIGURES, RESULTS, connect

PLANNED_PER_METER = 28 * 96


def query(conn, text: str, params=None) -> pd.DataFrame:
    cur = conn.execute(text, params)
    return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def section_5_1(conn) -> None:
    print("=== 5.1 Demand patterns ===")
    daily = query(conn, """
        SELECT time_bucket('1 day', event_time, 'Africa/Kigali')::date AS day_cat,
               sum(energy_kwh) AS energy_kwh, count(*) AS readings
        FROM energy_readings GROUP BY 1 ORDER BY 1""")
    daily["weekday"] = pd.to_datetime(daily["day_cat"]).dt.day_name().str[:3]
    daily["period"] = (pd.to_datetime(daily["day_cat"]) - pd.Timestamp("2026-09-01")).dt.days // 7
    weekly = daily.groupby("period").agg(energy_kwh=("energy_kwh", "sum"),
                                         first_day=("day_cat", "min"),
                                         last_day=("day_cat", "max")).reset_index()
    weekly["label"] = weekly.apply(lambda r: f"{r.first_day:%d %b} - {r.last_day:%d %b}", axis=1)
    print(daily.to_string(index=False))
    print(weekly[["label", "energy_kwh"]].to_string(index=False))
    daily.to_csv(RESULTS / "s5_1_daily_energy.csv", index=False)
    weekly.to_csv(RESULTS / "s5_1_weekly_energy.csv", index=False)

    fig, (a, b) = plt.subplots(1, 2, figsize=(14, 4.5), gridspec_kw={"width_ratios": [3, 1]})
    colors = ["#c0504d" if d in ("Sat", "Sun") else "#4f81bd" for d in daily["weekday"]]
    a.bar(pd.to_datetime(daily["day_cat"]), daily["energy_kwh"], color=colors)
    a.set_title("Total network energy per CAT day (red = weekend)")
    a.set_ylabel("Energy (kWh)")
    a.tick_params(axis="x", rotation=45)
    b.bar(weekly["label"], weekly["energy_kwh"], color="#4f81bd")
    b.set_title("Seven-day periods")
    b.set_ylabel("Energy (kWh)")
    b.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    fig.savefig(FIGURES / "s5_1_daily_weekly_energy.png", dpi=150)

    peaks = query(conn, """
        SELECT event_time, sum(power_kw) AS network_kw, count(*) AS meters_reporting
        FROM energy_readings GROUP BY event_time
        ORDER BY network_kw DESC, event_time LIMIT 3""")
    print("\nThree highest 15-minute network demand intervals:")
    print(peaks.to_string(index=False))
    peaks.to_csv(RESULTS / "s5_1_peak_intervals.csv", index=False)

    profile = query(conn, """
        SELECT (extract(hour FROM event_time) + extract(minute FROM event_time) / 60)::float AS hour_of_day,
               extract(isodow FROM event_time) >= 6 AS weekend,
               m.customer_type, sum(power_kw) / count(DISTINCT event_time) AS avg_network_kw
        FROM energy_readings r JOIN meters m USING (meter_id)
        GROUP BY 1, 2, 3 ORDER BY 1""")
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for (ctype, weekend), part in profile.groupby(["customer_type", "weekend"]):
        ax.plot(part["hour_of_day"], part["avg_network_kw"],
                linestyle="--" if weekend else "-", label=f"{ctype} ({'weekend' if weekend else 'weekday'})")
    ax.set_title("Average 15-minute demand profile by customer type")
    ax.set_xlabel("Hour of day (CAT)")
    ax.set_ylabel("Demand (kW)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES / "s5_1_daily_profile.png", dpi=150)


def section_5_2(conn) -> None:
    print("\n=== 5.2 Regions and customer types ===")
    for key in ("region", "customer_type"):
        frame = query(conn, f"""
            WITH registered AS (SELECT {key}, count(*) AS meters FROM meters GROUP BY {key}),
                 energy AS (SELECT m.{key}, sum(r.energy_kwh) AS energy_kwh
                            FROM energy_readings r JOIN meters m ON m.meter_id = r.meter_id
                            GROUP BY m.{key})
            SELECT g.{key}, g.meters, e.energy_kwh, e.energy_kwh / g.meters AS kwh_per_meter
            FROM registered g JOIN energy e USING ({key})
            ORDER BY kwh_per_meter DESC""")
        frame["share_pct"] = 100 * frame["energy_kwh"] / frame["energy_kwh"].sum()
        print(frame.to_string(index=False))
        frame.to_csv(RESULTS / f"s5_2_by_{key}.csv", index=False)
    mix = query(conn, """SELECT region, customer_type, count(*) AS meters,
                                round(avg(base_power_kw)::numeric, 3) AS avg_base_kw
                         FROM meters GROUP BY 1, 2 ORDER BY 1, 2""")
    pivot = mix.pivot(index="region", columns="customer_type", values="meters")
    print("\nCustomer mix per region (meter counts) - explains regional differences:")
    print(pivot.to_string())
    pivot.to_csv(RESULTS / "s5_2_region_mix.csv")


def section_5_3(conn) -> None:
    print("\n=== 5.3 Reporting quality ===")
    comp = query(conn, f"""
        WITH reg AS (SELECT region, count(*) AS meters FROM meters GROUP BY region),
             obs AS (SELECT m.region, count(*) AS observed
                     FROM energy_readings r JOIN meters m USING (meter_id) GROUP BY m.region)
        SELECT region, meters, meters * {PLANNED_PER_METER} AS planned, observed,
               100.0 * observed / (meters * {PLANNED_PER_METER}) AS completeness_pct
        FROM reg JOIN obs USING (region)
        UNION ALL
        SELECT 'NETWORK', sum(meters), sum(meters) * {PLANNED_PER_METER}, sum(observed),
               100.0 * sum(observed) / (sum(meters) * {PLANNED_PER_METER})
        FROM reg JOIN obs USING (region)
        ORDER BY completeness_pct""")
    print(comp.to_string(index=False))
    comp.to_csv(RESULTS / "s5_3_completeness.csv", index=False)

    late = query(conn, """
        SELECT sum(energy_kwh) FILTER (WHERE received_time <= '2026-09-22 00:00+02') AS by_midnight_kwh,
               count(*)        FILTER (WHERE received_time <= '2026-09-22 00:00+02') AS by_midnight_rows,
               sum(energy_kwh)  AS all_arrivals_kwh,
               count(*)         AS all_rows
        FROM energy_readings
        WHERE event_time >= '2026-09-21 00:00+02' AND event_time < '2026-09-22 00:00+02'""")
    row = late.iloc[0]
    diff = row.all_arrivals_kwh - row.by_midnight_kwh
    late["difference_kwh"] = diff
    late["difference_pct_of_final"] = 100 * diff / row.all_arrivals_kwh
    print("\n21 September energy, arrivals by 00:00 on 22 Sep vs all arrivals:")
    print(late.T.to_string(header=False))
    late.to_csv(RESULTS / "s5_3_late_arrivals_21sep.csv", index=False)

    late_slots = query(conn, """
        SELECT event_time, count(*) FILTER (WHERE received_time > '2026-09-22 00:00+02') AS late_rows
        FROM energy_readings
        WHERE event_time >= '2026-09-21 20:00+02' AND event_time < '2026-09-22 00:00+02'
        GROUP BY 1 ORDER BY 1""")
    print("\nReadings of 21 Sep arriving after midnight, by interval (last 4 hours):")
    print(late_slots.to_string(index=False))


def main() -> None:
    with connect() as conn:
        section_5_1(conn)
        section_5_2(conn)
        section_5_3(conn)
    print(f"\ncharts saved in {FIGURES}")


if __name__ == "__main__":
    main()
