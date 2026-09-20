"""devconsole/probes.py -- what the console watches, and how it watches it.

    python scrub3d/devconsole/probes.py --check     # every target still exists

`STAGES` IS THE ONE LIST
------------------------
Each stage names the call boundaries that carry its decisions, how to capture
them, what to keep from what they return, and when the stage counts as having
REFUSED rather than failed. The Pipeline tab draws its graph from this list and
the runner installs its wrappers from it, so the picture and the capture cannot
disagree about what exists.

It works because the pipeline already reports on itself: `scan.scan` returns a
report, `shell.build` returns its gates, `propose` returns a Verdict with a
reason. A probe keeps a pruned copy of that. It never recomputes anything.

IMPORTS
-------
Standard library only at module level. The console imports this for the graph
and must never pull in a pipeline module, and the job loads it by file path
under a private name because `armlink` puts `py/` -- with its own `config`,
`vision` and `motion` modules -- at the front of `sys.path`.

THREE WAYS TO SILENTLY CAPTURE NOTHING, AND WHAT STOPS EACH
-----------------------------------------------------------
1. Wrapping a module object the call site never uses. Every scrub3d command
   runs as `python scrub3d/<script>.py`, so modules are imported under their
   BARE names (`sys.modules["scan"]`), never as `scrub3d.scan`. The import hook
   here only ever sees bare names.
2. Wrapping after a name was copied. `from anatomy import anatomical_body`
   copies the function into `viz` at import time. The hook wraps each module
   the moment it finishes executing -- before anything can import from it -- so
   the copy is already the wrapper.
3. Wrapping the script's own functions. `main()` finds `preflight` in
   `__main__`'s globals, which no import hook can reach. The runner executes
   the script's body itself and hands that namespace to `install()` before the
   `__main__` block runs.
"""
import ast
import collections
import functools
import inspect
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRUB3D = os.path.dirname(HERE)
WORKTREE = os.path.dirname(SCRUB3D)
PY_DIR = os.path.join(WORKTREE, "py")

GROUPS = ("Sense", "Shape", "Plan", "Follow", "Decide", "Act", "Tools")

# Rerun entity paths whose Scalars are recorded as series. Everything else is
# only counted: meshes and point clouds are the operator view's business.
SCALAR_PREFIXES = ("track/", "reach/", "coverage/", "collision/")

SUMMARY_HZ = 2.0          # full summaries for per-frame stages, at most
AGG_EVERY_S = 1.0         # aggregates and entity counts
STOP_GRACE_S = 3.0        # cooperative stop, then an async exception
FRAME_GRAB_S = 0.5        # colour and depth snapshots


class DevConsoleStop(BaseException):
    """Raised by a probe once Stop was pressed.

    A BaseException on purpose: `plan_rig`, `check_camera_day` and `viz.live`
    all have broad `except Exception` blocks that would otherwise swallow the
    stop and carry on.
    """


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

class T:
    """One wrapped call boundary."""
    __slots__ = ("module", "attr", "mode", "keep", "refused", "rate",
                 "silent_inside", "state", "stop_blocks", "stop_safe", "stage",
                 "key")

    def __init__(self, module, attr, mode="call", keep=None, refused=None,
                 rate=None, silent_inside=(), state=None, stop_blocks=False,
                 stop_safe=False):
        self.module = module
        self.attr = attr
        self.mode = mode                  # once call frame change gen mirror guard
        self.keep = keep                  # (args, kwargs, result, rt) -> dict
        self.refused = refused            # (summary, result, exc) -> bool
        self.rate = rate                  # max spans per second, or None
        self.silent_inside = tuple(silent_inside)
        self.state = state                # (args, kwargs, result) -> hashable
        self.stop_blocks = stop_blocks    # refuse to run at all once stopped
        # Never interrupted by Stop: calls that make things safer. A stop that
        # blocked an estop, or the consent revoke a script ends with, would be
        # the console making a run less safe than it was without it.
        self.stop_safe = stop_safe
        self.stage = None
        self.key = f"{module}.{attr}"


class Stage:
    __slots__ = ("id", "group", "label", "what", "targets")

    def __init__(self, sid, group, label, what, targets):
        assert group in GROUPS, group
        self.id, self.group, self.label, self.what = sid, group, label, what
        self.targets = targets
        for t in targets:
            t.stage = sid


# --- small helpers the keep functions share ---------------------------------

def _arg(args, kwargs, i, name, default=None):
    if name in kwargs:
        return kwargs[name]
    return args[i] if len(args) > i else default


def _r(x, nd=1):
    try:
        return round(float(x), nd)
    except Exception:                                         # noqa: BLE001
        return None


def _gates(g):
    try:
        return [[str(n), bool(ok), str(d)] for n, ok, d in g]
    except Exception:                                         # noqa: BLE001
        return []


def _any_gate_failed(s, *_):
    return any(not ok for _, ok, _ in (s or {}).get("gates", []))


def _pick(d, keys):
    return {k: d[k] for k in keys if isinstance(d, dict) and k in d}


class STATE_PER(tuple):
    """A state value that belongs to one instance of a shared target."""

    def __new__(cls, instance, value):
        return super().__new__(cls, (instance, value))


# --- keep functions ------------------------------------------------------------

def k_feed_start(a, kw, res, rt):
    self = a[0]
    m = getattr(self, "meta", {}) or {}
    intr = m.get("color_intrinsics") or {}
    return {"source": m.get("source"), "rotation": m.get("rotation"),
            "size": [intr.get("width"), intr.get("height")],
            "fx": _r(intr.get("fx")), "preset": getattr(self, "preset", None)}


def k_record(a, kw, res, rt):
    m = res or {}
    return _pick(m, ("label", "frames_captured", "median_frames",
                     "depth_valid_fraction", "median_valid_fraction",
                     "visual_preset", "rotation", "gravity_cam", "bag_mb",
                     "device"))


def k_frames_solve(a, kw, res, rt):
    f = (res or {}).get("floor") or {}
    return {"capture": os.path.basename(str(_arg(a, kw, 0, "capture_dir", ""))),
            "floor_rms_mm": _r(f.get("rms_mm"), 2),
            "camera_height_mm": _r(f.get("camera_height_mm")),
            "pitch_down_deg": _r(f.get("pitch_down_deg"), 2),
            "roll_deg": _r(f.get("roll_deg"), 2),
            "floor_points": f.get("n_points"),
            "subject": (res or {}).get("subject_world")}


def _gpu_note(rt, before):
    if before is None:
        return {}
    after = rt.gpu_mem()
    return {} if after is None else {"gpu_mb_delta": _r((after - before) / 2**20)}


def k_model_init(a, kw, res, rt):
    self = a[0]
    return {"model": type(self).__name__, "device": str(getattr(self, "device", "")),
            "dtype": str(getattr(self, "dtype", ""))}


def k_seg_call(a, kw, res, rt):
    out = {"model": type(a[0]).__name__}
    try:
        out["shape"] = list(res.shape)
        if type(a[0]).__name__ == "SapiensSeg":
            import numpy as np
            out["classes"] = int(len(np.unique(res)))
            rt.writer.image("seg", res[::2, ::2].copy(), "seg")
    except Exception:                                         # noqa: BLE001
        pass
    return out


def k_sleeves(a, kw, res, rt):
    ok, info = res
    return {"ok": bool(ok), **_pick(info, ("limb_fraction", "message",
                                           "limb_px", "clothing_px"))}


def k_landmarks(a, kw, res, rt):
    lms, detail = res
    return {"found": len(lms or {}),
            "below_threshold": (detail or {}).get("below_threshold"),
            "why": (detail or {}).get("why")}


def k_carve(a, kw, res, rt):
    out = {}
    try:
        import numpy as np
        masks = res or {}
        out = {k: int(v.sum()) for k, v in masks.items()}
        if masks:
            first = next(iter(masks.values()))
            img = np.zeros(first.shape + (3,), np.uint8)
            img[:] = (24, 24, 28)
            palette = [(74, 92, 232), (74, 160, 232), (122, 199, 108),
                       (74, 176, 232), (150, 150, 160)]
            for i, m in enumerate(masks.values()):
                img[m] = palette[i % len(palette)]
            rt.writer.image("carve", img[::2, ::2].copy(), "mask")
    except Exception:                                         # noqa: BLE001
        pass
    return {"px": out}


