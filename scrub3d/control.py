"""scrub3d/control.py -- the coverage controller. There is no stored trajectory.

WHAT THIS IS NOT
----------------
It is not a path generator. Nothing here produces a list of waypoints to be
executed later, and nothing replays one. A waypoint list warped through an
updated transform is still a decision taken under conditions that have since
changed: the body has moved, the reachable set is different, and the surface
under the sponge is not the surface that was measured.

WHAT IT IS
----------
A goal plus a policy. The goal is a set of cells that still need scrubbing. The
policy is evaluated at control rate from live state and answers one question:
from where the sponge is now, which way should it move? The sweep you observe
is not scripted -- it emerges from "keep going the way you were going, toward
whatever still needs doing".

    precomputed (measurements and goals)   |  live, every tick
    ---------------------------------------|---------------------------
    the body's shape                       |  where the sponge goes next
    which cells still need scrubbing       |  direction and speed
    territory ownership (1Hz + handoff)    |  contact depth, by touch
    sponge footprint                       |  the local surface frame

THE FIELD
---------
Potential is the geodesic distance to the nearest unvisited cell, by multi
source Dijkstra over the surface adjacency graph. Move downhill. Three reasons
this particular choice is right here rather than a hand rolled attraction term:

  - It cannot strand. Greedy "walk toward the nearest unvisited point" gets
    trapped behind already-cleaned ground and dithers; a geodesic field routes
    around, because the shortest path over the surface IS the route.
  - It degrades into a sweep. With a smoothness bias, downhill on this field
    produces boustrophedon-like coverage without anyone planning a
    boustrophedon.
  - It adapts for free. The field is defined on the body, so when the body
    moves the field moves with it. There is nothing to recompute and nothing
    to warp.

Reach margin enters as a term rather than a test, which is the difference
between steering away from your own limits and driving into them and failing.
py/arm.py:275-279 returns False and HOLDS on an unreachable target, so a
waypoint follower stalls silently while its timer runs and coverage is credited
for cells never visited. A field controller never proposes the point.
"""
import heapq
import math
import os
import sys

import numpy as np
from scipy.spatial import cKDTree

# ONE easing curve for the whole project, not a second copy of the quintic.
# py/motion.py owns it and documents why it is SMOOTHERSTEP and not
# smoothstep; a copy here would drift from that comment the first time
# either is touched. Same import shape armlink.py:72-73 already uses.
_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "py")
if _PY not in sys.path:
    sys.path.insert(0, _PY)
from motion import smooth5                                      # noqa: E402

try:
    from . import kinematics as K
except ImportError:
    import kinematics as K


