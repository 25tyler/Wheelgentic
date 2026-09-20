"""scrub3d/armlink.py -- the only bridge from our software to a real arm.

    python scrub3d/armlink.py        # runs offline, no serial port, no robot

WHY THIS EXISTS
----------------
Everything else in this package decides WHERE an arm should go. Nothing until
now could make one go there. The governor produced verdicts, the controller
produced targets, and the gap between them and `py/arm.py` was a paragraph in a
plan.

This closes it. The plan called for subclassing `Arm` and overriding
`set_target`, with the module-level `BOX` left underneath as a wider outer
backstop. Two things changed on contact with the code.

BOX IS NOT WIDER. It is `xmax=420` while this arm reaches 519, and the governor
will approve a point at 518 because it is reachable and clear. `set_target`
then CLAMPS -- measured, 98mm -- and returns True. A governor-approved target
becomes a different target, the arm drives there confidently, and every check
upstream still reports clear. `arm.py` prints a warning about exactly this and
moves anyway. So the two limit systems are not nested, they DISAGREE, and
splitting the difference is the worst of the three available options. This
refuses instead: when the governor and BOX disagree about a point, nobody goes
anywhere until a person has decided which is right.

AND IT COMPOSES RATHER THAN SUBCLASSES, which matters for one method. `estop()`
must work when our code is wrong -- that is its entire job - so it is better
reached through an object we have not re-parented than through a class whose
method resolution we have changed. Everything not named here passes straight
through.

WHY A WRAPPER AND NOT AN EDIT
------------------------------
`py/arm.py` is not ours to change. Every obstacle to using it from outside has
a clean workaround, and all of them are exercised below:

    find_port() globs /dev/cu.* and is macOS-only   ->  pass port= explicitly,
                                                        which bypasses it
    BOX is one module global for all four arms      ->  our per-arm box and
                                                        half-space run FIRST,
                                                        and a point outside
                                                        BOX is refused, not
                                                        clamped
    the fourth axis is never rate limited           ->  the governor slews it
    no serial port on this machine                  ->  dry_run=True builds a
                                                        complete Arm with none

THE ONE RULE
-------------
A CLEAR verdict is the only thing that reaches the hardware. `set_target`
refuses everything else and says which gate stopped it, so a caller cannot get
an arm to move by not knowing about the governor. `estop()` is inherited
unchanged and therefore still bypasses all of this, which is deliberate: safety
must not depend on the governor being alive.

WHAT THIS CANNOT DO WITHOUT HARDWARE
-------------------------------------
Confirm any of it against a real servo. The self-test below runs the whole path
with `dry_run=True`, so what is proven is that the refusals happen, that the
ordering is right, and that nothing reaches the transport that should not. What
is unproven is everything past the serial port, which is why pre-flight reports
those checks UNKNOWN rather than PASS.
"""
import os
import sys
import time

import numpy as np

_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "py")
if _PY not in sys.path:
    sys.path.insert(0, _PY)

try:
    from . import fleet as FLEET
    from . import kinematics as K
except ImportError:
    import fleet as FLEET
    import kinematics as K

# How often the torque caps are re-asserted. Pre-flight item 2, and the reason
# is documented in arm.py itself: ESP32 brownout is routine, and a reset arm
# comes back at the firmware default of 1000, about 16x the intended shoulder
# torque. Four arms means four power draws and four chances of it. The call is
# idempotent, so re-sending it costs nothing and closes the hole without
# editing a file we do not own.
TORQUE_REASSERT_S = 10.0

# No successful feedback in this long and the link is degraded, whatever the
# writes say. arm.py's link_ok counts CONSECUTIVE WRITE FAILURES only, and a
# USB link will happily accept bytes while the ESP32 is wedged -- its loop() is
# a single cooperative thread and is documented to stall. Write success is not
# proof of life.
FEEDBACK_STALE_S = 1.0


