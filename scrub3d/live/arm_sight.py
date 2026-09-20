"""scrub3d/live/arm_sight.py -- whether the depth camera sees each real arm
where scrub3d has it.

    python scrub3d/live/arm_sight.py --selftest

An arm's joint sensors say where it is relative to its own base. Whether
that base stands where the rig file puts it, and so whether the arm is
where the collision checks think, only something outside the arm can tell:
the camera. Each arm's model, posed with its measured joint angles on its
rig-file base, is drawn as the camera would see it (every STEP_PX-th pixel)
and compared with the depth the camera measured there. A drawn pixel is:

  - seen: the camera has a surface within MATCH_MM of the model;
  - not seen: it has one further away (it looks through where the arm
    should be) or nearer (something in front, or the arm itself is nearer);
  - unknown: no depth there.

An arm whose drawn pixels are mostly seen (SEEN_OK) is where scrub3d has
it. Otherwise the drawn arm is moved about, SHIFT_MM steps up to SHIFT_N
steps each way, to find where it matches best: a clearly better match
somewhere else says which way the base is off.

This only warns. It has not yet looked at a real arm, and dark parts can
give the D455 no depth at all; with too few pixels it says it cannot tell.
"""
import argparse
import itertools
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(HERE)
for p in (WT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import armmesh                                  # noqa: E402

MATCH_MM = 25.0             # the D455 at 1 to 2 m is good to about 1 to 2 cm
STEP_PX = 4
MIN_PIXELS = 40             # fewer drawn pixels with depth: cannot tell
SEEN_OK = 0.6               # this share seen: where scrub3d has it
BETTER = 0.25               # a shift this much better matched: the base is off
SHIFT_MM = 10.0
SHIFT_N = 6                 # shifts up to 60 mm each way, on each axis
SEARCH_POINTS = 600         # drawn points the search moves about
NAMES = ("red", "blue", "green", "orange")


def _cam(T_wc):
    T = np.asarray(T_wc, float)
    return T[:3, :3], T[:3, 3]


class ArmSight:
    """Draws arms as the camera sees them and compares with its depth."""

    def __init__(self, intr):
        self.intr = intr
        self.shifts = np.array(sorted(
            itertools.product(range(-SHIFT_N, SHIFT_N + 1), repeat=3),
            key=lambda s: sum(abs(v) for v in s)), float) * SHIFT_MM

    def _project(self, Pc):
        i = self.intr
        z = Pc[..., 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = np.round(Pc[..., 0] / z * i["fx"] + i["ppx"])
            v = np.round(Pc[..., 1] / z * i["fy"] + i["ppy"])
        return u, v, z

    def draw(self, T_wc, T_base, joints):
        """The arm's pixels as the camera sees it. -> (N, 3) camera-frame
        points on the arm's visible surface (one a drawn pixel)."""
        import open3d as o3d
        R, t = _cam(T_wc)
        scene = o3d.t.geometry.RaycastingScene()
        allc = []
        j = tuple(float(v) for v in joints[:3])
        for _name, (V, F) in armmesh.posed(*j, T_world_base=T_base).items():
            Vc = (V - t) @ R
            allc.append(Vc)
            scene.add_triangles(o3d.core.Tensor(Vc.astype(np.float32)),
                                o3d.core.Tensor(F.astype(np.uint32)))
        Vc = np.concatenate(allc)
        if not np.any(Vc[:, 2] > 50.0):
            return np.zeros((0, 3))
        u, v, _z = self._project(Vc[Vc[:, 2] > 50.0])
        W, H = self.intr["width"], self.intr["height"]
        u0, u1 = int(max(np.nanmin(u), 0)), int(min(np.nanmax(u), W - 1))
        v0, v1 = int(max(np.nanmin(v), 0)), int(min(np.nanmax(v), H - 1))
        if u1 < u0 or v1 < v0:
            return np.zeros((0, 3))
        vs, us = np.mgrid[v0 - v0 % STEP_PX:v1 + 1:STEP_PX,
                          u0 - u0 % STEP_PX:u1 + 1:STEP_PX]
        us, vs = us.ravel(), vs.ravel()
        d = np.stack([(us - self.intr["ppx"]) / self.intr["fx"],
                      (vs - self.intr["ppy"]) / self.intr["fy"],
                      np.ones(len(us))], 1)
        rays = np.concatenate([np.zeros_like(d), d], 1).astype(np.float32)
        th = scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
        hit = np.isfinite(th)
        return d[hit] * th[hit, None]

    def _shares(self, depth, Pc, shifts_c):
        """The drawn points moved by each camera-frame shift. -> (drawn
        pixels with depth, share of them seen), one of each per shift"""
        P = Pc[None, :, :] + shifts_c[:, None, :]
        u, v, z = self._project(P)
        H, W = depth.shape
        ok = (z > 50.0) & (u >= 0) & (u < W) & (v >= 0) & (v < H)
        m = depth[np.where(ok, v, 0).astype(int), np.where(ok, u, 0).astype(int)]
        valid = ok & (m > 0)
        seen = valid & (np.abs(m - z) <= MATCH_MM)
        n = valid.sum(axis=1)
        return n, seen.sum(axis=1) / np.maximum(n, 1)

    def look(self, depth, T_wc, T_base, joints):
        """One arm. -> dict: pixels (drawn, with depth), seen (share at
        its rig place), shift (world mm where it matches best), seen_there,
        verdict ('here', 'off', 'unclear', 'cannot tell')."""
        Pc = self.draw(T_wc, T_base, joints)
        if not len(Pc):
            return {"pixels": 0, "seen": 0.0, "shift": [0.0, 0.0, 0.0],
                    "seen_there": 0.0, "verdict": "cannot tell"}
        R, _t = _cam(T_wc)
        n0, s0 = self._shares(depth, Pc, np.zeros((1, 3)))
        n0, s0 = int(n0[0]), float(s0[0])
        best, best_s = np.zeros(3), s0
        if n0 >= MIN_PIXELS and s0 < SEEN_OK:
            sub = Pc[::max(1, len(Pc) // SEARCH_POINTS)]
            for k in range(1, len(self.shifts), 512):
                chunk = self.shifts[k:k + 512]
                n, s = self._shares(depth, sub, chunk @ R)
                s = np.where(n >= min(MIN_PIXELS, len(sub) // 2), s, -1.0)
                i = int(np.argmax(s))
                if s[i] > best_s + 1e-9:
                    best, best_s = chunk[i], float(s[i])
        if n0 < MIN_PIXELS:
            verdict = "cannot tell"
        elif s0 >= SEEN_OK:
            verdict = "here"
        elif best_s >= s0 + BETTER and best_s >= SEEN_OK * 0.8:
            verdict = "off"
        else:
            verdict = "unclear"
        return {"pixels": n0, "seen": round(s0, 3),
                "shift": [round(float(x), 1) for x in best],
                "seen_there": round(best_s, 3), "verdict": verdict}

    def check(self, depth, T_wc, layout, joints):
        """{arm: joints or None} -> {arm: look()}"""
        return {a: self.look(depth, T_wc, layout[a], j)
                for a, j in joints.items() if j is not None}


def way(shift):
    """World mm -> words: +X toward the camera, +Y the person's left, +Z up."""
    x, y, z = shift
    parts = []
    for v, pos, neg in ((y, "to the person's left", "to the person's right"),
                        (x, "toward the camera", "away from the camera"),
                        (z, "higher", "lower")):
        if abs(v) >= 5.0:
            parts.append(f"{abs(v) / 10:.0f} cm {pos if v > 0 else neg}")
    return ", ".join(parts) or "where it is"


def words(a, r):
    """One arm's result, for the panel."""
    name = NAMES[a % 4]
    if r["verdict"] == "here":
        return f"{name} is where the rig file puts it ({100 * r['seen']:.0f}% seen)"
    if r["verdict"] == "off":
        return (f"**{name} does not look where the rig file puts it**: it matches "
                f"best {way(r['shift'])} of there. Check its base against the rig "
                f"editor")
    if r["verdict"] == "unclear":
        return (f"{name} is only partly seen where expected "
                f"({100 * r['seen']:.0f}%); something may hide it")
    return f"{name}: the camera cannot see it well enough to tell"


# --- the self-test ---------------------------------------------------------------
def _scene_depth(intr, T_wc, wall_x=-900.0):
    """A floor (z = 0) and a wall behind the chair (x = wall_x), as depth."""
    R, t = _cam(T_wc)
    H, W = intr["height"], intr["width"]
    vs, us = np.mgrid[0:H, 0:W]
    d = np.stack([(us - intr["ppx"]) / intr["fx"], (vs - intr["ppy"]) / intr["fy"],
                  np.ones((H, W))], -1)
    dw = d @ R.T
    depth = np.full((H, W), np.inf)
    for axis, value in ((2, 0.0), (0, wall_x)):
        with np.errstate(divide="ignore", invalid="ignore"):
            s = (value - t[axis]) / dw[..., axis]
        depth = np.where((s > 0) & (s < depth), s, depth)
    depth[~np.isfinite(depth)] = 0.0
    return depth


def _paint(sight, depth, T_wc, T_base, joints, rng, holes=0.0, noise=6.0):
    """Put an arm into a depth image (full resolution), nearest wins."""
    import open3d as o3d
    R, t = _cam(T_wc)
    scene = o3d.t.geometry.RaycastingScene()
    for _name, (V, F) in armmesh.posed(*joints[:3], T_world_base=T_base).items():
        scene.add_triangles(o3d.core.Tensor(((V - t) @ R).astype(np.float32)),
                            o3d.core.Tensor(F.astype(np.uint32)))
    intr = sight.intr
    H, W = depth.shape
    vs, us = np.mgrid[0:H, 0:W]
    d = np.stack([(us - intr["ppx"]) / intr["fx"], (vs - intr["ppy"]) / intr["fy"],
                  np.ones((H, W))], -1).reshape(-1, 3)
    rays = np.concatenate([np.zeros_like(d), d], 1).astype(np.float32)
    th = scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy().reshape(H, W)
    hit = np.isfinite(th)
    z = th + rng.normal(0.0, noise, th.shape)
    near = hit & ((depth == 0) | (z < depth))
    depth[near] = z[near]
    if holes:
        depth[hit & (rng.random(th.shape) < holes)] = 0.0
    return depth


def selftest():
    import json
    import arms_live as AL
    import kinematics as K
    rng = np.random.default_rng(3)
    intr = {"width": 1280, "height": 720, "fx": 640.2, "fy": 640.2,
            "ppx": 644.1, "ppy": 365.8}
    # the D455 1.5 m in front of the seat, 99 cm up, 14 degrees down
    c, s = math.cos(math.radians(14)), math.sin(math.radians(14))
    T_wc = np.eye(4)
    T_wc[:3, :3] = np.array([[0.0, s, -c], [1.0, 0.0, 0.0], [0.0, -c, -s]])
    T_wc[:3, 3] = [1500.0, 0.0, 990.0]
    with open(os.path.join(HERE, "live_rig.json"), encoding="utf-8") as f:
        rel = json.load(f)["arms"]
    layout = [AL.base_pose(r) for r in rel]
    poses = [K.ik(250.0, 0.0, 250.0), K.ik(200.0, 60.0, 150.0), K.ik(300.0, -40.0, 320.0)]
    joints = {a: poses[a % 3] for a in range(len(layout))}
    sight = ArmSight(intr)

    def moved(T, dx, dy, dz):
        T = np.array(T, float)
        T[:3, 3] += (dx, dy, dz)
        return T

    # 1. every arm where the rig file puts it
    depth = _scene_depth(intr, T_wc)
    for a in joints:
        depth = _paint(sight, depth, T_wc, layout[a], joints[a], rng, holes=0.1)
    got = sight.check(depth, T_wc, layout, joints)
    for a, r in got.items():
        assert r["verdict"] == "here", (a, r)
    print("  arms where the rig file puts them: "
          + "; ".join(f"{NAMES[a]} {100 * r['seen']:.0f}% seen of {r['pixels']} pixels"
                      for a, r in got.items()))
    # 2. blue's base 4 cm to the person's left, green's 4 cm lower, red's
    # 4 cm toward the camera
    off = {0: (40.0, 0.0, 0.0), 1: (0.0, 40.0, 0.0), 2: (0.0, 0.0, -40.0)}
    depth = _scene_depth(intr, T_wc)
    for a in joints:
        depth = _paint(sight, depth, T_wc, moved(layout[a], *off[a]), joints[a], rng,
                       holes=0.1)
    got = sight.check(depth, T_wc, layout, joints)
    for a, r in got.items():
        assert r["verdict"] == "off", (a, r)
        err = np.linalg.norm(np.array(r["shift"]) - np.array(off[a]))
        assert err <= 15.0, (a, r, off[a])
        print(f"  {NAMES[a]} moved {way(off[a])}: found {way(r['shift'])} "
              f"({100 * r['seen']:.0f}% seen where expected, "
              f"{100 * r['seen_there']:.0f}% there)")
    # 3. an arm that gives no depth (dark parts), and one hidden behind a board
    depth = _scene_depth(intr, T_wc)
    depth = _paint(sight, depth, T_wc, layout[0], joints[0], rng, holes=1.0)
    dark = sight.look(depth, T_wc, layout[0], joints[0])
    assert dark["verdict"] == "cannot tell", dark
    Pc = sight.draw(T_wc, layout[1], joints[1])
    depth = _scene_depth(intr, T_wc)
    depth = _paint(sight, depth, T_wc, layout[1], joints[1], rng)
    u, v, _z = sight._project(Pc)
    board = float(Pc[:, 2].min()) - 150.0
    depth[int(v.min()) - 8:int(v.max()) + 8, int(u.min()) - 8:int(u.max()) + 8] = board
    r = sight.look(depth, T_wc, layout[1], joints[1])
    assert r["verdict"] in ("unclear", "cannot tell") and r["verdict"] != "off", r
    print(f"  an arm with no depth on it: {dark['verdict']}; an arm behind a board: "
          f"{r['verdict']}")
    print("  arm_sight self-test OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
