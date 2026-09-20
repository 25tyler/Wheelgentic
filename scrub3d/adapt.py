"""scrub3d/adapt.py -- what happens when the person moves.

TWO THINGS ADAPT FOR FREE, AND THEY COVER MOST OF IT
-----------------------------------------------------
Worth stating before the machinery, because they explain why what is left is
narrow.

Motion is defined ON the body, not in space. The coverage field lives on the
surface, cells are stored in region-local coordinates, so when a forearm moves
the field on that forearm moves with it. There is no path to warp and nothing
to re-plan. The geometry never goes stale.

And contact is found by torque rather than by aiming at a computed depth, so
pose error, soft tissue and breathing are absorbed by the control law instead
of needing to be modelled.

WHAT DOES GO STALE IS REACHABILITY, and that is all this file is about. A cell
an arm could reach when the person sat down may be behind them now.

WHY NOT SIMPLY RE-PARTITION EVERY FRAME
----------------------------------------
It is the obvious design and it is dangerous. The separating planes the
governor enforces are derived FROM the partition, so re-solving continuously
moves those planes while arms are in flight, and an arm can find itself on the
wrong side of a boundary that moved under it. Territory churn between two arms
reaching the same seam is exactly how they collide.

So: make the partition robust rather than reactive -- that is what the pose
envelope buys -- and handle the residue with a bounded, explicit handoff.

THREE TIMESCALES
----------------
    40Hz    the coverage field is re-read at the sponge's current position.
            That IS the trajectory; nothing is stored. control.py.
    ~1Hz    THIS FILE. Re-check the feasibility of each arm's REMAINING cells
            against the current pose. Milliseconds.
    on demand   a full re-partition, only with every arm parked.
"""
import numpy as np

try:
    from . import partition as P
except ImportError:
    import partition as P

# A cell must be infeasible for this long before anything is handed over.
# Momentary loss is a landmark flicker, not a person moving.
PERSIST_S = 2.0

# At most one handoff per arm pair per session. If a pair needs a second one,
# the person has moved far enough that the honest answer is to stop and
# re-plan, not to keep patching.
MAX_HANDOFFS_PER_PAIR = 1

# Fraction of an arm's remaining work that must be reachable for it to carry on
# without help.
OK_FRACTION = 0.55


class Adapter:
    """Watches reachability as the person moves. Recommends, never acts.

    Deliberately returns decisions rather than executing them. The caller owns
    the arms, knows whether they are in contact and knows where they are in a
    stroke, and all three gate a handoff. A module that moved arms from in here
    would have to duplicate that state and could then disagree with it.
    """

    def __init__(self, layout, terr, obstacles=None, d_body=None):
        self.layout = layout
        self.terr = terr
        self.obstacles = obstacles
        self.d_body = d_body
        self.n = len(layout)
        self.since = {}                       # (arm) -> first time seen bad
        self.handoffs = {}                    # (a, b) -> count
        self.events = []

    def check(self, posed_body, remaining, t):
        """-> report. `remaining` is {arm: bool mask over cells}.

        Cheap because it re-uses the same feasibility field the partition is
        built from, evaluated at ONE pose rather than over an envelope.
        """
        feas, qual, pts, area = P.feasibility(
            posed_body, self.layout, envelope=[posed_body],
            obstacle_points=self.obstacles, d_body=self.d_body)

        out = {"t": t, "arms": {}, "handoffs": [], "stop": False}
        for a in range(self.n):
            mask = remaining.get(a)
            if mask is None or not mask.any():
                self.since.pop(a, None)
                out["arms"][a] = {"remaining": 0, "reachable_frac": 1.0,
                                  "ok": True}
                continue
            frac = float((feas[mask, a] > 0.5).mean())
            ok = frac >= OK_FRACTION
            out["arms"][a] = {"remaining": int(mask.sum()),
                              "reachable_frac": frac, "ok": ok}
            if ok:
                self.since.pop(a, None)
                continue

            # Persistence, not a single bad frame.
            t0 = self.since.setdefault(a, t)
            if t - t0 < PERSIST_S:
                continue

            # Who else can take it? The alternate owner was pre-computed at
            # plan time, so this is a lookup and a feasibility read, not a
            # re-solve.
            lost = mask & (feas[:, a] <= 0.5)
            best, best_frac = None, 0.0
            for b in range(self.n):
                if b == a:
                    continue
                if self.handoffs.get((min(a, b), max(a, b)), 0) >= \
                        MAX_HANDOFFS_PER_PAIR:
                    continue
                f = float((feas[lost, b] > 0.5).mean()) if lost.any() else 0.0
                if f > best_frac:
                    best, best_frac = b, f
            if best is None or best_frac < OK_FRACTION:
                # Nobody can take it and the arm cannot finish. That is a stop,
                # not a handoff, and saying so is the whole point of the file.
                out["stop"] = True
                out["arms"][a]["why"] = "no arm can reach this work"
                continue
            out["handoffs"].append({
                "from": a, "to": best, "cells": int(lost.sum()),
                "to_reachable_frac": best_frac})
        return out

    def commit(self, handoff):
        """Record a handoff the caller actually performed."""
        k = (min(handoff["from"], handoff["to"]),
             max(handoff["from"], handoff["to"]))
        self.handoffs[k] = self.handoffs.get(k, 0) + 1
        self.events.append(dict(handoff))
        return self.handoffs[k]


