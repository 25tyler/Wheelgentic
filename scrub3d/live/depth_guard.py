"""scrub3d/live/depth_guard.py -- what the depth camera measured, as a check
on the body model that does not depend on the model.

    python scrub3d/live/depth_guard.py --selftest

Every check the arms make against the body model is only as good as the
model. When the fit is off (a limb placed a few cm wrong, the model a
moment behind a quick move, a hand the model does not have), the model says
clear where the person is. So the live view also keeps what the camera
measured in the frame:

  - Scene: the measured surfaces as points. The arms themselves are taken
    out: each arm, with its sponge, is drawn where it is, and on and next to
    its outline, whatever the camera measured at the arm's depth or behind
    it is taken for the arm (or is hidden by it). Something MATCH_MM or more
    nearer than the arm is in front of it, and stays. The floor and each
    arm's stand are taken out too.
  - RawGuard: how close an arm's moving structure comes to those points. A
    spot counts only with RAW_K points (one stray pixel is not a person).
  - agreement(): for each scrubbed body part, how far its model sits from
    what the camera measured, over the part's cells that face the camera
    and are not hidden.

What arms_live does with them is in its module notes (THE DEPTH GUARD).
"""
import argparse
import math
import os
import sys

import numpy as np
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(HERE)
for p in (WT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import armmesh                                  # noqa: E402
import collide as COL                           # noqa: E402
import kinematics as K                          # noqa: E402

STEP_PX = 6                 # every 6th pixel: about 14 mm apart at 1.5 m
NEAR_MM, FAR_MM = 250.0, 2500.0
MATCH_MM = 20.0             # nearer than an arm drawn there by this: in front of it
FLOOR_MM = 30.0             # points lower than this are the floor
STAND_R_MM = 80.0           # under a base, this far out, is its stand
REACH_PAD_MM = 150.0        # points further than this beyond every arm's reach are dropped
RAW_K = 3                   # a spot is an obstacle with this many points
SAMPLES = 14                # along each capsule, as collide.link_clearance
FACING = 0.4                # cells facing the camera at least this much are compared
HIDDEN_MM = 60.0            # measured this much nearer than a cell: something hides it
BEHIND_MM = 10.0            # this much further than the measured surface: behind it
OFF_MM = 60.0               # measured this much further: the model is not on the person
OWN_HIDE_MM = 25.0          # the model's own surface this far in front of a cell hides it
PERSON_GROW_PX = 4          # the person mask grown this much, for its edge


def person_grid(mask, step=STEP_PX):
    """The person mask as Scene uses it: grown by PERSON_GROW_PX, then every
    `step`-th pixel. -> (H, W) bool, or None"""
    if mask is None:
        return None
    import cv2
    k = 2 * PERSON_GROW_PX + 1
    grown = cv2.dilate(np.asarray(mask, np.uint8), np.ones((k, k), np.uint8))
    return grown[::step, ::step].astype(bool)


def filtered(depth, step=STEP_PX):
    """The depth image as Scene uses it: every other pixel, a median to take
    out lone flying pixels, then every `step`-th pixel. -> (H, W) mm"""
    import cv2
    d = np.clip(np.nan_to_num(np.asarray(depth, np.float32)), 0, 65535)[::2, ::2]
    half = max(1, step // 2)
    return cv2.medianBlur(d.astype(np.uint16), 5).astype(np.float32)[::half, ::half]


_LINKS = {}


def link_scenes():
    """Each arm link's mesh, indexed for ray casting once, in its own frame."""
    if not _LINKS:
        import open3d as o3d
        for name, (V, F) in armmesh.meshes().items():
            sc = o3d.t.geometry.RaycastingScene()
            sc.add_triangles(o3d.core.Tensor(np.asarray(V, np.float32)),
                             o3d.core.Tensor(np.asarray(F).astype(np.uint32)))
            _LINKS[name] = (sc, (np.asarray(V).min(0), np.asarray(V).max(0)))
    return {k: v[0] for k, v in _LINKS.items()}


def link_boxes():
    link_scenes()
    return {k: v[1] for k, v in _LINKS.items()}


class Scene:
    """One frame's measured surfaces, the arms taken out.

    grid: the filtered depth on the STEP_PX grid, mm, 0 where unknown (no
    depth, or an arm). P: the kept points, world mm. T_wc, intr: the camera.
    """

    def __init__(self, depth, intr, T_wc, layout=(), joints=None, sponges=None,
                 step=STEP_PX, grid=None, person=None):
        """depth: the camera's image, mm; or grid: filtered() of it, already.
        person: person_grid() of the segmentation mask, or None; each kept
        point is then marked as in it or not (in_person)."""
        self.intr, self.step = intr, step
        self.T_wc = np.asarray(T_wc, float)
        R, t = self.T_wc[:3, :3], self.T_wc[:3, 3]
        g = (filtered(depth, step) if grid is None
             else np.array(grid, np.float32, copy=True))
        H, W = g.shape
        vs, us = np.mgrid[0:H, 0:W]
        self.us, self.vs = us * step, vs * step
        ray = np.stack([(self.us - intr["ppx"]) / intr["fx"],
                        (self.vs - intr["ppy"]) / intr["fy"], np.ones((H, W))], -1)
        ok = (g > NEAR_MM) & (g < FAR_MM)
        g = np.where(ok, g, 0.0)
        self.arm = np.zeros((H, W), bool)
        if len(layout) and joints is not None:
            drawn = self._draw(ray, layout, joints, sponges, R, t)
            # The nearest arm surface on or next to each pixel (one grid step).
            dp = np.pad(drawn, 1, constant_values=np.inf)
            near = np.full_like(drawn, np.inf)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    near = np.minimum(near, dp[1 + di:H + 1 + di, 1 + dj:W + 1 + dj])
            self.arm = (g > 0) & np.isfinite(near) & (g >= near - MATCH_MM)
            self.grid_all = g                     # the arms still in it
            g = np.where(self.arm, 0.0, g)
        else:
            self.grid_all = g
        self.grid = g
        keep = g > 0
        Pc = ray[keep] * g[keep][:, None]
        Pw = Pc @ R.T + t
        mask = (np.zeros(len(Pw), bool) if person is None
                else np.asarray(person, bool)[keep])
        sel = Pw[:, 2] > FLOOR_MM
        for T in layout:
            T = np.asarray(T, float)
            b = T[:3, 3]
            under = (np.hypot(Pw[:, 0] - b[0], Pw[:, 1] - b[1]) < STAND_R_MM) & \
                (Pw[:, 2] < b[2] + 20.0)
            sel &= ~under
        if len(layout):
            reach = COL.arm_bounding_sphere(np.eye(4))[1] + REACH_PAD_MM
            near = np.zeros(len(Pw), bool)
            for T in layout:
                near |= np.linalg.norm(Pw - np.asarray(T, float)[:3, 3], axis=1) < reach
            sel &= near
        self.P = Pw[sel].astype(np.float32)
        self.in_person = mask[sel]
        self.has_person = person is not None

    def _draw(self, ray, layout, joints, sponges, R, t):
        """The arms drawn on the grid, sponges included. -> depth of the
        nearest arm surface, (H, W) mm, inf where none

        Each link's mesh is indexed once, in its own frame (link_scenes);
        the rays are moved into each link's frame instead."""
        import open3d as o3d
        scenes = link_scenes()
        H, W = ray.shape[:2]
        flat = ray.reshape(-1, 3)
        rr2 = (flat * flat).sum(1)
        depth = np.full(H * W, np.inf)
        T_cw = np.eye(4)
        T_cw[:3, :3], T_cw[:3, 3] = R.T, -R.T @ t
        for a, T in enumerate(layout):
            j = joints[a] if not isinstance(joints, dict) else joints.get(a)
            if j is None:
                continue
            j = tuple(float(v) for v in tuple(j)[:3])
            tf = armmesh.link_transforms(*j, T_world_base=T)
            tcp = (np.asarray(T, float) @ np.r_[K.fk(*j), 1.0])[:3]
            if sponges is not None and sponges.get(a) is not None:
                tcp = np.asarray(sponges[a], float)
            c = (tcp - t) @ R
            # Where this arm can be in the image: its links' boxes and the ball.
            corners = [c + np.array([[dx, dy, dz] for dx in (-1, 1) for dy in (-1, 1)
                                     for dz in (-1, 1)]) * COL.R_SPONGE]
            poses = {}
            for name, (lo, hi) in link_boxes().items():
                M = T_cw @ tf[name]
                poses[name] = M
                box8 = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                                 for z in (lo[2], hi[2])])
                corners.append(box8 @ M[:3, :3].T + M[:3, 3])
            sel = np.flatnonzero(self._bbox(np.concatenate(corners), H, W).ravel())
            if not len(sel):
                continue
            d = flat[sel]
            for name, M in poses.items():
                Rl, tl = M[:3, :3], M[:3, 3]
                o = np.broadcast_to(-Rl.T @ tl, d.shape)
                rays = np.concatenate([o, d @ Rl], 1).astype(np.float32)
                th = scenes[name].cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
                depth[sel] = np.minimum(depth[sel], th)
            # The ray p = s * r meets the ball |p - c| = R_SPONGE where
            # (r.r) s^2 - 2 (r.c) s + c.c - R^2 = 0; s is depth, as r_z = 1.
            b = d @ c
            disc = b * b - rr2[sel] * (c @ c - COL.R_SPONGE ** 2)
            hit = disc >= 0.0
            s0 = np.where(hit, (b - np.sqrt(np.where(hit, disc, 0.0))) / rr2[sel], np.inf)
            depth[sel] = np.minimum(depth[sel], s0)
        return depth.reshape(H, W)

    def _bbox(self, Pc, H, W):
        """The grid pixels inside the image box of camera-frame points (a
        pixel's margin round it). -> (H, W) bool"""
        out = np.zeros((H, W), bool)
        front = Pc[Pc[:, 2] > 1.0]
        if not len(front):
            return out
        u = front[:, 0] / front[:, 2] * self.intr["fx"] + self.intr["ppx"]
        v = front[:, 1] / front[:, 2] * self.intr["fy"] + self.intr["ppy"]
        j0 = int(max(0, np.floor(u.min() / self.step) - 1))
        j1 = int(min(W - 1, np.ceil(u.max() / self.step) + 1))
        i0 = int(max(0, np.floor(v.min() / self.step) - 1))
        i1 = int(min(H - 1, np.ceil(v.max() / self.step) + 1))
        if j1 >= j0 and i1 >= i0:
            out[i0:i1 + 1, j0:j1 + 1] = True
        return out

    def behind(self, Pw, everything=False):
        """How far each world point is behind the surface the camera
        measured along its own ray, mm (negative: in front of it; -inf where
        the camera measured nothing there).
        -> (N,)

        A point in front of the measured surface is in space the camera has
        seen through. Behind it, the point is inside what the camera measured
        or in its shadow, whatever the model says. Distance to the measured
        POINTS cannot tell the two apart: deep inside a thigh, the nearest
        measured point is far away again.
        """
        m, z = self.lookup(Pw, everything)
        return np.where(m > 0.0, z - m, -np.inf)

    def lookup(self, Pw, everything=False):
        """Measured depth where world points project, and their own depth.
        `everything`: the arms left in, so that space an arm itself hides
        counts as measured (for choosing where to wait).
        -> (measured mm, 0 where unknown or off the image; point depth mm)"""
        R, t = self.T_wc[:3, :3], self.T_wc[:3, 3]
        Pc = (np.asarray(Pw, float) - t) @ R
        z = Pc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = Pc[:, 0] / z * self.intr["fx"] + self.intr["ppx"]
            v = Pc[:, 1] / z * self.intr["fy"] + self.intr["ppy"]
        gi = np.round(v / self.step)
        gj = np.round(u / self.step)
        grid = self.grid_all if everything else self.grid
        H, W = grid.shape
        ok = (z > 0) & (gi >= 0) & (gi < H) & (gj >= 0) & (gj < W)
        m = np.zeros(len(Pc))
        m[ok] = grid[gi[ok].astype(int), gj[ok].astype(int)]
        return m, z