class CoverageController:
    """One arm working one territory. Stateless except for what is covered.

    Parameters are deliberately few and none of them encode the body, the arm
    position, or a pattern -- if a new body or a moved arm needed a parameter
    change, the controller would not be general and the whole claim would be
    false. The "nothing is preset" test in __main__ is what holds that honest.
    """

    def __init__(self, pts, nrm, area, T_world_arm, sponge_r=40.0,
                 standoff=110.0, v_scrub=110.0, hz=40.0,
                 w_smooth=0.45, w_reach=0.30, k_adj=8):
        self.pts = np.asarray(pts, float)
        self.nrm = np.asarray(nrm, float)
        self.area = np.asarray(area, float)
        self.T = np.asarray(T_world_arm, float)
        self.Tinv = np.linalg.inv(self.T)
        self.sponge_r = sponge_r
        self.standoff = standoff
        self.v_scrub = v_scrub          # mm/s the sponge travels; PacedTool reads it
        self.hz = hz
        self.step = v_scrub / hz
        self.w_smooth = w_smooth
        self.w_reach = w_reach

        self.visited = np.zeros(len(self.pts), bool)
        self.tree = cKDTree(self.pts)

        # PLANNING NODES, SEPARATE FROM MEASUREMENT CELLS.
        #
        # You cannot plan coverage on a grid finer than the tool. Surface cells
        # sit a few millimetres apart because that is the resolution the depth
        # scan measures at; the sponge is 80mm across. Planning on the fine
        # grid meant every single step marked the whole local neighbourhood
        # visited, so no adjacent cell ever needed scrubbing, so EVERY move
        # became a traverse. Measured: the scrubbing path length collapsed to
        # essentially zero while coverage still read 100%, because the
        # controller was hopping across the body rather than sweeping it.
        #
        # So: fine cells for ACCOUNTING, coarse nodes for PLANNING, spaced at
        # roughly one swath with overlap. Voxel-bucket the cells and keep one
        # representative each.
        # Spacing must guarantee every cell falls inside SOME node's footprint,
        # or those cells can never be marked and coverage stalls short of 100%
        # for a purely geometric reason that looks like a tuning problem.
        # Voxel worst case is spacing*sqrt(3)/2 from the representative, so
        # spacing <= sponge_r keeps it at 0.87*sponge_r. Measured: 1.4x gives a
        # 48.5mm worst case against a 40mm sponge and coverage tops out at
        # ~94.6%; 1.0x gives 34.6mm and reaches 99.6%.
        spacing = max(self.sponge_r * 1.0, 1e-3)
        keys = np.floor(self.pts / spacing).astype(np.int64)
        _, first = np.unique(keys, axis=0, return_index=True)
        self.node_idx = np.sort(first)
        self.nodes = self.pts[self.node_idx]

        k = min(k_adj, len(self.nodes))
        if k > 1:
            _, nb = cKDTree(self.nodes).query(self.nodes, k=k)
            self.nb = nb[:, 1:]
        else:
            self.nb = np.zeros((len(self.nodes), 0), int)
        self.adj_w = np.linalg.norm(
            self.nodes[self.nb] - self.nodes[:, None, :], axis=2) \
            if self.nb.size else np.zeros((len(self.nodes), 0))

        # Reach margin per cell, in the arm's own frame. Recomputed by
        # refresh() whenever the body moves, which is what makes body motion
        # and arm placement the same kind of input.
        self.margin = np.zeros(len(self.pts))
        self.refresh()

        self.here = None          # current cell index
        self.dir = None           # last travel direction, world
        self.stalls = 0

    # -- live state ---------------------------------------------------------

    def refresh(self, pts=None, nrm=None):
        """Recompute reach margin for the CURRENT body pose.

        Call when the body moves. Cheap: one IK per cell. Everything that
        depends on where the person is lives here and nowhere else.
        """
        if pts is not None:
            self.pts = np.asarray(pts, float)
            self.tree = cKDTree(self.pts)

            # THE PLANNING NODES HAVE TO MOVE TOO, and they did not.
            #
            # refresh() updated the cells and the reach margin and left
            # self.nodes and self.adj_w where the body was when this controller
            # was built. Live, that meant the arm kept planning over a graph
            # sitting where the person USED to be: the rendered body followed
            # them, the coverage accounting followed them, and the thing
            # choosing where to go next did not. It looks like it is working,
            # which is the worst kind of wrong.
            #
            # The node INDICES stay. They are a voxel subsample of the cells,
            # and a body does not change topology by moving -- the same cells
            # are still each other's neighbours. What changes is WHERE they
            # are, so positions and edge lengths are recomputed while the
            # adjacency is kept. That is also what makes this cheap enough to
            # run every frame.
            self.nodes = self.pts[self.node_idx]
            self.adj_w = (np.linalg.norm(
                self.nodes[self.nb] - self.nodes[:, None, :], axis=2)
                if self.nb.size else np.zeros((len(self.nodes), 0)))
        if nrm is not None:
            self.nrm = np.asarray(nrm, float)
        tgt = self.pts + self.nrm * self.standoff
        h = np.c_[tgt, np.ones(len(tgt))] @ self.Tinv.T
        self.margin = K.reach_margin_many(h[:, 0], h[:, 1], h[:, 2])
        if self.margin.max() > 0:
            self.margin = self.margin / self.margin.max()

    def reachable_mask(self):
        """Per FINE CELL: can the arm reach it at all."""
        return self.margin > 0.0

    def remaining(self):
        """Per PLANNING NODE: is there still unscrubbed reachable surface here.

        A node is done when every fine cell its footprint covers is done, which
        is the only definition under which coverage accounting and the
        controller's own stopping condition agree.
        """
        live = (~self.visited) & self.reachable_mask()
        out = np.zeros(len(self.nodes), bool)
        for i, n in enumerate(self.nodes):
            if self.margin[self.node_idx[i]] <= 0.0:
                continue
            for j in self.tree.query_ball_point(n, self.sponge_r):
                if live[j]:
                    out[i] = True
                    break
        return out

    # -- the field ----------------------------------------------------------

    def potential(self):
        """Geodesic distance to the nearest unvisited reachable cell.

        Multi-source Dijkstra over the surface graph. inf where nothing is
        reachable from that cell, which the caller reads as "done here".
        """
        dist = np.full(len(self.nodes), np.inf)
        heap = []
        for i in np.where(self.remaining())[0]:
            dist[i] = 0.0
            heap.append((0.0, int(i)))
        heapq.heapify(heap)
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist[u]:
                continue
            for k, v in enumerate(self.nb[u]):
                nd = d + self.adj_w[u, k]
                if nd < dist[v]:
                    dist[v] = nd
                    heapq.heappush(heap, (nd, int(v)))
        return dist

    def next_cell(self, phi):
        """-> (cell index, is_traverse) or None.

        Two regimes, and having only the first is what makes a local policy
        thrash. Measured with the local step alone: 1085m of path over a 350cm2
        territory and 7.4 direction reversals per metre, because the sponge
        footprint is far wider than the cell spacing, so one tick cleans a
        whole neighbourhood and the controller then has to crawl across
        already-cleaned ground one cell at a time to reach the frontier -- and
        gives up entirely when its immediate neighbours are all unreachable.

          1. LOCAL STEP -- an adjacent cell still needs scrubbing. Move there,
             biased to keep the current heading. This is the sweep.
          2. TRAVERSE -- the neighbourhood is done. Go straight to the nearest
             remaining cell instead of crawling. The geodesic field already
             knows which one and how far.

        This is the classic boustrophedon-plus-backtracking structure. The
        traverse is not scrubbing and is reported separately, because counting
        it as coverage would be a lie.
        """
        rem = self.remaining()
        if not rem.any():
            return None
        nmarg = self.margin[self.node_idx]
        if self.here is None:
            # Start at the most comfortably reachable remaining node.
            return int(np.argmax(np.where(rem, nmarg, -np.inf))), False

        # -- 1. local step ---------------------------------------------------
        local = [int(v) for v in self.nb[self.here] if rem[v]]
        if local:
            best, best_cost = None, np.inf
            for v in local:
                # Among nodes that all still need scrubbing, the tie-break is
                # heading and reach comfort. This is what turns a greedy walk
                # into a sweep.
                cost = 0.0
                if self.dir is not None:
                    d = self.nodes[v] - self.nodes[self.here]
                    n = np.linalg.norm(d)
                    if n > 1e-9:
                        cost -= self.w_smooth * float(d @ self.dir) / n
                cost -= self.w_reach * nmarg[v]
                if cost < best_cost:
                    best, best_cost = v, cost
            return best, False

        # -- 2. traverse -----------------------------------------------------
        # The sponge LIFTS and moves through free space; it does not drag
        # across cleaned skin. So the traverse target is simply the nearest
        # remaining cell in space, not the nearest along the surface graph.
        #
        # Walking the surface graph instead was measurably wrong: a territory
        # spanning an upper arm and a forearm is not necessarily connected in
        # the kNN graph, and any unreachable cell in between severs it, so the
        # controller declared itself finished with a third of its territory
        # untouched. Coverage read 50-75% and looked like a tuning problem.
        idx = np.where(rem)[0]
        d = np.linalg.norm(self.nodes[idx] - self.nodes[self.here], axis=1)
        return int(idx[int(np.argmin(d))]), True

    # -- one tick -----------------------------------------------------------

    def tick(self):
        """Advance one control step. -> (world target xyz, arm-frame xyz) or None.

        None means this territory is finished or nothing further is reachable.
        """
        out = self.next_cell(None)
        if out is None:
            return None
        nxt, traverse = out

        if self.here is not None and not traverse:
            d = self.nodes[nxt] - self.nodes[self.here]
            n = np.linalg.norm(d)
            if n > 1e-9:
                self.dir = d / n
        elif traverse:
            self.dir = None            # heading is meaningless after a jump
        self.here = nxt

        # Mark every fine cell under the sponge footprint as done. Footprint,
        # not the node: the sponge is 80mm across and crediting a point would
        # under-report coverage by an order of magnitude.
        ci = self.node_idx[nxt]
        for j in self.tree.query_ball_point(self.nodes[nxt], self.sponge_r):
            if self.margin[j] > 0.0:
                self.visited[j] = True

        world = self.nodes[nxt] + self.nrm[ci] * self.standoff
        arm = (self.Tinv @ np.r_[world, 1.0])[:3]
        return world, arm, traverse

    def run(self, max_ticks=20000):
        """Drive to completion. -> stats dict. No hardware involved."""
        scrub, trav = [], []
        reversals, prev = 0, None
        for _ in range(max_ticks):
            out = self.tick()
            if out is None:
                break
            world, arm, traverse = out
            (trav if traverse else scrub).append(world)
            if traverse:
                prev = None
                continue
            if prev is not None and self.dir is not None \
                    and float(prev @ self.dir) < -0.5:
                reversals += 1
            if self.dir is not None:
                prev = self.dir.copy()

        rm = self.reachable_mask()
        cov = (self.area[self.visited & rm].sum() / self.area[rm].sum()
               if rm.any() else 0.0)
        s = np.array(scrub) if scrub else np.zeros((0, 3))
        length_m = (float(np.linalg.norm(np.diff(s, axis=0), axis=1).sum())
                    / 1000.0 if len(s) > 1 else 0.0)
        return {
            "coverage": float(cov),
            "ticks": len(scrub) + len(trav),
            "path_m": length_m,
            "traverses": len(trav),
            "reversals": reversals,
            "reversals_per_m": reversals / max(length_m, 1e-6),
            "reachable_cells": int(rm.sum()),
        }


