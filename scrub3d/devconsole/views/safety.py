"""Safety: every verdict, every refusal, the consent latch, and pre-flight."""
import collections

import dash
from dash import Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from devconsole import inventory as INV
from devconsole.store import STORE
from devconsole.views import common as C

GOV = ("fleet.FleetGovernor.propose", "fleet.FleetGovernor._hold")


def layout(sel):
    return html.Div([
        dcc.Interval(id="sf-tick", interval=1500),
        dcc.Store(id="sf-ver", data=None),
        html.Div(id="sf-head"),
        html.Div([
            html.Span("Show verdicts: ", className="muted"),
            dcc.Checklist(id="sf-actions", inline=True,
                          options=[{"label": f" {a} ", "value": a} for a in
                                   ("clear", "hold", "retreat", "refuse", "estop")],
                          value=["hold", "retreat", "refuse", "estop"],
                          inputStyle={"marginLeft": "10px"}),
        ], style={"margin": "6px 0 10px"}),
        html.Div(id="sf-body"),
    ])


def _preflight(rid):
    snap = STORE.snapshot(rid) or {}
    rows = snap.get("preflight")
    source = "this run"
    if not rows:
        for r in STORE.list_runs(include_nested=False):
            if r["script"] == "main.py":
                s = STORE.snapshot(r["id"]) or {}
                if s.get("preflight"):
                    rows, source = s["preflight"], f"{r['label']} · {C.age(r['started'])}"
                    break
    if not rows:
        return C.card("Pre-flight", C.empty("No pre-flight has run yet."))
    counts = collections.Counter(st for _, st, _ in rows)
    verdict = ("FAILED. Nothing moves." if counts.get("FAIL") else
               f"{counts.get('UNKNOWN', 0)} could not be checked here. Nothing moves "
               f"until they are." if counts.get("UNKNOWN") else "all eight pass")
    return C.card("Pre-flight: all eight or nothing moves", C.table(
        ["", "check", "detail"],
        [[C.badge(st), n, html.Span(d, className="muted")] for n, st, d in rows]),
        html.Div(verdict, className="note " + ("bad" if counts.get("FAIL") else "")),
        sub=f"from {source}. UNKNOWN is never counted as PASS.")


def _governor(rid, actions):
    ver = [e for e in STORE.verdicts(rid) if e.get("fn") in GOV]
    counts = collections.Counter()
    reasons = collections.Counter()
    for e in ver:
        s = e.get("sum") or {}
        counts[s.get("action")] += 1
        reasons[(s.get("action"), s.get("reason") or "(clear)")] += 1
    rows = []
    for e in reversed(ver):
        s = e.get("sum") or {}
        if s.get("action") not in actions:
            continue
        rows.append([C.clock(e.get("t")), e["fn"].split(".")[-1], s.get("arm"),
                     C.badge(s.get("action")), s.get("reason") or "",
                     C.fmt(s.get("xyz")), C.fmt(s.get("target")),
                     html.Span(C.one_line(s.get("detail"), 110), className="muted")])
        if len(rows) >= 300:
            break
    drops, last = 0, {}
    for row in STORE.stage_table(rid):
        if row["id"] == "governor":
            drops = row["dropped"]
            last = row.get("last") or {}
    state = last.get("sum") or {}
    head = html.Div([
        html.Span([C.badge(a), f" {n}  "], style={"marginRight": "10px"})
        for a, n in counts.most_common()], style={"marginBottom": "8px"})
    return C.card(
        "Governor verdicts", head,
        C.table(["action", "reason", "count"],
                [[C.badge(a), r, n] for (a, r), n in reasons.most_common()],
                num=(2,)),
        html.Div(style={"height": "10px"}),
        C.table(["time", "via", "arm", "", "reason", "asked for (arm frame)",
                 "sent to", "detail"],
                rows, max_height="420px") if rows else C.empty(
            "No verdicts of the selected kinds in this run."),
        html.Div(f"estopped: {C.fmt(state.get('estopped'))}   degraded arms: "
                 f"{C.fmt(state.get('degraded'))}   dropped by the rate limit: {drops}",
                 className="faint", style={"marginTop": "6px"}),
        sub="Every command must pass through propose(). HOLD and RETREAT come from "
            "_hold, which the fleet self-test also drives directly. \"Sent to\" is "
            "where the verdict moves the arm: the target itself when clear, the "
            "backed-off point on a retreat, nothing when refused.")


