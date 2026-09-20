"""Catalogue: everything that is implemented, generated from the code."""
import dash
from dash import Input, Output, dcc, html

from devconsole import inventory as INV
from devconsole import probes as PR
from devconsole.views import common as C


def layout(sel):
    cat = INV.catalogue()
    counts = cat["counts"]
    probed = {}
    for s in PR.STAGES:
        for t in s.targets:
            probed.setdefault(t.module, []).append(t.attr)
    head = html.Div([
        html.Span([html.Span(f"{k} ", className="muted"), html.B(str(v))],
                  className="dc-chip")
        for k, v in (("files", counts["files"]), ("lines", f"{counts['lines']:,}"),
                     ("public functions", counts["functions"]),
                     ("classes", counts["classes"]), ("constants", counts["constants"]),
                     ("explained in place", counts["explained"]),
                     ("self-tests", counts["selftests"]),
                     ("probed call boundaries", len(PR.TARGETS)),
                     ("built in", f"{cat['build_ms']} ms"))],
        style={"display": "flex", "flexWrap": "wrap", "gap": "8px", "margin": "4px 0 10px"})
    warn = []
    if cat["missing_from_all"]:
        warn.append(C.note("Not listed in scrub3d/__init__.py __all__: "
                           + ", ".join(cat["missing_from_all"]), "bad"))
    if cat["errors"]:
        warn.append(C.note("Could not parse: " + "; ".join(cat["errors"]), "bad"))
    return html.Div([
        head, *warn,
        html.Div([
            dcc.Input(id="ct-q", type="text", debounce=False, className="dc-in",
                      placeholder="filter: a module, function, class or constant "
                                  "name, or words from a docstring or comment"),
        ], style={"maxWidth": "640px", "marginBottom": "10px"}),
        dcc.Store(id="ct-probed", data=probed),
        html.Div(id="ct-body"),
    ])


def _match(q, *texts):
    return not q or any(q in (t or "").lower() for t in texts)


def _module(m, q, probed):
    fns = [f for f in m["functions"] if _match(q, f["name"], f["doc"], m["name"])]
    classes = []
    for c in m["classes"]:
        meths = [x for x in c["methods"] if _match(q, x["name"], x["doc"], c["name"],
                                                    m["name"])]
        if meths or _match(q, c["name"], c["doc"], m["name"]):
            classes.append((c, meths if q else c["methods"]))
    consts = [k for k in m["constants"] if _match(q, k["name"], k["comment"], k["inline"],
                                                  k["value"], m["name"])]
    if q and not (fns or classes or consts or _match(q, m["name"], m["doc"])):
        return None
    probed_here = set(probed.get(m["name"], []))

    def mark(name):
        return html.Span(" probed", className="tag") if name in probed_here else None

    body = [html.Div(m["doc"], className="doc")]
    if fns:
        body.append(C.table(["function", "what it says it does", "line"],
                            [[html.Span([html.Code(f["sig"]), mark(f["name"])]),
                              f["doc"], f["line"]] for f in fns], num=(2,)))
    for c, meths in classes:
        body.append(html.Div([html.B(f"class {c['name']}"),
                              html.Span(f"  {c['doc']}", className="muted")],
                             style={"marginTop": "8px"}))
        if meths:
            body.append(C.table(["method", "what it says it does", "line"],
                                [[html.Span([html.Code(x["sig"]),
                                             mark(f"{c['name']}.{x['name']}")]),
                                  x["doc"], x["line"]] for x in meths], num=(2,)))
    if consts:
        body.append(html.Div(html.B("constants"), style={"marginTop": "8px"}))
        body.append(C.table(
            ["name", "value", "why, as the code explains it", "line"],
            [[html.Code(k["name"]), html.Code(k["value"]),
              html.Div(k["comment"] or k["inline"] or "no comment",
                       className="const-comment" if (k["comment"] or k["inline"])
                       else "faint"), k["line"]] for k in consts], num=(3,)))
    flags = []
    if m["selftest"]:
        flags.append(html.Span("self-test", className="tag"))
    if probed_here:
        flags.append(html.Span(f"{len(probed_here)} probed", className="tag"))
    summary = html.Summary([html.Code(m["name"]),
                            html.Span(f"   {m['path']} · {m['lines']} lines   ",
                                      className="faint"), *flags])
    return html.Details([summary, *body], open=bool(q), className="cat-mod")


@dash.callback(Output("ct-body", "children"), Input("ct-q", "value"),
               Input("ct-probed", "data"))
def render(q, probed):
    q = (q or "").strip().lower()
    cat = INV.catalogue()
    by_name = {m["name"]: m for m in cat["modules"]}
    out = []
    seen = set()
    groups = list(cat["groups"]) + [("tools", [m["name"] for m in cat["modules"]
                                               if m["kind"] == "tool"])]
    for label, names in groups:
        items = []
        for n in names:
            m = by_name.get(n)
            if m is None or n in seen:
                continue
            seen.add(n)
            d = _module(m, q, probed or {})
            if d is not None:
                items.append(d)
        if items:
            out.append(C.card(label, *items))
    rest = [m for m in cat["modules"] if m["name"] not in seen]
    if rest:
        items = [d for d in (_module(m, q, probed or {}) for m in rest) if d is not None]
        if items:
            out.append(C.card("not grouped", *items))
    return html.Div(out, className="stack") if out else C.empty("Nothing matches.")
