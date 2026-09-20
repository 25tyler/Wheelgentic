"""tests/test_signals.py — SIGTERM must retreat the arm off the person.

THE WORST BUG THIS PROJECT HAS HAD. The shutdown retreat lives in main()'s
`finally:`. Python's default SIGTERM disposition terminates the interpreter
WITHOUT unwinding, so no `finally` and no `atexit` ever ran. Every
non-interactive stop path sends SIGTERM:

  - run.sh's cleanup() traps EXIT/INT/TERM and does `kill -TERM` on each child
  - README tells you to use SIGTERM for the background case
  - scrubbot PRINTS `pkill -f 'py/scrubbot.py'` to the operator, and pkill
    defaults to SIGTERM -- at exactly the moment they are already scrambling

Measured before the fix, arm at the demo contact pose (300,120,40 = 5mm INTO
the skin plane):

    SIGTERM -> finally ran: False, T:0 stops: 0, final z=40.0   (PARKED ON THE ARM)
    SIGINT  -> finally ran: True,  T:0 stops: 1, final z=123.5  (lifted)

The process vanishes, taking the 40Hz pump, the torque watchdog and the estop
key with it, while the ESP32 holds its last commanded pose forever. Nothing in
software can lift the sponge; the only recovery is cutting the 12V supply.

test_runsh.py already sent SIGTERM -- and PASSED, because it only asserted "no
orphaned children". It passed *because* the retreat was skipped. A test can
confirm the wrong half of a shutdown.
"""
import os, signal, subprocess, sys, textwrap, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "py"))
os.chdir(os.path.join(HERE, ".."))
import watchdog; watchdog.arm(150)
from fake_roarm import FakeRoArm

FAILS = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(label)

CONTACT = (300.0, 120.0, 40.0)      # 5mm into the skin plane

CHILD = r'''
import sys, time, signal
sys.path.insert(0, "/Users/tyler/Wheelgentic/py")
import scrubbot
# EXACTLY what main() installs -- ALL THREE. Importing the real handler means
# this test fails if someone deletes it, rather than testing a copy. SIGINT is
# listed explicitly because relying on Python's default KeyboardInterrupt here
# made the child reproduce the very bug under test: the default is deferred
# while the main thread is inside a C call, so under a loaded suite the retreat
# never ran and the sponge stayed at z=40.0 on the contact plane.
signal.signal(signal.SIGTERM, scrubbot._term_handler)
signal.signal(signal.SIGHUP,  scrubbot._term_handler)
signal.signal(signal.SIGINT,  scrubbot._term_handler)
from arm import Arm
a = Arm(port=sys.argv[1]); time.sleep(0.4)
a.set_target(300.0, 120.0, 40.0)
# WAIT FOR THE RATE LIMITER TO ARRIVE. A fixed 1.2s sleep left the arm still
# at HOME when the signal landed, so SIGTERM and SIGINT looked identical and
# the first version of this test proved nothing.
for _ in range(140):
    time.sleep(0.1)
    if abs(a.last_sent[2] - 40.0) < 1.0:
        break
print("CHILD_AT_CONTACT", flush=True)
try:
    while True:
        time.sleep(0.05)
except KeyboardInterrupt:
    pass
finally:
    print("CHILD_FINALLY", flush=True)
    a.go_home(); time.sleep(0.4); a.close()
'''
open("/tmp/_sigchild.py", "w").write(CHILD)


