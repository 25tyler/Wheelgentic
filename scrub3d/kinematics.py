"""scrub3d/kinematics.py -- RoArm-M2-S forward and inverse kinematics.

PURE MATH. No serial, no I/O, no hardware. Runs on Windows today.

WHY THIS EXISTS AND WHY IT IS NOT A PORT OF THE FIRMWARE
--------------------------------------------------------
Collision avoidance between four arms needs to know where every LINK is, not
just where the tool point is. The firmware only tells us the tool point plus
joint angles, so we need our own kinematics to place the links in space.

The obvious approach -- reverse engineer the firmware's simpleLinkageIkRad --
is wrong twice. It is AGPL-3.0, so we may not copy it. And it hides a trap:
ARM_L2 is hypot(236.82, 30.00), meaning link 2 has a 30mm perpendicular offset
that acts as a fixed 7.22 degree angular bias. Miss it and every joint angle is
off by 7.22 degrees, which is 63mm of link position error at 500mm reach --
larger than any collision threshold worth setting.

Instead the geometry below is derived from PUBLISHED CONSTANTS (facts, not
code) using textbook two-link trigonometry. The offsets are structural here,
so the bias cannot be forgotten.

THE CONSTANTS AND WHERE THEY COME FROM
---------------------------------------
Waveshare ships two sources of truth and they do not fully agree.

  roarm_description.urdf joint origins, and the closed-form ComputeFk baked
  into the generated IKFast solver (Apache-2.0, OpenRAVE, no ROS deps).
  Those two agree with each other exactly.

  RoArm-M2_example/RoArm-M2_config.h (AGPL, read for constants only) agrees on
  every length EXCEPT the base height: it says ARM_L1_LENGTH_MM 126.06 where
  the URDF says 123.059. A 3mm disagreement, unresolved.

We take the URDF/IKFast numbers because they are the pair that agree, and
because the URDF is what the vendor's own visualisation uses. BASE_H_MM_FIRMWARE
is kept below so the discrepancy is measurable rather than forgotten -- resolve
it against a physical arm (see verify_against_hardware in tests).

Also note ARM_L4 (67.85mm) plus the configurable EoAT offsets, which exist in
the firmware config and in the physical arm but are NOT in the original
py/arm.py model. Whatever tool is fitted extends the chain past TCP_* below;
set TOOL_OFFSET_MM once you have measured your sponge mount.

GEOMETRY
--------
The yaw axis sits at (BASE_X_MM, 0, BASE_H_MM) in the base frame. Everything
distal rotates about it. Working in the vertical plane containing that axis and
the target, the chain reduces to a clean two-link problem with perpendicular
offsets, so define per-elbow-angle:

    A(j2) = L2 + Fz*cos(j2) - Fx*sin(j2)     reach along the upper-arm axis
    C(j2) = E  + Fz*sin(j2) + Fx*cos(j2)     perpendicular offset

then with H = BASE_H_MM:

    radial = C*cos(j1) - A*sin(j1)
    z      = H + A*cos(j1) + C*sin(j1)
    x      = BASE_X_MM + cos(j0)*radial
    y      =            -sin(j0)*radial

The negative sign on y is the base joint's axis convention (roarm_ws_em0 URDF
declares axis "0 0 -1"). NOTE the newer roarm_ws repo flips that sign; the two
vendor repos disagree. If your arm yaws the wrong way, flip YAW_SIGN, and
resolve it against hardware rather than by reading either repo.

UNITS: millimetres and radians throughout, matching py/arm.py's Cartesian
convention (+X forward from base, +Y to the arm's left).
"""
import math
import os                       # read by the SCRUB3D_ARM switch at the foot

import numpy as np

# --- Published link geometry (mm) -------------------------------------------
# From roarm_description.urdf joint origins / IKFast ComputeFk (metres -> mm).
BASE_X_MM = 10.0            # yaw axis is offset 10mm from base_link origin
BASE_H_MM = 123.059270461044  # base_link -> link1 origin Z
L2_MM = 236.815132922094    # shoulder -> elbow, along the link
E_MM = 30.0023995170449     # elbow perpendicular offset -- the 7.22 deg bias
TCP_Z_MM = 280.2            # elbow -> hand_tcp, along the forearm
TCP_X_MM = 2.0              # elbow -> hand_tcp, perpendicular

# Kept only so the disagreement stays visible and testable. Not used.
BASE_H_MM_FIRMWARE = 126.06

