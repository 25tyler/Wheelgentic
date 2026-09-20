"""Verify the calibration guards ACTUALLY fire. A guard that has never been
seen to fail is not a guard.

THIS FILE ITSELF HAD THE BUG IT EXISTS TO CATCH. It printed
"*** FAIL: guard did NOT fire" and then exited 0, so the runner reported PASS
while the corner-order guard was fully disabled. Found by an adversarial
audit and reproduced by planting the bug. Every check now records a failure
and the script exits non-zero.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import watchdog; watchdog.arm(60)   # SIGALRM: a daemon-thread
# watchdog once let a test run for 3h21m and block a whole suite.
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","py"))

FAILS = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(label)
import numpy as np, cv2
import calibrate as C

# Synthetic: a camera looking obliquely at the A4 sheet. Build plausible
# pixel corners for a 1280x720 frame viewing the sheet at ~65 degrees.
GOOD_PX = [[420.0, 210.0], [880.0, 225.0], [960.0, 520.0], [340.0, 505.0]]

print("=== TEST 1: correct click order should SOLVE ===")
H = C.solve(GOOD_PX)
# round-trip a known corner
mm = C.px_to_mm(H, *GOOD_PX[0])
print(f"  TL pixel -> {mm[0]:.2f}, {mm[1]:.2f} mm (expect 150.0, -105.0)")
check("TL pixel maps to the sheet's TL in mm",
      abs(mm[0]-150.0) < 0.01 and abs(mm[1]-(-105.0)) < 0.01,
      f"{mm[0]:.2f},{mm[1]:.2f}")
print()

print("=== TEST 2: SWAPPED corner order (TL,TR,BL,BR) must be CAUGHT ===")
swapped = [GOOD_PX[0], GOOD_PX[1], GOOD_PX[3], GOOD_PX[2]]
fired = False
try:
    C.solve(swapped, verbose=False)
except AssertionError as e:
    fired = True
    print(f"  guard fired: {str(e)[:80]}")
check("corner-order guard REJECTS a swapped click order", fired,
      "a real mis-click would ship silently")
print()

print("=== TEST 3: what the swapped solve would have produced, unguarded ===")
Hbad = cv2.getPerspectiveTransform(np.float32(swapped), C.WORLD_MM)
ctr_px = np.float32(GOOD_PX).mean(axis=0)
bad = cv2.perspectiveTransform(np.float32([[ctr_px]]), Hbad).ravel()
good = cv2.perspectiveTransform(np.float32([[ctr_px]]), H).ravel()
print(f"  correct order: centre -> {good[0]:8.1f}, {good[1]:8.1f} mm")
print(f"  swapped order: centre -> {bad[0]:8.1f}, {bad[1]:8.1f} mm")
print(f"  error magnitude: {np.hypot(*(bad-good)):.0f} mm  <-- silent without the guard\n")

print("=== TEST 4: a mis-clicked corner (30px off) ===")
misclick = [list(p) for p in GOOD_PX]; misclick[2][0] += 30
try:
    Hm = C.solve(misclick, verbose=False)
    err = abs(C.px_to_mm(Hm, *GOOD_PX[2])[0] - (150.0+297))
    print(f"  residual guard passed (4-pt solve is always exact) — "
          f"true position error {err:.1f} mm")
    print("  NOTE: residual guard canNOT catch a mis-click with exactly 4 points.")
    print("  It only catches numerical failure. Corner-ORDER guard is the real one.")
except AssertionError as e:
    print(f"  guard fired: {str(e)[:70]}")


print("\n=== TEST 5: ILL-CONDITIONED CLICK GEOMETRY (grafted from handeye) ===")
# scrub3d/handeye.py refuses a fit whose SAMPLES are badly spread however good
# its residual looks. A 4-point homography has the same disease and worse: its
# residual is zero by construction. These three click sets all pass GUARD 1
# (0.000000 residual) and GUARD 2 (centre lands in the box) and used to be
# accepted in total silence, while turning one pixel of hand tremor into tens
# of millimetres on somebody's forearm.
EDGE_ON = [[400.0, 300.0], [700.0, 308.0], [705.0, 318.0], [405.0, 310.0]]
TINY = [[640.0, 360.0], [660.0, 361.0], [661.0, 371.0], [641.0, 370.0]]

s_good = C.sensitivity(GOOD_PX)
s_edge = C.sensitivity(EDGE_ON)
s_tiny = C.sensitivity(TINY)
print(f"  good quad     {s_good:6.2f} mm/px")
print(f"  edge-on quad  {s_edge:6.2f} mm/px")
print(f"  tiny quad     {s_tiny:6.2f} mm/px")
check("a good click set reads well under the warn band",
      s_good < C.SENS_WARN_MM_PER_PX, f"{s_good:.2f} mm/px")
check("an edge-on click set is flagged", s_edge >= C.SENS_WARN_MM_PER_PX,
      f"{s_edge:.2f} mm/px -- 1px of tremor is tens of mm on a forearm")
check("a too-small click set is flagged", s_tiny >= C.SENS_WARN_MM_PER_PX,
      f"{s_tiny:.2f} mm/px -- shape looks fine, scale does not")

# The residual genuinely cannot see any of this. Prove it rather than assert
# it, because this is the whole reason the guard had to be a separate check.
_res = []
for _px in (GOOD_PX, EDGE_ON, TINY):
    _H = cv2.getPerspectiveTransform(np.float32(_px), C.WORLD_MM)
    _b = cv2.perspectiveTransform(np.float32(_px).reshape(1, 4, 2), _H)
    _res.append(float(np.abs(_b.reshape(4, 2) - C.WORLD_MM).max()))
print(f"  residual on all three: {['%.6f' % r for r in _res]}")
check("the residual is blind to all three", max(_res) < 1e-3,
      "which is why GUARD 3 cannot be folded into GUARD 1")

# HARD GATE, rule 3: this must WARN, never raise. An assert here could refuse
# to calibrate at the venue, and a known-shaky calibration still runs a demo
# where a hard stop does not.
_raised = False
try:
    C.solve(TINY, verbose=False)
except AssertionError:
    _raised = True
check("an ill-conditioned set still SOLVES (warn, never block)", not _raised,
      "a hard stop here could kill the demo at the venue")


print("\n=== A REAL CALIBRATION RETIRES THE FIXTURE SENTINEL ===")
# The recovery card says "rm homography.pkl && python py/calibrate.py", which
# leaves .homography-is-synthetic behind -- so every run AFTERWARDS screamed
# "THIS IS A TEST FIXTURE, NOT A REAL CALIBRATION" over a calibration that WAS
# real. An operator who learns to ignore that banner has also learned to
# ignore it on the day it is true, and it is the banner standing between the
# sponge and a forearm 30cm off target.
import pickle as _pk
_d = "/tmp/_calsent_test"
os.makedirs(_d, exist_ok=True)
_out = os.path.join(_d, "homography.pkl")
_sent = os.path.join(_d, ".homography-is-synthetic")
open(_sent, "w").write("x")
_pk.dump(np.eye(3, dtype=np.float32), open(_out, "wb"))
# Execute the REAL block out of calibrate.py rather than retyping it, so this
# tests the shipped code and fails if someone deletes it.
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "py", "calibrate.py")).read()
if "# A REAL CALIBRATION RETIRES THE FIXTURE SENTINEL." not in _src:
    check("calibrate.py retires the fixture sentinel", False,
          "every later run would claim a real calibration is a fixture")
else:
    _i = _src.index("    # A REAL CALIBRATION RETIRES THE FIXTURE SENTINEL.")
    _j = _src.index('    print(f"wrote {out}.', _i)
    _blk = "\n".join(l[4:] for l in _src[_i:_j].splitlines())
    check("the sentinel exists before", os.path.exists(_sent))
    exec(compile(_blk, "<calibrate-tail>", "exec"),
         {"os": os, "out": _out, "print": lambda *a, **k: None})
    check("a real calibration removes it", not os.path.exists(_sent),
          "every later run would claim this real calibration is a fixture")


print("="*58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  CALIBRATION GUARD CHECKS PASSED")
