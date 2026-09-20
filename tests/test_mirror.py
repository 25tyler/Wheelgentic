"""tests/test_mirror.py — SETTLE THE MIRROR CONVENTION.

Getting this backwards makes the robot scrub the WRONG ARM. It was the last
item blocked on "needs a human waving at a webcam"; py/fakecam.py renders a
subject waving ONE arm, so it is now settled deterministically and re-checked
on every run.

THE MEASURED FACTS (I had this BACKWARDS before running it):

  1. **Mirroring SWAPS the anatomical labels.** MediaPipe infers left/right
     from the IMAGE, not from any knowledge of the person. Same frame, arm
     raised:
         as rendered    L15=(442,175)  R16=(100,324)  raised = 15
         cv2.flip(f,1)  L15=(382,316)  R16=(183,313)  raised = 16
     The raised arm changes landmark NUMBER when you flip.

  2. Therefore `mirror` decides WHICH ARM THE ROBOT SCRUBS. With
     `mirror: true` and the code reading L_ELBOW/L_WRIST, the arm goes to the
     limb on the image-left -- which is the subject's RIGHT arm.

  3. vision.py's `mirror` and calibrate.py's `mirror` must MATCH, because a
     homography fitted on flipped pixels is wrong for unflipped ones.

  4. The operator-facing rule stays simple: run `CAM=fake python py/vision.py`
     (or the real camera), wave ONE arm, and check the GREEN/ORANGE dots land
     on the arm you are waving. If they land on the other arm, flip `mirror`
     in config.json. This test pins the underlying behaviour so that rule is
     never guesswork again.
"""
import math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "py"))
sys.path.insert(0, HERE)
import watchdog; watchdog.arm(150)

import cv2, numpy as np
import time
import fakecam
import mediapipe as mp
from mediapipe.tasks import python as mpp
from mediapipe.tasks.python import vision as mpv
from mediapipe.tasks.python import BaseOptions

FAILS = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(label)

lm = mpv.PoseLandmarker.create_from_options(
    mpv.PoseLandmarkerOptions(
        base_options=mpp.BaseOptions(
            model_asset_path="models/pose_landmarker_full.task",
            delegate=BaseOptions.Delegate.GPU),
        running_mode=mpv.RunningMode.IMAGE, num_poses=1))

L_WRIST, R_WRIST = 15, 16


def sweep(wave_left, mirror, frames=7):
    """-> (L15 y-travel, R16 y-travel, mean L15 x, image width)."""
    yl, yr, xl = [], [], []
    w = 0
    for k in range(frames):
        f = fakecam.render(phase=k * math.pi / (frames - 1),
                           wave_left=wave_left, tag=False)
        if mirror:
            f = cv2.flip(f, 1)
        h, w = f.shape[:2]
        r = np.ascontiguousarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGBA))
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGBA, data=r))
        if not res.pose_landmarks:
            continue
        P = res.pose_landmarks[0]
        yl.append(P[L_WRIST].y * h); yr.append(P[R_WRIST].y * h)
        xl.append(P[L_WRIST].x * w)
    trav = lambda v: (max(v) - min(v)) if v else 0.0
    return trav(yl), trav(yr), (sum(xl) / len(xl) if xl else 0.0), w


print("=== 1. THE RENDER moves only the waving side ===")
a = fakecam.render(phase=0.0, wave_left=True, tag=False)
b = fakecam.render(phase=math.pi / 2, wave_left=True, tag=False)
d = cv2.absdiff(a, b).sum(axis=2)
w2 = d.shape[1] // 2
lhalf, rhalf = d[:, :w2].sum(), d[:, w2:].sum()
check("wave_left=True moves ONLY the image-RIGHT half",
      rhalf > lhalf * 20,
      f"L {lhalf/1e6:.2f}M vs R {rhalf/1e6:.2f}M — they face us, so their left is our right")

print("\n=== 2. UNMIRRORED: the person's own labelling holds ===")
tl, tr, mx, w = sweep(wave_left=True, mirror=False)
check("subject waves their LEFT -> landmark 15 moves",
      tl > tr * 3, f"L15 travel {tl:.0f}px vs R16 {tr:.0f}px")
check("and landmark 15 sits on the image-RIGHT", mx > w / 2,
      f"mean x={mx:.0f} of {w} — they face us, so their left is our right")

print("\n=== 3. MIRRORED: THE LABELS SWAP ===")
# This is the fact that decides which arm gets scrubbed, and I had it
# backwards until I ran it.
tl_m, tr_m, _, _ = sweep(wave_left=True, mirror=True)
check("the SAME subject, flipped -> landmark 16 moves instead",
      tr_m > tl_m * 3, f"L15 travel {tl_m:.0f}px vs R16 {tr_m:.0f}px")
check("so `mirror` DOES swap the anatomical labels",
      (tl > tr) and (tr_m > tl_m),
      "unflipped: 15 moves; flipped: 16 moves — same physical arm")

print("\n=== 4. Waving the OTHER arm swaps which landmark moves ===")
tl2, tr2, _, _ = sweep(wave_left=False, mirror=False)
check("wave_left=False -> landmark 16 (person's RIGHT) is the one moving",
      tr2 > tl2 * 3, f"L15 travel {tl2:.0f}px vs R16 {tr2:.0f}px")

