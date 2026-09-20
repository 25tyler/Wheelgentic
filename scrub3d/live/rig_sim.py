"""Run the four arms on a recorded person, fast, for any rig.

    python scrub3d/live/rig_sim.py RECORDING [RIG.json] [--loops 2]

The person is followed once (MediaPipe and the body model, about 20 s for
the 25 s recording) and every frame's posed body is kept. The arms then run
over those frames as often as wanted, a few seconds a rig, with nothing
else changed: the same Arms, the same governor, the same clock. The rig
editor's Simulate button and arms_anywhere.py both use it.

What a run reports is what says whether the arms worked: what each arm
scrubbed per pass, whether any pass stalled, the governor's verdicts, how
close two arms came, whether any arm jumped (a move no checked step could
make), and whether an arm's own move ever put its structure inside the
body model.
"""
import argparse
import collections
import copy
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import arms_live as AL                          # noqa: E402
import live_body as LB                          # noqa: E402

COL, FLEET, K, PA = AL.COL, AL.FLEET, AL.K, AL.PA

# A pass that has not finished after this long has stalled: every way an arm
# can wait is bounded well below it (a stroke, PHASE_WAIT_S, STALL_S, the
# return timeout), and a pass is SWEEPS times over each patch.
PASS_LIMIT_S = 120.0 * AL.SWEEPS


def snapshot(body):
    """The posed body as it is now. Poses are replaced each frame, not
    changed in place, so copying the dicts is enough."""
    s = copy.copy(body)
    s.T, s.S = dict(body.T), dict(body.S)
    s.joints, s.thighs = dict(body.joints), dict(body.thighs)
    s.chair = list(body.chair)
    return s


class Track:
    """A recording, followed once: [(seconds, events, posed body)]."""

    def __init__(self, folder, seat_mm=420.0, max_frames=None, keep_depth=False):
        """keep_depth: also keep each frame's depth and person mask, as
        depth_guard.Scene uses them (self.depth: [(grid, camera pose or None,
        mask or None)])."""
        intr, src = LB.replay_source(folder, loop=False)
        live = LB.Live(intr, seat_mm)
        self.intr = intr
        self.frames, self.seat, self.T_wc = [], None, None
        self.depth = []
        t0 = None
        try:
            for i, (c, d, t) in enumerate(src):
                if max_frames is not None and i >= max_frames:
                    break
                ev = live.step(c, d, t)
                t0 = t if t0 is None else t0
                if "seat" in ev and self.seat is None:
                    self.seat = copy.deepcopy(live.seat)
                    self.T_wc = np.array(live.T_wc, float)
                self.frames.append((t - t0, frozenset(ev), snapshot(live.body)))
                if keep_depth:
                    import depth_guard
                    self.depth.append((depth_guard.filtered(d),
                                       None if live.T_wc is None
                                       else np.array(live.T_wc, float),
                                       depth_guard.person_grid(live.mask)))
        finally:
            live.close()
        times = np.array([f[0] for f in self.frames])
        step = float(np.median(np.diff(times))) if len(times) > 1 else 0.1
        self.span = float(times[-1] - times[0] + step) if len(times) else 0.0
        # The body a rig is judged against: posed, with the chair, resting.
        self.rest = next((b for (_t, ev, b) in self.frames
                          if "posed" in ev and b.chair), None)

    def seat_xy(self):
        return float(self.seat["xy"][0]), float(self.seat["xy"][1])


def layout_of(rel, seat):
    """Seat-relative arms -> world base poses."""
    sx, sy = float(seat["xy"][0]), float(seat["xy"][1])
    return [AL.base_pose(r, sx, sy) for r in rel]


