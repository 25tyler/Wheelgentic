"""scrub3d/bodymodel.py -- the body as a set of surface cells, plus a synthetic
body so every downstream stage is testable with no camera and no person.

WHAT A BODY MODEL IS HERE
--------------------------
Not a mesh. A set of SURFACE CELLS, each carrying a position, an outward
normal, an area, and a region label. That is everything the partition and the
coverage controller need, and it is what a depth scan reduces to anyway.

Real models come from scrub3d/scan.py (a front-only RealSense capture). The
synthetic model below produces the same structure from cylinders, so the
partition, the collision layer and the coverage controller can all be developed
and regression-tested before the camera arrives. This is the same reasoning the
project already applies with CAM=fake, and py/fakecam.py:65 already models the
figure as a list of (a, b, thickness) segments -- that is a capsule list, which
is exactly what is needed here.

FRONT ONLY
-----------
We reconstruct and scrub the front. Cells whose outward normal faces away from
the camera are never created, so the model cannot describe surface it never
measured. That is an honest constraint, not a limitation to work around: we do
not scrub what we did not see.

POSE
----
Cells live in a REGION-LOCAL frame and each region carries a 4x4. Re-posing the
body is one matrix per region, not skinning thousands of vertices -- and it is
exact, because within a region the surface is rigid by construction. Move a
limb and its cells move with it; nothing needs recomputing.
"""
import math
from dataclasses import dataclass, field

import numpy as np

try:
    from . import frames as FRAME
except ImportError:
    import frames as FRAME


@dataclass
class Region:
    """One anatomical patch: a bone-aligned cylinder sampled into cells.

    Cells are stored in the region's own frame. `T` maps them to world. To
    re-pose the body you write `T` and nothing else.
    """
    name: str
    T: np.ndarray                    # 4x4 region -> world
    pts: np.ndarray                  # (N,3) cell centres, region frame
    nrm: np.ndarray                  # (N,3) outward unit normals, region frame
    area: np.ndarray                 # (N,) mm^2 represented by each cell
    scrubbable: np.ndarray           # (N,) bool: policy + observation mask
    # The angular span the cells cover, and the superellipse exponent they were
    # built with. Carried rather than inferred: the normals self-test used to
    # guess both from the region's NAME, which worked only while every region
    # was a limb or the torso and broke the moment a full-wrap neck appeared.
    arc: tuple = (-math.pi / 2, math.pi / 2)
    n_exp: float = 2.0

    def world(self):
        """-> (points, normals) in world mm."""
        R, t = self.T[:3, :3], self.T[:3, 3]
        # errstate, and it is NOT hiding a numerical problem. numpy 2.1.3 on
        # Apple's Accelerate BLAS raises divide-by-zero, overflow AND invalid
        # on matmul shapes like (3,3)@(3,N) while returning the exactly right
        # answer: np.ones((3,3)) @ np.ones((3,448)) warns three times and is
        # still all 3.0. Measured here, and measured clean on the Linux box,
        # same numpy 2.1.3 against OpenBLAS -- so it is the BLAS, not the data.
        # This is the hottest geometry call in the package (every region, every
        # solve), so unsuppressed it buries a REAL warning in hundreds of fake
        # ones. Suppressing the flag does not hide genuine trouble: bad input
        # still puts nan/inf in the returned array, where every caller sees it.
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            return (R @ self.pts.T).T + t, (R @ self.nrm.T).T

    @property
    def n(self):
        return len(self.pts)


@dataclass
class BodyModel:
    regions: list = field(default_factory=list)

    def world_cells(self, only_scrubbable=True):
        """-> (points, normals, region_index, area) concatenated across regions."""
        P, N, I, A = [], [], [], []
        for i, r in enumerate(self.regions):
            p, n = r.world()
            m = r.scrubbable if only_scrubbable else np.ones(r.n, bool)
            P.append(p[m]); N.append(n[m])
            I.append(np.full(int(m.sum()), i))
            A.append(r.area[m])
        if not P:
            return (np.zeros((0, 3)), np.zeros((0, 3)),
                    np.zeros(0, int), np.zeros(0))
        return (np.vstack(P), np.vstack(N), np.concatenate(I),
                np.concatenate(A))

    def total_area(self, only_scrubbable=True):
        return float(self.world_cells(only_scrubbable)[3].sum())


def _cylinder_region(name, T, length, r0, r1, n_ax=14, n_th=20,
                     theta_span=(-math.pi / 2, math.pi / 2), scrubbable=True):
    """A tapered cylinder sampled into cells, local +Z along the bone axis.

    theta_span limits the arc to the part a front-mounted camera can see and a
    front-mounted arm can reach. theta=0 points along local +X, which we aim
    anteriorly, so the default is the front half.
    """
    s = (np.arange(n_ax) + 0.5) / n_ax
    th = np.linspace(theta_span[0], theta_span[1], n_th)
    S, TH = np.meshgrid(s, th, indexing="ij")
    rad = r0 + (r1 - r0) * S

    pts = np.stack([rad * np.cos(TH), rad * np.sin(TH), S * length], -1)
    nrm = np.stack([np.cos(TH), np.sin(TH), np.zeros_like(TH)], -1)

    d_ax = length / n_ax
    d_th = (theta_span[1] - theta_span[0]) / max(n_th - 1, 1)
    area = (rad * d_th * d_ax).ravel()

    return Region(name=name, T=np.asarray(T, float),
                  pts=pts.reshape(-1, 3), nrm=nrm.reshape(-1, 3),
                  area=area,
                  scrubbable=np.full(pts.reshape(-1, 3).shape[0], scrubbable),
                  arc=tuple(theta_span), n_exp=2.0)


