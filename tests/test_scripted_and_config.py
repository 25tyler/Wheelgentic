"""tests/test_scripted_and_config.py — the two never-executed code paths.

scripted.py is cut-list item #3: if live tracking fails at hour 7, the
volunteer puts their forearm on a taped X and this runs. It had never been
executed once. A fallback nobody has run is not a fallback.

config.py hot-reload is the venue escape hatch (change forearm_z_mm without a
restart). Its whole point is surviving a malformed edit.
"""
import json, os, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog; watchdog.arm(150)   # SIGALRM, not a
# daemon thread: a 90s daemon watchdog once let a test run for 3h21m.

from fake_roarm import FakeRoArm
import arm as A
import motion
from config import Config
from scripted import run_canned, FOREARM_MID, BUCKET

fails = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: fails.append(label)

# --------------------------------------------------------------- scripted --
print("=== SCRIPTED MODE (never executed before) ===")
fake = FakeRoArm()
a = A.Arm(port=fake.port)
time.sleep(0.3)

events, resets = [], []
SPLOTCH_TS = [0.22, 0.50, 0.78]
# use_water=True here to exercise the bucket path; the DEFAULT is False
# because the team brainstorm says water damages the motors (DIRECTIVE §0c).
run_canned(a, lambda l, t, c, ct: events.append({"limb": l, "t": t, "clean": c}),
           lambda: resets.append(1), SPLOTCH_TS, z_mm=45.0, dur=3.0,
           use_water=True, settle=0.3)

check("it ran to completion without raising", True)
check("all 3 splotches fired", len(events) == 3, f"{len(events)} events")
check("cleanliness reaches exactly 100", events and events[-1]["clean"] == 100,
      f"final={events[-1]['clean'] if events else 'n/a'}")
check("cleanliness is monotonically increasing",
      all(b["clean"] > x["clean"] for x, b in zip(events, events[1:])),
      str([e["clean"] for e in events]))
# Passes through IN ORDER -- plant-verified by reversing the emit index.
# NOT the browser-agreement check: that one is further down and compares
# py/scrubbot.py against web/main.js, two independent files.
check("splotch t values pass through in order",
      [e["t"] for e in events] == SPLOTCH_TS,
      str([e["t"] for e in events]))
check("reset fired at the end", len(resets) == 1)

pts = [(c["x"], c["y"], c["z"]) for c in fake.of_type(1041)]
check("commands were actually sent", len(pts) > 50, f"{len(pts)} points")
check("every point is reachable", all(A.reachable(*p) for p in pts))
xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
check("visited the BUCKET", min(ys) < BUCKET[1] + 30,
      f"min y={min(ys):.0f}, bucket y={BUCKET[1]}")
check("visited the FOREARM", any(abs(p[0] - FOREARM_MID[0]) < 40 for p in pts),
      f"forearm x={FOREARM_MID[0]}")
zs = [p[2] for p in pts]
check("descended to contact depth (z < 45)", min(zs) < 45.0, f"min z={min(zs):.1f}")
check("lifted to hover (z > 100)", max(zs) > 100.0, f"max z={max(zs):.1f}")
step = fake.max_step_mm()
check("scripted motion respects the rate limit", step <= 10.35,
      f"{step:.2f} mm/tick = {step*40:.0f} mm/s")

# oscillation actually oscillated
mid_pts = [p for p in pts if abs(p[2] - 40.0) < 3]
if len(mid_pts) > 10:
    spread = max(p[0] for p in mid_pts) - min(p[0] for p in mid_pts)
    check("scrub oscillation has real amplitude", spread > 20,
          f"{spread:.0f} mm of travel along the forearm")
a.close(); fake.close()

# ---- the two team-decision modes must BOTH work -------------------------
print("\n=== DRY MODE (default — no water, per the brainstorm) ===")
fake2 = FakeRoArm(); a2 = A.Arm(port=fake2.port); time.sleep(0.3)
ev2 = []
run_canned(a2, lambda l, t, c, ct: ev2.append(c), lambda: None, SPLOTCH_TS,
           z_mm=45.0, dur=2.0, use_water=False, settle=0.3)
pts2 = [(c["x"], c["y"], c["z"]) for c in fake2.of_type(1041)]
ys2 = [p[1] for p in pts2]
check("dry mode NEVER visits the bucket", min(ys2) > BUCKET[1] + 60,
      f"min y={min(ys2):.0f}, bucket at y={BUCKET[1]}")
check("dry mode still scrubs and completes", ev2 and ev2[-1] == 100.0,
      f"final={ev2[-1] if ev2 else 'n/a'}")
a2.close(); fake2.close()

