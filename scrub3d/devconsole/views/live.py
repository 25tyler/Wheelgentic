"""Live loop: what the loop logs every frame, streamed."""
import dash
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from devconsole import inventory as INV
from devconsole.store import STORE
from devconsole.views import common as C

MAX_POINTS = 3000

LAT_COLOURS = ["#6ea8fe", "#4cc38a", "#d58cf2", "#f2b24c", "#f06a6a", "#8a93a1"]


def _graphs():
    jump = INV.constant("track", "MAX_JUMP_MM", 25.0)
    okf = INV.constant("adapt", "OK_FRACTION", 0.55)
    return [
        {"id": "lv-fps", "title": "Frame rate (frames per second)",
         "traces": [("feed/fps", "frames / s", "#6ea8fe", "lines")], "line": None},
        {"id": "lv-lat", "title": "Time per frame, by stage (ms)",
         "traces": [(k, lbl, LAT_COLOURS[i], "markers" if "reach" in k else "lines")
                    for i, (k, lbl) in enumerate([
             ("ms/tracking", "tracking"), ("ms/coverage", "coverage control"),
             ("ms/skinning", "re-pose"), ("ms/loop", "loop helpers"),
             ("ms/reachability", "reachability (1 Hz)"),
             ("feed/produce_ms", "reading the frame")])], "line": None, "log": True},
        {"id": "lv-jump",
         "title": f"Furthest any body cell moved per frame (mm) — freeze above {jump:g}",
         "traces": [("track/jump_mm", "jump", "#f2b24c", "lines")],
         "line": (jump, "freeze")},
        {"id": "lv-flags", "title": "Tracking flags and valid depth",
         "traces": [("track/ok", "tracked", "#4cc38a", "lines"),
                    ("track/freeze", "frozen", "#f06a6a", "markers"),
                    ("track/away", "not in the scanned seat", "#f2b24c", "markers"),
                    ("track/stop", "stop", "#d58cf2", "markers"),
                    ("feed/depth_valid", "valid depth fraction", "#8a93a1", "lines")],
         "line": None},
        # Both per-arm series are logged only when they change, so a value holds
        # until the next point: steps, not ramps.
        {"id": "lv-cov", "title": "Scrubbed, per arm (% of what it can reach)",
         "traces": [(f"coverage/arm_{a}", f"arm {a}", C.ARM_COLOURS[a], "lines", "hv")
                    for a in range(4)], "line": None},
        {"id": "lv-reach", "title": f"Remaining work still reachable, per arm (%) — "
                                    f"handoff below {okf * 100:g}",
         "traces": [(f"reach/arm_{a}", f"arm {a}", C.ARM_COLOURS[a], "lines+markers",
                     "hv") for a in range(4)], "line": (okf * 100, "handoff")},
    ]


def layout(sel):
    graphs = _graphs()
    return html.Div([
        dcc.Interval(id="lv-tick", interval=500),
        dcc.Store(id="lv-cursor", data=None),
        html.Div(id="lv-head"),
        html.Div(id="lv-summary"),
        html.Div([html.Div(C.card(g["title"], dcc.Graph(
            id=g["id"], figure=_empty(g), config={"displaylogo": False},
            style={"height": "260px"})), className="col")
            for g in graphs], className="row"),
        C.note("Everything here is what viz.live already logs to Rerun, mirrored as it "
               "is logged, plus the probes' own per-frame timings. X is the frame "
               "number. Threshold lines are the code's own constants.", "info"),
    ])


def _empty(g):
    fig = go.Figure()
    for key, label, colour, mode, *shape in g["traces"]:
        fig.add_trace(go.Scattergl(x=[], y=[], name=label, mode=mode,
                                   line=dict(color=colour, width=1.5,
                                             shape=shape[0] if shape else "linear"),
                                   marker=dict(color=colour, size=5)))
    lay = dict(C.PLOT_LAYOUT)
    lay["uirevision"] = g["id"]
    if g.get("log"):
        lay["yaxis"] = dict(lay.get("yaxis") or {}, type="log")
    if g["line"]:
        y, label = g["line"]
        lay["shapes"] = [dict(type="line", xref="paper", x0=0, x1=1, y0=y, y1=y,
                              line=dict(color="#f06a6a", dash="dot", width=1))]
        lay["annotations"] = [dict(xref="paper", x=1, y=y, text=label, showarrow=False,
                                   font=dict(size=10, color="#f06a6a"),
                                   xanchor="right", yanchor="bottom")]
    fig.update_layout(**lay)
    return fig


