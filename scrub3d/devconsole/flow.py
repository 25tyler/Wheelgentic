"""devconsole/flow.py -- what each stage hands to the next.

The Overview draws the pipeline as one diagram: the stages of probes.STAGES as
nodes and these edges as the arrows between them. The edges are written by
hand from the code, so `probes.py --check` holds them to it: both ends must be
stages, and a gap edge must name a gap inventory.gaps() still reports.

Each edge is (from, to, what) plus an optional kind:
  (none)          data passed whenever a run reaches both ends
  "selftest"      exercised by a module's own self-test only
  "gap:<id>"      a link the design needs and the code does not make yet. It is
                  drawn dashed and red with the gap's own words, and turns into
                  an ordinary arrow by itself once inventory.gaps() reports the
                  gap closed

Plain data, no imports: the probe check loads this file by path.
"""

EDGES = [
    # --- Sense: a capture, and what is read off it -------------------------
    ("capture", "world", "the floor the camera saw"),
    ("capture", "segmentation", "the colour image"),
    ("capture", "landmarks", "the colour image"),
    ("segmentation", "carve", "the person's outline"),
    ("landmarks", "carve", "where the joints are"),
    ("segmentation", "sleeves", "the limb classes", "selftest"),
    ("segcache", "segmentation", "rebuilt cached masks"),
    ("cameraday", "capture", "a rehearsal capture, deleted after"),

    # --- Shape: the person, measured once ------------------------------------
    ("carve", "scan", "one mask per limb"),
    ("world", "scan", "the world frame"),
    ("capture", "scan", "median depth"),
    ("capture", "reconstruction", "depth and colour"),
    ("normals", "reconstruction", "predicted surface normals"),
    ("depthnet", "reconstruction", "hole fill, only when its fit is good"),
    ("scan", "skinning", "the bones"),
    ("reconstruction", "skinning", "the person's surface"),
    ("scan", "bodystore", "a body to keep"),

    # --- Plan: who scrubs what -----------------------------------------------
    ("scan", "territories", "cells and measured obstacles"),
    ("bodystore", "territories", "a saved body, identity checked"),
    ("modelled", "territories", "a stand-in body, no capture"),
    ("rig", "territories", "the four mounts"),
    ("territories", "preflight", "territories and phases"),
    ("rig", "preflight", "the four mounts"),
    ("governor", "preflight", "verdicts on the head-ceiling probe"),
    ("territories", "posture", "coverage for each posture", "selftest"),
    ("placement", "search", "scores for candidate rigs"),
    ("search", "rig", "a better rig, written to config.json"),
    ("calibration", "rig", "measured mounts", "gap:handeye-live"),

    # --- Follow: every frame -------------------------------------------------
    ("feed", "tracking", "colour and depth frames"),
    ("world", "tracking", "the world frame"),
    ("scan", "tracking", "bone lengths"),
    ("tracking", "skinning", "posed bones"),
    ("tracking", "coverage", "posed cells"),
    ("tracking", "reachability", "the posed body, once a second"),
    ("tracking", "loop", "freeze, lost limbs, nobody seated"),
    ("territories", "coverage", "each arm's cells"),
    ("territories", "reachability", "who owns what"),
    ("coverage", "loop", "the next sponge target"),
    ("reachability", "loop", "handoffs and stops"),

    # --- Decide and act ------------------------------------------------------
    ("loop", "governor", "sponge targets", "gap:governor-in-loop"),
    ("contact", "governor", "contact force", "gap:torque-live"),
    ("capsules", "governor", "capsule radii that fit the arm", "selftest"),
    ("consent", "governor", "fresh consent to clear an estop", "selftest"),
    ("governor", "armlink", "CLEAR verdicts only"),
    ("armlink", "driver", "targets inside BOX, or refused"),
    ("driver", "hardware", "serial writes"),
    ("skinning", "viewer", "the person, posed"),
    ("loop", "viewer", "arms, paint and progress"),
]


def edges():
    """-> [{src, dst, what, kind, gap}] with kind data, selftest or gap."""
    out = []
    for e in EDGES:
        src, dst, what = e[:3]
        tag = e[3] if len(e) > 3 else ""
        kind, _, gap = tag.partition(":")
        out.append({"src": src, "dst": dst, "what": what,
                    "kind": kind or "data", "gap": gap or None})
    return out


def problems(stage_ids, gap_ids):
    """-> list of problems with the edge table against the code."""
    out = []
    seen = set()
    for e in edges():
        for end in (e["src"], e["dst"]):
            if end not in stage_ids:
                out.append(f"flow edge {e['src']} -> {e['dst']}: no stage {end!r}")
        if e["kind"] not in ("data", "selftest", "gap"):
            out.append(f"flow edge {e['src']} -> {e['dst']}: kind {e['kind']!r}")
        if e["kind"] == "gap" and e["gap"] not in gap_ids:
            out.append(f"flow edge {e['src']} -> {e['dst']}: gap {e['gap']!r} is "
                       f"not one inventory.gaps() reports")
        key = (e["src"], e["dst"])
        if key in seen:
            out.append(f"flow edge {e['src']} -> {e['dst']} is listed twice")
        seen.add(key)
    lonely = set(stage_ids) - {x for e in edges() for x in (e["src"], e["dst"])}
    for sid in sorted(lonely):
        out.append(f"stage {sid!r} has no flow edge, so the diagram shows it alone")
    return out
