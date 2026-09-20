"""scrub3d/place_arms.py -- where should the arms go?

The partition scores any placement, so invert it: search placements and report
the best. This is the most direct demonstration that arm position is genuinely
an input rather than a baked-in assumption -- if it were baked in, there would
be nothing to search.

Practically it is also the cheapest way to avoid a bad rig. A layout that
reaches 68% of the body and loads two arms four times harder than the other two
is not obvious from looking at it, and discovering it after bolting everything
down is expensive.

METHOD
------
Coordinate descent from a ring. Full joint optimisation over four 6-DOF poses
is 24 dimensions and pointless here: the objective is dominated by each arm's
own reachability, arms interact mainly through territory overlap, and mounts
are placed by a human with a drill who wants round numbers. So: start from a
ring around the chair, then improve one arm at a time over a local grid,
keeping the others fixed. A few sweeps converge.

Scoring is deliberately multi-objective, because "reaches the most area" alone
produces a rig where one arm does everything:

    score = coverage
          - imbalance_penalty   (makespan is set by the slowest arm)
          - phase_penalty       (interlocked territories serialise the work)
"""
import math

import numpy as np

try:
    from .bodymodel import synthetic_body
    from . import partition as P
except ImportError:
    from bodymodel import synthetic_body
    import partition as P


def pose(x, y, z, yaw, pitch=0.0):
    """Base pose from the numbers a person can actually measure with a tape."""
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    Ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    T = np.eye(4)
    T[:3, :3] = Rz @ Ry
    T[:3, 3] = (x, y, z)
    return T


# Minimum centre-to-centre spacing between two bases, mm. The base column is
# ~90mm across before you add a mounting plate, clamps and the servo cable, and
# a person has to be able to get a spanner between them. Without this the
# search cheerfully returns two bases 91mm apart -- measured -- because nothing
# in the reachability objective knows that arms occupy space.
MIN_BASE_SEPARATION_MM = 320.0


def _too_close(layout):
    """-> the smallest centre-to-centre base gap, mm."""
    worst = float("inf")
    for i in range(len(layout)):
        for j in range(i + 1, len(layout)):
            worst = min(worst, float(np.linalg.norm(
                layout[i][:3, 3] - layout[j][:3, 3])))
    return worst


def camera_occlusion(body, layout, T_world_camera, terr=None):
    """What fraction of the body the arms hide from the camera. -> 0..1.

    THE CAMERA AND THE ARMS WANT THE SAME SPACE, and nothing in the objective
    knew it. Scrubbing is front-only, so the camera looks at the person's front;
    the arms reach 527mm and the skin sits about a metre out, so every base
    lands roughly 0.5 to 0.9m from the camera -- inside its cone, between it and
    the person it is watching. At 0.7m the frame is only 1.4m across, so one
    base is about 7% of it before any link moves.

    That matters beyond the picture. Live tracking re-registers the torso from
    depth, and a body the camera cannot see is a body whose pose goes stale
    while four arms move around it.

    A cell is hidden when the segment from camera to cell passes within a
    capsule's radius of that capsule -- the same segment-to-segment distance the
    collision layer already uses, so this adds no geometry and no rendering.

    Each arm is evaluated at the pose reaching its own territory's centroid
    where that is known, which is where it will actually spend its time.
    """
    try:
        from . import collide as C
        from . import kinematics as K
    except ImportError:
        import collide as C
        import kinematics as K

    P, _, _, _ = body.world_cells()
    if len(P) == 0 or T_world_camera is None:
        return 0.0
    cam = np.asarray(T_world_camera, float)[:3, 3]
    home = K.ik(235.11, 0.0, 234.79)

    hidden = np.zeros(len(P), bool)
    for a, T in enumerate(layout):
        j = None
        if terr is not None:
            own = terr.owner == a
            if own.sum() >= 8:
                local = (np.linalg.inv(np.asarray(T, float))
                         @ np.r_[P[own].mean(0), 1.0])[:3]
                j = K.ik(*local)
        j = j or home
        if j is None:
            continue
        caps = C.arm_capsules(T, *j)
        for k, (p, q) in enumerate(caps):
            if k == 3:                  # the sponge sits ON the person, so it
                continue                # hides nothing that is not itself
            d = C._seg_seg_dist(np.broadcast_to(cam, P.shape), P,
                                np.broadcast_to(np.asarray(p), P.shape),
                                np.broadcast_to(np.asarray(q), P.shape))
            hidden |= d < C.CAPSULE_RADII[k]
    return float(hidden.mean())