def _link_ok(arm):
    """arm.py's own link health, whether it is a property or a method.

    It is a property today. Calling it as a method raises TypeError on a bool,
    which is a silly way for a safety check to die, and pinning the assumption
    here means a change upstream is a one-line fix rather than a crash inside
    the health loop.
    """
    v = getattr(arm, "link_ok", True)
    return bool(v() if callable(v) else v)


class GovernedArm:
    """An `Arm` that can only be commanded through a `FleetGovernor`.

    Composition rather than inheritance for the ownership, inheritance for the
    protocol: the real `Arm` is constructed and held, and every method not
    named here passes through to it untouched. That keeps `estop()` exactly as
    written -- the one method that must never depend on our code being correct.
    """

    def __init__(self, arm_id, governor, port=None, dry_run=False, arm=None):
        try:
            import arm as ARM
        except ImportError as exc:                              # pragma: no cover
            raise RuntimeError(f"py/arm.py not importable from {_PY}: {exc}")
        self.arm_id = int(arm_id)
        self.gov = governor
        # port= explicitly, ALWAYS. arm.py falls back to find_port(), which
        # globs /dev/cu.* and finds nothing on Windows.
        self.arm = arm if arm is not None else ARM.Arm(port=port,
                                                       dry_run=dry_run)
        self.last_verdict = None
        self.last_home = None
        self.refusals = {}
        self._last_caps = -1e9
        # None until the first tick, not time.time(). Seeding it from the
        # wall clock and then ticking with any other clock makes staleness
        # meaninglessly negative, which reads as a permanently healthy link.
        self._last_feedback_ok = None

    # --- the gate ----------------------------------------------------------

    def set_target(self, x, y, z, t=3.14, normal=None, now=None):
        """Ask to go somewhere. -> True only if the governor allowed it.

        Ordering matters and it is: our checks first, hardware second. The
        per-arm box, half-space, joint limits, capsule separation and body
        clearance all run before a single byte is written. Running them the
        other way round would let a refused target be clamped into a
        legal-looking one and sent.
        """
        now = time.time() if now is None else now

        # BOX IS NOT A WIDER BACKSTOP, AND THE PLAN SAID IT WAS.
        #
        # arm.py's module BOX is xmax=420, while this arm reaches 519 and the
        # governor happily approves a point at 518. super().set_target() then
        # CLAMPS it -- measured, 98mm -- and returns True. So a governor-approved
        # target becomes a different target, the arm drives there confidently,
        # and every check upstream reports clear.
        #
        # arm.py warns about exactly this ("check the homography") and still
        # moves. Splitting the difference between two disagreeing limit systems
        # is the worst of the three options, so this refuses instead: if the
        # governor and BOX disagree about a point, nobody goes anywhere until a
        # person has decided which is right.
        outside = self._outside_box(x, y, z)
        if outside:
            key = "outside arm.py's BOX"
            self.refusals[key] = self.refusals.get(key, 0) + 1
            self.last_verdict = FLEET.Verdict(
                FLEET.REFUSE, reason="outside arm.py's BOX, which would "
                                     "silently relocate it",
                detail=outside)
            return False

        v = self.gov.propose(self.arm_id, x, y, z, t=now, normal=normal)
        self.last_verdict = v
        if not v.ok:
            self.refusals[v.reason] = self.refusals.get(v.reason, 0) + 1
            if v.action == FLEET.RETREAT and v.target is not None:
                # A retreat is not a refusal to move, it is a different move,
                # and it bypasses the governor for the same reason estop does.
                # It starts from a point BOX accepted and backs off 20mm, so
                # at worst BOX shortens it, and a shorter retreat is still away.
                return bool(self.arm.set_target(*v.target, t=t))
            return False
        return bool(self.arm.set_target(*v.target, t=t))

    def go_home(self):
        """Refuse a HOME that is inside the person, and say so.

        fleet.home_order() found arm 0 sitting 3.5mm from the body at HOME, and
        go_home fires on estop clear, dropout, link loss and shutdown -- during
        recovery, when nobody is watching closely. Inheriting it unchanged would
        put the trap back.
        """
        # home_order() returns (order, details). Testing membership of that
        # pair was never true for an arm index, so every go_home was refused,
        # safe or not.
        order, self.last_home = self.gov.home_order()
        if self.arm_id not in order:
            return False
        # arm.py's go_home() is `self.set_target(*HOME)` and returns nothing,
        # so its answer is read from the call it makes rather than assumed.
        import arm as ARM
        return bool(self.arm.set_target(*ARM.HOME))

    @staticmethod
    def _outside_box(x, y, z):
        """How far outside arm.py's BOX this point is. -> dict, or {} if inside."""
        import arm as ARM
        B = ARM.BOX
        over = {}
        for name, v, lo, hi in (("x", x, B["xmin"], B["xmax"]),
                                ("y", y, B["ymin"], B["ymax"]),
                                ("z", z, B["zmin"], B["zmax"])):
            if v < lo:
                over[name] = round(float(v - lo), 1)
            elif v > hi:
                over[name] = round(float(v - hi), 1)
        return over

    # --- health, which is not the same as the link being up ----------------

    def health_tick(self, now=None):
        """Call at any rate. -> dict of what it did and what it found.

        Two jobs, both closing holes that arm.py documents and does not fill.
        """
        now = time.time() if now is None else now
        out = {"caps_reasserted": False, "link": "ok"}

        if now - self._last_caps >= TORQUE_REASSERT_S:
            self._last_caps = now
            try:
                self.arm.apply_torque_caps()
                out["caps_reasserted"] = True
            except Exception as exc:                            # noqa: BLE001
                out["link"] = f"torque cap write failed: {exc}"

        fb = getattr(self.arm, "feedback", None) or {}
        if fb or self._last_feedback_ok is None:
            self._last_feedback_ok = now
        stale = now - self._last_feedback_ok
        if stale > FEEDBACK_STALE_S:
            out["link"] = "degraded"
            out["stale_s"] = round(stale, 2)
            # NOT an estop. A dead arm is an obstacle whose pose nobody knows,
            # so the healthy ones lift off and it is modelled as having fallen.
            out["fleet"] = self.gov.link_lost(self.arm_id)
        elif not _link_ok(self.arm):
            out["link"] = "write failures"
            out["fleet"] = self.gov.link_lost(self.arm_id)
        return out

    # --- everything else is the real Arm, untouched ------------------------

    def __getattr__(self, name):
        return getattr(self.arm, name)


