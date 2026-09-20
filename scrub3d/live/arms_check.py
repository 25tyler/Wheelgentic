"""Run the arms on a recording with no viewer and report what the governor saw.

    python scrub3d/live/arms_check.py RECORDING [RIG|-] [LOOPS]

RIG is a seat-relative rig (default: live_rig.json beside this file); "-"
uses the project's config.json. Prints every verdict the governor gave, why,
the closest two arms ever came, and what each arm scrubbed in each phase.
"""
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

folder = sys.argv[1]
rig = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "live_rig.json")
rig = None if rig == "-" else rig
loops = int(sys.argv[3]) if len(sys.argv) > 3 else 1
sys.argv = sys.argv[:1]
import json  # noqa: E402

import live_body as LB  # noqa: E402

intr, frames = LB.replay_source(folder, loop=loops > 1)
live = LB.Live(intr, 420.0)
arms = (LB.Arms(rel=json.load(open(rig))["arms"]) if rig
        else LB.Arms(layout=LB.rigconfig.load().layout()))
n_frames = 0
total = 220 * loops
gaps, t0, last = [], time.time(), 0.0
for i, (c, d, t) in enumerate(frames):
    if i >= total:
        break
    ev = live.step(c, d, t)
    if "seat" in ev:
        arms.place(live.seat)
        arms.start_plan(live.body)
        arms.worker.join()
    line = arms.adopt()
    if line:
        print(line, flush=True)
    arms.step(live.body, t, hold="posed" not in ev)
    if arms.gov is not None:
        gaps.append(arms.gap_now)
    n_frames += 1
    if time.time() - last > 15:
        last = time.time()
        print(f"  frame {i}: {arms.summary().splitlines()[0][:260]}", flush=True)
g = np.array([x for x in gaps if np.isfinite(x)])
print(f"{n_frames} frames in {time.time() - t0:.0f}s")
print("verdicts:", dict(arms.verdicts))
if len(g):
    print(f"closest two arms: min {g.min():.0f} mm, 1st percentile {np.percentile(g, 1):.0f} mm")
for k, v in sorted(arms.why.items(), key=lambda kv: -kv[1]):
    mm = arms.why_mm.get(k)
    print(f"  arm {k[0]} {k[1]:9s} {k[2]:7s} x{v:4d}  {k[3]}"
          + (f"  (clearance median {np.median(mm):.0f} mm, min {min(mm):.0f})" if mm else ""))
for ps, ph, tt, pr in arms.history:
    print(f"  pass {ps} phase {ph} ended at {tt - arms.history[0][2]:5.1f}s: "
          + ", ".join(f"{LB.ARM_NAMES[k]} {100 * v:.0f}%" for k, v in pr.items()))
print(arms.summary())
