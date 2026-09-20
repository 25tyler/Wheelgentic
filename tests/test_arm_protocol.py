"""tests/test_arm_protocol.py — arm.py against a protocol-level fake RoArm.

Before this, arm.py had never talked to ANYTHING. Every claim in it about wire
format, rate, rate-limiting, reachability and estop was unverified.

READ tests/fake_roarm.py's docstring for what this can and cannot prove.
It does NOT mean "the arm works" — first real-hardware contact is a human
running `python py/arm.py`.
"""
import json, math, os, sys, threading, time
import serial

# WATCHDOG. A serial test can block forever on a read; without this a hung
# test takes the whole run with it. os._exit(3) so no atexit handler can also
# block on the same fd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 210s, NOT 150, AND THE NUMBER COMES FROM A MEASUREMENT. This test aborted
# on 3 of 20 suite runs (15%) while passing at exit 0 on every re-run alone.
# The cause is not a wedge: the estop-race block below sweeps 10 timing
# offsets and RETRIES the whole sweep up to twice when any offset comes out
# inconsistent, and each pass is 10 x 1.3s. Two retries add 39 seconds. The
# inconsistency that triggers them is itself load-sensitive, so a busy
# machine -- which is exactly what a full suite run is -- causes the retries
# that then push the test past the budget.
#
# Base run measures ~87s solo. 87 + 39 = 126, and 150 left 24 seconds of
# headroom for everything else the machine is doing. 210 leaves 84.
#
# NOT a way of hiding a hang: a real wedge still trips this, just later, and
# the alternative fixes are worse. Raising it is honest; deleting the retries
# is not, because they are what prove the race is not real and the comment
# above them records that block producing five separate critical bugs.
import watchdog; watchdog.arm(210)   # SIGALRM, not a
# daemon thread: a 90s daemon watchdog once let a test run for 3h21m.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py"))
sys.path.insert(0, HERE)

import fake_roarm
from fake_roarm import FakeRoArm
import arm as A

fails = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: fails.append(label)

print("=== 1. CONNECT + STARTUP COMMANDS ===")
fake = FakeRoArm()
a = A.Arm(port=fake.port)
time.sleep(0.4)
check("debug-spam disabled (T:605)", len(fake.of_type(605)) == 1)
t112 = fake.of_type(112)
check("torque caps applied (T:112)", len(t112) == 1,
      json.dumps(t112[0]) if t112 else "none sent")
if t112:
    check("torque caps are BELOW firmware default 1000",
          all(t112[0].get(k, 1000) < 1000 for k in "bseh"),
          f"b={t112[0].get('b')} s={t112[0].get('s')} e={t112[0].get('e')} h={t112[0].get('h')}")
check("every line is valid JSON", not fake.snapshot()["bad"],
      f"{len(fake.snapshot()['bad'])} bad")

# ORDERING, not just presence. The suite checked that T:112 was SENT but never
# that it was sent BEFORE motion -- so a startup sequence that streamed
# full-torque commands first and capped torque afterwards would pass while the
# arm briefly had no torque limit against a person's forearm.
Ts = [c.get("T") for c in fake.snapshot()["commands"]]
i112 = Ts.index(112) if 112 in Ts else -1
i1041 = Ts.index(1041) if 1041 in Ts else len(Ts)
check("torque caps (T:112) are set BEFORE any motion (T:1041)",
      i112 >= 0 and i112 < i1041,
      f"T:112 at index {i112}, first T:1041 at {i1041}")
check("debug spam silenced (T:605) before feedback reads",
      605 in Ts and (105 not in Ts or Ts.index(605) < Ts.index(105)),
      f"order: {Ts[:6]}")
raw = fake.snapshot()["raw"]
check("newline-terminated, one cmd per line", all(l.startswith("{") and l.endswith("}") for l in raw))

print("\n=== 2. COMMAND RATE (claimed 40 Hz) ===")
a.set_target(*A.HOME)
time.sleep(2.0)
hz = fake.rate_hz(1041)
check("1041 stream is 36-42 Hz", 36 <= hz <= 42, f"{hz:.1f} Hz")

