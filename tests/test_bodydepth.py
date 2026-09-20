"""tests/test_bodydepth.py -- the depth body path, on one REAL recorded frame.

WHY A FIXTURE RATHER THAN SYNTHETIC ARRAYS. py/bodydepth.py's own self-test
already checks the arithmetic on made-up numbers, and it passed while the
module was broken: intrinsics arrive as a DICT from a recording, the code
read them as attributes, and the first real frame died with "'I' object is
not subscriptable". Synthetic data cannot catch a shape mismatch, because
whoever writes the synthetic data writes it in the shape they assumed.

So this runs the whole path -- floor fit, subject mask, world transform,
MediaPipe, depth sampling -- against one frame captured on the arm computer
with the real D455. 1.6MB is cheap for the only test that has ever caught a
real bug in this file.

WHAT IT ASSERTS, AND WHY EACH ONE
----------------------------------
Not "it ran without raising". Each check is a claim about the body that
would fail if the geometry were wrong in a way that still produced numbers:

  * six joints measured, not five. A dropped joint means the depth sample
    or the mask lost an arm.
  * shoulders level with each other. They are on the same person at the
    same height; a sideways transform tilts them apart.
  * left and right on opposite sides of the centre line. Sign errors in the
    deprojection put both arms on one side and everything still "works".
  * wrists further forward than shoulders. A seated person's hands are in
    front of their back; if not, the facing axis is reversed.

Run: ./venv/bin/python tests/test_bodydepth.py
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "py"))

FIX = os.path.join(ROOT, "tests", "fixtures", "depth_frame")

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(name)


def main():
    print("bodydepth against one real D455 frame")

    if not os.path.isdir(FIX):
        print(f"  SKIP: no fixture at {FIX}")
        return 0

    import cv2
    import bodydepth as BD
    from scrub3d import frames as F

    intr = json.load(open(os.path.join(FIX, "intr.json")))
    depth = cv2.imread(os.path.join(FIX, "00000_d.png"),
                       cv2.IMREAD_UNCHANGED).astype(np.float32)
    color = cv2.imread(os.path.join(FIX, "00000_c.png"))

    check("depth frame loaded", depth is not None and depth.size > 0,
          f"{depth.shape} valid={100 * (depth > 0).mean():.0f}%")

    floor = F.fit_floor(depth, intr)
    check("floor plane found", floor is not None)
    if floor is None:
        return 1

    subj = F.deproject(depth, intr, F.subject_mask(depth))
    check("subject found", len(subj) > 1000, f"{len(subj)} points")
    if not len(subj):
        return 1

    T = F.world_from_camera(floor, subj.mean(0))

    # MediaPipe on the colour frame. Same model the projector uses.
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision as mpv
    model = os.path.join(ROOT, "models", "pose_landmarker_full.task")
    if not os.path.exists(model):
        print("  SKIP: pose model missing -- run ./vendor.sh")
        return 0
    lm = mpv.PoseLandmarker.create_from_options(mpv.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model),
        running_mode=mpv.RunningMode.IMAGE))
    res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                             data=cv2.cvtColor(color, cv2.COLOR_BGR2RGB)))
    check("person detected", bool(res.pose_landmarks))
    if not res.pose_landmarks:
        return 1

    h, w = depth.shape
    px = {n: (res.pose_landmarks[0][k].x * w, res.pose_landmarks[0][k].y * h)
          for n, k in BD.JOINT_IDX.items()}
    out = BD.joints_world_mm(px, depth, intr, T)

    check("all six joints measured", len(out) == 6,
          f"{len(out)}/6: {sorted(out)}")
    if len(out) < 6:
        return 1

    # The world frame is x forward (the way the person faces), y left, z up --
    # world_from_camera builds it that way from the floor normal.
    ls, rs = out["l_shoulder"], out["r_shoulder"]
    lw, rw = out["l_wrist"], out["r_wrist"]

    check("shoulders level with each other", abs(ls[2] - rs[2]) < 120.0,
          f"dz={abs(ls[2] - rs[2]):.0f}mm")
    check("left and right on opposite sides", (ls[1] > 0) != (rs[1] > 0),
          f"l_y={ls[1]:.0f} r_y={rs[1]:.0f}")
    check("wrists ahead of shoulders",
          lw[0] > ls[0] and rw[0] > rs[0],
          f"wrist_x {lw[0]:.0f}/{rw[0]:.0f} vs shoulder_x {ls[0]:.0f}/{rs[0]:.0f}")

    # A joint with no depth must be ABSENT, never invented. Blank the depth
    # under one wrist and confirm it drops out rather than coming back as a
    # point at the camera.
    d2 = depth.copy()
    wx, wy = int(px["l_wrist"][0]), int(px["l_wrist"][1])
    d2[max(0, wy - 12):wy + 12, max(0, wx - 12):wx + 12] = 0.0
    out2 = BD.joints_world_mm(px, d2, intr, T)
    check("joint with no depth is absent, not invented",
          "l_wrist" not in out2 and "r_wrist" in out2,
          f"{sorted(out2)}")

    return 1 if fails else 0


if __name__ == "__main__":
    rc = main()
    print(f"\n*** {len(fails)} FAILED: {fails}" if fails else "\nALL CHECKS PASSED")
    sys.exit(rc)
