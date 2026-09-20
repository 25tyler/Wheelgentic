"""scrub3d/kinematics_openyam.py -- the Anvil OpenYAM as scrub3d's kinematics.

    SCRUB3D_ARM=openyam python scrub3d/live/live_body.py --drive dimos ...
    python scrub3d/kinematics_openyam.py          # self-test against the URDF

WHY A THREE-JOINT MODEL OF A SIX-JOINT ARM
------------------------------------------
Everything in scrub3d that reasons about an arm -- the fleet governor, the
collision capsules, the depth guard, reach maps, parking -- does it through
kinematics.py's surface: a tool point in, three joint angles out, and the
four link points those angles put in space. The RoArm-M2-S had three
position joints, so that surface was the whole arm.

The OpenYAM has six. dimOS solves all six (Pink IK on the real URDF) and
owns the arm; scrub3d only needs to know WHETHER a point is reachable and
WHERE THE LINKS ARE when the tool is there. For both, the arm's first three
joints are what matter: yaw, shoulder and elbow place the elbow and the
wrist, and the wrist joints only turn the last 210 mm. So this module is the
OpenYAM with its wrist held at zero: exact for the first three joints,
and the elbow-to-tool segment treated as one rigid link at the home wrist
pose.

THE NUMBERS, AND WHERE THEY COME FROM
-------------------------------------
Measured with pinocchio on dimOS's dual_openyam.urdf (the i2rt YAM), left
arm, in its own base frame, wrist joints at zero:

  yaw axis        at the base origin, +z up, right-handed
  shoulder pivot  20.0 mm forward of the yaw axis, 113.5 mm up
  upper arm       264.0 mm, shoulder -> elbow
  forearm         252.2 mm, elbow -> wrist pitch, with a 13.76 deg set
  wrist + gripper 209.6 mm further, straight out at the home pose
  => elbow -> grasp frame  458.6 mm at -6.23 deg to the forearm's set

Checked below against three URDF poses to 0.1 mm. The lateral offsets the
real links carry (up to 35 mm sideways at the elbow) cancel at the tool and
are covered by the collision capsule radii; they are not modelled.

JOINT CONVENTIONS -- THE URDF's, NOT INVENTED
--------------------------------------------
  j0  joint1, yaw, rad, +ve turns the arm toward +y      [-150 deg, 180 deg]
  j1  joint2, shoulder: 0 = upper arm horizontal BACK (-x),
      pi/2 = vertical, 210 deg = forward and down           [0, 210 deg]
  j2  joint3, elbow: 0 = forearm folded back along the upper arm,
      pi = fully open                                     [0, 180 deg]
So HOME = (0, 60 deg, 60 deg) puts the grasp frame at (342.6, 0, 402.1) mm,
which is where dimOS reports it.

WHAT THIS IS NOT
----------------
Not the real IK. dimOS may put the wrist somewhere else, which moves the
tool up to a hand-length from where this predicts for the same first three
joints. The governor's capsules therefore run to the point dimOS REPORTS
(arm_dimos feeds it that), and this model's tool point is used only to ask
"can the arm get there at all", where a hand-length of slack is inside the
reach it has anyway.
"""
import math
import os
import sys

import numpy as np

# --- geometry (mm) -------------------------------------------------------------
BASE_X_MM = 20.0                 # yaw axis -> shoulder pivot, along the arm
BASE_H_MM = 113.5                # floor of the base -> shoulder pivot
L2_MM = 264.0                    # shoulder -> elbow
FORE_MM = 252.2                  # elbow -> wrist pitch joint
FORE_SET_RAD = math.radians(13.76)   # the forearm's built-in bend
TCP_Z_MM = 458.6                 # elbow -> grasp frame, wrist at zero (rigid here)
TCP_SET_RAD = math.radians(-6.23)    # that segment, relative to the forearm's set
E_MM = 0.0                       # no RoArm-style elbow offset in this model
TCP_X_MM = 0.0
TOOL_OFFSET_MM = 0.0             # grasp frame -> sponge contact centre: MEASURE IT
BASE_H_MM_FIRMWARE = BASE_H_MM
YAW_SIGN = +1.0                  # measured: +90 deg yaw sends +x to +y

# Elbow-open reference: with j2 = 0 the forearm lies back along the upper
# arm; the forearm direction is upper + j2 - FORE_ZERO_RAD.
FORE_ZERO_RAD = math.pi - FORE_SET_RAD

REACH_MAX = L2_MM + TCP_Z_MM             # 722.6 mm from the shoulder
REACH_MIN = abs(L2_MM - TCP_Z_MM)        # 194.6 mm