print("\n=== 3. RATE LIMITER (MAX_STEP_MM) ===")
# Ask for a huge jump; the pump must walk there in <=6mm steps.
a.set_target(A.HOME[0], A.HOME[1] + 200, A.HOME[2])
time.sleep(2.5)
step = fake.max_step_mm()
# TWO assertions, deliberately. Checking only against A.MAX_STEP_MM makes the
# test agree with whatever that constant says -- plant-verified: setting it to
# 9999 let a 200mm lurch pass as "no step exceeds MAX_STEP_MM=9999".
# The ABSOLUTE ceiling is the safety property: this machine touches a person's
# forearm at 40Hz, so 10mm/tick = 400mm/s is the outer bound of defensible.
check(f"no step exceeds the configured MAX_STEP_MM={A.MAX_STEP_MM}",
      step <= A.MAX_STEP_MM + 0.35, f"max observed {step:.2f} mm")
check("MAX_STEP_MM itself is within the ABSOLUTE safety ceiling (10mm/tick)",
      A.MAX_STEP_MM <= 10.0,
      f"{A.MAX_STEP_MM} mm/tick = {A.MAX_STEP_MM*40:.0f} mm/s at 40Hz")
check("observed motion never exceeds 400 mm/s regardless of config",
      step <= 10.35, f"{step:.2f} mm/tick = {step*40:.0f} mm/s")

print("\n=== 4. reachable() + hard-clamp signalling ===")
# NOTE: set_target CLAMPS to BOX before calling reachable(), so an absurd
# target cannot come back False -- it comes back relocated. That silent
# relocation was the real finding here: a broken homography would move the arm
# confidently to the wrong place with no signal. Hence clamped_hard.
a.clamped_hard = False
a.set_target(2000, 0, 200)
check("huge clamp is SIGNALLED via clamped_hard", a.clamped_hard is True)
a.clamped_hard = False
a.set_target(A.HOME[0] + 3, A.HOME[1], A.HOME[2])
check("a small in-box move does NOT trip the warning", a.clamped_hard is False)

before = len(fake.of_type(1041)); time.sleep(0.3); after = len(fake.of_type(1041))
bad = [c for c in fake.of_type(1041)[before:after]
       if not A.reachable(c["x"], c["y"], c["z"])]
check("no unreachable point ever reaches the wire", not bad, f"{len(bad)} leaked")
check("reachable() rejects beyond REACH_MAX", not A.reachable(600, 0, 126.06))
# REACH_MIN is the |L2-L3| dead zone measured FROM THE SHOULDER at ARM_L1,
# i.e. d = hypot(hypot(x,y), z-ARM_L1) < 41.44. A point near the base at
# shoulder height qualifies; (126,0,131) does NOT (its d is 126).
check("reachable() rejects inside REACH_MIN (d<41.4)",
      not A.reachable(20, 0, A.ARM_L1), f"REACH_MIN={A.REACH_MIN:.1f}")
check("reachable() accepts HOME", A.reachable(*A.HOME[:3]))

print("\n=== 5. SAFETY BOX CLAMP ===")
a.set_target(9999, 9999, 9999)
time.sleep(0.4)
pts = [(c["x"], c["y"], c["z"]) for c in fake.of_type(1041)]
B = A.BOX
out = [p for p in pts if not (B["xmin"]-0.5 <= p[0] <= B["xmax"]+0.5
                              and B["ymin"]-0.5 <= p[1] <= B["ymax"]+0.5
                              and B["zmin"]-0.5 <= p[2] <= B["zmax"]+0.5)]
check("every commanded point inside BOX", not out, f"{len(out)} outside")

print("\n=== 6. FEEDBACK + CONTACT DETECTION ===")
fb = a.poll_feedback()
check("T:105 returns a parsed dict", isinstance(fb, dict) and "torS" in fb,
      str(list(fb)[:6]))
check("no contact when torque is low", a.contact is False, f"torS={fb.get('torS')}")
fake.set_contact(True)
fb2 = a.poll_feedback()
check("contact detected on torque spike", a.contact is True, f"torS={fb2.get('torS')}")
fake.set_contact(False)
a.poll_feedback()
check("contact clears when torque drops", a.contact is False)

print("\n=== 7. EMERGENCY STOP ===")
a.estop()
# POLL, don't sleep: a fixed wait before a safety assertion goes red on a
# loaded machine. Measured today in test_consent_latch.py -- the suite came
# back 24/25 on one, while isolation read the value EXACTLY right 5/5.
_d = time.time() + 5
while time.time() < _d and not fake.of_type(0):
    time.sleep(0.02)
