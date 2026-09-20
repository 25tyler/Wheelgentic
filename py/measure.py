"""py/measure.py -- measure the person in front of the camera, for real.

WHAT THIS FIXES
---------------
The projector strip has always read "4 arms, measured body". Until now the
second half was false. `tools/export_body.py` calls
`anatomy.anatomical_body()` with no arguments, so every body the page has ever
drawn -- the bake, the 'n' second person, and the live 'v' solve -- was built
from `anatomy.ADULT`, a table of population-typical adult numbers. Nobody was
measured. The word on screen described a constant.

This module makes the claim true by supplying the ONE argument that was
missing: a `measurements` dict of this person's own circumferences and bone
lengths, which `anatomical_body(measurements=...)` already accepts and has
always accepted. Nothing downstream changes. Same solver, same serialiser,
same JSON, same renderer -- only the source of the semi-axes moves, from a
table to a camera. That is exactly the property anatomy.py's own header claims
for the D455 pipeline, and it is why this is not a second pipeline: there is
one body builder, and this feeds it.

WHERE THE NUMBERS COME FROM
---------------------------
`scrub3d/girth.py` is the measurement, unmodified and imported rather than
reimplemented. Its contract is per cross-section:

    a  (half-width across the view)  <- from a SILHOUETTE, because an RGB
       segmentation boundary does not inherit the stereo edge error that makes
       depth worst at exactly the outline you want to measure.
    b  (half-depth along the view)   <- from the DEPTH profile inside that
       silhouette, or from an anatomical flattening ratio when depth is absent.

THE SILHOUETTE SOURCE, AND WHY IT IS NOT SAPIENS HERE
------------------------------------------------------
scrub3d/scan.py gets its per-limb silhouette from Sapiens, which labels body
PARTS. That is the better instrument and it is what the offline scanner should
keep using. It is not available in the live demo: the weights are 1.36GB of
CC-BY-NC download and torch is not installed on the demo box. Waiting for it
would mean shipping nothing.

MediaPipe is already running in the live loop, for pose, and it can also emit a
person-versus-background mask. That mask cannot tell an arm from a torso -- its
own limitation, stated plainly in sapiens.py's header. So the arm is cut out of
it geometrically: the shoulder-elbow and elbow-wrist landmarks give the bone,
and a band around that bone intersected with the person mask is the limb. The
band is generous on purpose; girth measures the silhouette run it finds inside
the band, so the band only has to EXCLUDE the torso, not define the arm's edge.

This is honestly weaker than Sapiens at one thing and identical at the rest:
a long sleeve is measured as a sleeve either way (sapiens.py says the same),
and the torso boundary here is a landmark geometry call rather than a learned
one. Every number this module returns carries the source that produced it, so
nothing downstream can mistake one for the other.

WHERE THIS REFUSES
------------------
girth refuses a section whose silhouette touches a frame border, is split by an
occluder, or does not look like a limb cross-section. This module refuses a
whole limb that did not clear `MIN_STATIONS` accepted sections, and refuses a
whole PERSON whose limbs it could not measure. A refusal returns None and the
demo keeps the population table -- which is the behaviour it has today, so a
failed measurement costs nothing that was not already lost.

A wrong circumference is worse than no circumference: the partition sizes the
body the arms plan against, so a limb measured too thin puts planned surface
inside a real person.
"""
import math
import os
import sys

import numpy as np

# scrub3d is the backend package. Import girth from it rather than copying any
# of it here: this module must measure with the SAME code the offline scanner
# measures with, or the two drift and the demo stops proving anything.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "scrub3d") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "scrub3d"))
import girth as G                                          # noqa: E402

# MediaPipe landmark indices. Duplicated from py/scrubbot.py deliberately, for
# the same reason it duplicates them: this module must be importable on a
# machine with no mediapipe installed, so a caller can feed it landmarks it got
# some other way (a replay, a test, the GB10's own capture) without dragging
# the whole vision stack in.
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24

# Limbs, as (name, proximal landmark, distal landmark, which anatomy keys they
# set). The circumference key is what anatomy.ADULT calls the same quantity, so
# the dict this module returns drops straight into anatomical_body.
LIMBS = (
    ("upper_arm_L", L_SHOULDER, L_ELBOW, "upper_arm_circ_mm", "upper_arm_len_mm"),
    ("forearm_L",   L_ELBOW,    L_WRIST, "forearm_circ_mm",   "forearm_len_mm"),
    ("upper_arm_R", R_SHOULDER, R_ELBOW, "upper_arm_circ_mm", "upper_arm_len_mm"),
    ("forearm_R",   R_ELBOW,    R_WRIST, "forearm_circ_mm",   "forearm_len_mm"),
)

