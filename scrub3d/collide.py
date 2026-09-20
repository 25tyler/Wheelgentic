"""scrub3d/collide.py -- capsule collision for N arms around a person.

PURE MATH. No serial, no I/O, no hardware. Runs on Windows today.

WHY CAPSULES AND NOT THE VENDOR MESHES
---------------------------------------
roarm_description ships STL meshes, but its URDF points <collision> at the SAME
mesh as <visual> -- there are no simplified collision shapes. Mesh-vs-mesh for
six arm pairs at 40Hz is the wrong tool anyway. Four capsules per arm captures
the geometry that matters to within a few millimetres, costs microseconds, and
is inspectable by eye in the Rerun view.

FOUR CAPSULES, NOT THREE
------------------------
The sponge gets its own capsule, separate from the forearm link. It is the only
part of the machine permitted to touch the person; folding it into the forearm
capsule makes that distinction impossible to express, and the distinction is
the entire safety argument. D_BODY applies to the three structural capsules and
explicitly NOT to the sponge.

THE THRESHOLD THAT IS EASY TO GET WRONG
----------------------------------------
D_BODY -- structure vs person -- is the check nobody performs today, and it is
more likely to hurt someone than an arm-arm collision: the elbow swinging into
a head while the sponge sits politely on a forearm. It is not optional.

STOPPING DISTANCE DRIVES THE NUMBERS
-------------------------------------
    40Hz governor tick            25 ms
    10Hz feedback latency        100 ms
    serial                        10 ms
    servo response                50 ms
                                 -------
    reaction                     185 ms  -> 44mm at 240mm/s
    plus deceleration                       ~15mm
                                 d_stop ~= 60mm

    model error: kinematics 10 + skeleton/mesh 15 + base calibration 10 ~= 35mm

If you raise py/arm.py's MAX_STEP_MM to unclip the scrub oscillation, d_stop
rises proportionally and D_HOLD must rise with it. Those two numbers are
coupled; change them together or not at all.
"""
import math

import numpy as np

try:
    from . import kinematics as K
except ImportError:                       # running as a script
    import kinematics as K

# --- Capsule radii (mm) -----------------------------------------------------
# MEASURED from the vendor's own STL meshes, not guessed. See
# scrub3d/armmesh.py: measured_radii() takes each capsule's mesh vertices,
# computes perpendicular distance from that capsule's axis, and reports the
# 99th percentile -- high, because the failure direction here is undersizing,
# and a percentile rather than the maximum so one mounting lug does not inflate
# every radius.
#
# THE FIRST VALUES HERE WERE GUESSES AND TWO WERE DANGEROUSLY SMALL:
#
#     base   guessed 45.0   measured 57.3   under by 12.3mm
#     upper  guessed 35.0   measured 45.0   under by 10.0mm
#     fore   guessed 30.0   measured 28.3   over, harmless
#
# An undersized capsule is a collision the guard never reports. Note the error
# was larger than a bounding-box estimate suggested (49.3 and 39.5), because
# the bulk sits off the capsule's own axis -- which is exactly why this is
# computed from the axis rather than eyeballed from extents.
#
# Regenerate with:  python scrub3d/armmesh.py
R_BASE = 57.3        # base column, servo housing and link1
R_UPPER = 46.0       # shoulder -> elbow. 45.7 measured from the STL
                     # against the capsule AXIS, not a bounding box:
                     # mesh_escapes_capsules() found link2 poking
                     # 0.7mm out in 381 of 400 poses at 45.0, and an
                     # undersized capsule is an unflagged collision.
R_FORE = 30.0        # elbow -> tool point (measured 28.3; kept conservative)
R_SPONGE = 40.0      # the sponge itself. A SPHERE, see the plan: the arm has
                     # no commandable orientation, so a ball has nothing to
                     # get wrong. Set to the ball's actual radius.
