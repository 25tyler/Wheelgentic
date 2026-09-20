#!/usr/bin/env python3
"""tools/synth_record.py — generate a pose recording with no camera.

    ./venv/bin/python tools/synth_record.py recordings/good_run.jsonl

The demo's safety net is `REPLAY=recordings/good_run.jsonl ./run.sh`. Until a
real one is captured (Tyler, hour 20, with the camera), this produces a
plausible stand-in by running a synthetic moving figure through the REAL
MediaPipe pipeline -- so the recorded landmarks come from the same code path
that will produce the real ones, not from hand-written numbers.

The subject is synthetic but the LANDMARKS are real: they come out of the same
MediaPipe pipeline that will produce the live ones, filtered by the same One
Euro, gated by the same visibility threshold. A hand-written JSONL would not
be.

Replace with a live capture when a camera is available:
    python py/scrubbot.py --record recordings/good_run.jsonl

WITH A REAL CAMERA. NOT WITH CAM=fake. The fake subject barely moves, and a
recording of it is a WORSE safety net than this file. Measured, CAM=fake
--record against what this script produces: elbow-x stdev 1.74 vs 34.94,
wrist-x 0.48 vs 72.26 -- 20x and 150x less motion. It would still load and
still replay; it would just show a person standing almost still, on the one
run where everything else has already failed. tests/test_record_replay.py now
fails if good_run.jsonl drops below that motion floor.
"""
import json, math, os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "py"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.chdir(ROOT)

import cv2, numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mpp
from mediapipe.tasks.python import vision as mpv
from mediapipe.tasks.python import BaseOptions
from fakecam import render as figure         # the detectable, WAVING render
from vision import L_ELBOW, L_WRIST, Vec2Filter

OUT = sys.argv[1] if len(sys.argv) > 1 else "recordings/good_run.jsonl"
FRAMES = int(sys.argv[2]) if len(sys.argv) > 2 else 240      # 8s at 30fps

lm = mpv.PoseLandmarker.create_from_options(
    mpv.PoseLandmarkerOptions(
        base_options=mpp.BaseOptions(
            model_asset_path="models/pose_landmarker_full.task",
            delegate=BaseOptions.Delegate.GPU),
        running_mode=mpv.RunningMode.VIDEO, num_poses=1))

fe, fw = Vec2Filter(), Vec2Filter()
rows, t0, kept = [], time.time(), 0
for i in range(FRAMES):
    # A real subject moves their whole arm, not just an angle. fakecam's
    # phase drives a wave, which is what a recorded run should contain.
    img = figure(phase=i * (2 * math.pi / 42.0), tag=False)
    h, w = img.shape[:2]
    r = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGBA))
    res = lm.detect_for_video(
        mp.Image(image_format=mp.ImageFormat.SRGBA, data=r), i * 33)
    if not res.pose_landmarks:
        continue
    L = res.pose_landmarks[0]
    e, wr = L[L_ELBOW], L[L_WRIST]
    if e.visibility < 0.5 or wr.visibility < 0.5:
        continue
    en = fe((e.x, e.y), 1/30.0)
    wn = fw((wr.x, wr.y), 1/30.0)
    rows.append({"t": t0 + i / 30.0,
                 "elbow": [en[0] * w, en[1] * h],
                 "wrist": [wn[0] * w, wn[1] * h]})
    kept += 1

os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
open(OUT, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
print(f"wrote {OUT}: {kept}/{FRAMES} frames "
      f"({100*kept/FRAMES:.0f}% detected)")
if rows:
    xs = [r["elbow"][0] for r in rows]
    print(f"  elbow x range: {min(xs):.0f}..{max(xs):.0f} px "
          f"({max(xs)-min(xs):.0f}px of travel)")
    print(f"  duration: {rows[-1]['t']-rows[0]['t']:.1f}s")
print("\nThis is SYNTHETIC. Replace with a real capture when a camera works:")
print("  python py/scrubbot.py --record recordings/good_run.jsonl")
print("  ^ WITH A REAL CAMERA. CAM=fake barely moves: 20x less elbow motion,")
print("    150x less wrist motion. The guard in test_record_replay.py will")
print("    reject a capture that weak.")
