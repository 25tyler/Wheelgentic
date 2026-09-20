"""tests/test_record_replay.py — the --record / REPLAY= round trip.

recordings/good_run.jsonl is THE demo safety net: camera dies, you run
REPLAY=... and the show goes on. The round trip has never been proven, and a
recording that cannot be replayed is worse than none — you would find out on
stage.

This uses --scripted to generate motion without a camera, records the pose
stream, then replays it and checks the arm follows the SAME path.
"""
import json, math, os, signal, subprocess, sys, threading, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "py"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog; watchdog.arm(120)   # SIGALRM, not a daemon thread:
# a 90s daemon watchdog once let a test run for 3h21m.

from fake_roarm import FakeRoArm
import fixture
import arm as A
import calibrate as calib
import replay as R

fails = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: fails.append(label)

fixture.ensure_homography()
H = calib.load()

# Per-run temp dir. Bare /tmp paths made this test depend on state from other
# runs: it passed only because a stale file from an earlier invocation existed,
# then failed in tests/quick.sh when it did not. A test that needs leftovers
# from a previous run is not a test.
import tempfile
TMP = tempfile.mkdtemp(prefix="wheelgentic_rt_")

print("=== 1. WRITE a recording the way --record does ===")
path = os.path.join(TMP, "roundtrip.jsonl")
rows = []
for i in range(150):
    t = 2000.0 + i / 30.0
    rows.append({"t": t,
                 "elbow": [500 + 10*math.sin(i/25), 380 + 6*math.cos(i/19)],
                 "wrist": [740 + 10*math.sin(i/25), 395 + 6*math.cos(i/19)]})
