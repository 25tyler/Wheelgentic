"""The small pieces every tab is built from."""
import datetime
import json
import time

from dash import html

from devconsole.store import STORE

ARM_RGB = [(232, 92, 74), (74, 160, 232), (108, 199, 122), (232, 176, 74)]
ARM_COLOURS = [f"rgb{c}" for c in ARM_RGB]

PLOT_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#11151b",
    font=dict(color="#d7dce3", size=11,
              family="Segoe UI, system-ui, sans-serif"),
    margin=dict(l=48, r=12, t=30, b=34),
    legend=dict(orientation="h", y=1.14, x=0, font=dict(size=10)),
    xaxis=dict(gridcolor="#232833", zerolinecolor="#2a303b"),
    yaxis=dict(gridcolor="#232833", zerolinecolor="#2a303b"),
)


def status_class(s):
    return "s-" + str(s or "na").lower().replace(" ", "-").replace("/", "")


def badge(s, text=None):
    return html.Span(text or str(s), className=f"badge {status_class(s)}")


def card(title, *children, right=None, sub=None, cls=""):
    head = [html.Span(title)]
    if right is not None:
        head += [html.Span(className="grow"), right]
    body = [html.H3(head)]
    if sub:
        body.append(html.Div(sub, className="sub"))
    body.extend(c for c in children if c is not None)
    return html.Div(body, className=f"card {cls}")


def note(text, kind=""):
    return html.Div(text, className=f"note {kind}")


def empty(text="Nothing yet."):
    return html.Div(text, className="muted")


def table(columns, rows, num=(), mono=(), max_height=None):
    head = html.Thead(html.Tr([html.Th(c) for c in columns]))
    body = []
    for r in rows:
        cells = []
        for i, v in enumerate(r):
            cls = "num" if i in num else ("mono" if i in mono else None)
            cells.append(html.Td(v if isinstance(v, (html.Span, html.Div, html.A, list,
                                                     html.Code, html.B, html.Details,
                                                     html.Button))
                                 else fmt(v), className=cls))
        body.append(html.Tr(cells))
    t = html.Table([head, html.Tbody(body)], className="dc")
    style = {"maxHeight": max_height} if max_height else None
    return html.Div(t, className="scroll-x scroll-y", style=style)


def kv(d, keys=None):
    items = d.items() if keys is None else [(k, d.get(k)) for k in keys]
    return html.Table(html.Tbody([html.Tr([html.Td(k), html.Td(
        v if isinstance(v, (html.Span, html.Div, list)) else fmt(v),
        className="mono")]) for k, v in items]), className="dc kv")


def fmt(v, nd=3):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if v != v:
            return "nan"
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        if abs(v) >= 100:
            return f"{v:.1f}"
        return f"{v:.{nd}g}" if abs(v) < 1 else f"{v:.2f}"
    if isinstance(v, (list, tuple)):
        if len(v) <= 8 and all(not isinstance(x, (list, tuple, dict)) for x in v):
            return ", ".join(fmt(x) for x in v)
        return json.dumps(v, default=str)[:300]
    if isinstance(v, dict):
        return json.dumps(v, default=str)[:300]
    return str(v)


def ms(v):
    if v is None:
        return "—"
    if v >= 1000:
        return f"{v / 1000:.2f} s"
    if v >= 10:
        return f"{v:.0f} ms"
    return f"{v:.2f} ms"


def age(t):
    if not t:
        return "—"
    d = time.time() - float(t)
    if d < 60:
        return f"{d:.0f}s ago"
    if d < 3600:
        return f"{d / 60:.0f}m ago"
    if d < 86400:
        return f"{d / 3600:.1f}h ago"
    return f"{d / 86400:.1f}d ago"


def clock(t):
    if not t:
        return "—"
    return datetime.datetime.fromtimestamp(float(t)).strftime("%H:%M:%S")


def duration(s):
    if s is None:
        return "—"
    s = float(s)
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        return f"{int(s // 60)}m{int(s % 60):02d}s"
    return f"{int(s // 3600)}h{int((s % 3600) // 60):02d}m"


