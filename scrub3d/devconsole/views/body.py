"""Body & territories: the world frame, the scan, the reconstruction, the split."""
import os

import dash
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from devconsole import viewer3d as V3D
from devconsole.store import RUNS, STORE
from devconsole.views import common as C


def layout(sel):
    return html.Div([
        dcc.Interval(id="bd-tick", interval=2000),
        dcc.Store(id="bd-ver", data=None),
        html.Div(id="bd-head"),
        # Outside bd-body, so the two-second refresh never reloads the viewer.
        C.card("3D, in Rerun's own viewer", html.Div([
            html.Button("Open this run in 3D", id="bd-3d-open",
                        className="dc-btn primary", n_clicks=0),
            html.Button("Close it", id="bd-3d-close", className="dc-btn",
                        n_clicks=0, style={"marginLeft": "8px"}),
            html.Span(id="bd-3d-msg", className="muted",
                      style={"marginLeft": "12px"}),
        ]), html.Div(id="bd-3d-frame"),
            sub="The operator view this run saved to view.rrd, served by "
                "`rerun --serve-web` on 127.0.0.1. Not blurred, like the "
                "Overview's live view; camera images still are."),
        html.Div(id="bd-body", style={"marginTop": "12px"}),
    ])


@dash.callback(Output("bd-3d-frame", "children"), Output("bd-3d-msg", "children"),
               Input("bd-3d-open", "n_clicks"), Input("bd-3d-close", "n_clicks"),
               State("sel-run", "data"), prevent_initial_call=True)
def three_d(_open, _close, sel):
    if not any(t.get("value") for t in ctx.triggered):
        raise PreventUpdate
    view = V3D.viewer()
    if ctx.triggered_id == "bd-3d-close":
        view.close()
        return None, "closed"
    rid = C.resolve_run(sel)
    if not rid:
        return None, "no run selected"
    run_dir = os.path.join(RUNS, rid)
    url, msg = view.open(rid, os.path.join(run_dir, "view.rrd"),
                         os.path.join(RUNS, "viewer3d.log"))
    if url is None:
        return None, msg
    return html.Iframe(src=url, style={"width": "100%", "height": "640px",
                                       "border": "1px solid var(--line)",
                                       "borderRadius": "6px",
                                       "marginTop": "10px"}), msg


def _world(rid):
    spans = STORE.spans(rid, "frames.solve")
    if not spans:
        return C.card("World frame", C.empty("frames.solve was not called."))
    s = spans[-1].get("sum") or {}
    subj = s.get("subject") or {}
    return C.card("World frame, from the floor", C.kv({
        "capture": s.get("capture"), "floor residual": f"{s.get('floor_rms_mm')} mm",
        "camera height": f"{s.get('camera_height_mm')} mm",
        "pitch down": f"{s.get('pitch_down_deg')}°", "roll": f"{s.get('roll_deg')}°",
        "floor points": s.get("floor_points"),
        "subject top (p99)": (f"{C.fmt(subj.get('z_p99_mm'))} mm"
                              if subj.get("z_p99_mm") is not None else None),
        "calls this run": len(spans)}),
        sub="+Z up is the floor normal; +X is the way the person faces.")