def k_scan(a, kw, res, rt):
    body, _meshes, obstacles, rep = res
    parts = {}
    for name, p in (rep.get("parts") or {}).items():
        parts[name] = _pick(p, ("ok", "why", "length_mm", "nominal_len_mm",
                                "stations", "refused", "bone_from", "snapped",
                                "snap_frac", "snap_median_mm", "observed_frac",
                                "circumference_mm", "scrubbable", "partial"))
    hist = {}
    try:
        import numpy as np
        reg = np.asarray(rep.get("obstacle_region"))
        vals, counts = np.unique(reg, return_counts=True)
        names = [r.name for r in body.regions]
        for v, c in zip(vals.tolist(), counts.tolist()):
            hist[names[v] if 0 <= v < len(names) else "unlabelled"] = c
    except Exception:                                         # noqa: BLE001
        pass
    rt.remember("scan", {"regions": [r.name for r in body.regions]})
    return {"capture": os.path.basename(str(rep.get("capture", ""))),
            "scrub_trunk": _arg(a, kw, 6, "scrub_trunk"),
            "regions": [r.name for r in body.regions],
            "parts": parts, "gates": _gates(rep.get("gates", [])),
            "clothing": rep.get("clothing"),
            "shoulder_z_mm": _r(rep.get("shoulder_z_mm")),
            "scrubbable_cm2": _r(rep.get("scrubbable_cm2")),
            "normals_from": rep.get("normals_from"),
            "obstacle_points": int(len(obstacles)),
            "obstacles_by_region": hist}


def k_measure_part(a, kw, res, rt):
    return _pick(res or {}, ("n_stations", "refused", "straightness",
                             "bone_from", "length", "station_span_mm"))


def k_triple(names):
    def keep(a, kw, res, rt):
        try:
            return {n: (_r(v, 3) if isinstance(v, float) else v)
                    for n, v in zip(names, res)}
        except Exception:                                     # noqa: BLE001
            return {"result": res}
    return keep


def k_shell_build(a, kw, res, rt):
    rt.shell_faces = res.get("faces")
    rt.shell_colours = res.get("colours")
    return {"capture": (res.get("source") or {}).get("capture"),
            "verts": int(len(res.get("verts", []))),
            "faces": int(len(res.get("faces", []))),
            "max_tris": _arg(a, kw, 7, "max_tris"),
            "source": res.get("source"), "gates": _gates(res.get("gates", []))}


def r_shell_build(s, res, exc):
    if exc is not None:
        return "does not look like a seated person" in str(exc)
    return _any_gate_failed(s)


def k_fill(a, kw, res, rt):
    return {"stats": res[2]}


def r_fill(s, res, exc):
    st = (s or {}).get("stats") or {}
    return bool(st) and not st.get("filled")


def k_verify(a, kw, res, rt):
    return {"gates": _gates(res)}


def k_render_still(a, kw, res, rt):
    rt.writer.still(res)
    return {"path": res}


def k_bind(a, kw, res, rt):
    return {"verts": int(len(res.get("idx", []))), "regions": res.get("n_regions")}


def k_cell_map(a, kw, res, rt):
    try:
        gid = res["gid"]
        return {"verts": int(len(gid)), "mapped_frac": _r((gid >= 0).mean(), 3)}
    except Exception:                                         # noqa: BLE001
        return {}


def k_shell_pose(a, kw, res, rt):
    rt.shell_verts = res
    return {"verts": int(len(res))}


def k_bodystore_save(a, kw, res, rt):
    return {"path": res, "person": _arg(a, kw, 2, "person_id")}


def k_bodystore_load(a, kw, res, rt):
    body, meta = res
    consent = meta.get("consent") or {}
    return {"person": meta.get("person_id"), "regions": len(body.regions),
            "consent": consent, "identity": meta.get("identity")}


def k_identity_ok(a, kw, res, rt):
    verdict, rows = res
    return {"verdict": verdict,
            "rows": [[k, _r(s), _r(l), _r(rel, 4), ok] for k, s, l, rel, ok in rows]}


def r_identity_ok(s, res, exc):
    return exc is None and (s or {}).get("verdict") != "LOAD"


def k_rig_load(a, kw, res, rt):
    arms = []
    try:
        for arm in res.arms:
            arms.append(_pick(arm, ("id", "x_mm", "y_mm", "z_mm", "facing_deg")))
        name = res.d.get("name") if hasattr(res, "d") else None
    except Exception:                                         # noqa: BLE001
        name = None
    return {"name": name, "arms": arms}


def k_rig_save(a, kw, res, rt):
    d = _arg(a, kw, 0, "rig_dict", {}) or {}
    return {"path": res, "name": d.get("name"), "WROTE_THE_RIG": True}


def k_partition(a, kw, res, rt):
    terr, planes, phases, rep = res
    rt.last_layout = _arg(a, kw, 1, "base_poses")
    return {"envelope_k": _arg(a, kw, 2, "envelope_k", 8),
            "min_feas": _arg(a, kw, 3, "min_feas", 0.6),
            "obstacle_points": (None if _arg(a, kw, 4, "obstacle_points") is None
                                else len(_arg(a, kw, 4, "obstacle_points"))),
            "per_limb_exemption": _arg(a, kw, 5, "obstacle_region") is not None,
            "cells": rep.get("cells"),
            "covered_frac": _r(rep.get("covered_frac"), 4),
            "per_arm_cm2": [_r(v) for v in rep.get("per_arm_cm2", [])],
            "classes": rep.get("classes"), "phases": phases,
            "separable_pairs": sum(1 for v in (planes or {}).values() if v is not None),
            "pairs": len(planes or {})}


def r_partition(s, res, exc):
    return exc is None and not (s or {}).get("covered_frac")


def k_score(a, kw, res, rt):
    s, rep = res
    return {"score": _r(s, 4), "covered_frac": _r(rep.get("covered_frac"), 4),
            "imbalance": _r(rep.get("imbalance"), 3),
            "camera_occluded": _r(rep.get("camera_occluded"), 3),
            "per_arm_cm2": [_r(v) for v in rep.get("per_arm_cm2", [])],
            "phases": rep.get("phases"), "reject": rep.get("reject"),
            "envelope_k": _arg(a, kw, 2, "envelope_k")}


def k_layout_result(a, kw, res, rt):
    try:
        layout, rep = res
    except Exception:                                         # noqa: BLE001
        return {"result": res}
    mounts = []
    try:
        import math
        for T_ in layout:
            mounts.append([_r(T_[0][3]), _r(T_[1][3]), _r(T_[2][3]),
                           _r(math.degrees(math.atan2(T_[1][0], T_[0][0])))])
    except Exception:                                         # noqa: BLE001
        pass
    return {"mounts": mounts, "report": _pick(rep or {}, (
        "covered_frac", "imbalance", "per_arm_cm2", "phases", "score",
        "camera_occluded"))}


def k_preflight(a, kw, res, rt):
    return {"checks": [[str(n), str(st), str(d)] for n, st, d in res]}


def r_preflight(s, res, exc):
    return any(st == "FAIL" for _, st, _ in (s or {}).get("checks", []))


def k_report(a, kw, res, rt):
    return {"may_move": bool(res)}


def k_record_and_segment(a, kw, res, rt):
    return {"capture": res}


def k_tracker_init(a, kw, res, rt):
    self = a[0]
    return {"regions": sorted(getattr(self, "rest", {}) or {})}


def k_track_update(a, kw, res, rt):
    posed, info = res
    info = info or {}
    return {"ok": info.get("ok"), "why": info.get("why"),
            "regions": info.get("regions"), "missing": info.get("missing"),
            "held": info.get("held"), "lost": info.get("lost"),
            "jump_mm": _r(info.get("jump_mm")), "freeze": info.get("freeze"),
            "seat_mm": _r(info.get("seat_mm")), "away": info.get("away")}


def s_track_update(a, kw, res):
    info = res[1] or {}
    return (bool(info.get("ok")), bool(info.get("freeze")), info.get("why"),
            bool(info.get("away")), tuple(info.get("lost") or ()))


def r_track_update(s, res, exc):
    s = s or {}
    return exc is None and (not s.get("ok") or bool(s.get("freeze"))
                            or bool(s.get("away")))