# A sponge that is NOT a ball: SCRUB3D_SPONGE_R_MM is how far it reaches from
# the grip point, whichever way the claw happens to be turned. The OpenYAMs hold
# a block about 90 x 60 x 40 mm crosswise in the claw: 30 mm out at its flat
# face, 45 along its long edge, 64 at a corner. At 40 the grip point was held
# 37 mm off the skin and the block was pushed 1 to 3 cm into the person.
R_SPONGE = float(__import__("os").environ.get("SCRUB3D_SPONGE_R_MM", R_SPONGE))
R_EOAT = 69.0        # the EoAT jaw, as a sphere about its own pivot.
#
# THIS TERM WAS MISSING ENTIRELY, and it is the worst of the three errors on
# this list because it was not an undersized capsule but an absent one.
#
# The arm has FOUR joints. armmesh attached gripper_link with a fixed
# transform, so the model was 3-DOF and the jaw was tacitly assumed to live
# inside R_SPONGE. The vendor's URDF declares link3_to_gripper_link revolute
# over 0 to 1.5 rad. Measured against the real mesh, the jaw sweeps 86 degrees,
# travels up to 73mm, and puts 77.6% of its swept volume OUTSIDE the sponge
# sphere, overhanging by up to 47.6mm -- more than D_ESTOP. The collision layer
# could have been 47mm wrong about where a piece of metal was and reported
# clear.
#
# A sphere at the pivot bounds the jaw at EVERY j3, because the jaw is rigid
# about that pivot. So this costs one capsule and no knowledge of the fourth
# servo, which is just as well: nothing reads it today.
#
# If the jaw is physically replaced by a ball sponge, drop this to the ball's
# radius -- but only after looking at the arm, not before.

# --- Thresholds (mm) --------------------------------------------------------
D_HOLD = 90.0        # lower-priority arm holds
D_ESTOP = 40.0       # fleet estop. Should never fire. If it does, the model or
                     # the base calibration is wrong -- do not just raise it.
D_BODY = 60.0        # structural capsules vs the person. Sponge exempt.

CAPSULE_NAMES = ("base", "upper", "fore", "sponge", "eoat")
CAPSULE_RADII = (R_BASE, R_UPPER, R_FORE, R_SPONGE, R_EOAT)

# Which capsules are STRUCTURE, i.e. metal that must never touch a person. The
# sponge is the only part allowed to make contact; the jaw is not, and adding
# it here is half the reason the fifth capsule exists.
STRUCTURAL = (0, 1, 2, 4)

# THE MOUNT DOES NOT MOVE. The base column, and the upper arm where it leaves
# the shoulder, stay where the arm was mounted whatever the joints do, so how
# close they are to the person is settled by the placement, and no move makes
# it better or worse. Checked move by move, they refuse EVERY move of an arm
# mounted a few centimetres from a chair edge or a knee, and let a few
# millimetres of body model decide whether an arm works at all. So checks of
# a MOVE (moving_capsules) leave them out: the upper arm starts
# SHOULDER_ZONE_MM from the shoulder, where a point travels about 5mm in the
# stopping time above. The mount itself is checked where the arm is placed.
MOVING = (1, 2, 4)
SHOULDER_ZONE_MM = 60.0


def _seg_seg_dist(p1, q1, p2, q2):
    """Closest distance between segments p1q1 and p2q2. Vectorized.

    Ericson, Real-Time Collision Detection 5.1.9. All inputs (..., 3) arrays;
    returns (...,).

    The degenerate cases are the whole difficulty: a zero-length segment (a
    parked arm can produce one) and exactly parallel segments (two arms mounted
    on the same rail will produce them constantly). Both are handled by the
    clamped denominators below rather than by branching, so there is no path
    that returns NaN. A NaN here would compare False against every threshold
    and silently disable the guard -- the same class of bug as the project's
    documented `if (n < 1e-8)` NaN trap.
    """
    p1, q1, p2, q2 = (np.asarray(a, float) for a in (p1, q1, p2, q2))
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a = np.sum(d1 * d1, -1)
    e = np.sum(d2 * d2, -1)
    f = np.sum(d2 * r, -1)
    c = np.sum(d1 * r, -1)
    b = np.sum(d1 * d2, -1)

    eps = 1e-12
    denom = a * e - b * b

    # Non-parallel: solve. Parallel (denom ~ 0): pick s=0 and let the clamp
    # below resolve t, which is the correct answer for parallel segments.
    s = np.where(denom > eps, np.clip((b * f - c * e) / np.where(denom > eps, denom, 1.0),
                                      0.0, 1.0), 0.0)
    t = (b * s + f) / np.where(e > eps, e, 1.0)
    t = np.where(e > eps, t, 0.0)

    # Re-clamp s against the clamped t (the second half of Ericson's routine).
    t_c = np.clip(t, 0.0, 1.0)
    s = np.where(a > eps, np.clip((b * t_c - c) / np.where(a > eps, a, 1.0), 0.0, 1.0), 0.0)

    c1 = p1 + d1 * s[..., None]
    c2 = p2 + d2 * t_c[..., None]
    return np.linalg.norm(c1 - c2, axis=-1)