def _pose(x, y, z, rx=0.0, ry=0.0, rz=0.0):
    """Translation plus intrinsic X then Y then Z rotation. 4x4."""
    cx, sx, cy, sy, cz, sz = (math.cos(rx), math.sin(rx), math.cos(ry),
                              math.sin(ry), math.cos(rz), math.sin(rz))
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    T = np.eye(4)
    T[:3, :3] = Rz @ Ry @ Rx
    T[:3, 3] = (x, y, z)
    return T


def synthetic_body(shoulder_w=380.0, upper_len=280.0, fore_len=260.0,
                   upper_r=48.0, fore_r=42.0, seat_z=1050.0,
                   arm_drop=0.55, arm_out=0.25, scale=1.0):
    """A seated upper body facing +X, built from cylinders.

    Deliberately parameterised: `scale` and the limb lengths let a test build a
    DIFFERENT PERSON, which is how we prove the controller is not tuned to one
    body. Sitting at the origin, facing +X (the camera and arms are in front).

    arm_drop / arm_out pose the upper arms, so a test can also prove the same
    controller copes with a DIFFERENT POSE without re-tuning.
    """
    sw = shoulder_w * scale
    ul, fl = upper_len * scale, fore_len * scale
    ur, fr = upper_r * scale, fore_r * scale
    body = BodyModel()

    # Torso: a wide shallow cylinder, front arc only. Chest is excluded from
    # scrubbing by policy (dignity, and the highest-flinch region), so it is
    # present as an OBSTACLE and for tracking, but not scrubbable.
    body.regions.append(_cylinder_region(
        "torso", _pose(0, 0, seat_z - 280.0, rx=0.0), 380.0 * scale,
        150.0 * scale, 170.0 * scale, n_ax=12, n_th=18,
        theta_span=(-math.pi / 2.2, math.pi / 2.2), scrubbable=False))

    for side, sgn in (("L", +1.0), ("R", -1.0)):
        sh_y = sgn * sw / 2.0
        # Upper arm hangs down and slightly out from the shoulder. The Euler
        # pose sets where the bone points; region_pose sets the roll about it,
        # which is what puts the scrubbable arc on the FRONT of the limb.
        aim = _pose(0.0, sh_y, seat_z,
                    rx=sgn * arm_out, ry=math.pi - arm_drop)
        T_up = FRAME.region_pose(aim[:3, 3], aim[:3, :3] @ np.array([0.0, 0.0, 1.0]))
        body.regions.append(_cylinder_region(
            f"upper_arm_{side}", T_up, ul, ur, ur * 0.88))

        # Forearm continues from the elbow, angled forward.
        el = (T_up[:3, :3] @ np.array([0, 0, ul])) + T_up[:3, 3]
        aim = _pose(el[0], el[1], el[2],
                    rx=sgn * arm_out * 0.5, ry=math.pi - arm_drop - 0.75)
        T_fo = FRAME.region_pose(el, aim[:3, :3] @ np.array([0.0, 0.0, 1.0]))
        body.regions.append(_cylinder_region(
            f"forearm_{side}", T_fo, fl, fr, fr * 0.8))

    return body


def repose(body, **region_transforms):
    """Return a copy with the named regions moved. Cells are NOT recomputed.

    This is the whole point of region-local storage: `repose(b,
    forearm_L=T_new)` moves that limb exactly, in one matrix write. Used by the
    adaptation tests to move a person without touching any surface data.
    """
    out = BodyModel()
    for r in body.regions:
        T = region_transforms.get(r.name, r.T)
        # arc and n_exp must be carried, not defaulted. They describe how the
        # cells were BUILT, so a reposed region that forgets them is a region
        # whose self-checks quietly start testing a different shape.
        out.regions.append(Region(r.name, np.asarray(T, float), r.pts, r.nrm,
                                  r.area, r.scrubbable, r.arc, r.n_exp))
    return out


if __name__ == "__main__":
    b = synthetic_body()
    print("synthetic body")
    for r in b.regions:
        p, _ = r.world()
        tag = "scrub" if r.scrubbable.any() else "OBSTACLE"
        print(f"  {r.name:14s} {r.n:4d} cells  {tag:8s} "
              f"centre=({p[:, 0].mean():7.1f},{p[:, 1].mean():7.1f},"
              f"{p[:, 2].mean():7.1f})  area={r.area.sum() / 100:7.1f}cm2")
    P, N, I, A = b.world_cells()
    print(f"  scrubbable: {len(P)} cells, {A.sum() / 100:.0f} cm2")

    # Normals must point outward, i.e. away from each region's own axis.
    for r in b.regions:
        radial = r.pts.copy()
        radial[:, 2] = 0.0
        nn = r.nrm.copy()
        nn[:, 2] = 0.0
        dot = (radial * nn).sum(1) / (np.linalg.norm(radial, axis=1) + 1e-9)
        assert dot.min() > 0.99, f"{r.name}: normals are not outward"
    print("  normals outward: ok")

    big = synthetic_body(scale=1.25)
    print(f"  a 25% larger person: {big.total_area() / 100:.0f} cm2 "
          f"vs {b.total_area() / 100:.0f} cm2")
    print("OK")
