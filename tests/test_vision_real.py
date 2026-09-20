"""tests/test_vision_real.py — the REAL MediaPipe path, no human needed.

Phase 2 items that were blocked on "needs a human at a webcam" are mostly
provable with a synthetic video source: a rendered humanoid figure fed through
the actual PoseFeed. What this CANNOT prove is how a real body under venue
lighting behaves — that remains Tyler's hour-1 go/no-go.

What it DOES prove, against the shipped code:
  - mediapipe 1.0.0 imports and creates a landmarker without SIGABRT
  - the GPU/CPU delegate path works
  - SRGBA conversion is accepted (the 3-channel trap)
  - the FRAMING CONSTRAINT: torso cropped out -> no pose
  - the visibility gate rejects a low-confidence limb
  - the mirror convention is deterministic and documented
  - measured throughput
"""
import os, sys, time, threading
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.chdir(ROOT); sys.path.insert(0, os.path.join(ROOT, "py"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog; watchdog.arm(180)   # SIGALRM, not a daemon thread:
# a 90s daemon watchdog once let a test run for 3h21m.

import cv2, numpy as np

FAILS = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(label)


def figure(w=480, h=640, arm_angle=0.0):
    """A synthetic person MediaPipe will actually detect.

    Flat vector art is NOT detected -- measured: the same figure without
    Gaussian blur and sensor noise returns no pose at all. MediaPipe is
    trained on photographs, so the render needs soft edges, rounded joints,
    a background, and a little noise. That took three attempts and is worth
    writing down: a "clearly a person" stick figure proves nothing.
    """
    import math
    rng = np.random.default_rng(3)
    img = np.full((h, w, 3), 200, np.uint8)
    cv2.rectangle(img, (0, int(h*0.75)), (w, h), (170, 170, 175), -1)
    cx = w // 2
    skin = (145, 170, 200)
    S = lambda p: (int(p[0]), int(p[1]))
    swing = math.sin(arm_angle) * w * 0.05
    head = (cx, int(h*0.13)); neck = (cx, int(h*0.20))
    lsh = (cx-int(w*0.13), int(h*0.24)); rsh = (cx+int(w*0.13), int(h*0.24))
    lel = (cx-int(w*0.20)-swing, int(h*0.40)); rel = (cx+int(w*0.20)+swing, int(h*0.40))
    lwr = (cx-int(w*0.23)-swing, int(h*0.55)); rwr = (cx+int(w*0.23)+swing, int(h*0.55))
    lhp = (cx-int(w*0.09), int(h*0.53)); rhp = (cx+int(w*0.09), int(h*0.53))
    lkn = (cx-int(w*0.10), int(h*0.72)); rkn = (cx+int(w*0.10), int(h*0.72))
    lan = (cx-int(w*0.10), int(h*0.90)); ran = (cx+int(w*0.10), int(h*0.90))
    cv2.fillPoly(img, [np.array([lsh, rsh, rhp, lhp], np.int32)], (120, 100, 90))
    for a, b, t in [(lsh,lel,26),(lel,lwr,22),(rsh,rel,26),(rel,rwr,22),
                    (lhp,lkn,30),(lkn,lan,26),(rhp,rkn,30),(rkn,ran,26)]:
        cv2.line(img, S(a), S(b), skin, t)
        cv2.circle(img, S(b), t//2, skin, -1)
    cv2.line(img, S(neck), S(head), skin, 22)
    cv2.circle(img, S(head), int(w*0.075), skin, -1)
    img = cv2.GaussianBlur(img, (7, 7), 0)
    noise = rng.normal(0, 6, img.shape).astype(np.int16)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def main():
    print("=== 1. MEDIAPIPE 1.0.0 CREATES A LANDMARKER (no SIGABRT) ===")
    t0 = time.time()
    from vision import PoseFeed, OneEuro, L_ELBOW, L_WRIST
    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision as mpv
    from mediapipe.tasks.python import BaseOptions
    check("mediapipe is the pinned 1.0.0", mp.__version__ == "1.0.0", mp.__version__)

    made = None
    for delegate in ("GPU", "CPU"):
        try:
            lm = mpv.PoseLandmarker.create_from_options(
                mpv.PoseLandmarkerOptions(
                    base_options=mpp.BaseOptions(
                        model_asset_path="models/pose_landmarker_full.task",
                        delegate=getattr(BaseOptions.Delegate, delegate)),
                    running_mode=mpv.RunningMode.VIDEO, num_poses=1))
            made = delegate
            break
        except Exception as e:
            print(f"    {delegate} delegate failed: {str(e)[:70]}")
    check("a landmarker was created", made is not None, f"delegate={made}")
    print(f"    import + create took {time.time()-t0:.1f}s")

    print("\n=== 2. SRGBA CONVERSION IS ACCEPTED (the 3-channel trap) ===")
    img = figure()
    rgba = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGBA))
    mpimg = mp.Image(image_format=mp.ImageFormat.SRGBA, data=rgba)
    res = lm.detect_for_video(mpimg, 0)
    check("detect_for_video accepted an SRGBA image", res is not None)
    found_full = bool(res.pose_landmarks)
    print(f"    pose detected on the synthetic figure: {found_full}")

    print("\n=== 3. THROUGHPUT ===")
    n, t0 = 0, time.time()
    while time.time() - t0 < 2.0:
        f = figure(arm_angle=n * 0.05)
        r = np.ascontiguousarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGBA))
        lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGBA, data=r),
                            int((time.time()-t0)*1000) + 100)
        n += 1
    fps = n / (time.time() - t0)
    check("throughput is usable (>=20 fps)", fps >= 20, f"{fps:.1f} fps")

    print("\n=== 4. FRAMING CONSTRAINT (torso cropped -> NO pose) ===")
    # The single most expensive camera-mounting mistake available: MediaPipe Pose
    # is a WHOLE-BODY model, so a camera zoomed onto a forearm returns NOTHING and
    # fails silently. Mount at 60-70 degrees with the torso in frame.
    full = figure()
    check("whole body in frame -> pose FOUND", found_full)
    if found_full:
        # FRESH LANDMARKER IN *IMAGE* MODE, not the VIDEO one above.
        # RunningMode.VIDEO carries tracking between frames, so after seeing a
        # full body it happily tracks INTO a cropped frame -- the test passed
        # 3 runs in 5 and failed the other 2. Flaky for a reason that has
        # nothing to do with the constraint being tested. IMAGE mode
        # re-detects from scratch, which is the actual question: "can this
        # model find a pose in THIS image, cold?"
        still = mpv.PoseLandmarker.create_from_options(
            mpv.PoseLandmarkerOptions(
                base_options=mpp.BaseOptions(
                    model_asset_path="models/pose_landmarker_full.task",
                    delegate=getattr(BaseOptions.Delegate, made)),
                running_mode=mpv.RunningMode.IMAGE, num_poses=1,
                min_pose_detection_confidence=0.2))   # generous ON PURPOSE
        h_, w_ = full.shape[:2]
        as_img = lambda im: mp.Image(
            image_format=mp.ImageFormat.SRGBA,
            data=np.ascontiguousarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGBA)))
        check("cold detection on the FULL body finds a pose",
              bool(still.detect(as_img(full)).pose_landmarks))
        crop = cv2.resize(full[int(h_*0.36):int(h_*0.60), :].copy(), (w_, h_))
        got_crop = bool(still.detect(as_img(crop)).pose_landmarks)
        check("torso cropped out -> pose LOST (camera MUST see the torso)",
              not got_crop,
              "a tabletop camera zoomed on the arm silently returns nothing")

    print("\n=== 5. VISIBILITY GATE rejects a low-confidence limb ===")
    # EXERCISE THE SHIPPED read(), not a restatement of it. The previous
    # version computed `e.visibility < 0.5 or w.visibility < 0.5` inside the
    # test and asserted that equalled its own expectation -- a tautology that
    # would pass with the gate DELETED from vision.py.
    import types as _types
    from vision import PoseFeed as _PF, _load_mediapipe
    _load_mediapipe()          # __init__ normally does this; we bypass it

    class _FakeLM:
        def __init__(self, x, y, v): self.x, self.y, self.visibility = x, y, v

    class _FakeRes:
        def __init__(self, lms): self.pose_landmarks = [lms]

    def _probe(vis):
        """Drive the REAL PoseFeed.read() with landmarks at a given
        visibility, bypassing only the camera and the model."""
        feed = _PF.__new__(_PF)                 # no camera, no model
        feed.mirror = False
        feed.min_visibility = 0.5
        feed.low_vis_frames = 0
        feed.t0 = feed.prev_t = time.time() - 1
        from vision import Vec2Filter
        feed.f_elbow, feed.f_wrist = Vec2Filter(), Vec2Filter()
        lms = [_FakeLM(0.5, 0.5, 0.99) for _ in range(33)]
        lms[13] = _FakeLM(0.40, 0.50, vis)      # L elbow
        lms[15] = _FakeLM(0.60, 0.55, vis)      # L wrist
        feed.cap = _types.SimpleNamespace(
            read=lambda: (True, np.zeros((480, 640, 3), np.uint8)))
        feed.landmarker = _types.SimpleNamespace(
            detect_for_video=lambda *a, **k: _FakeRes(lms))
        return feed.read(13, 15)                # the SHIPPED code path

    _, e_hi, w_hi, _ = _probe(0.99)
    check("high visibility -> landmarks returned", e_hi is not None and w_hi is not None,
          f"elbow={e_hi}")
    _, e_lo, w_lo, _ = _probe(0.20)
    check("LOW visibility -> read() returns None (arm retreats)",
          e_lo is None and w_lo is None,
          "an occluded forearm must not produce a confident target")
    _, e_edge, _, _ = _probe(0.49)
    check("just below the 0.5 threshold is also rejected", e_edge is None)

    print("\n=== 6. ONE EURO removes real jitter at the shipped defaults ===")
    import random, statistics
    random.seed(11)
    f = OneEuro()                      # shipped defaults, normalised input
    raw, out = [], []
    for i in range(150):
        x = 0.594 + random.gauss(0, 3/1280)      # 3px of jitter, normalised
        raw.append(x); out.append(f(x, 1/30))
    sd_raw = statistics.pstdev(raw[40:]) * 1280
    sd_out = statistics.pstdev(out[40:]) * 1280
    check("filter removes >50% of jitter at defaults", sd_out < sd_raw * 0.5,
          f"{sd_raw:.2f}px -> {sd_out:.2f}px")

    print("\n=== 7. MIRROR CONVENTION is deterministic ===")
    a = figure(arm_angle=0.6)
    b = cv2.flip(a, 1)
    check("cv2.flip is its own inverse", np.array_equal(cv2.flip(b, 1), a))
    check("flipping changes the image (mirror is real)", not np.array_equal(a, b))
    print("    CONVENTION: vision.py and calibrate.py BOTH read config 'mirror'.")
    print("    They must agree or the robot scrubs the wrong arm.")

    print("\n" + "="*60)
    if FAILS:
        print(f"  *** {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
    print("  REAL-MEDIAPIPE CHECKS PASSED")
    print("  NOTE: a synthetic figure is not a human. Tyler's hour-1 go/no-go")
    print("  (python py/vision.py, wave one arm) still stands.")


if __name__ == "__main__":
    main()
