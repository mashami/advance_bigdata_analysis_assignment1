"""Pause, resume or list the assignment's TimescaleDB background jobs.

    python src/jobs.py status
    python src/jobs.py pause     # before timing in 4.3 and 7.4
    python src/jobs.py resume    # after timing

Covers compression/columnstore policies and continuous-aggregate refresh
policies (user jobs have id >= 1000; lower ids are TimescaleDB's own jobs).
"""
import sys

from common import connect, print_table

STATUS = """
SELECT j.job_id, j.proc_name, j.hypertable_name, j.schedule_interval, j.scheduled,
       j.config::text AS config, s.last_run_status, s.next_start
FROM timescaledb_information.jobs j
LEFT JOIN timescaledb_information.job_stats s USING (job_id)
WHERE j.job_id >= 1000
ORDER BY j.job_id
"""


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    with connect(autocommit=True) as conn:
        if action in ("pause", "resume"):
            only = sys.argv[2:]  # optional hypertable/view names
            for job_id, name in conn.execute(
                    "SELECT job_id, hypertable_name FROM timescaledb_information.jobs "
                    "WHERE job_id >= 1000").fetchall():
                if not only or name in only:
                    conn.execute("SELECT alter_job(%s, scheduled => %s)",
                                 (job_id, action == "resume"))
        rows = conn.execute(STATUS).fetchall()
    print_table(rows, ["job_id", "proc", "hypertable", "schedule", "scheduled", "config",
                       "last_status", "next_start"])


if __name__ == "__main__":
    main()
