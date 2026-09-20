"""The person, live, with a body model measured while it runs, and the four
arms working on it. Shown in Rerun.

    python scrub3d/live/live_body.py --seat-mm 420           # the camera
    python scrub3d/live/live_body.py --replay DIR            # a recording
    python scrub3d/live/live_body.py --dump DIR --dump-delay 15

Every frame, from a RealSense D455 (--upside-down if it is mounted that way;
a recording keeps its frames the right way up):
  - MediaPipe finds the joints and the person's outline (one pass, half size).
  - The world frame comes from the floor, fitted live, and is refitted only
    when the camera has really moved.
  - Bone lengths and body widths are measured where the frame shows them
    clearly. Each is a running median, blended from a typical adult's value
    while there are few samples.
  - The model is posed from the joints themselves. MediaPipe says where each
    joint is in the image, the depth says how far, and the bone lengths plus
    the depth along each limb settle the rest. The torso is fitted to its own
    front surface, the head to the face.
  - The chair is placed once, from the first seconds of sitting, under the
    upper back.
  - The arms (arms_live.py) plan on that model and scrub it, every move
    checked first by the project's fleet governor. Nothing talks to hardware.
  - The arms stand where the rig file puts them (--rig, live_rig.json by
    default). Saving it again, from rig_editor.py or by hand, moves them
    and plans again while the view runs.

With --diag DIR it also prints how far each model part stands from the live
surface, and saves side views and camera overlays: see diag().
"""
import argparse
import collections
import json
import math
import os
import queue
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(HERE)          # scrub3d/: its modules import by bare name
for p in (WT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import cv2                                      # noqa: E402
import mediapipe as mp                          # noqa: E402
import numpy as np                              # noqa: E402
import pyrealsense2 as rs                       # noqa: E402
import rerun as rr                              # noqa: E402
import rerun.blueprint as rrb                   # noqa: E402

import anatomy as AN                            # noqa: E402
import armmesh                                  # noqa: E402
import bodymodel as BM                          # noqa: E402
import collide as COL                           # noqa: E402
import control as CTL                           # noqa: E402
import frames as FRAME                          # noqa: E402
import kinematics as K                          # noqa: E402
import partition as PART                        # noqa: E402
import place_arms as PA                         # noqa: E402
import record as REC                            # noqa: E402
import rigconfig                                # noqa: E402
import viz as VIZ                               # noqa: E402
from arms_live import Arms, LOST_S              # noqa: E402

MAX_LEAN_DEG = 25.0   # a seated torso stays this close to upright
LM = {"nose": 0, "l_eye": 2, "r_eye": 5, "l_ear": 7, "r_ear": 8,
      "l_shoulder": 11, "r_shoulder": 12, "l_elbow": 13, "r_elbow": 14,
      "l_wrist": 15, "r_wrist": 16, "l_pinky": 17, "r_pinky": 18,
      "l_index": 19, "r_index": 20, "l_hip": 23, "r_hip": 24,
      "l_knee": 25, "r_knee": 26, "l_ankle": 27, "r_ankle": 28}
SEEN = 0.5            # MediaPipe visibility to use a joint at all
TRUSTED = 0.75        # ...and to measure from it
PRIOR_WEIGHT = 8.0    # a typical adult counts as this many frames
SMOOTH = 0.45         # smoothing of joints, per frame
REBUILD_S = 1.0
DEPTH_STEP_MM = 45.0  # a limb ends where what is behind it begins
TORSO_STEP_MM = 120.0  # the torso ends where the chair or the wall begins
ANKLE_Z = 75.0        # ankle joint above the floor, feet flat
PELVIS_MM = 90.0      # hip joints above what a seated person sits on
SEAT_SAMPLES = 25     # frames that decide where the seat is; then it is fixed
SEAT_SEEN_MM = (200.0, 800.0)  # a seat the camera reads outside this is not one
# The chair goes under the upper back, following MediaPipe's own lean up to
# this much. The chest's front surface is no guide to where the seat is: a
# seated front slopes back from the belly to the chest (17 degrees on the
# recording, where MediaPipe says upright), which put the chair 15 cm forward.
SEAT_LEAN_DEG = 15.0
NOSE_TO_HEAD_MM = 100.0  # the head's axis sits this far behind the nose tip
HEAD_AXIS_NOSE_MM = 95.0  # ...and the nose this far up the head from its base
LENGTH_SLACK = 0.2    # a joint's depth is believed while the bone stays this true
HAND_FLAT = 0.55      # a hand is about half as thick as the wrist is deep
HAND_PER_LANDMARK = 1.9  # MediaPipe's index and pinky points sit mid-hand
SKIN = (212, 178, 156)
CHAIR = (72, 76, 88)
PATCH_RGB = {"torso": (176, 118, 236),
             "upper_arm_L": (74, 160, 232), "forearm_L": (108, 199, 122),
             "upper_arm_R": (232, 176, 74), "forearm_R": (232, 92, 74)}
# The parts the arms scrub, in the order their cells are planned and counted.
SCRUB_PARTS = ("torso", "upper_arm_L", "forearm_L", "upper_arm_R", "forearm_R")
UNOWNED_RGB = (96, 96, 108)
BONES = [("l_shoulder", "r_shoulder"), ("l_shoulder", "l_elbow"),
         ("l_elbow", "l_wrist"), ("r_shoulder", "r_elbow"), ("r_elbow", "r_wrist"),
         ("l_shoulder", "l_hip"), ("r_shoulder", "r_hip"), ("l_hip", "r_hip"),
         ("l_hip", "l_knee"), ("l_knee", "l_ankle"), ("r_hip", "r_knee"),
         ("r_knee", "r_ankle")]


# The live surface: every STEP-th depth pixel of the person, meshed.
STEP = 3                  # every 3rd pixel of the aligned depth makes the surface
EDGE_MM = 35.0            # neighbours further apart than this are not one surface
NEARER_MM, BEHIND_MM = 750.0, 380.0   # shell.py's subject band around the torso


def surface(depth_r, color_r, mask_r, intr, T_wc):
    """The person's live surface. -> (verts, faces, normals, colours) or None."""
    z = depth_r[::STEP, ::STEP]
    H, W = z.shape
    rows = np.arange(H, dtype=np.float32)[:, None] * STEP
    cols = np.arange(W, dtype=np.float32)[None, :] * STEP
    ok = (z > 200.0) & mask_r[::STEP, ::STEP]
    if ok.sum() < 300:
        return None
    zc = float(np.median(z[ok]))
    ok &= (z > zc - NEARER_MM) & (z < zc + BEHIND_MM)
    rs_, cs_ = np.flatnonzero(ok.any(1)), np.flatnonzero(ok.any(0))
    if len(rs_) < 3 or len(cs_) < 3:
        return None
    r0, r1, c0, c1 = rs_[0], rs_[-1] + 1, cs_[0], cs_[-1] + 1
    z, ok = z[r0:r1, c0:c1], ok[r0:r1, c0:c1]
    rows, cols = rows[r0:r1], cols[:, c0:c1]
    H, W = z.shape
    x = (cols - intr["ppx"]) * z / intr["fx"]
    y = (rows - intr["ppy"]) * z / intr["fy"]
    Pc = np.stack([x, np.broadcast_to(y, z.shape), z], -1).reshape(-1, 3)
    P = Pc @ T_wc[:3, :3].T.astype(np.float32) + T_wc[:3, 3].astype(np.float32)

    za, zb, zc_, zd = z[:-1, :-1], z[:-1, 1:], z[1:, :-1], z[1:, 1:]
    oa, ob, oc, od = ok[:-1, :-1], ok[:-1, 1:], ok[1:, :-1], ok[1:, 1:]
    t1 = oa & ob & oc & (np.abs(za - zb) < EDGE_MM) & (np.abs(za - zc_) < EDGE_MM) \
        & (np.abs(zb - zc_) < EDGE_MM)
    t2 = ob & oc & od & (np.abs(zb - zd) < EDGE_MM) & (np.abs(zc_ - zd) < EDGE_MM) \
        & (np.abs(zb - zc_) < EDGE_MM)
    idx = np.arange(H * W, dtype=np.int64).reshape(H, W)
    ia, ib, ic, id_ = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    faces = np.concatenate([np.stack([ia[t1], ic[t1], ib[t1]], 1),
                            np.stack([ib[t2], ic[t2], id_[t2]], 1)])
    if len(faces) < 200:
        return None
    G = P.reshape(H, W, 3)
    N = np.cross(np.gradient(G, axis=0), np.gradient(G, axis=1)).reshape(-1, 3)
    N /= np.linalg.norm(N, axis=1, keepdims=True) + 1e-9
    rgb = color_r[r0 * STEP:r1 * STEP:STEP, c0 * STEP:c1 * STEP:STEP][..., ::-1]
    rgb = rgb[:H, :W].reshape(-1, 3)
    used = np.unique(faces)
    remap = np.full(H * W, -1, np.int64)
    remap[used] = np.arange(len(used))
    return P[used], remap[faces], N[used], rgb[used]


def _unit_perimeter(flatten, n=2.0):
    a, _b = AN._semi_axes(1000.0, flatten, n)
    return 1000.0 / a


LIMB_P = _unit_perimeter(AN.ADULT["limb_flatten"])
TORSO_P = _unit_perimeter(AN.ADULT["torso_flatten"], AN.ADULT["torso_n"])


def width_of(circ, perim, flatten):
    """A circumference -> the side-to-side width it implies."""
    return 2.0 * flatten * circ / perim


def circ_of(width, perim, flatten):
    return width / (2.0 * flatten) * perim


class Measured:
    """One body dimension: a running median over accepted frames, blended in
    from a typical adult's value while there are only a few."""

    def __init__(self, label, prior, lo, hi, keep=300):
        self.label, self.prior, self.lo, self.hi = label, prior, lo, hi
        self.samples = collections.deque(maxlen=keep)

    def add(self, v):
        if v is not None and np.isfinite(v) and self.lo <= v <= self.hi:
            self.samples.append(float(v))

    @property
    def n(self):
        return len(self.samples)

    def value(self):
        if not self.samples:
            return self.prior
        med = float(np.median(self.samples))
        w = self.n / (self.n + PRIOR_WEIGHT)
        return w * med + (1.0 - w) * self.prior

    def spread(self):
        if self.n < 5:
            return None
        s = np.asarray(self.samples)
        return float(np.median(np.abs(s - np.median(s))))


def dims():
    lf, tf = AN.ADULT["limb_flatten"], AN.ADULT["torso_flatten"]
    return {
        "shoulders": Measured("shoulder to shoulder", 390.0, 250.0, 520.0),
        "chest_w": Measured("chest width", width_of(980.0, TORSO_P, tf), 220.0, 480.0),
        "waist_w": Measured("waist width", width_of(860.0, TORSO_P, tf), 200.0, 460.0),
        "hip_w": Measured("hip joint to hip joint", 200.0, 130.0, 320.0),
        "torso_len": Measured("hip to shoulder", 470.0, 300.0, 650.0),
        "head_w": Measured("head width (ear to ear)", 150.0, 110.0, 210.0),
        "upper_arm_len": Measured("upper arm length", 300.0, 180.0, 420.0),
        "upper_arm_w": Measured("upper arm width",
                                width_of(300.0, LIMB_P, lf), 55.0, 160.0),
        "forearm_len": Measured("forearm length", 255.0, 170.0, 350.0),
        "forearm_w": Measured("forearm width", width_of(280.0, LIMB_P, lf), 45.0, 130.0),
        "wrist_w": Measured("wrist width", width_of(170.0, LIMB_P, lf), 35.0, 95.0),
        "thigh_len": Measured("thigh length", 420.0, 300.0, 560.0),
        "hand_len": Measured("hand length", 185.0, 130.0, 240.0),
    }


UPPER_BODY = ("shoulders", "chest_w", "waist_w", "hip_w", "torso_len", "head_w",
              "upper_arm_len", "upper_arm_w", "forearm_len", "forearm_w", "wrist_w")


def _near(xs, ys, segs):
    """Which of these pixels lie within a radius of any 2D segment."""
    hit = np.zeros(len(xs), bool)
    for a, b, r in segs:
        ax, ay = a
        bx, by = b
        ex, ey = bx - ax, by - ay
        L2 = max(ex * ex + ey * ey, 1e-9)
        t = np.clip(((xs - ax) * ex + (ys - ay) * ey) / L2, 0.0, 1.0)
        dx, dy = xs - (ax + t * ex), ys - (ay + t * ey)
        hit |= dx * dx + dy * dy < r * r
    return hit


def run_widths(mask, depth, p0, p1, stations, max_half_mm, fx, z_ref=None,
               step=DEPTH_STEP_MM, avoid=(), outer=None, balanced=False):
    """Widths across the outline at points along a 2D line. -> [mm or None].

    A side of the run ends where the outline ends or where depth steps FURTHER
    than `step` (the torso behind an arm, the chair behind a torso). Anything
    NEARER is in front and is looked past. A run that finds no end within
    max_half_mm on a side is merged with something, and is not a width.
    A side that runs into one of the `avoid` segments (an arm, for a torso)
    first is not a width either: the arm is not the torso's edge.
    """
    H, W = mask.shape
    d = np.asarray(p1, float) - np.asarray(p0, float)
    L = float(np.hypot(*d))
    if L < 12.0:
        return []
    t = d / L
    nrm = np.array([-t[1], t[0]])
    out = []
    for s in stations:
        c = np.asarray(p0, float) + s * d
        cx, cy = int(round(c[0])), int(round(c[1]))
        if not (3 <= cx < W - 3 and 3 <= cy < H - 3):
            out.append(None)
            continue
        if z_ref is None:
            patch = depth[cy - 3:cy + 4, cx - 3:cx + 4]
            pm = mask[cy - 3:cy + 4, cx - 3:cx + 4] & (patch > 0)
            if pm.sum() < 5:
                out.append(None)
                continue
            z0 = float(np.median(patch[pm]))
        else:
            z0 = float(z_ref)
        kmax = int(max_half_mm * fx / z0) + 1
        k = np.arange(1, kmax + 1, dtype=float)
        ends = []
        for sgn in (1.0, -1.0):
            xs = np.round(c[0] + sgn * k * nrm[0]).astype(int)
            ys = np.round(c[1] + sgn * k * nrm[1]).astype(int)
            inb = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
            xs, ys = np.clip(xs, 0, W - 1), np.clip(ys, 0, H - 1)
            dz = depth[ys, xs]
            inside = inb & mask[ys, xs] & ((dz == 0) | (dz - z0 < step))
            blocked = _near(xs.astype(float), ys.astype(float), avoid) if avoid \
                else np.zeros(len(xs), bool)
            off = np.flatnonzero(~inside | blocked)
            if len(off) == 0 or blocked[off[0]]:
                ends.append(None)
            else:
                ends.append(int(off[0]))
        if ends[0] is None or ends[1] is None or min(ends) < 2:
            if outer is not None:
                # An arm at the side runs straight into the torso on its
                # inner side; its outer side still ends. The landmark line
                # is the arm's axis, so that side is half the width.
                away = 0 if float((c - np.asarray(outer, float)) @ nrm) > 0 else 1
                e = ends[away]
                if e is not None and e >= 2 and ends[1 - away] is None:
                    out.append((2 * e + 1) * z0 / fx)
                    continue
            out.append(None)
            continue
        if balanced and abs(ends[0] - ends[1]) > 0.5 * max(ends):
            # The landmark line is the limb's axis, so a run ending much
            # further out on one side has met something else first.
            out.append(None)
            continue
        out.append((ends[0] + ends[1] + 1) * z0 / fx)
    return out


def _normals(V, F):
    """Outward vertex normals, so the viewer can light the model."""
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    N = np.zeros_like(V)
    for i in range(3):
        np.add.at(N, F[:, i], fn)
    N /= np.linalg.norm(N, axis=1, keepdims=True) + 1e-9
    radial = V.copy()
    radial[:, 2] = 0.0
    if float((N * radial).sum()) < 0.0:          # the builder winds them inward
        N = -N
    return N.astype(np.float32)


def _capped(V, F, n_th, lift=0.0):
    """Close a swept tube at both ends, so it reads as solid.

    Wound like the tube (inward), so _normals turns the lot outward at once.
    `lift` raises the top cap's middle into a dome: shoulders, a crown.
    """
    n = len(V)
    L = float(V[:, 2].max())
    V2 = np.concatenate([V, np.array([[0.0, 0.0, L + lift], [0.0, 0.0, 0.0]],
                                     np.float32)])
    ring = np.arange(n_th)
    nxt = np.roll(ring, -1)
    top, bot = n - n_th + ring, ring
    Ft = np.stack([top[nxt], top, np.full(n_th, n)], 1)
    Fb = np.stack([bot, bot[nxt], np.full(n_th, n + 1)], 1)
    return V2, np.concatenate([F, Ft, Fb])


def on_ray(pix, parent, L, guess, intr, T_wc):
    """The point on the camera ray through `pix` that lies L from `parent`.

    Of the two, the one nearer `guess`. When the ray never comes that close,
    the ray's nearest point: the image position is kept either way, because
    that is what MediaPipe measures well.
    """
    cam = T_wc[:3, 3]
    d = T_wc[:3, :3] @ np.array([(pix[0] - intr["ppx"]) / intr["fx"],
                                 (pix[1] - intr["ppy"]) / intr["fy"], 1.0])
    d = d / np.linalg.norm(d)
    w = cam - parent
    bq = float(d @ w)
    disc = bq * bq - (float(w @ w) - L * L)
    if disc < 0.0:
        return cam + d * max(-bq, 1.0)
    s = math.sqrt(disc)
    tg = float((guess - cam) @ d)
    t = min((-bq - s, -bq + s), key=lambda x: abs(x - tg))
    return cam + d * max(t, 1.0)


def pixel_ray(pix, intr, T_wc):
    d = T_wc[:3, :3] @ np.array([(pix[0] - intr["ppx"]) / intr["fx"],
                                 (pix[1] - intr["ppy"]) / intr["fy"], 1.0])
    return d / np.linalg.norm(d)


BONE_SLACK = 0.08     # a bone's length is believed to about this fraction


SURFACE_MM = 12.0     # how closely a limb's front should follow its samples
SURFACE_CAP = 9.0     # ...and how much one wild sample (a hand across) may cost


def _surface_cost(P0, P1, samples, a0, a1, cam, cz):
    """How far a tube from P0 to P1 (radius a0 -> a1) stands off the camera's
    samples along it. P0/P1 broadcast. samples: [(t, depth)]."""
    if not samples:
        return 0.0
    V = P1 - P0
    L = np.linalg.norm(V, axis=-1, keepdims=True) + 1e-9
    mid = (P0 + P1) / 2.0 - cam
    view = mid / (np.linalg.norm(mid, axis=-1, keepdims=True) + 1e-9)
    c = np.sum(V / L * view, axis=-1)
    tilt = np.sqrt(np.clip(1.0 - c * c, 0.12, 1.0))     # side-on 1, end-on small
    z0 = (P0 - cam) @ cz
    z1 = (P1 - cam) @ cz
    cost = 0.0
    for t, z in samples:
        pred = (1.0 - t) * z0 + t * z1 - ((1.0 - t) * a0 + t * a1) / tilt
        cost = cost + np.minimum(((pred - z) / SURFACE_MM) ** 2, SURFACE_CAP)
    return cost


def solve_arm(S, de, te_obs, se, dw, tw_obs, sw, L1, L2, cam, cz=None,
              ua_s=(), fa_s=(), ua_r=(0.0, 0.0), fa_r=(0.0, 0.0)):
    """Elbow and wrist, each on its own image ray, at the depths that best fit
    both bone lengths and what the camera measured. -> (elbow, wrist).

    te_obs/tw_obs: the measured distance along each ray (None if unmeasured),
    trusted to se/sw mm. ua_s/fa_s: depths the camera read along the upper arm
    and the forearm, which the front of each tube should follow. A forearm
    pointing at the camera hides its elbow behind it and puts the hand in
    front of the wrist: then the surface in between says where they are.
    """
    t_near = float((S - cam) @ de)
    te0 = te_obs if te_obs is not None else t_near
    te = te0 + np.arange(-220.0, 321.0, 5.0)
    E = cam + te[:, None] * de
    cost_e = ((np.linalg.norm(E - S, axis=1) - L1) / (BONE_SLACK * L1)) ** 2
    if te_obs is not None:
        cost_e = cost_e + ((te - te_obs) / se) ** 2
    if cz is not None:
        cost_e = cost_e + _surface_cost(S[None, :], E, ua_s, ua_r[0], ua_r[1], cam, cz)
    if dw is None:
        return E[int(np.argmin(cost_e))], None
    tw0 = tw_obs if tw_obs is not None else te0
    tw = tw0 + np.arange(-260.0, 261.0, 5.0)
    Wp = cam + tw[:, None] * dw
    L = np.linalg.norm(E[:, None, :] - Wp[None, :, :], axis=2)
    C = cost_e[:, None] + ((L - L2) / (BONE_SLACK * L2)) ** 2
    if tw_obs is not None:
        C = C + (((tw - tw_obs) / sw) ** 2)[None, :]
    if cz is not None:
        C = C + _surface_cost(E[:, None, :], Wp[None, :, :], fa_s, fa_r[0], fa_r[1],
                              cam, cz)
    i, j = np.unravel_index(int(np.argmin(C)), C.shape)
    return E[i], Wp[j]


def lift(p, mask, depth, intr, T_wc, radius=10):
    """A landmark -> (world point on the NEAREST surface under it, spread, z).

    The nearest surface, not the median: an elbow over the stomach has the
    stomach behind it, and a median of the two is neither.
    """
    H, W = depth.shape
    x0, y0 = int(round(p[0])), int(round(p[1]))
    if not (0 <= x0 < W and 0 <= y0 < H):
        return None, None, None
    ys = slice(max(0, y0 - radius), y0 + radius + 1)
    xs = slice(max(0, x0 - radius), x0 + radius + 1)
    sub, m = depth[ys, xs], mask[ys, xs]
    z = sub[m & (sub > 0)]
    if len(z) < 8:
        return None, None, None
    q20, q25, q75 = np.percentile(z, [20, 25, 75])
    zz = float(np.median(z[z <= q20 + 40.0]))
    return deproject(p, zz, intr, T_wc), float(q75 - q25), zz


def deproject(p, z, intr, T_wc):
    cam_p = np.array([(p[0] - intr["ppx"]) * z / intr["fx"],
                      (p[1] - intr["ppy"]) * z / intr["fy"], z])
    return T_wc[:3, :3] @ cam_p + T_wc[:3, 3]


def _box(center, fwd, size):
    """A box on the floor's axes, `fwd` along its first side. -> (V, F)."""
    f = np.array([fwd[0], fwd[1], 0.0])
    f /= np.linalg.norm(f) + 1e-9
    s = np.array([-f[1], f[0], 0.0])
    u = np.array([0.0, 0.0, 1.0])
    hx, hy, hz = np.asarray(size, float) / 2.0
    V = np.array([center + a * hx * f + b * hy * s + c * hz * u
                  for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)], np.float32)
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return V, F


