"""scrub3d/partition.py -- divide and conquer, for arms placed anywhere.

THE CLAIM THIS MODULE HAS TO EARN
----------------------------------
Bolt the arms down wherever you like. Give the software each base pose. It
works out who scrubs what. Move an arm and it works it out again. Nothing here
contains a hardcoded arm position, a hand-drawn zone, or a left/right
assumption; every output is a function of the base poses and the body.

PIPELINE
--------
  1. feasibility  -- for each surface cell and each arm, can that arm reach it,
                     evaluated across a RANGE of body poses rather than one.
  2. partition    -- smooth the raw argmax into contiguous territories.
  3. classify     -- core / fringe / contested / unreachable.
  4. balance      -- even out the workload.
  5. separate     -- derive a dividing plane per adjacent pair.
  6. schedule     -- colour the conflict graph into concurrent phases.

WHY A POSE ENVELOPE AND NOT ONE POSE
-------------------------------------
A partition computed for one frozen pose is invalidated the moment the person
leans. But recomputing it continuously is worse, not better: territories
shifting underneath arms that are already in flight is precisely how two arms
end up in the same place. The separating planes in step 5 would be moving while
an arm relies on them.

So the partition is made ROBUST rather than reactive. Each cell is tested
across the scan pose plus perturbations, and a cell only joins a territory if
its arm can still reach it when the person moves. Residual drift is handled by
an explicit, bounded handoff at runtime (scrub3d/adapt.py), never by silently
re-solving underneath a moving arm.

WHY SMOOTHING MATTERS
---------------------
Raw per-cell argmax produces speckled, interleaved ownership: arm 1 owns a cell,
arm 3 owns its neighbour. That is the worst possible input to a collision
system, because the arms interdigitate and no dividing surface exists. Smoothing
buys large territories with SHORT BOUNDARIES, which shrinks the collision
problem from "any arm can be anywhere" to "what happens at the seams".

The literature answer is an alpha-expansion graph cut (PyMaxflow). Iterated
conditional modes is used here instead: same objective, no dependency, and at
this problem size it converges in a handful of sweeps. Swap it if the
boundaries ever look ragged.
"""
import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

try:
    from . import kinematics as K
    from .bodymodel import repose
except ImportError:
    import kinematics as K
    from bodymodel import repose

CORE, FRINGE, CONTESTED, UNREACHABLE = "core", "fringe", "contested", "unreachable"


@dataclass
class Territory:
    owner: np.ndarray          # (N,) int arm index, -1 = nobody
    alt: np.ndarray            # (N,) int pre-computed handoff target, -1 = none
    klass: np.ndarray          # (N,) object, one of the constants above
    feas: np.ndarray           # (N, n_arms) fraction of envelope poses feasible
    qual: np.ndarray           # (N, n_arms) mean quality where feasible
    pts: np.ndarray            # (N, 3) world cell centres, scan pose
    area: np.ndarray           # (N,) mm^2


