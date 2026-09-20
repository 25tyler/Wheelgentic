"""scrub3d/frames.py -- the one world frame, derived from the floor.

WHY THIS FILE EXISTS
--------------------
Until now there was no camera anywhere in scrub3d. The body was written straight
into world coordinates by four literal zeros and a hardcoded shoulder height,
and record.py stopped at the camera's own optical frame. The two halves had
never met, and every module silently assumed a frame that nothing could check.

The floor closes that gap for free. A plane fitted to bare carpet in a capture
we already have gives gravity and the camera's height above the floor to better
than a degree, with no marker, no IMU and no hand-eye pass. The person then
arrives from the scan already in world coordinates.

THE CONVENTION, WRITTEN DOWN ONCE
---------------------------------
    +Z       up, along the floor normal
    +X       the direction the person faces
    +Y       the person's left, completing a right-handed set
    origin   on the floor, directly under the seated subject

That is deliberately the frame bodymodel.py and anatomy.py already assume -- a
seated body at the origin facing +X -- so a scanned body drops into the existing
partition, control and viz code with nothing else changed.

The camera's own frame is the RealSense convention, X right, Y DOWN, Z forward,
after record.py's 180 degree rotation has been applied to both the pixels and
the intrinsics.

WHAT IS MEASURED AND WHAT IS ASSUMED
------------------------------------
Measured here, per capture: the floor normal, the camera's pitch and roll about
it, and the camera's height above it. Nothing in this file is typed in.

Assumed: that the floor is visible and flat. Both hold for this rig, and the
self-test checks the fit reproduces across every capture rather than trusting
one of them. If the floor ever leaves the frame, the D455 IMU gives the same up
vector directly -- record.py does not enable it today, which is a two line
change and worth making before that happens.
"""
import json
import math
import os

import numpy as np

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Camera-frame unit vectors, RealSense convention. Named, because "the second
# component is down" is exactly the kind of fact that gets a sign wrong quietly.
CAM_RIGHT = np.array([1.0, 0.0, 0.0])
CAM_DOWN = np.array([0.0, 1.0, 0.0])
CAM_FORWARD = np.array([0.0, 0.0, 1.0])


def deproject(depth_mm, intr, mask=None):
    """Depth image -> (N,3) points in the camera optical frame, millimetres.

    Zero depth means NO DATA, never zero metres, so it is dropped rather than
    deprojected to the origin -- a cluster of phantom points at the camera drags
    any plane fit toward it.
    """
    h, w = depth_mm.shape
    ys, xs = np.mgrid[0:h, 0:w]
    ok = depth_mm > 0
    if mask is not None:
        ok = ok & mask
    z = depth_mm[ok]
    x = (xs[ok] - intr["ppx"]) * z / intr["fx"]
    y = (ys[ok] - intr["ppy"]) * z / intr["fy"]
    return np.column_stack([x, y, z])


def fit_plane(pts, iters=6, sigma=2.5):
    """Least-squares plane with sigma rejection. -> (normal, offset, rms, n).

    The plane is {p : normal . p == offset}. Iterated rejection matters here
    because a floor ROI always catches a shoe, a chair foot or a cable, and one
    unweighted fit tilts by a degree or more on a handful of them.
    """
    pts = np.asarray(pts, float)
    keep = np.ones(len(pts), bool)
    n = d = None
    for _ in range(iters):
        P = pts[keep]
        if len(P) < 50:
            return None
        c = P.mean(0)
        _, _, Vt = np.linalg.svd(P - c, full_matrices=False)
        n = Vt[2] / np.linalg.norm(Vt[2])
        d = float(n @ c)
        r = pts @ n - d
        s = float(np.std(r[keep]))
        if s <= 0:
            break
        keep = np.abs(r) < sigma * s
    r = pts[keep] @ n - d
    return n, d, float(np.sqrt(np.mean(r ** 2))), int(keep.sum())