def capsule_capsule_distance(a_seg, a_r, b_seg, b_r):
    """Surface-to-surface distance between two capsules. Negative = overlap."""
    return _seg_seg_dist(a_seg[..., 0, :], a_seg[..., 1, :],
                         b_seg[..., 0, :], b_seg[..., 1, :]) - (a_r + b_r)


def arm_capsules(T_world_base, j0, j1, j2):
    """Joint angles + base pose -> (4, 2, 3) capsule segment endpoints, world mm.

    T_world_base is the 4x4 from scrub3d/handeye.py. This is the ONLY place
    arm placement enters the collision layer, which is what makes arbitrary
    mounting free: tilt or invert the base and the capsules follow.
    """
    pts = np.array(K.link_points(j0, j1, j2), float)      # (4,3) base frame
    T = np.asarray(T_world_base, float)
    world = (T[:3, :3] @ pts.T).T + T[:3, 3]

    base, shoulder, elbow, tcp = world
    pivot = (T[:3, :3] @ _eoat_pivot(j0, j1, j2)) + T[:3, 3]

    # THE FORE CAPSULE STOPS SHORT OF THE TOOL, and this is a correctness fix
    # rather than a relaxation.
    #
    # It used to run elbow -> tcp. The sponge is a sphere of radius R_SPONGE
    # centred on that same tcp, so whenever the sponge is touching skin the tcp
    # is R_SPONGE from it and the fore capsule's surface is R_SPONGE - R_FORE
    # away: 10mm, against a D_BODY of 60. That made CONTACT ITSELF a
    # body-clearance violation. Not a tuning problem -- a model in which the
    # machine cannot do the thing it exists to do.
    #
    # D_BODY is there to stop an elbow swinging into somebody's head while the
    # sponge sits politely on their forearm. It was never meant to police the
    # last few centimetres of the tool mount, which is what the sponge and the
    # EoAT capsules are for. So the fore capsule ends one sponge radius short
    # of the tool point, and the two capsules that cover that stretch take over.
    ax = tcp - elbow
    L = float(np.linalg.norm(ax))
    fore_end = tcp if L < 1e-6 else elbow + ax * max(0.0, 1.0 - R_SPONGE / L)

    # Sponge and jaw are spheres: degenerate (zero-length) capsules.
    return np.array([
        [base, shoulder],
        [shoulder, elbow],
        [elbow, fore_end],
        [tcp, tcp],
        [pivot, pivot],
    ])


def moving_capsules(T_world_base, j0, j1, j2):
    """The structure a move can bring closer to someone. -> (3, 2, 3), one
    capsule per MOVING entry, with the same radii. See MOVING."""
    caps = arm_capsules(T_world_base, j0, j1, j2)
    s, e = caps[1]
    L = float(np.linalg.norm(e - s))
    start = s + (e - s) * min(1.0, SHOULDER_ZONE_MM / L) if L > 1e-6 else s
    return np.array([[start, e], caps[2], caps[4]])


def moving_capsules_many(T_world_base, J):
    """moving_capsules for many poses of one arm. (N, 3) joints ->
    (N, 3, 2, 3), the same numbers."""
    J = np.asarray(J, float).reshape(-1, 3)
    T = np.asarray(T_world_base, float)
    R, o = T[:3, :3], T[:3, 3]
    pts = K.link_points_many(J) @ R.T + o
    shoulder, elbow, tcp = pts[:, 1], pts[:, 2], pts[:, 3]
    pivot = _eoat_pivot_many(J) @ R.T + o
    ax = tcp - elbow
    L = np.linalg.norm(ax, axis=1)
    k = np.maximum(0.0, 1.0 - R_SPONGE / np.maximum(L, 1e-9))
    fore_end = np.where((L < 1e-6)[:, None], tcp, elbow + ax * k[:, None])
    se = elbow - shoulder
    Ls = np.linalg.norm(se, axis=1)
    ks = np.minimum(1.0, SHOULDER_ZONE_MM / np.maximum(Ls, 1e-9))
    start = np.where((Ls > 1e-6)[:, None], shoulder + se * ks[:, None], shoulder)
    out = np.empty((len(J), 3, 2, 3))
    out[:, 0, 0], out[:, 0, 1] = start, elbow
    out[:, 1, 0], out[:, 1, 1] = elbow, fore_end
    out[:, 2, 0], out[:, 2, 1] = pivot, pivot
    return out


