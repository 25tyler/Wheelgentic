"""The arms, wherever they are mounted: many rigs on one recording.

    python scrub3d/live/arms_anywhere.py RECORDING [--rigs 30] [--seed 1]
        [--loops 2] [--jobs 6] [--only NAME]

Random mounts all around the chair, behind it too, at any height and any
facing, half of them tipped, and the awkward rigs a person dragging arms
around will make: all on one side, all behind, facing away, far off, on the
floor, overhead, one inside the chair, bases stacked together, tipped,
rolled, on the walls, hanging from the ceiling. Each rig runs through
rig_sim.

A rig passes when nothing crashed, nothing stopped the fleet, no pass
stalled, no arm jumped, and no arm's own move went into the body. Coverage
is reported, not required: a bad rig scrubs little, and that is the rig's
fault, not the motion's.
"""
import argparse
import concurrent.futures as CF
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def arm(x, y, z, facing, tilt=0.0, roll=0.0):
    out = {"x_from_seat_mm": round(float(x), 1), "y_from_seat_mm": round(float(y), 1),
           "z_mm": round(float(z), 1), "facing_deg": round(float(facing), 1)}
    if round(float(tilt), 1):
        out["tilt_deg"] = round(float(tilt), 1)
    if round(float(roll), 1):
        out["roll_deg"] = round(float(roll), 1)
    return out


def toward_seat(x, y):
    return math.degrees(math.atan2(-y, -x))


def named_rigs():
    # The rig the live view uses now, and variations on it. A saved rig can
    # have fewer than four arms.
    with open(os.path.join(HERE, "live_rig.json"), encoding="utf-8") as f:
        searched = json.load(f)["arms"]
    out = {"saved": searched}
    out["row in front"] = [arm(650, y, 900, 180) for y in (-480, -160, 160, 480)]
    out["all on the left"] = [arm(x, 620, 850, toward_seat(x, 620))
                              for x in (-300, 0, 300, 600)]
    out["all behind"] = [arm(-550, y, 900, 0) for y in (-450, -150, 150, 450)]
    out["facing away"] = [dict(r, facing_deg=r["facing_deg"] + 180) for r in searched]
    out["far away"] = [arm(1600 * math.cos(a), 1600 * math.sin(a), 900,
                           toward_seat(1600 * math.cos(a), 1600 * math.sin(a)))
                       for a in np.radians([-60, -20, 20, 60])]
    out["on the floor"] = [dict(r, z_mm=150.0) for r in searched]
    out["overhead"] = [dict(r, z_mm=1500.0) for r in searched]
    out["one inside the chair"] = [arm(0, 0, 500, 0)] + searched[1:]
    out["stacked together"] = [arm(600 + 60 * k, 0, 900, 180) for k in range(4)]
    out["two pairs touching"] = [arm(600, -300, 900, 180), arm(600, -210, 900, 180),
                                 arm(600, 300, 900, 180), arm(600, 390, 900, 180)]
    out["close all round"] = [arm(330 * math.cos(a), 330 * math.sin(a), 800,
                                  toward_seat(330 * math.cos(a), 330 * math.sin(a)))
                              for a in np.radians([0, 90, 180, 270])]
    out["one arm, three far"] = [arm(550, 0, 900, 180)] + [
        arm(2500, y, 900, 180) for y in (-600, 0, 600)]
    # Bases that are not level: tipped toward the person or away, rolled,
    # screwed to the walls, hanging from the ceiling.
    out["tipped toward"] = [dict(r, tilt_deg=30.0) for r in searched]
    out["tipped away"] = [dict(r, tilt_deg=-30.0) for r in searched]
    out["rolled"] = [dict(r, roll_deg=35.0 * (-1) ** k) for k, r in enumerate(searched)]
    out["on the walls"] = [arm(x, y, 800, toward_seat(x, y), tilt=90.0)
                           for x, y in ((650, -560), (650, 560), (-150, -700), (-150, 700))]
    out["from the ceiling"] = [dict(r, z_mm=1450.0, roll_deg=180.0) for r in searched]
    return out


def random_rig(rng, tip=None):
    """Four random mounts. With `tip` (a second generator, so the mounts
    stay what the seed made them), half the rigs get bases tipped up to 45
    degrees each way, and one in ten any way at all."""
    out = []
    for _ in range(4):
        a = rng.uniform(-math.pi, math.pi)
        r = rng.uniform(250.0, 1100.0)
        x, y = r * math.cos(a), r * math.sin(a)
        z = rng.uniform(250.0, 1400.0)
        if rng.random() < 0.7:
            facing = toward_seat(x, y) + rng.uniform(-60.0, 60.0)
        else:
            facing = rng.uniform(-180.0, 180.0)
        out.append(arm(x, y, z, facing))
    if tip is not None:
        u = tip.random()
        span = 180.0 if u < 0.1 else 45.0 if u < 0.5 else 0.0
        for r in out:
            tilt, roll = tip.uniform(-span, span, 2)
            r.update({k: v for k, v in arm(0, 0, 0, 0, tilt, roll).items()
                      if k in ("tilt_deg", "roll_deg")})
    return out


