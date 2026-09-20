"""scrub3d/fleet.py -- the governor every motion command has to go through.

    FleetGovernor.propose(arm, x, y, z) -> Verdict

NOTHING ELSE MAY COMMAND AN ARM. Arm agents do not call set_target; they
propose, and the governor decides. That is the only structure in which "the
arms never collide" is a property of the system rather than a habit of the
code that happens to be written today.

THE ONE DELIBERATE EXCEPTION
-----------------------------
`estop()` and the retreat that follows a FAILED estop bypass the governor
entirely. Safety must not depend on the governor being alive, and a stop that
first asks permission from a possibly-wedged arbiter is not a stop.

WHAT IT ENFORCES, IN ORDER
---------------------------
1. The half-space for that arm, derived from the territory partition. Not a
   box: four world regions are not four axis-aligned boxes in four rotated
   frames, and a box constrains the TOOL POINT while two arms with tool points
   in disjoint boxes can still cross forearms.
2. Joint limits, via IK. An unreachable target is refused here rather than
   silently clamped by firmware.
3. Capsule separation against every other arm, at the proposed pose. The
   half-spaces reduce how often this matters; they never remove the need for
   it.
4. Structural clearance to the person. The sponge is exempt because it is
   supposed to be in contact; nothing else is.

MOVING AWAY IS NEVER HELD OR REFUSED
------------------------------------
Rules 3 and 4 judge where a move ends, so an arm that is already too close
could not move at all: every step away still ended inside the limit. That
happens without any arm moving wrongly. A person moves their arm onto a
parked one, or a retreat stops short. So a move that ends inside a limit is
still allowed when it ends further away than the arm is now, by at least
SEPARATING_MM. Staying put is never safer than backing off.

PRIORITY IS A FIXED TOTAL ORDER WITHIN A TICK
----------------------------------------------
A dynamic rule chatters: both arms see the other as higher priority, both
hold, neither progresses, and the log fills with a deadlock that looks like
caution. Priority rotates only at path-segment boundaries, and only after an
arm has been starved for longer than STARVE_S.

A HELD ARM MUST NEVER SIT IN UNBOUNDED CONTACT. Holding while touching someone
is the exact end-state the estop history exists to prevent, so a hold that is
in contact retreats along the approach normal instead, and any hold longer
than HOLD_MAX_S escalates to a retreat and a re-queue.
"""
import numpy as np

try:
    from . import collide as C
    from . import kinematics as K
except ImportError:
    import collide as C
    import kinematics as K

STARVE_S = 2.0          # before priority may rotate
HOLD_MAX_S = 5.0        # before a hold becomes a retreat
RETREAT_MM = 20.0       # how far a held, in-contact arm backs off
SEPARATING_MM = 1.0     # a move inside a limit must gain this much to count as
                        # moving away, so jitter never does

CLEAR, HOLD, RETREAT, REFUSE, ESTOP = "clear", "hold", "retreat", "refuse", "estop"


# The fourth joint may move this far per 25ms tick, and no further.
#
# arm.py:333-340 clamps cx, cy and cz through MAX_STEP_MM and then sends
# "t": round(tt, 3) RAW -- last_sent holds only the position. So the position
# is rate limited and the fourth axis is not, on a servo that is pointed at a
# person. Nothing varies it today, because the arm cannot orient its tool and
# the sponge is a sphere, which is exactly why this is cheap insurance: it
# costs one clamp and it means a future change cannot silently re-arm the
# hazard by passing a number that used to be ignored.
J3_MAX_STEP_RAD = 0.05

# How far a released arm falls. clear_estop's own comment in arm.py documents
# about 60mm of gravity droop when the servos let go, and an arm whose link has
# dropped is exactly that: uncommanded, unpowered, and no longer running its own
# collision logic.
DROOP_MM = 60.0