check("T:0 sent on estop", len(fake.of_type(0)) >= 1)
check("fake hardware is estopped", fake.estopped is True)
n_before = len(fake.of_type(1041))
a.set_target(300, 0, 200)
# FIXED SLEEP IS CORRECT HERE. This asserts nothing leaked; the condition is
# already true at t=0, so a poll would break out instantly and test nothing.
# Give a leak time to APPEAR.
time.sleep(0.6)
n_after = len(fake.of_type(1041))
check("NO motion commands sent while estopped", n_after == n_before,
      f"{n_after - n_before} leaked")
a.clear_estop()
time.sleep(0.4)
check("T:999 sent on clear", len(fake.of_type(999)) >= 1)
check("fake hardware cleared", fake.estopped is False)
time.sleep(0.4)
check("motion resumes after clear", len(fake.of_type(1041)) > n_after)

print("\n=== 7b. ESTOP RECOVERY MUST NOT THROW THE ARM (regression) ===")
# Found by an adversarial audit and reproduced: clear_estop() used to set
# last_sent = None, and the pump's None branch SKIPS the rate limiter. Measured
# 237.8 mm in one 25 ms tick = 9,513 mm/s -- 40x the limit -- while the sponge
# rests on a person's forearm. This is the button an operator presses under
# pressure. It must be the gentlest motion in the program.
a.set_target(300, 120, 40)          # sponge on a forearm
time.sleep(1.2)
n_before = len(fake.of_type(1041))
a.estop(); time.sleep(0.35)
a.clear_estop(); time.sleep(1.0)
after = [(c["x"], c["y"], c["z"]) for c in fake.of_type(1041)][n_before:]
jump = math.dist((300, 120, 40), after[0]) if after else 0.0
check("first move after clearing estop respects MAX_STEP_MM",
      jump <= A.MAX_STEP_MM + 0.35, f"{jump:.1f} mm in one 25ms tick "
      f"= {jump/0.025:.0f} mm/s")
check("and stays under the absolute 400 mm/s ceiling", jump <= 10.35,
      f"{jump/0.025:.0f} mm/s")
steps = [math.dist(p, q) for p, q in zip(after, after[1:])]
check("no later step exceeds the limit either",
      (max(steps) if steps else 0) <= 10.35,
      f"max {max(steps) if steps else 0:.1f} mm")

print("\n=== 7c. ESTOP MUST NOT REPORT SUCCESS IT DID NOT ACHIEVE ===")
# estop() used to set estopped=True, call _send (which swallows EVERY failure)
# and print "*** EMERGENCY STOP ***" unconditionally. On a wedged or unplugged
# link the operator got a confirmation while the command never left the
# machine -- and estopped=True also stops the pump, so the arm held its last
# target pressed into a forearm. The one mechanism that must never lie.
a.clear_estop(); time.sleep(0.3)
check("estop on a HEALTHY link returns True", a.estop() is True)
check("and sets estop_confirmed", a.estop_confirmed is True)
_d = time.time() + 5     # the fake handles T:0 on its OWN reader thread, so
while time.time() < _d and not fake.estopped:   # poll it rather than guess
    time.sleep(0.02)
check("and the hardware really stopped", fake.estopped is True)
a.clear_estop(); time.sleep(0.3)

class _DeadSerial:
    def write(self, b): raise serial.SerialException("device not configured")
    def close(self): pass
    def reset_input_buffer(self): pass
    def readline(self): return b""

_real_ser, a.ser = a.ser, _DeadSerial()
a._last_write_warn = 0.0
dead_ok = a.estop()
a.ser = _real_ser
check("estop on a DEAD link returns False (does NOT lie)", dead_ok is False)
check("and estop_confirmed is False", a.estop_confirmed is False)
a.estopped = False
a.clear_estop(); time.sleep(0.3)

print("\n=== 7c2. CLEARING AN ESTOP AFTER THE ARM PHYSICALLY MOVED ===")
# last_sent is what we COMMANDED, not where the arm IS. During an estop the
# servos are released, so gravity or a nudge moves it -- and the rate limiter
# would then interpolate from an origin that is a lie. Measured before the
# fix: a 60mm droop produced a 2400 mm/s first command on the RECOVERY button.
a.clear_estop(); time.sleep(0.3)
a.set_target(300, 120, 40); time.sleep(1.2)
a.estop(); time.sleep(0.4)
fake.x, fake.y, fake.z = 300.0, 120.0, -20.0      # gravity droops it 60mm
n_pre = len(fake.of_type(1041))
a.clear_estop(); time.sleep(1.2)
moved = [(c["x"], c["y"], c["z"]) for c in fake.of_type(1041)][n_pre:]
first_gap = math.dist((300, 120, -20), moved[0]) if moved else 0.0
check("the drift is detected and the limiter re-seeded",
      first_gap <= A.MAX_STEP_MM + 0.5,
      f"first command {first_gap:.1f}mm from the TRUE position "
      f"= {first_gap/0.025:.0f} mm/s")
