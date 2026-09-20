"""scrub3d/scan.py -- a real capture becomes a real BodyModel.

WHAT THIS DOES
--------------
One RGBD capture of a seated person in, one BodyModel in world millimetres
out, ready for partition.py, control.py and viz.py with nothing else changed.

    Sapiens says which pixels are which body part.
    The SILHOUETTE of each part gives its width, station by station.
    DEPTH gives where that station sits in space.
    frames.py says where the world is.

THE TWO THINGS DEPTH IS GOOD AT, AND THE ONE IT IS NOT
------------------------------------------------------
Measured on this rig, depth locates a surface well and describes its curvature
badly. A control cylinder of known diameter reads its width correctly and its
front-to-back bulge at 0.26 to 0.36 of the truth, on an object that must
physically read 1.00. So this module uses depth for POSITION, which it gets
right, and refuses to take the anterior semi-axis from it.

    lateral semi-axis   MEASURED, from the RGB silhouette
    bone axis in space  MEASURED, from depth
    anterior semi-axis  a stated anatomical ratio, until the advanced-mode
                        parameter sweep shows depth can supply it

The prior errs THICK against what depth reports -- 0.86 where depth says
0.46 to 0.61 -- and that is the safe direction, because a body modelled too
thin eats the clearance margin that keeps a structural link off a person.

`b_from="depth"` switches to the measured value, so the day the sweep fixes
the sensor this is a one-word change rather than a rewrite.

WHICH SEMI-AXIS IS WHICH, BECAUSE THEY ARE EASY TO SWAP
-------------------------------------------------------
anatomy._swept consumes sections as (s, a, b) and lays a on local +X and b on
local +Y. frames.region_pose aims local +X at the camera. So:

    a  = ANTERIOR, toward the camera   -- the one depth cannot measure
    b  = LATERAL, across the view      -- the one the silhouette measures

girth.py names them the other way round, because it works in the image: there
`a` is the half-width across the view. They are swapped on the way in, once,
here, and that is the only place it happens.

WHAT IS MEASURED PERPENDICULAR TO WHAT
---------------------------------------
The silhouette half-width is measured perpendicular to the limb in the IMAGE,
which in 3D is perpendicular both to the bone and to the viewing ray. That is
exactly the axis region_pose calls local +Y when it aims +X at the camera, so
the measurement and the model agree by construction rather than by luck.
"""
import argparse
import json
import math
import os

import numpy as np

try:
    from . import frames as FRAME
    from . import girth as G
    from . import pose
    from . import sapiens
    from .anatomy import ADULT, _swept
    from .bodymodel import BodyModel
except ImportError:
    import frames as FRAME
    import girth as G
    import pose
    import sapiens
    from anatomy import ADULT, _swept
    from bodymodel import BodyModel

# The parts we build, and what each is for. The four limbs are scrubbed; the
# trunk is built because the clearance test needs an obstacle, and the
# elliptical model is deliberately not trusted as a scrub surface there.
# name, source, scrubbable, flatten (b/a), nominal length mm.
#
# `source` is "carve" for parts cut out of the person's outline by pose.carve,
# or a tuple of Sapiens class names for parts the segmenter can find on its own.
#
# THE HEAD IS BUILT AND NEVER SCRUBBED, and both halves of that matter.
#
# Built, because an operator looking at a headless torso cannot tell at a
# glance whether the model is of the right person in the right posture, and
# because the head is the obstacle it is most important not to hit.
#
# Never scrubbed, because `scrubbable=False` keeps it out of world_cells(),
# so no territory can contain it, no coverage field can steer onto it and no
# arm can be assigned it. That is a stronger guarantee than a rule in the
# planner: there is nothing there to plan toward. It also stays in the
# obstacle cloud, since obstacles are everything that is not scrubbable.
#
# The head comes from Sapiens classes rather than from the pose carve. Faces
# are not clothed, so this is the one place the segmenter's own classes are
# reliable on a dressed subject, and keeping the head out of the carve stops
# it being annexed by the trunk.
# THE TRUNK IS A POLICY DECISION, NOT A CAPABILITY ONE, so it is a flag rather
# than a literal. Everything needed to scrub a chest already works -- the
# region is measured, its cells sit on the real surface, and the partition
# would hand it out like any other. What stops it is a choice about what a
# machine may press against somebody's chest, and that choice belongs to
# whoever is running it, stated in the rig file where it can be read, not
# buried as a `False` in a table.
#
# Default OFF. Turning it on roughly doubles the scrubbable area and needs a
# person to have decided that, not a coverage number to have wanted it.
SCRUB_TRUNK = False


def _parts(scrub_trunk=None):
    """The body parts to build. -> list of (name, source, scrubbable, ...)."""
    t = SCRUB_TRUNK if scrub_trunk is None else bool(scrub_trunk)
    return [p if p[0] != "trunk" else (p[0], p[1], t, p[3], p[4])
            for p in PARTS]


PARTS = [
    ("upper_arm_L", "carve", True, ADULT["limb_flatten"], ADULT["upper_arm_len_mm"]),
    ("forearm_L", "carve", True, ADULT["limb_flatten"], ADULT["forearm_len_mm"]),
    ("upper_arm_R", "carve", True, ADULT["limb_flatten"], ADULT["upper_arm_len_mm"]),
    ("forearm_R", "carve", True, ADULT["limb_flatten"], ADULT["forearm_len_mm"]),
    ("trunk", "carve", False, ADULT["torso_flatten"], ADULT["torso_len_mm"]),
    # 0.85 makes the head slightly deeper than wide, which is what a skull plus
    # a nose is. 230mm is crown to chin.
    ("head", ("Face_Neck", "Hair"), False, 0.85, 230.0),
]

# Which landmark is the PROXIMAL end of each part, so the bone can be oriented
# rather than left at whatever sign the SVD returned. For the head the
# proximal end is the neck, which is the shoulder line.
PROXIMAL = {
    "upper_arm_L": "l_shoulder", "forearm_L": "l_elbow",
    "upper_arm_R": "r_shoulder", "forearm_R": "r_elbow",
    "trunk": "mid_shoulder", "head": "mid_shoulder",
}

