"""scrub3d/pose.py -- where the bones are, through clothing.

WHY THIS EXISTS, AND WHY IT IS NOT A FALLBACK
----------------------------------------------
The subject is fully clothed and will stay that way. That single fact moves
Sapiens out of the role it was brought in for.

Sapiens' Goliath vocabulary labels SKIN. `Left_Upper_Arm` fires on a bare arm;
put a T-shirt on the same person and those pixels become `Upper_Clothing`,
which is one class covering the trunk and both sleeves together. Measured on
`scan01`: 40% of the subject is `Upper_Clothing`, `Torso` is 0 pixels, and 11%
of the limb-or-cloth pixels are skin. There is no "arm under clothing" class,
and no threshold anywhere recovers one.

MediaPipe does not care. It is trained on clothed people and returns the
shoulder, elbow and wrist regardless of what is over them.

So the two models split the work by what each is actually good at:

    Sapiens    the person's OUTLINE against the background. Its boundary is
               excellent and it is computed from RGB, so it does not inherit
               the stereo edge error that makes depth useless at a silhouette.

    MediaPipe  WHICH part of that outline is which limb.

THE REFRAME THAT MAKES THIS CORRECT RATHER THAN A COMPROMISE
-------------------------------------------------------------
The earlier design treated a sleeve as contamination: "a sleeve's outline is
not an arm's outline." That was right when the goal was a person's girth.

It is the wrong way round here. The sponge presses on the shirt. The clothed
outline IS the surface being scrubbed, so measuring it is not a degraded
version of measuring skin -- it is the measurement that matters. What we lose
is only the claim to have measured the PERSON's arm, and we were not entitled
to that claim anyway while depth could not supply the second semi-axis.

Say it plainly wherever it is reported: this measures the clothed limb.
"""
import numpy as np

# MediaPipe Pose landmark indices. Left and right are the SUBJECT's, which is
# also Sapiens' convention, so the two agree without a flip.
IDX = {
    "nose": 0,
    "l_shoulder": 11, "r_shoulder": 12,
    "l_elbow": 13, "r_elbow": 14,
    "l_wrist": 15, "r_wrist": 16,
    "l_hip": 23, "r_hip": 24,
    "l_knee": 25, "r_knee": 26,
}

# The skeleton we carve the silhouette with. A part may own several segments;
# a pixel goes to the part owning the segment it is nearest to.
#
# The TRUNK segments are not decoration and there are three of them for a
# measured reason. With the trunk represented by its midline alone, an upper
# arm swallowed a wedge of chest: the shoulder joint sits INSIDE the outline,
# so chest pixels beside it are nearer the upper-arm segment than the distant
# midline. That read as a 135mm-wide upper arm with a straightness of 0.89,
# which is not a limb shape. Adding the clavicle and pelvis lines puts a trunk
# segment right where the competition actually happens, and costs nothing.
#
# The alternative is a distance cap, and the right cap is the limb radius,
# which is the thing being measured.
BONES = [
    ("upper_arm_L", "l_shoulder", "l_elbow"),
    ("forearm_L", "l_elbow", "l_wrist"),
    ("upper_arm_R", "r_shoulder", "r_elbow"),
    ("forearm_R", "r_elbow", "r_wrist"),
    ("trunk", "mid_shoulder", "mid_hip"),
    ("trunk", "l_shoulder", "r_shoulder"),
    ("trunk", "l_hip", "r_hip"),
]