class RawGuard:
    """Clearances to the measured points."""

    def __init__(self, P):
        P = np.asarray(P, float).reshape(-1, 3)
        self.n = len(P)
        self.tree = cKDTree(P) if self.n >= RAW_K else None

    def _near(self, pts):
        """Distance to the RAW_K-th nearest measured point, per point."""
        if self.tree is None:
            return np.full(len(pts), np.inf)
        d, _ = self.tree.query(pts, k=RAW_K)
        return d[:, -1]

    def clearances(self, T_base, joints):
        """Each moving part (collide.MOVING) to the measured points, mm."""
        if self.tree is None:
            return np.full(len(COL.MOVING), np.inf)
        caps = COL.moving_capsules(T_base, *tuple(joints)[:3])
        t = np.linspace(0.0, 1.0, SAMPLES)[:, None]
        pts = np.concatenate([p + (q - p) * t for p, q in caps])
        d = self._near(pts).reshape(len(caps), SAMPLES).min(axis=1)
        return d - np.asarray(COL.CAPSULE_RADII)[list(COL.MOVING)]

    def clearance(self, T_base, joints):
        """The nearest of them, mm."""
        if self.tree is None:
            return float("inf")
        return float(self.clearances(T_base, joints).min())

    def sponge_gap(self, p):
        """The sponge's surface to the measured points, mm (negative: in them)."""
        return float(self._near(np.asarray(p, float)[None])[0]) - COL.R_SPONGE