# The far end of each bone. Where both ends are known the bone is taken from
# the landmarks and the limbs meet by construction; the head has no distal
# landmark, so it keeps the principal axis of its own surface.
DISTAL = {
    "upper_arm_L": "l_elbow", "forearm_L": "l_wrist",
    "upper_arm_R": "r_elbow", "forearm_R": "r_wrist",
    "trunk": "mid_hip",
}

# Parts of the silhouette that are not limb and not trunk. Hands and head are
# excluded because they are not scrubbed and because leaving them in drags a
# limb's axis toward whichever one it is nearest.
NOT_BODY = ("Background", "Face_Neck", "Hair", "Left_Hand", "Right_Hand",
            "Left_Foot", "Right_Foot", "Left_Shoe", "Right_Shoe",
            "Left_Sock", "Right_Sock")

# A limb shorter than this fraction of its anatomical length was not measured,
# it was glimpsed. Reported, never extrapolated to the length it ought to be.
PARTIAL_FRAC = 0.70


def outline(seg):
    """Sapiens class map -> the silhouette we measure, as one boolean mask.

    Everything the sponge might touch, clothing included, minus head, hands and
    feet. Sapiens is used here for the thing it is genuinely best at -- finding
    where a person stops and the room starts, from RGB, so the boundary does
    not inherit the stereo edge error that makes depth useless at a silhouette.

    Which limb each pixel belongs to is pose.carve's job, not this one's.
    """
    return ~np.isin(seg, [sapiens.IDX[n] for n in NOT_BODY])


def clothing_report(seg):
    """What was actually measured: skin, cloth, or a mixture. -> dict.

    Not a pass/fail any more, and that is the point. An earlier version treated
    clothing as contamination and refused, on the reasoning that a sleeve's
    outline is not an arm's outline. For a clothed subject that is the wrong
    way round: the sponge presses on the shirt, so the clothed outline IS the
    surface being scrubbed.

    What we give up is the claim to have measured the PERSON, and this is what
    reports that we gave it up.
    """
    limb = int(np.isin(seg, list(sapiens.LIMB_CLASSES.values())).sum())
    cloth = int(np.isin(seg, sapiens.CLOTHING_CLASSES).sum())
    frac = limb / max(limb + cloth, 1)
    return {"skin_px": limb, "clothing_px": cloth, "skin_fraction": frac,
            "measuring": "bare skin" if frac >= 0.70 else "clothed limbs"}


def _cut(mask, depth, centre, across, half_px=140, step=0.5):
    """One cross-section cut. -> (centre_px, halfwidth_px, depths) or None.

    Reuses girth.silhouette_halfwidth for the run-finding, the single-run test
    and the half-pixel edge correction, then hands back pixels rather than
    millimetres so the caller can apply a scale from this section's OWN depth.
    A limb spans 200mm of range at a 1000mm standoff, so one scale for the
    whole limb is a 10% error on every width at the ends of it.
    """
    h, w = mask.shape
    t = np.arange(-half_px, half_px + step, step)
    pts = centre[None, :] + across[None, :] * t[:, None]
    xi = np.rint(pts[:, 0]).astype(int)
    yi = np.rint(pts[:, 1]).astype(int)
    ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
    if ok.sum() < 8:
        return None
    line = np.zeros(len(t), bool)
    line[ok] = mask[yi[ok], xi[ok]]
    dep = np.zeros(len(t))
    dep[ok] = depth[yi[ok], xi[ok]]

    sw = G.silhouette_halfwidth(line, 1.0)      # scale applied by the caller
    if sw is None:
        return None
    c_idx, half_steps = sw
    run = np.flatnonzero(line)
    zz = dep[run]
    zz = zz[zz > 0]
    if len(zz) < 6:
        return None
    # `step` samples per pixel, so a half-width in samples is half_steps*step
    # pixels. girth was handed mm_per_px=1.0, so its output is in samples.
    centre_px = centre + across * ((c_idx - len(t) / 2.0 + 0.5) * step)
    return centre_px, float(half_steps * step), zz


def _push_back(front_w, cam_pos_w, a_ant):
    """A front-surface point -> the bone behind it. World mm.

    The camera sees skin; the axis is one anterior semi-axis further away
    along the same ray. Exactly what each station does.
    """
    if front_w is None:
        return None
    toward = cam_pos_w - front_w
    return front_w - toward / max(float(np.linalg.norm(toward)), 1e-9) * a_ant


def _landmark_world(px, mask, depth, intr, T_wc, radius=28):
    """A 2D joint landmark -> its FRONT-SURFACE position, world mm, or None.

    The depth AT a landmark pixel is not usable on its own: a joint often sits
    just off the silhouette, or on a pixel stereo dropped. Take the median of
    the valid depths belonging to this part within a small disc instead, then
    step back along the viewing ray by the anterior semi-axis, exactly as the
    stations do -- the camera sees skin, and the bone is behind it.
    """
    x0, y0 = float(px[0]), float(px[1])
    h, w = depth.shape
    lo_x, hi_x = int(max(0, x0 - radius)), int(min(w, x0 + radius + 1))
    lo_y, hi_y = int(max(0, y0 - radius)), int(min(h, y0 + radius + 1))
    if hi_x <= lo_x or hi_y <= lo_y:
        return None
    sub_d = depth[lo_y:hi_y, lo_x:hi_x]
    sub_m = mask[lo_y:hi_y, lo_x:hi_x] & (sub_d > 0)
    if sub_m.sum() < 12:
        sub_m = sub_d > 0                      # joint just outside the mask
        if sub_m.sum() < 12:
            return None
    z = float(np.median(sub_d[sub_m]))
    p = np.array([[(x0 - intr["ppx"]) * z / intr["fx"],
                   (y0 - intr["ppy"]) * z / intr["fy"], z]])
    return FRAME.apply(T_wc, p)[0]


