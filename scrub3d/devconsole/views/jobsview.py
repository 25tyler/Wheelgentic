"""Jobs & tests: start what exists, watch it, stop it."""
import json

import dash
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from devconsole import jobs as JB
from devconsole import sysmon as SM
from devconsole.store import STORE
from devconsole.views import common as C

GROUPS = ("Pipeline", "Planning", "Data", "Console", "Self-tests")


def layout(sel):
    reg = JB.jobs().describe()
    # None while the first check is still running: leave the buttons alone and
    # let start() decide.
    camera = SM.sysmon().camera_present()
    groups = []
    for g in GROUPS:
        items = [j for j in reg if j["group"] == g]
        if not items:
            continue
        extra = None
        if g == "Self-tests":
            extra = html.Div([
                html.Button("Run all self-tests", id="jb-all", className="dc-btn primary",
                            n_clicks=0),
                html.Button("Cancel the sweep", id="jb-cancel", className="dc-btn",
                            n_clicks=0, style={"marginLeft": "8px"}),
                html.Div(id="jb-sweep", style={"marginTop": "8px"}),
            ], style={"marginBottom": "10px"})
        groups.append(C.card(g, extra, html.Div([_job(j, camera) for j in items],
                                                className="jobs-grid")))
    return html.Div([
        dcc.Interval(id="jb-tick", interval=1500),
        dcc.Store(id="jb-sig", data=None),
        dcc.Store(id="jb-pending", data=None),
        dcc.ConfirmDialog(id="jb-confirm"),
        dcc.ConfirmDialog(id="jb-del-confirm",
                          message="Delete every finished run directory? Running jobs "
                                  "are left alone."),
        html.Div(id="jb-msg"),
        C.card("Runs", html.Div([
            html.Button("Delete finished runs", id="jb-del", className="dc-btn danger",
                        n_clicks=0),
            html.Span("  The newest 20 runs are kept, plus the newest of each "
                      "self-test and check. Runs can hold camera frames and never "
                      "leave this machine.", className="muted"),
        ], style={"marginBottom": "8px"}), html.Div(id="jb-runs")),
        html.Div(groups, className="stack", style={"marginTop": "12px"}),
    ])


def _job(j, camera=None):
    no_camera = "camera" in j["needs"] and camera is False
    tags = [html.Span(n, className="tag") for n in j["needs"]]
    if no_camera:
        tags.append(html.Span("no camera connected", className="tag w"))
    if j["writes"]:
        tags.append(html.Span(f"writes {j['writes']}", className="tag w"))
    params = []
    for k, v in (j["params"] or {}).items():
        if isinstance(v, bool):
            ctrl = dcc.Checklist(id={"type": "jb-param", "job": j["id"], "key": k},
                                 options=[{"label": " yes", "value": "on"}],
                                 value=["on"] if v else [])
        else:
            ctrl = dcc.Input(id={"type": "jb-param", "job": j["id"], "key": k},
                             value=v, type="number" if isinstance(v, (int, float)) else "text",
                             className="dc-in", debounce=True)
        params.append(html.Div([html.Label(k), ctrl]))
    return html.Div([
        html.Div([html.B(j["label"]),
                  html.Button("Run", id={"type": "jb-run", "job": j["id"]},
                              className="dc-btn primary", n_clicks=0,
                              disabled=no_camera,
                              title="needs a D455" if no_camera else None)],
                 className="hd"),
        html.Div(j["doc"], className="doc") if j["doc"] else None,
        html.Div([html.Code(j["script"]), " ", *tags]),
        html.Div(params, className="params") if params else None,
    ], className="job")


def _short(text, n=70):
    text = text or ""
    return text if len(text) <= n else text[:n] + "…"


def _params(jid, ids, values):
    out = {}
    for i, v in zip(ids, values):
        if i.get("job") != jid:
            continue
        if isinstance(v, list):
            v = "on" in v
        out[i["key"]] = v
    return out


@dash.callback(Output("jb-msg", "children", allow_duplicate=True),
               Output("jb-confirm", "displayed"), Output("jb-confirm", "message"),
               Output("jb-pending", "data"), Output("sel-run", "data", allow_duplicate=True),
               Input({"type": "jb-run", "job": ALL}, "n_clicks"),
               State({"type": "jb-param", "job": ALL, "key": ALL}, "id"),
               State({"type": "jb-param", "job": ALL, "key": ALL}, "value"),
               prevent_initial_call=True)
def run(clicks, ids, values):
    trig = ctx.triggered_id
    if not trig or not any(c for c in (clicks or []) if c):
        raise PreventUpdate
    if not any(t.get("value") for t in ctx.triggered):
        raise PreventUpdate
    jid = trig["job"]
    job = JB.jobs().registry().get(jid)
    params = _params(jid, ids, values)
    if job is not None and job.confirm:
        return (no_update, True, f"{job.label}: {job.confirm} Continue?",
                {"job": jid, "params": params}, no_update)
    rid, msg = JB.jobs().start(jid, params,
                               camera_present=SM.sysmon().camera_present())
    box = html.Div(msg, className="msg")
    return box, False, no_update, None, (rid if rid else no_update)


