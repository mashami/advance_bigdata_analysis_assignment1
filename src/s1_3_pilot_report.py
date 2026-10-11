"""Section 1.3: combine the four pilot counts into one table and show sample rows."""
import json

from common import RESULTS, connect, print_table


def main() -> None:
    pub = json.loads((RESULTS / "pilot_publisher.json").read_text())
    sub = json.loads((RESULTS / "pilot_subscriber.json").read_text())
    with connect() as conn:
        stored = conn.execute("SELECT count(*) FROM energy_live").fetchone()[0]
        distinct = conn.execute(
            "SELECT count(*) FROM (SELECT DISTINCT meter_id, event_time FROM energy_live) d"
        ).fetchone()[0]
        sample = conn.execute(
            "SELECT * FROM energy_live ORDER BY event_time, meter_id LIMIT 5").fetchall()

    stages = [("1. dictionaries taken from generator", pub["taken_from_generator"]),
              ("2. messages acknowledged by broker", pub["acknowledged_by_broker"]),
              ("3. messages received by subscriber", sub["received"]),
              ("4. rows committed in energy_live", stored)]
    print_table([(name, n, n - 2000) for name, n in stages], ["stage", "count", "diff vs 2,000"])
    print(f"\nsubscriber parse errors={sub['parse_errors']}  insert errors={sub['insert_errors']}"
          f"  DUP-flag redeliveries={sub['redelivered_dup_flag']}")
    print(f"distinct (meter_id, event_time) pairs in energy_live: {distinct} "
          f"(deliberate generator duplicates = {stored - distinct})\n")
    print_table(sample, ["meter_id", "event_time", "received_time", "power_kw", "voltage_v",
                         "current_a", "frequency_hz", "energy_kwh"])
    (RESULTS / "pilot_counts.json").write_text(json.dumps(dict(stages), indent=2))


if __name__ == "__main__":
    main()