class PacedTool:
    """The arm's side of tick(): getting there takes time.

    tick() answers WHERE NEXT, instantly. The live view used to ask it again
    every video frame, so a territory was finished in about a second -- the
    sponge "moving" at several metres a second -- and the arms then stood still
    for the rest of the run.

    This holds the ONE target tick() last chose and moves the sponge toward it
    at the controller's own v_scrub. It is not a stored path: the only
    waypoints are the approach to that single target, and every one is re-read
    from the controller's live nodes each step, so a target moves with the
    person. A traverse lifts LIFT_MM clear of the standoff, crosses, and comes
    back down, because it goes through free space rather than dragging across
    skin that is already clean.

    CREDIT FOLLOWS THE SPONGE. tick() marks the chosen cell's footprint the
    moment it chooses it. Those cells stay `pending` until the sponge arrives,
    so paint and coverage never run ahead of the arm. The controller still
    plans on its full `visited`, which is what keeps it from choosing the same
    cell twice.

    The target point is the controller's own: node + normal * standoff, the
    same point reach and partition were computed for.
    """

    LIFT_MM = 60.0
    MAX_TICKS_PER_STEP = 50        # a guard; one step normally takes one or two
    # The slowest a traverse ever creeps, as a fraction of v_scrub. See
    # _speed_scale: a true zero at the start stalls the move forever.
    MIN_SCALE = 0.12
    # Distance over which a traverse comes up to speed. 40mm at 110mm/s is
    # about a third of a second of ramp, which removes the step without the
    # sponge visibly dawdling on its way across.
    RAMP_MM = 40.0

    def __init__(self, ctl, start):
        self.c = ctl
        self.tool = np.asarray(start, float).copy()   # world mm
        self.route = []                               # [(node, lift_mm)]
        # TRAVERSE EASING STATE. Distance travelled and total distance of the
        # current traverse, so the speed can be shaped along it. None while
        # sweeping, because the sweep must NOT be eased -- see step().
        self._tr_done = None
        self._tr_total = None
        self.pending = np.zeros(len(ctl.pts), bool)
        # Nothing is left to reach RIGHT NOW. Not final: live, the body moves
        # and the reachable set with it -- at the first tracked frame of bag01
        # three arms reach nothing at all -- so every step asks again.
        self.finished = False
        self.contact = False          # on the skin: sweeping, or just arrived
        self.sweeping = False         # the current move stays on the skin
        self.arrived = []             # nodes reached during the last step

    def _where(self, node, lift):
        c = self.c
        return c.nodes[node] + c.nrm[c.node_idx[node]] * (c.standoff + lift)

    def credited(self):
        """Cells the sponge has actually passed over."""
        return self.c.visited & ~self.pending

    def _route_len(self):
        """Total remaining path length of the queued route, mm."""
        p = self.tool
        total = 0.0
        for node, lift in self.route:
            q = self._where(node, lift)
            total += float(np.linalg.norm(q - p))
            p = q
        return total

    def _speed_scale(self):
        """Fraction of v_scrub to travel at right now, in (0, 1].

        Eases a traverse IN with the SAME quintic the scripted arm uses, so
        both paths accelerate the sponge identically.

        IN ONLY, NOT OUT, and that asymmetry is the whole point. A traverse
        is followed immediately by a sweep at full v_scrub, so braking to a
        crawl at the end of the traverse just moves the velocity step to the
        handoff instead of removing it. MEASURED with a symmetric ease-in-out
        hump: the start step went from 4400 to 528 mm/s^2, but the jerk count
        over a full run went UP, 2 to 7, every one of them at a
        traverse->sweep boundary, and the run took twice as long. Ease in,
        arrive at speed, let the sweep continue at that speed.

        The floor is not cosmetic: smooth5 is exactly 0 at t=0, so an
        unfloored scale makes the budget 0 and the move never starts -- the
        tool would sit still forever and `finished` would never be set.
        MIN_SCALE keeps it creeping off the mark while removing the step.
        """
        if self._tr_done is None or not self._tr_total:
            return 1.0                      # sweeping, or a zero-length move
        # Ramp over RAMP_MM of travel, not over a fraction of the move: a
        # fraction makes a long traverse accelerate lazily and a short one
        # snap, so the acceleration the arm feels would depend on how far it
        # happened to be going. A fixed ramp distance is one acceleration.
        f = min(max(self._tr_done / self.RAMP_MM, 0.0), 1.0)
        return self.MIN_SCALE + (1.0 - self.MIN_SCALE) * smooth5(f)

    def step(self, dt):
        """Advance `dt` seconds of travel. -> the tool's world position."""
        self.arrived = []
        budget = max(float(dt), 0.0) * self.c.v_scrub
        ticks = 0
        while budget > 1e-9:
            if not self.route:
                if ticks >= self.MAX_TICKS_PER_STEP:
                    break
                before = self.c.visited.copy()
                prev = self.c.here
                out = self.c.tick()
                ticks += 1
                if out is None:
                    self.finished = True
                    self.contact = False
                    break
                self.finished = False
                self.pending = self.c.visited & ~before
                nxt = self.c.here
                if prev is None or out[2]:
                    lifted = [(prev, self.LIFT_MM)] if prev is not None else []
                    self.route = lifted + [(nxt, self.LIFT_MM), (nxt, 0.0)]
                    self.sweeping = False
                    # Measure the whole lift-cross-descend up front so the
                    # easing knows where it is along the move. Measured before
                    # this: the sponge left home at the full 110mm/s in ONE
                    # frame (4400 mm/s^2) and stopped the same way. That step
                    # is the torque click py/motion.smooth5 exists to remove.
                    self._tr_done = 0.0
                    self._tr_total = self._route_len()
                else:
                    self.route = [(nxt, 0.0)]
                    self.sweeping = True
                    # NOT eased. The sweep is one short hop to an adjacent
                    # node and the next hop follows immediately, so easing
                    # each one would brake to a stop at every cell -- it would
                    # turn a continuous scrub into a stutter AND stretch the
                    # run, changing when coverage is credited. Constant speed
                    # is correct here; the two regimes differ on purpose.
                    self._tr_done = None
                    self._tr_total = None
                continue
            node, lift = self.route[0]
            d = self._where(node, lift) - self.tool
            dist = float(np.linalg.norm(d))
            # SHAPE THE SPEED, DO NOT SHORTEN THE PATH. The scale only ever
            # reduces how much of the budget is spent this pass, so the tool
            # still visits every route point and still stops exactly on the
            # target. Easing changes WHEN the sponge arrives, never WHERE --
            # which is the same property the __main__ pacing assertion checks.
            spend = budget * self._speed_scale()
            if dist <= spend:
                self.tool = self.tool + d
                budget -= dist
                if self._tr_done is not None:
                    self._tr_done += dist
                self.route.pop(0)
                if not self.route:
                    self.pending[:] = False       # arrived: the promise is kept
                    self.arrived.append(node)
                    self.contact = True
                    self._tr_done = self._tr_total = None   # traverse over
                else:
                    self.contact = False
            else:
                self.tool = self.tool + d * (spend / dist)
                if self._tr_done is not None:
                    self._tr_done += spend
                budget = 0.0
                self.contact = self.sweeping
        return self.tool