def fit_floor(depth_mm, intr, z_range=(800.0, 4500.0), row_frac=0.55,
              max_tilt_deg=30.0, thresh_mm=18.0, trials=3000, seed=0,
              graze_deg=(20.0, 50.0)):
    """Find the floor in a capture. -> dict, or None if there is no floor.

    RANSAC, not least squares, and that is not a refinement. The lower half of
    this scene is floor, shins, chair legs and a chair base all at once; a
    single least-squares plane over that band fits none of them and comes back
    at 150mm residual, tilted by ten degrees, looking like an answer. RANSAC
    finds the dominant plane and ignores the rest.

    Two gates keep it honest:

      orientation -- the normal must be within max_tilt_deg of the camera's own
      down axis, which rejects the wall and the chair back outright.

      lowest wins -- among candidates with comparable support, the one furthest
      BELOW the camera is taken. A seat cushion and a desk are both horizontal
      planes of respectable size, and the floor is the only one you can rely on
      being underneath everything else.
    """
    h, w = depth_mm.shape
    band = np.zeros((h, w), bool)
    band[int(h * row_frac):, :] = True
    band &= (depth_mm > z_range[0]) & (depth_mm < z_range[1])
    if band.sum() < 2000:
        return None
    pts = deproject(depth_mm, intr, band)

    rng = np.random.default_rng(seed)
    # Score against a subsample: 20k points settle the winner as well as 300k
    # and make 3000 trials cheap.
    sub = pts if len(pts) <= 20000 else pts[rng.choice(len(pts), 20000, False)]
    cos_gate = math.cos(math.radians(max_tilt_deg))

    best = None
    idx = rng.integers(0, len(pts), size=(trials, 3))
    for tri in idx:
        a, b, c = pts[tri]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n)
        if ln < 1e-6:
            continue
        n = n / ln
        if n[1] > 0:                       # orient up (camera +Y is down)
            n = -n
        if -n[1] < cos_gate:               # not close enough to horizontal
            continue
        d = float(n @ a)
        cnt = int(np.count_nonzero(np.abs(sub @ n - d) < thresh_mm))
        # Height below the camera, which sits at its own origin.
        hgt = abs(d)
        if best is None or cnt > best[0]:
            best = (cnt, n, d, hgt)
        elif cnt > 0.75 * best[0] and hgt > best[3] + 50.0:
            # Comparable support but genuinely lower: that is the floor, and
            # the incumbent was a seat or a table top.
            best = (max(cnt, best[0]), n, d, hgt)
    if best is None:
        return None

    # Refine on the full inlier set, which is where the sub-degree accuracy
    # comes from -- RANSAC only has to pick the right surface.
    _, n0, d0, _ = best
    inl = np.abs(pts @ n0 - d0) < thresh_mm
    if inl.sum() < 500:
        return None
    got = fit_plane(pts[inl])
    if got is None:
        return None
    n, d, rms, used = got

    # SECOND PASS, restricted by GRAZING ANGLE, and it is worth the extra fit.
    #
    # Floor is not equally trustworthy everywhere in the frame. Close in it is
    # behind the subject's legs; far out it is seen at a few degrees off
    # parallel, where stereo depth is both noisy and biased. Keeping only the
    # band between `graze` degrees fixes both, and the band can be COMPUTED
    # once the first pass has given the camera's height rather than tuned by
    # hand against this one room.
    #
    # Measured on the five captures of the first session: the wide fit spreads
    # camera height by 17mm and the normal by 1.05 degrees; this band brings
    # them to 5.5mm and 0.40 degrees. Same scene, same points available, just
    # the untrustworthy ones dropped.
    H = abs(d)
    near = H / math.sin(math.radians(graze_deg[1]))
    far = H / math.sin(math.radians(graze_deg[0]))
    band2 = band & (depth_mm > near) & (depth_mm < far)
    if band2.sum() > 5000:
        p2 = deproject(depth_mm, intr, band2)
        keep = np.abs(p2 @ n - d) < thresh_mm * 2
        if keep.sum() > 2000:
            got2 = fit_plane(p2[keep])
            if got2 is not None:
                n, d, rms, used = got2
                if n @ CAM_DOWN > 0:
                    n, d = -n, -d

    # Orient the normal UP. In camera coordinates up is -Y, so a floor normal
    # pointing up has a negative second component.
    if n @ CAM_DOWN > 0:
        n, d = -n, -d
    tilt = math.degrees(math.acos(float(np.clip(-(n @ CAM_DOWN), -1.0, 1.0))))
    if tilt > max_tilt_deg:
        return None

    # The camera is at the origin of its own frame, so its height above the
    # floor is simply its distance from the plane.
    return {
        "normal_cam": n.tolist(),
        "offset_mm": float(d),
        "rms_mm": rms,
        "n_points": used,
        "camera_height_mm": float(abs(d)),
        # Pitch is nose-down about the camera's X axis, roll is about Z.
        "pitch_down_deg": float(math.degrees(math.asin(float(np.clip(-n[2], -1, 1))))),
        "roll_deg": float(math.degrees(math.asin(float(np.clip(n[0], -1, 1))))),
    }


