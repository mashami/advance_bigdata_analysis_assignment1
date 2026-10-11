"""Shared helpers: database connection, CAT clock, result folders.

Connection settings come from the standard PG* environment variables
(see .env.example). No password is stored in code.
"""
from __future__ import annotations

import csv
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # makes smart_meter_simulator importable

# Optional .env (KEY=VALUE lines); real environment variables take precedence.
if (ROOT / ".env").exists():
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

import psycopg  # noqa: E402

RESULTS = ROOT / "results"
FIGURES = RESULTS / "figures"
LOGS = ROOT / "logs"
DATA = ROOT / "data"
for folder in (RESULTS, FIGURES, LOGS, DATA):
    folder.mkdir(exist_ok=True)

CAT = timezone(timedelta(hours=2), "CAT")
MACHINE = socket.gethostname()

DEFAULTS = {"host": "localhost", "port": "5433", "user": "postgres", "dbname": "energy_grid"}


def connect(dbname: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    """Open a connection; every session uses the Africa/Kigali (CAT) time zone."""
    params = {
        "host": os.getenv("PGHOST", DEFAULTS["host"]),
        "port": os.getenv("PGPORT", DEFAULTS["port"]),
        "user": os.getenv("PGUSER", DEFAULTS["user"]),
        "dbname": dbname or os.getenv("PGDATABASE", DEFAULTS["dbname"]),
    }
    if os.getenv("PGPASSWORD"):
        params["password"] = os.getenv("PGPASSWORD")
    conn = psycopg.connect(**params, autocommit=autocommit,
                           options="-c timezone=Africa/Kigali")
    return conn


def now_cat() -> datetime:
    return datetime.now(CAT)


def cat_text(value: datetime | None = None) -> str:
    return (value or now_cat()).astimezone(CAT).isoformat(timespec="milliseconds")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def print_table(rows: list, headers: list[str]) -> None:
    """Small fixed-width table printer for terminal screenshots."""
    text = [[str(h) for h in headers]] + [[_fmt(v) for v in row] for row in rows]
    widths = [max(len(r[i]) for r in text) for i in range(len(headers))]
    line = "+".join("-" * (w + 2) for w in widths)
    for n, row in enumerate(text):
        print(" | ".join(v.rjust(w) for v, w in zip(row, widths)))
        if n == 0:
            print(line)


def _fmt(value) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, float):
        return f"{value:,.6f}" if abs(value) < 1000 else f"{value:,.3f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


class Timer:
    """Context manager recording perf_counter seconds and CAT wall-clock bounds."""

    def __enter__(self):
        self.start_cat = now_cat()
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.seconds = time.perf_counter() - self._t0
        self.end_cat = now_cat()
        return False
