"""Section 1.2: insert the 4,000 meter_metadata() dictionaries into meters."""
from common import connect
import smart_meter_simulator as sim

COLUMNS = ("meter_id", "region", "customer_type", "base_power_kw", "power_factor")


def main() -> None:
    meters = sim.meter_metadata()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("TRUNCATE meters CASCADE")
        cur.executemany(
            f"INSERT INTO meters ({', '.join(COLUMNS)}) VALUES (%s, %s, %s, %s, %s)",
            [tuple(m[c] for c in COLUMNS) for m in meters])
        conn.commit()
        count = cur.execute("SELECT count(*) FROM meters").fetchone()[0]
    print(f"dictionaries returned by meter_metadata(): {len(meters):,}")
    print(f"rows in meters after commit             : {count:,}")
    assert count == 4000, "expected 4,000 meters"


if __name__ == "__main__":
    main()
