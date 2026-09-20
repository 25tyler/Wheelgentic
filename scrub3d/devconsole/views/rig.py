"""Rig & placement: where the arms are, the limits they work to, and the search."""
import math
import time

import dash
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from devconsole import inventory as INV
from devconsole.store import STORE
from devconsole.views import common as C

LIMITS = [
    ("collide", ("R_BASE", "R_UPPER", "R_FORE", "R_SPONGE", "R_EOAT", "D_HOLD",
                 "D_ESTOP", "D_BODY")),
    ("fleet", ("HOLD_MAX_S", "RETREAT_MM", "J3_MAX_STEP_RAD", "DROOP_MM")),
    ("track", ("MAX_JUMP_MM",)),
    ("adapt", ("PERSIST_S", "OK_FRACTION", "MAX_HANDOFFS_PER_PAIR")),
    ("partition", ("ENVELOPE_K", "LEAN_MM")),
    ("place_arms", ("MIN_BASE_SEPARATION_MM",)),
    ("session", ("ARM_TIMEOUT_S", "SESSION_BUDGET_S")),
    ("armlink", ("TORQUE_REASSERT_S", "FEEDBACK_STALE_S")),
    ("bodystore", ("IDENTITY_TOLERANCE", "MAX_ASYMMETRY")),
    ("handeye", ("GO_MM", "HOVER_MM", "MIN_CONDITION")),
]


def layout(sel):
    return html.Div([
        dcc.Interval(id="rg-tick", interval=2000),
        dcc.Store(id="rg-ver", data=None),
        html.Div(_static(), className="row"),
        html.Div(id="rg-head", style={"marginTop": "12px"}),
        html.Div(id="rg-search"),
    ])


def _topdown(arms):
    fig = go.Figure()
    fig.add_shape(type="circle", x0=-180, y0=-230, x1=180, y1=230,
                  line=dict(color="#5c6573", dash="dot"))
    fig.add_annotation(x=0, y=0, text="person", showarrow=False,
                       font=dict(color="#8a93a1"))
    for i, a in enumerate(arms):
        x, y, f = float(a["x_mm"]), float(a["y_mm"]), math.radians(float(a["facing_deg"]))
        col = C.ARM_COLOURS[i % 4]
        fig.add_trace(go.Scatter(x=[x], y=[y], mode="markers+text", text=[a.get("id")],
                                 textposition="top center", name=a.get("id"),
                                 marker=dict(size=14, color=col)))
        fig.add_annotation(x=x + 120 * math.cos(f), y=y + 120 * math.sin(f), ax=x, ay=y,
                           xref="x", yref="y", axref="x", ayref="y", showarrow=True,
                           arrowhead=3, arrowwidth=2, arrowcolor=col, text="")
    lay = dict(C.PLOT_LAYOUT)
    lay.update(height=360, showlegend=False,
               xaxis=dict(title="x mm (the way the person faces)", gridcolor="#232833",
                          zeroline=False),
               yaxis=dict(title="y mm", gridcolor="#232833", scaleanchor="x",
                          zeroline=False))
    fig.update_layout(**lay)
    return fig


def _static():
    rig = INV.rig()
    arms = rig.get("arms", []) if isinstance(rig, dict) else []
    th = rig.get("thresholds", {}) if isinstance(rig, dict) else {}
    left = C.card(
        f"The rig in config.json: {rig.get('name', '?')}",
        dcc.Graph(figure=_topdown(arms), config={"displaylogo": False}),
        C.table(["arm", "x mm", "y mm", "z mm", "facing °", "serial"],
                [[html.Span(a.get("id"), style={"color": C.ARM_COLOURS[i % 4]}),
                  a.get("x_mm"), a.get("y_mm"), a.get("z_mm"), a.get("facing_deg"),
                  a.get("serial_number")] for i, a in enumerate(arms)],
                num=(1, 2, 3, 4)),
        html.Details([html.Summary("provenance"),
                      html.Div(rig.get("provenance", ""), className="muted",
                               style={"whiteSpace": "pre-wrap"})]),
        sub="Top-down; arrows show which way each base faces.")
    rows = []
    for mod, names in LIMITS:
        for n in names:
            v = INV.constant(mod, n)
            comment = ""
            for m in INV.catalogue()["modules"]:
                if m["name"] == mod:
                    for c in m["constants"]:
                        if c["name"] == n:
                            comment = c["inline"] or " ".join(c["comment"].split())
            short = comment if len(comment) <= 260 else comment[:260] + "…"
            rows.append([mod, n, C.fmt(v), html.Span(short, className="muted",
                                                     title=comment)])
    right = C.card("Limits the arms work to", C.table(
        ["module", "constant", "value", "why (from the code)"], rows, mono=(0, 1),
        max_height="520px"), C.kv({f"config.json {k}": v for k, v in th.items()}),
        sub="Read from the code, not typed here.")
    return [html.Div(left, className="col"), html.Div(right, className="col-2")]


