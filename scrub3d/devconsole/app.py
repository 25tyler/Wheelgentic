"""devconsole/app.py -- the scrub3d developer console.

    python scrub3d/devconsole/app.py            # http://127.0.0.1:8077
    python scrub3d/devconsole/app.py --port 8078
    python scrub3d/devconsole/app.py --no-3d    # without the live 3D server

For whoever is changing the code. The Overview puts the rig in live 3D next to
every data process as one diagram. The other tabs show every stage of the
pipeline as a process -- what ran, in what order, how long it took, what it
decided and why it refused -- plus the live loop's telemetry, every safety
verdict, the vision stages, the body and its territories, the rig, a catalogue
of everything implemented generated from the code, the jobs it can start, and
the processes and GPU on this machine.

Localhost only: runs can hold camera frames of a person. It never commands
hardware.
"""
import argparse
import logging
import os
import re
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRUB3D = os.path.dirname(HERE)
# The console is imported as a PACKAGE. Its modules are called store, jobs,
# events..., and running this file puts its own directory first on sys.path,
# where a bare `import events` elsewhere would find ours.
sys.path[:] = [p for p in sys.path
               if os.path.normcase(os.path.abspath(p or ".")) != os.path.normcase(HERE)]
if SCRUB3D not in sys.path:
    sys.path.insert(0, SCRUB3D)

import dash                                                    # noqa: E402
from dash import Input, Output, State, dcc, html, no_update     # noqa: E402
from flask import Response, abort                               # noqa: E402

from devconsole import jobs as JB                               # noqa: E402
from devconsole import live3d as L3                             # noqa: E402
from devconsole import sysmon as SM                             # noqa: E402
from devconsole import viewer3d as V3D                          # noqa: E402
from devconsole.store import STORE, RUNS                        # noqa: E402
from devconsole.views import common as C                        # noqa: E402

PORT = 8077
HOST = "127.0.0.1"
# Which console process served the page. A page left open across a restart
# calls callbacks the new process may not have, so the header says to reload.
BOOT = f"{os.getpid()}-{time.time():.0f}"

RUN_ID = re.compile(r"^\d{8}-\d{6}-[A-Za-z0-9_.-]+(/children/\d{8}-\d{6}-[A-Za-z0-9_.-]+)*$")
FRAME_FILES = {"color": "color.jpg", "depth": "depth.jpg", "seg": "seg.jpg",
               "carve": "carve.jpg", "still": "still.jpg"}

app = dash.Dash(__name__, title="scrub3d · dev console", update_title=None,
                suppress_callback_exceptions=True,
                assets_folder=os.path.join(HERE, "assets"))
server = app.server


def _run_path(rid):
    if not rid or not RUN_ID.match(rid) or ".." in rid:
        abort(404)
    path = os.path.normpath(os.path.join(RUNS, rid))
    if not path.startswith(os.path.normpath(RUNS) + os.sep) or not os.path.isdir(path):
        abort(404)
    return path


def _read(path):
    for _ in range(4):
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except PermissionError:
            time.sleep(0.05)
        except FileNotFoundError:
            break
    abort(404)