def tipped(rel):
    """', tilt/roll a/b c/d' for the bases that are not level, else ''."""
    t = [f"{r.get('tilt_deg', 0):.0f}/{r.get('roll_deg', 0):.0f}" for r in rel
         if r.get("tilt_deg") or r.get("roll_deg")]
    return f"; tilt/roll {' '.join(t)}" if t else ""


def base_gap(rel):
    p = np.array([[r["x_from_seat_mm"], r["y_from_seat_mm"], r["z_mm"]] for r in rel])
    d = np.linalg.norm(p[:, None] - p[None], axis=2) + np.eye(len(p)) * 1e9
    return float(d.min())


_TRACK = None


def _init(recording, seat_mm, settings=()):
    global _TRACK
    sys.argv = sys.argv[:1]
    import arms_live as AL
    import rig_sim
    for name, value in settings:
        now = getattr(AL, name)
        setattr(AL, name, value.lower() in ("1", "true", "yes") if isinstance(now, bool)
                else type(now)(float(value)))
    _TRACK = rig_sim.Track(recording, seat_mm)


def _run(job):
    import rig_sim
    name, rel, loops = job
    rep = rig_sim.simulate(_TRACK, rel, loops=loops)
    rep["name"] = name
    rep["rel"] = rel
    rep["text"] = rig_sim.summary(rep)
    return rep


def verdict(rep):
    """-> list of what went wrong with the MOTION."""
    bad = []
    if rep["errors"]:
        bad.append("crashed")
    if rep["estop"]:
        bad.append("emergency stop")
    if rep["stalled"]:
        bad.append("a pass stalled")
    if rep["jumps"]:
        bad.append(f"{rep['jumps']} jumps")
    if rep["own_inside"]:
        bad.append(f"{rep['own_inside']} own moves into the body")
    if rep["own_close"]:
        bad.append(f"{rep['own_close']} own moves inside the hold line of an arm")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("recording")
    ap.add_argument("--rigs", type=int, default=30, help="random rigs, besides the named")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--loops", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 2)))
    ap.add_argument("--seat-mm", type=float, default=420.0)
    ap.add_argument("--only", help="run only the named rig (or 'random')")
    ap.add_argument("--out", help="write every report here as JSON")
    ap.add_argument("--level", action="store_true", help="random bases all level")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="an arms_live setting for these runs, e.g. GIVE_REST=0")
    a = ap.parse_args()

    settings = [tuple(kv.split("=", 1)) for kv in a.set]
    rng = np.random.default_rng(a.seed)
    tip = None if a.level else np.random.default_rng(a.seed + 7919)
    jobs = [(k, v, a.loops) for k, v in named_rigs().items()]
    jobs += [(f"random {i}", random_rig(rng, tip), a.loops) for i in range(a.rigs)]
    if a.only:
        jobs = [j for j in jobs if j[0] == a.only or (a.only == "random"
                                                      and j[0].startswith("random"))]
    # CHECK THE RECORDING BEFORE THE POOL, NOT INSIDE IT. _init opens it in
    # each worker, and a SystemExit raised in a ProcessPoolExecutor
    # INITIALIZER kills that worker before its message can travel back: the
    # parent sees only BrokenProcessPool, which names neither the folder nor
    # the reason. Replaying a missing recording therefore reported a pool
    # failure rather than "there is no recording", on the one tool most likely
    # to be run first. replay_source explains it properly; reach it from the
    # parent process where its message survives.
    if not os.path.isdir(a.recording):
        sys.path.insert(0, HERE)
        import live_body as LB
        LB.replay_source(a.recording)        # raises SystemExit with the why
    t0 = time.time()
    print(f"{len(jobs)} rigs, {a.jobs} at a time", flush=True)
    reports = []
    with CF.ProcessPoolExecutor(max_workers=a.jobs, initializer=_init,
                                initargs=(a.recording, a.seat_mm, settings)) as ex:
        for rep in ex.map(_run, jobs):
            bad = verdict(rep)
            reports.append(rep)
            print(f"\n== {rep['name']}: {'FAIL: ' + ', '.join(bad) if bad else 'ok'}"
                  f"  (closest base gap {base_gap(rep['rel']):.0f} mm"
                  f"{tipped(rep['rel'])})", flush=True)
            print(rep["text"], flush=True)
    failed = [r for r in reports if verdict(r)]
    print(f"\n{len(reports) - len(failed)} of {len(reports)} rigs ran cleanly "
          f"in {time.time() - t0:.0f}s", flush=True)
    for r in failed:
        print(f"  {r['name']}: {', '.join(verdict(r))}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(reports, f, indent=1, default=str)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