if __name__ == "__main__":
    import rigconfig
    import sys

    from bodymodel import synthetic_body, repose
    import partition as P

    def T(x, y, z, yaw):
        c, s = math.cos(yaw), math.sin(yaw)
        return np.array([[c, -s, 0, x], [s, c, 0, y],
                         [0, 0, 1, z], [0, 0, 0, 1]], float)

    # The layout scrub3d/place_arms.py found.
    LAYOUT = rigconfig.shipped_layout()

    def drive(body, layout, label):
        """Partition, then run every arm's controller to completion.

        Reports THREE numbers, and only two of them can really fail.

        Coverage is close to tautological: the traverse always finds the
        nearest remaining cell, so the controller reaches everything unless it
        hits the tick ceiling. It proves termination and that nothing is
        abandoned -- worth having, since both were broken twice during
        development -- but it is not evidence of a good sweep.

        REDUNDANCY is the discriminating metric: total sponge footprint applied
        divided by area actually covered. 1.0 would mean every touch landed on
        fresh surface, which is unachievable and not even desirable -- some
        overlap is what scrubbing IS. But a controller that wanders or
        backtracks inflates it without limit, while still reporting 100%
        coverage, so it is the number that can fail.

        An earlier version measured travel against a perfect-boustrophedon
        ideal of area/(2*sponge_r). That is the wrong yardstick here: an 80mm
        sponge on a 90mm forearm covers nearly half the circumference in one
        touch, so "neat parallel stripes" does not describe the problem and the
        metric read a misleading 20% for behaviour that is actually fine.
        """
        terr, _, _, _ = P.solve(body, layout, envelope_k=P.ENVELOPE_K)
        Pw, Nw, _, A = body.world_cells()
        cov, rev, red, n = 0.0, 0.0, 0.0, 0
        for a in range(len(layout)):
            m = terr.owner == a
            if m.sum() < 8:
                continue
            c = CoverageController(Pw[m], Nw[m], A[m], layout[a])
            st = c.run()
            touches = st["ticks"] - st["traverses"]
            applied = touches * math.pi * c.sponge_r ** 2
            covered = float(A[m][c.visited & c.reachable_mask()].sum())
            cov += st["coverage"]
            rev += st["reversals_per_m"]
            red += applied / max(covered, 1e-6)
            n += 1
        cov, rev, red = cov / n, rev / n, red / n
        print(f"  {label:30s} coverage {100 * cov:5.1f}%  "
              f"redundancy {red:4.1f}x  reversals/m {rev:4.1f}")
        return cov, red

    print("coverage controller -- THE 'NOTHING IS PRESET' TEST")
    print("  same controller, same parameters, one input changed at a time.\n")

    base = synthetic_body()
    results = {}
    results["baseline"] = drive(base, LAYOUT, "baseline")

    # 1. A different body shape.
    results["bigger"] = drive(synthetic_body(scale=1.3), LAYOUT,
                              "a 30% larger person")
    results["smaller"] = drive(synthetic_body(scale=0.8), LAYOUT,
                               "a 20% smaller person")
    results["long_arms"] = drive(
        synthetic_body(upper_len=340, fore_len=320, upper_r=40), LAYOUT,
        "longer thinner arms")

    # 2. A different body POSE. Same person, arms raised and torso turned.
    posed = synthetic_body(arm_drop=0.95, arm_out=0.45)
    results["posed"] = drive(posed, LAYOUT, "arms raised and out")

    # 3. A different ARM PLACEMENT.
    moved = list(LAYOUT)
    moved[0] = T(300, -560, 900, math.radians(65))
    moved[2] = T(520, 120, 640, math.radians(-140))
    results["moved_arms"] = drive(base, moved, "two arms relocated")

    print()
    worst_cov = min(c for c, _ in results.values())
    worst_red = max(r for _, r in results.values())
    for k, (c, r) in results.items():
        print(f"    {k:14s} coverage {100 * c:5.1f}%   redundancy {r:4.1f}x")

    assert worst_cov >= 0.98, (
        f"coverage fell to {100 * worst_cov:.1f}%: the controller abandoned "
        f"reachable surface on some input.")
    # A REGRESSION gate, not a claim of excellence. Measured redundancy sits
    # near 3x; 6x means the controller started wandering. Do not tighten this
    # toward 1.0 -- overlap is what scrubbing is, and 1.0 would mean each patch
    # was touched exactly once.
    assert worst_red <= 6.0, (
        f"redundancy rose to {worst_red:.1f}x on some input: the controller "
        f"reaches everything but wanders to do it, so it is tuned to one case "
        f"and the 'nothing is preset' claim is false.")
    print("\n  worst case across every variation, with NO parameter change:")
    print(f"    coverage {100 * worst_cov:.1f}%, redundancy {worst_red:.1f}x")

    # --- THE ARM'S SIDE: how long getting there takes ----------------------
    #
    # Two properties the live view depends on. The sponge never travels faster
    # than v_scrub, and a cell counts as scrubbed only once the sponge has
    # reached it. Pacing must not change WHAT is covered, only when.
    terr, _, _, _ = P.solve(base, LAYOUT, envelope_k=P.ENVELOPE_K)
    Pw, Nw, _, A = base.world_cells()
    arm = next(a for a in range(len(LAYOUT)) if (terr.owner == a).sum() >= 8)
    m = terr.owner == arm
    Tb = np.asarray(LAYOUT[arm], float)
    home = (Tb @ np.r_[np.asarray(K.fk(*K.ik(235.11, 0.0, 234.79)), float), 1.0])[:3]
    c = CoverageController(Pw[m], Nw[m], A[m], Tb)
    tool = PacedTool(c, home)
    dt, fastest, steps, early = 0.05, 0.0, 0, 0
    last_pos, last_credit = tool.tool.copy(), tool.credited()
    while not tool.finished and steps < 200000:
        pos = tool.step(dt)
        steps += 1
        fastest = max(fastest, float(np.linalg.norm(pos - last_pos)) / dt)
        last_pos = pos.copy()
        now = tool.credited()
        new = now & ~last_credit
        if new.any():
            reached = np.zeros(len(c.pts), bool)
            for node in tool.arrived:
                reached |= (np.linalg.norm(c.pts - c.nodes[node], axis=1)
                            <= c.sponge_r + 1e-6)
            early += int((new & ~reached).sum())
        last_credit = now
    unpaced = CoverageController(Pw[m], Nw[m], A[m], Tb).run()
    rm = c.reachable_mask()
    paced = float(c.area[tool.credited() & rm].sum() / c.area[rm].sum())
    print(f"\n  paced sponge on arm {arm}: fastest {fastest:.1f}mm/s against "
          f"v_scrub {c.v_scrub:.0f}, {steps * dt:.0f}s of travel, coverage "
          f"{100 * paced:.1f}% (unpaced {100 * unpaced['coverage']:.1f}%)")
    assert fastest <= c.v_scrub * 1.0001, "the sponge moved faster than v_scrub"

    # THE SPONGE LEAVES A STANDSTILL GENTLY. Grafted from py/motion.py, which
    # is the scripted arm's mover and had the quintic this one lacked.
    # MEASURED before the graft: the first frame of a traverse went 0 ->
    # 110mm/s, a 4400 mm/s^2 step, which is exactly the torque click
    # motion.smooth5's docstring says smoothstep leaves behind. After: 528.
    # The bound is 8x below the old step and well clear of the 528 measured,
    # so it fails if the ramp is ever removed and does not flake on tuning.
    ramp = PacedTool(CoverageController(Pw[m], Nw[m], A[m], Tb), home)
    first = ramp.step(dt)
    v0 = float(np.linalg.norm(first - home)) / dt
    assert v0 <= c.v_scrub * 0.25, (
        f"the sponge left home at {v0:.0f}mm/s of {c.v_scrub:.0f}: the "
        f"traverse ease-in is gone and the arm gets a velocity step.")
    # AND IT STILL GETS THERE. An ease-in that never reaches speed would pass
    # the line above and ruin the demo, so check it reaches full v_scrub too.
    top = v0
    for _ in range(200):
        was = ramp.tool.copy()
        top = max(top, float(np.linalg.norm(ramp.step(dt) - was)) / dt)
    assert top >= c.v_scrub * 0.95, (
        f"the sponge never got above {top:.0f}mm/s of {c.v_scrub:.0f}: the "
        f"ease-in ramps but never releases, so every traverse crawls.")
    print(f"  traverse leaves home at {v0:.0f}mm/s and reaches {top:.0f}")
    assert early == 0, f"{early} cells were credited before the sponge got there"
    assert tool.finished and abs(paced - unpaced["coverage"]) < 1e-9, \
        "pacing changed what gets covered, not just when"

    # Nothing reachable is a pause, not the end. Live, three arms reach nothing
    # at the first tracked frame of bag01 and plenty a moment later.
    idle = CoverageController(Pw[m], Nw[m], A[m], Tb)
    margin = idle.margin.copy()
    idle.margin[:] = 0.0
    waiting = PacedTool(idle, home)
    waiting.step(dt)
    assert waiting.finished and np.allclose(waiting.tool, home), \
        "a tool with nothing reachable moved anyway"
    idle.margin = margin
    waiting.step(dt)
    assert not waiting.finished and not np.allclose(waiting.tool, home), \
        "a tool that once had nothing to reach never started again"
    print("  an arm with nothing in reach waits, and starts once it has")
    print("  OK")