# --- joint limits (rad), the URDF's --------------------------------------------
BASE_MIN_RAD, BASE_MAX_RAD = -2.618, math.pi
SHOULDER_MIN_RAD, SHOULDER_MAX_RAD = 0.0, 3.665
ELBOW_MIN_RAD, ELBOW_MAX_RAD = 0.0, math.pi
JOINT_LIMITS = ((BASE_MIN_RAD, BASE_MAX_RAD),
                (SHOULDER_MIN_RAD, SHOULDER_MAX_RAD),
                (ELBOW_MIN_RAD, ELBOW_MAX_RAD))
ENCODER_RAD = 2.0 * math.pi / 16384.0    # Damiao 14-bit encoders


def _planar(j1, j2):
    """Shoulder and elbow angles -> (elbow, tcp) as (u, z): u forward along
    the yawed arm from the yaw axis, z up. mm. Scalars or arrays."""
    upper = math.pi - np.asarray(j1, float)
    eu = BASE_X_MM + L2_MM * np.cos(upper)
    ez = BASE_H_MM + L2_MM * np.sin(upper)
    fore = upper + np.asarray(j2, float) - FORE_ZERO_RAD + TCP_SET_RAD
    tu = eu + TCP_Z_MM * np.cos(fore)
    tz = ez + TCP_Z_MM * np.sin(fore)
    return (eu, ez), (tu, tz)


def fk(j0, j1, j2):
    """Joint angles (rad) -> tool point (x, y, z) mm in the base frame.

    Vectorized like the RoArm's: pass scalars or equal-shaped arrays.
    """
    j0 = np.asarray(j0, float)
    (_, _), (tu, tz) = _planar(j1, j2)
    return (tu * np.cos(j0), YAW_SIGN * tu * np.sin(j0), tz)


def link_points(j0, j1, j2):
    """-> (base, shoulder, elbow, tcp), each (x, y, z) mm in the base frame.
    What the collision layer consumes."""
    j0, j1, j2 = float(j0), float(j1), float(j2)
    c, s = math.cos(j0), YAW_SIGN * math.sin(j0)
    (eu, ez), (tu, tz) = _planar(j1, j2)
    eu, ez, tu, tz = float(eu), float(ez), float(tu), float(tz)
    return ((0.0, 0.0, 0.0),
            (BASE_X_MM * c, BASE_X_MM * s, BASE_H_MM),
            (eu * c, eu * s, ez),
            (tu * c, tu * s, tz))


def link_points_many(J):
    """(N, 3) joints -> (N, 4, 3)."""
    J = np.asarray(J, float).reshape(-1, 3)
    j0, j1, j2 = J[:, 0], J[:, 1], J[:, 2]
    c, s = np.cos(j0), YAW_SIGN * np.sin(j0)
    upper = math.pi - j1
    eu = BASE_X_MM + L2_MM * np.cos(upper)
    ez = BASE_H_MM + L2_MM * np.sin(upper)
    fore = upper + j2 - FORE_ZERO_RAD + TCP_SET_RAD
    tu = eu + TCP_Z_MM * np.cos(fore)
    tz = ez + TCP_Z_MM * np.sin(fore)
    z0 = np.zeros_like(j0)
    out = np.stack([np.stack([z0, z0, z0], 1),
                    np.stack([BASE_X_MM * c, BASE_X_MM * s, np.full_like(j0, BASE_H_MM)], 1),
                    np.stack([eu * c, eu * s, ez], 1),
                    np.stack([tu * c, tu * s, tz], 1)], axis=1)
    return out


# What the arm stands on. These arms are clamped to a plank across a
# wheelchair, and the thighs of the person in it pass under that plank. A tool
# point below the base plane is inside the plank, or down beside it against
# their leg. The governor's own list of places to wait runs to 200 mm BELOW the
# base, which is fine for an arm on a post; here it sent a claw 75 mm under the
# top of the plank and into a thigh, where the camera read it 16 mm inside the
# leg. Nothing is proposed below this, for a stroke, a wait or a way between.
FLOOR_Z_MM = float(os.environ.get("SCRUB3D_ARM_FLOOR_MM", 70.0))


def within_limits(j0, j1, j2):
    return (BASE_MIN_RAD <= j0 <= BASE_MAX_RAD
            and SHOULDER_MIN_RAD <= j1 <= SHOULDER_MAX_RAD
            and ELBOW_MIN_RAD <= j2 <= ELBOW_MAX_RAD)