def _obox(center, f, u, size):
    """A box with its first side along f and its height along u. -> (V, F)."""
    f = unit(np.asarray(f, float))
    u = unit(np.asarray(u, float))
    s = unit(np.cross(u, f))
    hx, hy, hz = np.asarray(size, float) / 2.0
    V = np.array([center + a * hx * f + b * hy * s + c * hz * u
                  for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)], np.float32)
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return V, F


def unit(v):
    return v / (float(np.linalg.norm(v)) + 1e-9)


def clamp_lean(u, max_deg=MAX_LEAN_DEG):
    """A torso direction, no further than max_deg from straight up."""
    z = np.array([0.0, 0.0, 1.0])
    u = unit(u)
    if math.degrees(math.acos(float(np.clip(u @ z, -1.0, 1.0)))) <= max_deg:
        return u
    h = u - (u @ z) * z
    h = unit(h) if float(np.linalg.norm(h)) > 1e-6 else np.array([1.0, 0.0, 0.0])
    t = math.radians(max_deg)
    return math.cos(t) * z + math.sin(t) * h


def torso_front(mask, depth, px, intr, T_wc, arms):
    """The torso's front surface from depth. -> {a, b, y, n} or None.

    Samples the body between the shoulders and most of the way to the hips,
    away from the arms and hands, drops anything well in front of the rest
    (a lap, a hand), and fits x = a + b*z in the world: b is how far the torso
    leans toward the camera per millimetre of height. Measured, where
    MediaPipe's 3D skeleton only guesses depth.
    """
    ls, rs_, lh, rh = (np.array(px[k], float) for k in
                       ("l_shoulder", "r_shoulder", "l_hip", "r_hip"))
    us = np.linspace(0.2, 0.8, 9)[None, :, None]
    vs = np.linspace(0.08, 0.62, 14)[:, None, None]
    left = ls + vs * (lh - ls)
    right = rs_ + vs * (rh - rs_)
    q = (left + us * (right - left)).reshape(-1, 2)
    H, W = depth.shape
    xs, ys = np.round(q[:, 0]).astype(int), np.round(q[:, 1]).astype(int)
    ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
    xs, ys = xs[ok], ys[ok]
    ok = mask[ys, xs] & (depth[ys, xs] > 0)
    if arms:
        ok &= ~_near(xs.astype(float), ys.astype(float), arms)
    xs, ys = xs[ok], ys[ok]
    if len(xs) < 20:
        return None
    z = depth[ys, xs]
    keep = z > float(np.median(z)) - 80.0
    xs, ys, z = xs[keep], ys[keep], z[keep]
    if len(xs) < 20:
        return None
    cam_p = np.stack([(xs - intr["ppx"]) * z / intr["fx"],
                      (ys - intr["ppy"]) * z / intr["fy"], z], 1)
    P = cam_p @ T_wc[:3, :3].T + T_wc[:3, 3]
    coef = None
    for _ in range(2):
        A = np.stack([np.ones(len(P)), P[:, 2]], 1)
        coef = np.linalg.lstsq(A, P[:, 0], rcond=None)[0]
        r = np.abs(P[:, 0] - A @ coef)
        good = r < max(25.0, 2.5 * float(np.median(r)))
        if good.sum() < 20:
            break
        P = P[good]
    return {"a": float(coef[0]), "b": float(coef[1]),
            "y": float(np.median(P[:, 1])), "n": int(len(P))}


# How hard the body model follows a moving person (OneEuro's beta): higher
# follows a quick move sooner and jitters more while they hold still. The
# depth guard stops the arms when the model and the camera disagree by
# AGREE_MM, and a model that lags a moving person disagrees at once.
SMOOTH_BETA = 0.01
# No joint of a person moves faster than this. A reading that jumps further
# between two frames is the solver changing its mind (a limb crossing the
# visibility threshold, an elbow hidden behind its own forearm), not the
# person: the model is carried toward it at this speed instead, which is
# what stopped it twitching as they raised an arm.
MAX_JOINT_V = 1500.0