# Distance from hand_tcp to the actual sponge contact centre. MEASURE THIS with
# calipers once the sponge mount exists; it is pure arm length as far as reach
# and collision are concerned, and leaving it at 0 makes both optimistic.
TOOL_OFFSET_MM = 0.0

YAW_SIGN = -1.0             # see module docstring; verify against hardware

# --- Joint limits (rad) -----------------------------------------------------
# py/arm.py CHECKS NONE OF THESE. Its reachable() tests only a sphere about the
# shoulder pivot; measured below, that over-approves by more than half. The
# firmware then silently constrain()s the joint and the arm goes somewhere
# other than commanded -- invisible on a table, but it puts collision capsules
# in the wrong place, which is what four arms around a person cannot afford.
#
# THE TWO VENDOR SOURCES DISAGREE, AND arm.py's OWN HOME POSE BREAKS THE TIE.
#
#   RoArm-M2_config.h : base, shoulder, elbow all +/- pi/2
#   roarm_description.urdf : base +/-3.1416, shoulder +/-1.5708, elbow [-1, 3.1416]
#
# Test: json_cmd.h documents the init pose as x=235.11, y=0, z=234.79, and
# py/arm.py:30 carries it as HOME. Solving it here needs j1=13.3deg,
# j2=129.6deg. That elbow angle is legal under the URDF limit and illegal under
# the firmware's, and the arm demonstrably reaches its own init pose -- so the
# firmware's ARM_ELBOW_LIMIT_*_RAD must apply to a DIFFERENT angle variable
# (servo space, or a differently-referenced elbow) than the URDF/IKFast j2 this
# module uses. Take the URDF limit for the elbow.
#
# For the base we keep the TIGHTER firmware value: it is the conservative
# choice, and being wrongly conservative costs coverage while being wrongly
# permissive drives an arm into a clamp we did not model. Shoulder agrees.
#
# VERIFY ALL THREE against measured encoder angles on first hardware contact
# (scrub3d/tools/verify_kinematics.py) and correct here. Until then these are
# the best-evidenced defaults, not measurements.
BASE_MIN_RAD, BASE_MAX_RAD = -math.pi / 2, math.pi / 2
SHOULDER_MIN_RAD, SHOULDER_MAX_RAD = -math.pi / 2, math.pi / 2
ELBOW_MIN_RAD, ELBOW_MAX_RAD = -1.0, math.pi

JOINT_LIMITS = (
    (BASE_MIN_RAD, BASE_MAX_RAD),
    (SHOULDER_MIN_RAD, SHOULDER_MAX_RAD),
    (ELBOW_MIN_RAD, ELBOW_MAX_RAD),
)

# Encoder resolution: ARM_SERVO_POS_RANGE 4096 over ARM_SERVO_ANGLE_RANGE 360.
# Useful as the floor on any "did it actually get there" tolerance.
ENCODER_RAD = math.radians(360.0 / 4096.0)   # ~0.00153 rad, 0.088 deg


def _ac(j2):
    """The two elbow-dependent lengths. See module docstring."""
    return (L2_MM + (TCP_Z_MM + TOOL_OFFSET_MM) * np.cos(j2) - TCP_X_MM * np.sin(j2),
            E_MM + (TCP_Z_MM + TOOL_OFFSET_MM) * np.sin(j2) + TCP_X_MM * np.cos(j2))


def fk(j0, j1, j2):
    """Joint angles (rad) -> tool point (x, y, z) in mm, base frame.

    Vectorized: pass scalars or equal-shaped arrays.
    """
    j0, j1, j2 = np.asarray(j0, float), np.asarray(j1, float), np.asarray(j2, float)
    a, c = _ac(j2)
    radial = c * np.cos(j1) - a * np.sin(j1)
    x = BASE_X_MM + np.cos(j0) * radial
    y = YAW_SIGN * np.sin(j0) * radial
    z = BASE_H_MM + a * np.cos(j1) + c * np.sin(j1)
    return x, y, z


def link_points(j0, j1, j2):
    """-> (base, shoulder, elbow, tcp), each (x, y, z) mm in the base frame.

    THIS is what the collision layer consumes. Capsules are built between
    consecutive points, so an error here is an error in every collision test.
    """
    j0, j1, j2 = float(j0), float(j1), float(j2)
    base = (0.0, 0.0, 0.0)
    shoulder = (BASE_X_MM, 0.0, BASE_H_MM)

    # Elbow is the same chain with the forearm removed, i.e. A=L2, C=E.
    radial_e = E_MM * math.cos(j1) - L2_MM * math.sin(j1)
    elbow = (BASE_X_MM + math.cos(j0) * radial_e,
             YAW_SIGN * math.sin(j0) * radial_e,
             BASE_H_MM + L2_MM * math.cos(j1) + E_MM * math.sin(j1))

    x, y, z = fk(j0, j1, j2)
    return base, shoulder, elbow, (float(x), float(y), float(z))


