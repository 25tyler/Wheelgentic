"""devconsole/inventory.py -- everything that is implemented, read from the code.

Nothing here imports a pipeline module. The catalogue is the AST of every file
in `scrub3d/` and `scrub3d/tools/`, so it is generated rather than typed and
cannot drift from what the code actually says. Parsing all of it takes about
150 ms, so it is rebuilt whenever any file's mtime changes rather than cached
by hand.

The same goes for what the code EXPLAINS. Most of the numbers in this package
carry a comment above them saying why they are what they are; the catalogue
shows that comment next to the value instead of asking anyone to go looking.

Besides the code: the rig file, the captures, the weights, the saved bodies,
git, the installed packages, and two kinds of finding that are computed rather
than written as prose:

  drift   two places that are meant to agree and do not
  gaps    something the code demonstrably does not do yet
"""
import ast
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRUB3D = os.path.dirname(HERE)
WORKTREE = os.path.dirname(SCRUB3D)
DATA = os.path.join(SCRUB3D, "data")
WEIGHTS = os.path.join(SCRUB3D, "weights")
BODIES = os.path.join(SCRUB3D, "bodies")
CONFIG = os.path.join(SCRUB3D, "config.json")
REQS = os.path.join(SCRUB3D, "requirements.txt")

KEY_PACKAGES = ("numpy", "scipy", "open3d", "rerun-sdk", "torch", "mediapipe",
                "opencv-python", "pyrealsense2", "trimesh", "dash", "plotly",
                "psutil", "flask", "pyserial")


def module_files():
    """-> [(module name, path, kind)] for every scrub3d source file."""
    out = []
    for p in sorted(glob.glob(os.path.join(SCRUB3D, "*.py"))):
        name = os.path.splitext(os.path.basename(p))[0]
        if name != "__init__":
            out.append((name, p, "module"))
    for p in sorted(glob.glob(os.path.join(SCRUB3D, "tools", "*.py"))):
        out.append((os.path.splitext(os.path.basename(p))[0], p, "tool"))
    return out


# ---------------------------------------------------------------------------
# The AST catalogue
# ---------------------------------------------------------------------------

def _first_para(doc):
    if not doc:
        return ""
    return doc.strip().split("\n\n")[0].replace("\n", " ").strip()


def _sig(node):
    try:
        return f"{node.name}({ast.unparse(node.args)})"
    except Exception:                                         # noqa: BLE001
        return f"{node.name}(...)"


def _comment_above(lines, lineno, col=0):
    """The contiguous comment block directly above a line (1-based).

    Only comments starting in the statement's own column count. A deeper one
    is the wrapped tail of the previous line's trailing comment, and belongs
    to that line.
    """
    i = lineno - 2
    block = []
    while i >= 0:
        text = lines[i].lstrip()
        if not text.startswith("#") or len(lines[i]) - len(text) != col:
            break
        block.insert(0, text.lstrip("#").strip())
        i -= 1
    return "\n".join(block).strip()


def _inline_comment(lines, index):
    """A trailing comment, with the lines that continue it at the same column."""
    # A '#' inside a string would fool this; constants rarely do that, and the
    # worst case is a comment shown that is really part of a value.
    m = re.search(r"\s#\s?(.*)$", lines[index])
    if not m:
        return ""
    col = m.start() + 1
    parts = [m.group(1).strip()]
    for line in lines[index + 1:]:
        text = line.lstrip()
        if not text.startswith("#") or len(line) - len(text) != col:
            break
        parts.append(text.lstrip("#").strip())
    return " ".join(p for p in parts if p)


def _value(node, limit=160):
    try:
        s = ast.unparse(node)
    except Exception:                                         # noqa: BLE001
        return "?"
    return s if len(s) <= limit else s[:limit] + " ..."


def _is_main_guard(node):
    t = getattr(node, "test", None)
    return (isinstance(node, ast.If) and isinstance(t, ast.Compare)
            and isinstance(t.left, ast.Name) and t.left.id == "__name__"
            and len(t.comparators) == 1
            and isinstance(t.comparators[0], ast.Constant)
            and t.comparators[0].value == "__main__")


def _prints_ok(tree):
    """Does this file print a line that ends in OK? That is what a self-test does."""
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if n.value.rstrip().endswith("OK") and len(n.value.strip()) >= 2:
                return True
        if isinstance(n, ast.JoinedStr):
            tail = n.values[-1] if n.values else None
            if (isinstance(tail, ast.Constant) and isinstance(tail.value, str)
                    and tail.value.rstrip().endswith("OK")):
                return True
    return False


