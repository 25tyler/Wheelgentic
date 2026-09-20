"""tests/test_uv_fsm.py — dirt_mode=uv through the REAL state machine.

Every UV piece was tested in isolation (the mask, the pop rule, the latch
spread) but the mode had never RUN inside vision_loop. That is the mode you
switch to when a judge asks "is the dirt detection real?", so it should have
been exercised before being offered.
"""
import os, sys, threading, time, types
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "py"))
sys.path.insert(0, HERE)
import watchdog; watchdog.arm(120)

import cv2, numpy as np
from fake_roarm import FakeRoArm
import fixture; fixture.ensure_homography()

FAILS = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(label)

# BOTH INSIDE a 640x480 frame -- an earlier version put the wrist at x=780 and
# cv2.circle silently clipped two of the three tracers off the right edge. The
# detector then honestly reported 2 blobs and I nearly filed it as a merge bug.
# The x range also has to land inside the arm's reachable box (xmin=120mm)
# under the test homography, or every command trips the workspace clamp and
# the run drowns in warnings that are the GUARD WORKING, not a bug.
ELBOW, WRIST = (380.0, 240.0), (625.0, 256.0)
# >=48px apart at r=14 (measured sweep: closer and two circles genuinely
# touch, which is the detector being right, not a merge bug).
#
# DELIBERATELY OFF the hardcoded SPLOTCH_TS [0.34, 0.50, 0.66]. Every spot is
# >0.07 from every default -- the browser's match window -- so latching the
# defaults instead of the detected positions CANNOT pass. An earlier version
# used 0.28/0.50/0.72 and a planted "picked = list(SPLOTCH_TS)" went green:
# the assertions compared latched against fired, which agree with each other
# even when both are wrong.
SPOTS = (0.12, 0.26, 0.76)
# 0.08 not 0.07: at exactly the window edge a rounding difference decides it.
assert all(abs(s - d) > 0.08 for s in SPOTS for d in (0.34, 0.50, 0.66)), \
    "a tracer landed within the browser match window of a default splotch"


class TracerFeed:
    """A forearm carrying three tracer blobs, each with its OWN fade.

    PER-SPOT rather than one global FADE, so the driving loop below can wipe one
    spot at a time and wait for each pop before the next. uv_should_pop gates on
    the sponge being near a spot, so a wiped spot pops only when the arm next
    sweeps past it -- wiping on a fixed timer instead let two spots go before the
    first pop registered and the sequence read [67, 100, 100]: honest, but no
    longer showing the counter track one spot at a time.

    (This used to say the reason was cleanliness_pct() reading 100% at the first
    pop. True when the counter was derived from remaining tracer area, false now
    that it is the count of positions cleaned.)
    """
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
            r = max(1, int(14 * f))
            x = ELBOW[0] + t * (WRIST[0] - ELBOW[0])
            y = ELBOW[1] + t * (WRIST[1] - ELBOW[1])
            cv2.circle(img, (int(x), int(y)), r, (90, 255, 205), -1)
        j = (self.n % 7) * 0.35          # jitter: a static pose trips the
        return (img, (ELBOW[0] + j, ELBOW[1] - j),   # freshness watchdog
                (WRIST[0] + j, WRIST[1] + j), 0.033)

    def close(self): pass


fv = types.ModuleType("vision"); fv.PoseFeed = TracerFeed
for k, v in dict(L_SHOULDER=11, R_SHOULDER=12, L_ELBOW=13, R_ELBOW=14,
                 L_WRIST=15, R_WRIST=16).items():
    setattr(fv, k, v)
sys.modules["vision"] = fv

import scrubbot as S
from arm import Arm

print("=== 0. THE TRACER IS VISIBLE TO THE DETECTOR AT ALL ===")
import dirt as D
_f = TracerFeed(); _, _, _, _ = _f.read()
TracerFeed.FADE = {t: 1.0 for t in SPOTS}
_img = _f.read()[0]
_blobs = D.blobs(D.fluor_mask(_img))
check("all three tracers are visible to the detector", len(_blobs) == 3,
      f"{len(_blobs)} blobs, areas {[b['area'] for b in _blobs]}")

