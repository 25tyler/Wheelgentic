"""scrub3d/track.py -- the body follows the person, every frame.

WHAT MAKES THIS REAL TIME
--------------------------
The expensive half of reconstruction runs ONCE. Sapiens segmentation and the
per-station girth fit take seconds and produce SHAPE: how thick this person's
forearm is, where their limbs taper, how wide their trunk is. None of that
changes when they move.

What changes when they move is POSE, and pose is cheap. MediaPipe returns the
joints at 30Hz on the CPU, and bodymodel.repose writes one 4x4 per region
without recomputing a single surface cell -- which is the entire reason cells
are stored in region-local coordinates. So the live loop is a pose solve and
thirteen matrix writes.

    scan once   ->  shape        seconds, GPU
    track       ->  pose         milliseconds, CPU, every frame

RETARGET, DO NOT RESCALE
-------------------------
Bone DIRECTIONS come from the live frame; bone LENGTHS come from the scan. A
person's forearm does not change length when they move it, so taking the
length from a live estimate only adds noise -- and worse, it makes the body
visibly breathe, growing and shrinking with the per-frame depth estimate.
Directions are what actually changed, so directions are what we take.

THE GUARD THAT MATTERS
-----------------------
A field-following controller will happily chase a person who stands up. If any
region moves more than MAX_JUMP_MM between consecutive frames -- 25mm, which is
750mm/s at 30fps -- the tracker reports `freeze` and the caller must stop and
retreat rather than follow. That is not a smoothing threshold to be tuned up
when it fires; it is the difference between tracking someone and lunging at
them.

"Moves" means any CELL of the region, which is what the planner aims at. The
guard used to watch region origins, and a forearm's origin is its elbow: a
forearm swinging about the elbow moved its origin by nothing and its cells by
hundreds of millimetres. In the first 300 frames of bag01 the old guard caught
5 of the 61 frames in which cells moved more than 25mm.

A LIMB THE FRAME MISSED STAYS WHERE IT WAS LAST SEEN
-----------------------------------------------------
A joint below MediaPipe's visibility floor leaves its region unsolved for that
frame. Such a region used to fall back to its pose at SCAN time -- wherever the
person sat for the scan. In the first 300 frames of bag01 a forearm dropped out
57 times and jumped as far as 1.15m and back. It now keeps the pose it last
had live, and after LOST_S it is reported `lost`: still drawn, too old to plan
against.

WHO IS IN THE CHAIR
--------------------
The territories were solved for the seat the scan saw. `seat_mm` is how far the
hip end of the trunk is from that seat, and past AWAY_MM the tracker reports
`away`: it is following somebody, but not somebody the plan is for. The first
four seconds of bag01 are the operator at the laptop starting the recording,
and until this existed the arms planned against them.

WHAT IT CANNOT DO YET
----------------------
See through an arm. A limb hidden behind the arm scrubbing it stays lost until
it shows again; masking each arm's own silhouette out of the frame, from its
joint angles, is the missing piece.
"""
import numpy as np

try:
    from . import frames as FRAME
    from . import pose as POSE
    from . import scan as SCAN
    from .bodymodel import repose
except ImportError:
    import frames as FRAME
    import pose as POSE
    import scan as SCAN
    from bodymodel import repose

# 25mm in one frame is 750mm/s. Past that it is a flinch or a stand-up, and the
# correct response is to stop, not to keep up.
MAX_JUMP_MM = 25.0

# How long a region the frame did not solve may keep its last live pose before
# it is reported lost. Long enough for a landmark flicker, short enough that a
# limb which moved while hidden is not planned against for long.
LOST_S = 1.0

# How far the hip end of the trunk may be from where the scan saw it before the
# person counts as not in the scanned seat. Measured against scan01: seated in
# bag01 99-112mm, reaching forward (pose_forward) 52, leaning (pose_lean) 80;
# the operator at the laptop before sitting down, 190-417. The hips, because a
# lean moves the shoulders 178mm and the hips 80.
AWAY_MM = 150.0

# How each region is rebuilt from the live joints: (proximal, distal).
# The head and the trunk ride the shoulders; hands ride the forearm.
LIVE_BONES = {
    "upper_arm_L": ("l_shoulder", "l_elbow"),
    "forearm_L": ("l_elbow", "l_wrist"),
    "upper_arm_R": ("r_shoulder", "r_elbow"),
    "forearm_R": ("r_elbow", "r_wrist"),
    "trunk": ("mid_shoulder", "mid_hip"),
    "thigh_L": ("l_hip", "l_knee"),
    "thigh_R": ("r_hip", "r_knee"),
}
# Regions with no joint pair of their own, carried by the region they hang off.
CARRIED = {"head": "trunk", "neck": "trunk",
           "hand_L": "forearm_L", "hand_R": "forearm_R",
           "shin_L": "thigh_L", "shin_R": "thigh_R"}