class OneEuro:
    """The One Euro filter (Casiez et al. 2012) on a vector.

    The cutoff rises with speed: a still arm is steadied hard, a moving one
    followed closely. A fixed blend lags a moving arm by a quarter second.
    """

    def __init__(self, min_cutoff=1.0, beta=0.01, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _alpha(dt, cutoff):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.x is None:
            self.x, self.dx, self.t = x.copy(), np.zeros_like(x), t
            return self.x.copy()
        dt = min(max(t - self.t, 1e-3), 0.5)
        self.t = t
        dx = (x - self.x) / dt
        self.dx = self.dx + self._alpha(dt, self.d_cutoff) * (dx - self.dx)
        cutoff = self.min_cutoff + self.beta * float(np.linalg.norm(self.dx))
        self.x = self.x + self._alpha(dt, cutoff) * (x - self.x)
        return self.x.copy()


def _median_of_readings(D):
    """Each pixel's median over the frames that have a reading there, 0 where
    none do. D: (frames, H, W), 0 meaning nothing was measured.

    The same answer as nanmedian over the stack with 0 read as NaN, and about
    four times quicker on eight frames of half resolution depth, where it was
    a third of every floor search: nanmedian carries NaN handling through the
    sort of every pixel, while sending the gaps to +inf lets a plain sort put
    them at the end, and the count of real readings says which of the sorted
    values is the middle one.
    """
    n = D.shape[0]
    S = np.where(D > 0.0, D, np.inf)
    S.sort(axis=0)
    cnt = np.count_nonzero(np.isfinite(S), axis=0)
    flat = S.reshape(n, -1)
    cols = np.arange(flat.shape[1])
    lo = (np.maximum(cnt, 1) - 1) // 2
    hi = np.minimum(np.maximum(cnt, 1) // 2, n - 1)
    a = flat[lo.ravel(), cols]
    b = flat[hi.ravel(), cols]
    med = (0.5 * (a + b)).reshape(D.shape[1:])
    return np.where(cnt > 0, med, 0.0).astype(np.float32)


class WorldFrame:
    """The floor, measured by the camera as it is NOW, and kept current.

    A capture's floor is only right while the camera stays where it was, and
    on this rig it did not: re-aimed 11 degrees down, which tilted every bone,
    lifted the seat and left the body hovering. So the floor is fitted from
    the live depth at start, re-checked every few seconds, and replaced when
    the camera has moved.
    """
    CHECK_S = 5.0
    # A re-aimed or bumped camera, not fit noise: with the camera level the
    # floor is only seen far off, and successive fits wander 25mm and half a
    # degree. A change must hold for MOVED_HITS fits running, over
    # MOVED_HITS * CHECK_S seconds, before the world is moved under the
    # person: a fit that took a different surface for a few seconds once
    # tilted the floor 3 degrees, and the rig that came with it put two arms
    # inside the person, where they had to stay still.
    MOVED_DEG, MOVED_MM = 3.0, 45.0
    MOVED_HITS = 3
    # A floor is flat. Fits of the real floor leave 4 to 6 mm on both
    # recordings; the wrong plane RANSAC sometimes prefers (more points, 34 cm
    # up, tilted) leaves 18. Anything rougher than this is not the floor.
    FLAT_MM = 12.0

    def __init__(self, keep=False):
        """keep: once the floor is found, hold it however the fits wander
        (--keep-floor). A fit that wanders re-tilts the world under the
        person, which restarts the body and can leave the arms' waiting
        poses inside them."""
        self.keep = keep
        self.seat_ok = False             # a seat was found on this floor: hold it
        self.T, self.floor, self.last, self.moves = None, None, 0.0, 0
        self.frames = collections.deque(maxlen=15)
        self.pending, self.pending_hits = None, 0
        self.tries = self.rough = 0          # fits so far, and ones not flat enough

    def _flat_floor(self, med, half):
        """The floor, or None: the lowest flat surface in view. RANSAC takes
        the surface with the most points, and with the camera near level
        that is often something else (a bed, a desk, a lap, flat or not), so
        each surface found is taken out and the fit tried again, four times,
        and the flat one furthest below the camera wins."""
        med = med.copy()
        h, w = med.shape
        ys, xs = np.mgrid[0:h, 0:w]
        best = None
        for _ in range(4):
            fl = FRAME.fit_floor(med, half)
            if fl is None:
                break
            if fl["rms_mm"] <= self.FLAT_MM:
                if best is None or fl["camera_height_mm"] > best["camera_height_mm"] + 30.0:
                    best = fl
            else:
                self.rough += 1
            n, d = np.asarray(fl["normal_cam"], float), float(fl["offset_mm"])
            x = (xs - half["ppx"]) * med / half["fx"]
            y = (ys - half["ppy"]) * med / half["fy"]
            off = np.abs(n[0] * x + n[1] * y + n[2] * med - d)
            med[(med > 0) & (off < 50.0)] = 0.0
            if int((med[int(h * 0.55):] > 0).sum()) < 2000:
                break
        return best

    @staticmethod
    def _same(a, b, deg, mm):
        dot = float(np.clip(np.dot(a["normal_cam"], b["normal_cam"]), -1.0, 1.0))
        return (math.degrees(math.acos(dot)) < deg
                and abs(a["camera_height_mm"] - b["camera_height_mm"]) < mm)

    def feed(self, depth, mask, intr, now):
        """-> True when the world frame is new: the first fit, or a moved camera."""
        self.frames.append(depth[::2, ::2])
        if len(self.frames) < 8 or (self.T is not None and now - self.last < self.CHECK_S):
            return False
        if self.floor is not None and self.keep and self.seat_ok:
            # The floor is held (--keep-floor), so everything below can only
            # be thrown away, and it is the most expensive thing in the
            # frame: the median of eight depth frames and up to four plane
            # searches, 230 ms together on the recording. Paying
            # for that and discarding it cost a stall every CHECK_S, and
            # about 6 ms of every frame averaged over a run.
            self.last = now
            return False
        self.last = now
        med = _median_of_readings(np.stack(self.frames).astype(np.float32))
        half = dict(intr, width=intr["width"] // 2, height=intr["height"] // 2,
                    fx=intr["fx"] / 2, fy=intr["fy"] / 2,
                    ppx=intr["ppx"] / 2, ppy=intr["ppy"] / 2)
        self.tries += 1
        fl = self._flat_floor(med, half)
        if fl is None:
            return False
        if self.floor is None:
            # The first floor too only counts once a second fit, on frames
            # the first did not use, agrees: one fit alone once took a plane
            # 34 cm above the floor and 15 degrees off it (18 mm residual,
            # where every later fit said 4.5 mm), and a wrong first floor
            # sticks for the next ten seconds.
            if self.pending is None or not self._same(
                    fl, self.pending, self.MOVED_DEG / 2.0, self.MOVED_MM / 2.0):
                self.pending, self.pending_hits = fl, 1
                self.frames.clear()
                return False
            self.pending, self.pending_hits = None, 0
        else:
            # A held floor (--keep-floor) has already returned, above.
            if self._same(fl, self.floor, self.MOVED_DEG, self.MOVED_MM):
                self.pending, self.pending_hits = None, 0
                return False
            if self.pending is None or not self._same(
                    fl, self.pending, self.MOVED_DEG / 2.0, self.MOVED_MM / 2.0):
                self.pending, self.pending_hits = fl, 1
                return False                       # wait for the next fits to agree
            self.pending_hits += 1
            if self.pending_hits < self.MOVED_HITS:
                return False
            self.pending, self.pending_hits = None, 0
            self.moves += 1
        m = mask[::2, ::2] & (med > 0)
        pts = FRAME.deproject(med, half, m)
        centroid = pts.mean(0) if len(pts) > 200 else None
        self.floor, self.T = fl, FRAME.world_from_camera(fl, centroid)
        return True


class Body:
    """The display model: built from the current measurements, posed live."""

    def __init__(self, D):
        self.T, self.S, self.joints, self.chair = {}, {}, {}, []
        self.butt, self.sitting_height, self.fwd = None, None, None
        self.thighs = {}
        self.build(D)

    def build(self, D):
        v = {k: m.value() for k, m in D.items()}
        lf, tf = AN.ADULT["limb_flatten"], AN.ADULT["torso_flatten"]
        # An upper arm beside the torso is rarely measured cleanly; the
        # forearm nearly always is, and bounds it.
        v["upper_arm_w"] = float(np.clip(v["upper_arm_w"], 0.95 * v["forearm_w"],
                                         1.3 * v["forearm_w"]))
        up_c = circ_of(v["upper_arm_w"], LIMB_P, lf)
        meas = {
            "biacromial_mm": v["shoulders"],
            "upper_arm_len_mm": v["upper_arm_len"],
            "forearm_len_mm": v["forearm_len"],
            "torso_len_mm": v["torso_len"],
            "upper_arm_circ_mm": up_c,
            # The elbow is below any sleeve: sized from the forearm.
            "elbow_circ_mm": 0.96 * circ_of(v["forearm_w"], LIMB_P, lf),
            "forearm_circ_mm": circ_of(v["forearm_w"], LIMB_P, lf),
            "wrist_circ_mm": circ_of(v["wrist_w"], LIMB_P, lf),
            "chest_circ_mm": circ_of(v["chest_w"], TORSO_P, tf),
            "waist_circ_mm": circ_of(v["waist_w"], TORSO_P, tf),
        }
        body, meshes = AN.anatomical_body(measurements=meas)
        foot, foot_mesh = AN._swept(np.eye(4), 240.0,
                                    [(0.0, 34.0, 40.0), (0.3, 36.0, 48.0),
                                     (0.8, 28.0, 44.0), (1.0, 14.0, 28.0)],
                                    name="foot", scrubbable=False,
                                    front_arc=(-math.pi, math.pi))
        items = [(r, meshes[r.name]) for r in body.regions]
        items += [(foot, foot_mesh), (foot, foot_mesh)]
        parts = {}
        for i, (r, (V, F)) in enumerate(items):
            name = r.name if r.name != "foot" else ("foot_L" if "foot_L" not in parts
                                                    else "foot_R")
            V = np.asarray(V, np.float32).copy()
            F = np.asarray(F, np.int64)
            length = float(V[:, 2].max())
            if name.startswith("thigh"):         # the builder types this one in
                V[:, 2] *= v["thigh_len"] / length
                length = v["thigh_len"]
            z = V[:, 2]
            a0 = float(np.abs(V[z <= z.min() + 1e-3, 0]).max())
            a1 = float(np.abs(V[z >= z.max() - 1e-3, 0]).max())
            a = float(np.abs(V[:, 0]).max())
            if name.startswith("hand"):
                V[:, 0] *= HAND_FLAT
            lift = {"torso": 0.35 * a1, "head": 0.3 * a}.get(name, 0.0)
            V, F = _capped(V, F, 28, lift)
            cells = np.asarray(r.pts, np.float32)
            if name == "torso":
                keep = np.ones(len(cells), bool)     # the whole front of it
            else:
                keep = np.asarray(r.scrubbable, bool)
            parts[name] = {"V": V, "F": F, "N": _normals(V, F), "len": length,
                           "a": a, "a0": a0, "a1": a1, "cells": cells[keep],
                           "cnrm": np.asarray(r.nrm, np.float32)[keep],
                           "carea": np.asarray(r.area, float)[keep],
                           "arc": tuple(r.arc), "n_exp": float(r.n_exp)}
        self.parts = parts
        self.shoulders, self.hip_w = v["shoulders"], v["hip_w"]

    def scrub_cells(self):
        """-> (points, normals, part index, area) of every scrub cell, in the
        world, always in the same order: the order territories are planned in.
        """
        P, N, I, A = [], [], [], []
        for i, name in enumerate(SCRUB_PARTS):
            p, T = self.parts[name], self.T[name]
            s = self.S.get(name, 1.0)
            R, t = T[:3, :3], T[:3, 3]
            P.append((p["cells"] * np.array([1.0, 1.0, s])) @ R.T + t)
            N.append(p["cnrm"] @ R.T)
            I.append(np.full(len(p["cells"]), i))
            A.append(p["carea"] * s)
        return np.vstack(P), np.vstack(N), np.concatenate(I), np.concatenate(A)

    def head_ceiling(self):
        """No sponge above this height: the base of the head, as posed."""
        hb = getattr(self, "head_base", None)
        return None if hb is None else float(hb[2])

    def as_model(self):
        """The posed model as the planner's BodyModel: one rigid region per
        scrub part, cells stretched as drawn."""
        regions = []
        for name in SCRUB_PARTS:
            p = self.parts[name]
            s = self.S.get(name, 1.0)
            n = len(p["cells"])
            regions.append(BM.Region(
                name, self.T[name].copy(),
                (p["cells"] * np.array([1.0, 1.0, s])).astype(float),
                p["cnrm"].astype(float), p["carea"] * s, np.ones(n, bool),
                p["arc"], p["n_exp"]))
        return BM.BodyModel(regions)

    def obstacles(self):
        """The whole posed model and the chair. -> (points, region)

        region is the scrub part a point belongs to, as an index into
        as_model()'s regions, or -1: an arm working on a limb is not blocked
        by that limb, and is by everything else.
        """
        pts, reg = [], []
        for name, p in self.parts.items():
            T = self.T.get(name)
            if T is None:
                continue
            s = self.S.get(name, 1.0)
            V = (p["V"] * np.array([1.0, 1.0, s])) @ T[:3, :3].T + T[:3, 3]
            pts.append(V)
            rid = SCRUB_PARTS.index(name) if name in SCRUB_PARTS else -1
            reg.append(np.full(len(V), rid))
        for bv, _bf in self.chair:
            q = _box_points(bv)
            pts.append(q)
            reg.append(np.full(len(q), -1))
        return np.vstack(pts).astype(float), np.concatenate(reg)

    def set_seat(self, seat):
        """Place the chair, once. It does not move after this.

        The backrest leans as the person did when the chair was placed, and
        sits against their back: an office chair reclines with its sitter.
        """
        z = float(seat["z"])
        fwd = unit(np.array([seat["fwd"][0], seat["fwd"][1], 0.0]))
        up = unit(np.asarray(seat["up"], float))
        ant = unit(fwd - (fwd @ up) * up)          # the back's outward normal, reversed
        base = np.array([seat["xy"][0], seat["xy"][1], 0.0])
        butt = base + np.array([0.0, 0.0, z])
        a = self.parts["torso"]["a"]
        zax = np.array([0.0, 0.0, 1.0])
        side = unit(np.cross(zax, fwd))
        self.chair = [
            _obox(butt + fwd * 110.0 - zax * 25.0, fwd, zax, (470.0, 460.0, 50.0)),
            _obox(butt + up * 290.0 - ant * (a + 28.0), ant, up, (36.0, 440.0, 520.0)),
            _obox(base + fwd * 110.0 + zax * (z - 50.0) / 2.0, fwd, zax,
                  (56.0, 56.0, max(z - 50.0, 10.0))),
            _obox(base + fwd * 110.0 + zax * 20.0, fwd, zax, (620.0, 60.0, 40.0)),
            _obox(base + fwd * 110.0 + zax * 20.0, side, zax, (620.0, 60.0, 40.0)),
        ]

    def chair_mesh(self):
        if not self.chair:
            return None
        CV, CF, off = [], [], 0
        for bv, bf in self.chair:
            CV.append(bv)
            CF.append(bf + off)
            off += len(bv)
        return np.concatenate(CV), np.concatenate(CF)

    def pose_joints(self, W, cam, seat, up, centre, arms, head_c, knees, legs_seen,
                    hand_len=None):
        """Pose the whole model as ONE connected, seated body, from joints.

        centre: the torso's axis at shoulder height, from its measured front.
        up: the torso's direction.
        arms: {"L"/"R": (shoulder, elbow, wrist, hand direction)}, joint
        centres, each where the camera saw it.
        head_c: the head's axis at the nose, from the face's depth.
        knees: {"L"/"R": knee centre}, where seen.
        W: MediaPipe's own skeleton in world axes, for what is not seen.
        seat: once known, the torso reaches down to it, so the body sits ON
        the chair instead of hovering a pelvis above it.
        """
        P = self.parts
        joints = {}

        def place(name, origin, direction, a=None, b=None, length=None):
            d = unit(direction)
            L = P[name]["len"] if length is None else max(float(length), 5.0)
            self.S[name] = L / P[name]["len"]
            self.T[name] = FRAME.region_pose(origin, d, anterior=cam - origin)
            end = origin + d * L
            if a:
                joints[a] = origin
            if b:
                joints[b] = end
            return end

        fwd = cam - centre
        fwd[2] = 0.0
        fwd = unit(fwd)                               # the way the person faces
        # --- torso, from the seat up to the shoulders ----------------------
        if seat is not None:
            reach = (centre[2] - seat["z"]) / max(float(up[2]), 0.5)
        else:
            reach = P["torso"]["len"] + PELVIS_MM
        reach = float(np.clip(reach, 300.0, 800.0))
        butt = centre - up * reach
        place("torso", butt, up, length=reach)
        hip_c = butt + up * PELVIS_MM
        self.butt, self.sitting_height = butt, reach
        # --- neck and head: the head where the face is ---------------------
        hdir = unit(0.7 * np.array([0.0, 0.0, 1.0]) + 0.3 * up)
        head_base = head_c - hdir * HEAD_AXIS_NOSE_MM
        self.head_base = head_base
        neck_top = head_base + hdir * 10.0
        place("neck", centre, neck_top - centre,
              length=float(np.linalg.norm(neck_top - centre)))
        place("head", head_base, hdir)
        # --- arms: joint to joint ------------------------------------------
        for side, s in (("L", "l"), ("R", "r")):
            sh, el, wr, hd = arms[side]
            place(f"upper_arm_{side}", sh, el - sh, f"{s}_shoulder", f"{s}_elbow",
                  length=float(np.linalg.norm(el - sh)))
            place(f"forearm_{side}", el, wr - el, None, f"{s}_wrist",
                  length=float(np.linalg.norm(wr - el)))
            # The hand is flatter than the wrist: raise its axis so its
            # back stays level with the wrist's front.
            tw = unit(cam - wr)
            perp = unit(tw - (tw @ hd) * hd)
            wa = P[f"forearm_{side}"]["a1"]
            place(f"hand_{side}", wr + perp * wa * (1.0 - HAND_FLAT), hd,
                  length=hand_len)
        # --- seated legs: thighs to the knees, shins down, feet flat -------
        lat = arms["L"][0] - arms["R"][0]
        lat[2] = 0.0
        lat = unit(lat)
        self.thighs = {}
        for side, s, sgn in (("L", "l", 1.0), ("R", "r", -1.0)):
            hp = hip_c + lat * sgn * self.hip_w / 2.0
            tlen = None
            if knees.get(side) is not None:
                tdir = knees[side] - hp
                tlen = float(np.clip(np.linalg.norm(tdir), 300.0, 650.0))
                self.thighs[side] = float(np.linalg.norm(tdir))
            elif legs_seen.get(side):
                d = W[f"{s}_knee"] - W[f"{s}_hip"]
                h = np.array([d[0], d[1], 0.0])
                if float(np.linalg.norm(h)) < 1e-6 or float(np.dot(unit(h), fwd)) < 0.3:
                    h = fwd
                tdir = unit(h)
            else:
                tdir = fwd + lat * sgn * 0.12 + np.array([0.0, 0.0, 0.04])
            kn = place(f"thigh_{side}", hp, tdir, f"{s}_hip", f"{s}_knee", length=tlen)
            shin = float(np.clip(kn[2] - ANKLE_Z, 250.0, 650.0))
            an = place(f"shin_{side}", kn, np.array([0.0, 0.0, -1.0]), None,
                       f"{s}_ankle", length=shin)
            heel = np.array([an[0], an[1], P[f"foot_{side}"]["a"]]) - fwd * 50.0
            place(f"foot_{side}", heel, fwd)
        self.joints = joints
        self.fwd = fwd
        self.ant = unit(fwd - (fwd @ up) * up)

    def pose(self, W, anchor, cam, legs_seen, seat=None, up=None, dirs=None,
             centre=None, knees=None):
        """Pose the whole model as ONE connected, seated body.

        W: MediaPipe's own 3D skeleton in world axes (directions only).
        anchor: the front of the chest between the shoulders, from depth.
        seat: once known, the torso reaches down to it, so the body sits ON
        the chair instead of hovering a pelvis above it.
        """
        P = self.parts
        joints = {}

        def place(name, origin, direction, a=None, b=None, length=None):
            d = unit(direction)
            L = P[name]["len"] if length is None else length
            self.S[name] = L / P[name]["len"]
            self.T[name] = FRAME.region_pose(origin, d, anterior=cam - origin)
            end = origin + d * L
            if a:
                joints[a] = origin
            if b:
                joints[b] = end
            return end

        msh = (W["l_shoulder"] + W["r_shoulder"]) / 2.0
        mhip = (W["l_hip"] + W["r_hip"]) / 2.0
        up = clamp_lean(msh - mhip) if up is None else up
        dirs = dirs or {}
        toward = unit(cam - anchor)
        fwd = toward.copy()
        fwd[2] = 0.0
        fwd = unit(fwd)                               # the way the person faces
        # The torso's axis at shoulder height: fitted to its front surface
        # when the frame shows one, else one torso radius behind the shoulders.
        sh_c = centre if centre is not None else anchor - toward * P["torso"]["a"]
        knees = knees or {}
        self.thighs = {}
        if seat is not None:
            reach = (sh_c[2] - seat["z"]) / max(float(up[2]), 0.5)
        else:
            reach = P["torso"]["len"] + PELVIS_MM
        reach = float(np.clip(reach, 300.0, 800.0))
        butt = sh_c - up * reach
        place("torso", butt, up, length=reach)
        hip_c = butt + up * PELVIS_MM
        self.butt, self.sitting_height = butt, reach
        k = self.shoulders / max(float(np.linalg.norm(W["l_shoulder"] - W["r_shoulder"])),
                                 1e-6)
        ears = (W["l_ear"] + W["r_ear"]) / 2.0
        head_dir = ears - msh
        neck_end = place("neck", sh_c, head_dir)
        place("head", neck_end, head_dir)
        lat = unit(W["l_hip"] - W["r_hip"])
        lat[2] = 0.0
        lat = unit(lat)
        for side, s, sgn in (("L", "l", 1.0), ("R", "r", -1.0)):
            # A shoulder joint sits one ARM radius behind the skin, not a
            # torso's: the arm hangs in front of where the chest centre is.
            sh = (anchor - toward * P[f"upper_arm_{side}"]["a"]
                  + (W[f"{s}_shoulder"] - msh) * k)
            ua = dirs.get(f"{s}_ua")
            el = place(f"upper_arm_{side}", sh,
                       ua if ua is not None else W[f"{s}_elbow"] - W[f"{s}_shoulder"],
                       f"{s}_shoulder", f"{s}_elbow")
            fa = dirs.get(f"{s}_fa")
            wr = place(f"forearm_{side}", el,
                       fa if fa is not None else W[f"{s}_wrist"] - W[f"{s}_elbow"],
                       None, f"{s}_wrist")
            tip = (W[f"{s}_index"] + W[f"{s}_pinky"]) / 2.0
            place(f"hand_{side}", wr, tip - W[f"{s}_wrist"])

            # --- seated legs: thighs about level, shins down, feet flat ----
            hp = hip_c + lat * sgn * self.hip_w / 2.0
            tlen = None
            if knees.get(side) is not None:
                # The knee the camera sees: the thigh goes to it.
                tdir = knees[side] - hp
                tlen = float(np.clip(np.linalg.norm(tdir), 300.0, 650.0))
                self.thighs[side] = float(np.linalg.norm(tdir))
            elif legs_seen.get(side):
                d = W[f"{s}_knee"] - W[f"{s}_hip"]
                h = np.array([d[0], d[1], 0.0])
                if float(np.linalg.norm(h)) < 1e-6 or float(np.dot(unit(h), fwd)) < 0.3:
                    h = fwd
                h = unit(h)
                tilt = float(np.clip(d[2] / (np.linalg.norm(d[:2]) + 1e-9), -0.35, 0.35))
                tdir = h + np.array([0.0, 0.0, tilt])
            else:
                tdir = fwd + lat * sgn * 0.12 + np.array([0.0, 0.0, 0.04])
            kn = place(f"thigh_{side}", hp, tdir, f"{s}_hip", f"{s}_knee",
                       length=tlen)
            shin = float(np.clip(kn[2] - ANKLE_Z, 250.0, 650.0))
            an = place(f"shin_{side}", kn, np.array([0.0, 0.0, -1.0]), None,
                       f"{s}_ankle", length=shin)
            heel = np.array([an[0], an[1], P[f"foot_{side}"]["a"]]) - fwd * 50.0
            place(f"foot_{side}", heel, fwd)
        self.joints = joints
        self.fwd = fwd

    def arrays(self):
        V, F, C, NN, off = [], [], [], [], 0
        pts, pcol = [], []
        for name, p in self.parts.items():
            T = self.T.get(name)
            if T is None:
                continue
            R, t = T[:3, :3].astype(np.float32), T[:3, 3].astype(np.float32)
            sc = np.array([1.0, 1.0, self.S.get(name, 1.0)], np.float32)
            V.append((p["V"] * sc) @ R.T + t)
            NN.append(p["N"] @ R.T)
            F.append(p["F"] + off)
            off += len(p["V"])
            # Shaded by how much each vertex faces the camera's side and how
            # high it sits: flat one colour reads as a cardboard cut-out.
            nn = p["N"] @ R.T
            lit = 0.72 + 0.28 * np.clip(nn @ np.array([0.55, 0.15, 0.82],
                                                      np.float32), 0.0, 1.0)
            vz = (p["V"] * sc) @ R.T + t
            if len(vz):
                h = vz[:, 2]
                span = max(float(h.max() - h.min()), 1.0)
                lit = lit * (0.94 + 0.10 * (h - h.min()) / span)
            base = np.array(SKIN, np.float32)
            C.append(np.clip(base[None] * lit[:, None], 0, 255).astype(np.uint8))
            if len(p["cells"]) and name in PATCH_RGB:
                pts.append((p["cells"] * sc) @ R.T + t)
                pcol.append(np.tile(np.array(PATCH_RGB[name], np.uint8),
                                    (len(p["cells"]), 1)))
        if not V:
            return None
        return (np.concatenate(V), np.concatenate(F), np.concatenate(C),
                np.concatenate(NN),
                np.concatenate(pts) if pts else np.zeros((0, 3), np.float32),
                np.concatenate(pcol) if pcol else np.zeros((0, 3), np.uint8))


ARM_NAMES = ("red", "blue", "green", "orange")


def territory_colours(own, done, reach):
    """Per scrub cell: its arm's colour, from dark to bright as this pass's
    sweeps go over it (`done`, 0 to 1, or True once scrubbed), faded if that
    arm cannot reach it now; grey when no arm can."""
    col = np.tile(np.array(UNOWNED_RGB, np.uint8), (len(own), 1))
    level = np.clip(np.asarray(done, float), 0.0, 1.0)
    for a, rgb in enumerate(VIZ.ARM_COLOURS):
        m = own == a
        if not m.any():
            continue
        base = np.array(rgb, float)
        dark, bright = 0.35 * base, np.minimum(base * 1.25 + 60.0, 255.0)
        col[m] = (dark + (bright - dark) * level[m][:, None]).astype(np.uint8)
        col[m & ~reach] = (0.25 * base + 50.0).astype(np.uint8)
    return col


def territory_radii(own, done):
    """Scrubbed patches large, waiting ones smaller, nobody's smallest."""
    level = np.clip(np.asarray(done, float), 0.0, 1.0)
    return np.where(own < 0, 2.0, 3.2 + 3.0 * level)


def floor_doubt(seat):
    """A plain warning when the camera's own seat reading cannot be a seat:
    the floor it fitted is then something else (a lap, a seat), usually
    because the camera is aimed too steeply to see the floor. -> str or None."""
    if seat is None or SEAT_SEEN_MM[0] <= seat["z_seen"] <= SEAT_SEEN_MM[1]:
        return None
    return (f"The camera's floor looks wrong: it puts the seat at "
            f"{seat['z_seen']:.0f} mm. Aim the camera so it sees the floor in front "
            f"of the chair, level or tilted a little down; the body starts over "
            f"when the camera moves.")


def table(D, fps, seat=None, sitting=None, pending=0, note=None, by_arm=False):
    rows = ["| | now | frames used | spread |", "|---|---|---|---|"]
    doubt = floor_doubt(seat)
    if doubt:
        note = doubt if not note else f"{doubt}\n\n## {note}"
    if seat is not None and abs(seat["z"] - seat["z_seen"]) > 0.5:
        rows.append(f"| seat height, as you measured it | **{seat['z']:.0f} mm** "
                    f"| camera says {seat['z_seen']:.0f} | |")
    elif seat is not None:
        rows.append(f"| seat height (where your torso ends), fixed | **{seat['z']:.0f} mm** "
                    f"| {SEAT_SAMPLES} | |")
    else:
        rows.append(f"| seat height | finding it | {pending} of {SEAT_SAMPLES} | |")
    if sitting is not None:
        rows.append(f"| sitting height (seat to shoulders) | **{sitting:.0f} mm** "
                    f"| this frame | |")
    for key in UPPER_BODY + ("hand_len", "thigh_len"):
        m = D[key]
        src = f"{m.n}" if m.n else "none yet: typical adult"
        sp = m.spread()
        rows.append(f"| {m.label} | **{m.value():.0f} mm** | {src} | "
                    f"{'±%.0f mm' % sp if sp is not None else ''} |")
    return ((f"## {note}\n\n" if note else "")
            + f"**Measured live, {fps:.1f} frames/s.** Scrub area: the whole upper "
            + ("body, never the head. " if by_arm else
               "body (purple torso, coloured arms), never the head. ")
            + "A width counts only when the camera sees where the body ends on "
            "both sides.\n\n"
            + "\n".join(rows))


def waiting_note(live):
    """What the view still waits for, before it has the floor. -> markdown"""
    seen = list(live.seen)
    pct = 100.0 * sum(seen) / len(seen) if seen else 0.0
    w = live.world
    if pct < 60.0:
        who = (f"**The camera finds you in {pct:.0f}% of frames.** Sit in the chair "
               f"facing the camera, with your head, shoulders and hips in its picture.")
    else:
        who = f"The camera sees you ({pct:.0f}% of frames)."
    floor = (f"**Looking for the floor:** {w.tries} tries"
             + (f", {w.rough} surfaces too uneven to be the floor" if w.rough else "")
             + ". The camera needs some floor in the lower part of its picture, and "
               "only looks while it sees you.")
    return "\n\n".join(["## Getting ready", who, floor]) + "\n\n"


def blueprint():
    """The layout. Eyes are placed around the PERSON (the world origin is on
    the floor beneath them, +X toward the camera), so they frame the same way
    wherever the camera stands."""
    front = rrb.EyeControls3D(position=[1350.0, -760.0, 1180.0],
                              look_target=[0.0, 0.0, 640.0], eye_up=[0, 0, 1])
    side = rrb.EyeControls3D(position=[-40.0, -1750.0, 760.0],
                             look_target=[-40.0, 0.0, 640.0], eye_up=[0, 0, 1])
    live = ["+ /world/live", "+ /world/skeleton", "+ /world/model/patches",
            "+ /world/arms/**"]
    return rrb.Blueprint(rrb.Horizontal(
        rrb.Vertical(
            rrb.Spatial3DView(origin="world", name="You live, with the model's skeleton "
                                                   "and scrub areas",
                              contents=live, eye_controls=front),
            rrb.Spatial3DView(origin="world", name="The same, from your right side",
                              contents=live + ["+ /world/model/seat"], eye_controls=side),
            row_shares=[3, 2]),
        rrb.Vertical(
            rrb.Spatial3DView(origin="world", name="The model, as measured so far",
                              contents=["+ /world/model/**", "+ /world/arms/**"],
                              eye_controls=front),
            rrb.Horizontal(
                rrb.TextDocumentView(origin="measurements", name="Measurements"),
                rrb.Spatial2DView(origin="camera", name="What the camera sees"),
                column_shares=[3, 2]),
            row_shares=[3, 2]),
    ), collapse_panels=True)


def camera_source(upside_down=False):
    """The D455. -> (intrinsics, frame generator). Mounted upside down, its
    frames and intrinsics are turned 180 degrees, so everything after this
    sees the room the right way up."""
    pipe = rs.pipeline()
    cfg = rs.config()
    cfg.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, 30)
    cfg.enable_stream(rs.stream.color, 1280, 720, rs.format.bgr8, 30)
    prof = pipe.start(cfg)
    ds = prof.get_device().first_depth_sensor()
    try:
        ds.set_option(rs.option.visual_preset, int(rs.rs400_visual_preset.high_density))
    except Exception:                                       # noqa: BLE001
        pass
    units = ds.get_depth_scale() * 1000.0
    ci = prof.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    intr = REC.rotate_intrinsics({"width": ci.width, "height": ci.height, "fx": ci.fx,
                                  "fy": ci.fy, "ppx": ci.ppx, "ppy": ci.ppy},
                                 "180" if upside_down else "none")
    align = rs.align(rs.stream.color)
    turn = (slice(None, None, -1),) * 2 if upside_down else (slice(None),) * 2

    def frames():
        try:
            while True:
                fs = align.process(pipe.wait_for_frames(2000))
                c, d = fs.get_color_frame(), fs.get_depth_frame()
                if not c or not d:
                    continue
                # A copy either way: the frame's own memory is reused.
                color = np.array(np.asanyarray(c.get_data())[turn], order="C")
                depth = np.ascontiguousarray(
                    np.asanyarray(d.get_data())[turn].astype(np.float32) * units)
                yield color, depth, time.time()
        finally:
            pipe.stop()
    return intr, frames()


def replay_source(folder, loop=True, fps=9.0):
    """Frames kept by --dump, played back as if they were live, on the
    recording's own clock (or `fps` for a recording that kept none)."""
    # THE ONE DOOR EVERY RIG TOOL COMES THROUGH. rig_sim, rig_editor,
    # search_rig, arms_anywhere and arms_check all start here, and a checkout
    # with no scrub3d/data/ (which is every fresh one -- recordings are not in
    # the repository) sent all five into a bare FileNotFoundError on
    # intr.json, or, through arms_anywhere's process pool, a BrokenProcessPool
    # naming nothing at all. Five tools that look broken, one missing folder.
    #
    # Saying it once here rather than in each tool is the point: a caller that
    # has to recognise this for itself is a caller that will forget.
    if not os.path.isdir(folder):
        raise SystemExit(
            f"no recording at {folder!r}.\n"
            f"  A recording is a FOLDER of <name>_c.png / <name>_d.png frames "
            f"plus intr.json,\n"
            f"  written by `python scrub3d/live/live_body.py --dump`, which "
            f"needs a depth camera.\n"
            f"  This checkout contains none, so the rig tools that replay one "
            f"cannot run here.")
    with open(os.path.join(folder, "intr.json"), encoding="utf-8") as f:
        intr = json.load(f)
    files = set(os.listdir(folder))
    names = sorted(nm[:-6] for nm in files if nm.endswith("_d.png"))
    lossless = bool(names)
    if not lossless:                       # the first recordings: JPEG colour
        names = sorted(nm[:-4] for nm in files if nm.endswith(".png"))
    times = None
    if "times.json" in files:
        with open(os.path.join(folder, "times.json"), encoding="utf-8") as f:
            kept = json.load(f)
        # A recording stopped early has frames past its last saved time. Play
        # the ones it timed, on their own clock, rather than throwing every
        # time away and playing the lot at a made up rate.
        timed = [(nm, kept[nm]) for nm in names if kept.get(nm) is not None]
        if timed and len(timed) < len(names):
            print(f"  {len(names) - len(timed)} frames past the last saved time "
                  f"are left out; {len(timed)} kept", flush=True)
        if timed:
            names = [nm for nm, _ in timed]
            times = [t for _, t in timed]
    if times is None:
        times = [i / fps for i in range(len(names))]
    span = times[-1] - times[0] + 1.0 / fps

    def frames():
        start, lap = time.time(), 0
        while True:
            for nm, t in zip(names, times):
                if lossless:
                    color = cv2.imread(os.path.join(folder, nm + "_c.png"))
                    depth = cv2.imread(os.path.join(folder, nm + "_d.png"),
                                       cv2.IMREAD_UNCHANGED).astype(np.float32)
                else:
                    color = cv2.imread(os.path.join(folder, nm + ".jpg"))
                    depth = cv2.imread(os.path.join(folder, nm + ".png"),
                                       cv2.IMREAD_UNCHANGED).astype(np.float32)
                yield color, depth, start + lap * span + (t - times[0])
            if not loop:
                return
            lap += 1
    return intr, frames()


class Dumper:
    """Keeps frames for --replay, written on a thread so the loop never waits.

    Colour is kept losslessly: JPEG moves the edges of a dark sleeve enough
    to change what the outline measures.

    The times are saved every TIMES_EVERY frames, not only at the last one:
    a recording stopped early used to keep its frames and lose every time,
    which leaves the folder unreplayable. They are written to a temporary
    name and moved over the old file, so a stop mid write cannot truncate
    the times that were already good.
    """

    TIMES_EVERY = 25

    def __init__(self, folder, intr, limit):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "intr.json"), "w", encoding="utf-8") as f:
            json.dump(intr, f)
        self.folder, self.limit, self.k = folder, limit, 0
        self.times = {}
        self.q = queue.Queue(maxsize=6)
        threading.Thread(target=self._run, daemon=True).start()

    def put(self, color, depth, t):
        if self.k < self.limit:
            key = f"{self.k:05d}"
            self.times[key] = t
            try:
                self.q.put_nowait((self.k, color, depth))
                self.k += 1
            except queue.Full:
                del self.times[key]

    def _run(self):
        while True:
            k, color, depth = self.q.get()
            base = os.path.join(self.folder, f"{k:05d}")
            cv2.imwrite(base + "_c.png", color, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            cv2.imwrite(base + "_d.png", np.clip(depth, 0, 65535).astype(np.uint16),
                        [cv2.IMWRITE_PNG_COMPRESSION, 1])
            if k == self.limit - 1 or (k + 1) % self.TIMES_EVERY == 0:
                self._save_times()
            self.q.task_done()

    def _save_times(self):
        out = os.path.join(self.folder, "times.json")
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(self.times), f)
        os.replace(tmp, out)

    def close(self):
        self.q.join()
        self._save_times()


