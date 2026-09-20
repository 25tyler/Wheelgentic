"""Vision: what the camera saw and what each model made of it."""
import dash
from dash import Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from devconsole.store import STORE
from devconsole.views import common as C

FRAMES = [("color", "Colour (live, 2 Hz)"), ("depth", "Depth (live, 2 Hz)"),
          ("seg", "Sapiens segmentation"), ("carve", "Limbs carved by MediaPipe"),
          ("still", "Reconstruction (painted when the loop runs)")]


def layout(sel):
    return html.Div([
        dcc.Interval(id="vi-tick", interval=1000),
        dcc.Store(id="vi-ver", data=None),
        html.Div(id="vi-head"),
        html.Div([
            html.Button(id="vi-blur-btn", className="dc-btn", n_clicks=0),
            html.Span("  These are pictures of a person. Blurred by default; nothing "
                      "is kept beyond the latest frame.", className="muted"),
        ], style={"margin": "6px 0 10px"}),
        html.Div(id="vi-frames"),
        html.Div(id="vi-models", style={"marginTop": "12px"}),
    ])


@dash.callback(Output("blur", "data"), Input("vi-blur-btn", "n_clicks"),
               State("blur", "data"), prevent_initial_call=True)
def toggle(n, blur):
    # Mounting the tab re-inserts the button with n_clicks=0, and Dash fires
    # this for it despite prevent_initial_call. Only a real click may unblur.
    if not n:
        raise PreventUpdate
    return not bool(blur)


def _frames(rid, frames, blur):
    tiles = []
    for name, label in FRAMES:
        n = frames.get(name)
        if n:
            img = html.Img(src=f"/frame/{rid}/{name}?v={n}", alt=label)
        else:
            img = html.Div("not produced by this run", className="muted",
                           style={"padding": "40px 10px", "textAlign": "center",
                                  "border": "1px dashed var(--line)",
                                  "borderRadius": "6px"})
        tiles.append(html.Div([img, html.Div([html.Span(label),
                                              html.Span(f"#{n}" if n else "",
                                                        className="faint")],
                                             className="cap")],
                              className="frame"))
    return html.Div(tiles, className="frames" + (" blurred" if blur else ""))


def _models(rid):
    rows = {r["id"]: r for r in STORE.stage_table(rid)}
    snap = STORE.snapshot(rid) or {}
    gpu = {}
    for e in snap.get("misc", []):
        if e.get("k") == "gpu":
            gpu.setdefault(e["fn"], []).append(e.get("mb_delta"))
    mrows = []
    for sid in ("segmentation", "normals", "depthnet", "landmarks", "carve"):
        r = rows.get(sid)
        if not r:
            continue
        for fn, a in (r.get("fns") or {}).items():
            last = STORE.last_span(rid, fn) or {}
            s = last.get("sum") or {}
            mrows.append([r["label"], fn.split(".", 1)[1], a.get("n"),
                          C.ms(a.get("p50")), C.ms(a.get("max")),
                          s.get("device") or "", C.fmt(gpu.get(fn)),
                          C.one_line({k: v for k, v in s.items()
                                      if k not in ("device", "model")}, 80)])
    notes = []
    for fn, label in (("shell._normals_from_sapiens", "normal convention"),
                      ("shell.drop_fattened_edges", "edge pixels dropped"),
                      ("shell.keep_subject", "subject band"),
                      ("shell.fill_dropouts", "AI depth fill"),
                      ("pose.landmarks", "landmarks"),
                      ("scan.scan", "normals used by the scan")):
        last = STORE.last_span(rid, fn)
        if not last:
            continue
        s = last.get("sum") or {}
        if fn == "shell.fill_dropouts":
            st = s.get("stats") or {}
            val = (f"REFUSED: {st.get('why')} ({st.get('rms_mm')} mm rms)"
                   if st and not st.get("filled") else
                   (f"filled {st.get('filled')} of {st.get('holes')} holes, "
                    f"{st.get('rms_mm')} mm rms" if st else "nothing to fill"))
        elif fn == "shell.keep_subject":
            d = s.get("dropped")
            val = ("alone in frame" if not d else
                   f"NOT ALONE: dropped {d.get('points')} points in "
                   f"{d.get('clusters')} cluster(s)")
        elif fn == "scan.scan":
            val = s.get("normals_from")
        else:
            val = s.get("note") or s.get("edge_px_dropped") or C.one_line(s, 120)
        notes.append([label, html.Span(C.fmt(val), className="mono")])
    return html.Div([
        html.Div(C.card("Models", C.table(
            ["stage", "call", "calls", "p50", "max", "device", "GPU MB change", "last"],
            mrows, num=(2, 3, 4)) if mrows else C.empty("No vision models ran."),
            sub="Each Sapiens construction reloads 1.3 GB; the call count shows how "
                "often that happened."), className="col-2"),
        html.Div(C.card("What the reconstruction decided", html.Table(
            html.Tbody([html.Tr([html.Td(a), html.Td(b)]) for a, b in notes]),
            className="dc kv") if notes else C.empty("No reconstruction in this run.")),
            className="col"),
    ], className="row")


# The button's label is set here rather than by a callback of its own: a
# callback whose only new component is its output does not fire when the tab
# mounts, so the button would start blank.
@dash.callback(Output("vi-head", "children"), Output("vi-frames", "children"),
               Output("vi-models", "children"), Output("vi-ver", "data"),
               Output("vi-blur-btn", "children"),
               Input("vi-tick", "n_intervals"), Input("sel-run", "data"),
               Input("blur", "data"), State("vi-ver", "data"))
def render(_n, sel, blur, ver):
    label = "Show images" if blur else "Blur images"
    rid = C.resolve_run(sel)
    if not rid:
        return C.run_header(None), None, None, None, label
    key = [rid, STORE.version(rid, "frames"), STORE.version(rid, "stages"), blur]
    if key == ver and ctx.triggered_id == "vi-tick":
        raise PreventUpdate
    snap = STORE.snapshot(rid) or {}
    return (C.run_header(rid), _frames(rid, snap.get("frames") or {}, blur),
            _models(rid), key, label)
