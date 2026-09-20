"""Overview: the whole system at a glance.

The rig in live 3D, every data process as one diagram, the numbers that say
whether a run is healthy, and what is running. Everything here is also on a
tab of its own; this page is for seeing it all at once.
"""
import math
import re
import time
from urllib.parse import urlencode

import dash
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from devconsole import flow as FL
from devconsole import inventory as INV
from devconsole import jobs as JB
from devconsole import live3d as L3
from devconsole import probes as PR
from devconsole.store import STORE
from devconsole.views import common as C

LOOP_SCRIPTS = ("main.py", "viz.py")
# Libraries talking about themselves: MediaPipe, TensorFlow Lite, protobuf.
NOISE = re.compile(r"^\s*(W0000|I0000|E0000|INFO:|WARNING:)|UserWarning|"
                   r"DeprecationWarning|warnings\.warn|SymbolDatabase|message_factory|"
                   r"inference_feedback_manager|Feedback manager requires")
STATUS_COLOUR = {"done": "#4cc38a", "running": "#6ea8fe", "refused": "#f2b24c",
                 "failed": "#f06a6a", "probe missing": "#d58cf2"}
IDLE_COLOUR = "#5c6573"
EDGE_STYLE = {                     # kind -> (colour, dash, width)
    "data": ("#6b7482", "solid", 1.3),
    "live": ("#9fb4cf", "solid", 1.8),
    "selftest": ("#5c6573", "dot", 1.2),
    "gap": ("#f06a6a", "dash", 1.6),
}


def layout(sel):
    return html.Div([
        dcc.Interval(id="hm-tick", interval=1000),
        dcc.Store(id="hm-frame-gen", data=None),
        dcc.Store(id="hm-flow-key", data=None),
        dcc.Store(id="hm-num-key", data=None),
        html.Div(id="hm-head"),
        html.Div(id="hm-vitals", className="hm-vitals"),
        html.Div([
            html.Div(C.card(
                "The rig, live",
                html.Div([
                    html.Button("Simulate the rig", id="hm-sim", n_clicks=0,
                                className="dc-btn primary",
                                title="No camera: the scanned person sits still and "
                                      "the four arms scrub at their real speed."),
                    html.Button("Replay bag01", id="hm-replay", n_clicks=0,
                                className="dc-btn",
                                title="The whole flow off the recorded bag, followed "
                                      "frame by frame."),
                    html.Button("Stop", id="hm-stop", n_clicks=0, className="dc-btn",
                                title="Stop the run shown here, the same way the Jobs "
                                      "tab does."),
                    html.Span(id="hm-3d-msg", className="muted"),
                ], className="hm-3d-bar"),
                html.Div(id="hm-3d-status", className="hm-3d-status"),
                html.Div(id="hm-3d-frame", className="hm-3d-frame"),
                sub="Rerun's own viewer, fed while the run writes its recording. "
                    "The person is shown unblurred here; camera images stay "
                    "blurred on the Vision tab."), className="col-2"),
            html.Div([
                C.card("Running now", html.Div(id="hm-running")),
                C.card("Latest lines from this run", html.Div(id="hm-logs"),
                       sub="Newest first. Library warnings are on the Logs tab."),
            ], className="col stack"),
        ], className="row", style={"marginTop": "12px"}),
        html.Div(C.card(
            "Every data process",
            dcc.Graph(id="hm-flow", config={"displayModeBar": False,
                                            "scrollZoom": False}),
            html.Div(_legend(), className="hm-legend"),
            sub="Each box is a stage the code runs, coloured by what it did in "
                "this run. Each arrow is what one stage hands the next. Click a "
                "stage to open it on the Pipeline tab."),
            style={"marginTop": "12px"}),
    ])