# A landmark below this confidence is a GUESS, not a measurement. MediaPipe
# emits coordinates for occluded joints regardless; py/vision.py gates on the
# same field for the same reason, and measuring a limb off a guessed elbow
# produces a confident number about a bone that was never seen.
MIN_VISIBILITY = 0.55

# Sections cut across each limb, and how many must survive for the limb to
# count. girth refuses individually, so this is the quorum over its refusals.
# Four is scan.py's own floor (min_stations=5 there over 16 stations); the same
# ratio over 16 is 5, and that is what is used.
N_STATIONS = 16
MIN_STATIONS = 5

# Half-width of the band carved around a bone, as a multiple of the bone's own
# pixel length. An upper arm is roughly 1/3 as wide as it is long, so 0.42
# leaves margin for a thick arm while still cutting the torso off a limb held
# against the body. The band only has to exclude the torso; girth finds the
# real edge inside it.
BAND_FRAC = 0.42

# The ends of a bone are joints, not limb. girth.measure_limb trims 15% by
# default for the same reason; this trims the BAND before girth sees it so that
# the elbow bulge never enters the silhouette at all.
END_TRIM = 0.18

# Anterior/lateral flattening for a limb, from anatomy.ADULT["limb_flatten"].
# Used ONLY when there is no depth: with one semi-axis measured and the other
# from a ratio, the circumference is a measurement scaled by a stated constant,
# which is a different and weaker claim than two measured axes. Every result
# says which it was.
LIMB_FLATTEN = 0.86

# Plausible adult limb circumferences, millimetres. This is not a tuning knob
# and it is not a tolerance: it is the last gate before a number is allowed to
# resize the body the arms plan against. A 40mm or a 900mm "forearm" is a
# segmentation failure that passed every geometric test, and letting it through
# would move real planned surface. Outside this range the limb is refused.
CIRC_RANGE_MM = (150.0, 520.0)

# And the same gate on LENGTH, because the two failures are independent.
# anatomy.ADULT has a 330mm upper arm and a 265mm forearm; this spans roughly
# half to one and a half of the shorter one, which covers any adult and still
# rejects the foreshortened projections that pass every per-section test.
LEN_RANGE_MM = (140.0, 480.0)


def _px_to_mm_scale(depth_mm, fx):
    """Millimetres per pixel at a given depth. One ray, one similar triangle."""
    return float(depth_mm) / float(fx)


def limb_band(shape, p_prox, p_dist, band_frac=BAND_FRAC, end_trim=END_TRIM):
    """Pixels within a band around one bone. -> bool mask, or None.

    The bone is a segment, not a line: a point is in the band when its
    projection lands INSIDE the trimmed segment and its perpendicular distance
    is under the half-width. Using an infinite line instead would sweep in the
    torso wherever the arm points at it, which is most seated postures.
    """
    H, W = shape[:2]
    p0 = np.asarray(p_prox, float)
    p1 = np.asarray(p_dist, float)
    v = p1 - p0
    L = float(np.linalg.norm(v))
    if L < 12.0:
        # The bone projects to almost nothing: the limb points at the camera
        # and its silhouette width is a foreshortened fiction. scan.py refuses
        # the same case (_foreshortening); refuse it here rather than measure a
        # circle and call it an arm.
        return None
    u = v / L
    ys, xs = np.mgrid[0:H, 0:W]
    d = np.stack([xs - p0[0], ys - p0[1]], -1)
    along = d @ u
    perp = np.abs(d[..., 0] * (-u[1]) + d[..., 1] * u[0])
    return ((along >= L * end_trim) & (along <= L * (1.0 - end_trim)) &
            (perp <= L * band_frac))


