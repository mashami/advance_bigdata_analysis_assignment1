"""Section 7: Raw and Aggregate dashboards (Plotly Dash).

    python dashboard/app.py --mode raw        # http://127.0.0.1:8050  "Raw"
    python dashboard/app.py --mode aggregate  # http://127.0.0.1:8051  "Aggregate"

Both dashboards have the same five panels, layout and filters (whole CAT days +
meter). Only the SQL differs (dashboard/queries.py). No result caching is used:
every refresh runs all five queries against PostgreSQL.

Timing (7.4)
  * per panel: server-side time.perf_counter() around execute + fetch
  * whole dashboard: browser performance.now() from the Refresh click until all
    five Plotly graphs have drawn the figure belonging to that refresh
    (each figure carries the refresh token in layout.meta; the browser waits for
    all five, then two animation frames so the paint has happened)
  * "Run timing (1 warm-up + 10)" clicks Refresh 11 times automatically and
    appends every refresh to results/dashboard_timings.csv
"""
import argparse
import csv
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html
from flask import jsonify, request
from psycopg_pool import ConnectionPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CAT, MACHINE, RESULTS, cat_text  # noqa: E402
from queries import PANELS, run_panel  # noqa: E402

TIMINGS = RESULTS / "dashboard_timings.csv"
PANEL_IDS = list(PANELS)


def make_pool() -> ConnectionPool:
    import os
    conninfo = (f"host={os.getenv('PGHOST', 'localhost')} port={os.getenv('PGPORT', '5433')} "
                f"user={os.getenv('PGUSER', 'postgres')} dbname={os.getenv('PGDATABASE', 'energy_grid')}")
    if os.getenv("PGPASSWORD"):
        conninfo += f" password={os.getenv('PGPASSWORD')}"
    return ConnectionPool(conninfo, min_size=5, max_size=5, open=True,
                          kwargs={"options": "-c timezone=Africa/Kigali", "autocommit": True})


def figure(panel: str, frame, token) -> go.Figure:
    fig = go.Figure()
    if panel == "hourly_regional":
        for region, part in frame.groupby("region"):
            fig.add_scatter(x=part["bucket"], y=part["energy_kwh"], mode="lines", name=region)
        fig.update_yaxes(title="Energy (kWh)")
    elif panel == "completeness":
        fig.add_bar(x=frame["region"], y=frame["completeness_pct"],
                    text=frame["completeness_pct"].round(3), textposition="outside")
        fig.update_yaxes(title="Completeness (%)", range=[95, 100.5])
    elif panel == "weekly_network":
        labels = [f"{b:%d %b} +7d" for b in frame["bucket"]]
        fig.add_bar(x=labels, y=frame["energy_kwh"], text=frame["energy_kwh"].round(1),
                    textposition="outside")
        fig.update_yaxes(title="Energy (kWh)")
    else:
        fig.add_bar(x=frame["bucket"], y=frame["energy_kwh"])
        fig.update_yaxes(title="Energy (kWh)")
    fig.update_layout(title=PANELS[panel], meta=token, height=330,
                      margin=dict(l=60, r=20, t=50, b=40), legend=dict(font=dict(size=9)))
    return fig