def score(body, layout, envelope_k=None, w_balance=0.45, w_phase=0.10,
          w_camera=0.25, T_world_camera=None, obstacle_points=None,
          obstacle_region=None):
    """-> (score, report). Higher is better. -1 for a physically unbuildable rig.

    `obstacle_points` matters more than it looks. Scoring a placement WITHOUT
    the body-clearance constraint optimises for a rig that can only reach the
    person by putting an elbow through them: measured, a layout scoring 89%
    coverage without the constraint drops to 14% once links must actually clear
    the body. Clearance has to be inside the objective, not checked afterwards.
    """
    gap = _too_close(layout)
    if gap < MIN_BASE_SEPARATION_MM:
        return -1.0, {"covered_frac": 0.0, "per_arm_cm2": [], "phases": [],
                      "classes": {}, "imbalance": 0.0, "reject": "bases too close",
                      "min_base_gap_mm": gap}

    if envelope_k is None:
        envelope_k = P.ENVELOPE_K
    terr, planes, phases, rep = P.solve(body, layout, envelope_k=envelope_k,
                                        obstacle_points=obstacle_points,
                                        obstacle_region=obstacle_region)
    rep["min_base_gap_mm"] = gap
    load = np.array(rep["per_arm_cm2"], float)
    if load.sum() <= 0:
        return -1.0, rep
    # Imbalance as max-over-mean: makespan is set by the busiest arm, so this
    # is the quantity that actually costs wall-clock time.
    imbalance = (load.max() / max(load.mean(), 1e-9)) - 1.0
    phase_cost = (len(phases) - 1) / max(len(layout) - 1, 1)
    occl = camera_occlusion(body, layout, T_world_camera, terr)         if T_world_camera is not None else 0.0
    s = (rep["covered_frac"] - w_balance * imbalance - w_phase * phase_cost
         - w_camera * occl)
    rep["score"] = float(s)
    rep["imbalance"] = float(imbalance)
    rep["camera_occluded"] = float(occl)
    return float(s), rep


def ring_layout(n, radius=520.0, height=760.0, front_bias=0.55, body=None):
    """A sane starting point: arms on an arc in front of the chair, facing it.

    front_bias < 1 squeezes the arc toward the front, because the person faces
    +X and we only scrub the front -- an arm behind them has nothing to do.

    `body` centres the ring on the PERSON. Without it the ring goes round the
    world origin, which was right only because every body was hardcoded to sit
    there. A scanned person sits where they sat, and a ring round the origin
    then puts four arms around a patch of empty floor.
    """
    cx = cy = 0.0
    if body is not None:
        P, _, _, _ = body.world_cells()
        if len(P):
            cx, cy = float(P[:, 0].mean()), float(P[:, 1].mean())
    out = []
    for k in range(n):
        frac = (k + 0.5) / n - 0.5
        ang = frac * math.pi * 2.0 * front_bias
        x, y = cx + radius * math.cos(ang), cy + radius * math.sin(ang)
        out.append(pose(x, y, height, math.atan2(cy - y, cx - x)))
    return out


def random_layout(body, n, rng, r_range=(360.0, 780.0), z_range=(520.0, 1120.0),
                  arc_deg=150.0, aim_jitter_deg=30.0):
    """One candidate rig, sampled around the person. -> layout.

    Bases go on a spherical shell around the body's own centroid rather than on
    a fixed ring at a fixed height, because "anywhere in 3D" is the actual
    requirement and a ring can only ever explore a circle of it. Height is
    sampled as widely as the radius: a low mount reaching up at a forearm
    resting on a thigh is a genuinely different rig from a high one reaching
    down, and only one of them is good.

    Each arm is aimed at the body with a jitter, because facing the person is
    right nearly always and exactly right almost never.
    """
    P, _, _, _ = body.world_cells()
    c = P.mean(0) if len(P) else np.zeros(3)
    out = []
    for _ in range(n):
        # Front arc only: the person faces +X and we scrub the front, so an arm
        # behind them has nothing to reach.
        ang = math.radians(rng.uniform(-arc_deg / 2, arc_deg / 2))
        r = rng.uniform(*r_range)
        x, y = c[0] + r * math.cos(ang), c[1] + r * math.sin(ang)
        z = rng.uniform(*z_range)
        yaw = math.atan2(c[1] - y, c[0] - x) + math.radians(
            rng.uniform(-aim_jitter_deg, aim_jitter_deg))
        out.append(pose(x, y, z, yaw))
    return out