def _legend():
    swatch = [("done", STATUS_COLOUR["done"]), ("running", STATUS_COLOUR["running"]),
              ("refused (worked, said no)", STATUS_COLOUR["refused"]),
              ("failed", STATUS_COLOUR["failed"]), ("not reached", IDLE_COLOUR)]
    lines = [("data it passes", "solid", EDGE_STYLE["data"][0]),
             ("passed in this run", "solid", EDGE_STYLE["live"][0]),
             ("only a self-test uses it", "dotted", EDGE_STYLE["selftest"][0]),
             ("missing in the code (a known gap)", "dashed", EDGE_STYLE["gap"][0])]
    return ([html.Span([html.Span(className="sq", style={"background": c}), t])
             for t, c in swatch]
            + [html.Span([html.Span(className="ln", style={
                "borderTop": f"2px {d} {c}"}), t]) for t, d, c in lines]
            + [html.Span([html.Span("◆", style={"color": "#8a93a1"}),
                          " a tool, drawn beside what it feeds"])])


# --- which run --------------------------------------------------------------

def described(sel):
    """The run this page describes. -> (run id or None, why that one).

    A run picked in the header wins. Otherwise whatever is running, and
    otherwise the newest run of the live loop or the simulation, because a
    self-test is rarely what someone opening this page wants to see.
    """
    if sel and sel != "latest":
        rid = C.resolve_run(sel)
        return rid, "picked in the header"
    runs = STORE.list_runs()
    live = [r for r in runs if r["status"] in ("running", "starting")]
    if live:
        return live[0]["id"], "running now"
    loops = [r for r in runs if r["script"] in LOOP_SCRIPTS and not r["nested"]]
    if loops:
        return loops[0]["id"], "the newest run of the live loop or the simulation"
    rid = STORE.latest()
    return rid, "the newest run"


# --- the numbers ------------------------------------------------------------

def _vital(label, value, cls="", title=None):
    return html.Span([html.Span(label + " ", className="muted"),
                      html.B(value, className=cls)], className="dc-chip",
                     title=title)


def _vitals(rid):
    snap = STORE.snapshot(rid) or {}
    s = snap.get("summary") or {}
    names = (["feed/fps", "track/ok", "track/jump_mm", "track/freeze", "track/away"]
             + [f"coverage/arm_{a}" for a in range(4)]
             + [f"reach/arm_{a}" for a in range(4)])
    st = STORE.series_stats(rid, names)
    out = [html.Span([C.badge(s.get("status")), html.B(f"  {s.get('label', '')}")],
                     className="dc-chip")]
    if "feed/fps" in st:
        out.append(_vital("frames/s", f"{st['feed/fps']['last']:.1f}"))
    if "track/ok" in st:
        pct = 100.0 * st["track/ok"]["mean"]
        out.append(_vital("tracked", f"{pct:.0f}%", "" if pct > 90 else "warn-text"))
    if "track/jump_mm" in st:
        j = st["track/jump_mm"]["last"]
        out.append(_vital("body moved", f"{j:.0f} mm/frame",
                          "warn-text" if j > 25 else "",
                          "The furthest any body cell moved since the last frame; "
                          "the arms freeze above 25 mm."))
    if "track/freeze" in st:
        n = round(st["track/freeze"]["mean"] * st["track/freeze"]["n"])
        out.append(_vital("frozen frames", str(n)))
    if "track/away" in st:
        away = st["track/away"]["last"] > 0.5
        out.append(_vital("seated", "no, arms holding" if away else "yes",
                          "warn-text" if away else "ok-text",
                          "Whether the person is where the scan saw them sit."))
    cov = [(a, st[f"coverage/arm_{a}"]["last"]) for a in range(4)
           if f"coverage/arm_{a}" in st]
    if cov:
        out.append(html.Span([html.Span("scrubbed ", className="muted")] + [
            html.B(f"{v:.0f}% ", style={"color": C.ARM_COLOURS[a]}) for a, v in cov],
            className="dc-chip", title="Per arm, of what that arm can reach."))
    reach = [st[f"reach/arm_{a}"]["last"] for a in range(4) if f"reach/arm_{a}" in st]
    if reach:
        low = min(reach)
        out.append(_vital("lowest reachable", f"{low:.0f}%",
                          "warn-text" if low < 55 else "",
                          "Of the work an arm has left, how much it can still reach. "
                          "Below 55% a handoff is considered."))
    pre = snap.get("preflight") or []
    if pre:
        counts = {}
        for row in pre:
            status = str(row[1] if isinstance(row, (list, tuple)) else row.get("status"))
            counts[status] = counts.get(status, 0) + 1
        out.append(_vital("pre-flight", "  ".join(f"{k} {v}" for k, v in sorted(counts.items())),
                          "bad-text" if counts.get("FAIL") else ""))
    verdicts = STORE.verdicts(rid, limit=5000)
    acts = {}
    for e in verdicts:
        a = (e.get("sum") or {}).get("action")
        if a:
            acts[a] = acts.get(a, 0) + 1
    if acts:
        out.append(_vital("governor", "  ".join(f"{k} {v}" for k, v in sorted(acts.items())),
                          title="Verdicts the governor gave in this run."))
    if s.get("status") not in ("running", "starting") and s.get("seconds") is not None:
        out.append(_vital("took", C.duration(s["seconds"])))
    return out


