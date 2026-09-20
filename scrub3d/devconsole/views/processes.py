"""Processes: what is actually running on this machine, and the GPU."""
import dash
from dash import Input, Output, dcc, html
from dash.exceptions import PreventUpdate

from devconsole import sysmon as SM
from devconsole import live3d as L3
from devconsole import viewer3d as V3D
from devconsole.store import STORE
from devconsole.views import common as C


def layout(sel):
    return html.Div([
        dcc.Interval(id="pr-tick", interval=3000),
        html.Div([html.Button("Re-check torch and CUDA", id="pr-torch",
                              className="dc-btn", n_clicks=0),
                  html.Span("  runs in a subprocess, so the console never loads CUDA "
                            "itself", className="muted")], style={"margin": "4px 0 10px"}),
        html.Div(id="pr-body"),
    ])


@dash.callback(Output("pr-torch", "children"), Input("pr-torch", "n_clicks"),
               prevent_initial_call=True)
def torch_again(n):
    if not n:
        raise PreventUpdate
    SM.sysmon().request_torch()
    return "Re-checking…"


def _camera(cam):
    if not cam:
        return C.empty("checking…")
    if not cam.get("ok"):
        return C.note(f"the check failed: {cam.get('error', '?')}", "bad")
    devs = cam.get("devices") or []
    rows = [[d.get("name"), d.get("serial"), d.get("firmware")] for d in devs]
    return html.Div([
        C.table(["device", "serial", "firmware"], rows) if rows
        else html.Div("No RealSense device is connected.", className="big"),
        html.Div(f"checked {C.clock(cam.get('t'))}"
                 + (f" · {cam['skipped']}" if cam.get("skipped") else "")
                 + " · looked for in a subprocess, never while a camera job runs",
                 className="faint"),
    ])


def _torch(t):
    if not t:
        return C.empty("checking…")
    if not t.get("ok"):
        return C.note(f"the check failed: {t.get('error', '?')}", "bad")
    return html.Div([
        C.kv({"torch": t.get("torch"), "CUDA available": t.get("cuda"),
              "device": t.get("device"), "CUDA version": t.get("cuda_version")}),
        html.Div(f"checked {C.clock(t.get('checked'))}", className="faint"),
    ])


@dash.callback(Output("pr-body", "children"), Input("pr-tick", "n_intervals"))
def render(_n):
    snap = SM.sysmon().snapshot()
    runs = {r["pid"]: r for r in STORE.list_runs() if r.get("pid")}
    sidecar = V3D.viewer().status() or {}
    rows = []
    for p in snap["procs"]:
        run = runs.get(p["pid"])
        if p["pid"] == sidecar.get("pid"):
            run = {"label": f"the Body tab's 3D view of {sidecar['run']}"}
        elif L3.live().label(p["pid"]):
            run = {"label": L3.live().label(p["pid"])}
        rows.append([C.badge("running" if p["kind"] != "rerun viewer" else "info",
                             p["kind"]), p["pid"], html.Code(p["script"] or p["name"]),
                     p["cpu"], p["rss_mb"], p["threads"], C.duration(p["uptime_s"]),
                     C.fmt(p.get("ports")) if p.get("ports") else "—",
                     run["label"] if run else "",
                     html.Span(p["args"], className="faint")])
    gpu = snap.get("gpu") or {}
    apps = snap.get("gpu_apps") or []
    cam = snap.get("camera") or {}
    torch = snap.get("torch") or {}
    procs = C.card(f"Processes ({len(rows)})", C.table(
        ["", "pid", "script", "cpu %", "RSS MB", "threads", "up", "listening", "run",
         "arguments"], rows, num=(1, 3, 4, 5), max_height="480px") if rows else C.empty(
        "No scrub3d process and no Rerun viewer is running."),
        sub="Python processes whose script lives in scrub3d/, and Rerun viewers. "
            "Wrapper shells that merely mention the path are not listed.")
    gpuc = C.card("GPU", C.kv({"device": gpu.get("name"),
                               "utilisation": f"{gpu.get('util')} %",
                               "memory": f"{gpu.get('mem_used')} / {gpu.get('mem_total')} MiB",
                               "temperature": f"{gpu.get('temp')} °C"})
                  if gpu.get("name") else C.empty(gpu.get("error", "checking…")),
                  C.table(["pid", "process", "memory"],
                          [[a["pid"], a["name"], a["memory"]] for a in apps],
                          max_height="200px") if apps else None,
                  C.note("Windows' driver model reports per-process GPU memory as "
                         "[N/A]. Each job records its own torch peak in its exit "
                         "record instead.", "info"))
    camc = C.card("Camera", _camera(cam))
    torchc = C.card("torch / CUDA", _torch(torch))
    peaks = []
    for r in STORE.list_runs()[:40]:
        if r.get("torch_peak_mb"):
            peaks.append([r["label"], C.clock(r["started"]), r["torch_peak_mb"]])
    peakc = C.card("Torch peak GPU memory per run", C.table(
        ["run", "started", "peak MB"], peaks, num=(2,)) if peaks else C.empty(
        "No finished run used CUDA."))
    return html.Div([procs, html.Div([
        html.Div(gpuc, className="col"), html.Div(peakc, className="col"),
        html.Div([camc, torchc], className="col stack")], className="row",
        style={"marginTop": "12px"})])