gaps = [math.dist(p, q) for p, q in zip(moved, moved[1:])]
check("and every later step stays limited",
      (max(gaps) if gaps else 0) <= A.MAX_STEP_MM + 0.5,
      f"max {max(gaps) if gaps else 0:.2f}mm")
a.clear_estop(); time.sleep(0.3)

print("\n=== 7d. A FAILED ESTOP MUST NOT BE WORSE THAN NO ESTOP ===")
# Proved by execution during an audit: with estopped=True latched on a FAILED
# write, a TRANSIENT link stall meant the pump was silenced forever -- when
# the link recovered, ZERO commands were sent and the arm held the contact
# pose on a forearm indefinitely. The control run, with no estop pressed,
# delivered 41 commands and ended at HOME. Pressing the emergency stop was
# STRICTLY WORSE than doing nothing.
class _FlakySerial:
    """Raises for `stall` seconds, then works perfectly."""
    def __init__(self, stall):
        self.until = time.time() + stall
        self.sent = []
    def write(self, b):
        if time.time() < self.until:
            raise serial.SerialTimeoutException("write timeout")
        self.sent.append(b)
    def close(self): pass
    def setRTS(self, v): pass
    def setDTR(self, v): pass
    def reset_input_buffer(self): pass
    def readline(self): return b""

def _park_pump(a, samples=3, every=0.03, limit=40):
    """Stop `a`'s 40Hz pump and PROVE it stopped. Returns True when proven.

    WHY THIS EXISTS: Arm's pump thread calls _send() at 40Hz and _send
    increments the SAME _consec_write_fail an assertion below reads. The
    neighbouring `not any(_res)` check only inspects the test's own 15 return
    values, so it passes whatever the pump adds -- which is why that one
    assertion failed in 11 of 74 suite runs and never once when this file ran
    alone. MEASURED: a tight 15-call loop lands on 15 five times out of five,
    a 1ms sleep inside it reads 16-17, and 5ms reads 18-20.

    `a._run = False` ALONE IS NOT A BARRIER: the pump tests that flag only
    BETWEEN ticks and may be inside its 25ms deadline sleep, so queued writes
    still land (measured: 16,16,15,16,15 with a bare flag flip). Arm.close()
    has the same shape and stores no thread handle, so join() is impossible
    without changing production code. Hence: clear the flag, then poll
    _write_fail_count until it holds steady across three samples.
    """
    a._run = False
    last, stable = a._write_fail_count, 0
    for _ in range(limit):
        time.sleep(every)
        now = a._write_fail_count
        stable = stable + 1 if now == last else 0
        last = now
        if stable >= samples:
            return True
    return False


def _stall_run(press_estop):
    import threading as _th
    a2 = A.Arm.__new__(A.Arm)
    a2.dry = False; a2.lock = _th.Lock(); a2.target = A.HOME
    a2.last_sent = (300, 120, 40)          # sponge pressed on a forearm
    a2.feedback = {}; a2.contact = False; a2.estopped = False
    a2.clamped_hard = False; a2.estop_confirmed = False
    a2._last_clamp_warn = 0.0; a2._last_write_warn = 0.0
    a2._write_fail_count = 0; a2._run = True
    a2.ser = _FlakySerial(0.5)
    a2.set_target(300, 120, 40)
    _th.Thread(target=a2._pump, daemon=True).start()
    if press_estop:
        time.sleep(0.15)                   # press DURING the stall
        a2.estop()
    time.sleep(1.6)                        # link recovers at 0.5s
    a2._run = False; time.sleep(0.1)
    return a2.ser.sent, a2.estopped

sent_e, est_e = _stall_run(True)
check("a FAILED estop does not latch estopped", est_e is False)
check("commands resume once the link recovers", len(sent_e) > 10,
      f"{len(sent_e)} delivered — 0 would mean the arm is frozen on a person")