# --- running now, and the log ------------------------------------------------

def _running(live):
    runs = [r for r in STORE.list_runs() if r["status"] in ("running", "starting")]
    rows = []
    for r in runs:
        rows.append(html.Div([
            C.badge(r["status"]),
            html.B(f" {r['label']}"),
            html.Span(f"  {r['script']}", className="mono muted"),
            html.Span(f"  {C.duration(time.time() - (r['started'] or time.time()))}",
                      className="muted"),
            html.A("  stdout", href=f"/stdout/{r['id']}", target="_blank"),
        ], className="hm-run"))
    if not rows:
        rows.append(C.empty("Nothing is running."))
    feed = [f for f in live.get("followers", []) if f["state"] in ("waiting", "streaming")]
    if feed:
        rows.append(html.Div([html.Span("live 3D  ", className="muted")] + [
            html.Span(f"{f['label']}: {f['state']}, {f['sent_mb']} MB  ",
                      className="mono") for f in feed], className="hm-run"))
    return rows


def _logs(rid):
    """The run's own last lines, newest first, without library chatter."""
    lines = [entry for entry in STORE.tail_logs(rid, 120)
             if entry[4].strip() and not NOISE.search(entry[4])][-14:]
    if not lines:
        return C.empty("No output yet.")
    return html.Div([html.Div(text, className=f"log-line lv-{level}")
                     for _seq, _t, level, _src, text in reversed(lines)],
                    className="hm-logs")


# --- the live 3D --------------------------------------------------------------

def _stage_of(live):
    """What the 3D view is showing, in a sentence."""
    state = live.get("state")
    if state != "up":
        return C.note(f"The live 3D server is {state}: {live.get('why')}.",
                      "bad" if state == "failed" else "info")
    shown = live.get("showing")
    waiting = [f for f in live.get("followers", []) if f["state"] == "waiting"]
    bits = []
    if shown is None:
        bits.append(html.Span("Nothing to show yet. Simulate the rig, or replay the "
                              "bag, and it streams in here.", className="muted"))
    elif shown["state"] == "streaming":
        bits += [html.Span("● live  ", className="live-dot"),
                 html.B(shown["label"]),
                 html.Span(f"  {shown['sent_mb']} MB so far", className="muted")]
    elif shown["state"] == "done":
        bits += [html.Span("showing ", className="muted"), html.B(shown["label"]),
                 html.Span(", finished" + (" (the last run that ended cleanly)"
                                           if shown.get("seed") else ""),
                           className="muted")]
    else:
        bits += [C.badge("failed" if shown["state"] == "failed" else "refused",
                         shown["state"]),
                 html.B(f"  {shown['label']}"),
                 html.Span(f"  {shown['why']}", className="muted")]
    for f in waiting:
        bits.append(html.Span(f"   {f['label']} is starting; it appears here once it "
                              f"opens its recording.", className="muted"))
    return html.Div(bits)


@dash.callback(Output("hm-3d-frame", "children"), Output("hm-frame-gen", "data"),
               Input("hm-tick", "n_intervals"), State("hm-frame-gen", "data"))
def frame(_n, shown):
    """The viewer, reloaded when the server behind it is a new one.

    A new server holds only the new run, so the old page would keep showing
    what it had. The frame is emptied for one tick and put back, which makes
    the browser load it afresh.
    """
    live = L3.live().status()
    gen = live.get("generation") if live.get("state") == "up" else None
    if gen is None:
        if shown == "off":
            raise PreventUpdate
        return C.empty("The live 3D view is not available."), "off"
    if shown == gen:
        raise PreventUpdate
    if shown not in (None, "off", "blank"):
        return html.Div("Loading the new run…", className="muted hm-3d-blank"), "blank"
    return html.Iframe(src=live["url"], className="hm-3d-iframe"), gen