class Verdict:
    """What the governor decided, and why. Never just a bool."""

    def __init__(self, action, target=None, joints=None, reason="", detail=None):
        self.action = action
        self.target = target
        self.joints = joints
        self.reason = reason
        self.detail = detail or {}

    @property
    def ok(self):
        return self.action == CLEAR

    def __repr__(self):
        return f"<{self.action}: {self.reason}>"


class FleetGovernor:
    """Owns every commanded pose for N arms."""

    def __init__(self, layout, planes=None, body_points=None, priority=None,
                 z_ceiling_mm=None, moving=False, d_body=None, d_hold=None,
                 d_estop=None):
        self.layout = [np.asarray(T, float) for T in layout]
        self.n = len(self.layout)
        self.fleet = C.Fleet(self.layout)
        self.planes = planes or {}
        # moving=True: D_BODY is measured on what a move can change, not on
        # the mount (collide.MOVING).
        self.moving = moving
        # The thresholds, collide's unless a caller runs closer. A caller that
        # lowers them keeps d_hold - d_estop above its longest checked step,
        # or a step from outside the hold line can land inside the stop line.
        self.d_body = C.D_BODY if d_body is None else float(d_body)
        self.d_hold = C.D_HOLD if d_hold is None else float(d_hold)
        self.d_estop = C.D_ESTOP if d_estop is None else float(d_estop)
        # THE HEAD, DESIGNED OUT RATHER THAN AVOIDED.
        #
        # Every one of these arms can physically put its tool point above the
        # neck -- measured, all four reach above z=1012mm on the shipped
        # layout. The plan's requirement is that touching a head be
        # geometrically impossible rather than planner-conditional, and a
        # planner that merely never chooses to go there is exactly the
        # conditional version.
        #
        # So it is a hard ceiling in the one place every command passes
        # through. A proposal above it is refused, whatever asked for it and
        # whatever it believed it was doing.
        self.z_ceiling = z_ceiling_mm
        self.body = None if body_points is None else np.asarray(body_points,
                                                                float)
        self._tree = None
        if self.body is not None and len(self.body):
            from scipy.spatial import cKDTree
            self._tree = cKDTree(self.body)

        self.priority = list(priority or range(self.n))
        self.joints = {a: K.ik(235.11, 0.0, 234.79) for a in range(self.n)}
        self.contact = {a: False for a in range(self.n)}
        self.hold_since = {a: None for a in range(self.n)}
        self.j3 = {a: 0.0 for a in range(self.n)}
        self.degraded = set()
        self.starved_since = {a: None for a in range(self.n)}
        self.estopped = False
        self.log = []

    # --- the half-space, which is what the partition buys ------------------
    def _half_space_ok(self, arm, p):
        pl = self.planes.get(arm)
        if pl is None:
            return True, 0.0
        w, d = pl
        margin = float(d - np.asarray(w, float) @ np.asarray(p, float))
        return margin >= 0.0, margin

    def _structural_clearance(self, arm, joints):
        """Min distance from this arm's STRUCTURE to the person. mm."""
        if self._tree is None:
            return float("inf")
        return self.fleet.link_clearance(arm, joints, self._tree, moving=self.moving)

    def link_lost(self, arm):
        """The link to one arm dropped. -> what the fleet should do now.

        NOT AN ESTOP, and the difference matters. An estop parks the pump, so
        three healthy arms would freeze wherever they are -- which, if any of
        them is in contact, means resting on somebody indefinitely. They should
        LIFT OFF instead. The fleet retreats; it does not stop.

        The dead arm cannot retreat, and worse, nobody knows where it is: the
        servos are released, so it falls. Until the link returns AND a fresh
        measured pose confirms where it actually ended up, it is treated as
        occupying everywhere between where it was last told to be and that pose
        DROOP_MM lower. Clearance against it is then the worse of the two,
        which is the bound that matters.
        """
        self.degraded.add(int(arm))
        return {"retreat": [a for a in range(self.n) if a != arm],
                "degraded": sorted(self.degraded),
                "reason": f"link to arm {arm} lost; it is uncommanded and "
                          f"may have drooped up to {DROOP_MM:.0f}mm"}

    def link_restored(self, arm, confirmed_joints=None):
        """Clear a degraded arm -- ONLY with a fresh measured pose. -> bool.

        Write success is not proof of life: the firmware's single cooperative
        loop can wedge while the USB link still accepts bytes, so an arm can
        look healthy and be reporting nothing. Requiring the confirmed joint
        angles, not just a link that came back, is what makes this different
        from hoping.
        """
        if confirmed_joints is None:
            return False
        self.joints[int(arm)] = tuple(float(v) for v in confirmed_joints)
        self.degraded.discard(int(arm))
        return True

    def _drooped(self, arm):
        """That arm's joints with the tool fallen DROOP_MM in world z.

        Solved through IK so the whole chain is consistent rather than just the
        endpoint moved. When the drop is unreachable -- the arm was already low
        -- the commanded pose is returned, which is the honest answer: there is
        nowhere for it to fall to.
        """
        j = self.joints[arm]
        tcp = np.asarray(K.link_points(*j), float)[3]
        R = np.asarray(self.layout[arm], float)[:3, :3]
        down_local = R.T @ np.array([0.0, 0.0, -DROOP_MM])
        fell = K.ik(*(tcp + down_local))
        return fell if fell is not None else j

    def _worst_pair(self, trial, arm):
        """Min separation involving `arm`, over every degraded arm's fall.

        One degraded arm is varied at a time rather than all combinations: with
        four arms that is at most five evaluations instead of sixteen, and the
        minimum over them is the same bound, because a pair distance only ever
        involves two arms.
        """
        base = [trial[a] for a in range(self.n)]
        variants = [base]
        for d in self.degraded:
            v = list(base)
            v[d] = self._drooped(d)
            variants.append(v)
        worst = float("inf")
        for states in variants:
            pairs = self.fleet.pair_distances(states)
            worst = min(worst, min((dd for (i, k), dd in pairs.items()
                                    if arm in (i, k)), default=float("inf")))
        return worst

    def propose(self, arm, x, y, z, t=0.0, normal=None, j3=None):
        """An arm asks to go somewhere. -> Verdict.

        `normal` is the surface normal it is approaching along, needed only so
        a retreat knows which way is away from the person. It is in the same
        frame as x, y, z -- the arm's own -- and points OUT of the body. `t`
        is TIME, in seconds, for the hold clock -- not the fourth joint.

        `j3` IS the fourth joint, and it is rate limited. Today nothing varies
        it: the arm cannot orient its tool, the sponge is a sphere, and the
        joint sits where it starts. The guard exists anyway because the
        hazard it closes is real and currently unreachable only by
        convention: `arm.py` clamps x, y and z and then sends `t` RAW, with
        `last_sent` holding only the position, so a future caller that varied
        it would drive an un-rate-limited servo against a person. Costing one
        clamp to make that impossible is a good trade."""
        if self.estopped:
            return Verdict(ESTOP, reason="fleet is estopped")

        world = (self.layout[arm][:3, :3] @ np.array([x, y, z], float)) \
            + self.layout[arm][:3, 3]

        if self.z_ceiling is not None and world[2] > self.z_ceiling:
            return Verdict(REFUSE, reason="above the head ceiling",
                           detail={"z_mm": float(world[2]),
                                   "ceiling_mm": self.z_ceiling})

        ok, margin = self._half_space_ok(arm, world)
        if not ok:
            return Verdict(REFUSE, reason="outside its own half-space",
                           detail={"margin_mm": margin})

        j = K.ik(x, y, z)
        if j is None:
            return Verdict(REFUSE, reason="unreachable or outside joint limits",
                           detail={"arm_xyz": (x, y, z)})

        # Collision at the PROPOSED pose, against everyone else where they are.
        trial = dict(self.joints)
        trial[arm] = j
        worst = self._worst_pair(trial, arm)
        if worst < self.d_estop:
            # Should never happen. If it does the model or the calibration is
            # wrong, and raising the threshold is not the fix.
            return Verdict(ESTOP, reason="proposed pose is inside D_ESTOP",
                           detail={"separation_mm": worst})
        separating = False
        if worst < self.d_hold:
            # Moving apart is never held: see the module docstring.
            now = self._worst_pair(dict(self.joints), arm)
            if not worst > now + SEPARATING_MM:
                return self._hold(arm, t, normal, worst)
            separating = True

        clear = self._structural_clearance(arm, j)
        backing_off = False
        if clear < self.d_body:
            # Nor is backing away from the person refused.
            now = self._structural_clearance(arm, self.joints[arm])
            if not clear > now + SEPARATING_MM:
                return Verdict(REFUSE, reason="structure would come inside D_BODY",
                               detail={"clearance_mm": clear})
            backing_off = True

        j3_out = self.j3[arm]
        if j3 is not None:
            step = float(np.clip(float(j3) - j3_out,
                                 -J3_MAX_STEP_RAD, J3_MAX_STEP_RAD))
            j3_out = j3_out + step
            self.j3[arm] = j3_out

        self.joints[arm] = j
        self.hold_since[arm] = None
        return Verdict(CLEAR, target=(x, y, z), joints=j,
                       detail={"separation_mm": worst, "clearance_mm": clear,
                               "half_space_margin_mm": margin,
                               "j3_rad": j3_out, "separating": separating,
                               "backing_off": backing_off})

    def _hold(self, arm, t, normal, sep):
        """Hold, unless holding would mean resting on somebody."""
        if self.hold_since[arm] is None:
            self.hold_since[arm] = t
        held = t - self.hold_since[arm]

        if self.contact[arm]:
            # In contact: do not hold. Back off along the approach normal.
            return self._retreat(arm, normal, "held while in contact",
                                 {"separation_mm": sep, "held_s": held})
        if held > HOLD_MAX_S:
            return self._retreat(arm, normal, f"held over {HOLD_MAX_S:.0f}s",
                                 {"separation_mm": sep, "held_s": held})
        return Verdict(HOLD, reason="a higher-priority arm is too close",
                       detail={"separation_mm": sep, "held_s": held})

    def _retreat(self, arm, normal, reason, detail):
        """A RETREAT that says where to go. -> Verdict.

        It used to carry no target, and the arm link only moves on a retreat
        that has one, so every retreat was a hold in practice: the in-contact
        case this exists for left the sponge resting on the person.

        The target is RETREAT_MM from where the arm was last sent, along the
        approach normal. With no normal there is no direction known to be away
        from the person, and inventing one could press harder rather than lift
        off, so the verdict then carries no target and says why.
        """
        detail = dict(detail, back_off_mm=RETREAT_MM,
                      normal=None if normal is None
                      else [float(v) for v in normal])
        n = None if normal is None else np.asarray(normal, float)
        if n is None or not np.linalg.norm(n) > 1e-9:
            detail["no_target"] = ("no approach normal, so no direction is "
                                   "known to be away from the person")
            return Verdict(RETREAT, reason=reason, detail=detail)
        here = np.asarray(K.fk(*self.joints[arm]), float)
        target = here + RETREAT_MM * n / np.linalg.norm(n)
        j = K.ik(*target)
        if j is None:
            detail["no_target"] = "the backed-off point is out of reach"
            return Verdict(RETREAT, reason=reason, detail=detail)
        # The arm is going there, so that is where the governor must think it
        # is for everyone else's next proposal.
        self.joints[arm] = j
        return Verdict(RETREAT, target=tuple(float(v) for v in target), joints=j,
                       reason=reason, detail=detail)

    # --- the exception ------------------------------------------------------
    def estop(self, why=""):
        """ALL arms, every time, and it does not go through propose().

        One arm stopping is almost never a local problem, and you cannot
        establish that it is in the 100ms available. More decisively: a stopped
        arm becomes an obstacle that no longer runs its own collision logic and
        whose pose is uncommanded, because the servos release and droop. Moving
        three arms around an object whose position you no longer know is
        exactly what this layer exists to prevent. A partial stop is strictly
        more dangerous than a full one.
        """
        self.estopped = True
        self.log.append(("estop", why))
        return [a for a in range(self.n)]

    def clear_estop(self, consent_pressed):
        """All or nothing, and only with fresh consent.

        Never let three arms resume around a drooped, uncommanded fourth.
        """
        if not consent_pressed:
            return False
        self.estopped = False
        self.hold_since = {a: None for a in range(self.n)}
        self.log.append(("clear_estop", "fresh consent"))
        return True

    # --- the recovery nobody plans for -------------------------------------
    def home_order(self, home_joints=None):
        """A staggered order for sending everything home. -> [arm indices].

        THE MOST PROBABLE REAL COLLISION IS FOUR go_home() AT ONCE. It fires on
        estop clear, on camera dropout, on link loss and in the shutdown
        `finally` -- which is to say during recovery, when everyone is already
        stressed. With arbitrary placement you cannot assume the four home
        poses are mutually clear, and the four PATHS to home certainly are not.

        So compute it rather than assume it: if every arm at home is mutually
        clear, they can go together; otherwise they go one at a time, in an
        order that never leaves a pair inside D_HOLD.
        """
        j = home_joints or K.ik(235.11, 0.0, 234.79)
        states = [j] * self.n
        pairs = self.fleet.pair_distances(states)
        worst = min(pairs.values()) if pairs else float("inf")

        # AND CHECK THE BODY, not only the other arms. Measured on the shipped
        # layout against a seated person at rest, arm 0 at HOME sits 3.5mm from
        # them, against a 60mm threshold. Staggering the order fixes arms
        # hitting each other and does nothing at all about that: an ordering
        # cannot make an unsafe destination safe.
        body_clear = {a: self._structural_clearance(a, j)
                      for a in range(self.n)}
        unsafe = [a for a, d in body_clear.items() if d < self.d_body]
        if unsafe:
            return [], {"together": False, "min_separation_mm": worst,
                        "unsafe_at_home": unsafe,
                        "body_clearance_mm": body_clear,
                        "reason": ("HOME puts these arms inside D_BODY of the "
                                   "person. Going there is not a recovery. "
                                   "Move the mount or pick another home pose.")}
        if worst >= self.d_hold:
            return list(range(self.n)), {"together": True,
                                         "min_separation_mm": worst,
                                         "body_clearance_mm": body_clear}
        # Order by how crowded each arm is: the most boxed-in moves first,
        # while the others are still out of its way.
        crowd = {a: min((d for (i, k), d in pairs.items() if a in (i, k)),
                        default=float("inf")) for a in range(self.n)}
        order = sorted(range(self.n), key=lambda a: crowd[a])
        return order, {"together": False, "min_separation_mm": worst,
                       "reason": "home poses are not mutually clear"}

    def rotate_priority(self, t, at_segment_boundary):
        """Move a starved arm up the order. Only at a segment boundary."""
        if not at_segment_boundary:
            return False
        for a in self.priority[1:]:
            if self.hold_since[a] is not None and \
                    t - self.hold_since[a] > STARVE_S:
                self.priority.remove(a)
                self.priority.insert(0, a)
                self.log.append(("rotate_priority", f"arm {a} starved"))
                return True
        return False