def _keep(trace, pts):
    """Flag series are logged every frame as 0 or 1; a marker only means 1."""
    key, _label, _colour, mode, *_shape = trace
    if mode == "markers" and key.startswith("track/"):
        return [p for p in pts if p[2]]
    return pts


def _thin(pts):
    if len(pts) <= MAX_POINTS:
        return pts
    step = len(pts) / MAX_POINTS
    return [pts[int(i * step)] for i in range(MAX_POINTS)]


def _summary(rid):
    rows = {r["id"]: r for r in STORE.stage_table(rid)}
    snap = STORE.snapshot(rid) or {}
    s = STORE.series(rid, ["feed/fps"]).get("feed/fps", [])
    fps = [p[2] for p in s]
    track = rows.get("tracking", {})
    reach = STORE.stage_detail(rid, "reachability") or {}
    handoffs = sum(len((e.get("sum") or {}).get("handoffs") or [])
                   for e in reach.get("recent", []) if e.get("fn") == "adapt.Adapter.check")
    stops = sum(1 for e in reach.get("recent", [])
                if e.get("fn") == "adapt.Adapter.check" and (e.get("sum") or {}).get("stop"))
    frames = rows.get("feed", {})
    items = [
        ("frames", (frames.get("last") or {}).get("sum", {}).get("frames")
         if (frames.get("last") or {}).get("fn") == "rsfeed.Feed.frames" else len(s)),
        ("tracker calls", track.get("calls")),
        ("frozen, lost or not seated", track.get("refusals")),
        ("tracker p50 / p95", f"{C.ms(track.get('p50'))} / {C.ms(track.get('p95'))}"),
        ("mean frames/s", f"{sum(fps) / len(fps):.1f}" if fps else "—"),
        ("handoffs", handoffs), ("stops", stops),
        ("entities logged", len(snap.get("entities") or {})),
    ]
    return html.Div([html.Span([html.Span(f"{k} ", className="muted"), html.B(C.fmt(v))],
                               className="dc-chip") for k, v in items],
                    style={"display": "flex", "flexWrap": "wrap", "gap": "8px",
                           "margin": "4px 0 12px"})


_OUT = []
for _g in _graphs():
    _OUT += [Output(_g["id"], "figure"), Output(_g["id"], "extendData")]


@dash.callback(*_OUT, Output("lv-cursor", "data"), Output("lv-head", "children"),
               Output("lv-summary", "children"),
               Input("lv-tick", "n_intervals"), Input("sel-run", "data"),
               State("lv-cursor", "data"))
def stream(_n, sel, cursor):
    rid = C.resolve_run(sel)
    graphs = _graphs()
    if not rid:
        out = []
        for g in graphs:
            out += [_empty(g), no_update]
        return (*out, None, C.run_header(None), None)
    ver = STORE.version(rid, "series")
    same_run = cursor and cursor.get("run") == rid
    if same_run and cursor.get("ver") == ver and ctx.triggered_id == "lv-tick":
        raise PreventUpdate
    names = [t[0] for g in graphs for t in g["traces"]]
    if not same_run:
        data = STORE.series(rid, names)
        out = []
        last_n = 0
        for g in graphs:
            fig = _empty(g)
            for i, trace in enumerate(g["traces"]):
                raw = data.get(trace[0], [])
                if raw:
                    last_n = max(last_n, raw[-1][0])
                pts = _thin(_keep(trace, raw))
                fig.data[i].x = [p[0] for p in pts]
                fig.data[i].y = [p[2] for p in pts]
            out += [fig, no_update]
        return (*out, {"run": rid, "n": last_n, "ver": ver}, C.run_header(rid),
                _summary(rid))
    after = cursor.get("n", 0)
    data = STORE.series(rid, names, after_n=after)
    out = []
    last_n = after
    for g in graphs:
        xs, ys, idx = [], [], []
        for i, trace in enumerate(g["traces"]):
            raw = data.get(trace[0], [])
            if raw:
                last_n = max(last_n, raw[-1][0])
            pts = _keep(trace, raw)
            xs.append([p[0] for p in pts])
            ys.append([p[2] for p in pts])
            idx.append(i)
        if any(xs):
            out += [no_update, (dict(x=xs, y=ys), idx, MAX_POINTS)]
        else:
            out += [no_update, no_update]
    head = C.run_header(rid) if (_n or 0) % 4 == 0 else no_update
    summ = _summary(rid) if (_n or 0) % 4 == 0 else no_update
    return (*out, {"run": rid, "n": last_n, "ver": ver}, head, summ)
