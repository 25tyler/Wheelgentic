"""tests/test_dirt.py — the UV detector on synthetic frames.

No UV lamp needed to prove the MATH. Synthesises a dim scene with a skin-tone
forearm and fluorescent blobs, then checks detection, limb assignment,
off-limb rejection, and the cleanliness guards.

The important case: a YELLOW highlighter (pyranine) emits GREEN. A blue-excess
detector misses it entirely. This suite proves the HSV approach catches both.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import watchdog; watchdog.arm(90)   # SIGALRM: a daemon-thread
# watchdog once let a test run for 3h21m and block a whole suite.
import math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py"))
import numpy as np, cv2
import dirt as D

fails = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: fails.append(label)

W, Hh = 640, 480
ELBOW, WRIST = (180.0, 240.0), (460.0, 260.0)

def scene(spots, skin_bgr=(96, 122, 152), bg=18):
    """Dim room, a skin-tone forearm band, plus fluorescent spots.
    spots: list of (x, y, radius, bgr)"""
    img = np.full((Hh, W, 3), bg, np.uint8)
    cv2.line(img, (int(ELBOW[0]), int(ELBOW[1])), (int(WRIST[0]), int(WRIST[1])),
             skin_bgr, 74)                       # forearm ~37px half-width
    for (x, y, r, c) in spots:
        cv2.circle(img, (int(x), int(y)), int(r), c, -1)
    return cv2.GaussianBlur(img, (5, 5), 0)

def on_limb(t):        # point at parametric t along the forearm
    return (ELBOW[0] + t*(WRIST[0]-ELBOW[0]), ELBOW[1] + t*(WRIST[1]-ELBOW[1]))

# Measured-ish emission colours (BGR).
GLO   = (255, 235, 150)   # blue-white  ~450nm
YELLO = ( 90, 255, 205)   # green       ~510nm  <- the one blue-excess misses
GREENP= ( 80, 255, 120)

print("=== 1. BASELINE: clean skin must yield NOTHING ===")
m = D.fluor_mask(scene([]))
b = D.blobs(m)
check("no false positives on bare skin", len(b) == 0, f"{len(b)} blobs")
check("mask is essentially empty", int(m.sum()) // 255 < 50, f"{int(m.sum())//255} px")

# EVERY TERM OF THE MASK MUST BE LOAD-BEARING. The original suite only ever
# showed DIM skin, so the V>190 and S>60 terms were never exercised: the hue
# row alone carried every case and a broken brightness threshold would pass.
# Under a UV lamp an over-exposed forearm is exactly what you get.
for label, skin in [("bright skin",   (150, 190, 235)),
                    ("very bright skin", (185, 215, 250)),
                    ("pale skin",     (170, 200, 225)),
                    ("dark skin",     ( 40,  62,  92))]:
    bb = D.blobs(D.fluor_mask(scene([], skin_bgr=skin)))
    check(f"no false positive on {label}", len(bb) == 0,
          f"{len(bb)} blobs — a bright forearm must not read as tracer")

print("\n=== 2. DETECTION across fluorophores ===")
for name, col in [("Glo Germ (blue-white)", GLO), ("yellow highlighter (GREEN)", YELLO),
                  ("green paint", GREENP)]:
    x, y = on_limb(0.5)
    bl = D.blobs(D.fluor_mask(scene([(x, y, 16, col)])))
    check(f"detects {name}", len(bl) == 1, f"{len(bl)} blobs, area={bl[0]['area'] if bl else 0}")

print("\n=== 3. WHY NOT BLUE-EXCESS (the documented trap) ===")
for name, col in [("Glo Germ", GLO), ("yellow highlighter", YELLO)]:
    x, y = on_limb(0.5)
    img = scene([(x, y, 16, col)])
    px = img[int(y), int(x)].astype(int)
    be = px[0] - (px[2] + px[1]) / 2.0
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[int(y), int(x)]
    print(f"    {name:22} blue_excess={be:+7.1f}   S={hsv[1]:3d} V={hsv[2]:3d}")
be_y = (lambda p: p[0]-(p[2]+p[1])/2.0)(scene([(*on_limb(.5),16,YELLO)])[int(on_limb(.5)[1]),int(on_limb(.5)[0])].astype(int))
check("blue-excess FAILS on the yellow highlighter (negative)", be_y < 0,
      f"blue_excess={be_y:+.1f} — a positive threshold misses it entirely")
check("HSV catches it anyway",
      len(D.blobs(D.fluor_mask(scene([(*on_limb(.5), 16, YELLO)])))) == 1)

print("\n=== 4. LIMB ASSIGNMENT — t must match the cartoon ===")
for want in (0.2, 0.5, 0.8):
    x, y = on_limb(want)
    bl = D.blobs(D.fluor_mask(scene([(x, y, 15, GLO)])))
    a = D.assign(bl[0], *ELBOW, *WRIST) if bl else None
    ok = a and abs(a["t"] - want) < 0.05
    check(f"blob at t={want} reports t≈{want}", bool(ok),
          f"got t={a['t'] if a else 'None'}")

print("\n=== 5. OFF-LIMB REJECTION (both failure modes) ===")
# (a) beyond the ends. CRITICAL: place it ON THE LIMB AXIS, extended past the
# elbow, so perp≈0 and ONLY the t_raw check can reject it. An off-axis blob is
# caught by the perpendicular test instead, and then removing the t_raw guard
# still "passes" — plant-verified: my first version of this case did exactly
# that and the plant did not fire.
ux = (WRIST[0]-ELBOW[0]); uy = (WRIST[1]-ELBOW[1])
L = math.hypot(ux, uy); ux, uy = ux/L, uy/L
far = (ELBOW[0] - ux*150, ELBOW[1] - uy*150, 15, GLO)     # perp ~ 0
bl = D.blobs(D.fluor_mask(scene([far])))
_t, _perp, _traw = D.project_to_limb(bl[0]["cx"], bl[0]["cy"], *ELBOW, *WRIST) if bl else (0,0,0)
a = D.assign(bl[0], *ELBOW, *WRIST) if bl else None
check("ON-AXIS blob 150px past the elbow is rejected", a is None,
      f"perp={_perp:.0f}px t_raw={_traw:.2f} — only the t_raw guard can catch this")
check("  (and it really was on-axis, so perp could not have caught it)",
      _perp < 20, f"perp={_perp:.1f}px")
# (b) beside the limb — on the table
side = (on_limb(0.5)[0], on_limb(0.5)[1] + 120, 15, GLO)
bl = D.blobs(D.fluor_mask(scene([side])))
a = D.assign(bl[0], *ELBOW, *WRIST) if bl else None
check("blob 120px BESIDE the limb (on the table) is rejected", a is None,
      f"got {a}" if a else "rejected")
# (c) a real on-limb blob still accepted
bl = D.blobs(D.fluor_mask(scene([(*on_limb(0.6), 15, GLO)])))
a = D.assign(bl[0], *ELBOW, *WRIST) if bl else None
check("a genuine on-limb blob is still ACCEPTED", a is not None)

print("\n=== 6. CLEANLINESS GUARDS ===")
check("zero initial area -> 100 (not a divide-by-zero crash)",
      D.cleanliness(0, 0) == 100.0)
check("tiny initial area -> 100", D.cleanliness(5, 5) == 100.0)
check("half removed -> 50", abs(D.cleanliness(1000, 500) - 50.0) < 0.01)
check("all removed -> 100", D.cleanliness(1000, 0) == 100.0)
check("GROWN blob clamps at 0, never negative",
      D.cleanliness(1000, 1400) == 0.0,
      "otherwise the projector counter runs BACKWARDS")

print("\n=== 7. TRACKER over a simulated scrub ===")
tr = D.DirtTracker()
areas = []
for frac in (1.0, 1.0, 0.75, 0.5, 0.25, 0.0):     # tracer shrinking
    spots = [(*on_limb(t), max(1, int(15*math.sqrt(frac))), GLO)
             for t in (0.25, 0.55, 0.8)] if frac > 0 else []
    tr.observe(scene(spots), ELBOW, WRIST)
    areas.append((round(frac, 2), tr.current_area, round(tr.cleanliness_pct(), 1)))
for f, ar, pc in areas:
    print(f"    tracer={f:<5} area={ar:<6} cleanliness={pc}%")
pcts = [p for _, _, p in areas]
check("starts at 0% clean", pcts[0] == 0.0)
check("ends at 100% clean", pcts[-1] == 100.0)
check("monotonically non-decreasing", all(b >= a for a, b in zip(pcts, pcts[1:])),
      str(pcts))
check("3 spots found while dirty", len(tr.spots) == 0 and areas[0][1] > 0)

tr.reset()
check("reset clears state", tr.initial_area == 0 and tr.spots == [])

print("\n=== 8. UV POP LOGIC survives occlusion (the arm blocks its own view) ===")
# The robot arm OCCLUDES the exact region it is scrubbing, so a tracer spot
# drops out of view constantly. An earlier version popped the instant a spot
# was not detected, which meant every splotch popped on the first pass whether
# or not anything was cleaned. Mirrors py/scrubbot.py's UV branch.
# Calls the SHIPPED decision function. The previous version re-implemented
# the rules inside the test, which is a tautology -- it would have passed with
# scrubbot's copy broken in any way the test's copy did not share.
from scrubbot import uv_should_pop

def sim(sequence):
    """sequence: list of (live_ts, sponge_u). -> set of cleaned targets."""
    targets = [0.30, 0.50, 0.70]
    missing = {t: 0 for t in targets}
    cleaned = set()
    for live, u in sequence:
        for st in targets:
            if st in cleaned:
                continue
            pop, missing[st] = uv_should_pop(st, live, u, missing[st])
            if pop:
                cleaned.add(st)
    return cleaned

check("brief occlusion (5 frames) does NOT pop a splotch",
      sim([([0.30, 0.70], 0.50)] * 5) == set(),
      "the arm blocks its own view constantly")
check("sustained removal UNDER the sponge DOES pop",
      sim([([0.30, 0.70], 0.50)] * 10) == {0.50})
check("occlusion then reappearance resets the counter",
      sim([([0.30, 0.70], 0.50)] * 5 + [([0.30, 0.50, 0.70], 0.50)] * 3
          + [([0.30, 0.70], 0.50)] * 5) == set(),
      "5 + 5 missing frames with a gap must not reach 8")
check("a spot vanishing AWAY from the sponge does not pop",
      0.70 not in sim([([0.30, 0.50], 0.30)] * 25),
      "a moved sleeve is not cleaning")

# THE PROXIMITY TOLERANCE ITSELF WAS UNGUARDED. The four cases above never
# exercise near_tol: in every one of them the targets sit at distance 0.00 or
# >= 0.20 from the sponge, so their verdicts are identical for any tolerance
# from ~0.01 to ~0.19. Measured by sweeping it -- four different values produced
# four identical results. And `near_tol` appears nowhere else in the repo except
# uv_should_pop's own signature, so nothing anywhere pinned it.
#
# That matters now that the sponge's position is the PRIMARY clean signal rather
# than a filter on a disappearance. A pair straddling the boundary fixes it:
# 0.16 must clean, 0.20 must not. Swept against the shipped function, this pair
# accepts ONLY 0.18 -- 0.10/0.14/0.16 fail the inside case, 0.20/0.22/0.25 fail
# the outside one. Two-sided by construction.
def _dwell_to_clean(target, sponge_u, frames=12):
    """Drive the SHIPPED decision directly: target never visible, sponge fixed."""
    n = 0
    for _ in range(frames):
        clean, n = uv_should_pop(target, [], sponge_u, n)
        if clean:
            return True
    return False

check("a spot just INSIDE the proximity tolerance gets cleaned",
      _dwell_to_clean(0.50, 0.50 + 0.16) is True,
      "distance 0.16 < near_tol 0.18 -- the sponge reached it")
check("a spot just OUTSIDE it does NOT",
      _dwell_to_clean(0.50, 0.50 + 0.20) is False,
      "distance 0.20 >= near_tol 0.18 -- the sponge never reached it")

# WHAT fire() PUTS ON THE WIRE, driven through the SHIPPED function.
#
# It used to do `"clean": int(clean)`. int() TRUNCATES, and every caller passes a
# raw float -- 100 * len(_cleaned) / len(targets). With the shipped THREE
# splotches, 2/3 is 66.666..., so the socket carried 66 where 67 is correct.
# Measured before the fix: raw [33.33, 66.67, 100.0] went out as [33, 66, 100].
#
# NO TEST COULD SEE IT, because the three that exercise this path all REPLACE
# the function with a stub that rounds: test_uv_fsm.py:101 and
# test_mode_switch.py:85 use round(c), test_consent_latch.py:460 drops the value.
# A stub cannot catch a conversion bug in the thing it replaces -- the same
# tautology this file warns about for uv_should_pop.
#
# So this guard calls the REAL fire() and reads the REAL queue.
import scrubbot as _S
_S.EVENT["pops"] = []
for _n in (1, 2, 3):
    _S.fire("forearm_L", 0.5, 100 * _n / 3, True)
_wire = [p["clean"] for p in _S.EVENT["pops"]]
check("fire() rounds the percentage onto the wire, it does not truncate",
      _wire == [33, 67, 100],
      f"wire={_wire} -- int() would send [33, 66, 100] for three splotches")
_S.EVENT["pops"] = []

print("\n=== 9. UV LATCHING picks spots SPREAD along the limb ===")
# sorted(live)[:3] took the three LOWEST t values, so with five detected blobs
# the two nearest the wrist were silently dropped and could never be cleaned.
# Measured before the fix: [0.3,0.4,0.5,0.6,0.7] -> [0.3,0.4,0.5].
sys.path.insert(0, os.path.join(HERE if 'HERE' in dir() else
                                os.path.dirname(os.path.abspath(__file__)),
                                "..", "py"))
from scrubbot import _spread_pick

cases = [
    ("five blobs spread across the limb", [0.3, 0.4, 0.5, 0.6, 0.7], 3),
    ("exactly three",                     [0.34, 0.50, 0.66],        3),
    ("two blobs",                         [0.2, 0.8],                3),
    ("one big blob",                      [0.5],                     3),
    ("eight blobs",                       [i/10 for i in range(2, 10)], 3),
]
for label, live, n in cases:
    got = _spread_pick(live, n)
    check(f"{label}: picks <= {n}", len(got) <= n, str([round(x,2) for x in got]))
    check(f"{label}: picks are in range", all(x in live for x in got))
    if len(live) > n:
        span_all = max(live) - min(live)
        span_got = max(got) - min(got)
        check(f"{label}: picks SPAN the limb (not clustered)",
              span_got >= span_all * 0.8,
              f"picked span {span_got:.2f} of available {span_all:.2f}")

print("\n=== 10. THE CAM=fake TRACER IS DETECTABLE (tools/tune_dirt.py) ===")
# tools/tune_dirt.py now accepts CAM=fake so the UV threshold UI can be
# learned with no camera and no lamp. That is only useful if the synthetic
# tracer actually trips the REAL detector -- otherwise the tuner shows an
# empty mask and teaches nothing.
sys.path.insert(0, os.path.join(HERE, "..", "py"))
from fakecam import FakeCapture as _FC

class _TracerCam(_FC):
    def read(self):
        ok, img = super().read()
        h, w = img.shape[:2]
        for fx, fy in ((0.72, 0.42), (0.76, 0.48), (0.80, 0.54)):
            cv2.circle(img, (int(w * fx), int(h * fy)), 11, (90, 255, 205), -1)
        return True, img

_c = _TracerCam()
_ok, _frame = _c.read()
_blobs = D.blobs(D.fluor_mask(_frame))
check("the synthetic tracer trips the REAL detector", len(_blobs) >= 3,
      f"{len(_blobs)} blobs — the tuner needs something to show")
check("and the blobs are a plausible size",
      all(100 < b["area"] < 5000 for b in _blobs) if _blobs else False,
      str([b["area"] for b in _blobs[:3]]))

print("\n=== EVERY KEY tune_dirt.py PRINTS IS ACTUALLY READ ===")
# tune_dirt.py tells the operator to paste five keys into config.json.
# dirt_skin_hue_max and dirt_skin_hue_min were read by NOTHING: DirtTracker did
# not accept them and fluor_mask() always got its module defaults. An operator
# whose venue lighting shifted skin hue would tune the slider, paste the
# numbers, see no change and have no way to tell why. Hue is the discriminator
# the whole detector rests on -- the last control that should be dead.
import re
_tuner = open("tools/tune_dirt.py").read()
_sb = open("py/scrubbot.py").read()
# POPULATION FLOOR. `for k in set(re.findall(...)): check(...)` reports success
# when the population is EMPTY -- the body never runs, nothing is asserted, and
# the section prints clean. Measured 5 dirt_* keys in the tuner today, all read by
# the FSM. If the tuner is rewritten, renamed, or the pattern drifts, this reds
# instead of silently asserting nothing about the hue controls the whole detector
# rests on.
_tuner_keys = sorted(set(re.findall(r'"(dirt_[a-z_]+)"', _tuner)))
check("the tuner names dirt_* keys at all", len(_tuner_keys) >= 3,
      f"{len(_tuner_keys)} keys -- an empty population asserts nothing and passes")
for _k in _tuner_keys:
    check(f"{_k} is read by the FSM", _k in _sb,
          "the tuner prints it but nothing consumes it")

# BEHAVIOUR, not just wiring: widening the skin band past the tracer's hue
# must actually make it vanish.
_img = np.full((480, 640, 3), 25, np.uint8)
cv2.line(_img, (380, 240), (625, 256), (96, 122, 152), 74)   # skin, H=14
for _t in (0.2, 0.5, 0.8):
    cv2.circle(_img, (int(380 + _t * 245), int(240 + _t * 16)), 14,
               (90, 255, 205), -1)                            # tracer, H=39
_n_default = len(D.DirtTracker().observe(_img, (380, 240), (625, 256)))
_n_wide = len(D.DirtTracker(skin_lo=45).observe(_img, (380, 240), (625, 256)))
check("the default hue band finds the tracer", _n_default == 3, f"{_n_default}")
check("widening skin_lo past H=39 makes it vanish -- the control is LIVE",
      _n_wide == 0, f"{_n_wide} spots still found")

print("\n=== THE THRESHOLDS ARE HOT, NOT JUST WIRED ===")
# WIRED IS NOT HOT. All five dirt_* keys were read from config -- at STARTUP,
# once, into a DirtTracker built before the frame loop. Measured: built at
# v_min=190, edited config.json to 250, CFG.reload() returned True and reported
# 250, and the tracker still held 190. Meanwhile the comment directly above
# that constructor promised "edit config.json and it switches WITHOUT a
# restart", and tools/tune_dirt.py tells the operator to paste these numbers
# after tuning under a real 365nm lamp. On the day, that paste did nothing --
# in the one mode a judge actually asks about ("is the dirt detection real?").
_sb_src = open("py/scrubbot.py").read()
# GREP THE CALL SHAPE THAT EXISTS, NOT THE ONE I REMEMBER WRITING. These five
# originally read `tracker.v_min = CFG.data.get("dirt_v_min", ...)`. Adding the
# coercion wrapper renamed the call to _num(...), and this guard failed on
# CORRECT code -- a guard naming an expression is a claim about that
# expression, and mine went stale the moment I improved the line it watched.
for _attr, _key in (("v_min", "dirt_v_min"), ("s_min", "dirt_s_min"),
                    ("min_area", "dirt_min_area"),
                    ("skin_lo", "dirt_skin_hue_max"),
                    ("skin_hi", "dirt_skin_hue_min")):
    check(f"{_key} is refreshed per frame, not only at startup",
          f'tracker.{_attr} = _num("{_key}"' in _sb_src
          or f'tracker.{_attr} = CFG.data.get("{_key}"' in _sb_src,
          "tuning under the lamp and pasting would need a restart")

# The refresh must ASSIGN, never rebuild: a fresh DirtTracker would wipe
# initial_area, the latched spots and the cleanliness baseline mid-cycle, so
# the counter would jump backwards the moment anyone saved the file.
_loop = _sb_src[_sb_src.index("def vision_loop"):]
check("the refresh assigns rather than rebuilding the tracker",
      _loop.count("D.DirtTracker(") == 1,
      "a rebuild inside the loop resets the cleanliness baseline")

_t = D.DirtTracker(v_min=190, s_min=60, min_area=80)
_t.initial_area, _t.spots = 1234, [{"t": 0.5}]
_t.v_min = 250
check("assignment keeps the cleanliness baseline intact",
      _t.initial_area == 1234 and len(_t.spots) == 1,
      f"initial_area={_t.initial_area} spots={len(_t.spots)}")

print("\n=== A BAD CONFIG EDIT MUST NOT KILL THE VISION THREAD ===")
# I WIDENED THIS BLAST RADIUS MYSELF, then found it by re-reading my own diff.
# Making the five dirt_* thresholds refresh per frame fixed a real freeze --
# but a string or null in any of them raises UFuncTypeError/TypeError inside
# fluor_mask, and that now runs EVERY FRAME inside vision_loop rather than once
# at startup. config.json is the file whose own _comment says "HOT-RELOADS --
# edit mid-demo if needed", so a venue typo would kill tracking mid-scrub
# instead of failing before anyone was watching.
_sb4 = open("py/scrubbot.py").read()
check("the per-frame refresh coerces rather than assigning raw config",
      "def _num(key, default)" in _sb4 and 'tracker.v_min = _num(' in _sb4,
      "a string in config.json would crash fluor_mask every frame")

def _num_probe(v, default):
    try:    return type(default)(v)
    except (TypeError, ValueError): return default

_img = np.full((60, 80, 3), 40, np.uint8); _img[20:40, 30:50] = (250, 250, 250)
for _bad in ("190", "abc", None, 190.5):
    _t = D.DirtTracker()
    _t.v_min = _num_probe(_bad, D.V_MIN)
    try:
        D.blobs(D.fluor_mask(_img, _t.v_min, _t.s_min, _t.skin_lo, _t.skin_hi),
                _t.min_area)
        _ok = True
    except Exception:
        _ok = False
    check(f"config v_min={_bad!r} survives coercion", _ok,
          "this value would kill the vision thread mid-scrub")

print("\n=== SHIPPED config.json MATCHES THE MODULE DEFAULTS ===")
# I SHIPPED INVENTED NUMBERS ONCE. Adding the two hue keys to config.json, I
# took 14/20 from the directive's measured SKIN-HUE OBSERVATIONS instead of
# dirt.py's band constants (26/160) -- which would have silently narrowed the
# discriminator the whole detector rests on, out of the box, with no error.
import json as _json
_cfg = _json.load(open("config.json"))
for _key, _const in (("dirt_v_min", D.V_MIN), ("dirt_s_min", D.S_MIN),
                     ("dirt_min_area", D.MIN_AREA),
                     ("dirt_skin_hue_max", D.SKIN_HUE_MAX),
                     ("dirt_skin_hue_min", D.SKIN_HUE_MIN)):
    if _key in _cfg:
        check(f"config.json {_key}={_cfg[_key]} matches dirt.py ({_const})",
              _cfg[_key] == _const,
              "a config 'default' that disagrees with the code changes "
              "behaviour out of the box and looks like a tuning choice")

print("\n" + "="*58)
if fails:
    print(f"  *** {len(fails)} FAILED: {fails}"); sys.exit(1)
print("  DIRT DETECTOR CHECKS PASSED")
