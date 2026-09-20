"""System: the machine, the code, the data, and what is open."""
import dash
from dash import Input, Output, State, dcc, html

from devconsole import inventory as INV
from devconsole import jobs as JB
from devconsole import sysmon as SM
from devconsole.store import STORE
from devconsole.views import common as C


def layout(sel):
    return html.Div([
        dcc.Interval(id="ov-tick", interval=3000),
        html.Div(id="ov-dynamic"),
        html.Div(_static(), className="stack", style={"marginTop": "12px"}),
    ])


def _static():
    cat = INV.catalogue()
    counts = cat["counts"]
    git = INV.git_facts()
    env = INV.environment()
    drift = INV.drift()
    gaps = INV.gaps()
    rig = INV.rig()

    code = C.card(
        "Code", C.kv({
            "files": counts["files"], "lines": f"{counts['lines']:,}",
            "public functions": counts["functions"], "classes": counts["classes"],
            "constants": f"{counts['constants']} ({counts['explained']} explained in place)",
            "self-tests": counts["selftests"],
            "catalogue built in": f"{cat['build_ms']} ms"}),
        sub="Generated from the AST; see the Catalogue tab.")

    dirty = git.get("dirty") or []
    gitc = C.card(
        "Git",
        C.kv({"branch": git.get("branch"), "commit": git.get("commit"),
              "ahead of main": git.get("ahead_of_main"),
              "working tree": C.badge("clean" if not dirty else "warn",
                                      "clean" if not dirty else f"{len(dirty)} changed")}),
        C.table(["", "when", "commit"],
                [[l[0], l[1] if len(l) > 1 else "", l[2] if len(l) > 2 else ""]
                 for l in git.get("log", [])[:8]], mono=(0,), max_height="220px"))

    pk = env["packages"]
    envc = C.card("Environment",
                  C.kv({"python": env["python"], **{k: v for k, v in pk.items()}}))

    def finding(ok_flag, what, detail, kind):
        if kind == "drift":
            b = C.badge("ok" if ok_flag else "fail", "agrees" if ok_flag else "DRIFT")
        else:
            b = C.badge("warn" if ok_flag else "ok", "open" if ok_flag else "closed")
        return html.Tr([html.Td(b), html.Td(what), html.Td(detail, className="muted")])

    findings = C.card(
        "Findings, computed from the code",
        html.Table(html.Tbody(
            [finding(d["ok"], d["what"], d["detail"], "drift") for d in drift]
            + [finding(g["open"], g["what"], g["detail"], "gap") for g in gaps]),
            className="dc"),
        sub="Drift: two places meant to agree that do not. Gaps: something the code "
            "demonstrably does not do yet.")

    weights = C.card("Model weights", C.table(
        ["model", "file", "present", "size"],
        [[w["model"], w["file"], C.badge("ok" if w["present"] else "fail",
                                         "yes" if w["present"] else "missing"),
          f"{w['mb']} MB" if w["mb"] else "—"] for w in INV.weights()], mono=(1,)))

    caps = INV.captures()
    capc = C.card(f"Captures ({len(caps)})", C.table(
        ["name", "label", "frames", "preset", "rot", "bag", "segmentation"],
        [[c["name"], c["label"], c["frames"], c["preset"], c["rotation"],
          f"{c['bag_mb']} MB" if c["bag"] else "—",
          C.badge("ok" if c["segmentation"] == "ok" else
                  ("na" if str(c["segmentation"]).startswith("not needed") else "fail"),
                  c["segmentation"])] for c in caps], mono=(0,), max_height="320px"),
        sub="data/ is gitignored: these are pictures and geometry of people.")

    bods = INV.bodies()
    bodc = C.card(f"Saved bodies ({len(bods)})", C.table(
        ["name", "given by", "delete after", "consent", "identity"],
        [[b["name"], b.get("given_by"), b.get("delete_after"),
          C.badge("fail" if b.get("expired") else "ok",
                  "EXPIRED" if b.get("expired") else "valid"),
          C.one_line(b.get("identity"), 90)] for b in bods]) if bods
        else C.empty("None saved."))

    arms = (rig or {}).get("arms", [])
    rigc = C.card(f"Rig: {(rig or {}).get('name', '?')}", C.table(
        ["arm", "x mm", "y mm", "z mm", "facing °"],
        [[html.Span(a.get("id"), style={"color": C.ARM_COLOURS[i % 4]}),
          a.get("x_mm"), a.get("y_mm"), a.get("z_mm"), a.get("facing_deg")]
         for i, a in enumerate(arms)], num=(1, 2, 3, 4)))

    return [
        html.Div([html.Div(code, className="col"), html.Div(gitc, className="col"),
                  html.Div(envc, className="col")], className="row"),
        findings,
        html.Div([html.Div(capc, className="col-2"),
                  html.Div([weights, bodc, rigc], className="col stack")], className="row"),
    ]


