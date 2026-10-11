"""Section 5.4: rule-based anomaly detection, then evaluation against labels.

Rule (decided BEFORE looking at the labels):
  expected_kw = median power_kw of the same meter, same 15-minute slot of the CAT
                day, same day type (weekday / weekend), over all 28 days.
  ratio       = power_kw / expected_kw
  flag        if ratio >= 2.0 (demand spike) or ratio <= 0.4 (unusual drop).
Why: each customer type has its own daily shape and commercial/industrial load
falls at weekends, so comparing a reading with the same meter at the same time
of day and day type removes the normal pattern. The median ignores the rare
abnormal days. Normal noise is about +/-15 % (plus <= 8 % trend over the month),
so normal ratios stay roughly within 0.8-1.25; the 2.0 / 0.4 limits leave a wide
safety margin while still catching a doubling or a collapse of demand.

Step 1 writes every flag (meter_id, event_time) to the table anomaly_flags.
Step 2 only then calls anomaly_events() and scores the flags.
"""
from datetime import datetime

import pandas as pd

from common import RESULTS, connect, print_table
import smart_meter_simulator as sim

SPIKE, DROP = 2.0, 0.4

DETECT = f"""
DROP TABLE IF EXISTS anomaly_baseline;
CREATE TABLE anomaly_baseline AS
SELECT meter_id,
       (extract(hour FROM event_time) * 4 + extract(minute FROM event_time) / 15)::int AS slot,
       extract(isodow FROM event_time) >= 6 AS weekend,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY power_kw) AS expected_kw,
       count(*) AS samples
FROM energy_readings
GROUP BY 1, 2, 3;

DROP TABLE IF EXISTS anomaly_flags;
CREATE TABLE anomaly_flags AS
SELECT r.meter_id, r.event_time, r.power_kw, b.expected_kw,
       r.power_kw / b.expected_kw AS ratio,
       CASE WHEN r.power_kw / b.expected_kw >= {SPIKE} THEN 'spike' ELSE 'drop' END AS flag_type
FROM energy_readings r
JOIN anomaly_baseline b
  ON b.meter_id = r.meter_id
 AND b.slot = (extract(hour FROM r.event_time) * 4 + extract(minute FROM r.event_time) / 15)::int
 AND b.weekend = (extract(isodow FROM r.event_time) >= 6)
WHERE r.power_kw / b.expected_kw >= {SPIKE} OR r.power_kw / b.expected_kw <= {DROP};
"""


def main() -> None:
    with connect() as conn:
        conn.execute(DETECT)
        conn.commit()
        flags = pd.DataFrame(conn.execute(
            "SELECT meter_id, event_time, power_kw, expected_kw, ratio, flag_type "
            "FROM anomaly_flags ORDER BY meter_id, event_time").fetchall(),
            columns=["meter_id", "event_time", "power_kw", "expected_kw", "ratio", "flag_type"])
        observed = conn.execute("SELECT count(*) FROM energy_readings").fetchone()[0]
        # Observed readings that fall inside injected intervals (needed for the false-flag rate)
        events = pd.DataFrame(sim.anomaly_events())       # labels used ONLY from here on
        events["start"] = events["start_time"].map(datetime.fromisoformat)
        events["end"] = events["end_time"].map(datetime.fromisoformat)
        inside = 0
        for e in events.itertuples():
            inside += conn.execute(
                "SELECT count(*) FROM energy_readings WHERE meter_id = %s "
                "AND event_time >= %s AND event_time < %s", (e.meter_id, e.start, e.end)).fetchone()[0]

    print(f"Rule: flag if power_kw / median(same meter, slot, day type) >= {SPIKE} or <= {DROP}")
    print(f"flags raised: {len(flags):,} (spike {sum(flags.flag_type == 'spike')}, "
          f"drop {sum(flags.flag_type == 'drop')})\n")
    print(flags.head(15).to_string(index=False))
    flags.to_csv(RESULTS / "s5_4_flags.csv", index=False)

    def in_event(row) -> bool:
        mine = events[events.meter_id == row.meter_id]
        return bool(((mine.start <= row.event_time) & (row.event_time < mine.end)).any())

    flags["inside_injected"] = flags.apply(in_event, axis=1)
    results = []
    for e in events.itertuples():
        hits = flags[(flags.meter_id == e.meter_id) & (flags.event_time >= e.start)
                     & (flags.event_time < e.end)]
        results.append((e.meter_id, e.event_type, e.start_time, e.end_time, len(hits),
                        "DETECTED" if len(hits) else "MISSED"))
    detected = sum(r[-1] == "DETECTED" for r in results)
    false_flags = flags[~flags.inside_injected]
    outside = observed - inside

    print("\nComparison with the 40 injected events:")
    print_table(results, ["meter_id", "event_type", "start", "end", "flags_inside", "result"])
    summary = {
        "injected_events": len(events), "detected": detected, "missed": len(events) - detected,
        "flags_total": len(flags), "true_flags": int(flags.inside_injected.sum()),
        "false_flags": len(false_flags), "observed_readings": observed,
        "readings_inside_injected_intervals": inside,
        "readings_outside_injected_intervals": outside,
        "false_flag_pct_of_outside_readings": round(100 * len(false_flags) / outside, 6),
        "meters_with_false_flags": false_flags.meter_id.nunique(),
        "pct_of_meters_affected_by_false_flags": round(100 * false_flags.meter_id.nunique() / 4000, 4),
    }
    print()
    for key, value in summary.items():
        print(f"  {key:40s} {value}")
    pd.DataFrame(results, columns=["meter_id", "event_type", "start", "end", "flags_inside",
                                   "result"]).to_csv(RESULTS / "s5_4_event_evaluation.csv", index=False)
    pd.Series(summary).to_csv(RESULTS / "s5_4_summary.csv", header=["value"])
    if len(false_flags):
        print("\nFalse flags (first 20):")
        print(false_flags.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