def _axis_rot(axis, ang):
    """Rotation about a unit axis. 3x3."""
    x, y, z = axis
    c, s, t = math.cos(ang), math.sin(ang), 1.0 - math.cos(ang)
    return np.array([
        [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
        [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
        [t * x * z - s * y, t * y * z + s * x, t * z * z + c]])


# Measured, not invented. `pose_forward` and `pose_lean` are the same person in
# two natural postures: their body centre moves 51mm and their torso yaws 9.5
# degrees between the two. See the plan, Part 0.
LEAN_MM = 55.0
TWIST_RAD = math.radians(11.0)
LIMB_RAD = math.radians(14.0)


def pose_envelope(body, k=8, lean_mm=LEAN_MM, twist=TWIST_RAD, limb=LIMB_RAD,
                  seed=0, extra=None):
    """Scan pose plus k perturbations. -> [BodyModel].

    A cell that survives the whole envelope is one we can promise, so the
    envelope has to resemble how a person actually moves. The previous version
    did not, in three separate ways, and each made that promise weaker than it
    read.

    THE TORSO NEVER MOVED. This skipped any region whose `scrubbable.any()` was
    false, and both body builders mark the torso unscrubbable, so the one
    region that leaning and turning actually moves was excluded. The docstring
    said it stood in for "the person leaning, turning" and it structurally
    could not. `scrubbable` says what may be SCRUBBED, not what may MOVE.

    THE BODY CAME APART. Every region was perturbed independently with no
    kinematic chain, so a forearm could rotate one way while its upper arm went
    the other and the elbow separated. A body is now moved as a body -- one
    rigid transform about the seat for lean and yaw -- and per-limb rotation
    goes on top of that, about each limb's own origin, which is what a shoulder
    and an elbow do.

    AND IT WAS ABOUT THREE TIMES TOO SMALL. Translation was `lean * 60.0` with
    lean=0.10, i.e. 6mm of sigma, against 51mm measured between two real
    postures. `lean` was not even an angle despite its name.

    `extra` takes bodies measured from other captures, so the two real postures
    on disk can be envelope members rather than something a random draw is
    hoped to resemble. That is the difference between testing against a model
    of movement and testing against movement.
    """
    rng = np.random.default_rng(seed)
    out = [body] + list(extra or [])

    # Lean and yaw pivot about the seat, not the world origin: a seated person
    # rotates about their hips. Lowest cell is close enough to it.
    allp = np.vstack([r.world()[0] for r in body.regions])
    pivot = np.array([0.0, 0.0, float(allp[:, 2].min())])

    for _ in range(k):
        # --- one rigid motion for the whole body ---------------------------
        yaw = _axis_rot((0.0, 0.0, 1.0), rng.uniform(-1, 1) * twist)
        lean_axis = rng.normal(size=2)
        lean_axis = np.array([lean_axis[0], lean_axis[1], 0.0])
        lean_axis /= np.linalg.norm(lean_axis) + 1e-12
        tilt = _axis_rot(lean_axis, rng.uniform(-1, 1) * twist)
        R_body = tilt @ yaw
        shift = rng.normal(scale=lean_mm / 1.732, size=3)   # ~lean_mm in norm

        tf = {}
        for r in _chain_order(body):
            T = r.T.copy()
            T[:3, :3] = R_body @ T[:3, :3]
            T[:3, 3] = R_body @ (T[:3, 3] - pivot) + pivot + shift

            # --- then the limb moves at its own joint ----------------------
            if r.scrubbable.any():
                ax = rng.normal(size=3)
                ax /= np.linalg.norm(ax) + 1e-12
                R_limb = _axis_rot(ax, rng.uniform(-1, 1) * limb)
                T[:3, :3] = R_limb @ T[:3, :3]

            # --- and carry the children with it --------------------------
            #
            # Rotating an upper arm about the shoulder moves the ELBOW, and a
            # forearm placed independently does not follow it. Measured, that
            # left the elbow open by up to 61mm across the envelope: the body
            # stayed put but the arms came apart.
            #
            # The fix needs no lengths or joint definitions. The forearm's pose
            # in the upper arm's frame is a constant of the body, so capture it
            # before the perturbation and re-apply it after.
            parent = PARENT.get(r.name)
            if parent is not None and parent in tf:
                p0 = dict((x.name, x.T) for x in body.regions)[parent]
                T = tf[parent] @ (np.linalg.inv(p0) @ r.T)
            tf[r.name] = T
        out.append(repose(body, **tf))
    return out


# Which region hangs off which. Only the elbow matters today: shoulders ride
# the trunk, which the rigid body transform already moves.
PARENT = {"forearm_L": "upper_arm_L", "forearm_R": "upper_arm_R"}


def _chain_order(body):
    """Regions with every parent before its children."""
    done, out = set(), []
    remaining = list(body.regions)
    while remaining:
        progressed = False
        for r in list(remaining):
            p = PARENT.get(r.name)
            if p is None or p in done or p not in {x.name for x in body.regions}:
                out.append(r)
                done.add(r.name)
                remaining.remove(r)
                progressed = True
        if not progressed:                 # a cycle; take what is left as-is
            out.extend(remaining)
            break
    return out


def _corridor_clear(pts, nrm, obstacles_tree, standoff=110.0, clearance=55.0):
    """Can the tool get to each cell along its own normal without fouling?

    A good surface normal does not mean the arm can reach it: the approach may
    pass through the person's own body. This is the filter that catches armpits
    and inner-arm surfaces, and it is easy to leave out.
    """
    probe = pts + nrm * standoff
    d, _ = obstacles_tree.query(probe)
    return d > clearance


_OBS_CACHE = {}


def _obs_trees(points, region=None):
    """Obstacle trees, one per scrubbable region plus a default. -> dict.

    THE PART BEING SCRUBBED CANNOT BE AN OBSTACLE TO THE ARM SCRUBBING IT, and
    that is geometry, not policy. The sponge has radius 40 and sits at the tool
    point; the forearm capsule runs to that same tool point with radius 30. So
    when the sponge is touching skin, the forearm capsule's surface is 10mm
    from that skin -- against a D_BODY of 60. No placement fixes it, because
    the tool is on the end of the thing that has to clear.

    Both extremes are wrong. Excluding every limb from the obstacle cloud left
    nothing to stop one arm driving an elbow through the limb another arm was
    working on. Including every limb dropped coverage to 25.4%, by forbidding
    each arm from approaching its own target.

    So the exemption is per-region: an arm working on a cell of limb L clears
    everything except L. Every other limb, the trunk, the head, the lap and the
    chair remain obstacles to it.

    Cached on the array's identity because feasibility is called once per
    envelope pose while the obstacles -- a static scan of the room -- do not
    change between them.
    """
    # Keyed on the array OBJECT, held by reference. id() alone is not a safe
    # cache key: CPython reuses an address once the object at it is collected,
    # so a later array could land on the same id and silently collect the wrong
    # trees -- an obstacle set from a different scan, producing clearances that
    # are confidently wrong. Holding the reference makes that reuse impossible.
    prev_pts, prev_out = _OBS_CACHE.get("entry", (None, None))
    if prev_pts is points:
        return prev_out
    out = {None: cKDTree(points)}
    if region is not None:
        region = np.asarray(region)
        for r in np.unique(region):
            if r < 0:
                continue
            keep = region != r
            if keep.sum() > 16:
                out[int(r)] = cKDTree(points[keep])
            if (~keep).sum() > 16:
                out[("own", int(r))] = cKDTree(points[~keep])
    _OBS_CACHE.clear()
    _OBS_CACHE["entry"] = (points, out)
    return out


def _body_clear(base_pose, joints_list, valid_idx, tree, radii, d_body,
                samples=12, moving=False):
    """Do the arm's STRUCTURAL links clear the body, for each candidate pose?

    -> boolean array over valid_idx. moving=True tests only what a move can
    change (collide.MOVING): the mount is the placement's business.

    REACHABILITY IS NOT CLEARANCE, and conflating them is a safety bug rather
    than an inaccuracy. Measured on the real anatomical mesh with every
    reachability test passing: all four arms drove their structural links 28mm
    INTO the body. The tool point was always fine. The elbow was not.

    The sponge is excluded -- it is the one part meant to be in contact.

    Batched deliberately: every capsule sample for every candidate cell goes
    into ONE tree query, because the per-cell version is slow enough that it
    would not survive being run inside the placement search, and a check that
    gets switched off for speed is not a check.
    """
    try:
        from . import collide as C
    except ImportError:
        import collide as C

    # STRUCTURAL, from collide, rather than range(3). That literal meant base,
    # upper arm and forearm, which was every structural capsule there WAS when
    # it was written. The EoAT jaw became a fifth capsule and this check never
    # saw it -- so the one piece of metal that sweeps 86 degrees and overhangs
    # the sponge by 47mm was excluded from the body-clearance test by an index
    # that used to be correct.
    idx = list(C.MOVING if moving else C.STRUCTURAL)
    r_struct = np.asarray([radii[c] for c in idx])
    t = np.linspace(0.0, 1.0, samples)[:, None]
    if moving and len(joints_list):
        # All poses at once: the same capsules, built in numpy.
        caps = C.moving_capsules_many(base_pose, np.asarray(joints_list, float)[:, :3])
        allpts = caps[:, :, :1, :] + (caps[:, :, 1:, :] - caps[:, :, :1, :]) * t[None, None]
    else:
        allpts = np.empty((len(valid_idx), len(idx), samples, 3))
    for k, j in enumerate(joints_list if not moving else ()):
        caps = C.arm_capsules(base_pose, *j)[idx]
        for slot in range(len(idx)):
            p, q = caps[slot]
            allpts[k, slot] = p + (q - p) * t

    d, _ = tree.query(allpts.reshape(-1, 3))
    d = d.reshape(len(valid_idx), len(idx), samples)
    clearance = (d - r_struct[None, :, None]).min(axis=(1, 2))
    return clearance > d_body


def feasibility(body, base_poses, envelope=None, standoff=110.0,
                obstacle_points=None, d_body=None, obstacle_region=None,
                moving=False, corridor=None, d_limb=None):
    """-> (feas, qual, pts, area). The core of placement independence.

    `standoff` is where the tool point works, out along the normal;
    `corridor` = (probe_mm, clearance_mm) is the free space an approach
    needs, (standoff, 55) unless given. `d_limb`: the limb being scrubbed
    is still cleared by this much, instead of being left out altogether.

    feas[i, a] is the fraction of envelope poses in which arm `a` can reach
    cell `i` AND get there without putting a link through the person. qual[i,a]
    is its mean quality over those poses. Both are functions of base_poses and
    nothing else.

    `obstacle_points` should be the FULL body surface including the back, not
    just the scrubbable front arc -- an arm reaching around a limb can foul on
    surface the camera never sees. Pass anatomy.world_meshes() vertices.
    """
    try:
        from . import collide as C
    except ImportError:
        import collide as C
    d_body = C.D_BODY if d_body is None else d_body

    envelope = envelope or [body]
    n_arms = len(base_poses)
    inv = [np.linalg.inv(np.asarray(T, float)) for T in base_poses]

    P0, N0, I0, A0 = body.world_cells()
    n_cells = len(P0)
    # Which REGION each cell belongs to, as an index into body.regions. Needed
    # so an arm working on a limb can be exempted from clearing that one limb
    # and no other; see _obs_trees.
    reg_of_cell = np.asarray(I0)
    feas = np.zeros((n_cells, n_arms))
    qual = np.zeros((n_cells, n_arms))

    for pose in envelope:
        P, N, _, _ = pose.world_cells()
        allP, _, _, _ = pose.world_cells(only_scrubbable=False)
        tree = cKDTree(allP)
        probe, room = corridor if corridor is not None else (standoff, 55.0)
        clear = _corridor_clear(P, N, tree, probe, room)
        obs_tree = tree if obstacle_points is None else _obs_trees(
            obstacle_points, obstacle_region)

        for a in range(n_arms):
            # Target the tool point a standoff back along the normal: that is
            # where the arm has to put its wrist for the sponge to land here.
            tgt = P + N * standoff
            # errstate for Accelerate's spurious matmul FPE flags, not for a
            # real one -- see bodymodel.Region.world. This shape is (N,4)@(4,4)
            # and is flagged too, which is why the fix cannot be a rewrite of
            # the expression: the bug follows the BLAS, not the operand order.
            with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
                h = np.c_[tgt, np.ones(len(tgt))] @ inv[a].T  # -> arm base frame

            cand, joints, margins = [], [], []
            for i in range(n_cells):
                if not clear[i]:
                    continue
                m = K.reach_margin(h[i, 0], h[i, 1], h[i, 2])
                if m <= 0.0:
                    continue
                j = K.ik(h[i, 0], h[i, 1], h[i, 2])
                if j is None:
                    continue
                cand.append(i); joints.append(j); margins.append(m)

            if not cand:
                continue

            # Group the candidates by the region they sit on, so each group is
            # tested against an obstacle set with its OWN limb removed and
            # every other body part left in. One tree query per region rather
            # than per cell keeps the batching that makes this affordable.
            cand = np.asarray(cand)
            joints = list(joints)
            margins = np.asarray(margins)
            if isinstance(obs_tree, dict):
                groups = {}
                for k, i in enumerate(cand):
                    groups.setdefault(int(reg_of_cell[i]), []).append(k)
            else:
                groups = {None: list(range(len(cand)))}

            for rid, ks in groups.items():
                t = (obs_tree.get(rid, obs_tree[None])
                     if isinstance(obs_tree, dict) else obs_tree)
                sub = [joints[k] for k in ks]
                ok = _body_clear(np.asarray(base_poses[a], float), sub,
                                 list(range(len(ks))), t, C.CAPSULE_RADII,
                                 d_body, moving=moving)
                own_t = (obs_tree.get(("own", rid))
                         if d_limb is not None and isinstance(obs_tree, dict) else None)
                if own_t is not None:
                    ok = ok & _body_clear(np.asarray(base_poses[a], float), sub,
                                          list(range(len(ks))), own_t, C.CAPSULE_RADII,
                                          d_limb, moving=moving)
                for kk, k in enumerate(ks):
                    if ok[kk]:
                        i = int(cand[k])
                        feas[i, a] += 1.0
                        qual[i, a] += margins[k]
    with np.errstate(invalid="ignore", divide="ignore"):
        qual = np.where(feas > 0, qual / np.maximum(feas, 1e-9), 0.0)
    feas /= len(envelope)
    # Normalise quality to 0..1 so the cost function is scale free.
    if qual.max() > 0:
        qual = qual / qual.max()
    return feas, qual, P0, A0


def partition(feas, qual, pts, area, min_feas=0.6, smooth=0.9, iters=12):
    """Assign cells to arms. -> Territory.

    Objective: minimise sum over cells of (1 - quality of owner) plus `smooth`
    times the number of neighbouring cells with a different owner. The second
    term is what produces contiguous territories instead of speckle.
    """
    n_cells, n_arms = feas.shape
    usable = feas >= min_feas

    # Raw best-arm, then smooth it.
    score = np.where(usable, qual, -np.inf)
    owner = np.where(usable.any(1), score.argmax(1), -1)

    tree = cKDTree(pts)
    _, nb = tree.query(pts, k=min(7, n_cells))
    nb = nb[:, 1:]

    for _ in range(iters):
        changed = 0
        for i in range(n_cells):
            if not usable[i].any():
                continue
            cand = np.where(usable[i])[0]
            neigh = owner[nb[i]]
            cost = [(1.0 - qual[i, a]) + smooth * np.sum(neigh != a)
                    for a in cand]
            best = cand[int(np.argmin(cost))]
            if best != owner[i]:
                owner[i] = best
                changed += 1
        if changed == 0:
            break

    owner = _enforce_contiguity(owner, nb, usable, qual)

    # Classify. Contested cells carry a pre-computed alternate owner so a
    # runtime handoff is a lookup, not a re-solve.
    klass = np.empty(n_cells, dtype=object)
    alt = np.full(n_cells, -1)
    for i in range(n_cells):
        if owner[i] < 0:
            klass[i] = UNREACHABLE
            continue
        others = [a for a in range(n_arms)
                  if a != owner[i] and feas[i, a] >= min_feas]
        if others:
            klass[i] = CONTESTED
            alt[i] = max(others, key=lambda a: qual[i, a])
        elif feas[i, owner[i]] >= 0.9:
            klass[i] = CORE
        else:
            klass[i] = FRINGE
    return Territory(owner, alt, klass, feas, qual, pts, area)


def _enforce_contiguity(owner, nb, usable, qual, min_frac=0.12):
    """Each arm keeps ONE connected territory. Strays are handed to their next
    best arm, or dropped.

    Smoothing alone does not guarantee this. Measured on a four-arm layout it
    left arm 0 owning eleven cells on the far side of the body and arm 1 owning
    ten on the near side -- a handful of cells each, but enough to make the two
    territories inseparable by any plane, which forced the scheduler to run
    both arms in separate phases and roughly halved throughput.

    Contiguity is not cosmetic. It is the property the separating-plane step
    depends on, and a cross-body stray is also the worst case for collision
    exposure, so dropping it is right twice over.

    Components smaller than min_frac of an arm's total are reassigned even if
    the arm has nothing better to do: a detached island is not worth the
    traverse, and it is exactly the kind of cell a runtime handoff handles.
    """
    owner = owner.copy()
    n_arms = usable.shape[1]
    for a in range(n_arms):
        idx = np.where(owner == a)[0]
        if len(idx) == 0:
            continue
        member = np.zeros(len(owner), bool)
        member[idx] = True

        # Connected components over the same kNN adjacency the smoother used.
        comp = np.full(len(owner), -1)
        cid = 0
        for seed in idx:
            if comp[seed] >= 0:
                continue
            stack, comp[seed] = [seed], cid
            while stack:
                u = stack.pop()
                for v in nb[u]:
                    if member[v] and comp[v] < 0:
                        comp[v] = cid
                        stack.append(v)
            cid += 1
        if cid <= 1:
            continue

        sizes = np.array([(comp[idx] == c).sum() for c in range(cid)])
        keep = int(sizes.argmax())
        for c in range(cid):
            if c == keep or sizes[c] >= min_frac * sizes.sum():
                continue
            stray = idx[comp[idx] == c]
            for i in stray:
                cand = [b for b in range(n_arms) if b != a and usable[i, b]]
                owner[i] = (max(cand, key=lambda b: qual[i, b])
                            if cand else -1)
    return owner


def balance(terr, min_feas=0.6, tol=0.18, iters=6):
    """Even the workload by nudging contested cells toward idle arms.

    Minimum-makespan scheduling on unrelated parallel machines, approximately.
    Only CONTESTED cells move, so a cell only one arm can reach is never taken
    from it.
    """
    n_arms = terr.feas.shape[1]
    for _ in range(iters):
        load = np.array([terr.area[terr.owner == a].sum() for a in range(n_arms)])
        if load.sum() <= 0:
            break
        spread = (load.max() - load.min()) / load.mean()
        if spread < tol:
            break
        busy, idle = int(load.argmax()), int(load.argmin())
        movable = np.where((terr.owner == busy) & (terr.klass == CONTESTED)
                           & (terr.feas[:, idle] >= min_feas))[0]
        if len(movable) == 0:
            break
        # Move the cells the busy arm values least.
        order = movable[np.argsort(terr.qual[movable, busy])]
        terr.owner[order[:max(1, len(order) // 3)]] = idle
    return terr


def separating_planes(terr, margin=25.0):
    """Derive a dividing plane per adjacent arm pair. -> {(i,j): (n, d) or None}

    None means the territories interlock and no plane separates them; that pair
    goes to the scheduler to be run in different phases instead.

    A plane, not a box: a per-arm axis-aligned box in each arm's own rotated
    frame cannot express a shared world-frame dividing surface. And note this
    constrains the TOOL POINT only, so it never removes the need for the
    runtime capsule check.
    """
    n_arms = terr.feas.shape[1]
    out = {}
    for i in range(n_arms):
        for j in range(i + 1, n_arms):
            A = terr.pts[terr.owner == i]
            B = terr.pts[terr.owner == j]
            if len(A) < 3 or len(B) < 3:
                continue
            out[(i, j)] = _fit_plane(A, B, margin)
    return out


def _fit_plane(A, B, margin, overlap_tol=0.03):
    """Best separating plane between two point sets, or None.

    Uses Fisher's linear discriminant for the direction rather than the
    difference of centroids. On a curved body the centroid axis is a poor
    separator -- two arms working opposite sides of a torso have territories
    that overlap heavily when projected onto the line joining them, so a
    centroid test reports "interlocked" for pairs that are in fact cleanly
    divisible. Fisher maximises between-class over within-class scatter and
    finds the direction that actually separates them.

    A small overlap is tolerated: demanding zero is unachievable on real
    geometry, and the few cells on the wrong side are simply excluded from the
    owning arm rather than invalidating the whole plane.
    """
    mu_a, mu_b = A.mean(0), B.mean(0)
    Sw = np.cov(A.T) * len(A) + np.cov(B.T) * len(B)
    Sw += np.eye(3) * (np.trace(Sw) / 3.0) * 1e-3     # regularise
    try:
        w = np.linalg.solve(Sw, mu_b - mu_a)
    except np.linalg.LinAlgError:
        w = mu_b - mu_a
    L = np.linalg.norm(w)
    if L < 1e-12:
        return None
    w /= L
    # Accelerate flags (N,3)@(3,) as well, so these projections warn on a
    # perfectly good separating direction. See bodymodel.Region.world.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        if (B @ w).mean() < (A @ w).mean():
            w = -w

        pa, pb = A @ w, B @ w
    # Allow overlap_tol of each side to sit on the wrong side of the cut.
    a_hi = np.quantile(pa, 1.0 - overlap_tol)
    b_lo = np.quantile(pb, overlap_tol)
    if b_lo - a_hi < margin:
        return None                                    # genuinely interlocked
    return (w, float(0.5 * (a_hi + b_lo)))


def schedule(planes, n_arms):
    """Colour the conflict graph. -> [[arm indices]] one list per phase.

    Arms whose territories are cleanly separated may run together. Arms that
    interlock are put in different phases. Two-at-a-time is therefore a
    SCHEDULING OUTCOME here, not a configuration flag.
    """
    conflict = {a: set() for a in range(n_arms)}
    for (i, j), p in planes.items():
        if p is None:
            conflict[i].add(j)
            conflict[j].add(i)
    colour = {}
    for a in sorted(range(n_arms), key=lambda x: -len(conflict[x])):
        used = {colour[b] for b in conflict[a] if b in colour}
        c = 0
        while c in used:
            c += 1
        colour[a] = c
    phases = {}
    for a, c in colour.items():
        phases.setdefault(c, []).append(a)
    return [sorted(v) for _, v in sorted(phases.items())]


# MIN_FEAS IS A FRACTION OF THE ENVELOPE, AND SMALL ENVELOPES QUANTISE IT
# BADLY. With envelope_k=1 there are 2 poses, so 0.6 demands BOTH; with
# envelope_k=2 there are 3 and two suffice. So a SMALLER envelope is STRICTER,
# which is the opposite of what anyone reading the parameter expects.
#
# Measured, that produced a placement search whose candidates were ranked at
# k=2 (56.5% coverage) and then refined at k=1, where the same rig scored 22.0%
# and the refinement spent its time climbing out of a hole the change of k had
# dug. Coverage numbers are only comparable at equal k.
#
# Keep envelope_k >= 4 wherever a number is going to be compared with another,
# and never compare across different k.
#
# So it is ONE NUMBER, named here, rather than a default repeated at each call
# site. Four call sites had drifted to k=2 while the placement search ran at
# k=4, which meant the coverage main.py printed was not the coverage the rig
# had been chosen for -- the comparison the comment above forbids, made by
# accident rather than on purpose.
ENVELOPE_K = 4


def solve(body, base_poses, envelope_k=8, min_feas=0.6,
          obstacle_points=None, obstacle_region=None, moving=False,
          d_body=None, standoff=110.0, corridor=None, d_limb=None):
    """The whole pipeline. -> (Territory, planes, phases, report).

    Pass `obstacle_points` (the full body mesh, back included) so the link
    clearance test is against real geometry. Without it the test falls back to
    the scrubbable front cells, which is better than nothing but will miss an
    arm fouling on surface behind a limb. `moving`: see _body_clear.
    `d_body`, `standoff` and `corridor` go to feasibility().
    """
    env = pose_envelope(body, k=envelope_k)
    feas, qual, pts, area = feasibility(body, base_poses, env,
                                        standoff=standoff,
                                        obstacle_points=obstacle_points,
                                        obstacle_region=obstacle_region,
                                        moving=moving, d_body=d_body,
                                        corridor=corridor, d_limb=d_limb)
    terr = balance(partition(feas, qual, pts, area, min_feas), min_feas)
    planes = separating_planes(terr)
    phases = schedule(planes, len(base_poses))

    total = area.sum()
    covered = area[terr.owner >= 0].sum()
    report = {
        "cells": len(pts),
        "covered_frac": float(covered / total) if total else 0.0,
        "per_arm_cm2": [float(area[terr.owner == a].sum() / 100.0)
                        for a in range(len(base_poses))],
        "classes": {c: int((terr.klass == c).sum())
                    for c in (CORE, FRINGE, CONTESTED, UNREACHABLE)},
        "phases": phases,
    }
    return terr, planes, phases, report


if __name__ == "__main__":
    from bodymodel import synthetic_body

    def T(x, y, z, yaw):
        c, s = math.cos(yaw), math.sin(yaw)
        return np.array([[c, -s, 0, x], [s, c, 0, y],
                         [0, 0, 1, z], [0, 0, 0, 1]], float)

    body = synthetic_body()

    # Four arms placed around a seated person. Deliberately NOT symmetric and
    # not hand-tuned -- if the result only works for a tidy layout, the claim
    # is false.
    layout_a = [
        T(430, 330, 700, math.radians(-150)),
        T(455, -300, 700, math.radians(150)),
        T(250, 520, 880, math.radians(-115)),
        T(260, -540, 880, math.radians(115)),
    ]

    print("=== layout A ===")
    terr, planes, phases, rep = solve(body, layout_a)
    print(f"  cells {rep['cells']}, reachable {100 * rep['covered_frac']:.1f}%")
    print(f"  per-arm load cm2: "
          f"{['%.0f' % v for v in rep['per_arm_cm2']]}")
    print(f"  classes: {rep['classes']}")
    for (i, j), p in sorted(planes.items()):
        print(f"  pair {i}-{j}: "
              f"{'interlocked' if p is None else 'separable, n=(%.2f,%.2f,%.2f)' % tuple(p[0])}")
    print(f"  phases (arms that may run together): {phases}")

    # THE ACTUAL TEST: move an arm and confirm the division changes coherently
    # rather than breaking. Nothing is re-tuned between these runs.
    print("\n=== layout B: arm 0 moved 300mm and rotated 40 degrees ===")
    layout_b = list(layout_a)
    layout_b[0] = T(430, 30, 700, math.radians(-110))
    terr_b, planes_b, phases_b, rep_b = solve(body, layout_b)
    print(f"  reachable {100 * rep_b['covered_frac']:.1f}%")
    print(f"  per-arm load cm2: "
          f"{['%.0f' % v for v in rep_b['per_arm_cm2']]}")
    moved = int((terr.owner != terr_b.owner).sum())
    print(f"  {moved} of {rep['cells']} cells changed owner "
          f"({100.0 * moved / rep['cells']:.0f}%)")
    assert moved > 0, "moving an arm changed nothing -- placement is not an input"

    # And a different body, same arms, no re-tuning.
    print("\n=== layout A, a 25% larger person ===")
    big = synthetic_body(scale=1.25)
    _, _, _, rep_c = solve(big, layout_a)
    print(f"  reachable {100 * rep_c['covered_frac']:.1f}%  "
          f"per-arm cm2 {['%.0f' % v for v in rep_c['per_arm_cm2']]}")

    print("\nOK")