def k_adapt_check(a, kw, res, rt):
    return {"t": _r(res.get("t"), 2), "arms": res.get("arms"),
            "handoffs": res.get("handoffs"), "stop": res.get("stop")}


def k_adapt_commit(a, kw, res, rt):
    return {"handoff": _arg(a, kw, 1, "h"), "count": res}


def k_may_hand_over(a, kw, res, rt):
    return {"in_contact": _arg(a, kw, 0, "in_contact"),
            "at_segment_boundary": _arg(a, kw, 1, "at_segment_boundary"),
            "final_approach": _arg(a, kw, 2, "final_approach", False),
            "allowed": bool(res)}


def _ctl_arm(rt, self):
    return rt.ctl_arm.get(id(self))


def k_ctl_init(a, kw, res, rt):
    self = a[0]
    arm = None
    try:
        import numpy as np
        T_ = np.asarray(_arg(a, kw, 4, "T_world_arm"), float)
        for i, L in enumerate(rt.last_layout or []):
            if np.allclose(np.asarray(L, float), T_):
                arm = i
                break
    except Exception:                                         # noqa: BLE001
        pass
    rt.ctl_arm[id(self)] = arm
    return {"arm": arm, "cells": int(len(getattr(self, "pts", []))),
            "reachable": int(self.reachable_mask().sum())
            if hasattr(self, "reachable_mask") else None}


def k_ctl_refresh(a, kw, res, rt):
    return {"arm": _ctl_arm(rt, a[0])}


def k_ctl_tick(a, kw, res, rt):
    self = a[0]
    out = {"arm": _ctl_arm(rt, self), "done": res is None}
    try:
        out["traverse"] = bool(res[2]) if res is not None else None
        out["visited"] = int(self.visited.sum())
        out["cells"] = int(len(self.visited))
    except Exception:                                         # noqa: BLE001
        pass
    return out


def s_ctl_tick(a, kw, res):
    # Keyed by controller: four arms share this target, and comparing one
    # arm's state against another's made every call look like a change.
    return STATE_PER(id(a[0]), res is None)


def k_live(a, kw, res, rt):
    rt.render_painted(final=True)
    return res


def k_pose_changed(a, kw, res, rt):
    return {"moved": bool(res[0])}


def s_pose_changed(a, kw, res):
    return bool(res[0])


def k_visited(a, kw, res, rt):
    try:
        return {"visited": int(res.sum()), "cells": int(len(res))}
    except Exception:                                         # noqa: BLE001
        return {}


def k_paint(a, kw, res, rt):
    rt.shell_paint = res
    rt.render_painted()
    return {"verts": int(len(res))}


def k_layout_len(a, kw, res, rt):
    return {"arms": len(res or [])}


def k_gov_init(a, kw, res, rt):
    self = a[0]
    return {"arms": getattr(self, "n", None), "z_ceiling_mm": _r(getattr(self, "z_ceiling", None)),
            "body_points": (None if getattr(self, "body", None) is None
                            else len(self.body))}


def _verdict(v):
    return {"action": getattr(v, "action", None), "reason": getattr(v, "reason", ""),
            "detail": getattr(v, "detail", {}),
            "target": getattr(v, "target", None)}


def k_propose(a, kw, res, rt):
    return {"arm": _arg(a, kw, 1, "arm"),
            "xyz": [_r(_arg(a, kw, 2, "x")), _r(_arg(a, kw, 3, "y")), _r(_arg(a, kw, 4, "z"))],
            "j3": _arg(a, kw, 7, "j3"), **_verdict(res),
            "estopped": getattr(a[0], "estopped", None),
            "degraded": sorted(getattr(a[0], "degraded", []) or [])}


def k_hold(a, kw, res, rt):
    return {"arm": _arg(a, kw, 1, "arm"), "held_since": _r(_arg(a, kw, 2, "t"), 2),
            "separation_mm": _r(_arg(a, kw, 4, "sep")), **_verdict(res)}


def r_verdict(s, res, exc):
    return exc is None and (s or {}).get("action") not in (None, "clear")


def k_gov_state(a, kw, res, rt):
    self = a[0]
    return {"args": [x for x in a[1:]], "result": res,
            "estopped": getattr(self, "estopped", None),
            "degraded": sorted(getattr(self, "degraded", []) or [])}


def k_home_order(a, kw, res, rt):
    order, detail = res
    return {"order": order, "detail": detail}


def r_home_order(s, res, exc):
    return exc is None and not (s or {}).get("order")


def k_consent(a, kw, res, rt):
    self = a[0]
    return {"state": getattr(self, "_state", None), "result": res,
            "remaining_s": _r(self.remaining_s()) if hasattr(self, "remaining_s") else None}


def s_consent(a, kw, res):
    return str(res)


def k_torque(a, kw, res, rt):
    self = a[0]
    return _pick(vars(self), ("rms", "gain", "rms_n", "cs", "ce"))


def k_contact(a, kw, res, rt):
    return _pick(res or {}, ("force_n", "contact", "scrub_energy", "scrub_ratio",
                             "scrubbing"))


def s_contact(a, kw, res):
    return (bool((res or {}).get("contact")), bool((res or {}).get("scrubbing")))


def k_handeye_solve(a, kw, res, rt):
    return _pick(res or {}, ("rms_mm", "conditioning", "n", "verdict", "why"))


def r_handeye(s, res, exc):
    return exc is None and (s or {}).get("verdict") == "no-go"


def k_pair(names):
    return k_triple(names)


def r_second_false(s, res, exc):
    try:
        return exc is None and not res[1]
    except Exception:                                         # noqa: BLE001
        return False


def r_first_false(s, res, exc):
    try:
        return exc is None and not res[0]
    except Exception:                                         # noqa: BLE001
        return False


def k_escape(a, kw, res, rt):
    return {"worst_mm": _r(res[0], 2), "link": res[1]}


def r_escape(s, res, exc):
    return exc is None and res[0] > 0


def k_armlink_init(a, kw, res, rt):
    self = a[0]
    return {"arm_id": getattr(self, "arm_id", None),
            "dry": getattr(getattr(self, "arm", None), "dry", None)}


def k_armlink_set(a, kw, res, rt):
    self = a[0]
    return {"arm_id": getattr(self, "arm_id", None), "result": bool(res),
            "xyz": [_r(_arg(a, kw, 1, "x")), _r(_arg(a, kw, 2, "y")), _r(_arg(a, kw, 3, "z"))],
            **{f"verdict_{k}": v for k, v in _verdict(getattr(self, "last_verdict", None)).items()},
            "refusals": dict(getattr(self, "refusals", {}) or {})}


def r_false(s, res, exc):
    return exc is None and not res


def k_result(a, kw, res, rt):
    return {"result": res}


def k_arm_init(a, kw, res, rt):
    self = a[0]
    return {"dry": getattr(self, "dry", None),
            "port": _arg(a, kw, 1, "port")}


def k_arm_set(a, kw, res, rt):
    return {"xyz": [_r(_arg(a, kw, 1, "x")), _r(_arg(a, kw, 2, "y")), _r(_arg(a, kw, 3, "z"))],
            "t": _r(_arg(a, kw, 4, "t", 3.14), 3), "result": res}


def k_anatomy(a, kw, res, rt):
    body, meshes = res
    return {"regions": [r.name for r in body.regions],
            "scrubbable": [r.name for r in body.regions if r.scrubbable.any()]}


def k_skin(a, kw, res, rt):
    V, F = res
    return {"verts": int(len(V)), "faces": int(len(F))}


def k_posture(a, kw, res, rt):
    return {"postures": [_pick(p, ("posture", "covered_frac", "gain_points"))
                         for p in (res or [])]}


def k_check_lines(a, kw, res, rt):
    return {"lines": res}


def k_segment_check(a, kw, res, rt):
    ok, det = res
    return {"capture": os.path.basename(str(_arg(a, kw, 0, "cap_dir", ""))),
            "ok": bool(ok), "detail": det}


def k_shutdown(a, kw, res, rt):
    return {"handler": res}


# ---------------------------------------------------------------------------
# STAGES
# ---------------------------------------------------------------------------