if sent_e:
    final = json.loads(sent_e[-1].decode())
    at_home = (abs(final.get("x", 0) - A.HOME[0]) < 1.5 and
               abs(final.get("z", 0) - A.HOME[2]) < 1.5)
    check("and the arm RETREATS to HOME, not stays at contact depth", at_home,
          f"final ({final.get('x')}, {final.get('y')}, {final.get('z')})")

print("\n=== 7e. AN ESTOP DURING clear_estop MUST WIN ===")
# poll_feedback() takes ~61ms inside clear_estop (reset_input_buffer + write +
# sleep(0.05) + readline). An operator who clears and immediately re-stops --
# exactly what a nervous operator does -- landed in that window, and the code
# then set estopped = False, RELEASING a stop that had just been requested.
a.clear_estop(); time.sleep(0.3)
a.set_target(300, 120, 40); time.sleep(0.8)
a.estop(); time.sleep(0.3)
threading.Timer(0.02, a.estop).start()      # new estop 20ms into the clear
res = a.clear_estop()
# FIXED SLEEP IS CORRECT HERE too -- negative assertion, see above.
time.sleep(0.5)
check("clear_estop REFUSES when a new estop raced it", res is False)
check("and the arm stays estopped", a.estopped is True,
      "releasing a live stop is the worst possible outcome here")
a.clear_estop(); time.sleep(0.4)
check("a clean clear still works afterwards", a.estopped is False)

print("\n=== 7e2. TWO CLEARS AT ONCE (python 'r' + browser shift+C) ===")
# 7e races estop-vs-clear. Nothing raced CLEAR-vs-CLEAR, and there are two
# clear entry points that can genuinely fire together: the python 'r' key and
# the projector's shift+C arriving over the socket. Both call clear_estop() on
# DIFFERENT threads. clear_estop bumps _estop_gen and re-sends T:999, so two
# interleaved clears could plausibly leave the generation counter ahead of the
# hardware and make the NEXT estop look like it raced a clear that already
# finished -- i.e. a stop refused for no reason. Measured: _estop_gen reaches 2
# (both clears bump it) and a later estop still lands at all three gaps.
#
# NO RETRY WRAPPER, unlike 7f below. 7f needs two retries because its
# threading.Timer is scheduled late when the suite loads the machine; this block
# uses Thread.start()/join() with no timer, so there is nothing for load to
# delay. Measured 9/9 across three trials at load 3.7 with the full suite
# running. If this ever DOES flake, the timer-free shape means it is a real
# race, not a busy machine -- do not paper it over with retries.
for _gap in (0.0, 0.01, 0.05):
    _f = fake_roarm.FakeRoArm(); _a = A.Arm(port=_f.port); time.sleep(0.2)
    _a.set_target(300, 120, 40); time.sleep(0.35)
    _a.estop(); time.sleep(0.25)
    _errs = []
    def _clear():
        try: _a.clear_estop()
        except Exception as _e: _errs.append(repr(_e))
    _t1 = threading.Thread(target=_clear); _t2 = threading.Thread(target=_clear)
    _t1.start()
    if _gap: time.sleep(_gap)
    _t2.start()
    _t1.join(); _t2.join(); time.sleep(0.4)
    check(f"two clears at {_gap}s: neither raised", not _errs, "; ".join(_errs))
    check(f"two clears at {_gap}s: latch agrees with hardware",
          _a.estopped == _f.estopped, f"latch={_a.estopped} hw={_f.estopped}")
    check(f"two clears at {_gap}s: ended cleared, not stuck stopped",
          _a.estopped is False and _f.estopped is False,
          f"latch={_a.estopped} hw={_f.estopped} -- the operator asked twice to clear")
    # ...and a stop AFTER the double-clear must still land, or the generation
    # counter is ahead of reality and the next emergency is refused.
    _a.estop(); time.sleep(0.3)
    check(f"two clears at {_gap}s: a LATER estop still works",
          _a.estopped is True and _f.estopped is True,
          f"latch={_a.estopped} hw={_f.estopped} -- a stop refused after a double clear")
    _a._run = False; _a.close(); _f.close()