def parse_module(name, path, kind):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    lines = src.splitlines()
    tree = ast.parse(src, filename=path)
    doc = ast.get_docstring(tree) or ""
    funcs, classes, consts = [], [], []
    has_main = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                funcs.append({"name": node.name, "sig": _sig(node),
                              "doc": _first_para(ast.get_docstring(node)),
                              "line": node.lineno})
        elif isinstance(node, ast.ClassDef):
            methods = []
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        not m.name.startswith("_") or m.name in ("__init__", "__call__")):
                    methods.append({"name": m.name, "sig": _sig(m),
                                    "doc": _first_para(ast.get_docstring(m)),
                                    "line": m.lineno})
            classes.append({"name": node.name, "doc": _first_para(ast.get_docstring(node)),
                            "line": node.lineno, "methods": methods})
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not names or not all(n.isupper() and not n.startswith("_") for n in names):
                continue
            if node.value is None:
                continue
            above = _comment_above(lines, node.lineno, node.col_offset)
            inline = _inline_comment(lines, node.lineno - 1)
            for n in names:
                consts.append({"name": n, "value": _value(node.value),
                               "comment": above, "inline": inline,
                               "line": node.lineno})
        elif _is_main_guard(node):
            has_main = True
    return {"name": name, "path": os.path.relpath(path, WORKTREE), "kind": kind,
            "doc": _first_para(doc), "lines": len(lines),
            "functions": funcs, "classes": classes, "constants": consts,
            "has_main": has_main, "selftest": has_main and _prints_ok(tree)}