if __name__ == "__main__":
    import rigconfig
    import math

    try:
        from .anatomy import anatomical_body, world_meshes
        from .place_arms import pose as arm_pose
        from . import partition as P
    except ImportError:
        from anatomy import anatomical_body, world_meshes
        from place_arms import pose as arm_pose
        import partition as P

    print("fleet governor")
    body, meshes = anatomical_body()
    obst = np.vstack([V for V, F in world_meshes(body, meshes).values()])
    layout = rigconfig.shipped_layout()
    terr, planes, phases, rep = P.solve(body, layout, envelope_k=P.ENVELOPE_K,
                                        obstacle_points=obst)
    gov = FleetGovernor(layout, planes={a: p for a, p in enumerate(
        [planes.get((0, 1)), None, None, None]) if p is not None},
        body_points=obst)

    print(f"  {gov.n} arms, {len(gov.planes)} half-space constraints, "
          f"{len(obst)} body points")

    # 1. A reachable target that ALSO clears the person. HOME is not one, and
    # that is the first thing this governor found: arm 0 at HOME sits 3.5mm
    # from a seated person at rest, against a 60mm threshold.
    from scipy.spatial import cKDTree
    tree = cKDTree(obst)
    jh = K.ik(235.11, 0.0, 234.79)
    print("\n  structural clearance at HOME, per arm:")
    for a in range(4):
        c = gov.fleet.link_clearance(a, jh, tree)
        print(f"    arm {a}: {c:7.1f}mm  "
              f"{'VIOLATES D_BODY' if c < C.D_BODY else 'ok'}")

    v = None
    for dz in (0.0, 90.0, 180.0, 260.0):
        for dx in (0.0, -70.0, -140.0):
            cand = gov.propose(0, 235.11 + dx, 0.0, 234.79 + dz, t=0.0)
            if cand.ok:
                v = cand
                break
        if v:
            break
    assert v is not None, "no target near home clears both arms and body"
    print(f"\n  a clear target            -> {v.action:8s} "
          f"clearance {v.detail['clearance_mm']:.0f}mm, "
          f"separation {v.detail['separation_mm']:.0f}mm")

    # 2. Something the arm physically cannot reach.
    v = gov.propose(0, 900.0, 0.0, 234.79, t=0.0)
    print(f"  far outside reach         -> {v.action:8s} {v.reason}")
    assert v.action == REFUSE

    # 3. Straight into the person.
    v = gov.propose(1, 235.11, 0.0, 0.0, t=0.0)
    print(f"  aimed low at the body     -> {v.action:8s} {v.reason}")

    # 4. Estop is all or nothing and does not need the governor's opinion.
    stopped = gov.estop("test")
    print(f"\n  estop stops               -> arms {stopped}")
    assert stopped == list(range(gov.n)), "estop was partial"
    v = gov.propose(0, 235.11, 0.0, 234.79, t=1.0)
    assert v.action == ESTOP, "a proposal was accepted while estopped"
    assert not gov.clear_estop(consent_pressed=False), \
        "estop cleared without consent"
    assert gov.clear_estop(consent_pressed=True)
    print(f"  clears only with consent  -> ok")

    # 5. The four-at-once recovery.
    order, det = gov.home_order()
    if det.get("unsafe_at_home"):
        print(f"\n  going home REFUSED: arms {det['unsafe_at_home']} would sit "
              f"inside D_BODY of the person")
        print(f"    {det['reason']}")
        assert order == [], "an unsafe home order was handed out anyway"
    else:
        print(f"\n  going home: "
              f"{'together' if det['together'] else 'staggered'}, order "
              f"{order}, min separation {det['min_separation_mm']:.0f}mm")
        assert len(order) == gov.n

    # 6. A held arm in contact must retreat, never hold -- and a retreat has to
    # say where to, or the arm link has nothing to send and it is a hold.
    was = np.asarray(K.fk(*gov.joints[2]), float)
    gov.contact[2] = True
    v = gov._hold(2, t=0.0, normal=(1.0, 0.0, 0.0), sep=70.0)
    print(f"\n  held while in contact     -> {v.action:8s} {v.reason}, to "
          f"{None if v.target is None else [round(c, 1) for c in v.target]}")
    assert v.action == RETREAT, "an arm was told to hold while touching someone"
    assert v.target is not None, "a retreat with nowhere to go is a hold"
    moved = np.asarray(v.target) - was
    assert abs(np.linalg.norm(moved) - RETREAT_MM) < 1e-6 and moved[0] > 0, \
        f"the retreat did not back off {RETREAT_MM:.0f}mm along the normal: {moved}"
    assert np.allclose(K.fk(*gov.joints[2]), v.target, atol=1e-3), \
        "the governor still thinks the arm is where it was before retreating"
    v = gov._hold(2, t=0.0, normal=None, sep=70.0)
    assert v.action == RETREAT and v.target is None and v.detail.get("no_target"), \
        "a retreat with no normal invented a direction"
    print(f"  ... with no normal given  -> no target: {v.detail['no_target']}")
    gov.contact[2] = False
    gov.hold_since[2] = None
    v = gov._hold(2, t=0.0, normal=None, sep=70.0)
    assert v.action == HOLD
    v = gov._hold(2, t=HOLD_MAX_S + 1.0, normal=(0.0, 0.0, 1.0), sep=70.0)
    print(f"  held past {HOLD_MAX_S:.0f}s            -> {v.action:8s} "
          f"{v.reason}")
    assert v.action == RETREAT and v.target is not None, "an arm held forever"

    # 7. An arm already too close to the person may back away, and only that.
    # A person moves their arm onto a parked one: every step away from them
    # still ends inside D_BODY, and refusing all of them pins the arm there.
    g4 = FleetGovernor(layout, body_points=obst)
    found = None
    for a in range(g4.n):
        for x in np.linspace(120.0, 320.0, 9):
            for y in np.linspace(-200.0, 200.0, 9):
                for z in np.linspace(-120.0, 280.0, 9):
                    j = K.ik(x, y, z)
                    if j is None:
                        continue
                    c = g4.fleet.link_clearance(a, j, tree)
                    trial = dict(g4.joints)
                    trial[a] = j
                    if 15.0 < c < C.D_BODY - 15.0 and \
                            g4._worst_pair(trial, a) > C.D_HOLD + 30.0:
                        found = (a, np.array([x, y, z]), j, c)
                        break
                if found:
                    break
            if found:
                break
        if found:
            break
    assert found is not None, "no pose inside D_BODY to test backing away from"
    a, p, j, now = found
    g4.joints[a] = j
    steps = []
    for d in np.vstack([np.eye(3), -np.eye(3)]) * 6.0:
        jj = K.ik(*(p + d))
        if jj is not None:
            steps.append((g4.fleet.link_clearance(a, jj, tree), p + d))
    away = max(steps, key=lambda s: s[0])
    toward = min(steps, key=lambda s: s[0])
    assert away[0] > now + SEPARATING_MM and toward[0] < now, \
        "the test pose has no step both away from and toward the person"
    v = g4.propose(a, *away[1], t=0.0)
    print(f"\n  inside D_BODY ({now:.0f}mm), backing off to {away[0]:.0f}mm "
          f"-> {v.action}")
    assert v.action == CLEAR and v.detail["backing_off"], \
        "an arm too close to the person was not allowed to back away"
    g4.joints[a] = j
    v = g4.propose(a, *toward[1], t=0.0)
    print(f"  inside D_BODY ({now:.0f}mm), closing to {toward[0]:.0f}mm   "
          f"-> {v.action}")
    assert v.action == REFUSE, "an arm was let closer to the person"

    # 8. Two arms already inside D_HOLD may move apart, and only apart.
    two = [arm_pose(0.0, 0.0, 0.0, 0.0), arm_pose(560.0, 0.0, 0.0, math.pi)]
    g5 = FleetGovernor(two)
    g5.joints[1] = K.ik(250.0, 0.0, 200.0)
    close = None
    for x in np.linspace(150.0, 320.0, 69):
        j = K.ik(x, 0.0, 200.0)
        if j is None:
            continue
        trial = dict(g5.joints)
        trial[0] = j
        if C.D_ESTOP + 15.0 < g5._worst_pair(trial, 0) < C.D_HOLD - 15.0:
            close = (x, j)
            break
    assert close is not None, "no pose inside D_HOLD to test moving apart from"
    x, j = close
    g5.joints[0] = j
    sep = g5._worst_pair(dict(g5.joints), 0)
    v = g5.propose(0, x - 4.0, 0.0, 200.0, t=0.0)
    print(f"\n  inside D_HOLD ({sep:.0f}mm), moving apart  -> {v.action} "
          f"({v.detail.get('separation_mm', float('nan')):.0f}mm)")
    assert v.action == CLEAR and v.detail["separating"], \
        "two arms too close together were not allowed to move apart"
    g5.joints[0] = j
    v = g5.propose(0, x + 4.0, 0.0, 200.0, t=0.0)
    print(f"  inside D_HOLD ({sep:.0f}mm), moving closer -> {v.action}")
    assert v.action in (HOLD, ESTOP), "two arms too close were let closer"


    # --- The fourth axis, which nothing varies and which is guarded anyway.
    #
    # arm.py rate limits x, y and z and then sends the fourth joint RAW. The
    # sponge is a sphere and the arm cannot orient its tool, so nothing asks
    # for a different j3 today -- which is the point: the hazard is currently
    # unreachable by convention, and a convention is not a guard.
    g2 = FleetGovernor(layout)
    asked, seen = 1.5, []
    for k in range(60):
        v = g2.propose(0, 235.11, 0.0, 234.79, t=0.03 * k, j3=asked)
        if v.ok:
            seen.append(v.detail["j3_rad"])
    steps = np.abs(np.diff(seen))
    print(f"\n  fourth joint asked to jump {asked} rad: largest step "
          f"{steps.max():.3f} rad, limit {J3_MAX_STEP_RAD}, arrived after "
          f"{len(seen)} ticks")
    assert steps.max() <= J3_MAX_STEP_RAD + 1e-9, \
        "the fourth axis moved faster than one tick allows"
    assert abs(seen[-1] - asked) < 1e-6, "it never got there"


    # --- One link drops. The fleet retreats; it does not stop. --------------
    #
    # An estop parks the pump, so three healthy arms freeze wherever they are
    # -- and if any is in contact that means resting on somebody. They lift
    # off instead. The dead one cannot, and nobody knows where it is, because
    # released servos fall. Until a MEASURED pose says otherwise it is treated
    # as occupying everywhere between where it was told to be and DROOP_MM
    # below that.
    g3 = FleetGovernor(layout)
    before = g3._worst_pair(dict(g3.joints), 0)
    rep = g3.link_lost(2)
    after = g3._worst_pair(dict(g3.joints), 0)
    print(f"\n  link to arm 2 lost: retreat {rep['retreat']}, "
          f"estopped={g3.estopped}")
    print(f"    separation involving arm 0: {before:.1f} -> {after:.1f}mm")
    assert not g3.estopped, "a lost link stopped the fleet instead of lifting it off"
    assert after <= before + 1e-9, \
        "a lost link made the world look SAFER, which is the wrong direction"
    # And assert the droop is actually MODELLED, not just declared. The
    # separation above can be unchanged simply because arm 2 is far from arm
    # 0, so a test resting on it alone would pass with the fall doing nothing.
    tcp_up = np.asarray(K.link_points(*g3.joints[2]), float)[3]
    tcp_down = np.asarray(K.link_points(*g3._drooped(2)), float)[3]
    fell = (np.asarray(layout[2], float)[:3, :3] @ (tcp_down - tcp_up))[2]
    print(f"    its tool point is modelled {fell:+.1f}mm in world z")
    assert abs(fell + DROOP_MM) < 1.0, \
        f"the fall is {fell:+.1f}mm, not the documented {-DROOP_MM:+.1f}mm"

    assert not g3.link_restored(2), \
        "cleared on a returning link alone; write success is not proof of life"
    assert g3.link_restored(2, confirmed_joints=g3.joints[2])
    assert 2 not in g3.degraded
    print("    clears only on a measured pose, never on a link that came back")

    print("\n  every command goes through propose(); estop does not. OK")