class Tracker:
    """Holds the scanned shape; produces a posed body per frame."""

    def __init__(self, body, T_world_camera, alpha=0.30):
        self.body = body
        self.T_wc = np.asarray(T_world_camera, float)
        self.cam = FRAME.apply(self.T_wc, np.zeros((1, 3)))[0]
        self.alpha = alpha
        self._pose = None
        self._smooth = {}
        self._frames = 0
        self.joints = {}          # the last frame's world joints, for a look

        # SCANNED lengths and semi-axes, captured once. These are the
        # measurements; the live frame only says where they point.
        self.rest = {}
        for r in body.regions:
            p = r.pts
            self.rest[r.name] = {
                "T": r.T.copy(),
                "length": float(p[:, 2].max() - p[:, 2].min()),
                "a_ant": float(np.abs(p[:, 0]).max()),
                "z_far": float(p[:, 2].max()) if len(p) else 0.0,
                # Every cell, homogeneous: what the jump guard measures.
                "cells": np.c_[p, np.ones(len(p))],
            }
        self._last = {}           # region -> (T, t) of its last live solve
        # The pose each region was last SHOWN in, which starts at the scan:
        # the first live frame is a jump from there like any other.
        self._shown = {k: v["T"] for k, v in self.rest.items()}
        self.seat = (self._seat(self.rest["trunk"]["T"])
                     if "trunk" in self.rest else None)

    def _seat(self, T_trunk):
        """The hip end of the trunk, world mm."""
        T = np.asarray(T_trunk, float)
        return T[:3, 3] + T[:3, 2] * self.rest["trunk"]["z_far"]

    def _mp(self):
        if self._pose is None:
            import mediapipe as mp
            # A VIDEO pipeline, not static images: the tracker reuses the
            # previous detection, which is both faster and steadier than
            # re-detecting a person who has not gone anywhere.
            #
            # complexity 0, the LITE model, and that is not a speed compromise.
            # Measured on 60 frames of bag01: lite runs at 40fps and finds the
            # person in 60 of 60, while the full model runs at 22fps and finds
            # them in 42. It is faster AND more reliable here, which is worth
            # knowing because the instinct is to reach for the heavier model
            # when tracking looks unsteady.
            #
            # Resolution was the other suspect and is not one: feeding it a
            # half-size image changes nothing, because MediaPipe resizes to its
            # own input internally. So it gets the full frame, which keeps the
            # landmark precision that everything downstream is measured in.
            self._pose = mp.solutions.pose.Pose(
                static_image_mode=False, model_complexity=0,
                enable_segmentation=False, min_detection_confidence=0.5,
                min_tracking_confidence=0.5)
        return self._pose

    def _joints_world(self, lms, depth, intr):
        """Landmarks -> {name: world position on the bone}, smoothed."""
        out = {}
        for name, px in lms.items():
            w = SCAN._landmark_world(px, depth > 0, depth, intr, self.T_wc)
            if w is None:
                continue
            prev = self._smooth.get(name)
            # A plain exponential smoother, deliberately: One-Euro's beta term
            # is only meaningful in the units it was tuned for, and this is
            # millimetres in a world frame, not normalised image coordinates.
            out[name] = w if prev is None else prev + self.alpha * (w - prev)
            self._smooth[name] = out[name]
        return out

    def update(self, frame):
        """One frame -> (posed BodyModel or None, info dict)."""
        import cv2
        res = self._mp().process(cv2.cvtColor(frame["color"], cv2.COLOR_BGR2RGB))
        if not res.pose_landmarks:
            return None, {"ok": False, "why": "no person in frame"}

        h, w = frame["color"].shape[:2]
        lm = res.pose_landmarks.landmark
        lms, vis = {}, {}
        for name, i in POSE.IDX.items():
            p = lm[i]
            vis[name] = float(p.visibility)
            if p.visibility >= 0.5:
                lms[name] = (float(p.x) * w, float(p.y) * h)
        for new, a, b in (("mid_shoulder", "l_shoulder", "r_shoulder"),
                          ("mid_hip", "l_hip", "r_hip")):
            if a in lms and b in lms:
                lms[new] = tuple((np.array(lms[a]) + np.array(lms[b])) / 2.0)

        self._frames += 1
        J = self._joints_world(lms, frame["depth_mm"], frame["intr"])
        self.joints = J
        t = frame.get("t")
        posed, info = self.solve(J, self._frames / 30.0 if t is None else float(t))
        info["visibility"] = vis
        info["t"] = t
        return posed, info

    def solve(self, J, t):
        """World joints at camera time `t` -> (posed BodyModel or None, info).

        The half of update() that needs no camera and no model, so the
        self-test can drive the hold, the guard and the seat with joints it
        made up.
        """
        tf, missing = {}, []
        for name, (pa, pb) in LIVE_BONES.items():
            if name not in self.rest:
                continue
            if pa not in J or pb not in J:
                missing.append(name)
                continue
            d = J[pb] - J[pa]
            n = float(np.linalg.norm(d))
            if n < 30.0:
                missing.append(name)          # foreshortened past usefulness
                continue
            # RETARGET: direction live, length from the scan.
            toward = self.cam - J[pa]
            toward /= max(float(np.linalg.norm(toward)), 1e-9)
            origin = J[pa] - toward * self.rest[name]["a_ant"]
            tf[name] = FRAME.region_pose(origin, d / n,
                                         anterior=self.cam - origin)

        # Carried regions keep their pose RELATIVE to whatever they hang off,
        # so a head stays on its neck and a hand on its wrist without needing
        # a landmark pair of their own.
        for name, parent in CARRIED.items():
            if name in self.rest and parent in tf:
                rel = np.linalg.inv(self.rest[parent]["T"]) @ self.rest[name]["T"]
                tf[name] = tf[parent] @ rel

        if len(tf) < 3:
            return None, {"ok": False, "why": f"only {len(tf)} regions solved"}
        solved = len(tf)

        # --- what the frame did not solve stays where it was last seen -----
        held, lost = [], []
        for name in self.rest:
            if name in tf:
                self._last[name] = (tf[name], t)
                continue
            if name not in LIVE_BONES and name not in CARRIED:
                continue                      # never tracked, never moves
            last = self._last.get(name)
            if last is None:
                lost.append(name)             # not seen live yet: scan pose
                continue
            tf[name] = last[0]
            (held if t - last[1] <= LOST_S else lost).append(name)

        # --- the jump guard, on every cell ----------------------------------
        shown = {k: tf.get(k, v["T"]) for k, v in self.rest.items()}
        jump = 0.0
        # errstate, and it is NOT masking a bad pose. Accelerate's BLAS raises
        # divide-by-zero/overflow/invalid on matmul shapes like this one while
        # returning the exact answer -- np.ones((3,3)) @ np.ones((3,448)) warns
        # three times and is still all 3.0 (measured here; clean on the Linux
        # box with the same numpy against OpenBLAS). THIS IS THE FLINCH GUARD,
        # so the noise is worse than cosmetic: it prints on every tracked frame
        # and teaches an operator to ignore the one console that reports a real
        # freeze.
        #
        # SUPPRESSING THE FLAG DOES NOT WEAKEN THE GUARD, and the reason is not
        # symmetric, so it is written down. An inf jump satisfies
        # `jump > MAX_JUMP_MM` and freezes. A NaN one does NOT: nan > x is
        # False in IEEE, so a NaN transform would fail OPEN and the arms would
        # keep following. That hole is pre-existing and has nothing to do with
        # errstate -- the warning never froze anything either, it only printed.
        # It is closed explicitly below, where a freeze can actually be caused.
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            for name, T in shown.items():
                cells = self.rest[name]["cells"]
                if len(cells):
                    d = cells @ (T - self._shown[name]).T
                    step = float(np.sqrt((d[:, :3] ** 2).sum(1)).max())
                    # A NaN here means the pose arithmetic produced something
                    # that is not a number, which is strictly less trustworthy
                    # than a large jump. Treat it as infinite so it freezes,
                    # rather than letting it slip through the comparison.
                    jump = max(jump, float("inf") if np.isnan(step) else step)
        self._shown = shown

        # --- is this the person the plan is for, in the seat it is for ------
        seat = None
        if self.seat is not None and "trunk" in tf and "trunk" not in lost:
            seat = float(np.linalg.norm(self._seat(tf["trunk"]) - self.seat))

        posed = repose(self.body, **tf)
        return posed, {"ok": True, "regions": solved, "missing": missing,
                       "held": held, "lost": lost, "jump_mm": jump,
                       "freeze": jump > MAX_JUMP_MM, "seat_mm": seat,
                       "away": seat is None or seat > AWAY_MM}

    def close(self):
        if self._pose is not None:
            self._pose.close()
            self._pose = None


