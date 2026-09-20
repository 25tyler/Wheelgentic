"""py/bodydepth.py -- the person's joints in millimetres, measured not guessed.

WHAT THIS REPLACES, AND WHY IT MATTERS
--------------------------------------
`vision.PoseFeed._publish_world` places the person like this:

    self._MP_TO_S3D @ [p.x, p.y, p.z] * 1000.0 + [0, 0, SEAT_Z_MM]

Those are MediaPipe's world landmarks: hip-centred and SCALE-NORMALISED TO A
GENERIC HUMAN. Left/right and up/down are real; the distance is a guess from
average human proportions, and the height is a constant for a seated adult.
`vision.read()`'s own docstring says it outright -- "NEVER command the arm
from these" -- because a fixed transform fitted against them drifts as the
subject shifts their weight.

This module answers the same question from a depth sensor instead: a distance
per pixel, deprojected through the camera's real intrinsics, expressed in the
world frame that the floor fit defines. Nothing about it is normalised to an
average body.

WHY IT IS A SEPARATE FILE AND NOT AN EDIT TO vision.py
-------------------------------------------------------
Two reasons, both about not breaking what works.

`vision.py` is the projector's hot path and it runs on a plain colour camera
with no depth at all. It must keep doing that: the demo has to survive with a
laptop webcam and no RealSense anywhere. So the depth path is additive and
optional, and `vision.py` never imports this.

And the depth pipeline already EXISTS -- `scrub3d/rsfeed.py` opens the camera
or replays a recording, `scrub3d/frames.py` fits the floor, finds the subject
and builds the world transform. Writing a second one would be two pipelines
that drift apart. This file is a thin adapter onto those, not a new
implementation.

THE NEAREST SURFACE, NOT THE AVERAGE
-------------------------------------
A depth pixel at an elbow held over a stomach may sample either. Averaging a
window puts the elbow inside the torso. So the sample takes the NEAREST valid
surface in a small window (the closest fifth), which is the elbow when the
elbow is in front and the stomach when it is not. `docs/LIVE-3D-PLAN.md` §5
3.3 states the same rule.

ABSENT MEANS ABSENT
-------------------
Zero depth is NO DATA, never zero millimetres -- rsfeed documents that and
frames.deproject drops it rather than putting a phantom point at the camera.
A joint with no valid depth nearby is simply not in the result. It is never a
last-known position, never an interpolation, and never MediaPipe's guess
quietly substituted back in. The caller can tell the difference between "the
elbow is here" and "we do not know where the elbow is", which is the whole
reason this exists.

NO HARDWARE NEEDED TO RUN THIS
-------------------------------
`rsfeed.Feed(source)` takes a recording directory or a .bag file and yields
the same frames a camera would, which is the property its docstring is built
around: "every line downstream of this runs the same whether a D455 is
plugged in or a 1.6GB bag is replaying". So this file is exercised with no
camera, no arm and no volunteer:

    python py/bodydepth.py scrub3d/data/<some_recording>

UNVERIFIED AGAINST REAL DEPTH, AND SAYING SO
---------------------------------------------
Written against rsfeed/frames' documented contracts and checked on synthetic
frames. It has NOT been run against a D455 or against a recording, because no
recording is in the repo (the .gitignore excludes the capture sessions) and
the camera is not attached to this machine. The arithmetic below is the part
to distrust first when a real frame finally arrives.
"""
import os
import sys

import numpy as np

# scrub3d is a sibling package of py/, and this is the one import that reaches
# into it. Kept at module scope so an ImportError is loud at startup rather
# than on the first frame, and wrapped so a caller can ask HAVE_DEPTH instead
# of catching.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from scrub3d import frames as _frames
    from scrub3d import rsfeed as _rsfeed
    HAVE_DEPTH = True
    _IMPORT_ERROR = None
except Exception as exc:                                     # noqa: BLE001
    # pyrealsense2 is not installed on every machine that runs the projector,
    # and that is fine -- it is why this is optional. The caller checks
    # HAVE_DEPTH and falls back to the colour-only path.
    _frames = _rsfeed = None
    HAVE_DEPTH = False
    _IMPORT_ERROR = exc

# The six joints the plan asks for, and MediaPipe's landmark index for each.
# Same six as vision.PoseFeed.LIMB_JOINTS, deliberately: this produces the
# SAME dict shape from a better source, so limb_event() does not care which
# one filled it.
JOINT_IDX = {
    "l_shoulder": 11, "r_shoulder": 12,
    "l_elbow":    13, "r_elbow":    14,
    "l_wrist":    15, "r_wrist":    16,
}