def main():
    try:
        from . import rigconfig
    except ImportError:
        import rigconfig

    print("\narm link, with no serial port and no robot\n")
    layout = rigconfig.load().layout()
    gov = FLEET.FleetGovernor(layout)
    a = GovernedArm(0, gov, dry_run=True)
    print(f"  built a real Arm in dry mode: {type(a.arm).__name__}, "
          f"dry={a.arm.dry}, port={a.arm.ser}")

    # --- the gate is the only way through ----------------------------------
    reach = K.link_points(*gov.joints[0])[3]
    ok = a.set_target(*reach)
    print(f"\n  a reachable target inside the plan      -> {ok}")
    assert ok, f"the governor refused a legal target: {a.last_verdict.reason}"
    assert a.arm.target is not None

    before = a.arm.target
    bad = a.set_target(5000.0, 0.0, 200.0)
    print(f"  a target 5m away                        -> {bad}"
          f"   ({a.last_verdict.reason})")
    assert not bad, "a 5m target reached the hardware"
    assert a.arm.target == before, \
        "a refused target still moved the arm; the BOX clamped it into range"

    far = K.link_points(0.0, -1.2, 0.2)[3]
    high = a.set_target(*far)
    print(f"  a target outside arm.py's own BOX       -> {high}"
          f"   ({a.last_verdict.reason} {a.last_verdict.detail})")
    assert not high, (
        "a point outside BOX was accepted; super().set_target() would have "
        "clamped it and returned True, moving the arm somewhere nobody approved")
    assert a.arm.target == before, "the refused target still moved the arm"

    # --- estop still bypasses everything -----------------------------------
    gov.estop("test")
    after = a.set_target(*reach)
    print(f"\n  once the fleet is estopped              -> {after}"
          f"   ({a.last_verdict.reason})")
    assert not after, "an estopped fleet still commanded an arm"
    assert a.arm.estop is not None, "estop() is not inherited"

    # --- a retreat is a move, and it has to arrive -------------------------
    gov.clear_estop(consent_pressed=True)
    import arm as ARM
    here = np.asarray(reach, float)
    away = here + [0.0, 0.0, 20.0]
    real_propose = gov.propose
    gov.propose = lambda *_a, **_k: FLEET.Verdict(
        FLEET.RETREAT, target=tuple(away), reason="held while in contact")
    backed = a.set_target(*reach)
    print(f"\n  a RETREAT with somewhere to go          -> {backed}")
    assert backed and np.allclose(a.arm.target[:3], away), \
        "a retreat verdict never reached the arm"
    gov.propose = lambda *_a, **_k: FLEET.Verdict(
        FLEET.RETREAT, reason="held while in contact")
    assert not a.set_target(*reach), "a retreat with no target moved the arm"
    gov.propose = real_propose

    # --- going home is checked, and then it actually goes --------------------
    went = a.go_home()
    print(f"  go home, nobody modelled in the chair   -> {went}   "
          f"(order {gov.home_order()[0]})")
    assert went, "a clear HOME was refused; every go_home used to be"
    assert np.allclose(a.arm.target[:3], ARM.HOME[:3]), "go_home did not send HOME"
    T0 = np.asarray(layout[0], float)
    elbow = K.link_points(*K.ik(*ARM.HOME[:3]))[2]
    near = (T0 @ np.r_[elbow, 1.0])[:3]
    crowded = FLEET.FleetGovernor(layout, body_points=near[None, :] + 5.0)
    b = GovernedArm(0, crowded, dry_run=True)
    refused = b.go_home()
    print(f"  go home with the person where HOME is   -> {refused}   "
          f"({b.last_home.get('reason', '')[:48]}...)")
    assert not refused and 0 in b.last_home.get("unsafe_at_home", []), \
        "an unsafe HOME was driven to"

    # --- health --------------------------------------------------------------
    h = a.health_tick(now=1000.0)
    print(f"\n  health at t=1000: caps re-asserted {h['caps_reasserted']}, "
          f"link {h['link']}")
    assert h["caps_reasserted"], "torque caps were never asserted"
    h = a.health_tick(now=1000.0 + TORQUE_REASSERT_S - 0.1)
    assert not h["caps_reasserted"], "caps re-asserted early"
    h = a.health_tick(now=1000.0 + TORQUE_REASSERT_S + 0.1)
    assert h["caps_reasserted"], \
        f"caps not re-asserted after {TORQUE_REASSERT_S}s; a brownout-reset " \
        f"arm sits at 16x the intended shoulder torque until they are"
    print(f"  re-asserts every {TORQUE_REASSERT_S:.0f}s, not once at startup")

    # Feedback staleness, which is the hole link_ok cannot see: writes keep
    # succeeding while the ESP32 is wedged.
    a.arm.feedback = {}
    h = a.health_tick(now=1000.0 + FEEDBACK_STALE_S + 5.0)
    print(f"  no feedback for {h.get('stale_s')}s            -> link "
          f"{h['link']}, fleet retreats {h['fleet']['retreat']}")
    assert h["link"] == "degraded", \
        "a wedged arm with a healthy write path read as healthy"
    assert not gov.estopped, "a stale link estopped the fleet instead of lifting it off"

    print("\n  a CLEAR verdict is the only thing that reaches the arm. OK")


if __name__ == "__main__":
    main()
