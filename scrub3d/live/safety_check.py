"""scrub3d/live/safety_check.py -- the arms against a person whose body
model is wrong, on a recording.

    python scrub3d/live/safety_check.py [RECORDING] [RIG.json]
                                        [--faults all | name,name] [--loops 2]

The live view plans and checks every move against the body model it fits
to the camera. Here the arms run over a recorded person with that model
made wrong on purpose (FAULTS: moved, thinner, late, stuck, one limb out
of place, the person out of view), while the depth camera's measurements
stay true, as they would. Each arm is followed by what the real arm would
do: fake_esp32.ServoModel with the speed cap and acceleration arm_hw sets,
fed the way arm_hw paces it. What it reports reaches the live view
REPORT_LATE_S late, as a real arm's reading does. It cannot go through the
true person: its sponge gives GIVE_MM and then slides along the skin at
that depth, its structure stops at the surface (its capsules, which are
bigger than the arm, so it stops early), and its motors push at their
capped torque. The driver's
rules for that are applied as arm_hw applies them: an arm pushing on the
skin held PRESS_BLOCK_MM short, or one met off the skin and held
LOAD_OFF_MM off its path, holds the run (a held run is safe; it is shown).

Both the planned arm and the followed one are measured against the TRUE
model, and each time the sponge (a touch) or the arm's moving structure (a
brush) reaches the person, it is sorted:
  - an impact, if that part was FREE_MM or more clear of the person in the
    FREE_S before, and (for the sponge) the plan did not have it on the
    skin: it came at them through the air. Its speed is the fastest it
    closed on them in the last IMPACT_S (the arm's own motion, measured on
    one model, not the person's);
  - a slide, otherwise: it was on or at the skin already, and the rub or
    the stroke carried it across the curve of a limb that is not quite
    where the model has it;
and the deepest the sponge pressed and the structure went in, and what was
scrubbed, per pass, by plan and by the followed sponge.

A fault passes when the followed (real) arm makes no impact faster than
IMPACT_OK_MM_S (250 mm/s, what industrial standards call reduced speed),
its sponge presses no deeper than PRESS_OK_MM, and its structure goes no
deeper than INSIDE_OK_MM into the capsule model (which is bigger than the
arm). The planned arm's numbers are shown too: they are what was asked
of the real one.
Slides are counted and shown, not failed: the arm is already touching,
with its torque capped.
"""
import collections
import argparse
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(HERE)
for p in (WT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import arm_hw as HW                             # noqa: E402
import arms_live as AL                          # noqa: E402
import depth_guard as DG                        # noqa: E402
import fake_esp32 as FE                         # noqa: E402
import rig_sim                                  # noqa: E402

COL, K = AL.COL, AL.K
IMPACT_OK_MM_S = 250.0
FREE_MM = 10.0
FREE_S = 0.5
IMPACT_S = 0.15
PRESS_OK_MM = 20.0
INSIDE_OK_MM = 15.0
TICK_S = 1.0 / HW.RATE_HZ
REPORT_LATE_S = 0.04
# A recording played again starts the person over: their model jumps from
# where they ended to where they began, which no person does. Nothing is
# counted against the arms for this long after a seam.
SEAM_S = 1.5
GIVE_MM = 8.0


def moved(T, d):
    T = np.array(T, float)
    T[:3, 3] += d
    return T


def shifted(body, d, parts=None):
    """The model with every part (or `parts`) moved by `d` world mm."""
    s = rig_sim.snapshot(body)
    d = np.asarray(d, float)
    s.T = {k: (moved(T, d) if parts is None or k in parts else T) for k, T in body.T.items()}
    if parts is None and getattr(body, "head_base", None) is not None:
        s.head_base = np.asarray(body.head_base, float) + d
    return s


def thinner(body, mm):
    """The model `mm` thinner all over than the person."""
    s = rig_sim.snapshot(body)
    s.parts = {}
    for k, p in body.parts.items():
        q = dict(p)
        q["V"] = (np.asarray(p["V"], float) - np.asarray(p["N"], float) * mm).astype(np.float32)
        q["cells"] = (np.asarray(p["cells"], float)
                      - np.asarray(p["cnrm"], float) * mm).astype(np.float32)
        s.parts[k] = q
    return s


def faults():
    """name -> (what it is, seen(frames, i, t) -> (body, posed?))"""
    out = {}

    def fixed(fn):
        return lambda frames, i, t: (fn(frames[i][2]), "posed" in frames[i][1])

    out["none"] = ("the model is right", fixed(lambda b: b))
    for axis, word in ((0, "toward the camera"), (1, "to the person's left"),
                       (2, "up")):
        for mm in (30.0, -30.0, 60.0, -60.0):
            d = np.zeros(3)
            d[axis] = mm
            way = word if mm > 0 else {"toward the camera": "away from the camera",
                                       "to the person's left": "to the person's right",
                                       "up": "down"}[word]
            out[f"moved_{'xyz'[axis]}{mm:+.0f}"] = (
                f"the whole model {abs(mm) / 10:.0f} cm {way} of the person",
                fixed(lambda b, d=d: shifted(b, d)))
    out["thinner_20"] = ("the model 2 cm thinner than the person",
                         fixed(lambda b: thinner(b, 20.0)))
    for part, word in (("forearm_L", "left forearm"), ("forearm_R", "right forearm")):
        out[f"{part}_off"] = (f"the {word} 8 cm off, outward and up",
                              fixed(lambda b, part=part: shifted(
                                  b, (40.0, 60.0 if part.endswith("L") else -60.0, 40.0),
                                  parts={part, part.replace("forearm", "hand")})))

    def late(frames, i, t, lag=0.4):
        j = i
        while j > 0 and frames[i][0] - frames[j][0] < lag:
            j -= 1
        return frames[j][2], "posed" in frames[j][1]

    out["late_0.4s"] = ("the model 0.4 s behind the person", late)

    def stuck(frames, i, t, after=12.0):
        j = i
        if frames[i][0] > after:
            j = max(k for k in range(len(frames)) if frames[k][0] <= after)
        return frames[j][2], "posed" in frames[j][1]

    out["stuck_12s"] = ("the model stops following the person after 12 s", stuck)

    def dropout(frames, i, t):
        posed = "posed" in frames[i][1] and (t % 8.0) > 1.5
        return frames[i][2], posed

    out["out_of_view"] = ("the person out of view 1.5 s in every 8", dropout)
    return out


class Follower:
    """What one real arm does: arm_hw's pacing into fake_esp32's servos."""

    def __init__(self, T, joints):
        self.T = np.asarray(T, float)
        self.Tinv = np.linalg.inv(self.T)
        self.servo = FE.ServoModel(joints, speed=math.radians(HW.SERVO_SPD_DEG),
                                   accel=FE.reg_acc(round(HW.SERVO_ACC * 4096 / 360)))
        self.sent = np.array(K.fk(*joints), float)
        self.fed = None
        self.v_fed = 0.0
        n = max(1, int(round(REPORT_LATE_S / TICK_S)))
        self.past = [tuple(float(v) for v in joints)] * (n + 1)
        self.pushed_since = None        # held against the person, since
        self.last_off = 0.0

    def feed(self, world, dt):
        p = (self.Tinv @ np.r_[np.asarray(world, float), 1.0])[:3]
        if self.fed is not None and dt > 1e-6:
            self.v_fed = 0.5 * self.v_fed + 0.5 * float(np.linalg.norm(p - self.fed)) / dt
        self.fed = p

    def tick(self, truth, t, stroking):
        """One pump period. -> why arm_hw would hold now, or None"""
        d = float(np.linalg.norm(self.fed - self.sent))
        if d >= 0.2:
            pace = min(HW.MAX_STEP_MM, max(self.v_fed * HW.PACE_OVER, HW.MIN_FEED_V) * TICK_S)
            step = self.sent + (self.fed - self.sent) * min(1.0, pace / d)
            if HW.solvable(step):
                self.sent = step
                self.servo.goal = np.array(K.ik(*step), float)
        q0 = self.servo.q.copy()
        g0, c0 = truth.gaps(self.T, tuple(q0))
        self.servo.step(TICK_S)
        q1 = self.servo.q.copy()
        g1, c1 = truth.gaps(self.T, tuple(q1))
        blocked = False
        if g1 < -GIVE_MM and g1 < g0:
            # the sponge is squeezed as far as it goes: it slides on the skin
            slid = truth.slide(self.T, q1, COL.R_SPONGE - GIVE_MM)
            if slid is not None:
                q1 = np.asarray(slid, float)
                self.servo.q = q1.copy()
                g1, c1 = truth.gaps(self.T, tuple(q1))
                blocked = True
        if (g1 < -GIVE_MM - 1.0 and g1 < g0) or (c1 < 0.0 and c1 < c0):
            # held by the person: as far along as it goes
            lo, hi = 0.0, 1.0
            for _ in range(10):
                mid = (lo + hi) / 2
                gm, cm = truth.gaps(self.T, tuple(q0 + (q1 - q0) * mid))
                if (gm < -GIVE_MM and gm < g0) or (cm < 0.0 and cm < c0):
                    hi = mid
                else:
                    lo = mid
            self.servo.q = q0 + (q1 - q0) * lo
            self.servo.qd[:] = 0.0
            blocked = True
        self.past = self.past[1:] + [self.joints()]
        off = float(np.linalg.norm(np.asarray(K.fk(*self.servo.q)) - self.sent))
        pushing = blocked or (self.pushed_since is not None and off > self.last_off - 0.5)
        self.last_off = off
        if not pushing:
            self.pushed_since = None
            return None
        if self.pushed_since is None:
            self.pushed_since = t
        held = t - self.pushed_since
        if stroking and off > HW.PRESS_BLOCK_MM and held > HW.PRESS_BLOCK_S:
            return f"pressing on the skin but held {off:.0f} mm short (it met the person)"
        if not stroking and off > HW.LOAD_OFF_MM and held > 2 * HW.LOAD_S:
            return f"stuck {off:.0f} mm off its path, off the skin (it met the person)"
        return None

    def reported(self):
        """What the live view has from this arm now: joints, world tool point."""
        j = self.past[0]
        return j, (self.T @ np.r_[K.fk(*j), 1.0])[:3]

    def joints(self):
        return tuple(float(v) for v in self.servo.q)

    def tool(self):
        return (self.T @ np.r_[K.fk(*self.servo.q), 1.0])[:3]


class Measure:
    """Distances to the true model."""

    def __init__(self, body):
        import open3d as o3d
        V, F = AL.body_mesh(body)
        self.o3d = o3d
        self.scene = o3d.t.geometry.RaycastingScene()
        self.scene.add_triangles(o3d.core.Tensor(V.astype(np.float32)),
                                 o3d.core.Tensor(F.astype(np.uint32)))

    def dist(self, P):
        P = np.asarray(P, np.float32).reshape(-1, 3)
        return self.scene.compute_distance(self.o3d.core.Tensor(P)).numpy()

    def slide(self, T, joints, depth):
        """The joints that put the sponge's centre `depth` mm out from the
        skin nearest it, on the same side. -> joints or None"""
        T = np.asarray(T, float)
        tcp = (T @ np.r_[K.fk(*tuple(joints)[:3]), 1.0])[:3]
        q = self.o3d.core.Tensor(tcp[None].astype(np.float32))
        near = self.scene.compute_closest_points(q)["points"].numpy()[0].astype(float)
        out = tcp - near
        n = float(np.linalg.norm(out))
        if n < 1e-6:
            return None
        goal = near + out / n * depth
        return K.ik(*(np.linalg.inv(T) @ np.r_[goal, 1.0])[:3])

    def gaps(self, T, joints):
        """-> (sponge gap, structure clearance) mm, to the true model"""
        tcp = (np.asarray(T, float) @ np.r_[K.fk(*joints[:3]), 1.0])[:3]
        caps = COL.moving_capsules(T, *joints[:3])
        tt = np.linspace(0.0, 1.0, DG.SAMPLES)[:, None]
        pts = np.concatenate([tcp[None]] + [p + (q - p) * tt for p, q in caps])
        d = self.dist(pts)
        radii = np.asarray(COL.CAPSULE_RADII)[list(COL.MOVING)]
        c = (d[1:].reshape(len(caps), DG.SAMPLES).min(axis=1) - radii).min()
        return float(d[0] - COL.R_SPONGE), float(c)


def run(track, rel, name, seen_fn, loops=2, log=print):
    frames = track.frames
    arms = AL.Arms(rel=rel)
    arms.driving = True
    n = len(rel)
    followers = None
    hist = {}
    rep = {"fault": name, "stops": [], "touch": [], "brush": [],
           "touch_slide": [], "brush_slide": [],
           "press": {"plan": 0.0, "real": 0.0}, "inside": {"plan": 0.0, "real": 0.0},
           "lag": [], "passes": [], "distrusted": set()}
    truth = None
    prev = {}                                      # (who, arm) -> (joints, T) before the step
    t_prev = None
    planned = False
    for lap in range(loops):
        for i, (t_rel, ev, true_body) in enumerate(frames):
            t = lap * track.span + t_rel
            seen, posed = seen_fn(frames, i, t)
            grid, T_wc, person = track.depth[i]
            if "seat" in ev and not planned:
                arms.place(track.seat)
                arms.start_plan(seen)
                arms.worker.join()
                planned = True
            if T_wc is not None and arms.layout:
                if followers is not None:
                    real_j = {a: f.reported()[0] for a, f in enumerate(followers)}
                    real_p = {a: f.reported()[1] for a, f in enumerate(followers)}
                else:
                    real_j = {a: tuple(j) for a, j in enumerate(arms.joints)}
                    real_p = {a: p for a, p in enumerate(arms.sponge)}
                scene = DG.Scene(None, track.intr, T_wc, arms.layout, real_j, real_p,
                                 grid=grid, person=person)
                arms.sense(scene, seen if posed else None)
            if arms.adopt() is not None and followers is None:
                followers = [Follower(arms.layout[a], arms.joints[a]) for a in range(n)]
            arms.step(seen, t, hold=not posed)
            rep["distrusted"] |= set(arms.distrust)
            if followers is None or arms.gov is None:
                t_prev = t
                continue
            dt = 0.0 if t_prev is None else max(t - t_prev, 0.0)
            t_prev = t
            if true_body.T:
                truth = Measure(true_body)
            if truth is None:
                continue
            if lap and t - lap * track.span < SEAM_S:
                t_prev = t                       # the seam of the replay
                continue
            ticks = max(1, int(round(dt / TICK_S)))
            for a, f in enumerate(followers):
                f.feed(arms.sponge[a], dt)
            held = None
            for _k in range(ticks):
                for a, f in enumerate(followers):
                    j0 = f.joints()
                    tick_t = t - dt + (_k + 1) * TICK_S
                    why = f.tick(truth, tick_t, stroking(arms, a))
                    check(rep, hist, truth, ("real", a), arms.layout[a], j0, f.joints(),
                          TICK_S, tick_t, stroking(arms, a))
                    if why and held is None:
                        held = f"{AL.NAMES[a % 4]}: {why}"
            if held:
                rep["stops"].append((round(t, 1), held))
                return finish(rep, arms)
            for a, f in enumerate(followers):
                rep["lag"].append(float(np.linalg.norm(f.tool() - arms.sponge[a])))
            for a in range(n):
                j1 = tuple(arms.joints[a])
                j0 = prev.get(("plan", a), j1)
                check(rep, hist, truth, ("plan", a), arms.layout[a], j0, j1, dt, t,
                      stroking(arms, a))
                prev[("plan", a)] = j1
            why = arms.real_frame({a: f.reported()[0] for a, f in enumerate(followers)},
                                  {a: f.reported()[1] for a, f in enumerate(followers)})
            if why:
                rep["stops"].append((round(t, 1), why))
                return finish(rep, arms)
            if arms.estopped:
                rep["stops"].append((round(t, 1), arms.status))
                return finish(rep, arms)
            if arms.pause_for is not None and (not rep["passes"]
                                               or rep["passes"][-1][0] != arms.passes):
                plan = arms.progress()
                real = arms.progress(real=True)
                reach = sum(r for _f, _o, r in plan.values())
                if reach >= 1.0:
                    rep["passes"].append((arms.passes, round(t, 1),
                                          sum(f_ * r for f_, _o, r in plan.values()) / reach,
                                          sum(f_ * r for f_, _o, r in real.values()) / reach))
    return finish(rep, arms)


def check(rep, hist, truth, who, T, j0, j1, dt, t, stroking=False):
    """Sort each time the sponge or the structure reaches the person (see
    the module notes); closing speeds on this frame's true model.
    `stroking`: the plan has this arm's sponge on the skin."""
    if dt <= 0:
        return
    g0, c0 = truth.gaps(T, j0)
    g1, c1 = truth.gaps(T, j1)
    rep["press"][who[0]] = min(rep["press"][who[0]], g1)
    rep["inside"][who[0]] = min(rep["inside"][who[0]], c1)
    h = hist.setdefault(who, collections.deque())
    h.append((t, g0, (g0 - g1) / dt, c0, (c0 - c1) / dt))
    while h and t - h[0][0] > FREE_S:
        h.popleft()
    for key, before, after, gi, vi in (("touch", g0, g1, 1, 2), ("brush", c0, c1, 3, 4)):
        if before >= 0.0 > after:
            free = max(x[gi] for x in h) >= FREE_MM and not (key == "touch" and stroking)
            speed = max(x[vi] for x in h if t - x[0] <= IMPACT_S)
            rep[key if free else key + "_slide"].append((who, speed))


def stroking(arms, a):
    tl = arms.tools.get(a)
    return bool(tl is not None and tl.contact and arms.mode.get(a) == "working")


def finish(rep, arms):
    rep["slowed"] = int(sum(arms.slowed.values()))
    rep["refused_by_camera"] = int(sum(v for (a, mode, act, reason), v in arms.why.items()
                                       if reason == "the camera sees something there"))
    rep["distrusted"] = sorted(rep["distrusted"])
    fast = [v for w, v in rep["touch"] + rep["brush"]
            if w[0] == "real" and v > IMPACT_OK_MM_S]
    rep["ok"] = (not fast and -rep["press"]["real"] <= PRESS_OK_MM
                 and -rep["inside"]["real"] <= INSIDE_OK_MM)
    lag = np.array(rep["lag"]) if rep["lag"] else np.zeros(1)
    rep["lag"] = [round(float(np.median(lag)), 1), round(float(np.percentile(lag, 95)), 1)]
    return rep


def line(rep, what):
    def worst(key, who):
        v = [s for w, s in rep[key] if w[0] == who]
        return f"{len(v)}, fastest {max(v, default=0):.0f} mm/s"

    passes = "; ".join(f"pass {p} at {t:.0f} s: plan {100 * a:.0f}%, real {100 * b:.0f}%"
                       for p, t, a, b in rep["passes"]) or "no pass finished"
    stops = "; ".join(f"{t:.0f} s: {w}" for t, w in rep["stops"])
    out = [f"{'PASS' if rep['ok'] else 'FAIL'} {rep['fault']}: {what}"]
    for who in ("plan", "real"):
        out.append(f"    {who}: impacts, sponge {worst('touch', who)}, arm "
                   f"{worst('brush', who)}; slides, sponge {worst('touch_slide', who)}, "
                   f"arm {worst('brush_slide', who)}; deepest sponge "
                   f"{-rep['press'][who]:.0f} mm, arm {max(0.0, -rep['inside'][who]):.0f} mm")
    out.append(f"    the real arm behind the plan: median {rep['lag'][0]:.0f} mm, 95% under "
               f"{rep['lag'][1]:.0f}; slowed {rep['slowed']} steps; "
               f"{rep['refused_by_camera']} refused by the camera's points; parts not "
               f"trusted: {rep['distrusted'] or 'none'}")
    out.append(f"    {passes}")
    if stops:
        out.append(f"    held: {stops}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("recording", nargs="?",
                    default=os.path.join(WT, "data", "live_rec_20260917"))
    ap.add_argument("rig", nargs="?", default=os.path.join(HERE, "live_rig.json"))
    ap.add_argument("--faults", default="all")
    ap.add_argument("--loops", type=int, default=2)
    ap.add_argument("--seat-mm", type=float, default=420.0)
    ap.add_argument("--json", default=None, help="write the results here too")
    a = ap.parse_args()
    with open(a.rig, encoding="utf-8") as f:
        rel = json.load(f)["arms"]
    table = faults()
    names = list(table) if a.faults == "all" else a.faults.split(",")
    t0 = time.time()
    track = rig_sim.Track(a.recording, a.seat_mm, keep_depth=True)
    print(f"followed {len(track.frames)} frames in {time.time() - t0:.0f}s", flush=True)
    results = []
    for name in names:
        what, fn = table[name]
        t1 = time.time()
        rep = run(track, rel, name, fn, loops=a.loops)
        rep["seconds"] = round(time.time() - t1)
        results.append(rep)
        print(line(rep, what), flush=True)
    bad = [r["fault"] for r in results if not r["ok"]]
    print(f"\n{len(results) - len(bad)} of {len(results)} faults pass"
          + (f"; failing: {', '.join(bad)}" if bad else ""), flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1, default=str)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