def agreement(scene, P, N, I, parts, mesh=None):
    """How far each scrubbed part's model sits from the measured surface.
    `mesh`: the posed model as one raycasting scene; cells the person's own
    body hides from the camera (a forearm across the chest) are then left
    out, since what the camera measures there is the forearm, not the chest.
    -> {part: (cells compared, median of measured minus model depth mm,
    share of them where the camera sees nothing within OFF_MM behind)}"""
    cam = scene.T_wc[:3, 3]
    v = cam - P
    dist = np.linalg.norm(v, axis=1)
    facing = np.einsum("ij,ij->i", N, v) / np.maximum(dist, 1e-9) > FACING
    m, z = scene.lookup(P)
    e = m - z
    seen = facing & (m > 0) & (e > -HIDDEN_MM)
    if mesh is not None and seen.any():
        import open3d as o3d
        idx = np.flatnonzero(seen)
        d = v[idx] / np.maximum(dist[idx], 1e-9)[:, None]
        rays = np.concatenate([np.broadcast_to(cam, d.shape), -d], 1).astype(np.float32)
        hit = mesh.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy()
        # The first surface along the ray is nearer than the cell by more
        # than the model's own thickness there: something of them is in front.
        seen[idx[np.isfinite(hit) & (hit < dist[idx] - OWN_HIDE_MM)]] = False
    out = {}
    for i in range(parts):
        s = seen & (I == i)
        n = int(s.sum())
        if not n:
            out[i] = (0, 0.0, 0.0)
            continue
        off = e[s] > OFF_MM
        near = e[s][~off]
        out[i] = (n, float(np.median(near)) if len(near) else float("nan"),
                  float(off.mean()))
    return out