if __name__ == "__main__":
    import argparse
    import os
    import time

    try:
        from . import rsfeed
    except ImportError:
        import rsfeed

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", default=os.path.join(FRAME.DATA, "scan01"))
    ap.add_argument("--source", default=os.path.join(FRAME.DATA, "bag01"))
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()

    print("live body tracking")
    print(f"  scanning {os.path.basename(a.scan)} once for SHAPE ...")
    t0 = time.time()
    body, meshes, obstacles, rep = SCAN.scan(a.scan)
    rig = FRAME.solve(a.scan)
    print(f"    {len(body.regions)} regions, {rep['scrubbable_cm2']:.0f}cm2 "
          f"scrubbable, in {time.time() - t0:.1f}s")

    tr = Tracker(body, rig["T_world_camera"])
    print(f"  tracking {os.path.basename(a.source)} for POSE ...")

    n, ok, frozen, jumps, solved = 0, 0, 0, [], []
    shown, away_at, held_n, lost_n = [], {}, 0, 0
    t0 = time.time()
    with rsfeed.Feed(a.source, repeat=False) as feed:
        for f in feed.frames(limit=a.limit):
            n += 1
            posed, info = tr.update(f)
            if not info["ok"]:
                continue
            ok += 1
            solved.append(info["regions"])
            jumps.append(info["jump_mm"])
            frozen += int(info["freeze"])
            away_at[n - 1] = info["away"]
            held_n += bool(info["held"])
            lost_n += bool(info["lost"])
            shown.append(({r.name: r.T for r in posed.regions}, info["jump_mm"]))
    wall = time.time() - t0
    tr.close()

    print(f"    {ok}/{n} frames tracked at {ok / max(wall, 1e-6):.1f} fps "
          f"(the camera records at 30)")
    print(f"    regions solved per frame: median {np.median(solved):.0f} "
          f"of {len(LIVE_BONES)} jointed")
    j = np.array(jumps[1:])
    print(f"    frame-to-frame motion of any cell: median {np.median(j):.1f}mm, "
          f"p95 {np.percentile(j, 95):.1f}mm, max {j.max():.1f}mm")
    print(f"    freeze guard fired on {frozen} frames "
          f"(> {MAX_JUMP_MM:.0f}mm in one frame)")
    print(f"    a region held at its last live pose on {held_n} frames, "
          f"lost on {lost_n}")

    assert ok > 0.8 * n, f"only {ok} of {n} frames tracked"
    assert ok / max(wall, 1e-6) > 15.0, "tracking is slower than half of 30fps"

    # THE GUARD SEES WHAT THE CELLS DO. Recomputed from the bodies the tracker
    # handed out, not from its own bookkeeping.
    cells = {r.name: np.c_[r.pts, np.ones(len(r.pts))] for r in body.regions}
    prev = {r.name: r.T for r in body.regions}
    short = 0.0
    for Ts, reported in shown:
        moved = max(float(np.linalg.norm((cells[k] @ (Ts[k] - prev[k]).T)[:, :3],
                                         axis=1).max()) for k in Ts)
        short = max(short, moved - reported)
        prev = Ts
    print(f"    the guard saw every cell move (largest shortfall {short:.2g}mm)")
    assert short < 1e-6, f"cells moved {short:.1f}mm more than the guard reported"

    away_n = sum(away_at.values())
    print(f"    not in the scanned seat on {away_n} of {len(away_at)} frames")
    bag01 = os.path.join(FRAME.DATA, "bag01")
    if (os.path.normcase(os.path.abspath(a.source)) == os.path.normcase(bag01)
            and a.limit >= 200):
        # bag01 opens with the operator at the laptop, starting the recording,
        # and they are seated by frame 150.
        laptop = [away_at[i] for i in range(0, 100) if i in away_at]
        seated = [away_at[i] for i in range(160, 200) if i in away_at]
        print(f"      the operator at the laptop (frames 0-99): away on "
              f"{sum(laptop)} of {len(laptop)}; seated (160-199): away on "
              f"{sum(seated)} of {len(seated)}")
        assert sum(laptop) >= 0.9 * len(laptop), "the laptop was taken for the seat"
        assert not any(seated), "a seated person was reported away"

    # --- the hold, the guard and the seat, with made-up joints ------------
    from scipy.spatial.transform import Rotation

    def joints_for(trk, poses):
        """Joints that make solve() put each region at `poses`, near enough."""
        out = {}
        for name, (pa, pb) in LIVE_BONES.items():
            if name not in poses:
                continue
            T = np.asarray(poses[name], float)
            o, z = T[:3, 3], T[:3, 2]
            p = o.copy()
            for _ in range(6):
                toward = trk.cam - p
                p = o + toward / np.linalg.norm(toward) * trk.rest[name]["a_ant"]
            out.setdefault(pa, p)
            out.setdefault(pb, p + z * trk.rest[name]["length"])
        return out

    def swing(J, prox, dist, deg):
        """`dist` rotated about `prox`, raising the bone by `deg`."""
        v = J[dist] - J[prox]
        axis = np.cross(v, [0.0, 0.0, 1.0])
        axis = axis / np.linalg.norm(axis)
        return J[prox] + Rotation.from_rotvec(axis * np.radians(deg)).apply(v)

    def moved(A, B, name):
        return float(np.linalg.norm((cells[name] @ (A - B).T)[:, :3], axis=1).max())

    print("  the hold, the guard and the seat, with made-up joints ...")
    dt = 1.0 / 30.0
    syn = Tracker(body, rig["T_world_camera"])
    J0 = joints_for(syn, {k: v["T"] for k, v in syn.rest.items()})
    J1 = dict(J0, l_wrist=swing(J0, "l_elbow", "l_wrist", 30.0))
    syn.solve(J1, 0.0)                   # the first frame jumps from the scan
    _, i = syn.solve(J1, dt)
    assert not i["freeze"] and i["jump_mm"] < 0.5, i
    live_pose = syn._shown["forearm_L"]
    no_wrist = {k: v for k, v in J1.items() if k != "l_wrist"}
    _, i = syn.solve(no_wrist, 2 * dt)
    assert i["held"] == ["forearm_L"] and i["jump_mm"] < 0.5, i
    p2, i = syn.solve(no_wrist, 2 * dt + LOST_S + 0.05)
    assert i["lost"] == ["forearm_L"] and not i["held"], i
    fl = next(r for r in p2.regions if r.name == "forearm_L")
    assert np.allclose(fl.T, live_pose), "a lost forearm went back to the scan"
    back = moved(live_pose, syn.rest["forearm_L"]["T"], "forearm_L")
    print(f"    a forearm the frame missed stayed where it was seen, lost after "
          f"{LOST_S:.0f}s (it used to jump {back:.0f}mm back to the scan pose)")

    _, i = syn.solve(J1, 3 * dt + LOST_S)
    assert not i["held"] and not i["lost"] and i["jump_mm"] < 0.5, i
    before = syn._shown["forearm_L"]
    _, i = syn.solve(dict(J1, l_wrist=swing(J1, "l_elbow", "l_wrist", 20.0)),
                     4 * dt + LOST_S)
    origin = float(np.linalg.norm(syn._shown["forearm_L"][:3, 3] - before[:3, 3]))
    assert origin < 1e-6 and i["freeze"] and i["jump_mm"] > MAX_JUMP_MM, (origin, i)
    print(f"    a forearm swung 20 degrees about its elbow: origin moved "
          f"{origin:.1f}mm, cells {i['jump_mm']:.0f}mm, freeze {i['freeze']}")

    seats = {}
    for shift in ((0.0, 0.0, 0.0), (40.0, 0.0, 0.0), (0.0, 300.0, 0.0)):
        s = Tracker(body, rig["T_world_camera"])
        _, i = s.solve({k: v + np.array(shift) for k, v in J0.items()}, 0.0)
        seats[shift] = (i["seat_mm"], i["away"])
    assert seats[(0.0, 0.0, 0.0)][0] < 1.0 and not seats[(0.0, 0.0, 0.0)][1], seats
    assert not seats[(40.0, 0.0, 0.0)][1] and seats[(0.0, 300.0, 0.0)][1], seats
    s = Tracker(body, rig["T_world_camera"])
    _, i = s.solve({k: v for k, v in J0.items()
                    if k not in ("mid_shoulder", "mid_hip")}, 0.0)
    assert i["seat_mm"] is None and i["away"], i
    print(f"    the seat: 40mm off reads {seats[(40.0, 0.0, 0.0)][0]:.0f}mm and "
          f"stays, 300mm off reads {seats[(0.0, 300.0, 0.0)][0]:.0f}mm and is "
          f"away, and a frame with no trunk is away")
    print("\n  shape measured once, pose solved every frame. OK")