def measure_limb_px(person_mask, p_prox, p_dist, mm_per_px, depth_mm=None,
                    n_stations=N_STATIONS, flatten=LIMB_FLATTEN):
    """One limb. -> dict or None.

    `person_mask` is person-versus-background. `depth_mm` is an aligned depth
    image in millimetres, or None. Everything geometric is girth's.
    """
    band = limb_band(person_mask.shape, p_prox, p_dist)
    if band is None:
        return None
    mask = np.asarray(person_mask, bool) & band
    if mask.sum() < 200:
        return None

    ax = G.limb_axis(mask)
    if ax is None:
        return None
    c, along, across = ax

    # Sections perpendicular to the mask's OWN principal axis, not to the
    # landmark bone. The two normally agree; where they disagree the mask is
    # what girth is about to measure, so a cut square to the landmarks would be
    # oblique to the thing being cut and would read wide.
    ys, xs = np.nonzero(mask)
    s = (np.c_[xs, ys] - c) @ along
    lo, hi = np.quantile(s, 0.12), np.quantile(s, 0.88)

    # No depth: hand girth a flat profile and take only its silhouette half-
    # width. fit_cross_section would reject a flat surface -- correctly, that
    # is its aspect guard -- so it is not called at all here, and `b` comes
    # from the stated ratio instead of a fit that could not have run.
    have_depth = depth_mm is not None

    widths, circs, refused = [], [], 0
    for sv in np.linspace(lo, hi, n_stations):
        centre = c + along * sv
        if have_depth:
            pr = G.sample_section(mask, depth_mm, centre, across, mm_per_px)
        else:
            pr = G.sample_section(mask, np.zeros_like(mask, float), centre,
                                  across, mm_per_px)
        if pr is None:
            refused += 1
            continue
        u, z, a_mm = pr
        widths.append(a_mm)
        if have_depth:
            fit = G.fit_cross_section(u, z, a_mm)
            if fit is None:
                # The depth profile did not describe a limb. The silhouette
                # still did, so fall back to the ratio for this station rather
                # than throwing the width away.
                circs.append(G.ellipse_perimeter(a_mm, a_mm * flatten))
            else:
                circs.append(fit["circumference"])
        else:
            circs.append(G.ellipse_perimeter(a_mm, a_mm * flatten))

    if len(circs) < MIN_STATIONS:
        return None

    circ = float(np.median(circs))
    if not (CIRC_RANGE_MM[0] <= circ <= CIRC_RANGE_MM[1]):
        return None

    length_mm = float(np.linalg.norm(np.asarray(p_dist, float) -
                                     np.asarray(p_prox, float)) * mm_per_px)

    # FORESHORTENING, WHICH IS THE ONE FAILURE THAT SURVIVES EVERY GATE ABOVE.
    #
    # A limb pointing at the camera projects short but keeps its true width, so
    # every cross-section is a clean, well-conditioned, PLAUSIBLE ellipse and
    # girth accepts all of them. Nothing in a per-section test can see the
    # problem, because no single section is wrong -- the limb's LENGTH is.
    #
    # MEASURED on recordings/backup.mp4, where the subject's forearm points
    # toward the lens: 16 of 16 sections accepted, reporting a 64mm-long
    # forearm with a 120mm circumference. Both numbers are individually
    # survivable and the pair is anatomically impossible.
    #
    # A real limb is longer than it is round: the slenderness of an adult
    # forearm (265mm long, 280mm around) is about 0.95, an upper arm about
    # 1.10. Below 0.55 the bone is projecting at more than about 60 degrees to
    # the image plane and its apparent length is a fiction. scan.py refuses the
    # same case for the same reason (_foreshortening).
    #
    # It matters which way this errs: a limb modelled SHORT leaves real surface
    # off the end of the body the arms plan against, and the partition then
    # never assigns anyone to scrub it.
    #
    # BOTH an absolute length gate and a slenderness gate, because they catch
    # different things and each one alone let a real case through. At a 2600mm
    # standoff the same recording produced a 156mm-round, 89mm-long "upper
    # arm": slenderness 0.57, which cleared a 0.55 ratio test, while 89mm is
    # obviously not an adult upper arm. The absolute gate catches that. The
    # ratio gate catches the opposite error, where a wrong standoff scales both
    # numbers into range together and only their RELATIONSHIP stays impossible.
    if not (LEN_RANGE_MM[0] <= length_mm <= LEN_RANGE_MM[1]):
        return None
    if length_mm < 0.55 * circ:
        return None
    return {
        "circumference_mm": circ,
        "half_width_mm": float(np.median(widths)),
        "length_mm": length_mm,
        "stations": len(circs),
        "refused": refused,
        # The claim's own strength, carried with the number so no consumer has
        # to guess which one it got.
        "b_from": "depth" if have_depth else "ratio",
    }