STAGES = [
    # -- Sense ---------------------------------------------------------------
    Stage("feed", "Sense", "Feed",
          "Camera or bag playback: aligned, rotated colour and depth frames.",
          [T("rsfeed", "Feed.start", "once", keep=k_feed_start),
           T("rsfeed", "Feed.frames", "gen")]),
    Stage("capture", "Sense", "Capture",
          "Records a still subject: median depth, colour, IMU gravity, bag.",
          [T("record", "record", "once", keep=k_record)]),
    Stage("world", "Sense", "World frame",
          "Fits the floor plane and derives T_world_camera from it.",
          [T("frames", "solve", keep=k_frames_solve)]),
    Stage("segmentation", "Sense", "Segmentation",
          "Sapiens body-part segmentation: the person's outline against the room.",
          [T("sapiens", "SapiensSeg.__init__", "once", keep=k_model_init),
           T("sapiens", "SapiensSeg.__call__", keep=k_seg_call)]),
    Stage("normals", "Sense", "Predicted normals",
          "Sapiens surface normals from colour, for snapping and reconstruction.",
          [T("sapiens", "SapiensNormal.__init__", "once", keep=k_model_init),
           T("sapiens", "SapiensNormal.__call__", keep=k_seg_call)]),
    Stage("depthnet", "Sense", "Predicted depth",
          "Sapiens depth from colour, used only to fill stereo dropouts.",
          [T("sapiens", "SapiensDepth.__init__", "once", keep=k_model_init),
           T("sapiens", "SapiensDepth.__call__", keep=k_seg_call)]),
    Stage("landmarks", "Sense", "Landmarks",
          "MediaPipe pose on the median frame: shoulders, elbows, wrists, hips.",
          [T("pose", "landmarks", keep=k_landmarks,
             refused=lambda s, r, e: e is None and not (s or {}).get("found"))]),
    Stage("carve", "Sense", "Limb carve",
          "Splits the outline into limbs by nearest bone, trunk included.",
          [T("pose", "carve", keep=k_carve)]),
    Stage("sleeves", "Sense", "Sleeve guard",
          "Refuses to call a sleeve an arm.",
          [T("sapiens", "check_sleeves", keep=k_sleeves,
             refused=lambda s, r, e: e is None and not (s or {}).get("ok"))]),

    # -- Shape ---------------------------------------------------------------
    Stage("scan", "Shape", "Scan",
          "A capture becomes a BodyModel whose cells sit on the measured surface.",
          [T("scan", "scan", "once", keep=k_scan, refused=_any_gate_failed),
           T("scan", "scan_gates", keep=k_verify, refused=_any_gate_failed),
           T("scan", "measure_part", keep=k_measure_part),
           T("scan", "snap_to_measured",
             keep=k_triple(("snapped", "cells", "median_shift_mm"))),
           T("scan", "mark_unobserved",
             keep=k_triple(("kept", "total", "median_observed")))]),
    Stage("reconstruction", "Shape", "Reconstruction",
          "The person as the camera saw them: Poisson over real depth and AI normals.",
          [T("shell", "build", "once", keep=k_shell_build, refused=r_shell_build),
           T("shell", "fill_dropouts", keep=k_fill, refused=r_fill),
           T("shell", "keep_subject", keep=lambda a, kw, r, rt: {"dropped": r[1]}),
           T("shell", "_normals_from_sapiens",
             keep=lambda a, kw, r, rt: {"note": r[1]}),
           T("shell", "drop_fattened_edges",
             keep=lambda a, kw, r, rt: {"edge_px_dropped": r[1]}),
           T("shell", "verify", keep=k_verify, refused=_any_gate_failed),
           T("shell", "render_still", keep=k_render_still)]),
    Stage("skinning", "Shape", "Skinning",
          "Binds the reconstruction to the scanned bones so it follows the person.",
          [T("shell", "bind", "once", keep=k_bind),
           T("shell", "cell_map", keep=k_cell_map),
           T("shell", "pose", "frame", keep=k_shell_pose)]),
    Stage("modelled", "Shape", "Modelled body",
          "The procedural body and its fused skin, for when there is no capture.",
          [T("anatomy", "anatomical_body", keep=k_anatomy),
           T("skin", "build", keep=k_skin)]),
    Stage("bodystore", "Shape", "Body store",
          "Saves a scan and refuses to reload it onto somebody else.",
          [T("bodystore", "save", keep=k_bodystore_save),
           T("bodystore", "load", keep=k_bodystore_load),
           T("bodystore", "identity_ok", keep=k_identity_ok, refused=r_identity_ok),
           T("bodystore", "identity_from_capture", keep=k_result),
           T("bodystore", "identity_from_frames",
             keep=lambda a, kw, r, rt: {"identity": r[0], "frames_used": r[1]})]),

    # -- Plan ----------------------------------------------------------------
    Stage("rig", "Plan", "Rig",
          "Where the four arms are bolted, read from config.json.",
          [T("rigconfig", "load", keep=k_rig_load),
           T("rigconfig", "save", keep=k_rig_save, stop_blocks=True),
           T("viz", "default_layout", keep=k_layout_len)]),
    Stage("territories", "Plan", "Territories",
          "Carves the body into one contiguous territory per arm, across a pose envelope.",
          [T("partition", "solve", keep=k_partition, refused=r_partition,
             silent_inside=("place_arms.score",))]),
    Stage("placement", "Plan", "Placement search",
          "Scores candidate rigs and searches for the best mounts.",
          [T("place_arms", "score", keep=k_score, rate=50,
             refused=lambda s, r, e: e is None and (s or {}).get("score", 0) < 0),
           T("place_arms", "search", "once", keep=k_layout_result),
           T("place_arms", "optimise", "once", keep=k_layout_result)]),
    Stage("posture", "Plan", "Posture advice",
          "Ranks postures by the coverage asking for them would buy.",
          [T("adapt", "suggest_posture", keep=k_posture)]),
    Stage("preflight", "Plan", "Pre-flight",
          "Eight checks; anything not checkable is UNKNOWN, never PASS.",
          [T("main", "preflight", keep=k_preflight, refused=r_preflight),
           T("main", "report", keep=k_report),
           T("main", "_perturbed_collision_test",
             keep=k_triple(("worst_mm", "bad", "tested"))),
           T("main", "_ceiling_holds", keep=k_result),
           T("main", "_can_reach_above", keep=k_result),
           T("main", "_install_shutdown", keep=k_shutdown),
           T("main", "_record_and_segment", keep=k_record_and_segment,
             refused=lambda s, r, e: e is None and r is None)]),

    # -- Follow --------------------------------------------------------------
    Stage("tracking", "Follow", "Tracking",
          "MediaPipe pose every frame, retargeted onto the scanned bone lengths.",
          [T("track", "Tracker.__init__", "once", keep=k_tracker_init),
           T("track", "Tracker.update", "frame", keep=k_track_update,
             state=s_track_update, refused=r_track_update)]),
    Stage("reachability", "Follow", "Reachability",
          "About once a second: can each arm still reach what it has left?",
          [T("adapt", "Adapter.check", keep=k_adapt_check,
             refused=lambda s, r, e: e is None and bool((s or {}).get("stop"))),
           T("adapt", "Adapter.commit", keep=k_adapt_commit),
           T("adapt", "may_hand_over", keep=k_may_hand_over)]),
    Stage("coverage", "Follow", "Coverage control",
          "A field re-read every frame; the sweep is not a stored path.",
          [T("control", "CoverageController.__init__", keep=k_ctl_init),
           T("control", "CoverageController.refresh", "frame", keep=k_ctl_refresh),
           T("control", "CoverageController.tick", "frame", keep=k_ctl_tick,
             state=s_ctl_tick),
           T("control", "CoverageController.run", "once", keep=k_result)]),
    Stage("loop", "Follow", "Live loop",
          "viz.live: scan once, then follow, paint and log every frame.",
          [T("viz", "live", "once", keep=k_live),
           T("viz", "build", "once", keep=k_result),
           T("viz", "preview", "once", keep=k_result),
           T("viz", "_pose_changed", "frame", keep=k_pose_changed,
             state=s_pose_changed),
           T("viz", "_visited_global", "frame", keep=k_visited),
           T("viz", "_paint", "frame", keep=k_paint)]),

    # -- Decide --------------------------------------------------------------
    Stage("governor", "Decide", "Governor",
          "The only route to an arm: half-space, limits, capsules, body, head ceiling.",
          [T("fleet", "FleetGovernor.__init__", keep=k_gov_init),
           T("fleet", "FleetGovernor.propose", keep=k_propose, refused=r_verdict,
             rate=200),
           T("fleet", "FleetGovernor._hold", keep=k_hold, refused=r_verdict,
             silent_inside=("fleet.FleetGovernor.propose",)),
           T("fleet", "FleetGovernor.estop", keep=k_gov_state, stop_safe=True),
           T("fleet", "FleetGovernor.clear_estop", keep=k_gov_state),
           T("fleet", "FleetGovernor.link_lost", keep=k_gov_state),
           T("fleet", "FleetGovernor.link_restored", keep=k_gov_state),
           T("fleet", "FleetGovernor.home_order", keep=k_home_order,
             refused=r_home_order),
           T("fleet", "FleetGovernor.rotate_priority", keep=k_gov_state)]),
    Stage("consent", "Decide", "Consent",
          "One latch for the fleet, two clocks.",
          [T("session", "Consent.__init__", keep=k_consent),
           T("session", "Consent.press", keep=k_consent),
           T("session", "Consent.start_motion", keep=k_consent,
             refused=lambda s, r, e: e is None and not r[0]),
           T("session", "Consent.finish", keep=k_consent, stop_safe=True),
           T("session", "Consent.revoke", keep=k_consent, stop_safe=True),
           T("session", "Consent.state", "change", keep=k_consent, state=s_consent,
             refused=lambda s, r, e: e is None and r[0] == "expired"),
           T("session", "Consent.may_move", "change", keep=k_consent,
             state=s_consent)]),
    Stage("contact", "Decide", "Contact sensing",
          "Pose-independent contact in newtons; scrubbing versus leaning.",
          [T("torque", "GravityModel.fit", keep=k_torque),
           T("torque", "ForceScale.fit", keep=k_torque),
           T("torque", "ContactSense.update", "frame", keep=k_contact,
             state=s_contact)]),
    Stage("calibration", "Decide", "Calibration",
          "Hand-eye by point sets, refused when it cannot see its own error.",
          [T("handeye", "solve", keep=k_handeye_solve, refused=r_handeye),
           T("handeye", "verify", keep=k_triple(("error_mm", "ok")),
             refused=r_second_false),
           T("handeye", "cross_validate", keep=k_triple(("spread_mm", "ok")),
             refused=r_second_false)]),
    Stage("capsules", "Decide", "Capsule fit",
          "Every STL vertex must sit inside the capsules that stand in for it.",
          [T("collide", "mesh_escapes_capsules", keep=k_escape, refused=r_escape,
             rate=100)]),

    # -- Act -----------------------------------------------------------------
    Stage("armlink", "Act", "Arm link",
          "The only bridge to py/arm.py; refuses what BOX would silently move.",
          [T("armlink", "GovernedArm.__init__", keep=k_armlink_init),
           T("armlink", "GovernedArm.set_target", keep=k_armlink_set,
             refused=r_false, rate=200),
           T("armlink", "GovernedArm.health_tick", keep=k_result,
             refused=lambda s, r, e: e is None and (r or {}).get("link") != "ok"),
           T("armlink", "GovernedArm.go_home", keep=k_result, refused=r_false)]),
    Stage("driver", "Act", "Arm driver",
          "py/arm.py itself, observed and never edited.",
          [T("arm", "Arm.__init__", keep=k_arm_init),
           T("arm", "Arm.set_target", keep=k_arm_set, rate=200),
           T("arm", "Arm.go_home", keep=k_result),
           T("arm", "Arm.estop", keep=k_result, stop_safe=True),
           T("arm", "Arm.apply_torque_caps", keep=k_result)]),
    Stage("hardware", "Act", "Hardware guard",
          "Opening a serial port inside a console job raises.",
          [T("serial", "Serial.__init__", "guard")]),
    Stage("viewer", "Act", "Operator view",
          "Rerun: the scalars the loop logs, and a count of everything else.",
          [T("rerun", "log", "mirror"), T("rerun", "set_time", "mirror"),
           T("rerun", "init", "mirror"), T("rerun", "save", "mirror"),
           T("rerun", "spawn", "mirror")]),

    # -- Tools ---------------------------------------------------------------
    Stage("cameraday", "Tools", "Camera-day check",
          "Three of the four runbook steps, with only the RealSense call stubbed.",
          [T("check_camera_day", "check_record_path", keep=k_check_lines),
           T("check_camera_day", "check_reconstruction", keep=k_check_lines),
           T("check_camera_day", "check_viewer", keep=k_check_lines)]),
    Stage("segcache", "Tools", "Segmentation caches",
          "Re-segments captures whose cached mask is missing or stale.",
          [T("segment_captures", "check", keep=k_segment_check,
             refused=lambda s, r, e: e is None and not (s or {}).get("ok"))]),
    Stage("search", "Tools", "Rig search",
          "tools/plan_rig.py, followed through its own output lines.",
          []),
]

