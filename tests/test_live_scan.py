"""tests/test_live_scan.py -- tools/live-scan.py against geometry we constructed.

WHY THIS EXISTS: live-scan.py's job is to tell an operator standing at the
tripod what is wrong with the aim. A tool that misreads a scene gives a
confident wrong instruction, which is worse than the raw "no floor plane
found" it replaces -- the operator moves the camera the wrong way and trusts
the result. So the diagnosis is checked against scenes whose answer is known
because this file rendered them.

WHAT IT PROVES: that fit_floor()'s three gates behave as live-scan.py claims
(a level floor solves, a floor tilted inside 30 deg solves, a wall does not),
that a wall is NAMED as vertical rather than reported as a generic failure,
and that the real mis-aimed scene observed on the rig is classified as
pointing up with the numbers that prove it.

WHAT IT CANNOT PROVE: anything about the physical camera. These depth images
are ray-traced from analytic planes, so they have no stereo bias, no
multipath, no shadow behind the subject and no rolling-shutter skew. A green
run here does NOT mean a body scan works. It means that IF the camera hands
live-scan.py a scene of a given shape, the tool reads that shape correctly.
"""
import importlib.util
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scrub3d import frames as FRAME

# live-scan.py has a hyphen in its name, so it cannot be imported by `import`.
# Loading it by path is the only way to test it without renaming the command
# an operator types.
_spec = importlib.util.spec_from_file_location(
    "live_scan", os.path.join(ROOT, "tools", "live-scan.py"))
LS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LS)

W, H = 848, 480
INTR = {"width": W, "height": H, "fx": 425.0, "fy": 425.0,
        "ppx": W / 2.0, "ppy": H / 2.0, "model": "brown_conrady",
        "coeffs": [0.0] * 5}


def render_plane(normal, offset_mm):
    """Depth image of one infinite plane, camera at the origin.

    A ray per pixel, intersected with {p : n.p == d}. Anything behind the
    camera or past z16's usable range reads as 0, which is record.py's and
    frames.py's NO DATA, not zero millimetres.
    """
    ys, xs = np.mgrid[0:H, 0:W]
    dx = (xs - INTR["ppx"]) / INTR["fx"]
    dy = (ys - INTR["ppy"]) / INTR["fy"]
    dirs = np.stack([dx, dy, np.ones_like(dx)], -1)
    dirs /= np.linalg.norm(dirs, axis=-1, keepdims=True)
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = offset_mm / (dirs @ n)
    z = dirs[..., 2] * t
    return np.where((t > 0) & np.isfinite(z) & (z < 20000), z, 0.0).astype(np.float32)


def pitched_floor_normal(deg):
    """Floor normal in camera coords for a camera pitched `deg` nose-down."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]]) @ np.array([0.0, -1.0, 0.0])


def test_level_floor_solves():
    """The baseline. If this fails nothing else in the file means anything."""
    f = FRAME.fit_floor(render_plane([0, -1, 0], -1100.0), INTR)
    assert f is not None, "a level floor 1100mm below the camera was not found"
    assert abs(f["camera_height_mm"] - 1100.0) < 30.0, f["camera_height_mm"]


def test_pitched_floor_inside_gate_solves():
    """20 degrees is inside fit_floor's 30 degree max_tilt_deg, so it must fit."""
    f = FRAME.fit_floor(render_plane(pitched_floor_normal(20.0), -1100.0), INTR)
    assert f is not None, "a floor pitched 20 deg was rejected by a 30 deg gate"
    assert abs(f["pitch_down_deg"] - 20.0) < 2.0, f["pitch_down_deg"]
    assert abs(f["camera_height_mm"] - 1100.0) < 30.0, f["camera_height_mm"]


def test_wall_is_rejected_and_named():
    """A wall must fail the tilt gate AND be described as vertical.

    The failure alone is not enough. "No floor plane found" on a wall sends an
    operator looking for a lighting or range problem; naming the surface
    vertical sends them to tilt the camera down, which is the actual fix.
    """
    depth = render_plane([0, 0, -1], -2000.0)
    assert FRAME.fit_floor(depth, INTR) is None, "a wall was accepted as a floor"
    findings, actions = LS.diagnose(depth, INTR)
    assert any("VERTICAL" in s for s in findings), findings
    assert any("down" in s.lower() for s in actions), actions


def test_real_misaimed_scene_reads_as_pointing_up():
    """The scene actually observed on the GB10 rig, from its measured thirds.

    Top 1218mm, middle 15061mm, bottom 8901mm: the bottom of the frame is the
    FURTHEST thing in it, which is the inverse of a room-facing camera. The
    tool must say so, and must not find a floor.
    """
    depth = np.zeros((H, W), np.float32)
    depth[:H // 3, :] = 1218.0
    depth[H // 3:2 * H // 3, :] = 15061.0
    depth[2 * H // 3:, :] = 8901.0

    lines, aim = LS.aim_verdict(depth)
    assert aim == "up", f"expected 'up', got {aim!r}: {lines}"
    assert FRAME.fit_floor(depth, INTR) is None, "a floor was found in the sky"

    findings, actions = LS.diagnose(depth, INTR)
    # The band gate is what actually rejects this scene, and the report has to
    # carry the number rather than the verdict alone.
    assert any("TOO FAR" in s for s in findings), findings
    assert any("DOWN" in s or "down" in s for s in actions), actions


def test_good_aim_with_a_subject_is_not_called_unusual():
    """A person in frame must not make a correct aim read as a bad one.

    Regression: aim_verdict originally compared the bottom third against the
    middle third. A subject standing in the centre pulls the middle to their
    own standoff, so a camera that was genuinely 1100mm up and pitched 12 deg
    onto a real floor reported "the aim is unusual". The bottom third alone is
    the honest signal.
    """
    depth = render_plane(pitched_floor_normal(12.0), -1100.0)
    subject = np.zeros_like(depth)
    cx, cy = W // 2, int(H * 0.42)
    hw = int(0.5 * 500 / 2 * INTR["fx"] / 1000.0)
    hh = int(900 / 2 * INTR["fy"] / 1000.0)
    subject[max(0, cy - hh):cy + hh, max(0, cx - hw):cx + hw] = 1000.0
    depth = np.where((subject > 0) & ((depth <= 0) | (subject < depth)),
                     subject, depth)

    _, aim = LS.aim_verdict(depth)
    assert aim == "down", f"a correct aim with a subject in it read as {aim!r}"
    f = FRAME.fit_floor(depth, INTR)
    assert f is not None, "the floor was lost once a subject stood on it"
    assert abs(f["camera_height_mm"] - 1100.0) < 30.0, f["camera_height_mm"]


def test_subject_survives_without_a_floor():
    """subject_mask + deproject need depth only, so they outlive a failed fit.

    This is the claim live-scan.py makes when it reports a subject in CAMERA
    coordinates on a scene with no floor. If it were false the tool would be
    printing a body that does not exist.
    """
    depth = np.zeros((H, W), np.float32)
    depth[2 * H // 3:, :] = 9000.0                 # far, so no floor is findable
    cx, cy = W // 2, H // 3
    depth[cy - 80:cy + 80, cx - 60:cx + 60] = 1000.0

    assert FRAME.fit_floor(depth, INTR) is None, "this scene should have no floor"
    pts = FRAME.deproject(depth, INTR, FRAME.subject_mask(depth, (600.0, 1300.0)))
    assert len(pts) > 5000, f"the subject blob vanished: {len(pts)} points"
    assert abs(pts[:, 2].mean() - 1000.0) < 50.0, pts[:, 2].mean()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
    print("NOTE: analytic depth only. This does not prove the camera works.")