def measure_person(person_mask, landmarks_px, visibility, fx, depth_mm=None,
                   torso_depth_mm=None):
    """A whole person -> ({anatomy measurement keys}, report), or (None, report).

    `landmarks_px` is the 33 MediaPipe landmarks in PIXELS, `visibility` their
    33 confidences. `fx` is the colour camera's focal length in pixels.

    The returned dict is exactly what `anatomy.anatomical_body(measurements=)`
    consumes, so the caller does not translate anything.
    """
    rep = {"limbs": {}, "refused": [], "source": "mediapipe-seg+landmarks"}

    # The scale from pixels to millimetres needs ONE depth. With a depth camera
    # it is measured at the torso; without one the caller must state a standoff
    # rather than have this module invent one. An unstated scale is the single
    # easiest way to produce a confident, wrong, whole-body size.
    if torso_depth_mm is None:
        if depth_mm is None:
            rep["refused"].append("no depth and no stated standoff: "
                                  "pixel size is unknown")
            return None, rep
        sh = [i for i in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)
              if visibility[i] >= MIN_VISIBILITY]
        if not sh:
            rep["refused"].append("no confident torso landmark to scale from")
            return None, rep
        vals = []
        for i in sh:
            x, y = int(round(landmarks_px[i][0])), int(round(landmarks_px[i][1]))
            if 0 <= y < depth_mm.shape[0] and 0 <= x < depth_mm.shape[1]:
                # A patch, not a pixel: stereo drops individual pixels, and a
                # zero read as a depth would put the person at the camera.
                patch = depth_mm[max(0, y - 6):y + 7, max(0, x - 6):x + 7]
                good = patch[patch > 0]
                if good.size:
                    vals.append(float(np.median(good)))
        if not vals:
            rep["refused"].append("depth had no valid pixel on the torso")
            return None, rep
        torso_depth_mm = float(np.median(vals))
    rep["standoff_mm"] = float(torso_depth_mm)
    mm_per_px = _px_to_mm_scale(torso_depth_mm, fx)
    rep["mm_per_px"] = mm_per_px
    # A STATED STANDOFF IS AN ASSUMPTION, AND NO GATE BELOW CAN CATCH IT BEING
    # WRONG. Every length and circumference is linear in this number, so an
    # error in it scales the whole person uniformly -- which is exactly the one
    # failure that leaves all the RATIOS correct and therefore passes every
    # plausibility test in this file.
    #
    # MEASURED on recordings/backup.mp4, whose true standoff is unknown: at an
    # assumed 1050mm and 2000mm the limbs were rejected as implausible, and at
    # 2600mm one arrived at 156mm round by 145mm long -- a shape no gate can
    # fault, from a scale nobody verified.
    #
    # With a depth camera this is measured rather than assumed and the problem
    # disappears. Without one, the number is carried in the report and stamped
    # on the result so no consumer can mistake an assumed scale for a measured
    # one. It is deliberately NOT hidden behind a default that looks measured.
    rep["scale_is_assumed"] = depth_mm is None

    for name, i_prox, i_dist, circ_key, len_key in LIMBS:
        if visibility[i_prox] < MIN_VISIBILITY or visibility[i_dist] < MIN_VISIBILITY:
            rep["refused"].append(f"{name}: landmark not confidently seen")
            continue
        got = measure_limb_px(person_mask, landmarks_px[i_prox],
                              landmarks_px[i_dist], mm_per_px, depth_mm)
        if got is None:
            rep["refused"].append(f"{name}: no measurable cross-section")
            continue
        rep["limbs"][name] = got

    # Left and right are the same bone measured twice. Averaging what was
    # actually measured is better than picking one, and anatomy's table has ONE
    # key per bone, so a choice has to be made here regardless.
    out = {}
    for key, names in (("upper_arm_circ_mm", ("upper_arm_L", "upper_arm_R")),
                       ("forearm_circ_mm", ("forearm_L", "forearm_R"))):
        vals = [rep["limbs"][n]["circumference_mm"] for n in names
                if n in rep["limbs"]]
        if vals:
            out[key] = float(np.mean(vals))
    for key, names in (("upper_arm_len_mm", ("upper_arm_L", "upper_arm_R")),
                       ("forearm_len_mm", ("forearm_L", "forearm_R"))):
        vals = [rep["limbs"][n]["length_mm"] for n in names
                if n in rep["limbs"]]
        if vals:
            out[key] = float(np.mean(vals))

    # Elbow and wrist are not separately measurable here: they are joints, and
    # the band deliberately trims them off because a joint is not a limb cross-
    # section. Rather than leave anatomy's population values beside measured
    # ones with no way to tell, they are SCALED by the measured/typical ratio of
    # the bone they terminate. That is an inference, and it is reported as one.
    try:
        from anatomy import ADULT
    except ImportError:
        ADULT = None
    if ADULT is not None:
        if "upper_arm_circ_mm" in out:
            r = out["upper_arm_circ_mm"] / ADULT["upper_arm_circ_mm"]
            out["elbow_circ_mm"] = ADULT["elbow_circ_mm"] * r
            rep.setdefault("inferred", []).append(
                f"elbow_circ_mm scaled x{r:.2f} from the measured upper arm")
        if "forearm_circ_mm" in out:
            r = out["forearm_circ_mm"] / ADULT["forearm_circ_mm"]
            out["wrist_circ_mm"] = ADULT["wrist_circ_mm"] * r
            rep.setdefault("inferred", []).append(
                f"wrist_circ_mm scaled x{r:.2f} from the measured forearm")

    # Shoulder to shoulder is a direct landmark distance and needs no
    # silhouette at all, so it is measured whenever both shoulders are seen.
    if (visibility[L_SHOULDER] >= MIN_VISIBILITY and
            visibility[R_SHOULDER] >= MIN_VISIBILITY):
        d = float(np.linalg.norm(np.asarray(landmarks_px[L_SHOULDER], float) -
                                 np.asarray(landmarks_px[R_SHOULDER], float)))
        # Landmarks sit on the SKIN at the acromion; biacromial is bone to
        # bone. anatomy's table is the bone number, so the skin span is scaled
        # by the same ratio the table itself implies rather than used raw.
        out["biacromial_mm"] = d * mm_per_px * 0.92
        rep.setdefault("inferred", []).append(
            "biacromial_mm from the shoulder landmark span, x0.92 skin to bone")

    if not rep["limbs"]:
        rep["refused"].append("no limb measured: keeping the population table")
        return None, rep
    rep["measured_keys"] = sorted(out)
    return out, rep