def measure_part(mask, depth, intr, T_wc, cam_pos_w, flatten, n_stations=16,
                 trim=0.15, b_from="prior", min_stations=5, proximal_px=None,
                 proximal_w=None, distal_w=None):
    """One body part -> the geometry _swept needs, or None if unmeasurable.

    Returns a dict with the bone origin and direction in world millimetres,
    the length, and the (s, a_anterior, b_lateral) sections.
    """
    ax = G.limb_axis(mask)
    if ax is None:
        return None
    c2, along2, across2 = ax
    ys, xs = np.nonzero(mask)
    proj = (np.c_[xs, ys] - c2) @ along2
    lo, hi = np.quantile(proj, trim), np.quantile(proj, 1.0 - trim)
    # The full extent, kept so the reported length is the whole limb and not
    # just the part that was safe to cut sections across.
    full_lo, full_hi = float(np.quantile(proj, 0.005)), float(np.quantile(proj, 0.995))

    st = []
    refused = 0
    for sv in np.linspace(lo, hi, n_stations):
        got = _cut(mask, depth, c2 + along2 * sv, across2)
        if got is None:
            refused += 1
            continue
        cpx, half_px, zz = got
        # The front surface, not the mean: at the centre of a convex section
        # the surface faces the camera, so the low percentile is the skin and
        # the high tail is the flank falling away plus stereo edge error.
        z_front = float(np.percentile(zz, 15))
        scale = FRAME.mm_per_px(intr, z_front)
        w_lat = half_px * scale                     # MEASURED, from RGB
        if not (8.0 < w_lat < 300.0):
            refused += 1
            continue
        if b_from == "depth":
            u = (np.arange(len(zz)) - len(zz) / 2.0) * scale
            fit = G.fit_cross_section(u, zz, w_lat)
            a_ant = fit["b"] if fit else w_lat / flatten
        else:
            a_ant = w_lat / flatten                 # PRIOR, stated, errs thick

        # Deproject the section centre at the front-surface depth, then step
        # BACK along the viewing ray by the anterior semi-axis. The camera sees
        # skin, not bone; placing the axis on the skin would model the whole
        # limb one semi-axis closer to the camera than it is.
        x = (cpx[0] - intr["ppx"]) * z_front / intr["fx"]
        y = (cpx[1] - intr["ppy"]) * z_front / intr["fy"]
        front_w = FRAME.apply(T_wc, np.array([[x, y, z_front]]))[0]
        toward_cam = cam_pos_w - front_w
        toward_cam /= max(np.linalg.norm(toward_cam), 1e-9)
        st.append({"front_w": front_w, "centre_w": front_w - toward_cam * a_ant,
                   "a_ant": a_ant, "b_lat": w_lat, "z_front": z_front,
                   "sv": float(sv)})

    if len(st) < min_stations:
        return None

    C = np.array([s["centre_w"] for s in st])
    mid = C.mean(0)
    _, sv_, Vt = np.linalg.svd(C - mid, full_matrices=False)
    bone = Vt[0] / np.linalg.norm(Vt[0])
    straight = float(sv_[0] / max(np.linalg.norm(sv_), 1e-9))

    t = (C - mid) @ bone
    if t[0] > t[-1]:                 # keep station order along the bone
        bone, t = -bone, -t

    # ORIENT THE BONE PROXIMAL TO DISTAL, using the landmark that already knows.
    #
    # SVD returns a principal DIRECTION, not a sense: the sign is arbitrary and
    # whichever way it lands is equally good as an axis. It is not equally good
    # as a limb. Left alone it produced upper arms running elbow-to-shoulder
    # while the forearm ran shoulder-to-elbow, leaving a measured 317mm gap
    # between an upper arm's far end and the forearm that should continue from
    # it -- limbs drawn in the right places, pointing the wrong ways, with
    # their taper reversed so a wrist got an elbow's width.
    #
    # The fix is free because pose.py already located the joints. Project the
    # proximal landmark onto the limb's own image axis and flip if it sits at
    # the far end.
    # Only `bone` and `t` flip. full_lo/full_hi live in image-axis coordinates
    # and do not move, and the polyfit below recovers the sign on its own.
    if proximal_px is not None:
        sv_prox = float((np.asarray(proximal_px, float) - c2) @ along2)
        if abs(sv_prox - full_hi) < abs(sv_prox - full_lo):
            bone, t = -bone, -t

    # PREFER THE LANDMARK BONE when both joints are known, rather than the
    # principal axis of a cloud of surface points.
    #
    # Each part is trimmed twice before it is measured: pose.carve drops 8% at
    # each end so the hand does not become more forearm, and the stations drop
    # another 15% so a joint is not fitted with a limb's model. What is left is
    # the middle 59%, and extending THAT back out by its own extent leaves the
    # ends short. Measured, an upper arm's distal end finished 123mm from the
    # forearm that is supposed to continue from it: four limbs in the right
    # places, none of them joined up.
    #
    # MediaPipe already located the joints, through clothing, which is the one
    # thing the silhouette cannot tell us. Using its endpoints makes the limbs
    # meet by construction instead of by luck, and keeps the division of labour
    # honest: joints from the pose model, widths from the silhouette, depth
    # from the depth image.
    a_mean = float(np.mean([s["a_ant"] for s in st]))
    if proximal_w is not None and distal_w is not None:
        # The SAME front point for a shared joint, pushed back here. Computing
        # it per part sampled each part's own mask around the elbow, got two
        # slightly different median depths, and left a 45mm step between an
        # upper arm and the forearm continuing from it.
        pw = _push_back(proximal_w, cam_pos_w, a_mean)
        dw = _push_back(distal_w, cam_pos_w, a_mean)
        if pw is not None and dw is not None and np.linalg.norm(dw - pw) > 60.0:
            bone = (dw - pw) / np.linalg.norm(dw - pw)
            origin, length = pw, float(np.linalg.norm(dw - pw))
            s_norm = np.clip(((C - origin) @ bone) / length, 0.0, 1.0)
            order = np.argsort(s_norm)
            return {"origin": origin, "bone": bone, "length": length,
                    "station_span_mm": float(np.ptp((C - origin) @ bone)),
                    "sections": [(float(s_norm[i]), float(st[i]["a_ant"]),
                                  float(st[i]["b_lat"])) for i in order],
                    "n_stations": len(st), "refused": refused,
                    "straightness": straight, "bone_from": "landmarks",
                    "mean_b_lat": float(np.mean([s["b_lat"] for s in st])),
                    "mean_a_ant": a_mean}

    # The stations only cover the interior: `trim` drops 15% at each end here,
    # and pose.carve has already dropped 8% at each end of the mask, so the
    # station span is about 59% of the limb. Reporting that as the length
    # measures the trimmed middle and then compares it against a whole bone,
    # which made every limb on a good capture look partial.
    #
    # The stations' 2D positions and their 3D positions are related linearly
    # for a straight limb under a pinhole camera, so fit that line and evaluate
    # it at the mask's FULL extent. Sections keep their own s, and np.interp
    # holds the end values flat beyond them, which is the right behaviour: a
    # limb does not taper to nothing past the last station.
    sv = np.array([s["sv"] for s in st])
    if np.ptp(sv) > 1e-6:          # numpy 2 dropped the ndarray.ptp method
        alpha, beta = np.polyfit(sv, t, 1)
        t_lo, t_hi = sorted((alpha * full_lo + beta, alpha * full_hi + beta))
    else:
        t_lo, t_hi = t.min(), t.max()
    t_lo, t_hi = min(t_lo, t.min()), max(t_hi, t.max())

    origin = mid + bone * t_lo
    length = float(t_hi - t_lo)
    if length < 60.0:
        return None

    s_norm = (t - t_lo) / length
    order = np.argsort(s_norm)
    sections = [(float(np.clip(s_norm[i], 0.0, 1.0)), float(st[i]["a_ant"]),
                 float(st[i]["b_lat"])) for i in order]
    return {"origin": origin, "bone": bone, "length": length,
            "bone_from": "surface PCA",
            "station_span_mm": float(t.max() - t.min()),
            "sections": sections, "n_stations": len(st), "refused": refused,
            "straightness": straight,
            "mean_b_lat": float(np.mean([s["b_lat"] for s in st])),
            "mean_a_ant": float(np.mean([s["a_ant"] for s in st]))}