def _latest_with(scripts):
    for r in STORE.list_runs(include_nested=False):
        if r["script"] in scripts:
            return r
    return None


@dash.callback(Output("ov-dynamic", "children"), Input("ov-tick", "n_intervals"),
               State("sel-run", "data"))
def dynamic(_n, sel):
    snap = SM.sysmon().snapshot()
    gpu = snap.get("gpu") or {}
    cam = snap.get("camera") or {}
    torch = snap.get("torch") or {}
    runs = STORE.list_runs()
    running = [r for r in runs if r["status"] in ("running", "starting")]

    if gpu.get("util") is not None:
        gpuc = C.card("GPU", html.Div(f"{gpu['util']:.0f}%", className="big"),
                      html.Div(f"{gpu['name']}  ·  {gpu['mem_used']:.0f} / "
                               f"{gpu['mem_total']:.0f} MiB  ·  {gpu['temp']:.0f} °C",
                               className="muted"))
    else:
        gpuc = C.card("GPU", C.empty(gpu.get("error", "checking…")))

    if not cam:
        camc = C.card("Camera", C.empty("checking…"))
    elif cam.get("ok"):
        devs = cam.get("devices") or []
        camc = C.card("Camera", html.Div(devs[0]["name"] if devs else "none connected",
                                         className="big"),
                      html.Div(f"serial {devs[0]['serial']} · firmware {devs[0]['firmware']}"
                               if devs else "Live and record jobs are disabled until "
                               "a D455 is enumerated.", className="muted"),
                      html.Div(cam.get("skipped", ""), className="faint"))
    else:
        camc = C.card("Camera", C.note(cam.get("error", "?"), "bad"))

    if not torch:
        torchc = C.card("torch / CUDA", C.empty("checking in a subprocess…"))
    elif torch.get("ok"):
        torchc = C.card("torch / CUDA", html.Div(
            torch.get("device") or "CPU only", className="big"),
            html.Div(f"torch {torch.get('torch')} · CUDA {torch.get('cuda_version')}",
                     className="muted"))
    else:
        torchc = C.card("torch / CUDA", C.note(torch.get("error", "?"), "bad"))

    runc = C.card(f"Running now ({len(running)})", C.table(
        ["run", "script", "started", "pid"],
        [[r["label"], r["script"], C.age(r["started"]), r["pid"]] for r in running])
        if running else C.empty("Nothing running."))

    pre = _latest_with(("main.py",))
    prec = C.card("Last pre-flight", C.empty("No main.py run yet."))
    if pre is not None:
        s = STORE.snapshot(pre["id"])
        rows = (s or {}).get("preflight") or []
        prec = C.card("Last pre-flight", C.table(
            ["", "check", "detail"],
            [[C.badge(st), n, html.Span(d, className="muted")] for n, st, d in rows]),
            sub=f"{pre['label']} · {C.age(pre['started'])}")

    sweep = JB.jobs().last_sweep()
    if sweep:
        res = sweep.get("results") or {}
        passed = sum(1 for v in res.values() if v.get("status") == "ok")
        sweepc = C.card("Last self-test sweep", html.Div(
            f"{passed} / {sweep.get('total')} passed", className="big"),
            html.Div("running now" if sweep.get("running") else
                     f"finished {C.age(sweep.get('ended'))}", className="muted"),
            html.Div([C.badge("fail", m) for m, v in res.items()
                      if v.get("status") not in ("ok",)], style={"marginTop": "6px"}))
    else:
        sweepc = C.card("Last self-test sweep",
                        C.empty("Not run from the console yet (Jobs tab)."))

    return html.Div([
        html.Div([html.Div(gpuc, className="col"), html.Div(camc, className="col"),
                  html.Div(torchc, className="col"), html.Div(sweepc, className="col")],
                 className="row"),
        html.Div([html.Div(runc, className="col"), html.Div(prec, className="col-2")],
                 className="row", style={"marginTop": "12px"}),
    ])