fake = FakeRoArm(); arm = Arm(port=fake.port); time.sleep(0.3)
S.CFG.data["dirt_mode"] = "uv"
S.CFG.data["scrub_seconds"] = 14.0
events = []
_real_fire = S.fire
S.fire = lambda l, t, c, ct: (events.append((round(t, 2), round(c))),
                              _real_fire(l, t, c, ct))[1]
args = types.SimpleNamespace(right_arm=False, model="", cam=0, delegate="CPU",
                             headless=True, no_contact_gate=True)
threading.Thread(target=S.vision_loop, args=(arm, args), daemon=True).start()
time.sleep(1.0)
S.ARMED = True; S._armed_at = time.time()

print("\n=== 1. UV MODE LATCHES THE POSITIONS IT ACTUALLY SEES ===")
deadline = time.time() + 12
while time.time() < deadline and not S._uv_targets:
    time.sleep(0.1)
latched = list(S._uv_targets)
check("it latched one target per DRAWN tracer", len(latched) == len(SPOTS),
      f"latched {[round(t,2) for t in latched]} vs drawn {list(SPOTS)}")
# Anchored to SPOTS -- the one fact the FSM cannot derive from its own state.
matched = [s for s in SPOTS if any(abs(s - t) < 0.05 for t in latched)]
check("every latched target sits on a tracer I actually drew",
      len(matched) == len(SPOTS),
      f"matched {[round(m,2) for m in matched]} of {list(SPOTS)}")

print("\n=== 2. FADING THE TRACER POPS THE SPLOTCHES ===")
n_before = len(events)
pcts = []
# Wipe one spot, then WAIT FOR ITS POP before wiping the next. uv_should_pop
# gates on the sponge being near the spot, so a wiped spot pops only when the
# arm next sweeps past it. Wiping on a fixed timer instead let two spots go
# before the first pop registered, and the percentage sequence read [67,100,
# 100] -- honest, but it no longer showed the counter tracking one spot at a
# time, which is the whole claim.
for t in SPOTS:
    want = len(events) + 1
    TracerFeed.FADE[t] = 0.0
    end = time.time() + 25
    while len(events) < want and time.time() < end:
        time.sleep(0.05)
    pcts.append(events[-1][1] if events else None)
time.sleep(1.0)
check("splotches popped as the tracer disappeared", len(events) > n_before,
      f"{len(events)-n_before} events after fading")
check("the counter reached 100", any(c >= 100 for _, c in events),
      str(events[-3:]))
# The POINT of uv mode: the counter must CLIMB as spots go rather than jump to
# 100 on the first pop. The number is the COUNT of positions cleaned -- the same
# expression the scripted path uses. It used to be derived from remaining tracer
# AREA, which reported a different number than the scripted path for the same
# event and only matched thirds here because these three blobs are equal-area
# (measured through the real detector: [609, 609, 609], spread 0).
seq = [c for _, c in events[n_before:]]
check("cleanliness climbed one third per wiped spot",
      seq == [33, 67, 100], f"sequence {seq} (want [33, 67, 100])")

print("\n=== 3. THE FINALE SWEEPS THE DETECTED POSITIONS ===")
# Not the hardcoded SPLOTCH_TS -- the browser moved its splotches to the
# detected ones, so firing the defaults would match nothing.
fired_ts = {t for t, _ in events}
on_tracer = [t for t in fired_ts if any(abs(t - s) < 0.05 for s in SPOTS)]
check("every fired position sits on a DRAWN tracer, not a default splotch",
      len(on_tracer) == len(fired_ts) == len(SPOTS),
      f"fired {sorted(fired_ts)} vs drawn {list(SPOTS)}")

# The browser only pops a splotch within 0.07 of an event, so UV mode MUST
# tell the page where the real tracer landed or the counter climbs while the
# splotches stay on screen.
placed = S.EVENT.get("place")
check("the browser was told to move its splotches to the detected spots",
      placed is not None and len(placed) == len(SPOTS)
      and all(any(abs(p - s) < 0.05 for p in placed) for s in SPOTS),
      f"place={placed}")

check("no workspace clamp fired -- the fixture limb is reachable",
      not arm.clamped_hard, "clamped_hard")

S.RUNNING = False
time.sleep(0.4)
arm.close(); fake.close()

print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
print("  UV MODE WORKS THROUGH THE REAL STATE MACHINE")
