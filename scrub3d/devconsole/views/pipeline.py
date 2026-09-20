"""Pipeline: every stage as a process, with what it did and what it decided."""
import dash
from dash import ALL, Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from devconsole import probes as PR
from devconsole.store import STORE
from devconsole.views import common as C

GROUP_ORDER = PR.GROUPS


def layout(sel, stage=None):
    """`stage` preselects a card: the Overview's diagram links here with it."""
    return html.Div([
        dcc.Interval(id="pl-tick", interval=1000),
        dcc.Store(id="pl-stage",
                  data=stage if stage in PR.stage_index() else None),
        dcc.Store(id="pl-ver", data=None),
        html.Div(id="pl-head"),
        html.Div(C.note(
            "Each card is a stage the code runs. Status comes from the stage's own "
            "report: REFUSED means it worked and said no; FAILED means it raised. "
            "Call counts include per-frame calls; timings are p50 / p95.", "info"),
            style={"margin": "4px 0 10px"}),
        html.Div(id="pl-graph"),
        html.Div(id="pl-detail", style={"marginTop": "12px"}),
    ])


def _card(row, selected):
    st = row["status"]
    meta = []
    if row["calls"]:
        meta.append(f"{row['calls']} call{'s' if row['calls'] != 1 else ''}")
    if row["p50"] is not None:
        meta.append(f"p50 {C.ms(row['p50'])}")
    if row["p95"] is not None and row["calls"] > 3:
        meta.append(f"p95 {C.ms(row['p95'])}")
    if row["refusals"]:
        meta.append(f"{row['refusals']} refused")
    if row["errors"]:
        meta.append(f"{row['errors']} errors")
    last = row.get("last") or {}
    line = last.get("err") or C.one_line(last.get("sum"), 90)
    return html.Div([
        html.Div([html.Span(row["label"]), C.badge(st)], className="name"),
        html.Div("  ·  ".join(meta) or row["what"], className="meta"),
        html.Div(line, className="last", title=line) if line else None,
    ], id={"type": "pl-card", "id": row["id"]}, n_clicks=0,
        className=f"stage b-{C.status_class(st)[2:]}" + (" sel" if selected else ""),
        title=row["what"])


def _graph(rows, selected):
    cols = []
    by = {g: [r for r in rows if r["group"] == g] for g in GROUP_ORDER}
    for i, g in enumerate(GROUP_ORDER):
        active = sum(1 for r in by[g] if r["status"] not in ("not called", "idle", "n/a"))
        cols.append(html.Div([
            html.H4([html.Span(f"{g}  ({active}/{len(by[g])})"),
                     html.Span("→" if i < len(GROUP_ORDER) - 1 else "",
                               className="arrow")]),
            *[_card(r, r["id"] == selected) for r in by[g]],
        ], className="pipe-col"))
    return html.Div(cols, className="pipe")


def _detail(rid, sid, rows):
    row = next((r for r in rows if r["id"] == sid), None)
    if row is None:
        return None
    det = STORE.stage_detail(rid, sid) or {}
    spec = PR.stage_index()[sid]
    fn_rows = []
    for t in spec.targets:
        a = det.get("fn_aggs", {}).get(t.key) or {}
        fn_rows.append([t.key, t.mode, a.get("n"), C.ms(a.get("p50")), C.ms(a.get("p95")),
                        C.ms(a.get("max")), a.get("ref"), a.get("err"), a.get("drop"),
                        C.badge("probe-missing", "missing") if t.key in det.get("missing", [])
                        else ""])
    recent = list(det.get("recent", []))[-40:][::-1]
    recent_rows = [[C.clock(e.get("t")), e.get("fn"), C.ms(e.get("ms")),
                    C.badge("refused" if e.get("refused") else ("ok" if e.get("ok") else "failed"),
                            "refused" if e.get("refused") else ("ok" if e.get("ok") else "failed")),
                    html.Span(e.get("err") or C.one_line(e.get("sum"), 140),
                              className="muted")] for e in recent]
    lasts = [html.Div([html.B(fn), C.json_tree(ev.get("sum"), open_depth=1)],
                      style={"marginBottom": "8px"})
             for fn, ev in (det.get("last_by_fn") or {}).items()]
    return C.card(
        f"{spec.group} · {spec.label}",
        html.Div(spec.what, className="muted", style={"marginBottom": "8px"}),
        html.Div([
            html.Div(C.card("Call boundaries", C.table(
                ["target", "mode", "calls", "p50", "p95", "max", "refused", "errors",
                 "dropped", ""], fn_rows, num=(2, 3, 4, 5, 6, 7, 8), mono=(0,))),
                className="col-2"),
            html.Div(C.card("Latest report from each", *lasts) if lasts
                     else C.card("Latest report from each", C.empty("Not called.")),
                     className="col"),
        ], className="row"),
        html.Div(C.card("Recent calls", C.table(
            ["time", "target", "took", "", "summary"], recent_rows, mono=(1,),
            max_height="360px")), style={"marginTop": "12px"}),
        right=C.badge(row["status"]))


@dash.callback(Output("pl-stage", "data"),
               Input({"type": "pl-card", "id": ALL}, "n_clicks"),
               State("pl-stage", "data"), prevent_initial_call=True)
def pick(clicks, current):
    trig = ctx.triggered_id
    if not trig or not any(clicks or []):
        raise PreventUpdate
    for item in ctx.triggered:
        if item.get("value"):
            return None if current == trig["id"] else trig["id"]
    raise PreventUpdate


@dash.callback(Output("pl-head", "children"), Output("pl-graph", "children"),
               Output("pl-detail", "children"), Output("pl-ver", "data"),
               Input("pl-tick", "n_intervals"), Input("pl-stage", "data"),
               Input("sel-run", "data"), State("pl-ver", "data"))
def render(_n, stage, sel, ver):
    rid = C.resolve_run(sel)
    v = STORE.version(rid, "stages") if rid else None
    key = [rid, v, stage]
    if ver == key and ctx.triggered_id == "pl-tick":
        raise PreventUpdate
    rows = STORE.stage_table(rid) if rid else [
        {"id": s.id, "group": s.group, "label": s.label, "what": s.what,
         "status": "idle", "calls": 0, "p50": None, "p95": None, "refusals": 0,
         "errors": 0, "last": None} for s in PR.STAGES]
    detail = _detail(rid, stage, rows) if (rid and stage) else C.note(
        "Click a stage to see its call boundaries, the latest report from each, "
        "and its recent calls.", "info")
    return C.run_header(rid), _graph(rows, stage), detail, key
