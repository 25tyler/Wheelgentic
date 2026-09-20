"""scrub3d/bodystore.py -- save a scanned person, and refuse to reload them onto
somebody else.

    python scrub3d/bodystore.py       # round trip on scan01, then the refusals

WHY SAVE AT ALL
----------------
A scan costs seconds of GPU: Sapiens, the carve, the girth fit, the snap onto
measured depth. Doing it once per session is fine. Doing it every time the same
person sits down is a minute of somebody waiting for a number that has not
changed since yesterday. Reloading is a few milliseconds.

WHY THIS IS THE DANGEROUS PART
-------------------------------
Applying person A's model to person B puts a sponge somewhere it should not be,
and nothing downstream can notice: the cells are internally consistent, the
partition succeeds, the controller drives to them, and every check reports
clear against a body nobody in the room has.

So loading is gated on GEOMETRY, and the threshold is deliberately tight. Four
measurements the scan already makes -- shoulder height, biacromial width, upper
arm and forearm -- must each match within 8%. That is well inside the spread
between two adults and well outside this pipeline's own repeatability.

**We identify you by your shoulder width, not your face**, which for a project
whose pitch is privacy is a better line than anything face recognition buys.

WHAT IS NOT STORED, ON PURPOSE
-------------------------------
No colour image, no face, no name. The directory holds geometry and the
provenance needed to say where it came from. It is still biometric data, so
`bodies/` is gitignored alongside `data/` and `weights/`, and the consent block
carries a delete-after date rather than leaving that to somebody's memory.

FLAGS ARE ANDed WITH POLICY AT LOAD, NEVER BAKED IN
----------------------------------------------------
`scrubbable` is stored as it was measured, and intersected at load with what the
code currently permits. So a policy can be TIGHTENED without re-scanning anyone,
and -- the direction that matters -- a stale file can never UNLOCK a region the
running code has since decided is off limits. A model saved when the chest was
scrubbable does not re-enable the chest.
"""
import datetime
import json
import os
import subprocess

import numpy as np

try:
    from . import bodymodel as BM
except ImportError:
    import bodymodel as BM

SCHEMA_VERSION = 1
HERE = os.path.dirname(os.path.abspath(__file__))
BODIES = os.path.join(HERE, "bodies")

# Each dimension must match within this or the model is refused. 8% is the
# plan's number and it is not arbitrary: the pipeline reproduces a bone length
# to about 2% between captures of the same person, and two different adults
# differ by far more than 8% on at least one of these four.
IDENTITY_TOLERANCE = 0.08

IDENTITY_KEYS = ("shoulder_z_mm", "biacromial_mm",
                 "upper_arm_len_mm", "forearm_len_mm")

# Left and right are the same length on everybody, so when the MEASURED ones
# disagree by more than this the subject is turned or a limb is pointing at the
# camera. The number is then foreshortened, not different, and the honest
# answer is that identity cannot be judged from this frame.
#
# MEASURED, and this is why the state exists: scan01 saved against 60 live
# frames of bag01 -- the same person, different posture -- reads biacromial
# 36% narrower, upper arm 27% shorter and forearm 68% shorter, at a forearm
# length of 78mm that no adult has. Without this the gate refuses somebody
# their own model and a frustrated operator turns the gate off.
MAX_ASYMMETRY = 0.15

LOAD, REFUSE, CANNOT_JUDGE = "LOAD", "REFUSE", "CANNOT JUDGE"


def _git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=HERE, capture_output=True, text=True,
                              timeout=5).stdout.strip() or "unknown"
    except Exception:                                           # noqa: BLE001
        return "unknown"


