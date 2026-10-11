"""Panel queries for the Raw and Aggregate dashboards (Section 7).

Every function takes (conn, start, end, meter_id) where start/end are CAT
midnights and end is exclusive. Both modes return identical columns so the
results can be compared row by row (7.3).

Raw       -> energy_readings (+ meters for region / registered counts)
Aggregate -> continuous aggregates only (+ meters for labels / registered counts)
"""
from datetime import datetime

import pandas as pd

PERIOD_ORIGIN = "2026-09-01 00:00+02"   # seven-day periods of 5.1 start 1, 8, 15, 22 Sep

PANELS = {
    "hourly_regional": "Hourly energy by region (kWh)",
    "daily_network": "Daily network energy (kWh)",
    "weekly_network": "Network energy per seven-day period (kWh)",
    "meter_daily": "Daily energy of selected meter (kWh)",
    "completeness": "Completeness by region (%)",
}

SQL = {
    "raw": {
        "hourly_regional": """
            SELECT time_bucket('1 hour', r.event_time, 'Africa/Kigali') AS bucket, m.region,
                   sum(r.energy_kwh) AS energy_kwh, count(*) AS reading_count
            FROM energy_readings r JOIN meters m ON m.meter_id = r.meter_id
            WHERE r.event_time >= %(start)s AND r.event_time < %(end)s
            GROUP BY 1, 2 ORDER BY 1, 2""",
        "daily_network": """
            SELECT time_bucket('1 day', event_time, 'Africa/Kigali') AS bucket,
                   sum(energy_kwh) AS energy_kwh, count(*) AS reading_count
            FROM energy_readings
            WHERE event_time >= %(start)s AND event_time < %(end)s
            GROUP BY 1 ORDER BY 1""",
        "weekly_network": f"""
            SELECT time_bucket('7 days', event_time, 'Africa/Kigali', TIMESTAMPTZ '{PERIOD_ORIGIN}') AS bucket,
                   sum(energy_kwh) AS energy_kwh, count(*) AS reading_count
            FROM energy_readings
            WHERE event_time >= %(start)s AND event_time < %(end)s
            GROUP BY 1 ORDER BY 1""",
        "meter_daily": """
            SELECT time_bucket('1 day', event_time, 'Africa/Kigali') AS bucket, meter_id,
                   sum(energy_kwh) AS energy_kwh, count(*) AS reading_count
            FROM energy_readings
            WHERE meter_id = %(meter)s AND event_time >= %(start)s AND event_time < %(end)s
            GROUP BY 1, 2 ORDER BY 1""",
        "completeness": """
            WITH reg AS (SELECT region, count(*) AS meters FROM meters GROUP BY region),
                 obs AS (SELECT m.region, count(*) AS reading_count
                         FROM energy_readings r JOIN meters m ON m.meter_id = r.meter_id
                         WHERE r.event_time >= %(start)s AND r.event_time < %(end)s
                         GROUP BY m.region)
            SELECT reg.region, coalesce(obs.reading_count, 0) AS reading_count,
                   reg.meters * %(slots)s AS planned,
                   100.0 * coalesce(obs.reading_count, 0) / (reg.meters * %(slots)s) AS completeness_pct
            FROM reg LEFT JOIN obs USING (region) ORDER BY reg.region""",
    },
    "aggregate": {
        "hourly_regional": """
            SELECT bucket, region, sum_energy_kwh AS energy_kwh, reading_count
            FROM cagg_energy_hour_region
            WHERE bucket >= %(start)s AND bucket < %(end)s
            ORDER BY 1, 2""",
        "daily_network": """
            SELECT time_bucket('1 day', bucket, 'Africa/Kigali') AS bucket,
                   sum(sum_energy_kwh) AS energy_kwh, sum(reading_count)::bigint AS reading_count
            FROM cagg_energy_15m_region
            WHERE bucket >= %(start)s AND bucket < %(end)s
            GROUP BY 1 ORDER BY 1""",
        "weekly_network": f"""
            SELECT time_bucket('7 days', bucket, 'Africa/Kigali', TIMESTAMPTZ '{PERIOD_ORIGIN}') AS bucket,
                   sum(sum_energy_kwh) AS energy_kwh, sum(reading_count)::bigint AS reading_count
            FROM cagg_energy_15m_region
            WHERE bucket >= %(start)s AND bucket < %(end)s
            GROUP BY 1 ORDER BY 1""",
        "meter_daily": """
            SELECT bucket, meter_id, sum_energy_kwh AS energy_kwh, reading_count
            FROM cagg_energy_day_meter
            WHERE meter_id = %(meter)s AND bucket >= %(start)s AND bucket < %(end)s
            ORDER BY 1""",
        "completeness": """
            WITH reg AS (SELECT region, count(*) AS meters FROM meters GROUP BY region),
                 obs AS (SELECT region, sum(reading_count)::bigint AS reading_count
                         FROM cagg_energy_15m_region
                         WHERE bucket >= %(start)s AND bucket < %(end)s
                         GROUP BY region)
            SELECT reg.region, coalesce(obs.reading_count, 0) AS reading_count,
                   reg.meters * %(slots)s AS planned,
                   100.0 * coalesce(obs.reading_count, 0) / (reg.meters * %(slots)s) AS completeness_pct
            FROM reg LEFT JOIN obs USING (region) ORDER BY reg.region""",
    },
}


def run_panel(conn, mode: str, panel: str, start: datetime, end: datetime, meter: int) -> pd.DataFrame:
    """Planned readings per meter = whole CAT days in the range x 96."""
    days = round((end - start).total_seconds() / 86400)
    params = {"start": start, "end": end, "meter": meter, "slots": days * 96}
    cur = conn.execute(SQL[mode][panel], params)
    frame = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    for col in ("energy_kwh", "completeness_pct"):
        if col in frame:
            frame[col] = frame[col].astype(float)
    return frame