def simulate(track, rel, loops=2, progress=None, deadline_s=None):
    """Run the arms of seat-relative rig `rel` over `track`. -> report dict.

    `progress(fraction)` is called now and then. `deadline_s` stops early.
    """
    arms = AL.Arms(rel=rel)
    n = len(rel)
    t_start = time.time()
    planned = adopted_at = None
    prev = [None] * n
    rep = {
        "arms": n, "frames": 0, "estop": False, "errors": [],
        "jumps": 0, "own_inside": 0, "worst_own_mm": float("inf"), "own_close": 0,
        "min_gap_mm": float("inf"), "frames_under_hold": 0,
        "passes": [], "stalled": False, "plan": None, "reach_cm2": {},
        "per_arm": {a: collections.Counter() for a in range(n)},
    }
    last_pass_t, total = 0.0, loops * len(track.frames)
    try:
        for lap in range(loops):
            for i, (t_rel, ev, body) in enumerate(track.frames):
                t = lap * track.span + t_rel
                if "seat" in ev and planned is None:
                    arms.place(track.seat)
                    arms.start_plan(body)
                    arms.worker.join()
                    planned = t
                line = arms.adopt()
                if line:
                    rep["plan"] = line.strip()
                    adopted_at = rep["frames"]
                    # What each arm could reach safely when the plan was made:
                    # what its passes are measured against.
                    rep["reach_cm2"] = {a: round(float(c.area[c.margin > 0.0].sum()) / 100.0, 1)
                                        for a, (c, _m) in arms.ctl.items()}
                before = ([np.asarray(j, float) for j in arms.joints]
                          if arms.gov is not None else prev)
                arms.step(body, t, hold="posed" not in ev)
                rep["frames"] += 1
                if rep["frames"] % 20 == 0:
                    arms.summary()              # the live view's panel text
                    if progress is not None:
                        progress(rep["frames"] / total)
                if arms.gov is None or arms.trees is None:
                    continue
                if arms.estopped:
                    rep["estop"] = True
                    return _finish(rep, arms, t_start)
                rep["min_gap_mm"] = min(rep["min_gap_mm"], arms.gap_now)
                rep["frames_under_hold"] += int(arms.gap_now < AL.D_HOLD_MM - 0.5)
                tree = arms.trees[None]
                for a in range(n):
                    j = np.asarray(arms.joints[a], float)
                    st = rep["per_arm"][a]
                    st[arms.mode.get(a, "parked")] += 1
                    if arms.waiting.get(a):
                        st["waiting"] += 1
                    moved = before[a] is not None and float(
                        np.max(np.abs(j[:3] - before[a][:3]))) > 1e-9
                    # A frame is up to MAX_SUBSTEPS checked steps; the arms
                    # count a step that turned too far themselves (jumped).
                    jump = before[a] is not None and float(
                        np.max(np.abs(j[:3] - before[a][:3]))) > \
                        AL.MAX_SWING_RAD * AL.MAX_SUBSTEPS + 1e-6
                    if jump and rep["frames"] - 1 != adopted_at:
                        rep["jumps"] += 1
                    if moved:
                        # The arm's own move, not the person moving onto it:
                        # against the body as it is now, the move ended
                        # inside it and closer than where it started.
                        fleet = arms.gov.fleet
                        states = [tuple(np.asarray(x, float)[:3]) for x in arms.joints]
                        gap_after = _gap(fleet, states, a)
                        states[a] = tuple(before[a][:3])
                        gap_before = _gap(fleet, states, a)
                        if gap_after < AL.D_HOLD_MM - 0.5 and \
                                gap_after < gap_before - FLEET.SEPARATING_MM:
                            rep["own_close"] += 1
                        after = fleet.link_clearance(a, tuple(j[:3]), tree, moving=True)
                        if after < 0.0:
                            was = fleet.link_clearance(a, tuple(before[a][:3]), tree,
                                                       moving=True)
                            if after < was - FLEET.SEPARATING_MM:
                                rep["own_inside"] += 1
                                rep["worst_own_mm"] = min(rep["worst_own_mm"], after)
                if len(arms.history) > len(rep["passes"]):
                    # cm2 under the sponges so far this pass, reachable or not,
                    # and the same counted by the share of the sweeps each had
                    got = sum(float(c.area[arms.tools[a].credited()].sum())
                              for a, (c, _m) in arms.ctl.items()) / 100.0
                    swept = sum(float((c.area * arms.tools[a].level()).sum())
                                for a, (c, _m) in arms.ctl.items()) / 100.0
                    for p, ph, tt, pr in arms.history[len(rep["passes"]):]:
                        rep["passes"].append({"pass": p, "phase": ph, "t": tt,
                                              "done": {int(k): float(v)
                                                       for k, v in pr.items()},
                                              "cm2": round(got, 1),
                                              "swept_cm2": round(swept, 1)})
                        last_pass_t = tt
                since = t - max(last_pass_t, planned or 0.0)
                if arms.phases and since > PASS_LIMIT_S:
                    rep["stalled"] = True
                    return _finish(rep, arms, t_start)
                if deadline_s is not None and time.time() - t_start > deadline_s:
                    return _finish(rep, arms, t_start)
    except Exception as exc:                            # noqa: BLE001
        import traceback
        rep["errors"].append("".join(traceback.format_exception(exc))[-2000:])
    return _finish(rep, arms, t_start)