def _scan(rid):
    spans = STORE.spans(rid, "scan.scan")
    if not spans:
        return C.card("Scan", C.empty("scan.scan was not called."))
    blocks = []
    for i, ev in enumerate(spans[-2:]):
        s = ev.get("sum") or {}
        parts = s.get("parts") or {}
        rows = []
        for name, p in parts.items():
            rows.append([name, C.badge("ok" if p.get("ok") else "fail",
                                       "ok" if p.get("ok") else p.get("why", "failed")),
                         p.get("length_mm"), p.get("nominal_len_mm"), p.get("stations"),
                         p.get("refused"), p.get("bone_from"), p.get("snapped"),
                         p.get("snap_frac"), p.get("snap_median_mm"),
                         p.get("observed_frac"), p.get("circumference_mm"),
                         C.fmt(p.get("scrubbable"))])
        gates = [[C.badge("ok" if ok else "fail", "pass" if ok else "FAIL"), n, d]
                 for n, ok, d in s.get("gates") or []]
        cl = s.get("clothing") or {}
        obs = s.get("obstacles_by_region") or {}
        blocks.append(html.Div([
            html.Div(f"call {len(spans) - len(spans[-2:]) + i + 1} of {len(spans)}  ·  "
                     f"{C.clock(ev.get('t'))}  ·  took {C.ms(ev.get('ms'))}  ·  "
                     f"scrub_trunk={C.fmt(s.get('scrub_trunk'))}", className="muted"),
            C.table(["part", "", "length", "nominal", "stations", "refused", "bone from",
                     "snapped", "snap frac", "snap median", "observed", "circumference",
                     "scrubbable"], rows, num=(2, 3, 4, 5, 7, 8, 9, 10, 11)),
            html.Div([
                html.Div(C.table(["", "gate", "detail"], gates), className="col-2"),
                html.Div(C.kv({
                    "measuring": cl.get("measuring"),
                    "skin fraction": cl.get("skin_fraction"),
                    "shoulder height": f"{s.get('shoulder_z_mm')} mm",
                    "scrubbable": f"{s.get('scrubbable_cm2')} cm²",
                    "normals": s.get("normals_from"),
                    "obstacle points": s.get("obstacle_points"),
                    "obstacles by region": C.one_line(obs, 200)}), className="col"),
            ], className="row", style={"marginTop": "8px"}),
        ], style={"marginBottom": "14px"}))
    note = None
    if len(spans) > 1:
        note = C.note(f"scan.scan ran {len(spans)} times in this run. main.py scans, "
                      f"then viz.live scans the same capture again, so --scrub-trunk "
                      f"reaches the first and not the second.", "")
    return C.card("Scan: a capture becomes a body", note, *blocks,
                  sub="Cells sit on the measured surface; what is modelled rather than "
                      "measured errs thick.")


def _reconstruction(rid):
    ev = STORE.last_span(rid, "shell.build")
    if not ev:
        return C.card("Reconstruction", C.empty("shell.build was not called."))
    s = ev.get("sum") or {}
    src = s.get("source") or {}
    gates = [[C.badge("ok" if ok else "fail", "ok" if ok else "NO"), n, d]
             for n, ok, d in s.get("gates") or []]
    bind = (STORE.last_span(rid, "shell.bind") or {}).get("sum") or {}
    cmap = (STORE.last_span(rid, "shell.cell_map") or {}).get("sum") or {}
    pose = [r for r in STORE.stage_table(rid) if r["id"] == "skinning"]
    pose_fn = (pose[0]["fns"].get("shell.pose") if pose else None) or {}
    snap = STORE.snapshot(rid) or {}
    still = (snap.get("frames") or {}).get("still")
    img = html.Img(src=f"/frame/{rid}/still?v={still}", style={
        "width": "100%", "borderRadius": "6px"}) if still else None
    return C.card(
        "Reconstruction and skinning",
        html.Div([
            html.Div([
                C.kv({"vertices": s.get("verts"), "faces": s.get("faces"),
                      "decimated to": s.get("max_tris"),
                      "silhouette": src.get("mask"), "normals": src.get("normals"),
                      "depth pixels": src.get("depth_px"),
                      "edge pixels dropped": src.get("edge_px_dropped"),
                      "points used": src.get("points_used"),
                      "AI depth fill": C.one_line(src.get("fill"), 120),
                      "someone else in frame": C.one_line(src.get("crowd"), 120),
                      "bound to regions": bind.get("regions"),
                      "vertices on scrubbable cells": cmap.get("mapped_frac"),
                      "re-pose p50": C.ms(pose_fn.get("p50")),
                      "re-pose calls": pose_fn.get("n")}),
                html.Div(style={"height": "8px"}),
                C.table(["", "gate", "value"], gates),
            ], className="col"),
            # Not `img or ...`: a component with no children is falsy.
            html.Div(img if img is not None else C.empty("No still render in this run."),
                     className="col blurred-optional"),
        ], className="row"),
        sub="Display only: nothing downstream reads this surface.")