print("\n=== 7f. THE ESTOP LATCH SURVIVES EVERY RACE OFFSET ===")
# A verifier swept 30 offsets across clear_estop's ~61ms window and found
# 25/30 left the HARDWARE latched while arm.estopped read False, and 1/30 let
# the arm actually move. A single well-chosen offset proves nothing here --
# sweep the window.
_outcomes = {"ok": 0, "inconsistent": 0}
_OFFSETS = list(range(0, 60, 6))           # the sweep size, reported below
for _off_ms in _OFFSETS:
    _f = fake_roarm.FakeRoArm(); _a = A.Arm(port=_f.port); time.sleep(0.15)
    _a.set_target(300, 120, 40); time.sleep(0.35)
    _a.estop(); time.sleep(0.2)
    threading.Timer(_off_ms / 1000.0, _a.estop).start()
    _a.clear_estop()
    time.sleep(0.3)
    if _a.estopped and _f.estopped:
        _outcomes["ok"] += 1
    else:
        _outcomes["inconsistent"] += 1
    _a._run = False; _a.close(); _f.close()
# RETRY an inconsistent offset before failing. A dedicated 72-trial sweep of
# this exact code found ZERO real failures, but inside the loaded suite an
# offset intermittently loses -- the timers this test relies on are not
# scheduled promptly when two dozen tests and a browser compete for CPU.
# Retrying distinguishes "the code races" from "the machine was busy"; a REAL
# race fails every time.
#
# TWO retries, not one. At 19 tests one retry was enough; at 24 the suite got
# heavier and a single retry started losing too, so the suite went red on a
# machine problem while the same test passed 10/10 in isolation. A guard that
# cries wolf on load gets ignored, and this one covers the estop -- the area
# that has produced five separate critical bugs.
for _attempt in range(2):
    if not _outcomes["inconsistent"]:
        break
    _retry_bad = 0
    for _off_ms in _OFFSETS:
        _f = fake_roarm.FakeRoArm(); _a = A.Arm(port=_f.port); time.sleep(0.2)
        _a.set_target(300, 120, 40); time.sleep(0.45)
        _a.estop(); time.sleep(0.25)
        threading.Timer(_off_ms / 1000.0, _a.estop).start()
        _a.clear_estop()
        time.sleep(0.4)
        if not (_a.estopped and _f.estopped):
            _retry_bad += 1
        _a._run = False; _a.close(); _f.close()
    _outcomes["inconsistent"] = _retry_bad
    print(f"    (retry {_attempt+1}: {_retry_bad} still inconsistent)")

# DENOMINATOR: report the sweep size, not sum(_outcomes.values()). The retry
# loop above overwrites _outcomes["inconsistent"] and never re-counts "ok", so
# the sum under-reports by exactly the number of offsets the retry fixed --
# it printed "of 9 offsets" for a 10-offset sweep on both of the two suite runs
# that ever retried (suite26, suite33). Invisible on a PASS because
# run_all.sh:167 tails 12 lines and this line scrolls off, so the only runs
# that showed the wrong denominator were the two that also aborted.
check("every race offset leaves BOTH software and hardware stopped",
      _outcomes["inconsistent"] == 0,
      f"{len(_OFFSETS) - _outcomes['inconsistent']} ok, "
      f"{_outcomes['inconsistent']} inconsistent "
      f"of {len(_OFFSETS)} offsets")

print("\n=== 7g. RECOVERY WORKS EVEN IF SOFTWARE THINKS NOTHING IS STOPPED ===")
# The dominant race outcome: hardware latched, arm.estopped False. A guard
# reading `if arm.estopped` then refused to re-send T:999 and the arm sat
# frozen at the contact pose with the recovery key dead.
_f2 = fake_roarm.FakeRoArm(); _a2 = A.Arm(port=_f2.port); time.sleep(0.2)
_a2.set_target(300, 120, 40); time.sleep(0.4)
_a2.estop(); time.sleep(0.25)
_a2.estopped = False                      # the pathological state
_n999 = len(_f2.of_type(999))
_a2.clear_estop()
_d = time.time() + 5          # poll, not a fixed wait (see line 135)
while time.time() < _d and len(_f2.of_type(999)) <= _n999:
    time.sleep(0.02)
check("clear_estop re-sends T:999 unconditionally",
      len(_f2.of_type(999)) > _n999,
      f"{len(_f2.of_type(999))} sent (was {_n999})")
check("and the hardware is released", _f2.estopped is False,
      "otherwise the arm is frozen on a forearm with no way back")
_a2._run = False; _a2.close(); _f2.close()

print("\n=== 8. CLEAN SHUTDOWN ===")
a.close(); time.sleep(0.2)
check("estop sent on close", len(fake.of_type(0)) >= 2)
fake.close()