def search(body, n_arms=4, n_random=900, keep=3, shortlist=20,
           sweeps=2, verbose=True, obstacle_points=None,
           obstacle_region=None, envelope_k=0, rank_k=4, refine_k=4,
           T_world_camera=None, seed=0):
    """Global sample, then local descent. -> (layout, report).

    WHY NOT JUST COORDINATE DESCENT FROM A RING
    --------------------------------------------
    Because a ring is one circle through a three-dimensional space of mounts,
    and descending from it can only reach what is downhill of that circle.
    Measured on the scanned body: the ring start scored 25.6% coverage against
    45.2% for the hand-placed layout it was supposed to beat, and no amount of
    local refinement from there was going to close that.

    So sample the space first, keep the best few, and refine those. The sample
    is cheap -- about 0.44s per candidate at envelope_k=0 on a coarse obstacle
    cloud -- and it is the difference between finding the best rig near a guess
    and finding the best rig.
    """
    rng = np.random.default_rng(seed)
    scored = []
    for i in range(n_random):
        lay = random_layout(body, n_arms, rng)
        if _too_close(lay) < MIN_BASE_SEPARATION_MM:
            continue
        s, r = score(body, lay, envelope_k=envelope_k,
                     obstacle_points=obstacle_points,
                     obstacle_region=obstacle_region,
                     T_world_camera=T_world_camera)
        if s > -0.5:
            scored.append((s, lay, r))
        if verbose and (i + 1) % 150 == 0:
            best = max(scored, key=lambda t: t[0]) if scored else None
            print(f"    sampled {i + 1}/{n_random}  kept {len(scored)}  "
                  f"best score {best[0]:+.3f} coverage "
                  f"{100 * best[2]['covered_frac']:.1f}%" if best else
                  f"    sampled {i + 1}/{n_random}  nothing viable yet")
    if not scored:
        raise RuntimeError("no viable layout found in the random phase")

    scored.sort(key=lambda t: -t[0])

    # RE-RANK THE SHORTLIST AGAINST THE POSE ENVELOPE before refining any of
    # them, and this stage is not optional.
    #
    # Sampling is done at envelope_k=0, one pose, because that is what makes
    # 900 candidates affordable. But a rig that is excellent for the exact
    # posture somebody was scanned in can be useless the moment they shift:
    # measured, the top sample scored 67.5% coverage at k=0 and 28.9% at k=1.
    # Ranking on the cheap score alone therefore selects FOR fragility -- it
    # rewards exactly the layouts that exploit one frozen pose.
    #
    # So the cheap score is used to shortlist, and the shortlist is re-scored
    # across the envelope. That costs `shortlist` evaluations instead of
    # `n_random` of them.
    short = scored[:max(shortlist, keep)]
    if verbose:
        print(f"    re-ranking the top {len(short)} across the pose envelope "
              f"(k={rank_k}), because a rig tuned to one posture is not a rig:")
    ranked = []
    for s0, lay, r0 in short:
        s1, r1 = score(body, lay, envelope_k=rank_k,
                       obstacle_points=obstacle_points,
                       obstacle_region=obstacle_region,
                       T_world_camera=T_world_camera)
        ranked.append((s1, lay, r1, s0, r0))
    ranked.sort(key=lambda t: -t[0])
    # rank_k and refine_k must MATCH. min_feas is a fraction of the envelope,
    # so the achievable fractions change with its size and a rig ranked at one
    # k cannot be compared with itself refined at another. See partition.solve.
    assert rank_k == refine_k, (
        f"rank_k={rank_k} and refine_k={refine_k} differ; coverage is not "
        f"comparable across envelope sizes")
    if verbose:
        for s1, _l, r1, s0, r0 in ranked[:keep]:
            print(f"      {100 * r0['covered_frac']:5.1f}% at one pose -> "
                  f"{100 * r1['covered_frac']:5.1f}% across the envelope   "
                  f"imbalance {r1['imbalance']:.2f}  camera-blocked "
                  f"{100 * r1.get('camera_occluded', 0):.0f}%")
    scored = [(s1, lay, r1) for s1, lay, r1, _s0, _r0 in ranked]

    best_s, best_lay, best_rep = -9.9, None, None
    for k, (_s, lay, _r) in enumerate(scored[:keep]):
        if verbose:
            print(f"    refining candidate {k}:")
        lay2, rep2 = optimise(body, n_arms=n_arms, sweeps=sweeps,
                              verbose=verbose,
                              obstacle_points=obstacle_points,
                              obstacle_region=obstacle_region,
                              envelope_k=refine_k,
                              T_world_camera=T_world_camera, start=lay)
        s2, r2 = score(body, lay2, envelope_k=refine_k,
                       obstacle_points=obstacle_points,
                       obstacle_region=obstacle_region,
                       T_world_camera=T_world_camera)
        if s2 > best_s:
            best_s, best_lay, best_rep = s2, lay2, r2

    # Refinement can only improve on what it started from, but it is still a
    # search and searches can come back empty-handed. Returning None here would
    # surface as an unrelated crash a long way downstream; falling back to the
    # best rig the sampling found is both correct and what the caller wanted.
    if best_lay is None:
        best_s, best_lay, best_rep = scored[0]
        if verbose:
            print("    refinement improved on nothing; keeping the best "
                  "sampled rig")
    return best_lay, best_rep