@dash.callback(Output("hm-3d-status", "children"), Output("hm-running", "children"),
               Output("hm-sim", "disabled"), Output("hm-replay", "disabled"),
               Output("hm-stop", "disabled"),
               Input("hm-tick", "n_intervals"))
def live_status(_n):
    live = L3.live().status()
    running = {r["job"] for r in STORE.list_runs()
               if r["status"] in ("running", "starting")}
    stoppable = bool(running & {"simulate", "replay"})
    return (_stage_of(live), _running(live), "simulate" in running,
            "replay" in running, not stoppable)


@dash.callback(Output("hm-3d-msg", "children"),
               Input("hm-sim", "n_clicks"), Input("hm-replay", "n_clicks"),
               Input("hm-stop", "n_clicks"), prevent_initial_call=True)
def act(sim, replay, stop):
    # A button that has just been put on the page fires with 0 clicks.
    if not any(t.get("value") for t in ctx.triggered):
        raise PreventUpdate
    jobs = JB.jobs()
    if ctx.triggered_id == "hm-stop":
        live = [r for r in STORE.list_runs()
                if r["status"] in ("running", "starting")
                and r["job"] in ("simulate", "replay")]
        if not live:
            return "nothing to stop"
        return f"{live[0]['label']}: {jobs.stop(live[0]['id'])}"
    jid = "simulate" if ctx.triggered_id == "hm-sim" else "replay"
    _rid, msg = jobs.start(jid)
    return msg


# --- the header and the numbers -------------------------------------------------

@dash.callback(Output("hm-head", "children"), Output("hm-vitals", "children"),
               Output("hm-logs", "children"), Output("hm-num-key", "data"),
               Input("hm-tick", "n_intervals"), Input("sel-run", "data"),
               State("hm-num-key", "data"))