def world_from_camera(floor, subject_centroid_cam=None):
    """Floor fit -> T_world_camera, a 4x4 in millimetres.

    `subject_centroid_cam` decides where the world origin lands: it is dropped
    onto the floor directly beneath the subject, so a scanned person sits at the
    origin exactly as anatomy.anatomical_body() already assumes. Without it the
    origin goes under the camera instead, which is a valid frame but not the one
    the rest of the package expects.
    """
    up = np.asarray(floor["normal_cam"], float)
    up = up / np.linalg.norm(up)

    # The person faces the camera, so their facing direction is the camera's
    # backward axis flattened onto the floor.
    f = -CAM_FORWARD - (-CAM_FORWARD @ up) * up
    nf = float(np.linalg.norm(f))
    if nf < 1e-6:
        raise ValueError("camera looks straight down; facing direction undefined")
    facing = f / nf
    left = np.cross(up, facing)

    R = np.vstack([facing, left, up])          # rows: world axes in cam coords
    if subject_centroid_cam is None:
        origin = floor["offset_mm"] * up       # the floor point under the camera
    else:
        c = np.asarray(subject_centroid_cam, float)
        origin = c - (c @ up - floor["offset_mm"]) * up

    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = -R @ origin
    return T


def apply(T, pts):
    """Transform (N,3) points by a 4x4."""
    pts = np.asarray(pts, float)
    return pts @ T[:3, :3].T + T[:3, 3]


# World +X is the direction the person faces, so it is also the direction a
# front-mounted camera looks from and a front-mounted arm reaches from.
ANTERIOR = np.array([1.0, 0.0, 0.0])
UP = np.array([0.0, 0.0, 1.0])


def region_pose(origin, bone_dir, anterior=ANTERIOR):
    """Place a limb region. -> 4x4 with local +Z along the bone, +X anterior.

    bodymodel._cylinder_region and anatomy._swept both lay their cells on an
    arc centred on local +X, and both document that axis as pointing
    anteriorly: "theta=0 points along local +X, which we aim anteriorly, so the
    default is the front half."

    Building that transform out of Euler angles does not achieve it. A limb
    posed by `_pose(x, y, z, rx=arm_out, ry=pi-arm_drop)` gets its +Z swung down
    and forward correctly, but +X is carried around with it and ends up
    pointing BACKWARD and DOWN -- measured on the shipped anatomical body, only
    7% of upper-arm cells and 18% of forearm cells faced anteriorly at all. The
    scrubbable arc sat on the back and underside of every limb: the one surface
    a front camera cannot see and a front arm cannot reach.

    The roll about the bone axis is a free choice, so fix it explicitly instead
    of inheriting whatever the rotation order leaves behind: take +X as the
    anterior-most direction perpendicular to the bone.

    Degenerate case: a limb pointing straight at the camera has no anterior
    perpendicular, and then the sensible front is the top of the limb, so fall
    back to world up.
    """
    z = np.asarray(bone_dir, float)
    nz = float(np.linalg.norm(z))
    if nz < 1e-9:
        raise ValueError("bone direction is zero length")
    z = z / nz

    a = np.asarray(anterior, float)
    x = a - (a @ z) * z
    if float(np.linalg.norm(x)) < 1e-3:
        x = UP - (UP @ z) * z          # limb points at the camera; use its top
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)

    T = np.eye(4)
    T[:3, :3] = np.column_stack([x, y, z])
    T[:3, 3] = np.asarray(origin, float)
    return T