def link_points_many(J):
    """link_points for many poses. (N, 3) joints -> (N, 4, 3)."""
    J = np.asarray(J, float).reshape(-1, 3)
    j0, j1, j2 = J[:, 0], J[:, 1], J[:, 2]
    out = np.zeros((len(J), 4, 3))
    out[:, 1] = (BASE_X_MM, 0.0, BASE_H_MM)
    radial_e = E_MM * np.cos(j1) - L2_MM * np.sin(j1)
    out[:, 2, 0] = BASE_X_MM + np.cos(j0) * radial_e
    out[:, 2, 1] = YAW_SIGN * np.sin(j0) * radial_e
    out[:, 2, 2] = BASE_H_MM + L2_MM * np.cos(j1) + E_MM * np.sin(j1)
    out[:, 3] = np.stack(fk(j0, j1, j2), 1)
    return out


def within_limits(j0, j1, j2):
    """Do these angles respect the firmware's constrain() bounds?"""
    return (BASE_MIN_RAD <= j0 <= BASE_MAX_RAD
            and SHOULDER_MIN_RAD <= j1 <= SHOULDER_MAX_RAD
            and ELBOW_MIN_RAD <= j2 <= ELBOW_MAX_RAD)


def ik(x, y, z, elbow_branch=+1):
    """Tool point (mm) -> (j0, j1, j2) in rad, or None if unreachable.

    Position-only, three joints -- which is all this arm has. Waveshare's own
    IKFast solver for the M2-S is translation3d with GetNumJoints()==3, and
    their MoveIt service takes {x,y,z} with no orientation. The fourth joint is
    the EoAT (gripper or wrist depending on {"T":1,"mode":N}) and is not part
    of the position solution.

    elbow_branch selects between the two arm configurations that reach the same
    point. Which one the firmware picks is NOT documented -- determine it by
    comparing against reported encoder angles and set the default accordingly.

    Returns None rather than raising, and None means "do not move", never
    "guess" -- the firmware has no NaN guard and a NaN target drives the
    shoulder to servo-middle at full speed.
    """
    dx, dy = x - BASE_X_MM, y
    radial = math.hypot(dx, dy)
    if radial < 1e-9:
        # Directly over the yaw axis: yaw is undefined, not zero. Refuse.
        return None
    j0 = math.atan2(YAW_SIGN * dy, dx)

    # radial is unsigned; recover its sign in the working plane.
    if math.cos(j0) * dx + YAW_SIGN * math.sin(j0) * dy < 0:
        radial = -radial

    dz = z - BASE_H_MM
    d_sq = radial * radial + dz * dz

    # d^2 = A(j2)^2 + C(j2)^2 = K0 + K1*cos(j2) + K2*sin(j2). Solve for j2.
    fz = TCP_Z_MM + TOOL_OFFSET_MM
    k0 = L2_MM ** 2 + E_MM ** 2 + fz ** 2 + TCP_X_MM ** 2
    k1 = 2.0 * (L2_MM * fz + E_MM * TCP_X_MM)
    k2 = 2.0 * (E_MM * fz - L2_MM * TCP_X_MM)

    amp = math.hypot(k1, k2)
    if amp < 1e-9:
        return None
    ratio = (d_sq - k0) / amp
    if not (-1.0 <= ratio <= 1.0):
        return None                       # outside the annulus, genuinely
    phi = math.atan2(k2, k1)
    j2 = phi + elbow_branch * math.acos(ratio)
    j2 = math.atan2(math.sin(j2), math.cos(j2))    # wrap to [-pi, pi]

    a, c = _ac(j2)
    j1 = math.atan2(dz, radial) - math.atan2(float(a), float(c))
    j1 = math.atan2(math.sin(j1), math.cos(j1))

    if not within_limits(j0, j1, j2):
        return None
    return j0, j1, j2


def reachable(x, y, z, elbow_branch=+1):
    """Can the arm actually put its tool point here?

    Stricter than py/arm.py's reachable(), deliberately. That one tests a
    sphere about the shoulder pivot and ignores the +/-90 degree base, shoulder
    and elbow limits entirely. This one solves the IK and checks the limits, so
    a True here means an achievable pose exists and link_points() will place
    the capsules where the arm really is.
    """
    return ik(x, y, z, elbow_branch) is not None