def landmarks(bgr, min_visibility=0.5):
    """BGR image -> ({name: (x_px, y_px)}, detail) or (None, detail).

    static_image_mode because a scan is one temporally-medianed frame, not a
    video: the tracking path would try to reuse a previous detection that does
    not exist and gives a worse single-frame answer.
    """
    import cv2
    import mediapipe as mp

    with mp.solutions.pose.Pose(static_image_mode=True, model_complexity=2,
                                enable_segmentation=False,
                                min_detection_confidence=0.5) as pose:
        res = pose.process(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    if not res.pose_landmarks:
        return None, {"why": "MediaPipe found no person"}

    h, w = bgr.shape[:2]
    lm = res.pose_landmarks.landmark
    out, vis, weak = {}, {}, []
    for name, i in IDX.items():
        p = lm[i]
        vis[name] = float(p.visibility)
        if p.visibility < min_visibility:
            weak.append(name)
            continue
        out[name] = (float(p.x) * w, float(p.y) * h)

    for new, a, b in (("mid_shoulder", "l_shoulder", "r_shoulder"),
                      ("mid_hip", "l_hip", "r_hip")):
        if a in out and b in out:
            out[new] = tuple((np.array(out[a]) + np.array(out[b])) / 2.0)

    return out, {"visibility": vis, "below_threshold": weak,
                 "found": sorted(out)}


def _seg_distance(P, a, b):
    """Distance from each point in P (N,2) to the segment ab, plus the
    parameter t along it. -> (dist, t)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    ab = b - a
    L2 = float(ab @ ab)
    if L2 < 1e-9:
        d = np.linalg.norm(P - a, axis=1)
        return d, np.zeros(len(P))
    t = np.clip(((P - a) @ ab) / L2, 0.0, 1.0)
    proj = a[None, :] + t[:, None] * ab[None, :]
    return np.linalg.norm(P - proj, axis=1), t


def carve(person_mask, lms, want=None, t_margin=0.08):
    """Split a person's silhouette into limbs by nearest bone.

    -> {name: bool mask}. Only bones whose endpoints were both found are used.

    Each silhouette pixel goes to the bone it is closest to, the trunk
    included, so the shoulder boundary falls where the two are equidistant
    rather than at a number somebody chose. `t_margin` then trims each limb to
    its own segment: without it every pixel past the wrist is still nearest to
    the forearm and the hand is measured as more forearm.
    """
    want = want or [n for n, _, _ in BONES]
    ys, xs = np.nonzero(person_mask)
    if len(xs) == 0:
        return {}
    P = np.c_[xs, ys].astype(float)

    usable = [(n, a, b) for n, a, b in BONES
              if n in want or n == "trunk"]
    usable = [(n, a, b) for n, a, b in usable if a in lms and b in lms]
    if not usable:
        return {}

    D = np.empty((len(usable), len(P)))
    T = np.empty_like(D)
    for i, (_, a, b) in enumerate(usable):
        D[i], T[i] = _seg_distance(P, lms[a], lms[b])
    win = np.argmin(D, axis=0)

    out = {}
    for i, (name, _, _) in enumerate(usable):
        if name not in want:
            continue
        sel = win == i
        if name != "trunk":
            # Endpoint pixels pile up at t==0 or t==1 because the projection
            # was clamped there; those are the shoulder cap and the hand.
            sel &= (T[i] > t_margin) & (T[i] < 1.0 - t_margin)
        elif "mid_hip" in lms:
            # The trunk is shoulders to hips, by definition. Nearest-bone alone
            # gives it everything that is not an arm, which on a seated person
            # is the thighs as well: measured, that read as a 1007mm trunk.
            sel &= P[:, 1] <= lms["mid_hip"][1] + 0.05 * abs(
                lms["mid_hip"][1] - lms.get("mid_shoulder", lms["mid_hip"])[1])
        m = np.zeros(person_mask.shape, bool)
        m[ys[sel], xs[sel]] = True
        # A part may own several segments, so accumulate rather than assign.
        out[name] = m if name not in out else (out[name] | m)
    return out


def bone_lengths_px(lms):
    """-> {bone: length in pixels}, for the identity and sanity checks."""
    out = {}
    for name, a, b in BONES:
        if a in lms and b in lms:
            out[name] = float(np.linalg.norm(np.array(lms[a]) -
                                             np.array(lms[b])))
    return out


if __name__ == "__main__":
    import json
    import os

    import cv2

    try:
        from . import frames as FRAME
        from . import sapiens
    except ImportError:
        import frames as FRAME
        import sapiens

    cap = os.path.join(FRAME.DATA, "scan01")
    bgr = cv2.imread(os.path.join(cap, "color_median.png"))
    seg = np.load(os.path.join(cap, "sapiens_seg.npy"))
    depth = np.load(os.path.join(cap, "depth_median_mm.npy"))
    with open(os.path.join(cap, "meta.json")) as fh:
        intr = json.load(fh)["color_intrinsics"]

    print("pose landmarks through clothing")
    lms, det = landmarks(bgr)
    assert lms is not None, det
    print(f"  found {len(det['found'])} of {len(IDX)} landmarks; "
          f"below threshold: {det['below_threshold'] or 'none'}")
    for k in ("l_shoulder", "l_elbow", "l_wrist", "r_shoulder", "r_elbow",
              "r_wrist"):
        v = det["visibility"][k]
        print(f"    {k:12s} visibility {v:.2f}"
              f"{'' if k in lms else '   DROPPED'}")

    # The person's outline comes from Sapiens; everything that is not a limb
    # in its own right is fair game for the carve.
    drop = [sapiens.IDX[n] for n in ("Background", "Face_Neck", "Hair",
                                     "Left_Hand", "Right_Hand", "Left_Foot",
                                     "Right_Foot", "Left_Shoe", "Right_Shoe",
                                     "Left_Sock", "Right_Sock")]
    person = ~np.isin(seg, drop)
    print(f"\n  Sapiens outline: {int(person.sum())} px of scrubbable-or-cloth "
          f"body")

    parts = carve(person, lms)
    print(f"\n  carved by nearest bone:")
    print(f"    {'part':12s} {'px':>8s} {'len px':>8s} {'len mm':>8s}")
    bl = bone_lengths_px(lms)
    for name in ("upper_arm_L", "forearm_L", "upper_arm_R", "forearm_R",
                 "trunk"):
        if name not in parts:
            print(f"    {name:12s}  not carved (landmark missing)")
            continue
        m = parts[name]
        zz = depth[m & (depth > 0)]
        mm = (bl[name] * FRAME.mm_per_px(intr, float(np.median(zz)))
              if len(zz) else float("nan"))
        print(f"    {name:12s} {int(m.sum()):8d} {bl[name]:8.0f} {mm:8.0f}")

    # The carve must actually divide: overlapping limbs would double-count
    # surface and, worse, hand two arms the same cell.
    names = [n for n in parts if n != "trunk"]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            ov = int((parts[names[i]] & parts[names[j]]).sum())
            assert ov == 0, f"{names[i]} and {names[j]} overlap by {ov} px"
    print("\n  limb masks are disjoint. OK")

    # And it must beat what Sapiens alone can do on a clothed subject, or
    # there is no reason for this file to exist.
    skin = int(np.isin(seg, list(sapiens.LIMB_CLASSES.values())).sum())
    carved = sum(int(parts[n].sum()) for n in names)
    print(f"\n  limb pixels, Sapiens skin classes alone: {skin}")
    print(f"  limb pixels, outline carved by pose:      {carved}"
          f"   ({carved / max(skin, 1):.1f}x)")
    assert carved > 2 * skin, "the carve found no more limb than skin alone did"

    print("")
    print("OK")