# Half-width of the depth window sampled around a joint's pixel, in pixels.
# Not a smoothing radius: it is there so a joint that lands on a single
# dropped pixel still has neighbours to read. 3 -> a 7x7 window, about 25mm
# across at a metre on the D455's 848x480 depth stream.
WINDOW_PX = 3

# What fraction of the valid samples in that window count as "the near
# surface". The closest fifth, floored at one sample.
NEAR_FRACTION = 0.2


def sample_depth_mm(depth_mm, px, py, window=WINDOW_PX):
    """The nearest surface at a pixel, in millimetres. -> float or None.

    None means NO VALID DEPTH THERE. It is not 0.0 and not the last reading:
    a joint we cannot measure has to be distinguishable from one at the
    camera's own origin, or the body folds into the lens.
    """
    h, w = depth_mm.shape
    x0, x1 = max(0, px - window), min(w, px + window + 1)
    y0, y1 = max(0, py - window), min(h, py + window + 1)
    if x0 >= x1 or y0 >= y1:
        return None
    patch = depth_mm[y0:y1, x0:x1]
    # ZERO IS NO DATA, NOT ZERO DISTANCE. rsfeed documents this and
    # frames.deproject drops it for the same reason.
    valid = patch[patch > 0]
    if valid.size == 0:
        return None
    # THE NEAR SURFACE. See the module docstring: an elbow in front of a
    # stomach must read as the elbow. Averaging would put it inside the body.
    k = max(1, int(valid.size * NEAR_FRACTION))
    return float(np.mean(np.sort(valid)[:k]))


def joints_world_mm(landmarks_px, depth_mm, intr, T_world_cam):
    """Six joints in world millimetres, from pixels plus depth. -> dict.

    `landmarks_px` is {name: (x, y)} in DEPTH-FRAME pixel coordinates -- the
    frames rsfeed yields are already aligned, so a colour pixel and a depth
    pixel with the same index are the same point in the world. Without that
    alignment the D455's colour and depth sensors are physically offset and
    every joint lands on whatever happens to be behind it.

    A joint with no valid depth is OMITTED. The caller must treat a missing
    key as unknown, exactly as vision.PoseFeed already does for a joint below
    the visibility floor.
    """
    out = {}
    for name, (px, py) in landmarks_px.items():
        ipx, ipy = int(round(px)), int(round(py))
        z = sample_depth_mm(depth_mm, ipx, ipy)
        if z is None:
            continue
        # Deproject through the camera's OWN intrinsics rather than an
        # assumed field of view. fx/fy/ppx/ppy come off the sensor's
        # calibration, and rsfeed rotates them with the pixels so they keep
        # describing the image it hands over.
        x = (ipx - intr.ppx) / intr.fx * z
        y = (ipy - intr.ppy) / intr.fy * z
        p_cam = np.array([x, y, z, 1.0])
        out[name] = (np.asarray(T_world_cam, float) @ p_cam)[:3]
    return out