def _box_points(bv, step=30.0):
    """Points over a box's six faces, from its 8 corners in _obox's order."""
    bv = np.asarray(bv, float)
    ext = [np.linalg.norm(bv[4] - bv[0]), np.linalg.norm(bv[2] - bv[0]),
           np.linalg.norm(bv[1] - bv[0])]
    g = [np.linspace(0.0, 1.0, max(int(e / step) + 1, 2)) for e in ext]
    out = []
    for fix in range(3):
        o = [i for i in range(3) if i != fix]
        U, V = np.meshgrid(g[o[0]], g[o[1]], indexing="ij")
        for val in (0.0, 1.0):
            w = np.zeros((U.size, 3))
            w[:, fix] = val
            w[:, o[0]] = U.ravel()
            w[:, o[1]] = V.ravel()
            x, y, z = w[:, :1], w[:, 1:2], w[:, 2:]
            out.append(bv[0] * (1 - x) * (1 - y) * (1 - z) + bv[1] * (1 - x) * (1 - y) * z
                       + bv[2] * (1 - x) * y * (1 - z) + bv[3] * (1 - x) * y * z
                       + bv[4] * x * (1 - y) * (1 - z) + bv[5] * x * (1 - y) * z
                       + bv[6] * x * y * (1 - z) + bv[7] * x * y * z)
    return np.vstack(out)