def run(sig, name):
    fake = FakeRoArm()
    # A FILE, NOT A PIPE. Reading p.stdout only after wait() truncated every
    # child but the last, so whichever signal ran last "passed" and the other
    # two "failed" -- reordering them moved the pass, which is how I knew the
    # harness and not the product was deciding the result.
    logp = f"/tmp/_sig_{name}.log"
    fh = open(logp, "w")
    p = subprocess.Popen([sys.executable, "/tmp/_sigchild.py", fake.port],
                         stdout=fh, stderr=subprocess.STDOUT,
                         text=True, start_new_session=True)
    # start_new_session: without it a shell-launched child inherits SIGINT
    # handling that makes the three signals look the same.
    at_contact = False
    for _ in range(200):
        time.sleep(0.1)
        try:
            if "CHILD_AT_CONTACT" in open(logp).read():
                at_contact = True
                break
        except FileNotFoundError:
            pass
    time.sleep(0.3)      # let it reach the try/except, not just print
    n_stop = len(fake.of_type(0))
    n_pts = len(fake.of_type(1041))
    p.send_signal(sig)
    # WAIT FOR IT TO EXIT, do not race it. A fixed 2.5s sleep then
    # p.kill() cut the retreat short under load and made SIGTERM look
    # broken on some runs and fine on others -- the classic flaky test
    # that hides the real answer. go_home() takes ~1.5s at 240mm/s.
    try:
        p.wait(timeout=12)
    except subprocess.TimeoutExpired:
        p.kill()
    time.sleep(1.5)          # let the pty drain the last writes
    fh.close()
    out = open(logp).read()
    pts = fake.of_type(1041)
    r = dict(name=name, at_contact=at_contact,
             finally_ran="CHILD_FINALLY" in out,
             stops=len(fake.of_type(0)) - n_stop,
             moves=len(pts) - n_pts,
             final_z=pts[-1]["z"] if pts else None)
    fake.close()
    return r


print("=== main() ACTUALLY REGISTERS THE HANDLERS ===")
# The child below installs them by hand so it can be a small, fast process.
# That means it does NOT prove main() wires them up -- and a plant that
# deleted the signal.signal() line from main() passed the whole file. Check
# the registration separately, against the real source.
_src = open("py/scrubbot.py").read()
for _sig in ("SIGTERM", "SIGHUP", "SIGINT"):
    check(f"main() installs a handler for {_sig}",
          f"signal.signal(signal.{_sig}, _term_handler)" in _src,
          "the retreat in main()'s finally: is unreachable without it")
# Slice to the NEXT top-level def, not a byte count -- the docstring is long
# and a fixed window sliced straight past the raise, failing on correct code.
_body = _src.split("def _term_handler")[1].split("\ndef ")[0] if "def _term_handler" in _src else ""
check("the handler raises rather than returning",
      "raise KeyboardInterrupt" in _body,
      "a handler that returns lets the process keep running with the sponge down")