def suggest_posture(body, layout, obstacles=None, variants=None, envelope_k=0):
    """Which posture would let the arms reach the most? -> ranked list.

    ADAPT THE PERSON, NOT ONLY THE PLAN, and it is often the better answer.

    Some surface is unreachable for reasons no planner can fix. The underside
    of a forearm resting on a thigh is pressed against the thigh: getting a
    sponge in there means getting an arm in there, and D_BODY forbids it, as it
    should. No amount of re-partitioning finds a way in, because there is no
    way in.

    Asking is free, immediate, and more dignified than a machine straining
    around somebody. "Please lift your forearms a little" costs one sentence
    and can hand back surface that no rig change would reach. So: score the
    postures a person can comfortably adopt, and say which one helps.

    This is escalation level 3 from the plan, and it belongs BEFORE the
    expensive options -- re-partitioning, re-placing arms, adding an arm --
    rather than after them.
    """
    try:
        from . import place_arms as PA
    except ImportError:
        import place_arms as PA

    variants = variants or DEFAULT_POSTURES
    out = []
    for name, ask, tf_fn in variants:
        posed = tf_fn(body)
        _s, rep = PA.score(posed, layout, envelope_k=envelope_k,
                           obstacle_points=obstacles)
        out.append({"posture": name, "ask": ask,
                    "covered_frac": float(rep["covered_frac"]),
                    "per_arm_cm2": rep["per_arm_cm2"]})
    base = next((o for o in out if o["posture"] == "as scanned"), None)
    for o in out:
        o["gain_points"] = 100.0 * (o["covered_frac"] -
                                    (base["covered_frac"] if base else 0.0))
    out.sort(key=lambda o: -o["covered_frac"])
    return out


def _rot_about(body, region_names, axis, ang_rad, about="origin"):
    """Rotate named regions about their own origin. -> a re-posed BodyModel."""
    try:
        from .bodymodel import repose
    except ImportError:
        from bodymodel import repose
    R = P._axis_rot(np.asarray(axis, float) /
                    np.linalg.norm(axis), ang_rad)
    tf = {}
    for r in body.regions:
        if r.name not in region_names:
            continue
        T = r.T.copy()
        T[:3, :3] = R @ T[:3, :3]
        tf[r.name] = T
    return repose(body, **tf) if tf else body


FOREARMS = ("forearm_L", "forearm_R")
UPPERARMS = ("upper_arm_L", "upper_arm_R")

# Postures a seated person can hold without effort, each with the sentence you
# would actually say. Nothing here asks anyone to hold something awkward: a
# posture that cannot be held for two minutes is not a posture.
DEFAULT_POSTURES = [
    ("as scanned", "no change", lambda b: b),
    ("forearms lifted a little", "lift your forearms off your legs",
     lambda b: _rot_about(b, FOREARMS, (0, 1, 0), -0.30)),
    ("forearms lifted and turned out", "lift your forearms and turn your "
     "palms up", lambda b: _rot_about(_rot_about(b, FOREARMS, (0, 1, 0), -0.30),
                                      FOREARMS, (1, 0, 0), 0.45)),
    ("elbows out from the body", "let your elbows come away from your sides",
     lambda b: _rot_about(b, UPPERARMS, (1, 0, 0), 0.28)),
    ("elbows out and forearms lifted", "elbows out, and lift your forearms",
     lambda b: _rot_about(_rot_about(b, UPPERARMS, (1, 0, 0), 0.28),
                          FOREARMS, (0, 1, 0), -0.30)),
]