def json_tree(obj, label=None, open_depth=1, depth=0):
    """A collapsible view of any JSON-like value."""
    def leaf(v):
        if isinstance(v, bool) or v is None:
            return html.Span(json.dumps(v), className="jt-b")
        if isinstance(v, (int, float)):
            return html.Span(fmt(v) if isinstance(v, float) else str(v), className="jt-n")
        return html.Span(json.dumps(v, default=str)[:600], className="jt-s")

    if isinstance(obj, dict) and obj:
        kids = [json_tree(v, k, open_depth, depth + 1) for k, v in list(obj.items())[:200]]
        head = f"{label}  {{{len(obj)}}}" if label is not None else f"{{{len(obj)}}}"
        return html.Details([html.Summary(head)] + kids, open=depth < open_depth,
                            className="jt")
    if isinstance(obj, list) and obj and any(isinstance(x, (dict, list)) for x in obj):
        kids = [json_tree(v, f"[{i}]", open_depth, depth + 1)
                for i, v in enumerate(obj[:200])]
        head = f"{label}  [{len(obj)}]" if label is not None else f"[{len(obj)}]"
        return html.Details([html.Summary(head)] + kids, open=depth < open_depth,
                            className="jt")
    row = [html.Span(f"{label}: ", className="jt-k")] if label is not None else []
    if isinstance(obj, list):
        row.append(html.Span(fmt(obj), className="jt-s"))
    else:
        row.append(leaf(obj))
    return html.Div(row, className="jt-row")


def _placeholder(v):
    """The pruner's stand-ins ("<dict>", "<3 bytes>") say nothing on one line."""
    if isinstance(v, str):
        return v.startswith("<") and v.endswith(">")
    if isinstance(v, (list, tuple)) and v:
        return all(_placeholder(x) for x in v)
    return False


def one_line(summary, limit=110):
    if not summary:
        return ""
    if isinstance(summary, dict):
        parts = []
        for k, v in summary.items():
            if isinstance(v, (dict, list)) and len(json.dumps(v, default=str)) > 40:
                continue
            if _placeholder(v):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool)                     and 1e9 < v < 1e10:
                parts.append(f"{k}={clock(v)}")         # a wall-clock timestamp
                continue
            parts.append(f"{k}={fmt(v)}")
        s = "  ".join(parts)
    else:
        s = fmt(summary)
    return s if len(s) <= limit else s[:limit] + "…"


def resolve_run(sel):
    """"latest", or a run id the store knows. Anything else -- a stale or
    hand-typed ?run= -- is no run, rather than a path handed to a view."""
    if not sel or sel == "latest":
        return STORE.latest()
    return sel if STORE.has(sel) else None


def run_header(rid):
    if not rid:
        return note("No runs yet. Start one from the Jobs tab, or run "
                    "`python scrub3d/devconsole/runner.py scrub3d/main.py --replay "
                    "scrub3d/data/bag01` in a terminal.", "info")
    snap = STORE.snapshot(rid)
    if snap is None:
        return note(f"Run {rid} is gone.", "bad")
    s = snap["summary"]
    cost = snap.get("cost") or {}
    wall = None
    if snap.get("first_t") and snap.get("last_t"):
        wall = snap["last_t"] - snap["first_t"]
    overhead = None
    if wall and cost.get("probe_ms") is not None:
        overhead = 100.0 * cost["probe_ms"] / 1000.0 / max(wall, 1e-6)
    bits = [badge(s["status"]), html.B(f"  {s['label']}  "),
            html.Span(s["script"], className="mono muted"),
            html.Span(f"   started {clock(s['started'])} ({age(s['started'])})",
                      className="muted")]
    if s.get("seconds") is not None:
        bits.append(html.Span(f"   took {duration(s['seconds'])}", className="muted"))
    if s.get("code") is not None:
        bits.append(html.Span(f"   exit {s['code']}", className="muted"))
    if overhead is not None:
        bits.append(html.Span(f"   probe cost {overhead:.2f}% of run time",
                              className="faint"))
    if s.get("why"):
        bits.append(html.Span(f"   ({s['why']})", className="faint"))
    return html.Div(bits, className="msg")