# plan_rig's progress is its stdout. These are what the console parses, and
# `--check` confirms each literal still appears in the source that prints it.
PLAN_RIG_PATTERNS = {
    "sampled": (r"sampled (\d+)/(\d+)\s+(?:kept (\d+)\s+best score ([+-][\d.]+) "
                r"coverage ([\d.]+)%|nothing viable yet)",
                ("place_arms.py", "sampled ")),
    "rerank": (r"re-ranking the top (\d+)", ("place_arms.py", "re-ranking the top")),
    "candidate": (r"refining candidate (\d+):", ("place_arms.py", "refining candidate")),
    "start": (r"^\s+start: score ([+-][\d.]+)\s+coverage ([\d.]+)%\s+imbalance "
              r"([\d.]+)\s+phases (\d+)", ("place_arms.py", "start: score")),
    "sweep": (r"sweep (\d+) arm (\d+): score ([+-][\d.]+)\s+coverage ([\d.]+)%\s+"
              r"imbalance ([\d.]+)\s+phases (\d+)", ("place_arms.py", "sweep ")),
    "report": (r"^\s{4}(.{22}) coverage +([\d.]+)%\s+imbalance ([\d.]+)\s+phases "
               r"(\d+)\s+camera-blocked +([\d.]+)%\s+per-arm cm2 \[(.*)\]",
               ("tools/plan_rig.py", "camera-blocked")),
    "worth": (r"the search is worth ([+-][\d.]+) points", ("tools/plan_rig.py",
                                                           "the search is worth")),
    "versus": (r"against the rig already in config\.json: ([+-][\d.]+) vs "
               r"([+-][\d.]+)\s+-> (replace|KEEP THE EXISTING RIG)",
               ("tools/plan_rig.py", "against the rig already in config.json")),
    "wrote": (r"wrote (.+config\.json)", ("tools/plan_rig.py", "wrote {path}")),
    "mount": (r"arm (\d+):\s+x=\s*([+-]\d+)\s+y=\s*([+-]\d+)\s+z=\s*([+-]\d+)\s+"
              r"facing\s+([+-][\d.]+) deg", ("place_arms.py", "facing {yaw")),
    "header": (r"searching: (\d+) random rigs", ("tools/plan_rig.py", "searching: ")),
}

TARGETS = [t for s in STAGES for t in s.targets]
BY_KEY = {t.key: t for t in TARGETS}
MODULES = sorted({t.module for t in TARGETS})


def stage_index():
    return {s.id: s for s in STAGES}


# ---------------------------------------------------------------------------
# The runtime -- only ever constructed inside a job
# ---------------------------------------------------------------------------

class _Stat:
    __slots__ = ("n", "err", "ref", "sum", "max", "ring", "last_emit",
                 "last_state", "drop", "bucket_t", "bucket", "dirty")

    def __init__(self):
        self.n = self.err = self.ref = self.drop = 0
        self.sum = self.max = 0.0
        self.ring = collections.deque(maxlen=256)
        self.last_emit = 0.0
        self.last_state = object()
        self.bucket_t = 0.0
        self.bucket = 0.0
        self.dirty = False