def subject_mask(depth_mm, z_range=(600.0, 1300.0)):
    """The seated person, as the largest connected blob in a depth window.

    Crude on purpose. frames.py needs a rough body only to place the origin and
    to report heights; the real mask is Sapiens, in scan.py.
    """
    import cv2
    m = ((depth_mm > z_range[0]) & (depth_mm < z_range[1])).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8),
                         iterations=2)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return m.astype(bool)
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return lab == k


def solve(capture_dir, z_range=(600.0, 1300.0)):
    """A capture directory -> everything downstream needs about the rig.

    -> dict with `floor`, `T_world_camera`, and the subject's extent in world
    millimetres. This is the single entry point; nothing else should be fitting
    a plane of its own.
    """
    with open(os.path.join(capture_dir, "meta.json")) as fh:
        meta = json.load(fh)
    depth = np.load(os.path.join(capture_dir, "depth_median_mm.npy"))
    intr = meta["color_intrinsics"]

    floor = fit_floor(depth, intr)
    if floor is None:
        raise RuntimeError(f"no floor plane found in {capture_dir}")

    subj = deproject(depth, intr, subject_mask(depth, z_range))
    centroid = subj.mean(0) if len(subj) else None

    T = world_from_camera(floor, centroid)
    out = {"dir": capture_dir, "label": meta.get("label", ""), "floor": floor,
           "T_world_camera": T, "intrinsics": intr}
    if len(subj):
        W = apply(T, subj)
        out["subject_world"] = {
            "n": int(len(W)),
            "z_min_mm": float(W[:, 2].min()),
            "z_max_mm": float(W[:, 2].max()),
            "z_p99_mm": float(np.percentile(W[:, 2], 99)),
            "centroid_mm": W.mean(0).tolist(),
            "y_span_mm": float(W[:, 1].max() - W[:, 1].min()),
        }
    return out


def mm_per_px(intr, z_mm):
    """Scene millimetres per image pixel at a given depth.

    girth.py and anatomy.py both hardcode 0.9 here, commented "~1.35m standoff".
    The rig measures ~1.05m, which is 1.65mm/px, so both self-tests have been
    validating at nearly twice the sampling the camera actually delivers.
    """
    return float(z_mm) / float(intr["fx"])


