"""Logs: every line a run printed, and every error a probe saw."""
import dash
from dash import Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from devconsole.store import STORE
from devconsole.views import common as C

MAX_LINES = 2500


def layout(sel):
    return html.Div([
        dcc.Interval(id="lg-tick", interval=1000),
        dcc.Store(id="lg-ver", data=None),
        html.Div(id="lg-head"),
        html.Div([
            dcc.Checklist(id="lg-level", inline=True, value=["info", "warn", "error"],
                          options=[{"label": f" {x} ", "value": x}
                                   for x in ("info", "warn", "error")],
                          inputStyle={"marginLeft": "10px"}),
            dcc.Checklist(id="lg-source", inline=True,
                          value=["stdout", "probe", "python", "console"],
                          options=[{"label": f" {x} ", "value": x} for x in
                                   ("stdout", "probe", "python", "console")],
                          inputStyle={"marginLeft": "10px"}),
            dcc.Checklist(id="lg-children", inline=True, value=["on"],
                          options=[{"label": " include nested runs", "value": "on"}],
                          inputStyle={"marginLeft": "10px"}),
            dcc.Input(id="lg-q", type="text", placeholder="filter text", debounce=True,
                      className="dc-in", style={"maxWidth": "320px"}),
        ], style={"display": "flex", "flexWrap": "wrap", "gap": "14px",
                  "alignItems": "center", "margin": "6px 0 10px"}),
        html.Div(id="lg-body"),
    ])


@dash.callback(Output("lg-head", "children"), Output("lg-body", "children"),
               Output("lg-ver", "data"), Input("lg-tick", "n_intervals"),
               Input("sel-run", "data"), Input("lg-level", "value"),
               Input("lg-source", "value"), Input("lg-children", "value"),
               Input("lg-q", "value"), State("lg-ver", "data"))
def render(_n, sel, levels, sources, children, q, ver):
    rid = C.resolve_run(sel)
    if not rid:
        return C.run_header(None), None, None
    ids = [rid]
    if children:
        snap = STORE.snapshot(rid) or {}
        for d in snap.get("children") or []:
            for r in STORE.list_runs():
                if r["nested"] and d and d.replace("\\", "/").endswith(r["id"].split("/")[-1]):
                    ids.append(r["id"])
    key = [ids, [STORE.version(i, "logs") for i in ids], levels, sources, children, q]
    if key == ver and ctx.triggered_id == "lg-tick":
        raise PreventUpdate
    lines = []
    for i in ids:
        tag = "" if i == rid else f"[{i.split('/')[-1]}] "
        for seq, t, level, source, text in STORE.logs(i, limit=MAX_LINES):
            if level not in (levels or []) or source not in (sources or []):
                continue
            if q and q.lower() not in text.lower():
                continue
            lines.append((t, tag, level, source, text))
    lines.sort(key=lambda x: x[0])
    lines = lines[-MAX_LINES:]
    body = html.Div([
        html.Div([html.Span(f"{C.clock(t)} {src:7s} ", className="src"),
                  html.Span(tag + text, className=f"l-{level}")])
        for t, tag, level, src, text in lines], className="log", id="lg-box")
    return (C.run_header(rid),
            html.Div([html.Div(f"{len(lines)} line(s) shown", className="faint"), body]),
            key)