@server.route("/frame/<path:rid>/<name>")
def frame(rid, name):
    fname = FRAME_FILES.get(name)
    if fname is None:
        abort(404)
    data = _read(os.path.join(_run_path(rid), "frames", fname))
    return Response(data, mimetype="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@server.route("/stdout/<path:rid>")
def stdout(rid):
    data = _read(os.path.join(_run_path(rid), "stdout.log"))
    return Response(data.decode("utf-8", errors="replace"),
                    mimetype="text/plain; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


# Imported after the app exists: each view registers its own callbacks.
from devconsole.views import (body, catalogue, home, jobsview, live,   # noqa: E402
                              logs, pipeline, processes, rig, safety, system,
                              vision)

TABS = [
    ("overview", "Overview", home),
    ("system", "System", system),
    ("pipeline", "Pipeline", pipeline),
    ("live", "Live loop", live),
    ("safety", "Safety", safety),
    ("vision", "Vision", vision),
    ("body", "Body & territories", body),
    ("rig", "Rig & placement", rig),
    ("catalogue", "Catalogue", catalogue),
    ("jobs", "Jobs & tests", jobsview),
    ("processes", "Processes", processes),
    ("logs", "Logs", logs),
]
VIEWS = {k: v for k, _, v in TABS}


def _run_options(runs):
    opts = [{"label": "latest run (follow)", "value": "latest"}]
    for r in runs[:80]:
        mark = {"running": "● ", "starting": "◌ ", "failed": "✕ ", "lost": "? ",
                "killed": "✕ ", "stopped": "■ "}.get(r["status"], "")
        indent = "    ↳ " if r["nested"] else ""
        opts.append({"label": f"{indent}{mark}{r['label']}  ·  {C.clock(r['started'])}"
                              f"  ·  {r['status']}", "value": r["id"]})
    return opts


def _header():
    # The options are filled in now, not two seconds later: the dropdown drops
    # a value it has no option for, which would lose a `?run=` link.
    return html.Div([
        html.Div(["scrub3d ", html.Small("dev console · backend only")],
                 className="dc-title"),
        dcc.Dropdown(id="run-dd", clearable=False, value="latest",
                     className="dc-runsel", options=_run_options(STORE.list_runs())),
        html.Div(id="hdr-chips", style={"display": "flex", "gap": "8px",
                                        "flexWrap": "wrap"}),
        html.Div(className="grow"),
        html.A("stdout", id="hdr-stdout", href="#", target="_blank",
               className="dc-chip", style={"textDecoration": "none"}),
    ], className="dc-header")


def serve_layout():
    """Built per page load, so the run list is current when the page opens."""
    return html.Div([
        dcc.Location(id="url", refresh=False),
        dcc.Store(id="sel-run", storage_type="session", data="latest"),
        dcc.Store(id="blur", storage_type="local", data=True),
        dcc.Store(id="boot", data=BOOT),
        dcc.Interval(id="hdr-tick", interval=2000),
        _header(),
        dcc.Tabs(id="tabs", value="overview", className="dc-tabs",
                 children=[dcc.Tab(label=label, value=key, className="dc-tab",
                                   selected_className="dc-tab--selected")
                           for key, label, _ in TABS]),
        html.Div(id="tab-body"),
    ], className="dc-shell")


app.layout = serve_layout


@dash.callback(Output("tabs", "value"), Output("run-dd", "value"),
               Input("url", "search"))
def from_url(search):
    """`/?tab=safety&run=<id>` opens that tab on that run."""
    tab, run = no_update, no_update
    if search:
        from urllib.parse import parse_qs
        q = parse_qs(search.lstrip("?"))
        if q.get("tab") and q["tab"][0] in VIEWS:
            tab = q["tab"][0]
        if q.get("run"):
            run = q["run"][0]
    return tab, run


@dash.callback(Output("sel-run", "data"), Input("run-dd", "value"))
def pick_run(value):
    return value or "latest"


# The selected run is State, not Input: every view follows it through its own
# callbacks, and remounting a tab on each selection would wipe what the tab
# was showing (the message a job button just wrote, a half-typed filter).
@dash.callback(Output("tab-body", "children"), Input("tabs", "value"),
               State("sel-run", "data"), State("url", "search"))
def route(tab, sel, search):
    view = VIEWS.get(tab or "overview", home)
    try:
        if view is pipeline:
            # The Overview's diagram links a stage here.
            from urllib.parse import parse_qs
            stage = (parse_qs((search or "").lstrip("?")).get("stage") or [None])[0]
            return view.layout(sel, stage=stage)
        return view.layout(sel)
    except Exception as exc:                                  # noqa: BLE001
        import traceback
        return C.note(f"{tab} failed to render: {exc!r}\n{traceback.format_exc()}",
                      "bad")


@dash.callback(Output("run-dd", "options"), Output("hdr-chips", "children"),
               Output("hdr-stdout", "href"),
               Input("hdr-tick", "n_intervals"), State("sel-run", "data"),
               State("boot", "data"), State("url", "search"))
def header(_n, sel, boot, search):
    runs = STORE.list_runs()
    opts = _run_options(runs)
    running = [r for r in runs if r["status"] in ("running", "starting")]
    snap = SM.sysmon().snapshot()
    gpu = snap.get("gpu") or {}
    cam = snap.get("camera") or {}
    chips = [html.Span([html.Span(className="dot", style={
        "background": "var(--run)" if running else "var(--faint)"}),
        html.B(str(len(running))), " running"], className="dc-chip")]
    if gpu.get("util") is not None:
        chips.append(html.Span(["GPU ", html.B(f"{gpu['util']:.0f}%"),
                                f"  {gpu['mem_used'] / 1024:.1f}/"
                                f"{gpu['mem_total'] / 1024:.0f} GB"], className="dc-chip"))
    if cam:
        devs = cam.get("devices") or []
        chips.append(html.Span(["camera ", html.B(devs[0]["name"] if devs else "none")],
                               className="dc-chip"))
    if boot != BOOT:
        # A real address: React drops an empty href, which leaves no link.
        chips.insert(0, html.A("the console restarted: reload this page",
                               href="/" + (search or ""), className="dc-chip stale"))
    rid = C.resolve_run(sel)
    href = f"/stdout/{rid}" if rid else "#"
    return opts, chips, href


def _port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((HOST, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def camera_busy():
    return JB.jobs().camera_busy()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-3d", action="store_true",
                    help="do not start the Overview's live 3D server")
    a = ap.parse_args()
    if not _port_free(a.port):
        print(f"port {a.port} is already in use on {HOST}. Is a console already "
              f"running? Use --port to pick another.", file=sys.stderr)
        return 1
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    STORE.start()
    left = V3D.reap_orphans(RUNS) + L3.reap_orphans(RUNS)
    if left:
        print(f"stopped {left} Rerun process(es) a previous console left running")
    JB.jobs()
    SM.sysmon(camera_busy).start()
    if a.no_3d:
        L3.live().why = "the console was started with --no-3d"
    else:
        print(L3.live().start(), flush=True)
    # Dash registers callbacks on its first request, but marks that done before
    # it has. A page left open across a restart fires several at once, and the
    # ones that lose the race get "callback not found". Take the first request
    # here, before the port opens.
    app.server.test_client().get("/")
    print(f"scrub3d dev console on http://{HOST}:{a.port}   (runs in "
          f"{os.path.relpath(RUNS, os.path.dirname(SCRUB3D))})", flush=True)
    # The reloader follows debug in Flask, and a reloader starts every
    # background thread twice. Both off, explicitly.
    app.run(host=HOST, port=a.port, debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