class Live:
    """One seated person, followed frame by frame.

    Keeps the measurements, the posed model and the chair between frames.
    step() takes one aligned, upright colour and depth frame.
    """

    def __init__(self, intr, seat_mm=None, keep_floor=False):
        self.intr, self.seat_mm = intr, seat_mm
        self.world = WorldFrame(keep_floor)
        self.T_wc = self.cam = None
        self.D = dims()
        self.body = Body(self.D)
        self.pose = mp.solutions.pose.Pose(
            static_image_mode=False, model_complexity=1, enable_segmentation=True,
            smooth_segmentation=True, min_detection_confidence=0.5,
            min_tracking_confidence=0.5)
        self.mask = self.color = None
        self.seen = collections.deque(maxlen=60)   # was the person found, per frame
        self.tfit = None
        self.px, self.vis, self.J, self.clean = {}, {}, {}, {}
        self.last_build = 0.0
        self.restart()

    def restart(self):
        """Forget the pose and the chair: a new world frame invalidates both."""
        self.Ws, self.Dir, self.filters = {}, {}, {}
        self.smoothed = {}               # key -> (last value, when), for MAX_JOINT_V
        self.flips = {}                  # key -> readings too fast to be the person
        self.up_s = self.anchor = self.centre_s = None
        self.seat, self.seat_z, self.seat_xy = None, [], []

    def close(self):
        self.pose.close()

    def step(self, color, depth, now):
        """One frame. -> the set of what happened: 'frame', 'seat', 'posed'."""
        ev = set()
        self.now = now
        H, W = depth.shape
        small = cv2.resize(color, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
        res = self.pose.process(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        found = not (res.segmentation_mask is None or res.pose_landmarks is None
                     or res.pose_world_landmarks is None)
        self.seen.append(found)
        self.color = color
        if not found:
            return ev
        mask = cv2.resize((res.segmentation_mask > 0.5).astype(np.uint8), (W, H),
                          interpolation=cv2.INTER_NEAREST).astype(bool)
        self.mask, self.color, self.depth = mask, color, depth
        intr, D, body = self.intr, self.D, self.body
        if self.world.feed(depth, mask, intr, now):
            self.T_wc, self.cam = self.world.T, self.world.T[:3, 3]
            self.restart()
            ev.add("frame")
        if self.T_wc is None:
            return ev
        T_wc, cam, Ws, Dir = self.T_wc, self.cam, self.Ws, self.Dir

        lms = res.pose_landmarks.landmark
        px, vis, J, clean, zc = {}, {}, {}, {}, {}
        for name, i in LM.items():
            p = lms[i]
            vis[name] = float(p.visibility)
            px[name] = (float(p.x) * W, float(p.y) * H)
            if p.visibility < SEEN:
                continue
            w, spread, zz = lift(px[name], mask, depth, intr, T_wc)
            if w is not None:
                J[name], clean[name], zc[name] = w, spread < 40.0, zz
        R3 = T_wc[:3, :3]
        for name, i in LM.items():
            q = res.pose_world_landmarks.landmark[i]
            v3 = R3 @ np.array([q.x, q.y, q.z]) * 1000.0
            Ws[name] = v3 if name not in Ws else Ws[name] + SMOOTH * (v3 - Ws[name])
        if "l_shoulder" in J and "r_shoulder" in J:
            a_now = (J["l_shoulder"] + J["r_shoulder"]) / 2.0
            self.anchor = (a_now if self.anchor is None
                           else self.anchor + SMOOTH * (a_now - self.anchor))
        self.px, self.vis, self.J, self.clean = px, vis, J, clean

        # --- measure what this frame shows clearly --------------------
        def trusted(*names):
            return all(nm in J and vis[nm] >= TRUSTED and clean.get(nm)
                       for nm in names)

        def seen(*names):
            return all(vis[nm] >= TRUSTED for nm in names)

        def dist(x, y):
            return float(np.linalg.norm(J[x] - J[y]))

        def mid_px(x, y):
            return ((px[x][0] + px[y][0]) / 2.0, (px[x][1] + px[y][1]) / 2.0)

        fx = intr["fx"]
        z_chest = None
        if trusted("l_shoulder", "r_shoulder"):
            D["shoulders"].add(dist("l_shoulder", "r_shoulder"))
            z_chest = (zc["l_shoulder"] + zc["r_shoulder"]) / 2.0
        if trusted("l_ear", "r_ear"):
            D["head_w"].add(1.05 * dist("l_ear", "r_ear"))
        arms = []
        if z_chest is not None:
            r_arm = (D["upper_arm_w"].value() / 2.0 + 10.0) * fx / z_chest
            for s_ in ("l", "r"):
                for x_, y_ in ((f"{s_}_shoulder", f"{s_}_elbow"),
                               (f"{s_}_elbow", f"{s_}_wrist"),
                               (f"{s_}_wrist", f"{s_}_index")):
                    if vis[x_] >= SEEN and vis[y_] >= SEEN:
                        # From a little below the shoulder: the shoulder
                        # itself IS the top of the torso's outline.
                        a_ = px[x_]
                        if x_.endswith("shoulder"):
                            a_ = (a_[0] + 0.25 * (px[y_][0] - a_[0]),
                                  a_[1] + 0.25 * (px[y_][1] - a_[1]))
                        arms.append((a_, px[y_], r_arm))
        tfit = None
        if z_chest is not None and min(vis["l_hip"], vis["r_hip"]) >= SEEN:
            tfit = torso_front(mask, depth, px, intr, T_wc, arms)
        self.tfit = tfit
        if z_chest is not None and seen("l_hip", "r_hip"):
            p_sh, p_hp = mid_px("l_shoulder", "r_shoulder"), mid_px("l_hip", "r_hip")
            # Read in the image at the chest's depth, because the depth AT a
            # hip is the lap in front of it; then undone for how far the
            # torso leans toward or away from the camera, which MediaPipe's
            # own 3D skeleton knows.
            ax = R3.T @ (Ws["l_shoulder"] + Ws["r_shoulder"] - Ws["l_hip"] - Ws["r_hip"])
            ax = ax / (np.linalg.norm(ax) + 1e-9)
            square = max(float(np.hypot(ax[0], ax[1])), 0.6)
            D["torso_len"].add(float(np.hypot(p_sh[0] - p_hp[0], p_sh[1] - p_hp[1]))
                               * z_chest / fx / square)
            D["hip_w"].add(float(np.hypot(px["l_hip"][0] - px["r_hip"][0],
                                          px["l_hip"][1] - px["r_hip"][1]))
                           * z_chest / fx)
            half = 0.62 * D["shoulders"].value()
            for w_ in run_widths(mask, depth, p_sh, p_hp, (0.25, 0.35), half, fx,
                                 z_ref=z_chest, step=TORSO_STEP_MM, avoid=arms):
                D["chest_w"].add(w_)
            for w_ in run_widths(mask, depth, p_sh, p_hp, (0.75, 0.85), half, fx,
                                 z_ref=z_chest, step=TORSO_STEP_MM, avoid=arms):
                D["waist_w"].add(w_)
        def jpt(name, r):
            """The joint behind landmark `name`: r further along the camera ray."""
            return J[name] + unit(J[name] - cam) * r

        def side_on(p0, p1):
            """A bone seen mostly from the side. One pointing at the camera
            hides its far end behind its near one, and reads short."""
            return abs(float(unit(p1 - p0) @ unit((p0 + p1) / 2.0 - cam))) < 0.5

        legs_seen, knees = {}, {}
        for side, s in (("L", "l"), ("R", "r")):
            ua_, fa_ = body.parts[f"upper_arm_{side}"], body.parts[f"forearm_{side}"]
            if trusted(f"{s}_shoulder", f"{s}_elbow"):
                p0, p1 = jpt(f"{s}_shoulder", ua_["a0"]), jpt(f"{s}_elbow", ua_["a1"])
                if side_on(p0, p1):
                    D["upper_arm_len"].add(float(np.linalg.norm(p1 - p0)))
                for w_ in run_widths(mask, depth, px[f"{s}_shoulder"],
                                     px[f"{s}_elbow"], (0.35, 0.5, 0.65), 85.0, fx,
                                     balanced=True):
                    D["upper_arm_w"].add(w_)
            if trusted(f"{s}_elbow", f"{s}_wrist"):
                p0, p1 = jpt(f"{s}_elbow", ua_["a1"]), jpt(f"{s}_wrist", fa_["a1"])
                if side_on(p0, p1):
                    D["forearm_len"].add(float(np.linalg.norm(p1 - p0)))
                for w_ in run_widths(mask, depth, px[f"{s}_elbow"], px[f"{s}_wrist"],
                                     (0.2, 0.3), 70.0, fx):
                    D["forearm_w"].add(w_)
                for w_ in run_widths(mask, depth, px[f"{s}_elbow"], px[f"{s}_wrist"],
                                     (0.85, 0.92), 50.0, fx):
                    D["wrist_w"].add(w_)
            if trusted(f"{s}_wrist", f"{s}_index", f"{s}_pinky"):
                mid = (J[f"{s}_index"] + J[f"{s}_pinky"]) / 2.0
                D["hand_len"].add(HAND_PER_LANDMARK
                                  * float(np.linalg.norm(mid - J[f"{s}_wrist"])))
            if trusted(f"{s}_knee"):
                legs_seen[side] = True
                # The knee joint is about a knee's radius behind what the
                # camera sees of it.
                kp = J[f"{s}_knee"]
                k_now = kp - unit(cam - kp) * 45.0
                kk = f"{s}_knee_pt"
                Dir[kk] = k_now if kk not in Dir else Dir[kk] + SMOOTH * (k_now - Dir[kk])
                knees[side] = Dir[kk]

        if now - self.last_build > REBUILD_S:
            body.build(D)
            self.last_build = now
        if self.anchor is None:
            return ev
        mp_up = unit(Ws["l_shoulder"] + Ws["r_shoulder"] - Ws["l_hip"] - Ws["r_hip"])
        centre = None
        if tfit is not None:
            # Lean toward the camera from the measured front; sideways
            # lean, which the front cannot show, from MediaPipe.
            b = float(np.clip(tfit["b"], -1.2, 1.2))
            side_lean = float(np.clip(mp_up[1] / max(mp_up[2], 0.3), -0.3, 0.3))
            # A seated torso, however the front reads: hands in the lap or a
            # phone held low make the front look like a steep lean back.
            u_now = clamp_lean(np.array([b, side_lean, 1.0]))
            z_sh = float((J["l_shoulder"][2] + J["r_shoulder"][2]) / 2.0)
            front = np.array([tfit["a"] + b * z_sh, tfit["y"], z_sh])
            c_now = front - unit(np.array([1.0, 0.0, -b])) * body.parts["torso"]["a"]
            self.centre_s = (c_now if self.centre_s is None
                             else self.centre_s + SMOOTH * (c_now - self.centre_s))
            centre = self.centre_s
        else:
            u_now = clamp_lean(mp_up)
        self.up_s = u_now if self.up_s is None else unit(self.up_s + 0.3 * (u_now - self.up_s))
        toward = unit(cam - self.anchor)
        if centre is None:
            centre = (self.centre_s if self.centre_s is not None
                      else self.anchor - toward * body.parts["torso"]["a"])
        # --- the joints: MediaPipe says where in the image, the depth says
        # how far, and the measured bone lengths keep that honest.
        msh = (Ws["l_shoulder"] + Ws["r_shoulder"]) / 2.0
        k = D["shoulders"].value() / max(
            float(np.linalg.norm(Ws["l_shoulder"] - Ws["r_shoulder"])), 1e-6)
        P = body.parts
        arms = {}
        for side, s in (("L", "l"), ("R", "r")):
            ua, fa = P[f"upper_arm_{side}"], P[f"forearm_{side}"]
            if f"{s}_shoulder" in J:
                sh = jpt(f"{s}_shoulder", ua["a0"])
            else:
                sh = self.anchor - toward * ua["a0"] + (Ws[f"{s}_shoulder"] - msh) * k
            el, wr = self.arm(s, sh, ua, fa, D["upper_arm_len"].value(),
                              D["forearm_len"].value())
            if all(f"{s}_{x}" in J for x in ("index", "pinky", "wrist")):
                hd = (J[f"{s}_index"] + J[f"{s}_pinky"]) / 2.0 - J[f"{s}_wrist"]
            else:
                hd = (Ws[f"{s}_index"] + Ws[f"{s}_pinky"]) / 2.0 - Ws[f"{s}_wrist"]
            arms[side] = (self.smooth(f"{s}_sh", sh), self.smooth(f"{s}_el", el),
                          self.smooth(f"{s}_wr", wr),
                          unit(self.smooth(f"{s}_hd", unit(hd), beta=2.0)))
        if "nose" in J:
            h_now = J["nose"] + unit(J["nose"] - cam) * NOSE_TO_HEAD_MM
        else:
            h_now = centre + self.up_s * 200.0
        head_c = self.smooth("head", h_now)
        body.pose_joints(Ws, cam, self.seat, self.up_s, centre, arms, head_c, knees,
                         legs_seen, hand_len=D["hand_len"].value())
        for tl in body.thighs.values():
            D["thigh_len"].add(tl)
        if self.seat is None and D["torso_len"].n >= 15:
            # Where the measured torso ends. A knee is no guide: hands
            # resting on it put its surface well above the joint.
            self.seat_z.append(float(body.butt[2]))
            self.seat_xy.append((centre.copy(), clamp_lean(mp_up, SEAT_LEAN_DEG)))
            if len(self.seat_z) >= SEAT_SAMPLES:
                seen = float(np.median(self.seat_z))
                z = self.seat_mm if self.seat_mm else seen
                # Where the torso meets THAT height, frame by frame.
                xy = np.median(np.array([c[:2] - u[:2] * (c[2] - z) / max(u[2], 0.5)
                                         for c, u in self.seat_xy]), axis=0)
                self.world.seat_ok = abs(seen - z) < 150.0
                self.seat = {"z": z, "z_seen": seen, "xy": xy, "fwd": body.fwd.copy(),
                             "up": np.median(np.array([u for _c, u in self.seat_xy]),
                                             axis=0)}
                body.set_seat(self.seat)
                ev.add("seat")
        ev.add("posed")
        return ev

    def smooth(self, key, v, beta=None):
        """Steady while still, quick to follow a moving limb (One Euro), and
        never moving a joint faster than a person can (MAX_JOINT_V)."""
        f = self.filters.get(key)
        if f is None:
            f = self.filters[key] = OneEuro(beta=SMOOTH_BETA if beta is None else beta)
        v = np.asarray(v, float)
        was = self.smoothed.get(key)
        if was is not None:
            dt = max(self.now - was[1], 1e-3)
            d = v - was[0]
            L = float(np.linalg.norm(d))
            cap = MAX_JOINT_V * dt
            if L > cap:
                v = was[0] + d * (cap / L)
                self.flips[key] = self.flips.get(key, 0) + 1
        out = f(v, self.now)
        self.smoothed[key] = (np.asarray(out, float).copy(), self.now)
        return out

    def arm(self, s, sh, ua, fa, L1, L2):
        """-> (elbow, wrist) joint centres for one arm."""
        J, vis, px, cam, Ws = self.J, self.vis, self.px, self.cam, self.Ws
        e, w = f"{s}_elbow", f"{s}_wrist"
        mp_ua = unit(Ws[e] - Ws[f"{s}_shoulder"])
        mp_fa = unit(Ws[w] - Ws[e])
        if vis.get(e, 0.0) < SEEN:
            el = sh + mp_ua * L1
            if vis.get(w, 0.0) < SEEN:
                return el, el + mp_fa * L2
            return el, self.chain(w, el, L2, fa["a1"], mp_fa)

        def obs(name, r, sure, unsure):
            if name not in J:
                return None, None
            t = float(np.linalg.norm(J[name] - cam)) + r
            return t, (sure if self.clean.get(name) else unsure)

        de = pixel_ray(px[e], self.intr, self.T_wc)
        te, se = obs(e, ua["a1"], 60.0, 150.0)
        cz = self.T_wc[:3, 2]
        p_sh = px[f"{s}_shoulder"]
        ua_s = self.line_samples(p_sh, px[e], (0.3, 0.45, 0.6, 0.75))
        if vis.get(w, 0.0) < SEEN:
            el, _ = solve_arm(sh, de, te, se, None, None, None, L1, L2, cam, cz,
                              ua_s=ua_s, ua_r=(ua["a0"], ua["a1"]))
            return el, el + mp_fa * L2
        dw = pixel_ray(px[w], self.intr, self.T_wc)
        tw, sw = obs(w, fa["a1"], 40.0, 90.0)
        fa_s = self.line_samples(px[e], px[w], (0.25, 0.4, 0.55, 0.7, 0.85))
        # A forearm pointing at the camera looks short in the image. Then the
        # elbow is hidden behind it and the hand stands in front of the
        # wrist, so both readings are near misses: the lengths decide.
        z_ref = te if te is not None else float(np.linalg.norm(sh - cam))
        seen_len = math.hypot(px[w][0] - px[e][0], px[w][1] - px[e][1]) * z_ref \
            / self.intr["fx"]
        if seen_len < 0.6 * L2:
            se = None if se is None else max(se, 200.0)
            sw = None if sw is None else max(sw, 120.0)
        self.fore = seen_len / L2
        return solve_arm(sh, de, te, se, dw, tw, sw, L1, L2, cam, cz,
                         ua_s=ua_s, fa_s=fa_s, ua_r=(ua["a0"], ua["a1"]),
                         fa_r=(fa["a0"], fa["a1"]))

    def line_samples(self, p0, p1, ts, radius=4):
        """The nearest surface at points along an image segment. -> [(t, z)]"""
        depth, mask = self.depth, self.mask
        H, W = depth.shape
        out = []
        for t in ts:
            x = int(round(p0[0] + t * (p1[0] - p0[0])))
            y = int(round(p0[1] + t * (p1[1] - p0[1])))
            if not (radius <= x < W - radius and radius <= y < H - radius):
                continue
            sub = depth[y - radius:y + radius + 1, x - radius:x + radius + 1]
            z = sub[mask[y - radius:y + radius + 1, x - radius:x + radius + 1]
                    & (sub > 0)]
            if len(z) < 6:
                continue
            q20 = float(np.percentile(z, 20))
            out.append((float(t), float(np.median(z[z <= q20 + 30.0]))))
        return out

    def chain(self, name, parent, L, r, fallback):
        """The joint `name`, L from `parent`: where the camera saw it, or on
        its image ray at the depth that keeps the bone its measured length."""
        J, cam = self.J, self.cam
        if self.vis.get(name, 0.0) < SEEN:
            return parent + unit(fallback) * L
        if name in J:
            guess = J[name] + unit(J[name] - cam) * r
            if abs(float(np.linalg.norm(guess - parent)) - L) < LENGTH_SLACK * L:
                return guess
        else:
            guess = parent + unit(fallback) * L
        return on_ray(self.px[name], parent, L, guess, self.intr, self.T_wc)


def part_arrays(body):
    """-> {name: (vertices, patch cells, faces)} of the posed model, in the world."""
    out = {}
    for name, p in body.parts.items():
        T = body.T.get(name)
        if T is None:
            continue
        R, t = T[:3, :3], T[:3, 3]
        sc = np.array([1.0, 1.0, body.S.get(name, 1.0)])
        out[name] = ((p["V"] * sc) @ R.T + t, (p["cells"] * sc) @ R.T + t, p["F"])
    return out


def diag(live, depth, live_mesh, n, folder):
    """How well the model sits on the live surface: numbers, and a picture.

    surface: the model's front, as the camera would see it, minus the real
    front, per part, in mm. Positive means the model is BEHIND the person.
    patch: the same for the scrub patches, and the share of them that land
    on the person at all.
    """
    import open3d as o3d
    body, intr, T = live.body, live.intr, live.T_wc
    R, t = T[:3, :3], T[:3, 3]
    parts = part_arrays(body)
    scene = o3d.t.geometry.RaycastingScene()
    names = []
    for name, (V, _C, F) in parts.items():
        Vc = ((V - t) @ R).astype(np.float32)
        scene.add_triangles(o3d.core.Tensor(Vc), o3d.core.Tensor(F.astype(np.uint32)))
        names.append(name)
    H, W = depth.shape
    ys, xs = np.mgrid[0:H:4, 0:W:4]
    xs, ys = xs.ravel(), ys.ravel()
    d = np.stack([(xs - intr["ppx"]) / intr["fx"], (ys - intr["ppy"]) / intr["fy"],
                  np.ones(len(xs))], 1)
    rays = np.concatenate([np.zeros_like(d), d], 1).astype(np.float32)
    r = scene.cast_rays(o3d.core.Tensor(rays))
    th = r["t_hit"].numpy()
    gid = r["geometry_ids"].numpy()
    hit = np.isfinite(th)
    lz = depth[ys, xs]
    m = live.mask[ys, xs] & (lz > 0)
    rows = []
    for i, name in enumerate(names):
        sel = hit & (gid == i) & m
        if sel.sum() < 10:
            continue
        df = th[sel] - lz[sel]
        q1, q2, q3 = np.percentile(df, [25, 50, 75])
        near = df[np.abs(df) < 60.0]
        nm_ = f"{np.median(near):+.0f}" if len(near) >= 5 else "--"
        rows.append(f"{name} {q2:+.0f} [{q1:+.0f},{q3:+.0f}] near {nm_} "
                    f"front {np.mean(df < -60.0):.2f} behind {np.mean(df > 60.0):.2f}")
    cover = (hit & m).sum() / max(m.sum(), 1)
    spill = (hit & ~m).sum() / max(hit.sum(), 1)
    prow = []
    for name, (_V, C, _F) in parts.items():
        if name not in PATCH_RGB or not len(C):
            continue
        Cc = (C - t) @ R
        u = np.round(Cc[:, 0] / Cc[:, 2] * intr["fx"] + intr["ppx"]).astype(int)
        v = np.round(Cc[:, 1] / Cc[:, 2] * intr["fy"] + intr["ppy"]).astype(int)
        ok = (u >= 0) & (u < W) & (v >= 0) & (v < H)
        u, v, zc = u[ok], v[ok], Cc[ok, 2]
        on = live.mask[v, u] & (depth[v, u] > 0)
        if on.sum() < 5:
            prow.append(f"{name} OFF")
            continue
        df = zc[on] - depth[v[on], u[on]]
        prow.append(f"{name} {np.median(df):+.0f} on {on.mean():.2f}")
    up = live.up_s
    tf = live.tfit
    print(f"ALIGN {n}: " + "; ".join(rows)
          + f" | cover {cover:.2f} spill {spill:.2f}", flush=True)
    print(f"PATCH {n}: " + "; ".join(prow)
          + f" | up {np.round(up, 2)} lean {math.degrees(math.acos(np.clip(up[2], -1, 1))):.0f}deg"
          + (f" tfit b {tf['b']:+.2f} n {tf['n']}" if tf else " tfit none")
          + f" reach {body.sitting_height:.0f}", flush=True)
    # Each model joint, projected, against the joint MediaPipe found.
    jrow = []
    PJ = body.joints
    for nm in ("l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
               "l_hip", "r_hip", "l_knee", "r_knee"):
        if nm not in PJ or nm not in live.px:
            continue
        q = (PJ[nm] - t) @ R
        u, v = q[0] / q[2] * intr["fx"] + intr["ppx"], q[1] / q[2] * intr["fy"] + intr["ppy"]
        e = math.hypot(u - live.px[nm][0], v - live.px[nm][1])
        dz = f" dz {q[2] - float((live.J[nm] - t) @ R[:, 2]):+.0f}" if nm in live.J else ""
        jrow.append(f"{nm} {e:.0f}px{dz}")
    print(f"JOINT {n}: " + "; ".join(jrow), flush=True)
    if live_mesh is not None:
        side_plot(live, live_mesh[0], parts, os.path.join(folder, f"side_{n:05d}.png"))
    if live.color is not None:
        overlay(live, parts, os.path.join(folder, f"over_{n:05d}.jpg"))


# Where the measurements land for anything outside this process. /tmp so it
# never pollutes the repo and never survives a reboot: a stale body from a
# different person is worse than no body at all.
SHARE_PATH = os.environ.get("SCRUB3D_DIMS", "/tmp/wheelgentic-dims.json")


def _share_dims(D, seat, sitting_height):
    """Write the measured dimensions where another process can read them.

    ATOMIC, because a reader polling this will otherwise catch a half-written
    file and get a body with three limbs. Write beside it and rename, which
    is atomic on the same filesystem.

    NEVER RAISES. This rides the loop that drives the arms; a full disk must
    cost a stale file, not a run.
    """
    try:
        out = {"t": time.time(),
               "seat_mm": None if seat is None else
                          [round(float(v), 1) for v in
                           (list(seat.get("xy", [0, 0])) + [seat.get("z", 0.0)])],
               "sitting_height_mm": None if sitting_height is None
                                    else round(float(sitting_height), 1),
               # Each dimension with the SAMPLE COUNT behind it, because a
               # value blended mostly from the typical-adult prior is not a
               # measurement and a reader has to be able to tell.
               "mm": {k: round(float(m.value()), 1) for k, m in D.items()},
               "n": {k: int(m.n) for k, m in D.items()}}
        tmp = SHARE_PATH + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh)
        os.replace(tmp, SHARE_PATH)
    except Exception:                                        # noqa: BLE001
        pass


def overlay(live, parts, path):
    """The model drawn over the camera image, with MediaPipe's joints."""
    intr, T = live.intr, live.T_wc
    R, t = T[:3, :3], T[:3, 3]
    img = (live.color // 2).copy()
    H, W = img.shape[:2]

    def proj(P):
        q = (np.asarray(P, float).reshape(-1, 3) - t) @ R
        u = q[:, 0] / q[:, 2] * intr["fx"] + intr["ppx"]
        v = q[:, 1] / q[:, 2] * intr["fy"] + intr["ppy"]
        return np.stack([u, v], 1), q[:, 2]

    for name, (V, C, _F) in parts.items():
        uv, _ = proj(V)
        for x, y in uv[::2].astype(int):
            if 0 <= x < W and 0 <= y < H:
                img[y, x] = (120, 170, 210)
        if name in PATCH_RGB and len(C):
            uv, _ = proj(C)
            col = tuple(int(c) for c in PATCH_RGB[name][::-1])
            for x, y in uv.astype(int):
                if 0 <= x < W and 0 <= y < H:
                    cv2.circle(img, (int(x), int(y)), 2, col, -1)
    PJ = live.body.joints
    for x, y in BONES:
        if x in PJ and y in PJ:
            uv, _ = proj(np.stack([PJ[x], PJ[y]]))
            cv2.line(img, tuple(uv[0].astype(int)), tuple(uv[1].astype(int)),
                     (255, 120, 0), 3)
    for nm, (x, y) in live.px.items():
        if live.vis.get(nm, 0.0) >= SEEN:
            cv2.drawMarker(img, (int(x), int(y)), (255, 255, 255), cv2.MARKER_CROSS, 18, 2)
    cv2.imwrite(path, cv2.resize(img, (W * 2 // 3, H * 2 // 3)),
                [cv2.IMWRITE_JPEG_QUALITY, 85])


def side_plot(live, P, parts, path):
    """The live surface and the model from the side and from above."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    body = live.body
    Tt = body.T["torso"]
    yc = float(Tt[1, 3])
    fig, ax = plt.subplots(1, 3, figsize=(21, 8))
    # 1: the middle of the body, side on
    sl = np.abs(P[:, 1] - yc) < 35.0
    ax[0].scatter(P[sl, 0], P[sl, 2], s=2, c="k", label="you, live (middle slice)")
    loc = body.parts["torso"]["V"]
    mid = np.abs(loc[:, 1]) < 0.2 * np.abs(loc[:, 1]).max()
    tv, tc, _ = parts["torso"]
    ax[0].scatter(tv[mid, 0], tv[mid, 2], s=8, c="orange", label="model torso (middle)")
    cl = body.parts["torso"]["cells"]
    cm = np.abs(cl[:, 1]) < 0.15 * np.abs(cl[:, 1]).max()
    ax[0].scatter(tc[cm, 0], tc[cm, 2], s=8, c="purple", label="torso patches (middle)")
    ax[0].set_title("side, middle slice (camera to the right)")
    # 2: everything, side on
    ax[1].scatter(P[::2, 0], P[::2, 2], s=1, c="0.6", label="you, live")
    for name, (V, C, _F) in parts.items():
        ax[1].scatter(V[:, 0], V[:, 2], s=1, c="tan")
        if name in PATCH_RGB and len(C):
            ax[1].scatter(C[:, 0], C[:, 2], s=3,
                          c=[np.array(PATCH_RGB[name]) / 255.0])
    ax[1].set_title("side, everything")
    # 3: from above
    ax[2].scatter(P[::2, 1], P[::2, 0], s=1, c="0.6")
    for name, (V, C, _F) in parts.items():
        ax[2].scatter(V[:, 1], V[:, 0], s=1, c="tan")
        if name in PATCH_RGB and len(C):
            ax[2].scatter(C[:, 1], C[:, 0], s=3,
                          c=[np.array(PATCH_RGB[name]) / 255.0])
    ax[2].set_title("from above (camera at the top, your left to the right)")
    J = body.joints
    for x, y in BONES:
        if x in J and y in J:
            for k in (0, 1):
                ax[k].plot([J[x][0], J[y][0]], [J[x][2], J[y][2]], "b-", lw=2)
            ax[2].plot([J[x][1], J[y][1]], [J[x][0], J[y][0]], "b-", lw=2)
    if live.seat is not None and body.chair:
        for bv, _bf in body.chair:
            for k in (0, 1):
                ax[k].plot(bv[:, 0], bv[:, 2], ".", c="green", ms=4)
            ax[2].plot(bv[:, 1], bv[:, 0], ".", c="green", ms=4)
    for k in (0, 1):
        ax[k].axhline(0.0, c="brown")
        ax[k].set_xlabel("x: toward the camera (mm)")
        ax[k].set_ylabel("z: up (mm)")
    ax[2].set_xlabel("y: your left (mm)")
    ax[2].set_ylabel("x: toward the camera (mm)")
    for k in range(3):
        ax[k].set_aspect("equal")
        ax[k].grid(True, alpha=0.3)
    ax[0].legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=70)
    plt.close(fig)


class RigWatch:
    """The rig file, looked at once a second: the rig editor saves it while
    the view runs."""

    def __init__(self, path):
        self.path = path
        self.stamp = self._stamp()
        self.next_look = 0.0

    def _stamp(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def poll(self, now):
        """-> the new seat-relative arms, once per change; otherwise None."""
        if now < self.next_look:
            return None
        self.next_look = now + 1.0
        stamp = self._stamp()
        if stamp is None or stamp == self.stamp:
            return None
        self.stamp = stamp
        try:
            with open(self.path, encoding="utf-8") as f:
                rel = json.load(f)["arms"]
            if not isinstance(rel, list) or not 1 <= len(rel) <= 8:
                raise ValueError("a rig has 1 to 8 arms")
            for r in rel:
                for k in ("x_from_seat_mm", "y_from_seat_mm", "z_mm", "facing_deg"):
                    if not math.isfinite(float(r[k])):
                        raise ValueError(f"{k} is not a number")
                for k in ("tilt_deg", "roll_deg"):             # optional
                    if not math.isfinite(float(r.get(k, 0.0))):
                        raise ValueError(f"{k} is not a number")
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"  the rig file changed but cannot be used ({e}); "
                  f"the arms stay as they are", flush=True)
            return None
        return rel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=3600.0)
    ap.add_argument("--replay", help="a folder written by --dump, played instead "
                                     "of the camera")
    ap.add_argument("--once", action="store_true", help="with --replay: stop at its end")
    ap.add_argument("--replay-fps", type=float, default=9.0,
                    help="with --replay: the rate of a recording that kept no clock")
    ap.add_argument("--dump", help="keep the frames in this folder, for --replay")
    ap.add_argument("--dump-max", type=int, default=200)
    ap.add_argument("--dump-delay", type=float, default=0.0,
                    help="seconds after the view is up before --dump starts keeping")
    ap.add_argument("--diag", help="print how well the model sits on the live "
                                   "surface, and save side views in this folder")
    ap.add_argument("--diag-every", type=int, default=10)
    ap.add_argument("--no-viewer", action="store_true")
    ap.add_argument("--viewer-port", type=int, default=9876,
                    help="another port opens a second viewer window")
    ap.add_argument("--upside-down", action="store_true",
                    help="the camera is mounted upside down: turn its frames over")
    ap.add_argument("--no-arms", action="store_true", help="the person only")
    ap.add_argument("--rig", default=os.path.join(HERE, "live_rig.json"),
                    help="a seat-relative rig from search_rig.py (default: the "
                         "one searched for a person sitting at rest)")
    ap.add_argument("--project-rig", action="store_true",
                    help="use scrub3d/config.json instead: the rig searched "
                         "for the arms-out scan")
    ap.add_argument("--keep-floor", action="store_true",
                    help="hold the floor once found: the world never tilts again "
                         "under the person, whatever later fits say")
    ap.add_argument("--seat-mm", type=float, default=None,
                    help="the chair's seat height, if measured by tape; "
                         "otherwise it is taken from where the torso ends")
    ap.add_argument("--drive", nargs="?", const="arms", default=None,
                    metavar="fake",
                    help="drive the real arms named in arm_ports.json (see "
                         "arm_hw.py and ARMS.md); '--drive fake' drives "
                         "simulated boards instead")
    a = ap.parse_args()

    intr, frames = (replay_source(a.replay, loop=not a.once, fps=a.replay_fps)
                    if a.replay else camera_source(a.upside_down))
    live = Live(intr, a.seat_mm, a.keep_floor)
    arms = watch = None
    if a.rig and not a.no_arms and not a.project_rig:
        watch = RigWatch(a.rig)
        with open(a.rig, encoding="utf-8") as f:
            arms = Arms(rel=json.load(f)["arms"])
    elif not a.no_arms:
        arms = Arms(layout=rigconfig.load().layout())
    if arms is not None:
        arms.part_names = PART_WORDS
    hw = sight = None
    if a.drive is not None:
        if arms is None or arms.rel is None:
            raise SystemExit("--drive needs a seat-relative rig (--rig)")
        hw = connect_arms(arms, a.drive)
        if a.drive == "fake" or a.replay:
            print("  camera check of the real arms: off ("
                  + ("simulated boards" if a.drive == "fake" else "a recording")
                  + " cannot be seen)", flush=True)
        else:
            import arm_sight
            sight = {"sight": arm_sight.ArmSight(intr), "at": 0.0, "said": {},
                     "runs": {}}
    dump = Dumper(a.dump, intr, a.dump_max) if a.dump else None
    view = not a.no_viewer
    if view:
        rr.init("wheelgentic3d_live_body", spawn=False)
        # SPAWN ONLY IF NOBODY IS ALREADY LISTENING. rr.spawn starts the
        # desktop viewer, and a desktop window cannot be put beside her UI in
        # a browser. `rerun --serve-web` hosts the SAME viewer over HTTP and
        # accepts SDK connections on the same gRPC port, so when one is
        # already up this connects to it and the render appears in the page
        # instead of on the desktop.
        #
        # Checked by connecting a socket rather than by a flag, because the
        # server is usually started by hand and would not know about a flag.
        import socket as _sock
        _up = False
        try:
            with _sock.create_connection(("127.0.0.1", a.viewer_port), 0.4):
                _up = True
        except OSError:
            _up = False
        if not _up:
            rr.spawn(port=a.viewer_port, connect=False)
        rr.connect_grpc(f"rerun+http://127.0.0.1:{a.viewer_port}/proxy")
        rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)
        rr.send_blueprint(blueprint())
        if arms is not None:
            arms.log_static()
            arms.log()
    if a.diag:
        os.makedirs(a.diag, exist_ok=True)

    D, body = live.D, live.body
    n, t0, last_text, last_print, last_image = 0, time.time(), 0.0, 0.0, 0.0
    stamps = collections.deque(maxlen=30)
    dump_at, note = None, None
    news = {}               # what the console was told about the arms
    replan = False          # a new rig is placed, and plans on the next posed frame
    first_frame = None      # (recording time, wall time) of the first frame, when pacing
    try:
        for color, depth, t_frame in frames:
            if hw is not None and a.replay:
                # Real arms move in real time: a replay must not run ahead.
                if first_frame is None:
                    first_frame = (t_frame, time.time())
                ahead = (t_frame - first_frame[0]) - (time.time() - first_frame[1])
                if ahead > 0:
                    time.sleep(ahead)
            now = time.time()
            if now - t0 > a.seconds:
                break
            if dump is not None and live.T_wc is not None:
                # A countdown on screen first, so the person is ready.
                if dump_at is None:
                    dump_at = now + a.dump_delay
                if now < dump_at:
                    note = (f"Recording starts in {dump_at - now:.0f} s. Sit as you "
                            f"normally would, facing the camera.")
                elif dump.k < dump.limit:
                    if dump.k == 0:
                        print("  recording started", flush=True)
                    dump.put(color, depth, t_frame)
                    note = (f"RECORDING: {dump.k} of {dump.limit} frames. Sit as you "
                            f"normally would; move your arms now and then.")
                    if dump.k >= dump.limit:
                        print("  recording done", flush=True)
                else:
                    note = "Recording done. Thanks."
            ev = live.step(color, depth, t_frame)
            guarded = True
            if arms is not None and arms.layout and live.T_wc is not None:
                # What the camera measured, the arms (real ones where their
                # encoders put them) taken out: arms_live's depth guard.
                guarded = guard_scene(arms, hw, depth, intr, live.T_wc,
                                      body if "posed" in ev else None, live.mask)
            if (sight is not None and live.T_wc is not None and arms.layout
                    and now - sight["at"] > SIGHT_EVERY_S):
                sight["at"] = now
                look_at_arms(arms, hw, sight, depth, live.T_wc)
            if view and now - last_image > 0.3:
                last_image = now
                small = cv2.resize(color, (320, 180), interpolation=cv2.INTER_AREA)
                rr.log("camera", rr.Image(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)),
                       static=True)
            if live.T_wc is None and now - last_text > 0.5:
                last_text = now
                if view:
                    rr.log("measurements", rr.TextDocument(
                        waiting_note(live), media_type=rr.MediaType.MARKDOWN), static=True)
                if now - last_print > 10.0:
                    last_print = now
                    seen = list(live.seen)
                    print(f"  waiting: you are found in "
                          f"{100.0 * sum(seen) / max(len(seen), 1):.0f}% of frames; "
                          f"floor tries {live.world.tries} ({live.world.rough} too uneven)",
                          flush=True)
            if "frame" in ev:
                fl = live.world.floor
                print(f"  floor fitted live: camera {fl['camera_height_mm']:.0f}mm up, "
                      f"{fl['pitch_down_deg']:.1f} degrees down"
                      + (" (the camera moved; starting the body over)"
                         if live.world.moves else ""), flush=True)
                if view:
                    rr.log("world/model/seat", rr.Clear(recursive=False), static=True)
                if arms is not None:
                    arms.reset()
                    replan = False
                    if view:
                        arms.clear()
                        arms.log()
            if "seat" in ev:
                print(f"  the seat is {live.seat['z']:.0f}mm above the floor, fixed "
                      f"(the camera put it at {live.seat['z_seen']:.0f}mm)", flush=True)
                doubt = floor_doubt(live.seat)
                if doubt:
                    print("  " + doubt, flush=True)
                if view:
                    cm = body.chair_mesh()
                    rr.log("world/model/seat", rr.Mesh3D(
                        vertex_positions=cm[0], triangle_indices=cm[1].astype(np.uint32),
                        albedo_factor=CHAIR), static=True)
                if arms is not None:
                    arms.place(live.seat)
                    if view:
                        arms.log_static()
                        arms.log()
                    arms.start_plan(body)
                    replan = False
            rel = watch.poll(now) if watch is not None else None
            if rel is not None and hw is not None:
                print("  the rig file changed, but these arms are real and have not "
                      "moved: restart the live view to use it", flush=True)
                watch = None
                rel = None
            if rel is not None:
                # Saved from the rig editor: the arms start over where the
                # file now puts them.
                was = arms.new_rig(rel)
                print(f"  the rig file changed: {len(rel)} arms, planning again",
                      flush=True)
                if view:
                    arms.clear(was)
                if live.seat is not None:
                    arms.place(live.seat)
                    if view:
                        arms.log_static()
                        arms.log()
                    replan = True
            if replan and "posed" in ev:
                replan = False
                arms.start_plan(body)
            if arms is not None:
                line = arms.adopt()
                if line:
                    print(line, flush=True)
                # A frame the guard could not check is one the person is not
                # seen in: the arms hold (and lift off, if it lasts).
                arms.step(body, t_frame, hold="posed" not in ev or not guarded)
                for line in arms_news(arms, news, now):
                    print(line, flush=True)
                if hw is not None:
                    drive_arms(arms, hw, view)
                if view and arms.gov is not None:
                    arms.log()
            if "posed" not in ev:
                continue
            # Only the viewer and --diag ever read it: building it for a run
            # that has neither is work nobody looks at.
            live_mesh = (surface(depth, color, live.mask, intr, live.T_wc)
                         if view or a.diag else None)
            if view:
                if live_mesh is not None:
                    P, F, N, C = live_mesh
                    rr.log("world/live", rr.Mesh3D(vertex_positions=P,
                                                   triangle_indices=F.astype(np.uint32),
                                                   vertex_normals=N, vertex_colors=C),
                           static=True)
                arr = body.arrays()
                if arr is not None:
                    MV, MF, MC, MN, PP, PC = arr
                    rr.log("world/model/body", rr.Mesh3D(
                        vertex_positions=MV, triangle_indices=MF.astype(np.uint32),
                        vertex_normals=MN, vertex_colors=MC), static=True)
                    if arms is None or arms.owner is None:
                        rr.log("world/model/patches",
                               rr.Points3D(PP, colors=PC, radii=3.0), static=True)
                if arms is not None and arms.owner is not None:
                    # Each patch in its arm's colour, bright once scrubbed.
                    Pl, _N, _I, _A = body.scrub_cells()
                    own, done, reach = arms.cell_state(len(Pl))
                    rr.log("world/model/patches", rr.Points3D(
                        Pl, colors=territory_colours(own, done, reach),
                        radii=territory_radii(own, done)), static=True)
                PJ = body.joints
                segs = [np.stack([PJ[x], PJ[y]]) for x, y in BONES
                        if x in PJ and y in PJ]
                if segs:
                    rr.log("world/skeleton", rr.LineStrips3D(
                        segs, colors=[(255, 255, 255)], radii=6.0), static=True)
            if a.diag and n % a.diag_every == 0:
                diag(live, depth, live_mesh, n, a.diag)
            stamps.append(now)
            n += 1
            if now - last_text > 0.5 and len(stamps) > 1:
                fps = (len(stamps) - 1) / max(stamps[-1] - stamps[0], 1e-6)
                if view:
                    rr.log("measurements", rr.TextDocument(
                        (arms.summary() if arms is not None else "")
                        + table(D, fps, live.seat, body.sitting_height, len(live.seat_z),
                                note, arms is not None and arms.owner is not None),
                        media_type=rr.MediaType.MARKDOWN), static=True)
                last_text = now
                if now - last_print > 10.0:
                    last_print = now
                    print(f"  {n} frames, {fps:.1f}/s; " + ", ".join(
                        f"{k} {m.value():.0f}({m.n})" for k, m in D.items()), flush=True)
                # SHARE THE MEASUREMENTS. This loop measures thirteen body
                # dimensions off the silhouette and the depth, and until now
                # they existed only in this process and its viewer. The
                # projector measured three of them again, worse, from six
                # joint positions -- a second implementation of the same
                # question, which is the thing this project keeps paying for.
                #
                # Written to a file rather than a socket because this loop
                # must not gain a network dependency: it drives real arms,
                # and a reader that blocks or a port that is taken cannot be
                # allowed to matter here. A reader either finds the file or
                # does not.
                _share_dims(D, live.seat, body.sitting_height)
    finally:
        if hw is not None:
            missed = hw.close()
            print("  the arms drew back and are holding where they are"
                  + (f"; NOT confirmed for {', '.join(missed)}: switch their "
                     f"12 V supply off" if missed else ""), flush=True)
            print(f"  what the real arms did: python scrub3d/live/arm_hw.py --report "
                  f"\"{hw.drive_log.path}\"", flush=True)
        frames.close()
        live.close()
        if dump is not None:
            dump.close()
        if view:
            rr.disconnect()


def connect_arms(arms, how):
    """The real arms for --drive (or simulated boards). -> arm_hw.Hardware"""
    import arm_hw
    n = len(arms.rel)
    if how == "fake":
        hw = arm_hw.Hardware.fake(n)
    else:
        try:
            ports = arm_hw.load_ports()
        except arm_hw.ArmError as exc:
            raise SystemExit(str(exc))
        missing = [arm_hw.NAMES[i % 4] for i in range(n) if not ports.get(i)]
        if missing:
            raise SystemExit(f"arm_ports.json names no port for {', '.join(missing)}: "
                             f"run python scrub3d/live/arm_hw.py --assign")
        try:
            hw = arm_hw.Hardware.connect({i: ports[i] for i in range(n)})
        except arm_hw.ArmError as exc:
            raise SystemExit(f"the arms could not be started: {exc}")
    names = ", ".join(f"{arm.name} on {arm.link.port}" for arm in hw.arms.values())

    def start_from(layout):
        hw.place(layout)
        return hw.measured()

    arms.start_from = start_from
    arms.driving = True
    arms.hw_note = (f"**Driving the real arms:** {names}. Ctrl+C draws them back "
                    f"and holds them; the 12 V switch is the emergency stop.")
    hw.drive_log = arm_hw.DriveLog()
    hw.start()
    print(f"  driving {names}; Ctrl+C draws them back and holds them", flush=True)
    print(f"  every sample goes to {hw.drive_log.path}", flush=True)
    return hw


SIGHT_EVERY_S = 2.0
PART_WORDS = {"torso": "your body", "upper_arm_L": "your left upper arm",
              "forearm_L": "your left forearm", "upper_arm_R": "your right upper arm",
              "forearm_R": "your right forearm"}
PART_WORDS = [PART_WORDS[n] for n in SCRUB_PARTS]


def guard_scene(arms, hw, depth, intr, T_wc, body, mask=None):
    """The depth camera's surfaces for this frame, for the arms' depth guard.
    -> did it work?"""
    import depth_guard
    if hw is not None:
        joints = hw.joints()
        sponges = {a: arm.actual_world() for a, arm in hw.arms.items()}
    else:
        joints = list(arms.joints)
        sponges = {a: p for a, p in enumerate(arms.sponge)}
    try:
        scene = depth_guard.Scene(depth, intr, T_wc, arms.layout, joints, sponges,
                                  person=depth_guard.person_grid(mask))
    except Exception as exc:                            # noqa: BLE001
        print(f"  depth guard failed, the arms hold: {exc!r}", flush=True)
        arms.sense(None)
        return False
    arms.sense(scene, body)
    return True


WAIT_WORDS = {True: "waiting for its patch", "still": "waiting for you to hold still"}


def arms_news(arms, said, now):
    """Console lines on the arms: each finished pass, each change in what the
    depth guard holds them for, and where they are every 10 s. `said` keeps
    what was said already."""
    out = []
    if arms is None or arms.owner is None:
        said.clear()
        return out
    if len(arms.history) < said.get("hist", 0):
        said.clear()                                   # the arms started over
    for p, _ph, t, got in arms.history[said.get("hist", 0):]:
        start = said.pop("start", None)
        out.append(f"  pass {p} done" + (f" in {t - start:.0f} s" if start else "")
                   + ": " + ", ".join(f"{ARM_NAMES[a % 4]} {100 * f:.0f}%"
                                      for a, f in sorted(got.items())))
    said["hist"] = len(arms.history)
    if arms.phases and not arms.settling and arms.phase_t0 != said.get("t0"):
        said["t0"] = said["start"] = arms.phase_t0       # a pass (or turn) began
    names = arms.part_names or []
    held = ", ".join(names[q] if q < len(names) else f"part {q}"
                     for q in sorted(arms.distrust))
    if held != said.get("held", ""):
        out.append(f"  depth guard: the camera disagrees with the model of {held}; "
                   f"the arms keep off" if held else
                   "  depth guard: the camera and the model agree again")
        said["held"] = held
    lost = (arms.lost_since is not None and arms.t_prev is not None
            and arms.t_prev - arms.lost_since >= LOST_S)
    if lost != said.get("lost", False):
        out.append("  depth guard: you are out of view; the arms lift off and wait"
                   if lost else "  depth guard: you are in view again")
        said["lost"] = lost
    if arms.estopped and not said.get("stopped"):
        out.append(f"  ARMS STOPPED: {arms.status}")
        said["stopped"] = True
    if now - said.get("at", 0.0) >= 10.0:
        said["at"] = now
        prog = arms.progress(real=arms.driving)
        reach = sum(r for _f, _o, r in prog.values())
        done = sum(f * r for f, _o, r in prog.values())
        modes = ", ".join(
            f"{ARM_NAMES[a % 4]} " + ("waiting for the camera to agree" if a in arms.paused
                                      else WAIT_WORDS.get(arms.waiting.get(a))
                                      or arms.mode.get(a, "idle"))
            for a in sorted(prog))
        out.append(f"  arms: pass {arms.passes}, {100 * done / max(reach, 1e-9):.0f}% "
                   f"of {reach:.0f} cm2 in reach; {modes}")
    return out


def look_at_arms(arms, hw, sight, depth, T_wc):
    """Whether the camera sees each real arm where the rig file puts it
    (arm_sight): in the panel, the log, and the terminal once a verdict
    has held for two looks."""
    import arm_sight
    got = sight["sight"].check(depth, T_wc, arms.layout, hw.joints())
    if not got:
        return
    arms.sight_note = ("**Camera check of the real arms:** "
                       + "; ".join(arm_sight.words(a, r) for a, r in sorted(got.items()))
                       + ".")
    for a, r in got.items():
        v = r["verdict"]
        run = sight["runs"].get(a)
        sight["runs"][a] = (v, run[1] + 1) if run and run[0] == v else (v, 1)
        if sight["runs"][a][1] >= 2 and sight["said"].get(a) != v:
            sight["said"][a] = v
            print("  camera check: " + arm_sight.words(a, r).replace("**", ""),
                  flush=True)
        if hw.drive_log is not None:
            hw.drive_log.note("sight", arm=a, **r)


def drive_arms(arms, hw, view):
    """Each real arm follows its governor-approved point; the real arms are
    checked where their encoders put them, and credited only for the skin
    their sponges reached; a fault stops all."""
    if arms.gov is None:
        return                       # not planned yet: the arms hold still
    n = len(arms.sponge)
    approved = {a: arms.sponge[a].copy() for a in range(n)}
    on_skin = {a: arms.mode.get(a) == "working" and a in arms.tools
               and bool(arms.tools[a].contact) for a in range(n)}
    why = hw.update(approved, stop=arms.estopped, on_skin=on_skin)
    real = {a: arm.actual_world() for a, arm in hw.arms.items()}
    bad = arms.real_frame(hw.joints(), real)
    if bad and not why:
        hw.hold_all(bad)
        why = hw.reason
    log = hw.drive_log
    if log is not None:
        log.write(hw, approved, arms.mode, on_skin, arms.real_clear)
        done = arms.pause_for is not None
        key = (arms.passes, done)
        now = time.monotonic()
        if key != getattr(log, "last_key", None) or now - getattr(log, "last_at", 0) > 5.0:
            log.last_key, log.last_at = key, now
            prog, got = arms.progress(), arms.progress(real=True)
            reach = sum(r for _f, _o, r in prog.values())
            if reach >= 1.0:
                log.note("coverage", **{"pass": arms.passes, "done": done,
                                        "plan": sum(f * r for f, _o, r in prog.values())
                                        / reach,
                                        "real": sum(f * r for f, _o, r in got.values())
                                        / reach})
    if why and not arms.estopped:
        arms.estopped = True
        arms.status = f"STOPPED: {why}"
        arms.hw_note = (f"**The real arms are HOLDING where they are:** {why}. "
                        f"Restart the live view to go on.")
        print(f"  ARMS HOLDING: {why}", flush=True)
    if view:
        pts = [p for p in real.values() if p is not None]
        if pts:
            rr.log("world/arms/real", rr.Points3D(pts, colors=[(255, 255, 255)],
                                                  radii=12.0), static=True)


if __name__ == "__main__":
    main()