def _gap(fleet, states, a):
    pairs = fleet.pair_distances(states)
    return min((d for (p, q), d in pairs.items() if a in (p, q)), default=float("inf"))


def _finish(rep, arms, t_start):
    rep["seconds"] = round(time.time() - t_start, 1)
    rep["jumps"] += arms.jumped
    rep["verdicts"] = dict(arms.verdicts)
    rep["phases"] = [list(p) for p in arms.phases]
    rep["home_note"] = arms.home_note
    rep["still"] = {int(a): why for a, why in arms.disabled.items()}
    rep["owned_cm2"] = {a: round(float(c.area.sum()) / 100.0, 1)
                        for a, (c, _m) in arms.ctl.items()}
    rep["per_arm"] = {a: dict(v) for a, v in rep["per_arm"].items()}
    # The turn the run ended inside, and how far its arms had got.
    rep["unfinished"] = None
    if arms.phases and arms.pause_for is None and arms.phase_i < len(arms.phases):
        prog = arms.progress()
        done = {int(a): round(prog[a][0], 3) for a in arms.phases[arms.phase_i]
                if a in prog}
        if done:
            rep["unfinished"] = {"pass": arms.passes, "phase": arms.phase_i + 1,
                                 "done": done}
    return rep


def summary(rep):
    """A few lines a person can read."""
    names = AL.NAMES
    out = []
    if rep["errors"]:
        out.append("CRASHED: " + rep["errors"][0].strip().splitlines()[-1])
    if rep["plan"]:
        out.append(rep["plan"])
    else:
        out.append("  no plan was made")
    for p in rep["passes"]:
        out.append(f"  pass {p['pass']} phase {p['phase']}: " + ", ".join(
            f"{names[a % 4]} {100 * v:.0f}%" for a, v in p["done"].items()))
    u = rep.get("unfinished")
    if u:
        out.append(f"  pass {u['pass']} phase {u['phase']}, when the run ended: " + ", ".join(
            f"{names[a % 4]} {100 * v:.0f}%" for a, v in u["done"].items()))
    for a, why in sorted(rep.get("still", {}).items()):
        out.append(f"  {names[a % 4]} stays still: {why}")
    gap = rep["min_gap_mm"]
    out.append(f"  closest two arms {gap / 10:.1f} cm; verdicts {rep['verdicts']}; "
               f"jumps {rep['jumps']}; own moves into the body {rep['own_inside']}"
               + (f" (worst {rep['worst_own_mm']:.0f} mm)" if rep["own_inside"] else "")
               + f"; own moves toward an arm {rep['own_close']}"
               + ("; EMERGENCY STOP" if rep["estop"] else "")
               + ("; A PASS STALLED" if rep["stalled"] else "")
               + f"; {rep['frames']} frames in {rep['seconds']}s")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("recording")
    ap.add_argument("rig", nargs="?", default=os.path.join(HERE, "live_rig.json"))
    ap.add_argument("--loops", type=int, default=2)
    ap.add_argument("--seat-mm", type=float, default=420.0)
    a = ap.parse_args()
    sys.argv = sys.argv[:1]
    t0 = time.time()
    track = Track(a.recording, a.seat_mm)
    print(f"followed {len(track.frames)} frames in {time.time() - t0:.0f}s", flush=True)
    with open(a.rig, encoding="utf-8") as f:
        rel = json.load(f)["arms"]
    print(summary(simulate(track, rel, loops=a.loops)))


if __name__ == "__main__":
    main()