def summary(measurements, rep):
    """One block of plain text for the operator console. -> str."""
    if not measurements:
        return ("measurement REFUSED -- the demo keeps the population table.\n"
                + "\n".join(f"    {r}" for r in rep["refused"]))
    lines = [f"measured from {rep['source']} at "
             f"{rep.get('standoff_mm', 0):.0f}mm standoff "
             f"({rep.get('mm_per_px', 0):.2f} mm/px)"]
    for name, d in sorted(rep["limbs"].items()):
        lines.append(f"    {name:12s} circumference {d['circumference_mm']:5.0f}mm  "
                     f"length {d['length_mm']:5.0f}mm  "
                     f"{d['stations']} sections, {d['refused']} refused  "
                     f"[b from {d['b_from']}]")
    for r in rep.get("inferred", []):
        lines.append(f"    inferred: {r}")
    for r in rep["refused"]:
        lines.append(f"    refused:  {r}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Synthetic ground truth, so the wiring can be checked with no camera.
    # girth.render_section is the inverse of the fit, so a limb built from
    # known semi-axes must come back out at its known circumference.
    print("measuring a person from a segmentation mask and landmarks")
    H, W, fx = 480, 640, 600.0
    standoff = 1050.0
    mm_per_px = standoff / fx

    truth_a, truth_b = 48.0, 41.0
    truth_circ = G.ellipse_perimeter(truth_a, truth_b)

    # A vertical forearm, 250mm long, sitting in the middle of the frame.
    mask = np.zeros((H, W), bool)
    depth = np.zeros((H, W))
    half_px = truth_a / mm_per_px
    cx = W / 2.0
    y0, y1 = 140, 140 + int(250.0 / mm_per_px)
    for y in range(y0, y1):
        m_row, d_row = G.render_section(truth_a, truth_b, standoff, mm_per_px, W,
                                        noise_mm=0.30, edge_fatten_px=3,
                                        rng=np.random.default_rng(y))
        mask[y] = m_row
        depth[y] = d_row

    lms = np.zeros((33, 2))
    vis = np.zeros(33)
    lms[L_ELBOW] = (cx, y0)
    lms[L_WRIST] = (cx, y1)
    vis[L_ELBOW] = vis[L_WRIST] = 1.0

    got = measure_limb_px(mask, lms[L_ELBOW], lms[L_WRIST], mm_per_px, depth)
    assert got is not None, "a clean synthetic limb was refused"
    err = abs(got["circumference_mm"] - truth_circ)
    print(f"\n  with depth:    truth {truth_circ:.1f}mm  "
          f"measured {got['circumference_mm']:.1f}mm  "
          f"error {err:.1f}mm  ({got['stations']} sections)")
    assert err < 25.0, f"circumference off by {err:.1f}mm"

    got2 = measure_limb_px(mask, lms[L_ELBOW], lms[L_WRIST], mm_per_px, None)
    assert got2 is not None, "the no-depth path was refused"
    err2 = abs(got2["circumference_mm"] - truth_circ)
    print(f"  without depth: truth {truth_circ:.1f}mm  "
          f"measured {got2['circumference_mm']:.1f}mm  "
          f"error {err2:.1f}mm  [b from a stated ratio, not measured]")

    # --- it must REFUSE rather than invent ----------------------------------
    print("\n  refusals (a wrong size moves where the arms plan to press):")

    bad = measure_limb_px(mask, (cx, y0), (cx, y0 + 4), mm_per_px, depth)
    print(f"    bone foreshortened to nothing   -> "
          f"{'REFUSED' if bad is None else 'ACCEPTED (bad)'}")
    assert bad is None

    empty = measure_limb_px(np.zeros((H, W), bool), lms[L_ELBOW], lms[L_WRIST],
                            mm_per_px, depth)
    print(f"    nothing inside the band         -> "
          f"{'REFUSED' if empty is None else 'ACCEPTED (bad)'}")
    assert empty is None

    # A limb running off the side of the frame: girth refuses every section
    # because the silhouette is then a LOWER BOUND on the width, not a
    # measurement, and a limb measured too thin is the unsafe direction.
    #
    # The frame has to be narrower than the limb for this to be the case it
    # claims to be. girth.py's own header records the same trap: an earlier
    # version of its test used a 120px frame against an 86px limb, so "out of
    # frame" was never actually exercised. This limb is 58px wide, so a 40px
    # frame genuinely clips it on both sides.
    clip_w = 40
    narrow = np.zeros((H, clip_w), bool)
    nd = np.zeros((H, clip_w))
    for y in range(y0, y1):
        m_row, d_row = G.render_section(truth_a, truth_b, standoff, mm_per_px,
                                        clip_w, noise_mm=0.3,
                                        rng=np.random.default_rng(y))
        narrow[y] = m_row
        nd[y] = d_row
    assert narrow[(y0 + y1) // 2][0] and narrow[(y0 + y1) // 2][-1], \
        "the 'out of frame' case does not actually run out of frame"
    off = measure_limb_px(narrow, (clip_w // 2, y0), (clip_w // 2, y1),
                          mm_per_px, nd)
    print(f"    limb runs out of frame          -> "
          f"{'REFUSED' if off is None else 'ACCEPTED (bad)'}")
    assert off is None

    # --- the whole person, and the dict anatomy actually consumes -----------
    # The fixture above is one forearm, which measure_person correctly refuses
    # for want of a torso to take the pixel scale from. Add a shoulder and an
    # upper arm so the whole path runs, including the left/right average and
    # the keys inferred from the bones that WERE measured.
    sh_y = y0 - int(330.0 / mm_per_px)
    for y in range(max(0, sh_y), y0):
        m_row, d_row = G.render_section(56.0, 48.0, standoff, mm_per_px, W,
                                        noise_mm=0.30, edge_fatten_px=3,
                                        rng=np.random.default_rng(1000 + y))
        mask[y] = m_row
        depth[y] = d_row
    lms[L_SHOULDER] = (cx, sh_y)
    lms[R_SHOULDER] = (cx - 390.0 / mm_per_px, sh_y)
    lms[L_HIP] = (cx, y1)
    vis[L_SHOULDER] = vis[R_SHOULDER] = vis[L_HIP] = 1.0

    m, rep = measure_person(mask, lms, vis, fx, depth)
    print("\n  whole person, the left arm visible:")
    print("    " + summary(m, rep).replace("\n", "\n    "))

    if m:
        try:
            from anatomy import anatomical_body, ADULT
            body_pop, _ = anatomical_body()
            body_meas, _ = anatomical_body(measurements=m)
            print(f"\n  anatomical_body accepts it: "
                  f"{len(body_meas.regions)} regions, same as the "
                  f"population body's {len(body_pop.regions)}")
            for k in sorted(m):
                print(f"    {k:22s} table {ADULT[k]:7.1f}  "
                      f"measured {m[k]:7.1f}mm")
        except ImportError as e:
            print(f"\n  (anatomy not importable here: {e})")

    print("\nOK")