if __name__ == "__main__":
    CAPS = ["scan01", "bag01", "pose_forward", "pose_lean", "empty",
            "dx_close", "dx_density", "dx_hires"]

    print("world frame, fitted from the floor")
    print("\n  floor plane, independently per capture:")
    print(f"    {'capture':14s} {'pts':>7s} {'rms':>6s} {'pitch':>7s} "
          f"{'roll':>7s} {'height':>8s}")
    fits, ups = {}, []
    for name in CAPS:
        d = os.path.join(DATA, name)
        if not os.path.isdir(d):
            continue
        depth = np.load(os.path.join(d, "depth_median_mm.npy"))
        with open(os.path.join(d, "meta.json")) as fh:
            intr = json.load(fh)["color_intrinsics"]
        f = fit_floor(depth, intr)
        if f is None:
            print(f"    {name:14s}  no floor found")
            continue
        fits[name] = f
        ups.append(f["normal_cam"])
        print(f"    {name:14s} {f['n_points']:7d} {f['rms_mm']:5.1f}mm "
              f"{f['pitch_down_deg']:6.2f}d {f['roll_deg']:6.2f}d "
              f"{f['camera_height_mm']:7.0f}mm")

    # The camera was re-aimed between the two sessions, so pitch legitimately
    # differs. Height and roll must not: nobody moved the tripod vertically.
    U = np.array(ups)
    hs = np.array([f["camera_height_mm"] for f in fits.values()])
    rolls = np.array([f["roll_deg"] for f in fits.values()])
    print(f"\n    camera height spread {hs.max() - hs.min():.0f}mm "
          f"over {len(hs)} captures   (tripod never moved vertically)")
    print(f"    roll spread          {rolls.max() - rolls.min():.2f} deg")
    assert hs.max() - hs.min() < 20.0, "the floor fit disagrees on camera height"
    assert abs(rolls).max() < 2.0, "the camera is rolled further than expected"

    # Within one session the aim is fixed, so those normals must agree tightly.
    for session in (["scan01", "bag01", "pose_forward", "pose_lean", "empty"],
                    ["dx_close", "dx_density", "dx_hires"]):
        have = [np.array(fits[n]["normal_cam"]) for n in session if n in fits]
        if len(have) < 2:
            continue
        worst = max(math.degrees(math.acos(float(np.clip(a @ b, -1, 1))))
                    for a in have for b in have)
        print(f"    session of {len(have)}: normals agree within {worst:.2f} deg")
        assert worst < 0.5, "floor normal is not reproducible within a session"

    print("\n  the world frame, from scan01:")
    r = solve(os.path.join(DATA, "scan01"))
    T = r["T_world_camera"]
    np.set_printoptions(precision=3, suppress=True)
    print(f"    T_world_camera rotation rows are the world axes in cam coords:")
    for lbl, row in zip(("+X facing", "+Y left  ", "+Z up    "), T[:3, :3]):
        print(f"      {lbl}  {row}")
    print(f"    translation  {T[:3, 3].round(1)} mm")

    # A frame is only real if it round-trips and preserves distance.
    Rm = T[:3, :3]
    assert np.allclose(Rm @ Rm.T, np.eye(3), atol=1e-9), "rotation is not orthonormal"
    assert abs(np.linalg.det(Rm) - 1.0) < 1e-9, "frame is left-handed"
    probe = np.array([[0., 0., 0.], [100., 0., 0.], [0., 250., 900.]])
    back = apply(np.linalg.inv(T), apply(T, probe))
    assert np.allclose(back, probe, atol=1e-6), "world transform does not invert"
    d0 = np.linalg.norm(probe[1] - probe[2])
    d1 = np.linalg.norm(apply(T, probe)[1] - apply(T, probe)[2])
    assert abs(d0 - d1) < 1e-6, "world transform is not rigid"
    print(f"    orthonormal, right-handed, inverts and preserves length. OK")

    s = r["subject_world"]
    print(f"\n  the seated subject, in world millimetres above the floor:")
    print(f"    {s['n']} points   crown {s['z_p99_mm']:.0f}mm "
          f"(max {s['z_max_mm']:.0f})   lowest seen {s['z_min_mm']:.0f}mm")
    print(f"    centroid {np.array(s['centroid_mm']).round(0)}")
    # This is fingertip to fingertip in scan01, where the arms are held out to
    # the sides -- NOT a shoulder width. Biacromial width needs the Sapiens
    # torso mask and belongs in scan.py.
    print(f"    lateral extent {s['y_span_mm']:.0f}mm "
          f"(arms out to the sides, so this is a wingspan, not a shoulder)")

    # The number this file exists to check. anatomy.py hardcodes seat_z=1050 as
    # the SHOULDER height. Crown is what we can measure without a segmentation,
    # and a seated adult's crown sits roughly 230mm above the acromion, so this
    # is an estimate that scan.py will replace with the real thing.
    implied_shoulder = s["z_p99_mm"] - 230.0
    print(f"\n    crown {s['z_p99_mm']:.0f}mm implies shoulders near "
          f"{implied_shoulder:.0f}mm; anatomy.py assumes 1050mm, "
          f"{1050.0 - implied_shoulder:+.0f}mm off")
    print(f"    ESTIMATE, from a population head height. scan.py measures the "
          f"acromion from the Sapiens torso mask and that value wins.")

    print("\nOK")
