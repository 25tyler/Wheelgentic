"""tests/test_mode_switch.py — flipping dirt_mode MID-SCRUB.

THE RECOVERY CARD TELLS YOU TO DO THIS: "UV detector finds nothing -> edit
config.json -> dirt_mode: scripted. Hot-reloads, no restart." A documented
recovery step that has never been executed is the dangerous kind, and this one
was broken.

Measured before the fix: UV latches at 0.115/0.254/0.756 and the browser moves
its splotches there. Flip to "scripted" and the FSM fires the hardcoded
SPLOTCH_TS 0.34/0.50/0.66 instead -- none within the page's 0.07 match window.
All three splotches stayed on screen while the counter ran to 100%. A judge
sees a full green bar over a visibly dirty arm.

The fix keys both the scrub pops and the finale on what the browser WAS TOLD
(_uv_targets), not on what dirt_mode says right now.

THE REVERSE FLIP (scripted -> uv) was measured too and is FINE: the page still
has its splotches at the defaults, UV latches the detected positions on top,
and six events fire instead of three. Noisy, but every on-screen splotch pops
and the counter reaches 100. Not worth a fix -- the direction that matters is
uv -> scripted, which is the one the recovery card tells you to do.
"""
import os, sys, threading, time, types
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "py"))
sys.path.insert(0, HERE)
import watchdog; watchdog.arm(150)

import cv2, numpy as np
from fake_roarm import FakeRoArm
import fixture; fixture.ensure_homography()

FAILS = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(label)

ELBOW, WRIST = (380.0, 240.0), (625.0, 256.0)
SPOTS = (0.12, 0.26, 0.76)
assert all(abs(s - d) > 0.08 for s in SPOTS for d in (0.34, 0.50, 0.66)), \
    "a tracer landed within the browser match window of a default splotch"


class TracerFeed:
    FADE = {t: 1.0 for t in SPOTS}

    def __init__(self, *a, **kw): self.n = 0

    def read(self, *a, **kw):
        self.n += 1
        time.sleep(0.004)
        img = np.full((480, 640, 3), 25, np.uint8)
        cv2.line(img, (int(ELBOW[0]), int(ELBOW[1])),
                 (int(WRIST[0]), int(WRIST[1])), (96, 122, 152), 74)
        for t in SPOTS:
            f = TracerFeed.FADE[t]
            if f <= 0.05:
                continue
            x = ELBOW[0] + t * (WRIST[0] - ELBOW[0])
            y = ELBOW[1] + t * (WRIST[1] - ELBOW[1])
            cv2.circle(img, (int(x), int(y)), max(1, int(14 * f)),
                       (90, 255, 205), -1)
        j = (self.n % 7) * 0.35
        return (img, (ELBOW[0] + j, ELBOW[1] - j),
                (WRIST[0] + j, WRIST[1] + j), 0.033)

    def close(self): pass


fv = types.ModuleType("vision"); fv.PoseFeed = TracerFeed
for k, v in dict(L_SHOULDER=11, R_SHOULDER=12, L_ELBOW=13, R_ELBOW=14,
                 L_WRIST=15, R_WRIST=16).items():
    setattr(fv, k, v)
sys.modules["vision"] = fv

import scrubbot as S
from arm import Arm

fake = FakeRoArm(); arm = Arm(port=fake.port); time.sleep(0.3)
S.CFG.data["dirt_mode"] = "uv"
S.CFG.data["scrub_seconds"] = 20.0
events = []
_real_fire = S.fire
S.fire = lambda l, t, c, ct: (events.append((round(t, 3), round(c))),
                              _real_fire(l, t, c, ct))[1]
args = types.SimpleNamespace(right_arm=False, model="", cam=0, delegate="CPU",
                             headless=True, no_contact_gate=True)
threading.Thread(target=S.vision_loop, args=(arm, args), daemon=True).start()
time.sleep(1.0)
S.ARMED = True; S._armed_at = time.time()

print("=== 1. UV LATCHES AND MOVES THE BROWSER'S SPLOTCHES ===")
deadline = time.time() + 12
while time.time() < deadline and not S._uv_targets:
    time.sleep(0.1)
placed = S.EVENT.get("place")
check("the browser was given the detected positions",
      placed is not None and len(placed) == len(SPOTS), f"place={placed}")

print("\n=== 2. FLIP dirt_mode -> scripted MID-SCRUB ===")
S.CFG.data["dirt_mode"] = "scripted"
n_at_flip = len(events)
# ARMED goes False exactly on entry to RETREAT (one cycle per keypress), so
# it is the signal that the scrub finished rather than a fixed sleep.
deadline = time.time() + 60
while time.time() < deadline and S.ARMED:
    time.sleep(0.2)
check("the cycle finished (ARMED cleared -> RETREAT)", not S.ARMED)
after = events[n_at_flip:]
check("the scrub still completed after the flip", len(after) > 0,
      f"{len(after)} events after the flip")

print("\n=== 3. NO SPLOTCH IS LEFT ON SCREEN ===")
# THE bug. The page pops a splotch only within 0.07 of an event, so any
# on-screen position with no matching event stays visible while the counter
# claims 100%.
onscreen = placed or list(S.SPLOTCH_TS)
fired = sorted({t for t, _ in events})
orphans = [p for p in onscreen
           if not any(abs(p - f) < 0.07 for f in fired)]
check("every splotch the browser has got a matching event", not orphans,
      f"orphans {orphans} — onscreen {onscreen} vs fired {fired}")
check("nothing fired at a hardcoded default the page does not have",
      all(any(abs(f - p) < 0.07 for p in onscreen) for f in fired),
      f"fired {fired} vs onscreen {onscreen}")
check("the counter reached 100", any(c >= 100 for _, c in events),
      str(events[-2:]))

S.RUNNING = False
time.sleep(0.4)
arm.close(); fake.close()

print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
print("  MID-SCRUB dirt_mode FLIP LEAVES NOTHING ON SCREEN")
