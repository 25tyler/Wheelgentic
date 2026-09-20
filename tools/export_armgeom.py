#!/usr/bin/env python3
"""tools/export_armgeom.py -- the real arm's proportions, for the projector.

    python3 tools/export_armgeom.py          # writes web/assets/armgeom.json

WHY THIS EXISTS
---------------
web/robotarm.js draws the arm the audience actually looks at, and its link
lengths were typed by hand: upper 0.72, fore 0.60. The real RoArm-M2-S is
upper 238.7mm, fore 280.2mm. So the drawn forearm is 29% SHORTER than the
machine's, and the ratio is inverted -- the page reads fore/upper = 0.833
where the vendor's own URDF says 1.174. On a projector the co-star of the
pitch is not the shape of the robot it claims to be.

The numbers were never a secret. scrub3d/kinematics.py carries the URDF
geometry and scrub3d/armmesh.py loads Waveshare's own STL meshes, so the
truth has been on disk the whole time with nothing carrying it to the page.
This is that carrier.

BAKED, NOT STREAMED, for the same reason the body is (tools/export_body.py
says it at length): py/scrubbot.py's socket is EVENTS ONLY so the cartoon
survives the robot stack dying, and nothing that needs the backend alive
belongs between a keypress and a picture. This writes a small JSON once; the
page fetches it at boot and falls back to its own constants if it is absent.

THE TOTAL LENGTH IS PRESERVED ON PURPOSE.
The page's sponge reach was solved against the measured dirt position -- base
0.63 puts the sponge at 1.40 against dirt at 1.33, and web/main.js reads
`robot.sponge`'s world position to place the suds. Re-proportioning the links
with a free scale would move the sponge and break a placement that was solved,
not guessed. So the chain keeps its current total (0.08 + 0.72 + 0.62 = 1.42
units from the root to the sponge at rest) and only the SPLIT between base,
upper and fore changes, into the real ratio. The sponge lands where it landed
before; the arm holding it is finally the right shape.

UNITS. scrub3d works in millimetres, the page in three.js units. The
conversion is not a constant anybody chose: it falls out of pinning the real
chain's total length to the page's existing total, and it is written into the
file as `mm_per_unit` so a reader can check any number here against a ruler.
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scrub3d import armmesh as AM        # noqa: E402
from scrub3d import kinematics as K      # noqa: E402

# The page's chain from the arm root to the sponge at the rest pose, in its own
# units: shoulder pivot at y=0.08, upper link 0.72 long, sponge at y=0.62 on
# the fore link. THIS NUMBER IS THE ANCHOR -- see the module docstring. Change
# it only together with web/robotarm.js and the dirt placement it was solved
# against.
PAGE_CHAIN_UNITS = 0.08 + 0.72 + 0.62


def _openyam_radii(percentile=99.0):
    """Capsule radii off the OpenYAM's own meshes. -> {"base","upper","fore"}

    THE SAME ARITHMETIC armmesh.measured_radii() does, pointed at the other
    arm: take each link's vertices in the pose where its axis is known, and
    measure the perpendicular distance from that axis. A high percentile
    rather than the maximum, so one mounting lug does not inflate the whole
    link -- but HIGH, because this becomes a drawn width and the failure
    direction is drawing an arm thinner than the metal.

    Which mesh belongs to which segment comes from the URDF's own chain:
    base and link1 sit on the base column, link2 spans shoulder to elbow,
    and link3 onward is everything past the elbow including the wrist and
    the gripper. That last grouping is deliberate -- kinematics_openyam
    models elbow-to-tool as ONE rigid link at the home wrist pose, so the
    drawn forearm has to cover the same span or the picture and the reach
    envelope disagree about where the arm ends.
    """
    from scrub3d import armmesh_openyam as OY
    tf = OY.link_transforms([0.0] * 6)
    ms = OY.meshes()
    groups = {
        "base":  ("base", "link1"),
        "upper": ("link2",),
        "fore":  ("link3", "link4", "link5", "gripper"),
    }
    # Segment endpoints at the zero pose, from the same kinematics the reach
    # envelope uses. Measuring against anything else would give a width that
    # does not belong to the arm being drawn.
    base, shoulder, elbow, tcp = K.link_points(0.0, 0.0, 0.0)
    segs = {"base": (base, shoulder), "upper": (shoulder, elbow),
            "fore": (elbow, tcp)}

    out = {}
    for cap, names in groups.items():
        pts = []
        for nm in names:
            got = ms.get(nm)
            if got is None:
                continue
            V = np.asarray(got[0], float)
            T = tf.get(nm)
            if T is not None:
                V = (np.asarray(T)[:3, :3] @ V.T).T + np.asarray(T)[:3, 3]
            pts.append(V)
        if not pts:
            continue
        P = np.vstack(pts)
        a, b = (np.asarray(v, float) for v in segs[cap])
        d = b - a
        L2 = float(d @ d)
        if L2 < 1e-9:
            continue
        t = np.clip((P - a) @ d / L2, 0.0, 1.0)
        perp = np.linalg.norm(P - (a + t[:, None] * d), axis=1)
        r = float(np.percentile(perp, percentile))
        if not np.isfinite(r):
            raise RuntimeError(f"OpenYAM radius for {cap!r} is not finite")
        out[cap] = r
    return out


def _openyam_eoat_radius(percentile=99.0):
    """How wide the claw is, off its own meshes. -> float mm.

    The gripper plus both finger tips, measured about the tool axis. Drawn
    as one box, so the widest part is what matters.
    """
    from scrub3d import armmesh_openyam as OY
    tf = OY.link_transforms([0.0] * 6)
    ms = OY.meshes()
    pts = []
    for nm in ("gripper", "tip_left", "tip_right"):
        got = ms.get(nm)
        if got is None:
            continue
        V = np.asarray(got[0], float)
        T = tf.get(nm)
        if T is not None:
            V = (np.asarray(T)[:3, :3] @ V.T).T + np.asarray(T)[:3, 3]
        pts.append(V)
    if not pts:
        return 0.0
    P = np.vstack(pts)
    c = P.mean(0)
    return float(np.percentile(np.linalg.norm(P[:, :2] - c[:2], axis=1),
                               percentile))


def geometry():
    """-> the dict written to web/assets/armgeom.json.

    Every length here is derived, never typed. kinematics.link_points() is the
    same function the collision layer places capsules with, so the page and the
    governor are reading one geometry rather than two that can drift.
    """
    # Zero pose: the chain is straight, so consecutive point distances ARE the
    # link lengths. Taking them from link_points rather than from the module's
    # L2_MM/TCP_Z_MM constants keeps the perpendicular offsets (E_MM, TCP_X_MM)
    # in the answer -- the 30mm elbow offset is a real 7.22 degree bias and the
    # drawn link is that little bit longer because of it.
    base, shoulder, elbow, tcp = K.link_points(0.0, 0.0, 0.0)
    base_h_mm = float(shoulder[2])
    upper_mm = float(np.linalg.norm(np.asarray(elbow) - np.asarray(shoulder)))
    fore_mm = float(np.linalg.norm(np.asarray(tcp) - np.asarray(elbow)))
    total_mm = base_h_mm + upper_mm + fore_mm

    # The scale is a CONSEQUENCE of pinning the real chain to the page's
    # existing total, not a number anyone picked. Everything below is in page
    # units once divided by it.
    mm_per_unit = total_mm / PAGE_CHAIN_UNITS

    # Capsule radii measured off the vendor's STL meshes against each capsule's
    # own axis -- the same call collide.py's radii were regenerated from. These
    # are HALF-widths of real metal, so a drawn box gets twice them.
    # THE RADII HAVE TO COME OFF THE ARM BEING DRAWN. armmesh.py loads the
    # RoArm's STLs and deliberately goes EMPTY under SCRUB3D_ARM=openyam --
    # everything that reads it (the depth guard, the camera check, the rig
    # editor) reasons about RoArm links, so it refuses rather than hand them
    # OpenYAM geometry. That refusal is correct and is why measured_radii()
    # raises KeyError here under the flag.
    #
    # For a WIDTH TO DRAW WITH, though, the OpenYAM's own meshes are exactly
    # right, and armmesh_openyam.py loads them. Measuring each link's points
    # against its own axis is the same arithmetic armmesh does; it just has
    # to be pointed at the right arm.
    if getattr(AM, "OPENYAM", False):
        radii = _openyam_radii()
        # The claw, from the same meshes. armmesh's eoat_radius() reads the
        # RoArm's gripper_link, which is not loaded under the flag for the
        # same reason the link radii are not.
        eoat_r = _openyam_eoat_radius()
    else:
        radii = AM.measured_radii()
        eoat_r = AM.eoat_radius()

    return {
        # WHICH ARM THIS FILE DESCRIBES. It said "roarm" unconditionally,
        # including when SCRUB3D_ARM=openyam had just baked OpenYAM numbers
        # into it -- so a file holding a 458.6mm forearm claimed to come from
        # the arm whose forearm is 280.2mm. That is how a stale bake went
        # unnoticed: the lengths were wrong and the only label that could
        # have said so was wrong in the same direction.
        "source": ("scrub3d/kinematics_openyam.py + scrub3d/armmesh_openyam.py"
                   " (OpenYAM URDF/STL)" if getattr(AM, "OPENYAM", False)
                   else "scrub3d/kinematics.py + scrub3d/armmesh.py"
                        " (roarm URDF/STL)"),
        "arm": "openyam" if getattr(AM, "OPENYAM", False) else "roarm",
        "mm_per_unit": round(mm_per_unit, 3),
        # Millimetres, so anybody can check these against the physical arm.
        "mm": {
            "base_height": round(base_h_mm, 2),
            "upper": round(upper_mm, 2),
            "fore": round(fore_mm, 2),
            "r_base": round(float(radii["base"]), 2),
            "r_upper": round(float(radii["upper"]), 2),
            "r_fore": round(float(radii["fore"]), 2),
            "r_eoat": round(float(eoat_r), 2),
            "reach_max": round(upper_mm + fore_mm, 2),
        },
        # Page units, ready to drop into robotarm.js's geometry calls. The
        # three lengths sum to PAGE_CHAIN_UNITS by construction.
        "units": {
            "shoulder_pivot": round(base_h_mm / mm_per_unit, 4),
            "upper": round(upper_mm / mm_per_unit, 4),
            "fore": round(fore_mm / mm_per_unit, 4),
            "w_base": round(2.0 * radii["base"] / mm_per_unit, 4),
            "w_upper": round(2.0 * radii["upper"] / mm_per_unit, 4),
            "w_fore": round(2.0 * radii["fore"] / mm_per_unit, 4),
            "d_eoat": round(2.0 * eoat_r / mm_per_unit, 4),
        },
        # The joint limits the real machine has, in degrees, so the page can
        # refuse to draw a pose the metal cannot hold. py/arm.py checks NONE of
        # these -- its reachable() is a sphere about the shoulder -- so the
        # drawn arm has been free to bend further than the real one can.
        "limits_deg": {
            "base": [round(np.degrees(K.BASE_MIN_RAD), 1),
                     round(np.degrees(K.BASE_MAX_RAD), 1)],
            "shoulder": [round(np.degrees(K.SHOULDER_MIN_RAD), 1),
                         round(np.degrees(K.SHOULDER_MAX_RAD), 1)],
            "elbow": [round(np.degrees(K.ELBOW_MIN_RAD), 1),
                      round(np.degrees(K.ELBOW_MAX_RAD), 1)],
        },
    }


def main(dest=None):
    out = geometry()
    if dest is None:
        dest = os.path.join(ROOT, "web", "assets", "armgeom.json")
    # Temp file then rename, exactly as export_body.py does: the page fetches
    # this path and a plain open("w") truncates first, so a fetch landing
    # mid-write reads a torn half-file and the JSON parse throws.
    tmp = dest + ".partial"
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    os.replace(tmp, dest)
    u, m = out["units"], out["mm"]
    print(f"wrote {dest}")
    print(f"  1 unit = {out['mm_per_unit']}mm")
    print(f"  base {m['base_height']}mm  upper {m['upper']}mm  fore {m['fore']}mm")
    print(f"  page units: pivot {u['shoulder_pivot']}  upper {u['upper']}  "
          f"fore {u['fore']}  (sum {u['shoulder_pivot'] + u['upper'] + u['fore']:.3f})")
    print(f"  robotarm.js had upper 0.72 / fore 0.60, a ratio of 0.833 where "
          f"the URDF says {m['fore'] / m['upper']:.3f}")
    return dest


if __name__ == "__main__":
    main()