print("\n=== THE ARM REACHES THE CONTACT POSE FIRST ===")
# If it never got there, all three signals look alike and the test is vacuous.
res = {}
for sig, name in ((signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT"),
                  (signal.SIGHUP, "SIGHUP")):
    r = run(sig, name)
    res[name] = r
    check(f"{name}: the arm was at contact depth before the signal",
          r["at_contact"], f"final_z={r['final_z']}")

print("\n=== EVERY SIGNAL LIFTS THE SPONGE OFF THE PERSON ===")
for name in ("SIGTERM", "SIGINT", "SIGHUP"):
    r = res[name]
    check(f"{name}: the shutdown retreat ran", r["finally_ran"])
    check(f"{name}: a stop was sent", r["stops"] >= 1, f"{r['stops']} T:0")
    # NOT a raw command count. PLANTED go_home() as a total no-op and this
    # still read 17-18 commands and passed: the 40Hz pump emits whether or not
    # anything retreats, so a count can never go red. Assert the arm moved AWAY
    # FROM CONTACT instead -- that is what the label claims.
    check(f"{name}: the arm MOVED after the signal",
          r["moves"] > 0 and r["final_z"] is not None
          and abs(r["final_z"] - CONTACT[2]) > 1.0,
          f"{r['moves']} commands, final z={r['final_z']} vs contact {CONTACT[2]}")
    # THE ASSERTION THAT MATTERS: not "it shut down cleanly" but "the sponge
    # is no longer pressing into someone's forearm".
    check(f"{name}: the arm ended ABOVE the skin plane",
          r["final_z"] is not None and r["final_z"] > CONTACT[2] + 40,
          f"z={r['final_z']} (contact was {CONTACT[2]})")

print("\n=== A SECOND SIGNAL MUST NOT ABORT THE RETREAT ===")
# Found by auditing my OWN SIGTERM fix. _term_handler stays installed and still
# raises, so a SECOND signal -- the impatient operator hitting ^C again when
# the arm does not visibly move, or re-running pkill -- lands INSIDE the
# finally block and cuts the retreat short. Measured with the identical
# handler+finally shape: a second SIGTERM at 0.05s AND at 0.30s both gave no
# stop and no clean shutdown, and the arm reaches only z~50-65mm of the 0.4s
# retreat. The skin plane is z=45, so the sponge is STILL on the forearm --
# the exact end state the SIGTERM fix eliminated, reached through the same key
# the operator is already pressing.
# BEHAVIOUR, not a token. Grepping the source for "signal.SIG_IGN" passed a
# plant that emptied the loop it sits in -- the string was still there, so the
# check was green on code that disarmed nothing. The child now CALLS the real
# scrubbot.disarm_signals(), so an empty loop actually shows up.
_dbl = ('import sys, time, signal\n'
        'sys.path.insert(0, "/Users/tyler/Wheelgentic/py")\n'
        'import scrubbot\n'
        'signal.signal(signal.SIGTERM, scrubbot._term_handler)\n'
        'signal.signal(signal.SIGHUP, scrubbot._term_handler)\n'
        'signal.signal(signal.SIGINT, scrubbot._term_handler)\n'
        'print("READY", flush=True)\n'
        'try:\n'
        '    while True: time.sleep(0.05)\n'
        'except KeyboardInterrupt:\n'
        '    pass\n'
        'finally:\n'
        '    scrubbot.disarm_signals()      # THE REAL ONE\n'
        '    time.sleep(0.4)                # stands in for go_home()\n'
        '    print("STOP SENT", flush=True)\n')
open("/tmp/_dblsig.py", "w").write(_dbl)

# It must actually disarm something, not just run.
import scrubbot as _S
check("disarm_signals() disarms all three signals", _S.disarm_signals() == 3,
      "an empty loop returns 0 and the retreat stays interruptible")
# ...AND main() actually calls it, FIRST. The child below calls the function
# directly, so it proves the function works but not that it is wired in --
# deleting the call from main()'s finally passed the whole file.
# SLICE FROM def main() FIRST. `_src.split("    finally:")[-1]` took the LAST
# finally: in the file, which is main()'s only by accident of ordering --
# measured: appending one try/finally after main() collapses this from 1157
# chars to 14, and both disarm_signals() and arm.go_home() vanish, so check
# 205 goes red for a bogus reason and 207's ternary silently yields False.
_main_src = _src.split("def main()")[-1]
_fin = _main_src.split("    finally:")[-1]
check("main()'s finally calls disarm_signals()", "disarm_signals()" in _fin,
      "the retreat stays interruptible however good the function is")
check("and calls it BEFORE the retreat starts",
      _fin.index("disarm_signals()") < _fin.index("arm.go_home()")
      if ("disarm_signals()" in _fin and "arm.go_home()" in _fin) else False,
      "disarming after go_home() leaves the same window open")


def _second_signal(gap):
    p2 = subprocess.Popen([sys.executable, "/tmp/_dblsig.py"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, start_new_session=True)
    while "READY" not in (p2.stdout.readline() or ""):
        pass
    p2.send_signal(signal.SIGTERM)
    if gap is not None:
        time.sleep(gap)
        p2.send_signal(signal.SIGTERM)
    try:
        p2.wait(timeout=8)
    except subprocess.TimeoutExpired:
        p2.kill()
    return "STOP SENT" in (p2.stdout.read() or "")


check("one signal completes the retreat (control)", _second_signal(None))
for _gap in (0.05, 0.30):
    check(f"a SECOND signal at {_gap}s does not abort the retreat",
          _second_signal(_gap),
          "the sponge is still on the forearm at z~50-65mm")

print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
print("  EVERY SIGNAL RETREATS THE ARM")