def reach_margin(x, y, z, elbow_branch=+1):
    """How much room is left before this point becomes unreachable, in mm.

    Positive inside the envelope, and it decays smoothly to 0 at the boundary.
    The coverage controller uses the gradient of this as a steering term, so it
    veers away from its own limits instead of hitting them and stalling.

    Returns 0.0 for points that are already unreachable.
    """
    sol = ik(x, y, z, elbow_branch)
    if sol is None:
        return 0.0
    j0, j1, j2 = sol
    # Distance to the nearest joint limit, converted to a Cartesian-ish scale
    # by the local moment arm. Crude but monotone, which is all a steering
    # term needs.
    dx, dy = x - BASE_X_MM, y
    radial = math.hypot(dx, dy)
    margins_rad = (
        min(j0 - BASE_MIN_RAD, BASE_MAX_RAD - j0),
        min(j1 - SHOULDER_MIN_RAD, SHOULDER_MAX_RAD - j1),
        min(j2 - ELBOW_MIN_RAD, ELBOW_MAX_RAD - j2),
    )
    lever = (max(radial, 1.0), L2_MM + TCP_Z_MM, TCP_Z_MM)
    return min(m * l for m, l in zip(margins_rad, lever))


def ik_many(x, y, z, elbow_branch=+1):
    """ik() for arrays of points. -> ((N, 3) joints, (N,) solved). Where
    ik() would return None, `solved` is False and the joints mean nothing."""
    x, y, z = (np.asarray(v, float) for v in (x, y, z))
    dx, dy = x - BASE_X_MM, y
    unsigned = np.hypot(dx, dy)
    j0 = np.arctan2(YAW_SIGN * dy, dx)
    radial = np.where(np.cos(j0) * dx + YAW_SIGN * np.sin(j0) * dy < 0,
                      -unsigned, unsigned)
    dz = z - BASE_H_MM
    d_sq = radial * radial + dz * dz
    fz = TCP_Z_MM + TOOL_OFFSET_MM
    k0 = L2_MM ** 2 + E_MM ** 2 + fz ** 2 + TCP_X_MM ** 2
    k1 = 2.0 * (L2_MM * fz + E_MM * TCP_X_MM)
    k2 = 2.0 * (E_MM * fz - L2_MM * TCP_X_MM)
    ratio = (d_sq - k0) / math.hypot(k1, k2)
    ok = (unsigned >= 1e-9) & (ratio >= -1.0) & (ratio <= 1.0)
    j2 = math.atan2(k2, k1) + elbow_branch * np.arccos(np.clip(ratio, -1.0, 1.0))
    j2 = np.arctan2(np.sin(j2), np.cos(j2))
    a, c = _ac(j2)
    j1 = np.arctan2(dz, radial) - np.arctan2(a, c)
    j1 = np.arctan2(np.sin(j1), np.cos(j1))
    J = np.stack([j0, j1, j2], -1)
    ok &= ((j0 >= BASE_MIN_RAD) & (j0 <= BASE_MAX_RAD)
           & (j1 >= SHOULDER_MIN_RAD) & (j1 <= SHOULDER_MAX_RAD)
           & (j2 >= ELBOW_MIN_RAD) & (j2 <= ELBOW_MAX_RAD))
    return J, ok


def reach_margin_many(x, y, z, elbow_branch=+1):
    """reach_margin() for arrays of points, the same sums done in numpy.

    The coverage controller asks this for every cell of a patch on every
    frame; one Python ik() per cell was the largest cost of a live frame.
    """
    J, ok = ik_many(x, y, z, elbow_branch)
    j0, j1, j2 = J[..., 0], J[..., 1], J[..., 2]
    unsigned = np.hypot(np.asarray(x, float) - BASE_X_MM, np.asarray(y, float))
    m0 = np.minimum(j0 - BASE_MIN_RAD, BASE_MAX_RAD - j0)
    m1 = np.minimum(j1 - SHOULDER_MIN_RAD, SHOULDER_MAX_RAD - j1)
    m2 = np.minimum(j2 - ELBOW_MIN_RAD, ELBOW_MAX_RAD - j2)
    out = np.minimum(np.minimum(m0 * np.maximum(unsigned, 1.0), m1 * (L2_MM + TCP_Z_MM)),
                     m2 * TCP_Z_MM)
    return np.where(ok, out, 0.0)