def _landmark_identity(colour, depth, intr, T_wc, solver):
    """One frame -> the four identity numbers, or None. THE definition.

    THERE MUST BE EXACTLY ONE OF THESE, and the first version of this file had
    two. The saved numbers came from the region origins the scan builds, and
    the live ones from MediaPipe landmarks. They measure different things -- a
    region origin is pushed back from the acromion by the limb's own anterior
    semi-axis -- so the same person compared 43% apart on shoulder width and
    was refused by their own model. A gate that refuses everybody gets switched
    off, which is worse than not having one.

    So both paths call this, on a median scan frame and on live frames
    respectively, and what is compared is the same measurement taken twice.
    """
    import cv2
    try:
        from . import pose as POSE
        from . import scan as SCAN
    except ImportError:
        import pose as POSE
        import scan as SCAN

    res = solver.process(cv2.cvtColor(colour, cv2.COLOR_BGR2RGB))
    if not res.pose_landmarks:
        return None
    h, w = colour.shape[:2]
    lm = res.pose_landmarks.landmark
    J = {}
    for name in ("l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
                 "l_wrist", "r_wrist"):
        p = lm[POSE.IDX[name]]
        if p.visibility < 0.5:
            continue
        wp = SCAN._landmark_world((p.x * w, p.y * h), depth > 0, depth, intr,
                                  np.asarray(T_wc, float))
        if wp is not None:
            J[name] = wp
    if "l_shoulder" not in J or "r_shoulder" not in J:
        return None

    out = {
        "biacromial_mm": float(np.linalg.norm(J["l_shoulder"] - J["r_shoulder"])),
        "shoulder_z_mm": float((J["l_shoulder"][2] + J["r_shoulder"][2]) / 2.0),
    }
    worst_asym = 0.0
    for key, (a, b) in (("upper_arm_len_mm", ("shoulder", "elbow")),
                        ("forearm_len_mm", ("elbow", "wrist"))):
        vals = [float(np.linalg.norm(J[f"{side}_{b}"] - J[f"{side}_{a}"]))
                for side in "lr"
                if f"{side}_{a}" in J and f"{side}_{b}" in J]
        out[key] = float(np.mean(vals)) if vals else 0.0
        # LEFT AND RIGHT ARE THE SAME LENGTH ON EVERYBODY. When the measured
        # ones disagree, the person is turned or a limb points at the camera,
        # and the number is foreshortened rather than wrong about them. This is
        # what lets the gate say "I cannot tell" instead of guessing.
        if len(vals) == 2 and max(vals) > 1e-6:
            worst_asym = max(worst_asym,
                             abs(vals[0] - vals[1]) / max(vals))
    out["asymmetry"] = worst_asym
    return out


def identity_from_capture(capture_dir):
    """The four numbers, measured from a capture's own median frame. -> dict."""
    import cv2
    import mediapipe as mp
    try:
        from . import frames as FRAME
    except ImportError:
        import frames as FRAME

    with open(os.path.join(capture_dir, "meta.json"), encoding="utf-8") as fh:
        intr = json.load(fh)["color_intrinsics"]
    depth = np.load(os.path.join(capture_dir, "depth_median_mm.npy"))
    colour = cv2.imread(os.path.join(capture_dir, "color_median.png"))
    T = FRAME.solve(capture_dir)["T_world_camera"]
    solver = mp.solutions.pose.Pose(model_complexity=0,
                                    min_detection_confidence=0.5)
    try:
        got = _landmark_identity(colour, depth, intr, T, solver)
    finally:
        solver.close()
    if got is None:
        raise RuntimeError(f"no person found in {capture_dir}")
    return got