def _search(rid):
    snap = STORE.snapshot(rid) or {}
    s = snap.get("search") or {}
    rows = {r["id"]: r for r in STORE.stage_table(rid)}
    score = rows.get("placement", {})
    if not s.get("seen") and not score.get("calls"):
        return C.card("Placement search", C.empty(
            "This run is not a placement search. Start one from the Jobs tab "
            "(the dry run writes nothing)."))
    meta = snap.get("meta") or {}
    args = meta.get("args") or []

    def arg(name, default):
        if name in args:
            try:
                return int(args[args.index(name) + 1])
            except (ValueError, IndexError):
                return default
        return default
    samples, shortlist = arg("--samples", 900), arg("--shortlist", 20)
    keep, sweeps = arg("--keep", 3), arg("--sweeps", 2)
    expected = samples + shortlist + keep * (1 + sweeps * 4 * 80 + 1)
    done = (score.get("fns") or {}).get("place_arms.score", {}).get("n") or 0
    eta = None
    if done and snap.get("first_t") and snap.get("last_t") and done < expected:
        rate = done / max(snap["last_t"] - snap["first_t"], 1e-6)
        eta = (expected - done) / max(rate, 1e-9)
    fig = go.Figure()
    pts = s.get("points") or []
    samp = [p for p in pts if p[1] is None and p[5] is not None]
    if samp:
        fig.add_trace(go.Scatter(x=[p[0] - (s.get("started") or p[0]) for p in samp],
                                 y=[p[5] for p in samp], name="best while sampling",
                                 mode="lines+markers", line=dict(color="#8a93a1")))
    cands = sorted({p[1] for p in pts if p[1] is not None})
    for c in cands:
        cp = [p for p in pts if p[1] == c]
        fig.add_trace(go.Scatter(x=[p[0] - (s.get("started") or p[0]) for p in cp],
                                 y=[p[5] for p in cp], name=f"candidate {c}",
                                 mode="lines+markers"))
    fig.update_layout(**C.PLOT_LAYOUT, height=300, xaxis_title="seconds",
                      yaxis_title="coverage %")
    reports = s.get("reports") or {}
    versus = s.get("versus") or {}
    mounts = s.get("mounts") or {}
    return C.card(
        "Placement search",
        C.kv({"phase": s.get("phase"), "sampled": C.fmt(s.get("sampled")),
              "best while sampling": C.one_line(s.get("best")),
              "score() calls": f"{done} of about {expected}",
              "time left, roughly": C.duration(eta) if eta else "—",
              "worth over the shipped layout": s.get("worth"),
              "against the rig in the file": (f"{versus.get('new')} vs {versus.get('old')}"
                                              f" -> {versus.get('decision')}")
              if versus else "—",
              "wrote": s.get("wrote") or "nothing"}),
        dcc.Graph(figure=fig, config={"displaylogo": False}),
        C.table(["layout", "coverage %", "imbalance", "phases", "camera blocked %",
                 "per-arm cm²"],
                [[k, v["coverage"], v["imbalance"], v["phases"], v["camera_blocked"],
                  v["per_arm"]] for k, v in reports.items()], num=(1, 2, 3, 4)),
        C.table(["arm", "x", "y", "z", "facing"],
                [[k, *v] for k, v in sorted(mounts.items(), key=lambda kv: int(kv[0]))],
                num=(1, 2, 3, 4)) if mounts else None,
        sub="Followed through the search's own output lines; the ETA assumes the "
            "remaining calls cost what the finished ones did.")


@dash.callback(Output("rg-head", "children"), Output("rg-search", "children"),
               Output("rg-ver", "data"), Input("rg-tick", "n_intervals"),
               Input("sel-run", "data"), State("rg-ver", "data"))
def render(_n, sel, ver):
    rid = C.resolve_run(sel)
    if not rid:
        return C.run_header(None), None, None
    key = [rid, STORE.version(rid, "search"), STORE.version(rid, "stages"),
           int(time.time() // 10)]
    if key == ver and ctx.triggered_id == "rg-tick":
        raise PreventUpdate
    return C.run_header(rid), _search(rid), key