def _solve_planar(u, z):
    """(u, z) of the tool -> (j1, j2) elbow-up, or None."""
    du, dz = u - BASE_X_MM, z - BASE_H_MM
    d = math.hypot(du, dz)
    if d < 1e-9 or d > REACH_MAX + 1e-9 or d < REACH_MIN - 1e-9:
        return None
    cg = (L2_MM ** 2 + TCP_Z_MM ** 2 - d * d) / (2.0 * L2_MM * TCP_Z_MM)
    cb = (L2_MM ** 2 + d * d - TCP_Z_MM ** 2) / (2.0 * L2_MM * d)
    cg, cb = max(-1.0, min(1.0, cg)), max(-1.0, min(1.0, cb))
    gamma, beta = math.acos(cg), math.acos(cb)
    upper = math.atan2(dz, du) + beta          # elbow above the chord
    psi = -(math.pi - gamma)                    # forearm turns clockwise
    j1 = math.pi - upper
    j2 = psi + FORE_ZERO_RAD - TCP_SET_RAD
    return j1, j2


def ik(x, y, z, elbow_branch=+1):
    """Tool point (mm) -> (j0, j1, j2) rad, or None if unreachable or
    outside the joint limits. Position only, elbow up, wrist at zero."""
    x, y, z = float(x), float(y), float(z)
    if z < FLOOR_Z_MM:                         # the plank, and the legs under it
        return None
    u = math.hypot(x, y)
    j0 = math.atan2(YAW_SIGN * y, x) if u > 1e-9 else 0.0
    if j0 < BASE_MIN_RAD:                      # -150 deg limit: try the far way
        j0 += 2.0 * math.pi
    sol = _solve_planar(u, z)
    if sol is None:
        return None
    j1, j2 = sol
    if not within_limits(j0, j1, j2):
        return None
    return (j0, j1, j2)


def ik_nearest(x, y, z):
    """The nearest legal (j0, j1, j2) to a tool point, never None.

    For FEEDBACK, not proposals: dimOS can fold the wrist and put the real
    tool up to a hand-length off this three-joint chain, so a reported point
    can sit just past a limit or just inside the minimum reach. The governor
    still needs links in space for such a pose; the nearest legal pose is
    the honest approximation. Proposals go through ik(), which refuses."""
    x, y, z = float(x), float(y), float(z)
    u = math.hypot(x, y)
    j0 = math.atan2(YAW_SIGN * y, x) if u > 1e-9 else 0.0
    if j0 < BASE_MIN_RAD:
        j0 += 2.0 * math.pi
    j0 = min(max(j0, BASE_MIN_RAD), BASE_MAX_RAD)
    du, dz = u - BASE_X_MM, z - BASE_H_MM
    d = math.hypot(du, dz)
    if d < 1e-9:
        du, dz, d = REACH_MIN, 0.0, REACH_MIN
    if d > REACH_MAX or d < REACH_MIN:
        k = min(max(d, REACH_MIN), REACH_MAX) / d
        du, dz = du * k, dz * k
    sol = _solve_planar(BASE_X_MM + du, BASE_H_MM + dz)
    if sol is None:                                  # cannot happen after clamping
        return (j0, math.pi / 3, math.pi / 3)
    j1, j2 = sol
    return (j0, min(max(j1, SHOULDER_MIN_RAD), SHOULDER_MAX_RAD),
            min(max(j2, ELBOW_MIN_RAD), ELBOW_MAX_RAD))


def reachable(x, y, z, elbow_branch=+1):
    return ik(x, y, z) is not None


def reach_margin(x, y, z, elbow_branch=+1):
    """Room left before this point becomes unreachable, mm (negative:
    already out). Reach only; the joint limits are ik()'s business."""
    u = math.hypot(float(x), float(y))
    d = math.hypot(u - BASE_X_MM, float(z) - BASE_H_MM)
    return float(min(REACH_MAX - d, d - REACH_MIN, float(z) - FLOOR_Z_MM))