class DepthBody:
    """The person's arm joints from a depth feed. Camera or recording.

    USAGE, and it is the same either way:

        b = DepthBody("scrub3d/data/live_rec_2026...")   # a recording
        b = DepthBody()                                  # the camera
        for joints in b.track(landmarker):
            ...

    NEVER RAISES PER FRAME. A dropped frame, a lost floor or a person who
    left are all normal, and each yields an empty dict rather than stopping
    the loop -- the same contract vision._publish_world already keeps.
    """

    def __init__(self, source=None, rotate="180"):
        if not HAVE_DEPTH:
            raise RuntimeError(
                f"depth pipeline unavailable: {_IMPORT_ERROR}. "
                f"pyrealsense2 is needed for the camera; a recording needs "
                f"scrub3d on the path.")
        self.feed = _rsfeed.Feed(source, rotate=rotate)
        self.source = source
        # The world transform is fitted ONCE from the floor, not per frame.
        # The floor does not move; re-fitting it every frame would make the
        # world jitter with the fit's own noise and the person would appear
        # to slide around a stationary room.
        self.T_world_cam = None
        self.floor = None

    def _fit_world(self, depth_mm, intr):
        """Fit the floor and the world frame. -> bool, did it work.

        Returns False rather than raising when the floor cannot be found --
        the usual cause is bags and cases on the floor around the chair, which
        DIMOS.md already records as the reason the arms wait.
        """
        try:
            floor = _frames.fit_floor(depth_mm, intr)
            if floor is None:
                return False
            # THE SAME THREE CALLS frames.solve() MAKES, in its order. The
            # centroid decides where the world origin lands -- dropped onto
            # the floor beneath the subject, which is where
            # anatomy.anatomical_body() already assumes the person is.
            # Passing None instead puts the origin under the CAMERA, a valid
            # frame but not the one the rest of the package expects, and
            # every joint would then be offset by however far the camera
            # stands from the chair.
            subj = _frames.deproject(depth_mm, intr,
                                     _frames.subject_mask(depth_mm))
            centroid = subj.mean(0) if len(subj) else None
            if centroid is None:
                return False            # nobody in frame yet; try next frame
            self.T_world_cam = _frames.world_from_camera(floor, centroid)
            self.floor = floor
            return True
        except Exception:                                    # noqa: BLE001
            return False

    def track(self, landmarker, limit=None):
        """Yield {name: xyz_mm} per frame. Empty dict when nobody is found."""
        import mediapipe as mp                               # noqa: F401

        for f in self.feed.frames(limit=limit):
            depth_mm, color, intr = f["depth_mm"], f["color"], f["intr"]
            if self.T_world_cam is None and not self._fit_world(depth_mm, intr):
                yield {}
                continue
            try:
                res = landmarker.detect_for_video(
                    mp.Image(image_format=mp.ImageFormat.SRGB, data=color),
                    int(f["t"] * 1000.0))
            except Exception:                                # noqa: BLE001
                yield {}
                continue
            lms = getattr(res, "pose_landmarks", None)
            if not lms:
                yield {}
                continue
            h, w = depth_mm.shape
            # NORMALISED COORDINATES -> PIXELS. MediaPipe's pose_landmarks are
            # 0..1 across the image; the depth frame is what they index into,
            # and the two are the same size because rsfeed aligns them.
            px = {}
            for name, idx in JOINT_IDX.items():
                p = lms[0][idx]
                px[name] = (p.x * w, p.y * h)
            yield joints_world_mm(px, depth_mm, intr, self.T_world_cam)


def _self_test():
    """Synthetic frames, no camera and no recording.

    What this DOES prove: the near-surface rule picks the near surface, no
    data stays absent rather than becoming a point at the origin, and the
    deprojection lands a known pixel at a known place.

    What it does NOT prove: that the numbers are right on a real D455. Only a
    camera or a recording can show that, and neither is available here.
    """
    print("bodydepth self-test (synthetic, no hardware)")

    # 1. Near surface, not the average. An elbow at 800mm in front of a
    #    stomach at 1000mm must read as the elbow.
    d = np.full((20, 20), 1000.0, np.float32)
    d[8:12, 8:12] = 800.0
    got = sample_depth_mm(d, 10, 10)
    assert 795.0 <= got <= 830.0, f"near surface: {got}"
    print(f"  near surface over a farther one: {got:.0f}mm (elbow at 800)")

    # 2. No data stays absent.
    assert sample_depth_mm(np.zeros((20, 20), np.float32), 10, 10) is None
    print("  all-zero window -> None, not 0.0")

    # 3. Deprojection. A joint ON the principal axis at 1000mm sits 1000mm
    #    straight ahead of the camera, so with an identity world transform it
    #    lands at (0, 0, 1000).
    class I:
        fx = fy = 600.0
        ppx, ppy = 10.0, 10.0
    d2 = np.full((20, 20), 1000.0, np.float32)
    p = joints_world_mm({"l_wrist": (10, 10)}, d2, I, np.eye(4))["l_wrist"]
    assert np.allclose(p, [0.0, 0.0, 1000.0], atol=1e-6), p
    print(f"  principal-axis pixel at 1000mm -> {tuple(round(v, 1) for v in p)}")

    # 4. A joint with no depth is OMITTED, not guessed.
    d3 = np.zeros((20, 20), np.float32)
    d3[10, 10] = 900.0
    out = joints_world_mm({"l_wrist": (10, 10), "r_wrist": (2, 2)}, d3, I,
                          np.eye(4))
    assert "l_wrist" in out and "r_wrist" not in out, out
    print("  joint with no valid depth is absent, not invented")

    print("OK -- arithmetic is sound. UNVERIFIED against a real D455 frame.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        src = sys.argv[1]
        print(f"replaying {src} (no camera needed)")
        b = DepthBody(src)
        for i, f in enumerate(b.feed.frames(limit=5)):
            print(f"  frame {i}: depth {f['depth_mm'].shape} "
                  f"color {f['color'].shape} t={f['t']:.3f}")
    else:
        _self_test()