def init_groups():
    """The grouping `scrub3d/__init__.py` already gives `__all__`.

    -> ([(group label, [module names])], set of all listed names)
    """
    path = os.path.join(SCRUB3D, "__init__.py")
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    groups, listed = [], set()
    inside, label = False, "ungrouped"
    for line in lines:
        s = line.strip()
        if s.startswith("__all__"):
            inside = True
            continue
        if not inside:
            continue
        if s.startswith("]"):
            break
        if s.startswith("#"):
            label = s.lstrip("#").strip()
            groups.append((label, []))
            continue
        for name in re.findall(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", s):
            if not groups:
                groups.append((label, []))
            groups[-1][1].append(name)
            listed.add(name)
    return groups, listed


_CACHE = {"key": None, "value": None}
_LOCK = threading.Lock()


def _mtime_key():
    files = [p for _, p, _ in module_files()] + [os.path.join(SCRUB3D, "__init__.py")]
    return tuple((p, os.path.getmtime(p)) for p in files if os.path.exists(p))


def catalogue():
    """The whole catalogue, rebuilt only when a source file changed."""
    key = _mtime_key()
    with _LOCK:
        if _CACHE["key"] == key and _CACHE["value"] is not None:
            return _CACHE["value"]
        t0 = time.perf_counter()
        mods = []
        errors = []
        for name, path, kind in module_files():
            try:
                mods.append(parse_module(name, path, kind))
            except SyntaxError as exc:
                errors.append(f"{name}: {exc}")
        groups, listed = init_groups()
        group_of = {m: g for g, ms in groups for m in ms}
        for m in mods:
            m["group"] = group_of.get(m["name"], "tools" if m["kind"] == "tool"
                                      else "not in __all__")
        missing = sorted(m["name"] for m in mods
                         if m["kind"] == "module" and m["name"] not in listed)
        value = {
            "built": time.time(),
            "build_ms": round((time.perf_counter() - t0) * 1000, 1),
            "modules": mods, "groups": groups, "missing_from_all": missing,
            "listed_but_absent": sorted(listed - {m["name"] for m in mods}),
            "errors": errors,
            "counts": {
                "files": len(mods),
                "lines": sum(m["lines"] for m in mods),
                "functions": sum(len(m["functions"]) for m in mods),
                "classes": sum(len(m["classes"]) for m in mods),
                "constants": sum(len(m["constants"]) for m in mods),
                "explained": sum(1 for m in mods for c in m["constants"]
                                 if c["comment"] or c["inline"]),
                "selftests": sum(1 for m in mods if m["selftest"]
                                 and m["kind"] == "module"),
            },
        }
        _CACHE.update(key=key, value=value)
        return value


def selftest_modules():
    return [m["name"] for m in catalogue()["modules"]
            if m["selftest"] and m["kind"] == "module"]


def constant(module, name, default=None):
    """A constant's value as the code states it, for threshold lines on charts."""
    for m in catalogue()["modules"]:
        if m["name"] == module:
            for c in m["constants"]:
                if c["name"] == name:
                    try:
                        return ast.literal_eval(c["value"])
                    except Exception:                         # noqa: BLE001
                        return default
    return default


# ---------------------------------------------------------------------------
# Data on disk
# ---------------------------------------------------------------------------

def _npy_shape(path):
    try:
        import numpy as np
        return list(np.load(path, mmap_mode="r").shape)
    except Exception:                                         # noqa: BLE001
        return None


def _size_mb(path):
    try:
        if os.path.isdir(path):
            total = 0
            for root, _, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return round(total / 2**20, 1)
        return round(os.path.getsize(path) / 2**20, 1)
    except OSError:
        return None


def captures():
    out = []
    for d in sorted(glob.glob(os.path.join(DATA, "*"))):
        if not os.path.isdir(d):
            continue
        rec = {"name": os.path.basename(d), "files": sorted(os.listdir(d))}
        meta = {}
        try:
            with open(os.path.join(d, "meta.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            pass
        intr = meta.get("color_intrinsics") or {}
        rec.update({
            "label": meta.get("label", ""),
            "created": meta.get("created_utc"),
            "device": (meta.get("device") or {}).get("name"),
            "frames": meta.get("frames_captured"),
            "preset": meta.get("visual_preset"),
            "rotation": meta.get("rotation"),
            "size": [intr.get("width"), intr.get("height")],
            "valid_depth": meta.get("depth_valid_fraction"),
            "gravity": meta.get("gravity_cam"),
            "bag": os.path.exists(os.path.join(d, "scan.bag")),
            "bag_mb": _size_mb(os.path.join(d, "scan.bag"))
            if os.path.exists(os.path.join(d, "scan.bag")) else None,
            "control": "CONTROL" in (meta.get("label") or "").upper(),
        })
        depth_shape = _npy_shape(os.path.join(d, "depth_median_mm.npy"))
        seg_path = os.path.join(d, "sapiens_seg.npy")
        if rec["control"]:
            seg = "not needed (rigid control object)"
        elif not os.path.exists(seg_path):
            seg = "missing"
        else:
            shape = _npy_shape(seg_path)
            seg = ("ok" if shape == depth_shape
                   else f"STALE: {shape} does not match depth {depth_shape}")
        rec["segmentation"] = seg
        rec["depth_shape"] = depth_shape
        out.append(rec)
    return out


def weights():
    expected = {}
    for m in catalogue()["modules"]:
        if m["name"] == "sapiens":
            for c in m["constants"]:
                if c["name"].endswith("_CKPT"):
                    try:
                        expected[c["name"]] = ast.literal_eval(c["value"])
                    except Exception:                         # noqa: BLE001
                        pass
    out = []
    for key, fname in sorted(expected.items()):
        path = os.path.join(WEIGHTS, fname)
        out.append({"model": key.replace("_CKPT", "").lower(), "file": fname,
                    "present": os.path.exists(path), "mb": _size_mb(path)
                    if os.path.exists(path) else None,
                    "partial": os.path.exists(path + ".part")})
    return out


def bodies():
    out = []
    now = datetime.datetime.now(datetime.timezone.utc)
    for d in sorted(glob.glob(os.path.join(BODIES, "*"))):
        if not os.path.isdir(d) or d.endswith(".partial"):
            continue
        rec = {"name": os.path.basename(d)}
        try:
            with open(os.path.join(d, "model.json"), encoding="utf-8") as fh:
                m = json.load(fh)
        except (OSError, ValueError) as exc:
            rec["error"] = str(exc)
            out.append(rec)
            continue
        consent = m.get("consent") or {}
        expires = consent.get("delete_after_utc")
        expired = None
        try:
            expired = datetime.datetime.fromisoformat(expires) < now
        except Exception:                                     # noqa: BLE001
            pass
        rec.update({"person": m.get("person_id"), "schema": m.get("schema_version"),
                    "given_by": consent.get("given_by"),
                    "given": consent.get("given_utc"), "delete_after": expires,
                    "expired": expired, "identity": m.get("identity"),
                    "regions": [r.get("name") for r in m.get("regions", [])],
                    "capture": (m.get("provenance") or {}).get("capture"),
                    "git_sha": (m.get("provenance") or {}).get("git_sha")})
        out.append(rec)
    return out


def rig():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}
    return d


# ---------------------------------------------------------------------------
# Git and the environment
# ---------------------------------------------------------------------------

_GIT = {"t": 0.0, "value": None}


def _git(*args):
    try:
        r = subprocess.run(["git", *args], cwd=WORKTREE, capture_output=True,
                           text=True, timeout=10,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return r.stdout.strip()
    except Exception:                                         # noqa: BLE001
        return ""


def git_facts(max_age=10.0):
    if _GIT["value"] is not None and time.time() - _GIT["t"] < max_age:
        return _GIT["value"]
    porcelain = _git("status", "--porcelain")
    value = {
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git("rev-parse", "--short", "HEAD"),
        "dirty": [l for l in porcelain.splitlines() if l.strip()],
        "ahead_of_main": _git("rev-list", "--count", "main..HEAD"),
        "log": [l.split("\t", 2) for l in
                _git("log", "-12", "--format=%h\t%cr\t%s").splitlines()],
        "worktree": WORKTREE,
    }
    _GIT.update(t=time.time(), value=value)
    return value


def environment():
    from importlib import metadata
    pkgs = {}
    for p in KEY_PACKAGES:
        try:
            pkgs[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pkgs[p] = None
    return {"python": sys.version.split()[0], "executable": sys.executable,
            "platform": sys.platform, "packages": pkgs}


# ---------------------------------------------------------------------------
# Drift and gaps: computed, not written
# ---------------------------------------------------------------------------

def _requirements():
    out = []
    try:
        with open(REQS, encoding="utf-8") as fh:
            for line in fh:
                s = line.split("#", 1)[0].strip()
                m = re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*([^\s;]+)", s)
                if m:
                    out.append((m.group(1), m.group(2)))
    except OSError:
        pass
    return out


_DRIFT = {"t": 0.0, "value": None}


def drift(max_age=5.0):
    """Places that are meant to agree. -> [{what, ok, detail}]"""
    if _DRIFT["value"] is not None and time.time() - _DRIFT["t"] < max_age:
        return _DRIFT["value"]
    value = _drift()
    _DRIFT.update(t=time.time(), value=value)
    return value


def _drift():
    from importlib import metadata
    out = []
    cat = catalogue()
    out.append({"what": "every module is listed in scrub3d/__init__.py __all__",
                "ok": not cat["missing_from_all"],
                "detail": ("missing: " + ", ".join(cat["missing_from_all"]))
                if cat["missing_from_all"] else "all listed"})
    th = (rig() or {}).get("thresholds") or {}
    pairs = (("d_hold_mm", "D_HOLD"), ("d_estop_mm", "D_ESTOP"), ("d_body_mm", "D_BODY"))
    bad = []
    for key, const in pairs:
        code = constant("collide", const)
        if key in th and code is not None and float(th[key]) != float(code):
            bad.append(f"{key} {th[key]} in config.json, {const} {code} in collide.py")
    out.append({"what": "config.json thresholds match collide.py",
                "ok": not bad, "detail": "; ".join(bad) if bad else
                ", ".join(f"{c}={constant('collide', c)}" for _, c in pairs)})
    mism = []
    for name, want in _requirements():
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            have = None
        if have is None:
            mism.append(f"{name} not installed (wants {want})")
        elif not (have == want or have.startswith(want + "+") or have.split("+")[0] == want):
            mism.append(f"{name} {have} installed, {want} pinned")
    out.append({"what": "requirements.txt matches what is installed",
                "ok": not mism, "detail": "; ".join(mism) if mism else
                f"{len(_requirements())} pins satisfied"})
    stale = [c["name"] for c in captures()
             if str(c["segmentation"]).startswith(("STALE", "missing"))]
    out.append({"what": "every person capture has a registered segmentation",
                "ok": not stale, "detail": ("needs re-segmenting: " + ", ".join(stale))
                if stale else "all registered"})
    expired = [b["name"] for b in bodies() if b.get("expired")]
    out.append({"what": "no saved body is past its consent date",
                "ok": not expired, "detail": ("delete: " + ", ".join(expired))
                if expired else f"{len(bodies())} saved, none expired"})
    return out


def _tree(name):
    path = os.path.join(SCRUB3D, f"{name}.py")
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=path)


def _names_in(tree):
    s = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            s.add(n.id)
        elif isinstance(n, ast.Attribute):
            s.add(n.attr)
    return s


def _importers(module):
    """Which scrub3d modules import `module`, anywhere in their source."""
    out = []
    for name, path, kind in module_files():
        if name == module:
            continue
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for n in ast.walk(tree):
            if isinstance(n, ast.Import) and any(a.name == module for a in n.names):
                out.append(name)
                break
            if isinstance(n, ast.ImportFrom) and (
                    n.module == module or any(a.name == module for a in n.names)):
                out.append(name)
                break
    return sorted(set(out))


_GAPS = {"key": None, "value": None}


def gaps():
    """What the code demonstrably does not do yet. -> [{id, what, open, detail}]

    `id` is stable, so the Overview's diagram can tie an arrow to a gap and
    draw it solid on its own once the gap closes.

    Parsing every module to answer this takes most of half a second, and the
    answer can only change when a source file does.
    """
    key = _mtime_key()
    if _GAPS["key"] == key and _GAPS["value"] is not None:
        return _GAPS["value"]
    value = _gaps()
    _GAPS.update(key=key, value=value)
    return value


def _gaps():
    out = []
    try:
        names = _names_in(_tree("viz"))
        missing = [n for n in ("FleetGovernor", "GovernedArm", "Consent")
                   if n not in names]
        out.append({"id": "governor-in-loop",
                    "what": "the live loop routes targets through the governor",
                    "open": bool(missing),
                    "detail": ("viz.py never references " + ", ".join(missing))
                    if missing else "viz.py references all three"})
    except Exception as exc:                                  # noqa: BLE001
        out.append({"id": "governor-in-loop",
                    "what": "the live loop routes targets through the governor",
                    "open": None, "detail": repr(exc)})
    try:
        # What stops the run is the handlers, not on_shutdown: each has to
        # hand the main thread an interrupt or a stop request, or Ctrl-C only
        # prints. And the message must not promise a retreat nothing performs.
        tree = _tree("main")
        installer = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                          and n.name == "_install_shutdown"), None)
        how = set()
        for n in ast.walk(installer) if installer is not None else ():
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Name) \
                    and n.exc.id == "KeyboardInterrupt":
                how.add("raise KeyboardInterrupt")
            if isinstance(n, ast.Call):
                attr = getattr(n.func, "attr", "")
                if attr == "interrupt_main":
                    how.add("interrupt the main thread")
                elif attr == "set":
                    how.add("ask a running feed to end at its next frame")
        says = " ".join(str(n.value) for n in ast.walk(tree)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str))
        promises = "retreating arms" in says
        stops = "raise KeyboardInterrupt" in how
        out.append({"id": "stop-on-ctrl-c",
                    "what": "Ctrl-C and console close actually stop main.py",
                    "open": not stops or promises,
                    "detail": ("the stop handlers only print, and the loop keeps "
                               "running" if not stops else
                               "the handlers stop the run, but the message still "
                               "promises a retreat" if promises else
                               "the handlers " + "; ".join(sorted(how)))})
    except Exception as exc:                                  # noqa: BLE001
        out.append({"id": "stop-on-ctrl-c",
                    "what": "Ctrl-C and console close actually stop main.py",
                    "open": None, "detail": repr(exc)})
    for mod, why in (("torque", "contact sensing"), ("handeye", "calibration")):
        users = [u for u in _importers(mod) if u not in ("devconsole",)]
        live = [u for u in users if u in ("viz", "track", "control", "adapt", "fleet",
                                          "armlink")]
        detail = f"imported by: {', '.join(users) or 'nothing'}"
        if mod == "handeye":
            src = open(os.path.join(SCRUB3D, "main.py"), encoding="utf-8").read()
            if "calibrations=" not in src.split("def main(")[-1]:
                detail += "; main.py never passes calibrations to preflight"
        out.append({"id": f"{mod}-live", "what": f"{why} has a live input",
                    "open": not live, "detail": detail})
    return out


if __name__ == "__main__":
    c = catalogue()
    print(json.dumps(c["counts"], indent=1))
    print("missing from __all__:", c["missing_from_all"])
    print("self-tests:", len(selftest_modules()), selftest_modules())
    for d in drift():
        print("drift", "ok " if d["ok"] else "NO ", d["what"], "--", d["detail"])
    for g in gaps():
        print("gap  ", "open" if g["open"] else "shut", g["what"], "--", g["detail"])
    print("weights:", [(w["model"], w["present"]) for w in weights()])
    print("captures:", [(x["name"], x["segmentation"]) for x in captures()])
    print("bodies:", [(b["name"], b.get("expired")) for b in bodies()])