class Runtime:
    def __init__(self, writer, events_mod, stop_event, allow_hardware, script,
                 run_dir):
        self.writer = writer
        self.ev = events_mod
        self.stop = stop_event
        self.allow_hardware = allow_hardware
        self.script = os.path.abspath(script)
        self.run_dir = run_dir
        self.local = threading.local()
        self.stats = {}
        self.lock = threading.Lock()
        self.tick_ms = {}
        self.tick_vals = {}
        self.tick_label = None
        self.frame_n = 0
        self.feed_active = 0
        self.entities = {}
        self.entities_dirty = False
        self.files = {}
        self.missed = set()
        self.probe_s = 0.0
        self.ctl_arm = {}
        self.last_layout = None
        self.memory = {}
        self.shell_faces = self.shell_colours = self.shell_verts = None
        self.shell_paint = None
        self._render_busy = False
        self._last_render = 0.0
        self._rr_scalars = None
        self.nested_parent = None
        self._flusher = threading.Thread(target=self._flush_loop,
                                         name="dc-agg", daemon=True)
        self._flusher.start()

    # --- bookkeeping -----------------------------------------------------

    def remember(self, key, value):
        self.memory[key] = value

    def stat(self, key):
        st = self.stats.get(key)
        if st is None:
            with self.lock:
                st = self.stats.setdefault(key, _Stat())
        return st

    def active(self):
        s = getattr(self.local, "active", None)
        if s is None:
            s = self.local.active = []
        return s

    def gpu_mem(self):
        torch = sys.modules.get("torch")
        try:
            if torch is not None and torch.cuda.is_initialized():
                return torch.cuda.memory_allocated()
        except Exception:                                     # noqa: BLE001
            pass
        return None

    def emit(self, kind, **fields):
        self.writer.emit(kind, **fields)

    # --- per-frame boundaries -------------------------------------------

    def flush_tick(self, produce_ms=None, consume_ms=None, camera_t=None):
        if not self.tick_ms and not self.tick_vals and produce_ms is None:
            return
        self.emit("tick", n=self.frame_n, tick=self.tick_label,
                  ct=camera_t, pm=None if produce_ms is None else round(produce_ms, 2),
                  cm=None if consume_ms is None else round(consume_ms, 2),
                  ms={k: round(v, 3) for k, v in self.tick_ms.items()},
                  v=self.tick_vals)
        self.tick_ms = {}
        self.tick_vals = {}

    def _flush_loop(self):
        while True:
            time.sleep(AGG_EVERY_S)
            try:
                self.flush_aggs()
            except Exception:                                 # noqa: BLE001
                pass

    def flush_aggs(self):
        for key, st in list(self.stats.items()):
            if not st.dirty:
                continue
            st.dirty = False
            ring = sorted(st.ring)
            p50 = ring[len(ring) // 2] if ring else None
            p95 = ring[min(len(ring) - 1, int(len(ring) * 0.95))] if ring else None
            t = BY_KEY.get(key)
            self.emit("agg", stage=t.stage if t else None, fn=key, n=st.n,
                      err=st.err, ref=st.ref, drop=st.drop,
                      sum=round(st.sum, 2), max=round(st.max, 2),
                      p50=None if p50 is None else round(p50, 3),
                      p95=None if p95 is None else round(p95, 3))
        if self.entities_dirty:
            self.entities_dirty = False
            self.emit("entities", counts=dict(self.entities))
        self.emit("cost", probe_ms=round(self.probe_s * 1000, 2),
                  writer_ms=round(self.writer.cost_s * 1000, 2),
                  calls=sum(s.n for s in self.stats.values()))

    # --- the painted still ------------------------------------------------

    def render_painted(self, final=False):
        """The reconstruction as the operator sees it, painted, now and then.

        At most every 30 s and once at the end, on its own thread, one render at
        a time. It rasterises twelve thousand triangles in Python, so doing it
        every frame would cost the loop more than every probe put together.
        """
        now = time.time()
        if self._render_busy or (not final and now - self._last_render < 30.0):
            return
        faces, verts, paint = self.shell_faces, self.shell_verts, self.shell_paint
        if faces is None or verts is None or paint is None:
            return
        if len(verts) != len(paint):
            return
        shell = sys.modules.get("shell")
        if shell is None or not hasattr(shell, "render_still"):
            return
        self._render_busy = True
        self._last_render = now
        render = getattr(shell.render_still, "__wrapped__", shell.render_still)
        path = os.path.join(self.run_dir, "frames", "painted.png")

        def work():
            try:
                render({"verts": verts, "faces": faces, "colours": paint / 255.0},
                       path, yaws=(0, -40))
                self.writer.still(path)
            except Exception as exc:                          # noqa: BLE001
                self.emit("note", what="painted still failed", err=repr(exc))
            finally:
                self._render_busy = False

        th = threading.Thread(target=work, name="dc-render", daemon=True)
        th.start()
        if final:
            th.join(timeout=20.0)

    # --- the record ---------------------------------------------------------

    def record(self, tgt, args, kwargs, result, exc, ms):
        st = self.stat(tgt.key)
        st.n += 1
        st.sum += ms
        if ms > st.max:
            st.max = ms
        st.ring.append(ms)
        st.dirty = True
        # Per-frame latency only counts work done inside the frame loop. A scan
        # that ran before the first frame is not frame 1's cost.
        if self.feed_active or tgt.mode == "frame":
            self.tick_ms[tgt.stage] = self.tick_ms.get(tgt.stage, 0.0) + ms
        if exc is not None and not isinstance(exc, DevConsoleStop):
            # A stage that says no by raising (shell.build refusing a
            # bystander) worked. That is a refusal, counted below, not an error.
            declared = False
            if tgt.refused is not None:
                try:
                    declared = bool(tgt.refused(None, None, exc))
                except Exception:                             # noqa: BLE001
                    declared = False
            if not declared:
                st.err += 1

        if tgt.silent_inside:
            act = self.active()
            if any(k in act for k in tgt.silent_inside):
                return

        mode = tgt.mode
        now = time.time()
        if mode in ("frame", "change"):
            need = exc is not None
            if tgt.state is not None and exc is None:
                try:
                    key = tgt.state(args, kwargs, result)
                except Exception:                             # noqa: BLE001
                    key = None
                if isinstance(key, STATE_PER):
                    inst, val = key
                    if not isinstance(st.last_state, dict):
                        st.last_state = {}
                    if st.last_state.get(inst, object()) != val:
                        st.last_state[inst] = val
                        need = True
                elif key != st.last_state:
                    st.last_state = key
                    need = True
            if mode == "frame" and now - st.last_emit >= 1.0 / SUMMARY_HZ:
                need = True
            if not need:
                return
            st.last_emit = now
        elif tgt.rate:
            # A token bucket: bursts are fine, a flood is counted, not written.
            st.bucket = min(tgt.rate, st.bucket + (now - st.bucket_t) * tgt.rate)
            st.bucket_t = now
            if st.bucket < 1.0:
                st.drop += 1
                return
            st.bucket -= 1.0

        summary = None
        if exc is None and tgt.keep is not None:
            try:
                summary = self.ev.summarise(tgt.keep(args, kwargs, result, self))
            except DevConsoleStop:
                raise
            except Exception as kexc:                         # noqa: BLE001
                summary = {"keep_error": repr(kexc)}
        elif exc is None and result is not None and tgt.keep is None:
            summary = self.ev.summarise(result)
        refused = False
        if tgt.refused is not None:
            try:
                refused = bool(tgt.refused(summary, result, exc))
            except Exception:                                 # noqa: BLE001
                refused = False
        if refused:
            st.ref += 1
        self.emit("span", stage=tgt.stage, fn=tgt.key, ms=round(ms, 3),
                  ok=exc is None, refused=refused,
                  err=None if exc is None else f"{type(exc).__name__}: {exc}"[:500],
                  frame=self.frame_n if self.feed_active else None,
                  sum=summary)


RT = None


# ---------------------------------------------------------------------------
# Wrapping
# ---------------------------------------------------------------------------

def _make_wrapper(func, tgt):
    rt = RT
    if tgt.mode == "gen":
        return _make_gen_wrapper(func, tgt)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Once Stop is pressed: while a feed is running, let the feed end the
        # loop at the next frame boundary, so `viz.live` returns its summary and
        # the script exits normally. Otherwise stop at the next call. A target
        # marked stop_blocks (writing the rig) refuses either way.
        if rt.stop.is_set() and not tgt.stop_safe and (
                tgt.stop_blocks or not rt.feed_active):
            raise DevConsoleStop(f"stopped before {tgt.key}")
        if tgt.mode == "once":
            rt.emit("start", stage=tgt.stage, fn=tgt.key)
        act = rt.active()
        act.append(tgt.key)
        before = rt.gpu_mem() if tgt.module == "sapiens" else None
        t0 = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:
            t1 = time.perf_counter()
            act.pop()
            if not isinstance(exc, DevConsoleStop):
                rt.record(tgt, args, kwargs, None, exc, (t1 - t0) * 1000)
            rt.probe_s += time.perf_counter() - t1
            raise
        t1 = time.perf_counter()
        act.pop()
        rt.record(tgt, args, kwargs, result, None, (t1 - t0) * 1000)
        if before is not None:
            after = rt.gpu_mem()
            if after is not None:
                rt.emit("gpu", fn=tgt.key, mb_delta=round((after - before) / 2**20, 1))
        rt.probe_s += time.perf_counter() - t1
        return result

    wrapper.__dc_stage__ = tgt.stage
    return wrapper


def _make_gen_wrapper(func, tgt):
    rt = RT

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        gen = func(*args, **kwargs)
        rt.feed_active += 1
        rt.emit("start", stage=tgt.stage, fn=tgt.key)
        last_grab = 0.0
        produced = 0
        t_start = time.perf_counter()
        why = "exhausted"
        try:
            while True:
                if rt.stop.is_set():
                    why = "stopped"
                    rt.emit("note", what="feed ended by Stop", frames=produced)
                    return
                t0 = time.perf_counter()
                try:
                    item = next(gen)
                except StopIteration:
                    return
                t1 = time.perf_counter()
                produced += 1
                rt.frame_n = produced
                camera_t = None
                try:
                    camera_t = item.get("t")
                    now = time.time()
                    if now - last_grab >= FRAME_GRAB_S:
                        last_grab = now
                        col = item.get("color")
                        dep = item.get("depth_mm")
                        if col is not None:
                            rt.writer.image("color", col[::2, ::2].copy(), "bgr")
                        if dep is not None:
                            d = dep[::2, ::2].copy()
                            rt.writer.image("depth", d, "depth")
                            rt.tick_vals["feed/depth_valid"] = round(
                                float((d > 0).mean()), 4)
                except Exception:                             # noqa: BLE001
                    pass
                rt.probe_s += time.perf_counter() - t1
                y0 = time.perf_counter()
                yield item
                consume = (time.perf_counter() - y0) * 1000
                f0 = time.perf_counter()
                rt.flush_tick((t1 - t0) * 1000, consume, camera_t)
                rt.probe_s += time.perf_counter() - f0
        except GeneratorExit:
            why = "closed by the consumer"
            raise
        finally:
            rt.feed_active -= 1
            try:
                gen.close()
            finally:
                rt.emit("span", stage=tgt.stage, fn=tgt.key,
                        ms=round((time.perf_counter() - t_start) * 1000, 1),
                        ok=True, refused=False,
                        sum={"frames": produced, "ended": why})

    wrapper.__dc_stage__ = tgt.stage
    return wrapper


def _resolve(container, path):
    parts = path.split(".")
    obj = container
    for p in parts[:-1]:
        if isinstance(obj, dict):
            obj = obj.get(p)
        else:
            obj = inspect.getattr_static(obj, p, None)
        if obj is None:
            return None, parts[-1]
    return obj, parts[-1]


def _wrap_attr(owner, name, tgt):
    """Replace owner.<name> with a wrapper. -> 'wrapped' | 'already' | 'missing'."""
    if isinstance(owner, dict):
        raw = owner.get(name)
        if raw is None:
            return "missing"
        if getattr(raw, "__dc_stage__", None):
            return "already"
        if not callable(raw):
            return "missing"
        owner[name] = _make_wrapper(raw, tgt)
        return "wrapped"
    if inspect.isclass(owner):
        raw = owner.__dict__.get(name)
        if raw is None:
            return "missing"
        if isinstance(raw, staticmethod):
            if getattr(raw.__func__, "__dc_stage__", None):
                return "already"
            setattr(owner, name, staticmethod(_make_wrapper(raw.__func__, tgt)))
            return "wrapped"
        if isinstance(raw, classmethod):
            if getattr(raw.__func__, "__dc_stage__", None):
                return "already"
            setattr(owner, name, classmethod(_make_wrapper(raw.__func__, tgt)))
            return "wrapped"
        if getattr(raw, "__dc_stage__", None):
            return "already"
        if not callable(raw):
            return "missing"
        setattr(owner, name, _make_wrapper(raw, tgt))
        return "wrapped"
    raw = inspect.getattr_static(owner, name, None)
    if raw is None:
        return "missing"
    if getattr(raw, "__dc_stage__", None):
        return "already"
    if not callable(raw):
        return "missing"
    setattr(owner, name, _make_wrapper(raw, tgt))
    return "wrapped"


def install(container, module_name, file_path=None):
    """Wrap every target declared for `module_name` inside `container`.

    `container` is a module, or the dict of the script's own `__main__`.
    Idempotent: a second call finds the wrappers and leaves them alone.
    """
    rt = RT
    if rt is None:
        return
    if module_name == "rerun":
        _install_rerun(container)
        return
    if module_name == "serial":
        _install_serial(container)
        return
    ident = id(container)
    if file_path:
        norm = os.path.normcase(os.path.abspath(file_path))
        seen = rt.files.setdefault(norm, [])
        if ident not in seen:
            seen.append(ident)
            if len(seen) > 1:
                rt.emit("dup", module=module_name, file=file_path, copies=len(seen))
    wrapped = []
    for tgt in TARGETS:
        if tgt.module != module_name or tgt.mode in ("mirror", "guard"):
            continue
        owner, leaf = _resolve(container, tgt.attr)
        how = "missing" if owner is None else _wrap_attr(owner, leaf, tgt)
        if how == "wrapped":
            wrapped.append(tgt.key)
            if tgt.mode == "gen":
                inner = owner.__dict__.get(leaf) if inspect.isclass(owner) else None
                fn = getattr(inner, "__wrapped__", None)
                if fn is not None and not inspect.isgeneratorfunction(fn):
                    rt.emit("mismatch", fn=tgt.key, what="no longer a generator")
        elif how == "missing" and (tgt.key, ident) not in rt.missed:
            rt.missed.add((tgt.key, ident))
            rt.emit("miss", stage=tgt.stage, fn=tgt.key, module=module_name)
    if wrapped:
        rt.emit("installed", module=module_name, targets=wrapped,
                main=isinstance(container, dict))


def _install_rerun(rr):
    rt = RT
    if getattr(rr, "__dc_rerun__", False):
        return
    rr.__dc_rerun__ = True
    scalars_cls = getattr(rr, "Scalars", None)
    orig_log = rr.log
    orig_set_time = rr.set_time

    def log(entity_path, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            p = entity_path if isinstance(entity_path, str) else str(entity_path)
            if (args and scalars_cls is not None and type(args[0]) is scalars_cls
                    and p.startswith(SCALAR_PREFIXES)):
                vals = args[0].scalars.as_arrow_array().to_pylist()
                rt.tick_vals[p] = vals[0] if len(vals) == 1 else vals
            else:
                key = "/".join(p.split("/")[:3])
                if key in rt.entities or len(rt.entities) < 200:
                    rt.entities[key] = rt.entities.get(key, 0) + 1
                    rt.entities_dirty = True
        except Exception:                                     # noqa: BLE001
            pass
        rt.probe_s += time.perf_counter() - t0
        return orig_log(entity_path, *args, **kwargs)

    def set_time(timeline, *args, **kwargs):
        try:
            if timeline == "tick" and kwargs.get("sequence") is not None:
                if not rt.feed_active:
                    # No feed (viz.build): the timeline IS the frame clock.
                    rt.flush_tick()
                    rt.frame_n = int(kwargs["sequence"])
                rt.tick_label = int(kwargs["sequence"])
        except Exception:                                     # noqa: BLE001
            pass
        return orig_set_time(timeline, *args, **kwargs)

    def watch(name):
        orig = getattr(rr, name, None)
        if orig is None:
            return

        @functools.wraps(orig)
        def w(*args, **kwargs):
            rt.emit("span", stage="viewer", fn=f"rerun.{name}", ms=0.0, ok=True,
                    refused=False, sum={"args": [str(a)[:200] for a in args],
                                        "kwargs": {k: str(v)[:80] for k, v in kwargs.items()}})
            return orig(*args, **kwargs)
        w.__dc_stage__ = "viewer"
        setattr(rr, name, w)

    functools.update_wrapper(log, orig_log)
    functools.update_wrapper(set_time, orig_set_time)
    log.__dc_stage__ = set_time.__dc_stage__ = "viewer"
    rr.log = log
    rr.set_time = set_time
    for n in ("init", "save", "spawn"):
        watch(n)
    rt.emit("installed", module="rerun", targets=["rerun.log", "rerun.set_time",
                                                  "rerun.init", "rerun.save",
                                                  "rerun.spawn"])


def _install_serial(serial_mod):
    rt = RT
    cls = getattr(serial_mod, "Serial", None)
    if cls is None or getattr(cls, "__dc_guard__", False):
        return
    orig_init = cls.__init__

    def __init__(self, *args, **kwargs):
        if not rt.allow_hardware:
            rt.emit("span", stage="hardware", fn="serial.Serial.__init__", ms=0.0,
                    ok=False, refused=True,
                    err="hardware access is disabled in console jobs",
                    sum={"port": str(args[0]) if args else kwargs.get("port")})
            raise RuntimeError(
                "devconsole: opening a serial port is disabled in console jobs. "
                "A console run never commands hardware; start the runner from a "
                "terminal with --allow-hardware if that is really intended.")
        return orig_init(self, *args, **kwargs)

    cls.__init__ = __init__
    cls.__dc_guard__ = True
    rt.emit("installed", module="serial", targets=["serial.Serial.__init__"],
            guard=not rt.allow_hardware)


# ---------------------------------------------------------------------------
# The import hook
# ---------------------------------------------------------------------------

def _ours(module_name, file_path):
    """Only wrap modules that are actually scrub3d's (or py/arm.py)."""
    if module_name in ("rerun", "serial"):
        return True
    if not file_path:
        return False
    f = os.path.normcase(os.path.abspath(file_path))
    roots = [os.path.normcase(SCRUB3D)]
    if module_name == "arm":
        roots.append(os.path.normcase(PY_DIR))
    return any(f.startswith(r + os.sep) for r in roots) and \
        os.path.normcase(HERE) not in f


class _PostImportFinder:
    def __init__(self, names):
        self.names = set(names)
        self._busy = set()

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self.names or fullname in self._busy:
            return None
        import importlib.util
        self._busy.add(fullname)
        try:
            spec = importlib.util.find_spec(fullname)
        except Exception:                                     # noqa: BLE001
            spec = None
        finally:
            self._busy.discard(fullname)
        if spec is None or spec.loader is None or not hasattr(spec.loader, "exec_module"):
            return None
        if not _ours(fullname, spec.origin):
            return None
        loader = spec.loader
        orig_exec = loader.exec_module

        def exec_module(module):
            orig_exec(module)
            try:
                install(module, fullname, getattr(module, "__file__", None))
            except Exception as exc:                          # noqa: BLE001
                RT.emit("note", what=f"install failed for {fullname}", err=repr(exc))

        loader.exec_module = exec_module
        return spec


def activate(rt):
    """Install the hook and the standard-library watchers. Call once."""
    global RT
    RT = rt
    # Every scrub3d module, not only those with probes: a module imported twice
    # under the same file (armmesh, when it is the script and `collide` imports
    # it again) has two copies of its caches and state, which is worth seeing
    # whether or not anything in it is wrapped.
    names = set(MODULES) | {"rerun", "serial"} | {
        os.path.splitext(f)[0] for f in os.listdir(SCRUB3D)
        if f.endswith(".py") and f != "__init__.py"}
    sys.meta_path.insert(0, _PostImportFinder(names))
    for name in names:
        mod = sys.modules.get(name)
        if mod is not None and _ours(name, getattr(mod, "__file__", None)):
            install(mod, name, getattr(mod, "__file__", None))
    _watch_warnings(rt)
    _watch_threads(rt)


def _watch_warnings(rt):
    import warnings
    orig = warnings.showwarning

    def showwarning(message, category, filename, lineno, file=None, line=None):
        try:
            rt.emit("warn", category=getattr(category, "__name__", str(category)),
                    message=str(message)[:500],
                    where=f"{os.path.basename(str(filename))}:{lineno}")
        except Exception:                                     # noqa: BLE001
            pass
        return orig(message, category, filename, lineno, file, line)

    warnings.showwarning = showwarning


def _watch_threads(rt):
    orig = threading.excepthook

    def hook(args):
        try:
            rt.emit("thread_exc", thread=getattr(args.thread, "name", None),
                    err=f"{args.exc_type.__name__}: {args.exc_value}"[:500])
        except Exception:                                     # noqa: BLE001
            pass
        return orig(args)

    threading.excepthook = hook


# ---------------------------------------------------------------------------
# --check: every target still exists, read from the source, not imported
# ---------------------------------------------------------------------------

def _source_for(module):
    if module == "arm":
        return os.path.join(PY_DIR, "arm.py")
    for cand in (os.path.join(SCRUB3D, f"{module}.py"),
                 os.path.join(SCRUB3D, "tools", f"{module}.py")):
        if os.path.exists(cand):
            return cand
    return None


def _defined(tree, path):
    """Is `Class.method` or `function` defined at the top of this AST?"""
    parts = path.split(".")
    body = tree.body
    node = None
    for i, p in enumerate(parts):
        node = next((n for n in body if isinstance(
            n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == p),
            None)
        if node is None:
            return None
        body = getattr(node, "body", [])
    return node


def _is_generator(fn_node):
    for n in ast.walk(fn_node):
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            return True
    return False


def check():
    """-> list of problems. Empty means every declared target resolves."""
    problems = []
    trees = {}
    for tgt in TARGETS:
        if tgt.module in ("rerun", "serial"):
            continue
        src = _source_for(tgt.module)
        if src is None:
            problems.append(f"{tgt.key}: no source file for module {tgt.module!r}")
            continue
        if src not in trees:
            with open(src, encoding="utf-8") as fh:
                trees[src] = ast.parse(fh.read(), filename=src)
        node = _defined(trees[src], tgt.attr)
        if node is None:
            problems.append(f"{tgt.key}: not defined in {os.path.relpath(src, WORKTREE)}")
            continue
        if tgt.mode == "gen" and not _is_generator(node):
            problems.append(f"{tgt.key}: declared a generator and no longer is one")
        if tgt.mode != "gen" and isinstance(node, ast.FunctionDef) and _is_generator(node):
            problems.append(f"{tgt.key}: is a generator but not declared as one")
    for name, (pattern, (fname, literal)) in PLAN_RIG_PATTERNS.items():
        try:
            re.compile(pattern)
        except re.error as exc:
            problems.append(f"plan_rig pattern {name!r} does not compile: {exc}")
        path = os.path.join(SCRUB3D, fname)
        with open(path, encoding="utf-8") as fh:
            if literal not in fh.read():
                problems.append(f"plan_rig pattern {name!r}: {literal!r} no longer "
                                f"appears in {fname}")
    ids = [s.id for s in STAGES]
    if len(ids) != len(set(ids)):
        problems.append("duplicate stage ids")
    for tgt in TARGETS:
        if tgt.stop_safe and tgt.stop_blocks:
            problems.append(f"{tgt.key}: both always allowed and always refused "
                            f"after Stop")
    return problems


def _load(name):
    """A console module by file path, under a private name: this file also
    runs inside jobs, where `devconsole` is not importable."""
    import importlib.util
    key = f"_dc_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            key, os.path.join(HERE, f"{name}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


def _check_flow():
    """The Overview's arrows join real stages and name real gaps."""
    gap_ids = {g.get("id") for g in _load("inventory").gaps()}
    return _load("flow").problems({s.id for s in STAGES}, gap_ids)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="devconsole probe table")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list or not a.check:
        for s in STAGES:
            print(f"{s.group:7s} {s.id:15s} {s.label}")
            for t in s.targets:
                print(f"          {t.mode:6s} {t.key}")
    if a.check:
        problems = check() + _check_flow()
        print(f"\n  {len(TARGETS)} targets across {len(STAGES)} stages, "
              f"{len(PLAN_RIG_PATTERNS)} plan_rig output patterns, "
              f"{len(_load('flow').EDGES)} flow edges")
        for p in problems:
            print(f"    PROBLEM  {p}")
        if problems:
            print(f"\n{len(problems)} PROBLEMS")
            return 1
        print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