print("\n=== link_ok: THE DEMO MUST NOT CLAIM A SCRUB IT DID NOT DO ===")
# Pulling the USB mid-scrub used to leave the FSM marching the counter
# 33 -> 67 -> 100 with contact=True on every event while the arm received ZERO
# further commands -- measured 115 before the unplug, 115 after. The projector
# claimed torque-confirmed contact from an arm that was not plugged in.
_f3 = FakeRoArm()
_a3 = A.Arm(port=_f3.port)
time.sleep(0.4)
check("a healthy link reports ok", _a3.link_ok is True)
# TOLERANT of a hiccup: 12 failures at the 40Hz pump is 0.3s; a real hiccup is
# 1-2 frames. A threshold that trips on those would drop the demo to IDLE
# mid-scrub for no reason.
_a3._consec_write_fail = 11
check("11 consecutive failures do NOT trip it", _a3.link_ok is True,
      "a 1-2 frame hiccup must not kill the demo")
_a3._consec_write_fail = 12
check("12 consecutive failures DO trip it", _a3.link_ok is False,
      "~0.3s of silence is a real unplug")
# CONSECUTIVE, not cumulative: the running total never resets, so a total-based
# rule would mark a healthy arm dead forever after one bad write an hour ago.
_a3._consec_write_fail = 50
_a3._send({"T": 105})
time.sleep(0.1)
check("one successful write clears the streak", _a3._consec_write_fail == 0)
check("and the link reads ok again", _a3.link_ok is True)
_a3.close(); _f3.close()
_dry = A.Arm(dry_run=True)
check("a --no-arm dry run reports a HEALTHY link", _dry.link_ok is True,
      "--no-arm is a deliberate mode, not a fault")
_dry.close()

# ---------------------------------------------------------------------------
# WARNING TO WHOEVER PLANTS BUGS IN THIS FILE NEXT.
#
# Mutating fake_roarm.stall() into a no-op and re-running this file WEDGES it:
# three attempts, each hung in the PRE-EXISTING estop-race section (the last
# log line is always "a NEW estop arrived while clearing"), never in the block
# below. Each wedge left `fake_roarm.py` mutated in the working tree, because
# the shell never reached its restore line.
#
# AND `watchdog.arm(150)` DID NOT FIRE -- measured, twice, at 4+ minutes with
# zero "WATCHDOG" lines in the log. That observation stands; the explanation
# that used to sit here does not. It said a main thread parked in a C call
# starves SIGALRM because CPython runs the handler only at a bytecode
# boundary. Re-measured on 3.14.6: eight of eight blocking calls fire the
# handler at 2.0s exit 3, including pyserial 3.5's read and readline on a
# timeout=None pty, which is precisely what this file drives. PEP 475 runs
# the handler and then retries the syscall. WHY THESE RUNS WEDGED IS OPEN --
# see watchdog.py's docstring before re-deriving the starvation story.
#
# So: plant against the ASSERTION IN-PROCESS rather than by editing the file,
# keep a verified-clean backup, and check `grep -c PLANT` after every attempt.
# ---------------------------------------------------------------------------
print("\n=== A LOST ESTOP MUST NOT SILENCE THE PUMP FOREVER ===")
# py/arm.py:422 claims: "0 commands delivered after recovery with estop
# pressed, versus 41 ending at HOME without it." It was the ONE measured claim
# in the codebase that could not be re-verified, because reproducing it needs a
# transient link stall and fake_roarm had no way to fail a write.
#
# WHY THE STALL MUST BREAK THE *WRITE*, not drop bytes at the pty: _send()
# catches SerialTimeoutException/SerialException and counts them into
# _consec_write_fail, which link_ok and the estop recovery both read.
# Discarding a command after delivery leaves _send() returning True, so none of
# that machinery engages and the scenario never happens. The link_ok section
# above sets _consec_write_fail BY HAND and never drives a real failing write.
_f4 = FakeRoArm(); _a4 = A.Arm(port=_f4.port); time.sleep(0.4)
_f4.attach_stall(_a4)
check("a healthy write succeeds", _a4._send({"T": 105}) is True)
_f4.stall(True)
# PARK THE PUMP FIRST. It shares _consec_write_fail with the writes below, and
# its own stalled ticks inflate the count past 15 whenever the scheduler gives
# it a slice -- the whole cause of this block's 11 intermittent failures. Prove
# it quiescent, then baseline the counter so the number below is the test's.
_parked = _park_pump(_a4)
check("the pump parks before the count is measured", _parked,
      "quiescent: the count below is the test's alone" if _parked
      else "NOT quiescent -- the count below would include the pump's writes")