def ik_many(x, y, z, elbow_branch=+1):
    """ik() for arrays. -> ((N, 3) joints, (N,) solved)."""
    x, y, z = (np.asarray(v, float).ravel() for v in (x, y, z))
    n = len(x)
    J = np.zeros((n, 3))
    ok = np.zeros(n, bool)
    u = np.hypot(x, y)
    j0 = np.where(u > 1e-9, np.arctan2(YAW_SIGN * y, x), 0.0)
    j0 = np.where(j0 < BASE_MIN_RAD, j0 + 2.0 * math.pi, j0)
    du, dz = u - BASE_X_MM, z - BASE_H_MM
    d = np.hypot(du, dz)
    inr = (d > 1e-9) & (d <= REACH_MAX) & (d >= REACH_MIN)
    dd = np.where(inr, d, 1.0)
    cg = np.clip((L2_MM ** 2 + TCP_Z_MM ** 2 - dd * dd) / (2.0 * L2_MM * TCP_Z_MM), -1, 1)
    cb = np.clip((L2_MM ** 2 + dd * dd - TCP_Z_MM ** 2) / (2.0 * L2_MM * dd), -1, 1)
    upper = np.arctan2(dz, du) + np.arccos(cb)
    j1 = math.pi - upper
    j2 = -(math.pi - np.arccos(cg)) + FORE_ZERO_RAD - TCP_SET_RAD
    lim = ((j0 >= BASE_MIN_RAD) & (j0 <= BASE_MAX_RAD)
           & (j1 >= SHOULDER_MIN_RAD) & (j1 <= SHOULDER_MAX_RAD)
           & (j2 >= ELBOW_MIN_RAD) & (j2 <= ELBOW_MAX_RAD))
    ok = inr & lim & (z >= FLOOR_Z_MM)
    J[:, 0], J[:, 1], J[:, 2] = j0, j1, j2
    J[~ok] = 0.0
    return J, ok


def reach_margin_many(x, y, z, elbow_branch=+1):
    x, y, z = (np.asarray(v, float).ravel() for v in (x, y, z))
    u = np.hypot(x, y)
    d = np.hypot(u - BASE_X_MM, z - BASE_H_MM)
    return np.minimum(np.minimum(REACH_MAX - d, d - REACH_MIN), z - FLOOR_Z_MM)


# Everything kinematics.py exports when SCRUB3D_ARM=openyam.
SURFACE = {k: v for k, v in globals().items()
           if not k.startswith("_") and k not in ("math", "os", "sys", "np", "SURFACE")}


def _self_test():
    """Against pinocchio's forward kinematics of the URDF, recorded here."""
    cases = {  # joints -> grasp frame in the left base frame, metres
        (0.0, 1.047, 1.047): (0.3426, 0.0000, 0.4021),
        (0.0, 1.5708, 0.0): (0.0800, 0.0000, -0.0771),
        (0.0, 0.0, 1.5708): (-0.3040, 0.0000, 0.5681),
        (1.5708, 1.047, 1.047): (0.0000, 0.3426, 0.4021),
    }
    worst = 0.0
    for j, p in cases.items():
        got = np.array(fk(*j))
        err = float(np.linalg.norm(got - np.array(p) * 1000.0))
        worst = max(worst, err)
        back = ik(*got)
        rt = float(np.linalg.norm(np.array(back) - np.array(j))) if back else float("nan")
        print(f"  j={tuple(round(v, 3) for v in j)} -> {np.round(got, 1).tolist()} mm, "
              f"URDF says {np.round(np.array(p) * 1000, 1).tolist()}: off by {err:.1f} mm; "
              f"ik round trip {rt:.4f} rad")
    assert worst < 3.0, f"model is {worst:.1f} mm off the URDF"
    Q = np.array([[0.0, 1.047, 1.047], [0.3, 1.2, 0.9], [-0.5, 0.8, 1.6]])
    X, Y, Z = fk(Q[:, 0], Q[:, 1], Q[:, 2])
    for i in range(3):
        assert np.allclose((X[i], Y[i], Z[i]), fk(*Q[i]), atol=1e-9)
    print("  fk takes arrays: OK")
    home = ik(235.11, 0.0, 234.79)
    assert home is not None, "the RoArm HOME point must stay reachable (it is the start pose)"
    print(f"  RoArm HOME (235, 0, 235) -> {tuple(round(v, 3) for v in home)}")
    folded = ik_nearest(123.0, 62.0, 343.0)      # a wrist-folded pose dimOS reported
    assert ik(123.0, 62.0, 343.0) is None and within_limits(*folded), folded
    print(f"  folded wrist (123, 62, 343): ik refuses, nearest {tuple(round(v, 3) for v in folded)}")
    J, ok = ik_many([300, 900, 100], [0, 0, 0], [300, 300, 700])
    assert ok.tolist() == [True, False, True], ok
    assert np.allclose(J[0], ik(300, 0, 300), atol=1e-9)
    print(f"  reach {REACH_MIN:.0f}..{REACH_MAX:.0f} mm from the shoulder; "
          f"limits yaw {math.degrees(BASE_MIN_RAD):.0f}..{math.degrees(BASE_MAX_RAD):.0f} deg")
    print("  OpenYAM kinematics: OK")


if __name__ == "__main__":
    _self_test()