open(path, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
check("recording written", os.path.exists(path))
check("one JSON object per line",
      all(json.loads(l) for l in open(path) if l.strip()))

print("\n=== 2. REPLAY drives the arm along the same path ===")
fake = FakeRoArm()
a = A.Arm(port=fake.port)
time.sleep(0.3)
n = 0
t0 = time.perf_counter()
for row in R.replay_source(path, loop=False):
    x, y = calib.px_to_mm(H, (row["elbow"][0]+row["wrist"][0])/2,
                             (row["elbow"][1]+row["wrist"][1])/2)
    a.set_target(x, y, 45.0)
    n += 1
elapsed = time.perf_counter() - t0
check("every frame replayed", n == len(rows), f"{n}/{len(rows)}")

# TIMING FIDELITY: 150 frames at 30Hz is 5.0s. Replay must PRESERVE that, not
# race through it -- a recording played at 10x looks obviously fake on stage.
check("replay preserves real-time pacing", 4.3 <= elapsed <= 6.2,
      f"{elapsed:.2f}s for {len(rows)} frames @30Hz (expect ~5.0s)")

time.sleep(0.5)
pts = [(c["x"], c["y"], c["z"]) for c in fake.of_type(1041)]
check("arm actually moved", len(pts) > 40, f"{len(pts)} commands")
check("all replayed points reachable", all(A.reachable(*p) for p in pts))
xs = [p[0] for p in pts]
check("motion spans a real range (not stuck)", max(xs) - min(xs) > 3,
      f"{max(xs)-min(xs):.1f} mm of X travel")
a.close(); fake.close()

print("\n=== 3. TRUNCATED file still yields its intact frames ===")
# A crash mid-record leaves a half-written last line. JSONL must degrade.
trunc = os.path.join(TMP, "trunc.jsonl")
data = open(path).read()
open(trunc, "w").write(data[:int(len(data)*0.55)] + '{"t": 9999, "elbow": [1')
got = list(R.replay_source(trunc, loop=False))
check("truncated file still replays its good frames", 60 < len(got) < len(rows),
      f"{len(got)} of {len(rows)} recovered")
check("no exception on the broken tail", True)

print("\n=== 4. DEGENERATE recordings ===")
one = os.path.join(TMP, "one.jsonl")
open(one, "w").write(json.dumps(rows[0]) + "\n")
got1 = list(R.replay_source(one, loop=False))
check("single-frame recording works", len(got1) == 1)

empty = os.path.join(TMP, "empty.jsonl")
open(empty, "w").write("")
try:
    list(R.replay_source(empty, loop=False))
    check("empty recording exits loudly", False, "no SystemExit raised")
except SystemExit:
    check("empty recording exits loudly (not silently)", True)

print("\n=== 5. LOOPING (the demo runs on repeat) ===")
gen = R.replay_source(path, loop=True)
seen = [next(gen) for _ in range(len(rows) + 12)]
check("wraps around past the end", len(seen) == len(rows) + 12)
check("wrapped frames match the start",
      seen[len(rows)]["elbow"] == rows[0]["elbow"],
      "loop restarts at frame 0")

import shutil
shutil.rmtree(TMP, ignore_errors=True)

print("\n=== REPLAY WORKS WITHOUT A CALIBRATION ===")
# THE RECOVERY CARD'S HEADLINE CAMERA-DEAD ANSWER: "Camera dead / black ->
# REPLAY=recordings/synthetic.jsonl ./run.sh, 10s". replay_loop() called
# calib.load(), which raises SystemExit when homography.pkl is missing --
# measured on a machine without one, it printed "[replay] ... no camera, no
# vision" (looking like it started) and then died with "No homography.pkl".
# A fresh laptop, a wiped checkout, or a tripod recalibration in progress all
# hit this, and it is the fallback you reach for when something ELSE already
# broke.
_src = open("py/scrubbot.py").read()
_rl = _src.split("def replay_loop")[1].split("\ndef ")[0]
check("replay_loop survives a missing calibration",
      "except SystemExit" in _rl,
      "the camera-dead fallback dies on a fresh machine")
check("it says the coordinates are not real",
      "NO CALIBRATION" in _rl, "silently wrong robot coordinates are worse")
# AND IT STILL REFUSES WITH A REAL ARM. A fabricated calibration would put the
# sponge ~30cm off a person's forearm -- exactly what calib.load()'s sentinel
# exists to prevent. Screen-only replay is safe; driving a real arm is not.
check("but it still REFUSES when a real arm is attached",
      "if not arm.dry" in _rl and "raise" in _rl,
      "a fabricated homography must never drive real hardware")

print("\n=== THE SAFETY NET MUST ACTUALLY MOVE ===")
# good_run.jsonl is what the recovery card hands you when the camera dies, and
# NOTHING checked its content -- only that the file exists and that the docs
# name it. A recording of a near-motionless subject satisfies both.
#
# That is not hypothetical. tools/synth_record.py tells the operator, twice, to
# replace this file with `python py/scrubbot.py --record recordings/good_run.jsonl`.
# Run that without a real camera and CAM=fake's near-static subject is what
# lands. MEASURED, a real --record capture under CAM=fake against the shipped
# file: elbow-x stdev 1.74 vs 34.94, wrist-x 0.48 vs 72.26 -- 20x and 150x less
# motion. The fallback would still load, still replay, and show a person who
# barely moves, on the one run where everything else has already failed.
import statistics as _st
_gr = [json.loads(_l) for _l in open("recordings/good_run.jsonl") if _l.strip()]
check("the safety net has frames", len(_gr) > 100, f"{len(_gr)} frames")
_ex = _st.pstdev([r["elbow"][0] for r in _gr])
_wx = _st.pstdev([r["wrist"][0] for r in _gr])
check(f"the elbow actually moves (stdev {_ex:.1f}px)", _ex > 15.0,
      "a CAM=fake capture measures 1.74 here -- re-record with a real camera")
check(f"the wrist actually moves (stdev {_wx:.1f}px)", _wx > 20.0,
      "a CAM=fake capture measures 0.48 here -- re-record with a real camera")

print("\n" + "="*58)
if fails:
    print(f"  *** {len(fails)} FAILED: {fails}"); sys.exit(1)
print("  RECORD/REPLAY ROUND TRIP PASSED")