def _eoat_pivot_many(J):
    """_eoat_pivot for many poses. -> (N, 3), base frame."""
    try:
        try:
            from . import armmesh
        except ImportError:
            import armmesh
        return armmesh.eoat_pivot_many(J)
    except Exception:                                          # noqa: BLE001
        return np.array([_eoat_pivot(*j) for j in np.asarray(J, float).reshape(-1, 3)])


def _eoat_pivot(j0, j1, j2):
    """The EoAT joint origin in the arm's base frame, from the URDF chain.

    Imported lazily so collide.py keeps working if the meshes or the URDF are
    ever absent -- the geometry here is pure maths and should not need assets
    to load. If it cannot be read, fall back to the tool point, which is the
    conservative direction only because R_EOAT is large; the warning says so.
    """
    global _PIVOT_WARNED
    try:
        try:
            from . import armmesh
        except ImportError:
            import armmesh
        return np.asarray(armmesh.eoat_pivot(j0, j1, j2), float)
    except Exception:                                          # noqa: BLE001
        if not _PIVOT_WARNED:
            print("collide: URDF unavailable, placing the EoAT sphere at the "
                  "tool point. The jaw model is approximate.")
            _PIVOT_WARNED = True
        return np.array(K.link_points(j0, j1, j2)[3], float)


_PIVOT_WARNED = False


def arm_bounding_sphere(T_world_base):
    """(centre, radius) enclosing everything this arm can reach. Broad phase.

    Radius is the furthest the tool point can get from the base origin, plus
    the largest capsule radius. Cheap rejection: if two arms' spheres do not
    intersect, skip all 16 of their pair tests.
    """
    T = np.asarray(T_world_base, float)
    reach = K.BASE_X_MM + K.L2_MM + K.TCP_Z_MM + K.TOOL_OFFSET_MM
    return T[:3, 3].copy(), reach + max(CAPSULE_RADII)