print("\n=== 4b. THE SHIPPED PoseFeed.read() ACTUALLY FLIPS ===")
# Sections 1-4 build their own landmarker, so they prove MediaPipe's
# behaviour but NOT that vision.py applies it. Deleting `if self.mirror:
# frame = cv2.flip(frame, 1)` from PoseFeed.read() would pass every check
# above. Drive the shipped read() with mirror on and off and compare where
# the landmark lands.
import types as _types
from vision import PoseFeed as _PF, _load_mediapipe
_load_mediapipe()

def _shipped(mirror):
    """Run the REAL PoseFeed.read() over a waving frame."""
    feed = _PF.__new__(_PF)                # no camera, no model construction
    feed.mirror = mirror
    feed.min_visibility = 0.0              # do not gate this probe
    feed.low_vis_frames = 0
    feed.t0 = feed.prev_t = time.time() - 1
    from vision import Vec2Filter
    feed.f_elbow, feed.f_wrist = Vec2Filter(), Vec2Filter()
    img = fakecam.render(phase=math.pi / 2, wave_left=True, tag=False)
    feed.cap = _types.SimpleNamespace(read=lambda: (True, img.copy()))
    feed.landmarker = lm_video
    _, e, w, _ = feed.read(13, 15)         # L elbow / L wrist
    return w

import time as _t
lm_video = mpv.PoseLandmarker.create_from_options(
    mpv.PoseLandmarkerOptions(
        base_options=mpp.BaseOptions(
            model_asset_path="models/pose_landmarker_full.task",
            delegate=BaseOptions.Delegate.GPU),
        running_mode=mpv.RunningMode.VIDEO, num_poses=1))
w_off = _shipped(False)
lm_video = mpv.PoseLandmarker.create_from_options(
    mpv.PoseLandmarkerOptions(
        base_options=mpp.BaseOptions(
            model_asset_path="models/pose_landmarker_full.task",
            delegate=BaseOptions.Delegate.GPU),
        running_mode=mpv.RunningMode.VIDEO, num_poses=1))
w_on = _shipped(True)
check("PoseFeed.read() returns a wrist with mirror=False", w_off is not None)
check("PoseFeed.read() returns a wrist with mirror=True", w_on is not None)
if w_off and w_on:
    print(f"    mirror=False -> wrist x={w_off[0]:.0f}")
    print(f"    mirror=True  -> wrist x={w_on[0]:.0f}")
    check("the SHIPPED flip actually moves the landmark",
          abs(w_off[0] - w_on[0]) > 40,
          f"moved {abs(w_off[0]-w_on[0]):.0f}px — a no-op flip would move 0")

print("\n=== 5. vision.py and calibrate.py must AGREE on mirror ===")
import re
_v = open("py/vision.py").read()
_c = open("py/calibrate.py").read()
check("vision.py takes a mirror flag", "self.mirror" in _v)
check("calibrate.py takes a mirror flag", "mirror" in _c)
check("both flip with cv2.flip(f, 1)",
      "cv2.flip(frame, 1)" in _v and "cv2.flip(f, 1)" in _c,
      "a homography fitted on flipped pixels is wrong for unflipped ones")
_cfg = open("config.json").read()
check("config.json carries the single source of truth", '"mirror"' in _cfg)

# THE CHECK ABOVE IS A SUBSTRING TEST AND CANNOT SEE THE VALUE. Flipping
# `mirror` to false in config.json passed every assertion in this file and in
# test_docs_match_code.py -- measured, both exit 0. That matters more here than
# almost anywhere: sections 2-4 above establish that with mirror:true the code
# reading L_* lands on the subject's RIGHT arm, so the shipped value IS the
# choice of which arm the robot scrubs. A wrong value scrubs the wrong limb on
# stage with a green suite. Read the value, and tie it to the mapping this file
# just measured.
import json as _json
_cfgd = _json.loads(_cfg)
_mir = _cfgd.get("mirror")
check("config.json's mirror is a BOOL, not a string", isinstance(_mir, bool),
      f"got bool {_mir!r}" if isinstance(_mir, bool)
      else f'got {type(_mir).__name__} {_mir!r} -- a non-empty string like '
           '"false" is TRUTHY, so the frame would mirror anyway')
check("and it is true, the value sections 2-4 measured the mapping against",
      _mir is True,
      "mirror:true + L_* = the subject's RIGHT arm (measured above). If this "
      "is deliberately false, the L_*/R_* pick at scrubbot.py:422 inverts and "
      "this line is the place to re-derive it"
      if _mir is not True else
      "so the default L_* pick targets the subject's RIGHT arm")

print("\n" + "=" * 62)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
print("  MIRROR CONVENTION SETTLED")
print("  MIRRORING SWAPS THE LABELS. MediaPipe reads left/right from the")
print("  IMAGE, so `mirror` decides WHICH ARM THE ROBOT SCRUBS.")
print("  With mirror:true and the code reading L_ELBOW/L_WRIST, the arm goes")
print("  to the limb on the image-left = the subject's RIGHT arm.")
print("  vision.py and calibrate.py MUST use the same value.")
print("  Operator rule: wave one arm, check the dots land on THAT arm.")