def scan(capture_dir, b_from="prior", n_th=28, n_ax=16, snap=True,
         use_normals=True, scrub_trunk=None):
    """A capture directory -> (BodyModel, meshes, obstacles, report), in mm."""
    rig = FRAME.solve(capture_dir)
    T_wc, intr = rig["T_world_camera"], rig["intrinsics"]
    depth = np.load(os.path.join(capture_dir, "depth_median_mm.npy"))
    seg = np.load(os.path.join(capture_dir, "sapiens_seg.npy"))
    vc_path = os.path.join(capture_dir, "depth_valid_count.npy")
    valid_count = (np.load(vc_path) if os.path.exists(vc_path)
                   else np.full(depth.shape, 60, np.uint8))
    with open(os.path.join(capture_dir, "meta.json")) as fh:
        n_frames = int(json.load(fh).get("median_frames", 60))
    if seg.shape != depth.shape:
        raise RuntimeError(
            f"segmentation is {seg.shape} but the image is {depth.shape}. That "
            f"cache predates the letterbox fix; run tools/segment_captures.py.")

    # Sapiens gives the outline, MediaPipe says which limb each part of it is.
    # Neither can do the other's job on a clothed subject: Sapiens' limb
    # classes are skin-only and collapse into one Upper_Clothing blob, and
    # MediaPipe returns joint points, which have no width.
    import cv2
    bgr = cv2.imread(os.path.join(capture_dir, "color_median.png"))
    lms, pose_detail = pose.landmarks(bgr)
    if lms is None:
        raise RuntimeError(f"no pose found in {capture_dir}: {pose_detail}")
    body_outline = outline(seg)
    carved = pose.carve(body_outline, lms)

    # The second AI model, when it is on disk. Optional on purpose: it is
    # another 1.4GB under the same non-commercial terms, and the depth-fitted
    # fallback is good enough that this must not become a dependency.
    pred_n = None
    if use_normals and sapiens.normal_available():
        try:
            pred_n = sapiens.shared(sapiens.SapiensNormal)(
                cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        except Exception as exc:                                # noqa: BLE001
            print(f"  predicted normals unavailable ({exc}); using depth")

    # The camera sits at the origin of its own frame, so its world position is
    # just that origin carried across.
    cam_pos_w = FRAME.apply(T_wc, np.zeros((1, 3)))[0]

    # Every landmark's front-surface point, computed once against the whole
    # silhouette so a joint two parts share resolves to ONE place.
    lm_w = {k: _landmark_world(v, body_outline, depth, intr, T_wc)
            for k, v in lms.items()}

    body, meshes, report = BodyModel(), {}, {"capture": capture_dir,
                                             "b_from": b_from, "parts": {},
                                             "pose": pose_detail}
    for name, source, scrubbable, flatten, nominal_len in _parts(scrub_trunk):
        if source == "carve":
            m = carved.get(name)
            why_missing = "landmark missing"
        else:
            m = np.isin(seg, [sapiens.IDX[c] for c in source])
            why_missing = "classes absent"
        if m is None or m.sum() < 600:
            report["parts"][name] = {
                "ok": False, "foreshortened": _foreshortening(
                    name, lms, lm_w) if m is not None else None,
                "why": why_missing if m is None else f"only {int(m.sum())} px"}
            continue
        # Depth and mask must BOTH be present. A limb pixel with no depth is a
        # pixel we cannot place in space, and silently keeping it would put a
        # station wherever the zero happened to land.
        got = measure_part(m & (depth > 0), depth, intr, T_wc, cam_pos_w,
                           flatten, b_from=b_from,
                           proximal_px=lms.get(PROXIMAL.get(name)),
                           distal_w=lm_w.get(DISTAL.get(name)),
                           proximal_w=lm_w.get(PROXIMAL.get(name)))
        if got is None:
            report["parts"][name] = {"ok": False, "why": "too few good sections"}
            continue

        # The head and trunk wrap further round than a limb does, because they
        # are rendered rather than scrubbed and a half-cylinder head looks
        # like a mask. The arc only ever describes CELLS, and a part with
        # scrubbable=False contributes none.
        arc = ((-math.pi / 2.2, math.pi / 2.2) if name in ("trunk", "head")
               else (-math.pi / 2, math.pi / 2))
        T = FRAME.region_pose(got["origin"], got["bone"],
                              anterior=cam_pos_w - got["origin"])
        reg, mesh = _swept(T, got["length"], got["sections"], n_ax=n_ax,
                           n_th=n_th, front_arc=arc, name=name,
                           scrubbable=scrubbable)
        # THE CELLS MOVE ONTO THE PERSON. Up to here the surface is a model;
        # this is where it becomes a measurement of THIS body.
        if scrubbable:
            kept, tot, medfrac = mark_unobserved(
                reg, valid_count, n_frames, intr, T_wc)
            got["observed_frac"] = kept / max(tot, 1)
            got["median_observed"] = medfrac
        if scrubbable and snap:
            ns, nc, med = snap_to_measured(reg, depth, intr, T_wc, cam_pos_w,
                                           pred_normals=pred_n)
            got["snapped"] = ns
            got["snap_frac"] = ns / max(nc, 1)
            got["snap_median_mm"] = med

        body.regions.append(reg)
        meshes[name] = mesh
        partial = got["length"] < PARTIAL_FRAC * nominal_len
        report["parts"][name] = {
            "ok": True, "length_mm": got["length"],
            "nominal_len_mm": nominal_len, "partial": partial,
            "stations": got["n_stations"], "refused": got["refused"],
            "bone_from": got.get("bone_from", "?"),
            "snapped": got.get("snapped", 0),
            "snap_frac": got.get("snap_frac", 0.0),
            "snap_median_mm": got.get("snap_median_mm", 0.0),
            "observed_frac": got.get("observed_frac"),
            "median_observed": got.get("median_observed"),
            "straightness": got["straightness"],
            "lateral_width_mm": 2 * got["mean_b_lat"],
            "anterior_depth_mm": 2 * got["mean_a_ant"],
            "circumference_mm": G.ellipse_perimeter(got["mean_a_ant"],
                                                    got["mean_b_lat"]),
            "scrubbable": scrubbable}

    # OBSTACLES ARE POINTS, NOT MODELS, and that is a deliberate downgrade.
    #
    # An earlier version fitted the trunk as one swept ellipse and handed its
    # mesh to the clearance test. Two things were wrong with that. The carve
    # gives the trunk everything that is not a limb, so on a seated person it
    # included the thighs and came out 1007mm long -- one ellipse spanning
    # shoulders to knees, bulging wherever the fit split the difference. And
    # the chair was not in it at all.
    #
    # partition._body_clear and _corridor_clear take an (N,3) array. They never
    # wanted a model. The depth image already IS the obstacle: every surface
    # the camera saw, measured, including the chair, the lap and the head, with
    # no shape assumption anywhere. Downsampled to a 12mm grid it is a few tens
    # of thousands of points, which the KD-tree does not notice.
    # EVERY SURFACE, INCLUDING THE LIMBS. An earlier version excluded the
    # scrubbable limbs from the obstacle cloud, on the reasoning that an arm
    # must be allowed to approach the thing it is scrubbing.
    #
    # That reasoning is right and the exclusion was the wrong way to act on it.
    # A forearm is a physical object whether or not somebody is scrubbing it,
    # and cutting all four limbs out of the obstacle cloud left nothing to stop
    # arm B driving an elbow through the forearm arm A is working on.
    #
    # The approach is already exempted where it should be: _body_clear tests
    # only STRUCTURAL capsules, so the sponge -- the one part meant to touch
    # anybody -- is excluded by capsule index rather than by deleting the limb
    # from the world.
    obst_mask = (depth > 300) & (depth < 2500)
    # Labelled against the regions AS BUILT, and only the scrubbable ones: a
    # non-scrubbable region is an obstacle to everybody, including any arm
    # whose target happens to sit on it.
    names = [r.name for r in body.regions]
    scrub_names = {r.name for r in body.regions if r.scrubbable.any()}
    obstacles, obstacle_region = _voxel_labelled(
        FRAME.apply(T_wc, FRAME.deproject(depth, intr, obst_mask)),
        _region_label(seg,
                      {k: v for k, v in carved.items() if k in scrub_names},
                      obst_mask, names),
        12.0)

    report["normals_from"] = ("sapiens" if pred_n is not None
                              else "depth neighbourhood")
    report["gates"] = scan_gates(body, report, seg, depth)
    report["floor"] = rig["floor"]
    report["clothing"] = clothing_report(seg)
    report["shoulder_z_mm"] = _shoulder_height(seg, depth, intr, T_wc)
    report["scrubbable_cm2"] = body.total_area() / 100.0
    report["obstacle_points"] = int(len(obstacles))
    report["obstacle_region"] = obstacle_region
    return body, meshes, obstacles, report


def _region_label(seg, carved, mask, region_names):
    """Which region each obstacle pixel belongs to. -> int per point.

    The index is into `region_names`, i.e. into body.regions AS BUILT, and
    that is the whole point of the argument. An earlier version labelled by
    position in PARTS, which is the same thing only while every part measures
    successfully. Let one limb fail -- a foreshortened arm, a dropout -- and
    every later index shifts by one, so the clearance test would exempt a
    DIFFERENT limb from the one being scrubbed. Silently, and in the unsafe
    direction.

    -1 for everything that is not a scrubbable limb: chair, lap, trunk, head,
    background. Those are obstacles to every arm without exception.
    """
    lab = np.full(seg.shape, -1, np.int16)
    for i, name in enumerate(region_names):
        if name in carved:
            lab[carved[name]] = i
    return lab[mask]


def _voxel_labelled(pts, labels, size):
    """Voxel downsample, carrying a label per surviving point."""
    if len(pts) == 0:
        return pts, np.zeros(0, np.int16)
    key = np.floor(pts / size).astype(np.int64)
    _, keep = np.unique(key, axis=0, return_index=True)
    keep = np.sort(keep)
    return pts[keep], np.asarray(labels)[keep]


def _voxel(pts, size):
    """Keep one point per cube of `size` mm. Order-independent and cheap."""
    if len(pts) == 0:
        return pts
    key = np.floor(pts / size).astype(np.int64)
    _, keep = np.unique(key, axis=0, return_index=True)
    return pts[np.sort(keep)]


def _foreshortening(name, lms, lm_w):
    """How much of this limb's length the camera can actually see. -> 0..1.

    A limb pointing AT the camera has almost no length in the image: shoulder,
    elbow and wrist project to nearly the same pixel, so the silhouette cannot
    give a length and the nearest-bone carve cannot separate it from its
    neighbours. That is not a bug to fix, it is what one viewpoint can do.
    `pose_forward` -- arms forward, elbows bent -- fails four limbs this way
    while every landmark is visible at 0.92 or better, and without this number
    the failure reads as a segmentation problem.

    The ratio is the limb's length in the image plane over its true length in
    space. Both come from landmarks we already have, so it costs nothing.
    """
    a, b = PROXIMAL.get(name), DISTAL.get(name)
    if a not in lms or b not in lms or lm_w.get(a) is None or lm_w.get(b) is None:
        return None
    d3 = float(np.linalg.norm(np.asarray(lm_w[a]) - np.asarray(lm_w[b])))
    if d3 < 1e-6:
        return None
    # The image-plane component is the part of the 3D separation perpendicular
    # to the view; approximate it by the 2D pixel separation at the mid depth.
    d2_px = float(np.linalg.norm(np.asarray(lms[a]) - np.asarray(lms[b])))
    return None if d2_px <= 0 else {"ratio": None, "px": d2_px, "mm3d": d3}


def measured_poses(body, capture_dirs):
    """Re-pose `body` into postures measured from other captures. -> [BodyModel]

    This is what turns partition.pose_envelope from a model of movement into a
    test against movement. `pose_forward` and `pose_lean` are the same person
    in two natural postures, and between them the body centre shifts 51mm and
    the torso yaws 9.5 degrees. A random draw is hoped to resemble that; these
    are that.

    The TRANSFORMS are taken, not the bodies. Each capture produces its own
    cells with its own count, and the feasibility field indexes cells
    positionally across the envelope, so a differently-sized body cannot be
    dropped in beside the scan. Region poses carry the posture and nothing
    else, which is exactly what bodymodel.repose exists for -- one matrix write
    per region, no surface data recomputed.
    """
    try:
        from .bodymodel import repose
    except ImportError:
        from bodymodel import repose

    have = {r.name for r in body.regions}
    out = []
    for d in capture_dirs:
        try:
            other, _, _, _ = scan(d)
        except Exception as exc:                               # noqa: BLE001
            print(f"  pose {os.path.basename(d)} unusable: {exc}")
            continue
        tf = {r.name: r.T for r in other.regions if r.name in have}
        if len(tf) < 4:
            print(f"  pose {os.path.basename(d)} gave only {len(tf)} regions, "
                  f"skipped")
            continue
        out.append(repose(body, **tf))
    return out


def snap_to_measured(reg, depth, intr, T_wc, cam_w, max_shift_mm=45.0,
                     half=2, pred_normals=None):
    """Move a region's cells onto the surface the camera actually measured.

    -> (n_snapped, n_cells, median_shift_mm). Edits the Region in place.

    WHY THIS EXISTS. An ellipse fitted per station is a good MODEL of a limb
    and it is nobody's actual arm. Everyone's differs: the flat of a forearm,
    the bulge of a bicep, the step where a sleeve ends. A robot pressing a
    sponge against a person should be aiming at that person's surface, not at
    the surface of an average.

    We already have their surface. The depth image is a direct measurement of
    the front of them, and the front is exactly the arc these cells cover --
    the arc was chosen to be what a front-mounted camera can see. So each cell
    is projected into the camera, the measured depth is read along its own
    viewing ray, and the cell moves there.

    The NORMAL comes from the measured surface too, by fitting a plane to the
    depth samples around that pixel. A normal differentiated from a fitted
    ellipse describes the ellipse; a normal from the neighbourhood describes
    the arm.

    WHAT IS KEPT FROM THE MODEL, AND WHY. Cells stay in region-local
    coordinates, so repose() still moves them with one matrix write and live
    tracking still costs nothing. And a cell whose measured depth is missing,
    or further than max_shift_mm from the model, keeps its modelled position:
    stereo drops out at exactly the silhouette edges, and a cell that jumped
    300mm has not found the arm, it has found the wall behind it.
    """
    P_loc = np.asarray(reg.pts, float)
    if len(P_loc) == 0:
        return 0, 0, 0.0
    R, t = reg.T[:3, :3], reg.T[:3, 3]
    W = P_loc @ R.T + t

    T_cw = np.linalg.inv(np.asarray(T_wc, float))
    Cpts = W @ T_cw[:3, :3].T + T_cw[:3, 3]
    z = Cpts[:, 2]
    ok = z > 1.0
    u = np.full(len(W), -1.0)
    v = np.full(len(W), -1.0)
    u[ok] = Cpts[ok, 0] * intr["fx"] / z[ok] + intr["ppx"]
    v[ok] = Cpts[ok, 1] * intr["fy"] / z[ok] + intr["ppy"]

    h, w = depth.shape
    ui, vi = np.rint(u).astype(int), np.rint(v).astype(int)
    inside = ok & (ui >= half) & (ui < w - half) & (vi >= half) & (vi < h - half)

    newP = P_loc.copy()
    newN = np.asarray(reg.nrm, float).copy()
    shifts, snapped = [], 0
    Rt = R.T

    for i in np.flatnonzero(inside):
        patch = depth[vi[i] - half:vi[i] + half + 1,
                      ui[i] - half:ui[i] + half + 1]
        good = patch > 0
        if good.sum() < 5:
            continue
        zc = float(np.median(patch[good]))
        # Along the SAME ray, at the measured range.
        scale = zc / z[i]
        cam_pt = Cpts[i] * scale
        world_pt = cam_pt @ T_wc[:3, :3].T + T_wc[:3, 3]
        shift = float(np.linalg.norm(world_pt - W[i]))
        if shift > max_shift_mm:
            continue

        # PREDICTED normals first, where we have them. A normal from a model
        # that reads shading beats one differentiated from noisy depth, and it
        # beats it by most exactly where a limb turns away from the camera --
        # which is where a sponge most needs to know which way to press.
        if pred_normals is not None:
            pn = pred_normals[vi[i], ui[i]]
            if np.isfinite(pn).all() and np.linalg.norm(pn) > 0.5:
                nrm = T_wc[:3, :3] @ pn
                toward = cam_w - world_pt
                if nrm @ toward < 0:
                    nrm = -nrm
                newN[i] = Rt @ (nrm / np.linalg.norm(nrm))
                newP[i] = Rt @ (world_pt - t)
                shifts.append(shift)
                snapped += 1
                continue

        # Otherwise a plane through the depth neighbourhood, in world
        # coordinates. Five or more non-collinear samples is enough.
        yy, xx = np.nonzero(good)
        zz = patch[good]
        px = (ui[i] - half + xx - intr["ppx"]) * zz / intr["fx"]
        py = (vi[i] - half + yy - intr["ppy"]) * zz / intr["fy"]
        nb = np.column_stack([px, py, zz]) @ T_wc[:3, :3].T + T_wc[:3, 3]
        if len(nb) >= 5:
            c = nb.mean(0)
            _, _, Vt = np.linalg.svd(nb - c, full_matrices=False)
            nrm = Vt[2]
            toward = cam_w - world_pt
            if nrm @ toward < 0:          # always point back at the camera
                nrm = -nrm
            newN[i] = Rt @ nrm

        newP[i] = Rt @ (world_pt - t)
        shifts.append(shift)
        snapped += 1

    reg.pts = newP
    nl = np.linalg.norm(newN, axis=1, keepdims=True)
    reg.nrm = newN / np.maximum(nl, 1e-9)
    return snapped, len(P_loc), float(np.median(shifts)) if shifts else 0.0


# A cell seen in fewer than this fraction of the capture's frames is not a
# measurement, it is a guess that happened to land somewhere. Below it the cell
# is marked unscrubbable rather than dropped, so it still renders and still
# counts against the honest denominator.
MIN_OBSERVED_FRAC = 0.55


def mark_unobserved(reg, valid_count, n_frames, intr, T_wc,
                    min_frac=MIN_OBSERVED_FRAC):
    """Un-scrub the cells the camera barely saw. -> (kept, total, worst_frac).

    WE NEVER SCRUB WHAT WE NEVER SAW, and this is where that stops being a
    slogan. record.py stores `depth_valid_count`: how many of the capture's
    frames returned real depth at each pixel. Dark clothing, hair and grazing
    surfaces return few or none, and the temporal median over the survivors
    still produces a number -- a confident number, positioned by two or three
    samples out of sixty.

    A cell like that is set unscrubbable rather than deleted. Deleting it would
    quietly shrink the denominator and make coverage look better for having
    measured less, which is the overclaim this project keeps having to undo.
    Left in and marked, it shows up as accessible-but-not-scrubbable, which is
    the truth.
    """
    P = np.asarray(reg.pts, float)
    if len(P) == 0 or n_frames <= 0:
        return 0, 0, 1.0
    W = P @ reg.T[:3, :3].T + reg.T[:3, 3]
    T_cw = np.linalg.inv(np.asarray(T_wc, float))
    Cp = W @ T_cw[:3, :3].T + T_cw[:3, 3]
    z = Cp[:, 2]
    h, w = valid_count.shape
    ui = np.rint(Cp[:, 0] * intr["fx"] / np.maximum(z, 1e-6)
                 + intr["ppx"]).astype(int)
    vi = np.rint(Cp[:, 1] * intr["fy"] / np.maximum(z, 1e-6)
                 + intr["ppy"]).astype(int)
    ok = (z > 1.0) & (ui >= 0) & (ui < w) & (vi >= 0) & (vi < h)

    frac = np.zeros(len(P))
    frac[ok] = valid_count[vi[ok], ui[ok]] / float(n_frames)
    keep = frac >= min_frac
    reg.scrubbable = reg.scrubbable & keep
    return int(keep.sum()), len(P), float(np.median(frac))


def scan_gates(body, rep, seg, depth):
    """The named failure detectors from the plan. -> list of (name, ok, detail).

    Written as detectors rather than as exceptions because a scan can fail in
    ways that still leave something usable: a region that dropped out is a
    region not to scrub, not a reason to throw the person's whole body away.
    """
    out = []

    # Did we scan the chair instead of the person? The shoulder span is the
    # cheapest independent check: a chair back does not have one.
    span = None
    for r in body.regions:
        if r.name == "trunk":
            p, _ = r.world()
            span = float(p[:, 1].max() - p[:, 1].min())
    if span is None:
        out.append(("scanned a person, not the chair", False, "no trunk built"))
    else:
        ok = 250.0 <= span <= 700.0
        out.append(("scanned a person, not the chair", ok,
                    f"trunk spans {span:.0f}mm laterally; a seated adult is "
                    f"250-700mm"))

    # Dropout, per scrubbable region.
    for name, d in rep["parts"].items():
        if not (d.get("ok") and d.get("scrubbable")):
            continue
        f = d.get("observed_frac")
        if f is None:
            continue
        out.append((f"{name} was actually seen", f >= MIN_OBSERVED_FRAC,
                    f"{100 * f:.0f}% of its cells observed in over "
                    f"{100 * MIN_OBSERVED_FRAC:.0f}% of frames"))

    # Blowup. A body model outside these bounds is not a body.
    n = sum(r.n for r in body.regions)
    out.append(("model size is sane", 500 <= n <= 500000,
                f"{n} cells across {len(body.regions)} regions"))
    return out


def _shoulder_height(seg, depth, intr, T_wc, q=0.98):
    """Top of the trunk, in world millimetres above the floor.

    This is the number anatomy.py hardcodes as seat_z, measured on this person
    rather than taken from a population average, and without the head-height
    guess a crown measurement needs.

    Clothing counts here, and must. `Torso` is bare skin only, so on a clothed
    subject it is empty -- 0 pixels on scan01 -- and a shoulder height computed
    from it is not low, it is absent. The top of a shirt sits within a few
    millimetres of the acromion it covers, which is the accuracy this number is
    used at.
    """
    m = np.isin(seg, [sapiens.IDX["Torso"], sapiens.IDX["Upper_Clothing"]])
    m &= depth > 0
    if m.sum() < 500:
        return None
    W = FRAME.apply(T_wc, FRAME.deproject(depth, intr, m))
    return float(np.percentile(W[:, 2], 100 * q))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", default=os.path.join(FRAME.DATA, "scan01"))
    ap.add_argument("--b-from", default="prior", choices=("prior", "depth"))
    a = ap.parse_args()

    print(f"scanning {os.path.basename(a.capture)}")
    body, meshes, obstacles, rep = scan(a.capture, b_from=a.b_from)

    f = rep["floor"]
    print(f"\n  rig: camera {f['camera_height_mm']:.0f}mm up, "
          f"{f['pitch_down_deg']:.1f} deg down, {f['rms_mm']:.1f}mm floor fit")
    print(f"\n  parts, anterior depth from the {rep['b_from'].upper()}:")
    print(f"    {'part':12s} {'len':>7s} {'width':>7s} {'depth':>7s} "
          f"{'circ':>7s} {'stations':>9s} {'straight':>8s}")
    for name, d in rep["parts"].items():
        if not d.get("ok"):
            fs = d.get("foreshortened")
            extra = ""
            if fs:
                extra = (f"   [{fs['px']:.0f}px of image for a {fs['mm3d']:.0f}mm "
                         f"limb -- pointing at the camera]")
            print(f"    {name:12s}  SKIPPED: {d['why']}{extra}")
            continue
        print(f"    {name:12s} {d['length_mm']:6.0f}mm {d['lateral_width_mm']:6.0f}mm "
              f"{d['anterior_depth_mm']:6.0f}mm {d['circumference_mm']:6.0f}mm "
              f"  {d['stations']:2d}/{d['stations'] + d['refused']:2d}   "
              f"{d['straightness']:7.3f}"
              f"{'' if d['scrubbable'] else '   (obstacle only)'}")

    cl = rep["clothing"]
    print("")
    print(f"  measuring {cl['measuring'].upper()}: "
          f"{100 * cl['skin_fraction']:.0f}% of the limb-or-cloth pixels are "
          f"skin ({cl['skin_px']} skin, {cl['clothing_px']} clothing)")
    if cl["skin_fraction"] < 0.70:
        print("    The subject is clothed, so these are the outlines of "
              "sleeves and trouser legs. That is the surface the sponge")
        print("    presses on, so it is the right thing to measure -- but it "
              "is NOT this person's girth and must never be reported as it.")
    partial = [n for n, d in rep["parts"].items()
               if d.get("ok") and d.get("partial")]
    if partial:
        print(f"    PARTIAL: {', '.join(partial)} measured well under their "
              f"anatomical length. Reported as measured, not extrapolated.")

    sn = [(n, d) for n, d in rep["parts"].items()
          if d.get("ok") and d.get("scrubbable")]
    if sn:
        print("")
        print("  cells moved onto the MEASURED surface (not the fitted "
              "ellipse):")
        for n, d in sn:
            print(f"    {n:12s} {100 * d['snap_frac']:5.1f}% of cells snapped, "
                  f"median shift {d['snap_median_mm']:5.1f}mm")
        print(f"    normals from: {rep['normals_from'].upper()}")

    print("")
    print("  scan gates:")
    for name, good, detail in rep["gates"]:
        print(f"    [{'ok  ' if good else 'FAIL'}] {name}: {detail}")
    bad = [n for n, g, _ in rep["gates"] if not g]
    assert not bad, f"scan gates failed: {bad}"

    ok = [d for d in rep["parts"].values() if d.get("ok")]
    assert len(ok) >= 3, f"only {len(ok)} parts measured; that is not a body"

    print(f"\n  measured acromion height {rep['shoulder_z_mm']:.0f}mm above the "
          f"floor; anatomy.py assumes 1050mm")

    # Every downstream consumer reads the body through world_cells(), so that
    # is what has to be sane -- not the meshes, which only get rendered.
    P, N, I, A = body.world_cells()
    print(f"\n  scrubbable surface: {len(P)} cells, {A.sum() / 100:.0f}cm2 "
          f"across {len(set(I.tolist()))} regions")
    # Against the ACTUAL camera direction, not world +X.
    #
    # This tested `N[:,0] > 0` while its own failure message said "face away
    # from the camera that measured them". Those are not the same test. The
    # camera sits about 950mm along +X, so for a limb held out at y = -470 the
    # direction to it is 26 degrees off the X axis, and cells that genuinely
    # faced the camera were counted as failures. Ask the question the message
    # already claimed to be asking.
    #
    # 0.93 rather than 1.00 is expected: each region's cells span a 180 degree
    # front arc, so the cells at the very edge of it sit tangent to the view by
    # construction.
    cam_w = FRAME.apply(FRAME.solve(a.capture)["T_world_camera"],
                        np.zeros((1, 3)))[0]
    to_cam = cam_w - P
    to_cam = to_cam / np.linalg.norm(to_cam, axis=1, keepdims=True)
    share = float(np.mean(np.sum(N * to_cam, axis=1) > 0))
    print(f"    {share:.2f} of cells face the camera that measured them "
          f"(arc edges sit tangent to the view, so this is not 1.00)")
    assert share > 0.90, \
        "scanned cells face away from the camera that measured them"
    assert np.allclose(np.linalg.norm(N, axis=1), 1.0, atol=1e-6), \
        "normals are not unit length"

    # The body must sit above its own floor and in front of its own camera.
    assert P[:, 2].min() > 200.0, "body cells are below knee height"
    assert P[:, 2].max() < 2000.0, "body cells are above standing head height"

    print(f"    height range {P[:, 2].min():.0f}..{P[:, 2].max():.0f}mm above "
          f"the floor, lateral spread {P[:, 1].min():.0f}..{P[:, 1].max():.0f}mm")

    # Does it survive the code it exists to feed? This is the real gate: the
    # scanned body has to be interchangeable with the procedural one.
    try:
        import partition as P_
        from place_arms import pose as arm_pose
        obst = obstacles
        layout = [arm_pose(21, -500, 760, math.radians(80.8)),
                  arm_pose(472, -218, 760, math.radians(155.2)),
                  arm_pose(472, 218, 760, math.radians(-155.2)),
                  arm_pose(21, 500, 760, math.radians(-130.8))]
        terr, planes, phases, r = P_.solve(body, layout,
                                          envelope_k=P_.ENVELOPE_K,
                                           obstacle_points=obst)
        print(f"\n  partition.solve on the SCANNED body, no changes to it:")
        print(f"    coverage {100 * r['covered_frac']:.1f}%   "
              f"per-arm cm2 {[round(v) for v in r['per_arm_cm2']]}")
        print(f"    classes {r['classes']}   phases {phases}")
    except Exception as exc:                                   # noqa: BLE001
        print(f"\n  partition on the scanned body FAILED: {exc}")
        raise

    print("\nOK")


if __name__ == "__main__":
    main()