class Fleet:
    """N arms, their placements, and the collision queries over them.

    Stateless per tick by design. A mutex-based scheme was considered and
    rejected: its failure mode is an arm holding a lock while estopped, which
    is precisely the shared-mutable-state class that produced five separate
    critical bugs in this project's emergency stop. This cannot deadlock
    because it holds nothing.
    """

    def __init__(self, base_poses, radii=CAPSULE_RADII):
        self.T = [np.asarray(t, float) for t in base_poses]
        self.n = len(self.T)
        self.radii = np.asarray(radii, float)
        self._spheres = [arm_bounding_sphere(t) for t in self.T]

    def capsules(self, joint_states):
        """[(j0,j1,j2)] * n -> (n, 4, 2, 3) world-frame segments."""
        return np.array([arm_capsules(T, *j)
                         for T, j in zip(self.T, joint_states)])

    def pair_distances(self, joint_states):
        """-> dict {(i, j): min surface distance mm}. Negative = interpenetrating.

        150 tests for four arms (6 pairs x 5x5), not 6. Saying "6" undercounts
        by 25x and would leave the forearm-vs-base cases unchecked. It was 96
        until the EoAT jaw became its own capsule; see R_EOAT.
        """
        caps = self.capsules(joint_states)
        out = {}
        for i in range(self.n):
            for j in range(i + 1, self.n):
                ci, ri = self._spheres[i]
                cj, rj = self._spheres[j]
                if np.linalg.norm(ci - cj) > ri + rj:
                    out[(i, j)] = float("inf")       # broad phase reject
                    continue
                a = caps[i][:, None, :, :]           # (4,1,2,3)
                b = caps[j][None, :, :, :]           # (1,4,2,3)
                d = _seg_seg_dist(a[..., 0, :], a[..., 1, :],
                                  b[..., 0, :], b[..., 1, :])
                d = d - (self.radii[:, None] + self.radii[None, :])
                out[(i, j)] = float(d.min())
        return out

    def body_distances(self, joint_states, body_points):
        """Min distance from each arm's STRUCTURAL capsules to the person.

        The sponge is deliberately excluded -- it is supposed to be in contact.
        body_points is (M, 3) world mm, e.g. the posed model's vertices.

        Returns [float] * n. This is the check that nothing in the project
        performs today.
        """
        caps = self.capsules(joint_states)
        pts = np.asarray(body_points, float)
        out = []
        for i in range(self.n):
            best = float("inf")
            for k in STRUCTURAL:              # base, upper, fore, EoAT jaw
                p, q = caps[i][k]
                seg = q - p
                L2 = float(seg @ seg)
                if L2 < 1e-12:
                    d = np.linalg.norm(pts - p, axis=1)
                else:
                    t = np.clip((pts - p) @ seg / L2, 0.0, 1.0)
                    d = np.linalg.norm(pts - (p + t[:, None] * seg), axis=1)
                best = min(best, float(d.min()) - self.radii[k])
            out.append(best)
        return out

    def link_clearance(self, arm, joints, tree, samples=14, moving=False):
        """Min distance from ONE arm's structural capsules to the body. Fast.

        moving=True measures only what a move can change (MOVING).

        The sponge is excluded: it is supposed to be in contact. Everything
        else must not be.

        This exists because reachability is not clearance. A tool point can sit
        perfectly on a forearm while the arm's own elbow is buried in the
        person's chest -- measured on the real body mesh, all four arms drove
        their structural links 28mm INTO the body while every reachability test
        passed. Nothing in the pipeline was asking this question.

        `tree` is a cKDTree over body points. Sampling along each capsule and
        querying the tree is ~60 lookups per pose, which is cheap enough to run
        inside the offline feasibility sweep where it belongs.
        """
        if moving:
            caps, parts = moving_capsules(self.T[arm], *joints), MOVING
        else:
            caps, parts = arm_capsules(self.T[arm], *joints), STRUCTURAL
            caps = caps[list(parts)]
        worst = float("inf")
        for (p, q), k in zip(caps, parts):      # structural only, not sponge
            t = np.linspace(0.0, 1.0, samples)[:, None]
            pts = p + (q - p) * t
            d, _ = tree.query(pts)
            worst = min(worst, float(d.min()) - self.radii[k])
        return worst

    def link_clearance_many(self, arm, J, tree, samples=14):
        """link_clearance(moving=True) for many poses of one arm at once, in
        one tree query. (N, 3) joints -> (N,) mm."""
        caps = moving_capsules_many(self.T[arm], J)
        t = np.linspace(0.0, 1.0, samples)[None, None, :, None]
        pts = caps[:, :, :1, :] + (caps[:, :, 1:, :] - caps[:, :, :1, :]) * t
        d, _ = tree.query(pts.reshape(-1, 3))
        d = d.reshape(len(caps), len(MOVING), samples).min(axis=2)
        return (d - self.radii[list(MOVING)][None, :]).min(axis=1)

    def check(self, joint_states, body_points=None):
        """One tick of the gate. -> (verdict, detail).

        verdict is "clear" | "hold" | "estop". The caller holds the
        LOWER-PRIORITY arm of any offending pair -- priority is a fixed total
        order within a tick, never a dynamic rule, because a dynamic rule
        chatters: both arms alternate yielding and neither makes progress.
        """
        pairs = self.pair_distances(joint_states)
        worst = min(pairs.values()) if pairs else float("inf")
        detail = {"pairs": pairs, "min_pair_mm": worst, "body_mm": None}

        verdict = "clear"
        if worst < D_ESTOP:
            verdict = "estop"
        elif worst < D_HOLD:
            verdict = "hold"

        if body_points is not None and len(body_points):
            bd = self.body_distances(joint_states, body_points)
            detail["body_mm"] = bd
            if min(bd) < D_BODY:
                # Structure near a person outranks an arm-arm hold.
                verdict = "estop" if min(bd) < D_BODY * 0.5 else "hold"
        return verdict, detail