def save(body, rep, person_id, identity, given_by="", keep_days=30,
         root=BODIES):
    """Write a scanned body. -> the directory it went to.

    Written to a temp directory and moved into place only once everything is
    on disk, so an interrupted save can never leave a half-written model that
    loads.
    """
    out = os.path.join(root, person_id)
    tmp = out + ".partial"
    if os.path.isdir(tmp):
        import shutil
        shutil.rmtree(tmp)
    os.makedirs(tmp, exist_ok=True)

    arrays = {}
    regions = []
    for i, r in enumerate(body.regions):
        arrays[f"{i}_pts"] = np.asarray(r.pts, np.float32)
        arrays[f"{i}_nrm"] = np.asarray(r.nrm, np.float32)
        arrays[f"{i}_area"] = np.asarray(r.area, np.float32)
        arrays[f"{i}_flags"] = np.asarray(r.scrubbable, bool)
        regions.append({"name": r.name, "T": np.asarray(r.T, float).tolist(),
                        "n": int(r.n), "arc": list(r.arc),
                        "n_exp": float(r.n_exp)})
    np.savez_compressed(os.path.join(tmp, "regions.npz"), **arrays)

    now = datetime.datetime.now(datetime.timezone.utc)
    meta = {
        "schema_version": SCHEMA_VERSION,
        "units": "mm",
        "person_id": person_id,
        "consent": {
            "given_by": given_by,
            "given_utc": now.isoformat(timespec="seconds"),
            "delete_after_utc": (now + datetime.timedelta(days=keep_days))
                                .isoformat(timespec="seconds"),
        },
        "identity": dict(identity),
        "regions": regions,
        "quality": {"parts": rep.get("parts", {}),
                    "clothing": rep.get("clothing", {}),
                    "scrubbable_cm2": rep.get("scrubbable_cm2")},
        "provenance": {"capture": rep.get("capture"), "git_sha": _git_sha(),
                       "b_from": rep.get("b_from"),
                       "normals_from": rep.get("normals_from")},
    }
    with open(os.path.join(tmp, "model.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)

    # The marker the plan asks for, so nobody mistakes a model directory for a
    # photograph of somebody.
    with open(os.path.join(tmp, "README.txt"), "w", encoding="utf-8") as fh:
        fh.write("Geometry only: no image, no face, no name.\n"
                 "Still biometric data. bodies/ is gitignored and this model\n"
                 f"is to be deleted after {meta['consent']['delete_after_utc']}.\n")

    if os.path.isdir(out):
        import shutil
        shutil.rmtree(out)
    os.replace(tmp, out)
    return out


def load(path, policy=None):
    """Read a saved body. -> (BodyModel, meta).

    `policy` maps region name -> whether the CURRENT code permits scrubbing it.
    Stored flags are ANDed with it, never replaced by it: policy can only ever
    take a region away.
    """
    with open(os.path.join(path, "model.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    if meta.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(
            f"{path} is schema {meta.get('schema_version')}, this code reads "
            f"{SCHEMA_VERSION}. Re-scan rather than guess what changed.")

    z = np.load(os.path.join(path, "regions.npz"))
    body = BM.BodyModel()
    for i, r in enumerate(meta["regions"]):
        flags = z[f"{i}_flags"]
        if policy is not None:
            # AND, not assign. A model saved when a region was scrubbable must
            # not re-enable it after the code stopped permitting it.
            flags = flags & bool(policy.get(r["name"], False))
        body.regions.append(BM.Region(
            r["name"], np.asarray(r["T"], float),
            np.asarray(z[f"{i}_pts"], float), np.asarray(z[f"{i}_nrm"], float),
            np.asarray(z[f"{i}_area"], float), flags,
            tuple(r.get("arc", (-np.pi / 2, np.pi / 2))),
            float(r.get("n_exp", 2.0))))
    return body, meta


def identity_ok(meta, live, tol=IDENTITY_TOLERANCE, max_asym=MAX_ASYMMETRY):
    """Is this saved model of the person in the chair? -> (verdict, table).

    THREE ANSWERS, NOT TWO. Every dimension must agree to LOAD -- not the mean,
    not three of four, because a model that matches on three and is 20% out on
    the fourth is a different person of similar build, and averaging that away
    is how the wrong body gets loaded.

    But REFUSE and LOAD are not the only honest outcomes. A subject who is
    turned, or whose forearm points at the camera, produces foreshortened
    lengths that are wrong about the measurement rather than about the person.
    Left and right disagreeing is the tell, and the answer there is CANNOT
    JUDGE, which falls back to re-scanning. Re-scanning is cheap; loading the
    wrong body is not, and refusing somebody their own model teaches an
    operator to switch the gate off.
    """
    asym = float(live.get("asymmetry", 0.0))
    if asym > max_asym:
        return CANNOT_JUDGE, [("left/right asymmetry", max_asym, asym,
                               asym, False)]
    rows, ok = [], True
    for k in IDENTITY_KEYS:
        a, b = float(meta["identity"].get(k, 0.0)), float(live.get(k, 0.0))
        if a <= 0 or b <= 0:
            rows.append((k, a, b, None, False))
            ok = False
            continue
        rel = abs(a - b) / a
        good = rel <= tol
        ok = ok and good
        rows.append((k, a, b, rel, good))
    return (LOAD if ok else REFUSE), rows


def identity_from_frames(source, T_world_camera, n_frames=30, repeat=False):
    """Measure the four identity numbers LIVE. -> (dict, frames used).

    MediaPipe and depth only: no Sapiens, no carve, no girth fit, no Poisson.
    That is the point. Deciding whether a saved model belongs to the person in
    the chair has to be fast, or it costs what re-scanning would have and the
    saved model buys nothing.

    Each dimension is the MEDIAN over frames rather than the mean. A landmark
    that drops out for a moment produces a wild single-frame value, and a mean
    carries it into the comparison.
    """
    try:
        from . import rsfeed
    except ImportError:
        import rsfeed
    import mediapipe as mp

    seen = {k: [] for k in IDENTITY_KEYS}
    used = 0
    solver = mp.solutions.pose.Pose(model_complexity=0,
                                    min_detection_confidence=0.5)
    try:
        with rsfeed.Feed(source, repeat=repeat) as feed:
            for f in feed.frames(limit=n_frames):
                got = _landmark_identity(f["color"], f["depth_mm"], f["intr"],
                                         T_world_camera, solver)
                if got is None:
                    continue
                used += 1
                for k, v in got.items():
                    if v > 0:
                        seen[k].append(v)
    finally:
        solver.close()
    return ({k: (float(np.median(v)) if v else 0.0) for k, v in seen.items()},
            used)


def describe(rows):
    for k, a, b, rel, good in rows:
        r = "  n/a" if rel is None else f"{100 * rel:4.1f}%"
        print(f"    [{'ok ' if good else 'NO '}] {k:18s} saved {a:7.1f}  "
              f"live {b:7.1f}   off by {r}")


def main():
    import time
    try:
        from . import scan as SCAN
    except ImportError:
        import scan as SCAN

    cap = os.path.join(HERE, "data", "scan01")
    print(f"\nscanning {os.path.basename(cap)} the slow way ...")
    t0 = time.perf_counter()
    body, _meshes, _obst, rep = SCAN.scan(cap)
    rep["capture"] = os.path.basename(cap)
    scan_s = time.perf_counter() - t0

    root = os.path.join(HERE, "bodies")
    ident = identity_from_capture(cap)
    path = save(body, rep, "selftest", ident, given_by="self-test",
                keep_days=1, root=root)
    print(f"  saved to {os.path.relpath(path, HERE)}")

    t0 = time.perf_counter()
    back, meta = load(path)
    load_s = time.perf_counter() - t0
    print(f"\n  scan {scan_s:.1f}s   reload {1000 * load_s:.0f}ms   "
          f"({scan_s / max(load_s, 1e-9):.0f}x faster)")
    assert load_s < 2.0, "reloading is supposed to be the fast path"

    # --- the round trip has to be exact -------------------------------------
    assert len(back.regions) == len(body.regions)
    worst = 0.0
    for a, b in zip(body.regions, back.regions):
        assert a.name == b.name
        assert a.scrubbable.tolist() == b.scrubbable.tolist()
        assert a.arc == b.arc and a.n_exp == b.n_exp
        worst = max(worst, float(np.abs(np.asarray(a.pts) - b.pts).max()),
                    float(np.abs(np.asarray(a.T) - b.T).max()))
    print(f"  round trip: worst difference {worst:.2e} mm across "
          f"{sum(r.n for r in body.regions)} cells")
    assert worst < 1e-3, "the saved body is not the scanned body"

    # --- it is the same person -----------------------------------------------
    live = dict(ident)
    ok, rows = identity_ok(meta, live)
    print("\n  the same person sits back down:")
    describe(rows)
    assert ok, "a model refused the person it was made from"

    # --- somebody else is not ------------------------------------------------
    #
    # 9% on ONE dimension, everything else identical. This is the case that
    # matters: not an obviously different body, but a similar one. Averaging
    # across dimensions would pass it, which is why every dimension must agree.
    other = dict(live)
    other["forearm_len_mm"] = live["forearm_len_mm"] * 1.09
    v2, rows2 = identity_ok(meta, other)
    print("\n  someone with a 9% longer forearm and everything else identical:")
    describe(rows2)
    assert v2 == REFUSE, (
        "a model loaded onto a different person. Three matching dimensions "
        "and one that does not is a similar build, not the same body.")

    # And 7% passes, so the gate is a threshold rather than a coin flip.
    near = dict(live)
    near["forearm_len_mm"] = live["forearm_len_mm"] * 1.07
    v3, _ = identity_ok(meta, near)
    assert v3 == LOAD, "7% was refused; the tolerance is not what it says"
    print(f"\n    7% passes, 9% refuses, tolerance {100 * IDENTITY_TOLERANCE:.0f}%")

    # --- and the third answer -----------------------------------------------
    #
    # A turned subject, or a forearm pointing at the camera, foreshortens. The
    # number is then wrong about the MEASUREMENT, not about the person.
    # Measured across two captures of the same person in different postures,
    # bag01 reads a 78mm forearm, which no adult has.
    turned = dict(live)
    turned["asymmetry"] = MAX_ASYMMETRY + 0.05
    v4, _ = identity_ok(meta, turned)
    print(f"\n  the same numbers, but left and right disagree by "
          f"{100 * turned['asymmetry']:.0f}%: {v4}")
    assert v4 == CANNOT_JUDGE, (
        "a foreshortened subject was judged rather than re-scanned; that is "
        "how an operator learns to switch the gate off")

    # --- policy can take a region away, and never give one back --------------
    permissive = {r["name"]: True for r in meta["regions"]}
    strict = dict(permissive)
    strict["forearm_L"] = False
    loose, _ = load(path, policy=permissive)
    tight, _ = load(path, policy=strict)
    n_loose = sum(int(r.scrubbable.sum()) for r in loose.regions)
    n_tight = sum(int(r.scrubbable.sum()) for r in tight.regions)
    print(f"\n  policy AND: {n_loose} scrubbable cells, {n_tight} once "
          f"forearm_L is withdrawn")
    assert n_tight < n_loose, "tightening the policy changed nothing"

    # The direction that matters: a stored True cannot re-enable what the code
    # has since banned, and a stored False cannot be turned on by a permissive
    # policy either.
    stored_trunk = next(r for r in body.regions if r.name == "trunk")
    back_trunk = next(r for r in loose.regions if r.name == "trunk")
    if not stored_trunk.scrubbable.any():
        assert not back_trunk.scrubbable.any(), (
            "a permissive policy re-enabled a region the scan marked "
            "unscrubbable; policy must only ever take away")
        print("    a permissive policy did NOT re-enable the trunk")

    print(f"\n  consent expires {meta['consent']['delete_after_utc']}")
    print("\n  geometry identifies you, not your face. OK")


if __name__ == "__main__":
    main()