# --- the self-test ---------------------------------------------------------------
def selftest():
    import json
    import time
    import open3d as o3d
    import arm_sight as S
    import arms_live as AL
    rng = np.random.default_rng(7)
    intr = {"width": 1280, "height": 720, "fx": 640.2, "fy": 640.2,
            "ppx": 644.1, "ppy": 365.8}
    c, s = math.cos(math.radians(14)), math.sin(math.radians(14))
    T_wc = np.eye(4)
    T_wc[:3, :3] = np.array([[0.0, s, -c], [1.0, 0.0, 0.0], [0.0, -c, -s]])
    T_wc[:3, 3] = [1500.0, 0.0, 990.0]
    R, t = T_wc[:3, :3], T_wc[:3, 3]
    with open(os.path.join(HERE, "live_rig.json"), encoding="utf-8") as f:
        rel = json.load(f)["arms"]
    layout = [AL.base_pose(r) for r in rel]
    sight = S.ArmSight(intr)
    j_red = K.ik(200.0, 0.0, 250.0)
    joints = {0: j_red, 1: K.ik(120.0, 0.0, 300.0), 2: K.ik(120.0, 0.0, 300.0)}
    H, W = intr["height"], intr["width"]
    vs, us = np.mgrid[0:H, 0:W]
    rays = np.stack([(us - intr["ppx"]) / intr["fx"], (vs - intr["ppy"]) / intr["fy"],
                     np.ones((H, W))], -1).reshape(-1, 3)
    rays = np.concatenate([np.zeros_like(rays), rays], 1).astype(np.float32)

    def paint(depth, mesh):
        """Put a world-frame open3d mesh into the depth image."""
        sc = o3d.t.geometry.RaycastingScene()
        Vc = (np.asarray(mesh.vertices) - t) @ R
        sc.add_triangles(o3d.core.Tensor(Vc.astype(np.float32)),
                         o3d.core.Tensor(np.asarray(mesh.triangles).astype(np.uint32)))
        th = sc.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy().reshape(H, W)
        hit = np.isfinite(th) & ((depth == 0) | (th < depth))
        depth[hit] = th[hit] + rng.normal(0.0, 4.0, int(hit.sum()))
        return depth

    # A floor, a wall, a "person" (an upright cylinder at the seat), the
    # three arms, and a hand-sized block 30 mm beside red's forearm.
    depth = S._scene_depth(intr, T_wc)
    cyl = o3d.geometry.TriangleMesh.create_cylinder(radius=160.0, height=700.0,
                                                    resolution=40, split=4)
    cyl.translate((0.0, 0.0, 850.0))
    depth = paint(depth, cyl)
    for a, j in joints.items():
        depth = S._paint(sight, depth, T_wc, layout[a], j, rng, noise=4.0)

    def box_points(lo, size=60.0, step=5.0):
        g = np.arange(0.0, size + 1e-6, step)
        A, B = np.meshgrid(g, g)
        A, B = A.ravel(), B.ravel()
        faces = []
        for axis in range(3):
            for side in (0.0, size):
                f = np.zeros((len(A), 3))
                f[:, axis] = side
                f[:, (axis + 1) % 3] = A
                f[:, (axis + 2) % 3] = B
                faces.append(f)
        return np.concatenate(faces) + lo

    def true_clearance(T, j, pts):
        """Moving structure to the block's whole surface, finely sampled."""
        caps = COL.moving_capsules(T, *j)
        tt = np.linspace(0.0, 1.0, 60)[:, None]
        d, _ = cKDTree(pts).query(np.concatenate([p + (q - p) * tt for p, q in caps]))
        d = d.reshape(len(caps), 60).min(axis=1)
        return float((d - np.asarray(COL.CAPSULE_RADII)[list(COL.MOVING)]).min())

    # A block beside red's forearm, along the normal of the arm's plane,
    # 25 mm clear of red's moving structure.
    T_red = np.asarray(layout[0], float)
    caps = COL.moving_capsules(T_red, *j_red)
    at = caps[1][0] + 0.3 * (caps[1][1] - caps[1][0])
    normal = T_red[:3, :3] @ np.array([0.0, 1.0, 0.0])
    lo = None
    for off in np.arange(250.0, -150.0, -1.0):
        cand = (at + normal * off - 30.0 + np.where(normal > 0.5, 30.0, 0.0)
                + np.where(normal < -0.5, -30.0, 0.0))
        if true_clearance(T_red, j_red, box_points(cand)) <= 25.0:
            lo = cand
            break
    assert lo is not None
    block_pts = box_points(lo)
    block = o3d.geometry.TriangleMesh.create_box(60.0, 60.0, 60.0)
    block.translate(lo)
    depth = paint(depth, block)
    t0 = time.perf_counter()
    sc = Scene(depth, intr, T_wc, layout, joints)
    took = time.perf_counter() - t0
    assert not np.any(sc.P[:, 2] < FLOOR_MM), "floor kept"
    # Each arm's own surface is taken out; the block is not.
    tree = cKDTree(sc.P)
    for a, j in joints.items():
        V = np.concatenate([v for v, _f in armmesh.posed(*j, T_world_base=layout[a]).values()])
        left = int((tree.query(V, distance_upper_bound=6.0)[0] < 6.0).sum())
        assert left < 0.01 * len(V), (a, left, len(V))
    on_block = (np.abs(sc.P - (lo + 30.0)) <= 31.0).all(axis=1).sum()
    assert on_block >= 10, on_block
    guard = RawGuard(sc.P)
    for a in (1, 2):
        assert guard.clearance(layout[a], joints[a]) > 50.0
    print(f"  the arms are taken out of the camera's points; a block beside red is "
          f"kept ({len(sc.P)} points, {1000 * took:.0f} ms)")
    # Red's next move, toward the block (the whole arm moved over): the
    # camera's points, taken with red where it is, see it go in.
    rows = []
    for step in range(0, 61, 10):
        T_next = T_red.copy()
        T_next[:3, 3] += normal * step
        rows.append((step, true_clearance(T_next, j_red, block_pts),
                     guard.clearance(T_next, j_red)))
    for step, true_cl, seen_cl in rows:
        print(f"  red {step} mm nearer: {true_cl:+.0f} mm from the block; the "
              f"camera's points say {seen_cl:+.0f}")
        assert seen_cl >= true_cl - 8.0, rows          # never much nearer than it is
        assert true_cl > -10.0 or seen_cl < 0.0, rows  # well inside is seen inside
    # A model on the "person" agrees with the camera; one that is off does not.
    ang = np.linspace(-1.2, 1.2, 60)
    A, Z = np.meshgrid(ang, np.linspace(600.0, 1100.0, 12))
    Pc = np.stack([160.0 * np.cos(A.ravel()), 160.0 * np.sin(A.ravel()), Z.ravel()], 1)
    Nc = np.stack([np.cos(A.ravel()), np.sin(A.ravel()), np.zeros(A.size)], 1)
    Ic = np.zeros(len(Pc), int)
    n, med, off = agreement(sc, Pc, Nc, Ic, 1)[0]
    assert n > 100 and abs(med) < 8.0 and off < 0.05, (n, med, off)
    print(f"  a model on the person: {n} cells compared, {med:+.0f} mm, "
          f"{100 * off:.0f}% off it")
    for shift, label in (((30.0, 0.0, 0.0), "3 cm toward the camera"),
                         ((-30.0, 0.0, 0.0), "3 cm away from it"),
                         ((0.0, 0.0, 250.0), "25 cm higher, past its top"),
                         ((0.0, 120.0, 0.0), "12 cm to the side")):
        n, med, off = agreement(sc, Pc + np.array(shift), Nc, Ic, 1)[0]
        print(f"  a model {label}: {n} cells, {med:+.0f} mm, {100 * off:.0f}% off it")
        assert abs(med) > 20.0 or off > 0.25, (label, n, med, off)
    print("  depth_guard self-test OK")


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