def _T(x=0.0, y=0.0, z=0.0, yaw=0.0):
    """Convenience base pose: translation plus yaw about world Z."""
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([[c, -s, 0, x], [s, c, 0, y], [0, 0, 1, z], [0, 0, 0, 1]], float)


def mesh_escapes_capsules(j, T_world_base=None, j3=0.0, tol_mm=0.0):
    """Does any real link vertex sit outside the capsules? -> (worst, name).

    THE CHECK THAT CATCHES AN UNDERSIZED RADIUS, and an undersized radius is an
    unflagged collision -- the arm is bigger than the thing the governor is
    keeping away from people. Two of these radii were originally guessed and
    two of the guesses were small: R_BASE by 4mm and R_UPPER by 4.5mm, against
    the STL geometry that was sitting on disk the whole time.

    Every vertex is tested against the UNION of the capsules rather than
    against the one for its own link. That is deliberate: the fore capsule now
    stops one sponge radius short of the tool point, so the far end of link3 is
    genuinely outside it and is covered by the sponge and EoAT spheres instead.
    Testing link by link would fail on a correct arm.

    -> (worst overhang in mm, which link) -- negative means fully enclosed.
    """
    try:
        from . import armmesh
    except ImportError:
        import armmesh
    caps = arm_capsules(T_world_base if T_world_base is not None else np.eye(4),
                        *j)
    tf = armmesh.link_transforms(*j, j3=j3, T_world_base=T_world_base)
    worst, where = -1e9, ""
    for name, (V, _F) in armmesh.meshes().items():
        T = np.asarray(tf[name], float)
        W = (T[:3, :3] @ np.asarray(V, float).T).T + T[:3, 3]
        # Distance to each capsule axis, minus that capsule radius. A vertex is
        # enclosed when it is inside ANY of them, so take the best.
        out = np.full(len(W), 1e9)
        for (a, b), r in zip(caps, CAPSULE_RADII):
            ab = np.asarray(b, float) - np.asarray(a, float)
            L2 = float(ab @ ab)
            if L2 < 1e-12:
                d = np.linalg.norm(W - np.asarray(a, float), axis=1)
            else:
                t = np.clip((W - np.asarray(a, float)) @ ab / L2, 0.0, 1.0)
                d = np.linalg.norm(W - (np.asarray(a, float)
                                        + t[:, None] * ab), axis=1)
            out = np.minimum(out, d - r)
        if out.max() > worst:
            worst, where = float(out.max()), name
    return worst - tol_mm, where