def optimise(body, n_arms=4, sweeps=2, verbose=True, obstacle_points=None,
             obstacle_region=None, envelope_k=1, T_world_camera=None,
             start=None):
    """Coordinate descent over base poses. -> (layout, report)."""
    layout = list(start) if start is not None else ring_layout(n_arms, body=body)
    best, rep = score(body, layout, envelope_k=envelope_k,
                      T_world_camera=T_world_camera,
                      obstacle_points=obstacle_points,
                      obstacle_region=obstacle_region)
    if verbose:
        print(f"    start: score {best:+.3f}  "
              f"coverage {100 * rep['covered_frac']:.1f}%  "
              f"imbalance {rep['imbalance']:.2f}  phases {len(rep['phases'])}")

    dx = (-120.0, 0.0, 120.0)
    dy = (-140.0, 0.0, 140.0)
    dz = (-120.0, 0.0, 120.0)
    dyaw = (math.radians(-25), 0.0, math.radians(25))

    for sweep in range(sweeps):
        for a in range(n_arms):
            base = layout[a]
            x0, y0, z0 = base[:3, 3]
            yaw0 = math.atan2(base[1, 0], base[0, 0])
            found = None
            for ddx in dx:
                for ddy in dy:
                    for ddz in dz:
                        for dda in dyaw:
                            if (ddx, ddy, ddz, dda) == (0.0, 0.0, 0.0, 0.0):
                                continue
                            trial = list(layout)
                            trial[a] = pose(x0 + ddx, y0 + ddy, z0 + ddz,
                                            yaw0 + dda)
                            s, r = score(body, trial, envelope_k=envelope_k,
                                         T_world_camera=T_world_camera,
                                         obstacle_points=obstacle_points,
                                         obstacle_region=obstacle_region)
                            if s > best + 1e-4:
                                best, found, rep = s, trial, r
            if found is not None:
                layout = found
                if verbose:
                    print(f"  sweep {sweep} arm {a}: score {best:+.3f}  "
                          f"coverage {100 * rep['covered_frac']:.1f}%  "
                          f"imbalance {rep['imbalance']:.2f}  "
                          f"phases {len(rep['phases'])}")
    return layout, rep


def describe(layout):
    """Print a layout the way someone holding a tape measure needs it."""
    print("  mount the bases at (millimetres from the chair centre, "
          "facing angle in degrees):")
    for i, T in enumerate(layout):
        x, y, z = T[:3, 3]
        yaw = math.degrees(math.atan2(T[1, 0], T[0, 0]))
        print(f"    arm {i}:  x={x:+7.0f}  y={y:+7.0f}  z={z:+6.0f}  "
              f"facing {yaw:+7.1f} deg")


if __name__ == "__main__":
    # Search against the ANATOMICAL body with link clearance enforced. Both
    # matter: a rig optimised against bare cylinders with no clearance test
    # scores 89% and then delivers 14% once the arms have to avoid the person.
    from anatomy import anatomical_body, world_meshes
    body, meshes = anatomical_body()
    obstacles = np.vstack([V for V, F in world_meshes(body, meshes).values()])

    print("placement search (anatomical body, 4 arms, link clearance ON)")
    print(f"  obstacle cloud: {len(obstacles)} vertices, "
          f"clearance threshold {__import__('collide').D_BODY:.0f}mm\n")
    layout, rep = optimise(body, obstacle_points=obstacles, envelope_k=1)
    print()
    describe(layout)

    gap = _too_close(layout)
    print(f"\n  closest two bases: {gap:.0f}mm "
          f"(minimum buildable {MIN_BASE_SEPARATION_MM:.0f}mm)")
    assert gap >= MIN_BASE_SEPARATION_MM, "search returned an unbuildable rig"

    print("\n  verifying the winner at full envelope fidelity:")
    terr, planes, phases, full = P.solve(body, layout, envelope_k=8)
    print(f"    coverage {100 * full['covered_frac']:.1f}%  "
          f"per-arm cm2 {['%.0f' % v for v in full['per_arm_cm2']]}")
    print(f"    classes {full['classes']}")
    print(f"    phases {phases}")
    sep = sum(1 for p in planes.values() if p is not None)
    print(f"    {sep}/{len(planes)} arm pairs cleanly separable")
    print("OK")