print("\n=== HOVER MODE (brainstorm: 'hovering, not touching') ===")
fake3 = FakeRoArm(); a3 = A.Arm(port=fake3.port); time.sleep(0.3)
run_canned(a3, lambda *x: None, lambda: None, SPLOTCH_TS,
           z_mm=45.0, dur=2.0, use_water=False, contact_depth=+20.0,
           settle=0.3)
zs3 = [c["z"] for c in fake3.of_type(1041)]
check("hover mode never descends to skin level", min(zs3) >= 45.0 + 20.0 - 0.5,
      f"min z={min(zs3):.1f} (skin plane is 45.0)")
a3.close(); fake3.close()

# ----------------------------------------------------------------- config --
print("\n=== CONFIG HOT-RELOAD (the venue escape hatch) ===")
tmp = "/tmp/_wheelgentic_cfg_test.json"
json.dump({"forearm_z_mm": 45.0, "scrub_hz": 1.2}, open(tmp, "w"))
c = Config(tmp)
check("initial load", c.data["forearm_z_mm"] == 45.0)

time.sleep(0.02)
json.dump({"forearm_z_mm": 61.0, "scrub_hz": 1.2}, open(tmp, "w"))
check("reload() picks up a valid edit", c.reload() and c.data["forearm_z_mm"] == 61.0,
      f"now {c.data['forearm_z_mm']}")

time.sleep(0.02)
open(tmp, "w").write('{"forearm_z_mm": 99.0, "scrub_hz":')   # truncated JSON
bad_ok = (c.reload() is False)
check("malformed JSON returns False", bad_ok)
check("KEEPS last-good value on malformed JSON", c.data["forearm_z_mm"] == 61.0,
      f"still {c.data['forearm_z_mm']} — must NOT be 99 or missing")

time.sleep(0.02)
os.remove(tmp)
check("missing file does not raise", c.reload() is False)
check("still holds last-good after deletion", c.data["forearm_z_mm"] == 61.0)

t0 = time.perf_counter()
for _ in range(2000): c.reload()
per = (time.perf_counter() - t0) / 2000 * 1e6
check("reload cost is negligible per frame", per < 60, f"{per:.1f} us/call")

# ---- SPLOTCHES MUST BE REACHABLE BY THE SPONGE --------------------------
print("\n=== SPLOTCH POSITIONS vs the sponge's ACTUAL travel ===")
# The FSM pops a splotch when |SPLOTCH_TS[i] - u| < 0.10, where
#   u = 0.5 + off/(2*half),  half = (forearm_mm/2)*0.8,
#   off is bounded by min(|scrub_amp_mm|, half).
# So u only ever spans 0.5 +/- amp/(2*half). With the original 0.22/0.78
# positions and a 250mm+ forearm, the outer two could NEVER be reached: the
# demo stalled at 33% and the operator faked the rest by hand. Found by an
# adversarial audit; this locks it.
SPL = [0.34, 0.50, 0.66]        # must match py/scrubbot.py AND web/main.js
TOL = 0.10
worst = None
for Lmm in (180, 200, 220, 250, 280, 300, 350, 400):
    half = (Lmm / 2.0) * 0.8
    amp = min(abs(35.0), half)
    umin, umax = 0.5 - amp/(2*half), 0.5 + amp/(2*half)
    unreachable = [t for t in SPL if not (umin - TOL <= t <= umax + TOL)]
    if unreachable:
        worst = (Lmm, umin, umax, unreachable)
    check(f"all splotches reachable on a {Lmm}mm forearm", not unreachable,
          f"u spans {umin:.3f}..{umax:.3f}, unreachable: {unreachable}")

# and the two files must agree
import re, os
_sb = open(os.path.join(HERE, "..", "py", "scrubbot.py")).read()
_mj = open(os.path.join(HERE, "..", "web", "main.js")).read()
py_vals = re.search(r"SPLOTCH_TS = \[([^\]]+)\]", _sb).group(1)
js_vals = re.search(r"SPLOTCH_TS = \[([^\]]+)\]", _mj).group(1)
norm = lambda v: [round(float(x), 3) for x in v.split(",")]
check("py/scrubbot.py and web/main.js agree on SPLOTCH_TS",
      norm(py_vals) == norm(js_vals), f"py={norm(py_vals)} js={norm(js_vals)}")
check("the tested constant matches the shipped one",
      norm(py_vals) == SPL, f"shipped={norm(py_vals)} tested={SPL}")

# ---- SAFETY GATES APPLY TO --scripted TOO -------------------------------
print("\n=== ESTOP ABORTS THE CANNED ROUTINE ===")
# --scripted never runs vision_loop, so it had NO estop gate at all: pressing
# the emergency stop parked the pump, and clearing it resumed the routine
# mid-scrub on a forearm with nobody re-authorising anything. This is the
# fallback you switch to when tracking fails -- the mode most likely to be
# running against a real person.
fake4 = FakeRoArm(); a4 = A.Arm(port=fake4.port); time.sleep(0.3)
aborted = {"hit": False}
def _stop_after_two(_n=[0]):
    _n[0] += 1
    return _n[0] > 60                     # trip partway through the routine