def may_hand_over(in_contact, at_segment_boundary, final_approach=False):
    """The three gates from the plan, in one place so they cannot drift.

    Never mid-stroke, never while either arm is touching the person, and never
    during a final approach. A handoff is a change of ownership of a piece of
    someone's body; the moment to do it is when nothing is moving toward them.
    """
    return (not in_contact) and at_segment_boundary and (not final_approach)


if __name__ == "__main__":
    import rigconfig
    import math
    import os

    try:
        from . import frames as FRAME
        from . import scan as SCAN
        from .anatomy import anatomical_body, world_meshes
        from .place_arms import pose as arm_pose
    except ImportError:
        import frames as FRAME
        import scan as SCAN
        from anatomy import anatomical_body, world_meshes
        from place_arms import pose as arm_pose

    print("adapting to a person who moves")
    body, meshes = anatomical_body()
    obst = np.vstack([V for V, F in world_meshes(body, meshes).values()])
    layout = rigconfig.shipped_layout()
    terr, planes, phases, rep = P.solve(body, layout, envelope_k=P.ENVELOPE_K,
                                        obstacle_points=obst)
    print(f"  planned: coverage {100 * rep['covered_frac']:.1f}%, "
          f"phases {phases}")

    ad = Adapter(layout, terr, obstacles=obst)
    remaining = {a: (terr.owner == a) for a in range(len(layout))}

    print("\n  re-checking as the person is re-posed through the envelope:")
    print(f"    {'pose':22s} " + "  ".join(f"arm{a}" for a in range(4)))
    env = P.pose_envelope(body, k=5)
    stops = 0
    for i, posed in enumerate(env):
        rep2 = ad.check(posed, remaining, t=float(i))
        cells = "  ".join(f"{100 * rep2['arms'][a]['reachable_frac']:4.0f}%"
                          for a in range(4))
        tag = "scan pose" if i == 0 else f"envelope pose {i}"
        flag = ""
        if rep2["handoffs"]:
            flag = "   handoff: " + ", ".join(
                f"{h['from']}->{h['to']} ({h['cells']} cells)"
                for h in rep2["handoffs"])
        if rep2["stop"]:
            flag += "   STOP"
            stops += 1
        print(f"    {tag:22s} {cells}{flag}")

    # The gates, which are the part that must not drift.
    print("\n  handoff gates:")
    for contact, boundary, final, want in (
            (False, True, False, True),
            (True, True, False, False),
            (False, False, False, False),
            (False, True, True, False)):
        got = may_hand_over(contact, boundary, final)
        state = (f"contact={contact!s:5s} boundary={boundary!s:5s} "
                 f"final={final!s:5s}")
        print(f"    {state} -> {'ALLOW' if got else 'refuse'}")
        assert got == want, f"gate disagreed at {state}"

    # And the cap, which is what stops a handoff loop.
    h = {"from": 0, "to": 1, "cells": 10}
    assert ad.commit(h) == 1
    ad_rep = ad.check(env[0], remaining, t=99.0)
    assert all(hh["from"] != 0 or hh["to"] != 1 for hh in ad_rep["handoffs"]), \
        "a pair was offered a second handoff after its cap"
    print(f"\n  one handoff per pair per session, enforced. "
          f"{stops} pose(s) had work no arm could reach.")

    # --- adapt the PERSON, not only the plan --------------------------------
    print("\n  which posture would let the arms reach the most?")
    ranked = suggest_posture(body, layout, obstacles=obst)
    for r in ranked:
        tag = ("" if r["posture"] == "as scanned"
               else f"   ({r['gain_points']:+.1f} pts)")
        print(f"    {100 * r['covered_frac']:5.1f}%  {r['posture']:32s}{tag}")
    best = ranked[0]
    if best["posture"] != "as scanned" and best["gain_points"] > 1.0:
        print(f'\n    so ask for it: "{best["ask"]}"')
        print(f'    worth {best["gain_points"]:+.1f} points of coverage, for '
              f'one sentence and no hardware.')
    else:
        print("\n    the scanned posture is already the best of these.")

    print("\nOK")