def build(mode: str) -> Dash:
    title = "Raw" if mode == "raw" else "Aggregate"
    app = Dash(__name__, title=title)
    pool = make_pool()
    with pool.connection() as conn:
        meters = [r[0] for r in conn.execute("SELECT meter_id FROM meters ORDER BY meter_id")]

    app.layout = html.Div(style={"fontFamily": "sans-serif", "padding": "10px", "minHeight": "100vh",
                                 "background": "#ffffff", "color": "#222222"}, children=[
        html.H2(f"{title} dashboard", id="dash-title"),
        html.Div(style={"display": "flex", "gap": "16px", "alignItems": "center", "flexWrap": "wrap"}, children=[
            html.Label("CAT days (inclusive):"),
            dcc.DatePickerRange(id="dates", start_date=date(2026, 9, 22), end_date=date(2026, 9, 28),
                                min_date_allowed=date(2026, 9, 1), max_date_allowed=date(2026, 9, 28),
                                display_format="YYYY-MM-DD"),
            html.Label("Meter:"),
            dcc.Dropdown(id="meter", options=meters, value=1000000000, clearable=False,
                         style={"width": "180px"}),
            html.Button("Refresh", id="refresh-btn", n_clicks=0),
            html.Button("Run timing (1 warm-up + 10)", id="bench-btn", n_clicks=0),
            html.Span(id="status", style={"fontFamily": "monospace"}),
        ]),
        dcc.Store(id="token", data=0),
        *[dcc.Store(id=f"done-{p}") for p in PANEL_IDS],
        html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "8px"},
                 children=[dcc.Graph(id=f"graph-{p}", config={"displayModeBar": False},
                                     style={"gridColumn": "1 / span 2"} if p == "hourly_regional" else {})
                           for p in PANEL_IDS]),
    ])

    # Refresh click -> new token; remember the click time in the browser.
    app.clientside_callback(
        """function(n) { window.dashT0 = performance.now(); window.dashToken = n; return n; }""",
        Output("token", "data"), Input("refresh-btn", "n_clicks"))

    def panel_callback(panel):
        @app.callback(Output(f"graph-{panel}", "figure"), Output(f"done-{panel}", "data"),
                      Input("token", "data"), State("dates", "start_date"),
                      State("dates", "end_date"), State("meter", "value"))
        def update(token, start_date, end_date, meter):
            start = datetime.fromisoformat(start_date).replace(tzinfo=CAT)
            end = datetime.fromisoformat(end_date).replace(tzinfo=CAT) + timedelta(days=1)
            t0 = time.perf_counter()
            with pool.connection() as conn:
                frame = run_panel(conn, mode, panel, start, end, int(meter))
            ms = (time.perf_counter() - t0) * 1000
            return figure(panel, frame, token), {"token": token, "ms": ms, "rows": len(frame)}

    for p in PANEL_IDS:
        panel_callback(p)

    # When all five panels report the current token, wait until every graph has
    # drawn that figure, record the elapsed time and (in timing mode) click again.
    app.clientside_callback(
        """
        function(...done) {
            const token = window.dashToken;
            if (!window.dashT0 || done.some(d => !d || d.token !== token)) {
                return window.dash_clientside.no_update;
            }
            const ids = %s;
            const t0 = window.dashT0;
            window.dashT0 = null;
            const drawn = () => ids.every(id => {
                const gd = document.querySelector('#graph-' + id + ' .js-plotly-plot');
                return gd && gd.layout && gd.layout.meta === token;
            });
            const finish = () => requestAnimationFrame(() => requestAnimationFrame(() => {
                const total = performance.now() - t0;
                const bench = window.benchIndex;
                const label = bench === undefined ? 'manual' : (bench === 0 ? 'warm-up' : String(bench));
                const panels = Object.fromEntries(ids.map((id, i) => [id, done[i].ms]));
                fetch('/api/timing', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({run: label, total_ms: total, panels: panels})});
                document.getElementById('status').innerText =
                    'refresh ' + label + ': ' + total.toFixed(1) + ' ms';
                if (bench !== undefined) {
                    if (bench < 10) {
                        window.benchIndex = bench + 1;
                        setTimeout(() => document.getElementById('refresh-btn').click(), 500);
                    } else {
                        window.benchIndex = undefined;
                        document.getElementById('status').innerText += '  -- timing run complete';
                    }
                }
            }));
            const wait = () => drawn() ? finish() : setTimeout(wait, 5);
            wait();
            return window.dash_clientside.no_update;
        }
        """ % PANEL_IDS,
        Output("status", "title"), [Input(f"done-{p}", "data") for p in PANEL_IDS])

    app.clientside_callback(
        """function(n) { if (n) { window.benchIndex = 0;
               document.getElementById('refresh-btn').click(); }
           return window.dash_clientside.no_update; }""",
        Output("bench-btn", "title"), Input("bench-btn", "n_clicks"), prevent_initial_call=True)

    @app.server.route("/api/timing", methods=["POST"])
    def record_timing():
        body = request.get_json()
        row = {"recorded_cat": cat_text(), "mode": title, "run": body["run"],
               "machine": MACHINE, "browser": request.headers.get("User-Agent", "")[:120],
               "total_ms": round(body["total_ms"], 3),
               **{f"{p}_query_ms": round(body["panels"][p], 3) for p in PANEL_IDS}}
        new = not TIMINGS.exists()
        with open(TIMINGS, "a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            if new:
                writer.writeheader()
            writer.writerow(row)
        return jsonify(ok=True)

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["raw", "aggregate"], required=True)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    port = args.port or (8050 if args.mode == "raw" else 8051)
    build(args.mode).run(host="127.0.0.1", port=port, debug=False)