run_canned(a4, lambda *x: None, lambda: None, SPLOTCH_TS,
           z_mm=45.0, dur=2.0, use_water=False, settle=0.2,
           should_stop=_stop_after_two)
pts4 = [(c["x"], c["y"], c["z"]) for c in fake4.of_type(1041)]
# MEASURE the full run rather than guessing a threshold. A guessed "< 400"
# blessed a COMPLETE 306-command run and the plant did not fire.
_fake_full = FakeRoArm(); _a_full = A.Arm(port=_fake_full.port); time.sleep(0.3)
run_canned(_a_full, lambda *x: None, lambda: None, SPLOTCH_TS,
           z_mm=45.0, dur=2.0, use_water=False, settle=0.2)
FULL = len(_fake_full.of_type(1041))
_a_full.close(); _fake_full.close()
check("an aborted routine is much SHORTER than a full one",
      len(pts4) < FULL * 0.5,
      f"{len(pts4)} commands vs {FULL} for a full run")
# go_home() sets the TARGET; the pump walks there at 6mm/tick, so give it
# time rather than sampling mid-flight.
check("the abort targets HOME",
      abs(a4.target[0] - A.HOME[0]) < 1 and abs(a4.target[2] - A.HOME[2]) < 1,
      f"target {tuple(round(v,1) for v in a4.target[:3])}")
time.sleep(2.0)
pts4b = [(c["x"], c["y"], c["z"]) for c in fake4.of_type(1041)]
check("and the arm ARRIVES home under the rate limit",
      pts4b and abs(pts4b[-1][2] - A.HOME[2]) < 2,
      f"final z={pts4b[-1][2] if pts4b else 'n/a'} (HOME z={A.HOME[2]:.0f})")
a4.close(); fake4.close()

print("\n=== AN ACTIVE ESTOP BLOCKS THE ROUTINE ENTIRELY ===")
fake5 = FakeRoArm(); a5 = A.Arm(port=fake5.port); time.sleep(0.3)
a5.estop(); time.sleep(0.3)
n_before = len(fake5.of_type(1041))
run_canned(a5, lambda *x: None, lambda: None, SPLOTCH_TS,
           z_mm=45.0, dur=1.0, use_water=False, settle=0.2)
check("no motion commands while estopped",
      len(fake5.of_type(1041)) == n_before,
      f"{len(fake5.of_type(1041)) - n_before} leaked")
a5.clear_estop(); a5.close(); fake5.close()

# ---- PATHOLOGICAL CONFIG MUST NOT KILL THE VISION THREAD ---------------
print("\n=== HOSTILE CONFIG VALUES ===")
# config.json HOT-RELOADS, so a typo at the venue lands in these functions
# mid-demo. An exception in the vision thread leaves the arm holding its last
# target on a forearm with no state machine left to retreat it.
# scrub_seconds=0 used to raise ZeroDivisionError.
import motion as _motion
for label, kw in [
    ("scrub_seconds=0",     dict(elapsed=1.0, duration=0.0)),
    ("duration=1e-9",       dict(elapsed=1.0, duration=1e-9)),
    ("duration negative",   dict(elapsed=1.0, duration=-5.0)),
    ("duration NaN",        dict(elapsed=1.0, duration=float("nan"))),
    ("scrub_hz=0",          dict(elapsed=1.0, duration=8.0, freq_hz=0.0)),
    ("scrub_hz=1e6",        dict(elapsed=1.0, duration=8.0, freq_hz=1e6)),
    ("amp=-500",            dict(elapsed=1.0, duration=8.0, amp_mm=-500.0)),
    ("elapsed negative",    dict(elapsed=-3.0, duration=8.0)),
]:
    try:
        v = _motion.scrub_offset(**kw)
        ok = (v == v) and abs(v) < 1e6
        check(f"scrub_offset survives {label}", ok, f"{v:.3f}")
    except Exception as e:
        check(f"scrub_offset survives {label}", False,
              f"RAISED {type(e).__name__} — this kills the vision thread")

check("smooth5 clamps below 0", _motion.smooth5(-5.0) == 0.0)
check("smooth5 clamps above 1", _motion.smooth5(99.0) == 1.0)
check("reachable() rejects NaN", not A.reachable(float("nan"), 0, 200),
      "NaN passes Arduino's constrain() and the int16 cast yields 0")
check("reachable() rejects inf", not A.reachable(float("inf"), 0, 200))

print("\n" + "="*58)
if fails:
    print(f"  *** {len(fails)} FAILED: {fails}"); sys.exit(1)
print("  SCRIPTED + CONFIG CHECKS PASSED")