@dash.callback(Output("jb-msg", "children", allow_duplicate=True),
               Output("sel-run", "data", allow_duplicate=True),
               Input("jb-confirm", "submit_n_clicks"), State("jb-pending", "data"),
               prevent_initial_call=True)
def confirmed(_n, pending):
    if not pending:
        raise PreventUpdate
    rid, msg = JB.jobs().start(pending["job"], pending.get("params"), confirmed=True,
                               camera_present=SM.sysmon().camera_present())
    return html.Div(msg, className="msg"), (rid if rid else no_update)


@dash.callback(Output("jb-msg", "children", allow_duplicate=True),
               Input("jb-all", "n_clicks"), prevent_initial_call=True)
def run_all(n):
    if not n:
        raise PreventUpdate
    return html.Div(JB.jobs().run_all_selftests(), className="msg")


@dash.callback(Output("jb-msg", "children", allow_duplicate=True),
               Input("jb-cancel", "n_clicks"), prevent_initial_call=True)
def cancel(n):
    if not n:
        raise PreventUpdate
    return html.Div(JB.jobs().cancel_batch(), className="msg")


@dash.callback(Output("jb-del-confirm", "displayed"), Input("jb-del", "n_clicks"),
               prevent_initial_call=True)
def ask_delete(n):
    return bool(n)


@dash.callback(Output("jb-msg", "children", allow_duplicate=True),
               Input("jb-del-confirm", "submit_n_clicks"), prevent_initial_call=True)
def delete(n):
    if not n:
        raise PreventUpdate
    k = JB.jobs().delete_finished()
    return html.Div(f"deleted {k} finished run(s)", className="msg")


@dash.callback(Output("jb-msg", "children", allow_duplicate=True),
               Output("sel-run", "data", allow_duplicate=True),
               Input({"type": "jb-stop", "run": ALL}, "n_clicks"),
               Input({"type": "jb-view", "run": ALL}, "n_clicks"),
               prevent_initial_call=True)
def row_action(stops, views):
    trig = ctx.triggered_id
    if not trig or not any(t.get("value") for t in ctx.triggered):
        raise PreventUpdate
    if trig["type"] == "jb-stop":
        return html.Div(JB.jobs().stop(trig["run"]), className="msg"), no_update
    return html.Div(f"selected {trig['run']} — the other tabs now show it",
                    className="msg"), trig["run"]


@dash.callback(Output("jb-runs", "children"), Output("jb-sweep", "children"),
               Output("jb-sig", "data"), Input("jb-tick", "n_intervals"),
               Input("sel-run", "data"), State("jb-sig", "data"))
def refresh(_n, sel, sig):
    runs = STORE.list_runs()
    sweep = JB.jobs().last_sweep()
    rid = C.resolve_run(sel)
    new_sig = json.dumps([[r["id"], r["status"], r["code"], r.get("last")]
                          for r in runs[:60]]
                         + [rid, (sweep or {}).get("running"),
                            len((sweep or {}).get("results") or {})], default=str)
    if new_sig == sig and ctx.triggered_id == "jb-tick":
        raise PreventUpdate
    rows = []
    for r in runs[:60]:
        live = r["status"] in ("running", "starting")
        rows.append([
            C.badge(r["status"]),
            html.Span(("↳ " if r["nested"] else "") + r["label"],
                      style={"fontWeight": 600 if r["id"] == rid else 400}),
            html.Code(r["script"]), C.clock(r["started"]),
            C.duration(r["seconds"]) if r["seconds"] is not None else
            ("running" if live else "—"),
            r["code"], r["pid"], html.Span(r.get("why") or "", className="muted"),
            html.Span(_short(r.get("last")), className="faint mono",
                      title=r.get("last") or ""),
            html.Span([
                html.Button("view", id={"type": "jb-view", "run": r["id"]},
                            className="dc-btn", n_clicks=0),
                html.Button("stop", id={"type": "jb-stop", "run": r["id"]},
                            className="dc-btn danger", n_clicks=0,
                            style={"marginLeft": "6px"}) if live else None,
            ]),
        ])
    table = C.table(["", "run", "script", "started", "took", "exit", "pid", "why",
                     "last line printed", ""],
                    rows, num=(5, 6), max_height="420px") if rows else C.empty(
        "No runs yet.")
    sw = None
    if sweep:
        res = sweep.get("results") or {}
        sw = html.Div([
            html.Div(f"{'running' if sweep.get('running') else 'finished'} · "
                     f"{len(res)} / {sweep.get('total')} done · "
                     f"{sum(1 for v in res.values() if v.get('status') == 'ok')} passed",
                     className="muted"),
            C.table(["module", "", "took", "last line"],
                    [[m, C.badge(v.get("status")), C.duration(v.get("seconds")),
                      html.Span(v.get("last") or v.get("why") or "", className="muted")]
                     for m, v in res.items()], max_height="300px") if res else None,
        ])
    return table, sw, new_sig