# --- WHICH ARM IS BOLTED TO THE CHAIR ----------------------------------------
# Everything above describes the RoArm-M2-S: 527mm of reach and a base that
# swings +-90deg. The arms on the wheelchair are Anvil OpenYAMs -- 743mm of
# reach, base -150deg..+180deg. kinematics_openyam.py measured them off
# dimOS's URDF with pinocchio and exports SURFACE for exactly this swap; its
# docstring has always named SCRUB3D_ARM, but nothing ever read the variable,
# so every importer silently got the smaller arm.
#
# THIS IS NOT COSMETIC. The coverage bake asks ik() whether a point on a
# person is reachable. With the RoArm numbers the answer was no for all 5824
# cells, and that zero was reported as a fact about the hardware -- a
# recommendation to re-mount arms that reach fine in real life. The operator
# said so, and he was right. A 600mm target solves under OpenYAM and fails
# under RoArm; the mounts are 425mm off centre, so a body sits squarely in
# the difference.
#
# Overwriting module globals rather than re-exporting keeps the ten importers
# (`import kinematics as K`) unchanged: K.ik, K.L2_MM and K.reachable are the
# OpenYAM's under the flag and the RoArm's without it. Default stays RoArm so
# no existing run changes behaviour by surprise.
if os.environ.get("SCRUB3D_ARM", "").strip().lower() == "openyam":
    try:
        from . import kinematics_openyam as _oy
    except ImportError:
        import kinematics_openyam as _oy
    globals().update(_oy.SURFACE)
    ARM_MODEL = "openyam"
else:
    ARM_MODEL = "roarm"


if __name__ == "__main__":
    # Self-test. Pure math, so this is meaningful with zero hardware -- which
    # is the point: the riskiest arithmetic in the project is checkable today.
    print("RoArm-M2-S kinematics self-test")
    print(f"  base offset {BASE_X_MM}mm, base height {BASE_H_MM:.3f}mm "
          f"(firmware disagrees: {BASE_H_MM_FIRMWARE})")

    # 1. Zero pose: arm straight up.
    x, y, z = fk(0.0, 0.0, 0.0)
    print(f"  zero pose -> ({x:.2f}, {y:.2f}, {z:.2f})")

    # 2. FK/IK round trip across the legal joint space.
    worst, n_ok, n_tot = 0.0, 0, 0
    grid = np.linspace(-math.pi / 2 * 0.98, math.pi / 2 * 0.98, 13)
    for a0 in grid:
        for a1 in grid:
            for a2 in grid:
                n_tot += 1
                px, py, pz = fk(a0, a1, a2)
                sol = ik(float(px), float(py), float(pz))
                if sol is None:
                    continue
                qx, qy, qz = fk(*sol)
                err = math.dist((float(px), float(py), float(pz)),
                                (float(qx), float(qy), float(qz)))
                worst = max(worst, err)
                n_ok += 1
    print(f"  round trip: {n_ok}/{n_tot} solved, worst position error "
          f"{worst:.6f}mm")
    assert worst < 1e-6, "FK/IK round trip broken"

    # 3. arm.py's HOME must solve -- it is the firmware's documented init pose,
    #    so an arm that cannot reach it would never boot. This doubles as the
    #    tie-breaker between the two vendor joint-limit tables (see above).
    home = (235.11, 0.0, 234.79)
    sol = ik(*home)
    assert sol is not None, (
        "HOME is unreachable -- the joint limits or the frame convention are "
        "wrong. Do not proceed; every capsule position depends on this.")
    print(f"  arm.py HOME {home} -> j=("
          f"{math.degrees(sol[0]):.2f}, {math.degrees(sol[1]):.2f}, "
          f"{math.degrees(sol[2]):.2f}) deg")

    # 4. How much does ignoring the base limit over-promise?
    #    Sample a shell at 400mm and count what a sphere test would approve.
    rng = np.random.default_rng(0)
    v = rng.normal(size=(20000, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    pts = v * 400.0 + np.array([BASE_X_MM, 0.0, BASE_H_MM])
    ok = sum(1 for p in pts if reachable(*p))
    print(f"  of 20000 points on a 400mm shell (which a sphere test approves "
          f"in full), only {ok} ({100.0 * ok / len(pts):.1f}%) are truly "
          f"reachable once joint limits are honoured")

    print("  link_points at zero pose:")
    for name, p in zip(("base", "shoulder", "elbow", "tcp"),
                       link_points(0.0, 0.0, 0.0)):
        print(f"    {name:9s} ({p[0]:8.2f}, {p[1]:8.2f}, {p[2]:8.2f})")
    print("OK")