def _territories(rid):
    spans = STORE.spans(rid, "partition.solve")
    if not spans:
        return C.card("Territories", C.empty("partition.solve was not called "
                                             "(or only inside a placement score)."))
    fig = go.Figure()
    rows = []
    for i, ev in enumerate(spans[-6:]):
        s = ev.get("sum") or {}
        per = s.get("per_arm_cm2") or []
        label = f"call {i + 1} · k={s.get('envelope_k')}"
        for a, v in enumerate(per):
            fig.add_trace(go.Bar(x=[label], y=[v], name=f"arm {a}",
                                 marker_color=C.ARM_COLOURS[a % 4],
                                 showlegend=(i == 0)))
        cls = s.get("classes") or {}
        rows.append([C.clock(ev.get("t")), s.get("envelope_k"),
                     f"{100 * (s.get('covered_frac') or 0):.1f}%", C.fmt(per),
                     C.fmt(s.get("phases")), f"{s.get('separable_pairs')}/{s.get('pairs')}",
                     C.fmt(s.get("per_limb_exemption")), s.get("obstacle_points"),
                     C.one_line(cls, 80), C.ms(ev.get("ms"))])
    fig.update_layout(**C.PLOT_LAYOUT, barmode="group", height=240,
                      yaxis_title="cm² owned")
    return C.card("Territories: who scrubs what",
                  C.table(["time", "k", "coverage", "per-arm cm²", "phases",
                           "separable pairs", "per-limb exemption", "obstacle pts",
                           "classes", "took"], rows),
                  dcc.Graph(figure=fig, config={"displaylogo": False}),
                  sub="Arms sharing a phase may move at the same time.")


def _bodystore(rid):
    ev = [e for e in (STORE.stage_detail(rid, "bodystore") or {}).get("recent", [])]
    if not ev:
        return None
    rows = []
    for e in ev[-30:][::-1]:
        s = e.get("sum") or {}
        if e["fn"] == "bodystore.identity_ok":
            rows.append([C.clock(e.get("t")), "identity", C.badge(
                "load" if s.get("verdict") == "LOAD" else
                ("cannot-judge" if s.get("verdict") == "CANNOT JUDGE" else "refuse"),
                s.get("verdict")), C.one_line({r[0]: f"{r[3]}" for r in s.get("rows") or []
                                               if isinstance(r, list)}, 140)])
        else:
            rows.append([C.clock(e.get("t")), e["fn"].split(".")[-1], "",
                         C.one_line(s, 140)])
    return C.card("Body store", C.table(["time", "call", "", "detail"], rows),
                  sub="Identity by geometry: every dimension within 8%, and CANNOT "
                      "JUDGE when left and right disagree.")


@dash.callback(Output("bd-head", "children"), Output("bd-body", "children"),
               Output("bd-ver", "data"),
               Input("bd-tick", "n_intervals"),
               Input("sel-run", "data"), Input("blur", "data"), State("bd-ver", "data"))
def render(_n, sel, blur, ver):
    rid = C.resolve_run(sel)
    if not rid:
        return C.run_header(None), None, None
    key = [rid, STORE.version(rid, "stages"), STORE.version(rid, "frames"), blur]
    if key == ver and ctx.triggered_id == "bd-tick":
        raise PreventUpdate
    recon = _reconstruction(rid)
    body = html.Div([
        html.Div([html.Div(_world(rid), className="col"),
                  html.Div(html.Div(recon, className="blurred" if blur else ""),
                           className="col-2")], className="row"),
        html.Div(_scan(rid), style={"marginTop": "12px"}),
        html.Div(_territories(rid), style={"marginTop": "12px"}),
        html.Div(_bodystore(rid), style={"marginTop": "12px"}),
    ])
    return C.run_header(rid), body, key