def _armlink(rid):
    ev = [e for e in STORE.verdicts(rid) if e.get("fn", "").startswith("armlink.")]
    if not ev:
        return C.card("Arm link", C.empty("The arm link was not used in this run."))
    rows = []
    for e in reversed(ev[-200:]):
        s = e.get("sum") or {}
        if e["fn"].endswith("set_target"):
            rows.append([C.clock(e.get("t")), "set_target", s.get("arm_id"),
                         C.badge("ok" if s.get("result") else "refused",
                                 "sent" if s.get("result") else "refused"),
                         s.get("verdict_reason") or "", C.fmt(s.get("xyz")),
                         C.one_line(s.get("verdict_detail"), 80)])
        else:
            rows.append([C.clock(e.get("t")), e["fn"].split(".")[-1], "",
                         C.badge("refused" if e.get("refused") else "ok"),
                         "", "", C.one_line(s, 100)])
    return C.card("Arm link", C.table(
        ["time", "call", "arm", "", "reason", "target", "detail"], rows,
        max_height="300px"),
        sub="The only route to py/arm.py. A point outside arm.py's BOX is refused "
            "before BOX can silently move it.")


def _consent(rid):
    ev = [e for e in STORE.verdicts(rid) if e.get("fn", "").startswith("session.")]
    if not ev:
        return C.card("Consent", C.empty("No consent latch in this run."))
    rows = [[C.clock(e.get("t")), e["fn"].split(".")[-1],
             C.badge((e.get("sum") or {}).get("state") or "na",
                     (e.get("sum") or {}).get("state") or "—"),
             C.fmt((e.get("sum") or {}).get("result")),
             C.fmt((e.get("sum") or {}).get("remaining_s"))] for e in ev[-60:]][::-1]
    return C.card("Consent", C.table(["time", "call", "state", "result",
                                      "budget left s"], rows, max_height="260px"),
                  sub="One latch for the fleet; a 30 s arm clock and a 180 s session "
                      "budget.")


def _handoffs(rid):
    ev = [e for e in STORE.verdicts(rid) if e.get("fn", "").startswith("adapt.")]
    guard = [e for e in STORE.verdicts(rid) if e.get("fn") == "serial.Serial.__init__"]
    parts = []
    if ev:
        parts.append(C.table(["time", "call", "detail"],
                             [[C.clock(e.get("t")), e["fn"].split(".")[-1],
                               C.one_line(e.get("sum"), 140)] for e in ev[-60:]][::-1],
                             max_height="200px"))
    else:
        parts.append(C.empty("No handoffs were considered."))
    parts.append(html.Div(style={"height": "8px"}))
    if guard:
        parts.append(C.note(f"The hardware guard refused {len(guard)} attempt(s) to "
                            f"open a serial port.", "bad"))
    else:
        parts.append(C.note("No job in this run tried to open a serial port.", "ok"))
    return C.card("Handoffs and the hardware guard", *parts)


def _gaps():
    items = [g for g in INV.gaps() if g["open"]]
    if not items:
        return None
    return C.card("Safety gaps, computed from the code", *[
        C.note(f"{g['what']}: {g['detail']}", "") for g in items])


@dash.callback(Output("sf-head", "children"), Output("sf-body", "children"),
               Output("sf-ver", "data"),
               Input("sf-tick", "n_intervals"), Input("sel-run", "data"),
               Input("sf-actions", "value"), State("sf-ver", "data"))
def render(_n, sel, actions, ver):
    rid = C.resolve_run(sel)
    key = [rid, STORE.version(rid, "verdicts") if rid else None,
           STORE.version(rid, "preflight") if rid else None, sorted(actions or [])]
    if key == ver and ctx.triggered_id == "sf-tick":
        raise PreventUpdate
    if not rid:
        return C.run_header(None), _preflight(None), key
    body = html.Div([
        html.Div([html.Div(_preflight(rid), className="col"),
                  html.Div(_gaps(), className="col")], className="row"),
        html.Div(_governor(rid, set(actions or [])), style={"marginTop": "12px"}),
        html.Div([html.Div(_armlink(rid), className="col-2"),
                  html.Div([_consent(rid), _handoffs(rid)], className="col stack")],
                 className="row", style={"marginTop": "12px"}),
    ])
    return C.run_header(rid), body, key