def numbers(_n, sel, key):
    rid, why = described(sel)
    if not rid:
        return C.run_header(None), None, C.empty("No runs yet."), None
    # Recomputed when the run has news, and every half minute for the ages.
    new = [rid, why] + [STORE.version(rid, k) for k in
                        ("series", "stages", "logs", "verdicts", "preflight")]         + [int(time.time() // 30)]
    if new == key:
        raise PreventUpdate
    head = html.Div([C.run_header(rid),
                     html.Div(f"This page describes {rid}: {why}. Pick another run "
                              f"in the header to see that one.", className="faint")])
    return head, _vitals(rid), _logs(rid), new


# --- the diagram ------------------------------------------------------------

_POS = {}


def _positions():
    """Stage -> (x, y). Columns are the groups.

    The Tools stages are drawn in the column of what they feed, so their
    arrows stay short. Within a column, a stage comes below whatever in the
    same column feeds it, and among stages at the same depth each sits level
    with the stages it is joined to in other columns, as far as the others
    allow (a few passes of the barycentre rule). That is what keeps the
    arrows short and mostly uncrossed.
    """
    if _POS:
        return _POS
    groups = [g for g in PR.GROUPS if g != "Tools"]
    edges = FL.edges()
    group_of = {s.id: s.group for s in PR.STAGES}
    for s in PR.STAGES:
        if s.group == "Tools":
            dst = next((e["dst"] for e in edges if e["src"] == s.id), None)
            group_of[s.id] = group_of.get(dst, groups[0])
    order = {s.id: i for i, s in enumerate(PR.STAGES)}
    cols = {g: [s.id for s in PR.STAGES if group_of[s.id] == g] for g in groups}
    nbrs = {sid: [] for sid in group_of}
    for e in edges:
        nbrs[e["src"]].append(e["dst"])
        nbrs[e["dst"]].append(e["src"])
    depth = {sid: 0 for sid in group_of}
    for _ in range(len(depth)):
        moved = False
        for e in edges:
            a, b = e["src"], e["dst"]
            if group_of[a] == group_of[b] and depth[b] < depth[a] + 1:
                depth[b] = depth[a] + 1
                moved = True
        if not moved:
            break
    tall = max(len(v) for v in cols.values())
    row = {}

    def place():
        for g in groups:
            top = (tall - len(cols[g])) / 2.0
            for i, sid in enumerate(cols[g]):
                row[sid] = top + i

    place()
    for sweep in range(8):
        for g in (groups if sweep % 2 == 0 else groups[::-1]):
            def centre(sid, g=g):
                ys = [row[n] for n in nbrs[sid] if group_of[n] != g]
                return sum(ys) / len(ys) if ys else row[sid]
            cols[g].sort(key=lambda sid: (depth[sid], centre(sid), order[sid]))
            place()
    for x, g in enumerate(groups):
        for sid in cols[g]:
            _POS[sid] = (float(x), -row[sid])
    return _POS


def _esc(text):
    """Plotly reads hover text as a little HTML."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _curve(p0, p1, n=14):
    """Points along an arrow. A straight line between columns; a bow to the
    left within one, so it does not run through the stages in between."""
    (x0, y0), (x1, y1) = p0, p1
    if abs(x1 - x0) > 1e-6:
        return [x0, x1], [y0, y1]
    bow = 0.22 + 0.06 * abs(y1 - y0)
    cx, cy = x0 - bow, (y0 + y1) / 2.0
    xs, ys = [], []
    for i in range(n + 1):
        t = i / n
        xs.append((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x1)
        ys.append((1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y1)
    return xs, ys


def _figure(rid):
    pos = _positions()
    rows = {r["id"]: r for r in STORE.stage_table(rid)} if rid else {}
    labels = {s.id: s.label for s in PR.STAGES}
    groups = {s.id: s.group for s in PR.STAGES}
    gaps = {g["id"]: g for g in INV.gaps()}
    active = {sid for sid, r in rows.items()
              if r["status"] in ("done", "running", "refused")}

    fig = go.Figure()
    segs = {k: ([], []) for k in EDGE_STYLE}
    mids = ([], [], [])
    arrows = []
    for e in FL.edges():
        kind = e["kind"]
        gap = gaps.get(e["gap"]) if e["gap"] else None
        if kind == "gap" and gap is not None and gap.get("open") is False:
            kind = "data"                 # the code makes this link now
        if kind == "data" and e["src"] in active and e["dst"] in active:
            kind = "live"
        xs, ys = _curve(pos[e["src"]], pos[e["dst"]])
        segs[kind][0].extend(xs + [None])
        segs[kind][1].extend(ys + [None])
        k = len(xs) // 2
        mid = ((xs[k - 1] + xs[k]) / 2, (ys[k - 1] + ys[k]) / 2) if len(xs) > 2 else \
            ((xs[0] + xs[1]) / 2, (ys[0] + ys[1]) / 2)
        text = (f"<b>{_esc(labels[e['src']])} → {_esc(labels[e['dst']])}</b>"
                f"<br>{_esc(e['what'])}")
        if e["kind"] == "gap" and gap is not None:
            state = "still open" if gap.get("open") else "closed"
            text += (f"<br><i>gap, {state}:</i> {_esc(gap['what'])}"
                     f"<br>{_esc(gap['detail'])}")
        elif e["kind"] == "selftest":
            text += "<br><i>only a module's self-test exercises this</i>"
        mids[0].append(mid[0])
        mids[1].append(mid[1])
        mids[2].append(text)
        colour = EDGE_STYLE[kind][0]
        # A short head at the end of the arrow, stopping short of the box.
        ax, ay = xs[-2], ys[-2]
        length = math.hypot(xs[-1] - ax, ys[-1] - ay) or 1.0
        f = min(1.0, 0.08 / length)
        arrows.append(dict(x=xs[-1], y=ys[-1], ax=xs[-1] - (xs[-1] - ax) * f,
                           ay=ys[-1] - (ys[-1] - ay) * f, xref="x", yref="y",
                           axref="x", ayref="y", showarrow=True, text="",
                           arrowhead=2, arrowsize=1.1, arrowwidth=1.4,
                           arrowcolor=colour, standoff=9))
    for kind, (xs, ys) in segs.items():
        if not xs:
            continue
        colour, dash_, width = EDGE_STYLE[kind]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", hoverinfo="skip",
                                 line=dict(color=colour, dash=dash_, width=width),
                                 showlegend=False))
    fig.add_trace(go.Scatter(x=mids[0], y=mids[1], mode="markers",
                             marker=dict(size=14, color="rgba(0,0,0,0)"),
                             hovertext=mids[2], hoverinfo="text", showlegend=False))

    xs, ys, colours, symbols, texts, hovers, ids = [], [], [], [], [], [], []
    for s in PR.STAGES:
        x, y = pos[s.id]
        r = rows.get(s.id) or {}
        status = r.get("status") or "idle"
        xs.append(x)
        ys.append(y)
        colours.append(STATUS_COLOUR.get(status, IDLE_COLOUR))
        symbols.append("diamond" if groups[s.id] == "Tools" else "square")
        texts.append(s.label)
        calls = r.get("calls") or 0
        last = (r.get("last") or {})
        headline = last.get("err") or C.one_line(last.get("sum"), 150)
        hovers.append(
            f"<b>{_esc(s.label)}</b> · {s.group}<br>{_esc(s.what)}<br>"
            f"<b>{status}</b>"
            + (f" · {calls} call{'s' if calls != 1 else ''}" if calls else "")
            + (f" · p50 {C.ms(r.get('p50'))}" if r.get("p50") is not None else "")
            + (f" · {r['refusals']} refused" if r.get("refusals") else "")
            + (f" · {r['errors']} errors" if r.get("errors") else "")
            + (f"<br>{_esc(headline)}" if headline else ""))
        ids.append(s.id)
    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="markers+text", text=texts, textposition="bottom center",
        textfont=dict(size=11, color="#d7dce3"),
        marker=dict(size=17, color=colours, symbol=symbols,
                    line=dict(color="#0f1115", width=1.5)),
        hovertext=hovers, hoverinfo="text", customdata=ids, showlegend=False))

    groups_drawn = [g for g in PR.GROUPS if g != "Tools"]
    top = max(y for _x, y in pos.values())
    heads = [dict(x=i, y=top + 0.75, text=f"<b>{g}</b>", showarrow=False,
                  font=dict(size=12, color="#8a93a1"), xref="x", yref="y")
             for i, g in enumerate(groups_drawn)]
    low = min(y for _x, y in pos.values())
    height = int(110 + 54 * (top - low + 1))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#d7dce3", family="Segoe UI, system-ui, sans-serif"),
        margin=dict(l=10, r=10, t=10, b=10), height=height, dragmode=False,
        hovermode="closest", uirevision="flow",
        hoverlabel=dict(bgcolor="#1c212a", bordercolor="#2a303b",
                        font=dict(color="#d7dce3", size=12), align="left"),
        xaxis=dict(visible=False, fixedrange=True,
                   range=[-0.6, len(groups_drawn) - 0.4]),
        yaxis=dict(visible=False, fixedrange=True, range=[low - 0.7, top + 1.1]),
        annotations=arrows + heads)
    return fig


@dash.callback(Output("hm-flow", "figure"), Output("hm-flow-key", "data"),
               Input("hm-tick", "n_intervals"), Input("sel-run", "data"),
               State("hm-flow-key", "data"))
def diagram(_n, sel, key):
    rid, _why = described(sel)
    new = [rid, STORE.version(rid, "stages") if rid else None,
           [g.get("open") for g in INV.gaps()]]
    if new == key:
        raise PreventUpdate
    return _figure(rid), new


@dash.callback(Output("url", "search"), Output("hm-flow", "clickData"),
               Input("hm-flow", "clickData"), State("sel-run", "data"),
               prevent_initial_call=True)
def open_stage(click, sel):
    point = ((click or {}).get("points") or [{}])[0]
    sid = point.get("customdata")
    if not isinstance(sid, str) or sid not in PR.stage_index():
        raise PreventUpdate
    rid, _why = described(sel)
    query = {"tab": "pipeline", "stage": sid}
    # Only pinned when "latest" would show the Pipeline a different run: a
    # pinned run stays pinned, and this page would stop following new ones.
    if rid and (sel not in (None, "latest") or rid != STORE.latest()):
        query["run"] = rid
    return "?" + urlencode(query), None