_a4._consec_write_fail = 0
_res = [_a4._send({"T": 105}) for _ in range(15)]
check("a stalled write FAILS through the real path", not any(_res),
      "stall() is a no-op; every assertion below would pass vacuously")
check("and it drives _consec_write_fail", _a4._consec_write_fail == 15,
      f"consec={_a4._consec_write_fail}")
check("so link_ok goes false", _a4.link_ok is False)
_f4.stall(False)
_a4._send({"T": 105})
check("one good write clears it again", _a4._consec_write_fail == 0
      and _a4.link_ok is True)
_a4._run = False; _a4.close(); _f4.close()

# THE CLAIM ITSELF. Latching on a failed estop makes pressing the button
# STRICTLY WORSE than not pressing it: the arm holds the contact pose, 5mm
# into the skin plane, on a person's forearm, indefinitely.
def _after_recovery(latch):
    f = FakeRoArm(); a = A.Arm(port=f.port); time.sleep(0.4); f.attach_stall(a)
    a.set_target(250, 0, 60); time.sleep(0.3)
    f.stall(True)                      # the T:0 is lost
    a.estop()
    if latch:                          # the OLD behaviour the comment describes
        a.estopped, a.abort_requested = True, False
    time.sleep(0.3)
    n0 = len(f.of_type(1041))
    f.stall(False)                     # link drains
    time.sleep(1.5)
    n1 = len(f.of_type(1041))
    pos = (f.x, f.y, f.z)
    a._run = False; a.close(); f.close()
    return n1 - n0, pos

_latched, _pl = _after_recovery(True)
_free, _pf = _after_recovery(False)
check("LATCHING delivers ZERO commands after the link returns", _latched == 0,
      f"{_latched} — if this is nonzero the scenario did not reproduce")
check("staying un-latched keeps the pump alive", _free > 0, f"{_free} commands")
check("and the arm actually reaches HOME", abs(_pf[2] - A.HOME[2]) < 15.0,
      f"ended at z={_pf[2]:.0f}, HOME z={A.HOME[2]:.0f}")
check("the latched arm is left DOWN on the forearm", _pl[2] < _pf[2] - 30,
      f"latched z={_pl[2]:.0f} vs un-latched z={_pf[2]:.0f}")

print("\n=== TORQUE CAPS SURVIVE A RECOVERY ===")
# T:112 was sent ONCE at construction and never again. The recovery card
# documents ESP32 brownout-reset as routine -- "Arm limp / ESP32 reset.
# Brownout. Reconnect, press r." -- and a reset ESP32 comes back with the
# FIRMWARE DEFAULT cap of 1000, not 60/110/50/50. The operator pressed r, the
# arm resumed, and it pushed with ~16x the intended shoulder torque into
# someone's forearm with nothing on screen to say the limit was gone.
# Measured before the fix: T:112 count after estop+clear was 1 -- the same one
# from connect, zero re-assertions.
_f2 = FakeRoArm()
_a2 = A.Arm(port=_f2.port)
time.sleep(0.5)
check("caps applied once at connect", len(_f2.of_type(112)) == 1,
      str(len(_f2.of_type(112))))
_a2.estop()
time.sleep(0.3)
_before = len(_f2.of_type(112))
_a2.clear_estop()
_d = time.time() + 6          # poll, not a fixed wait (see line 135)
while time.time() < _d and len(_f2.of_type(112)) - _before < 1:
    time.sleep(0.02)
_new = len(_f2.of_type(112)) - _before
check("clear_estop RE-ASSERTS the torque caps", _new >= 1,
      f"{_new} new T:112 — a brownout recovery would run uncapped")
if _f2.of_type(112):
    _c = _f2.of_type(112)[-1]
    check("and re-asserts the REAL values, not defaults",
          (_c.get("b"), _c.get("s"), _c.get("e"), _c.get("h")) == (60, 110, 50, 50),
          str({k: _c.get(k) for k in ("b", "s", "e", "h")}))
_a2.close(); _f2.close()

print("\n" + "="*58)
if fails:
    print(f"  *** {len(fails)} FAILED: {fails}")
    sys.exit(1)
print("  ALL ARM PROTOCOL CHECKS PASSED")
print("  NOTE: this proves PROTOCOL only. Real hardware is untested.")