if __name__ == "__main__":
    print("capsule collision self-test")

    # --- Known-answer geometry. These are the cases that break naive code. ---
    def d(p1, q1, p2, q2):
        return float(_seg_seg_dist(np.array(p1, float), np.array(q1, float),
                                   np.array(p2, float), np.array(q2, float)))

    cases = [
        ("parallel, offset 5",      (0, 0, 0), (10, 0, 0), (0, 5, 0), (10, 5, 0), 5.0),
        ("crossing, offset 3 in z", (-5, 0, 0), (5, 0, 0), (0, -5, 3), (0, 5, 3), 3.0),
        ("touching end to end",     (0, 0, 0), (1, 0, 0), (1, 0, 0), (2, 0, 0), 0.0),
        ("coincident",              (0, 0, 0), (1, 0, 0), (0, 0, 0), (1, 0, 0), 0.0),
        ("zero-length vs segment",  (0, 5, 0), (0, 5, 0), (-5, 0, 0), (5, 0, 0), 5.0),
        ("both zero-length",        (0, 0, 0), (0, 0, 0), (0, 0, 7), (0, 0, 7), 7.0),
        ("skew apart",              (0, 0, 0), (1, 0, 0), (0, 0, 9), (0, 1, 9), 9.0),
    ]
    for name, p1, q1, p2, q2, want in cases:
        got = d(p1, q1, p2, q2)
        assert abs(got - want) < 1e-9, f"{name}: got {got}, want {want}"
        assert not math.isnan(got), f"{name}: NaN"
        print(f"  {name:26s} {got:8.4f}  ok")

    # --- Four arms around a seated person, arbitrary placement. -------------
    # Deliberately NOT a symmetric layout: the point of the design is that
    # placement is an input, so the test should not look hand-tuned.
    poses = [
        _T(-380, -260, 0, math.radians(35)),
        _T(400, -240, 0, math.radians(145)),
        _T(-360, 300, 0, math.radians(-40)),
        _T(430, 330, 0, math.radians(-145)),
    ]
    fleet = Fleet(poses)
    home = K.ik(235.11, 0.0, 234.79)
    states = [home] * 4
    verdict, det = fleet.check(states)
    print(f"\n  four arms at HOME: verdict={verdict} "
          f"min pair separation={det['min_pair_mm']:.1f}mm")
    for (i, j), dist in sorted(det["pairs"].items()):
        tag = "inf (broad-phase reject)" if math.isinf(dist) else f"{dist:8.1f}mm"
        print(f"    arms {i}-{j}: {tag}")

    # Note the broad phase did NOT reject any pair above: at 800mm separation
    # the spheres still overlap, because a sphere of reach+radius is generous.
    # That is the correct conservative behaviour -- broad phase may only reject
    # what is definitely clear.

    # --- Drive two arms together and confirm the gate actually fires. -------
    # A guard never seen to fail is decoration. The four arms above are ~800mm
    # apart against ~520mm of reach, so they CANNOT touch -- a sweep between
    # them would "pass" while proving nothing. Use a pair that genuinely can
    # collide: two bases 360mm apart, facing each other.
    near = Fleet([_T(-350, 0, 0, 0.0), _T(350, 0, 0, math.pi)])
    print("\n  two arms 700mm apart, facing each other; arm 0 reaches across:")
    seen, fired_at = [], {}
    for step in range(45):
        x = 120.0 + step * 10.0
        sol = K.ik(x, 0.0, 300.0)
        if sol is None:
            continue
        v, dd = near.check([sol, home])
        seen.append(v)
        if v not in fired_at:
            fired_at[v] = (x, dd["min_pair_mm"])
    for v in ("clear", "hold", "estop"):
        if v in fired_at:
            x, m = fired_at[v]
            print(f"    first {v:6s} at x={x:6.1f}mm, separation {m:7.1f}mm")
    assert "hold" in seen, "the hold gate never fired -- it is decoration"
    assert "estop" in seen, "the estop gate never fired -- it is decoration"
    assert seen.index("hold") < seen.index("estop"), \
        "estop fired before hold -- the thresholds are inverted"
    print(f"    sequence: {' -> '.join(dict.fromkeys(seen))}")

    # --- Arm vs person. ------------------------------------------------------
    torso = np.random.default_rng(0).normal(scale=90.0, size=(400, 3)) + [0, 0, 950]
    bd = fleet.body_distances(states, torso)
    print(f"\n  structural capsules vs a torso point cloud: "
          f"min {min(bd):.1f}mm (threshold {D_BODY})")

    # --- The real arm, against the capsules that stand in for it. -----------
    #
    # An undersized capsule is an UNFLAGGED COLLISION: the metal is bigger than
    # the thing the governor keeps away from people, so every check reports
    # clear while the arm sweeps through the margin. Two of these radii were
    # originally guessed and both guesses were small.
    #
    # This is the check the plan asks for and it found a third: link2 poked
    # 0.7mm out of R_UPPER in 381 of 400 poses, measured against the capsule
    # AXIS rather than against a bounding box, which is why the box-derived
    # number missed it.
    rng2 = np.random.default_rng(0)
    worst, where, escaped = -1e9, "", 0
    for _ in range(400):
        j = tuple(rng2.uniform(-math.pi / 2, math.pi / 2) for _ in range(3))
        over, link = mesh_escapes_capsules(j, j3=rng2.uniform(0.0, 1.5))
        if over > worst:
            worst, where = over, link
        escaped += int(over > 0.0)
    print(f"\n  every STL vertex inside the capsules, 400 random poses:")
    print(f"    worst overhang {worst:+.2f}mm on {where}, {escaped} poses escape")
    assert escaped == 0, (
        f"{escaped}/400 poses put real geometry outside its capsule, worst "
        f"{worst:+.2f}mm on {where}. An undersized capsule is a collision "
        f"nothing will report.")

    print("OK")
