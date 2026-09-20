#!/usr/bin/env python3
"""tools/live-scan.py -- capture from the live D455 and solve the world frame.

    ./venv/bin/python tools/live-scan.py --out recordings/live01
    ./venv/bin/python tools/live-scan.py --out recordings/live01 --reuse

Everything in scrub3d that touches the real world starts from a capture
directory holding `meta.json` + `depth_median_mm.npy`. record.py makes one;
frames.solve() consumes one. Nothing tied the two together at a terminal, so
"point the camera at a person and tell me what you see" had no command.

This is that command, and it is a THIN WRAPPER ON PURPOSE. The capture is
record.record() unmodified -- same temporal median, same 180 degree rotation
of pixels and intrinsics together, same high_accuracy preset. The solve is
frames.solve() unmodified. If this file had its own capture path or its own
plane fit, the live rig and every capture on disk would be answering two
different questions, and the one that ran on a person would be the one nobody
had checked.

WHY IT DIAGNOSES INSTEAD OF JUST FAILING
-----------------------------------------
frames.solve() raises "no floor plane found" and stops. That sentence is true
and useless: it cannot distinguish a camera aimed at a ceiling from a camera
with a cloth over the lens from a floor that is genuinely out of range. Each
needs a different physical action from whoever is standing next to the tripod.

fit_floor() has three gates, and each one can be checked against the capture
BEFORE guessing. So when the solve fails this reports which gate rejected the
scene, with the measured number that tripped it, and what to physically do.

SUBJECT WITHOUT FLOOR
---------------------
frames.solve() needs the floor because the world frame is derived from it. But
frames.subject_mask() + frames.deproject() need only depth, so the subject
blob in CAMERA coordinates is recoverable from a capture with no floor in it.
That is reported separately and labelled as such: camera-frame millimetres are
not a body scan, they are the half of one that does not need the floor.
"""
import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scrub3d import frames as FRAME                             # noqa: E402
from scrub3d import record as RECORD                            # noqa: E402

# fit_floor()'s defaults, restated here because the diagnosis has to test the
# SAME gates the solver applies. Passed through explicitly rather than left
# implicit so a change in frames.py cannot make this file quietly lie.
ROW_FRAC = 0.55
Z_RANGE = (800.0, 4500.0)
MAX_TILT_DEG = 30.0


def require_camera():
    """Name the attached D455, or explain what is missing. -> dict or None.

    record.record() calls pipe.start() and a missing camera comes back as a
    bare RuntimeError("No device connected") tracebacked out of librealsense.
    That sentence cannot tell an unplugged USB cable from a device claimed by
    another process, and those need opposite actions from whoever is standing
    at the tripod. Asking the context FIRST costs one call and turns the
    traceback into an instruction.

    NO SDK AT ALL IS A THIRD CASE, and it needs its own answer. On Apple
    Silicon `pyrealsense2` is the replay stub in this venv (Intel ships no
    arm64 macOS wheel), and rs.context() RAISES rather than returning an empty
    device list. Unhandled, that crashed this function with a traceback out of
    the stub -- from the one function whose entire job is to turn a camera
    problem into an instruction. Measured on this Mac: RuntimeError from
    pyrealsense2.py line 40, before a single diagnostic line printed.
    Returning None would be WRONG here: it would print "check your USB cable"
    on a machine where no cable could ever help. So it is its own branch.
    """
    try:
        import pyrealsense2 as rs
        devs = list(rs.context().query_devices())
    except (ImportError, RuntimeError, AttributeError) as exc:
        # AttributeError as well as RuntimeError: a stub that resolves
        # attributes to placeholders fails at the CALL, and which of the two
        # you get depends on how the stub is written. Both mean the same
        # thing here -- there is no SDK -- and both must reach this branch.
        return {"no_sdk": str(exc).split("\n")[0]}
    if not devs:
        return None
    d = devs[0]
    return {"name": d.get_info(rs.camera_info.name),
            "serial": d.get_info(rs.camera_info.serial_number),
            "firmware": d.get_info(rs.camera_info.firmware_version)}


def thirds(depth_mm):
    """Median depth of the top, middle and bottom third of the frame.

    The single most diagnostic number for aim. A camera looking level at a
    room sees the floor at the bottom and it is the NEAREST thing in frame, so
    bottom < middle. Inverted, or a bottom third reading tens of metres, means
    the camera is not looking where anyone thinks it is.
    """
    h = depth_mm.shape[0]
    out = []
    for lo, hi in ((0, h // 3), (h // 3, 2 * h // 3), (2 * h // 3, h)):
        band = depth_mm[lo:hi]
        ok = band[band > 0]
        out.append(float(np.median(ok)) if ok.size else float("nan"))
    return out


def diagnose(depth_mm, intr):
    """Why fit_floor() found nothing. -> (list of findings, list of actions).

    Walks fit_floor()'s gates in the order fit_floor() applies them and stops
    describing the scene once one of them explains the failure. Reporting all
    three at once would be noise: a band with 40 points in it does not also
    have a meaningful tilt.
    """
    findings, actions = [], []
    h, w = depth_mm.shape
    top, mid, bot = thirds(depth_mm)

    # GATE 1 -- is there anything in the search band at all?
    #
    # fit_floor only ever looks at rows below row_frac of the image, within
    # z_range. Under 2000 surviving pixels it returns None before fitting
    # anything, so no amount of tilt analysis applies.
    band = np.zeros((h, w), bool)
    band[int(h * ROW_FRAC):, :] = True
    in_range = band & (depth_mm > Z_RANGE[0]) & (depth_mm < Z_RANGE[1])
    lower = depth_mm[int(h * ROW_FRAC):, :]
    lower_valid = lower[lower > 0]
    findings.append(
        f"lower {100 * (1 - ROW_FRAC):.0f}% of the frame (where the floor must "
        f"be): {lower_valid.size} pixels with depth, "
        f"{in_range.sum()} of them inside the {Z_RANGE[0]:.0f}-{Z_RANGE[1]:.0f}mm "
        f"search window (fit_floor needs 2000)")

    if in_range.sum() < 2000:
        if lower_valid.size < 2000:
            findings.append(
                "the bottom of the frame has almost no depth at all -- either "
                "nothing is close enough to range, or the lens is blocked")
            actions.append("check the lens is uncovered and something solid is "
                           "within 4.5m of the bottom of the frame")
        else:
            near = int((lower_valid < Z_RANGE[0]).sum())
            far = int((lower_valid > Z_RANGE[1]).sum())
            side = "TOO CLOSE" if near > far else "TOO FAR"
            findings.append(
                f"the bottom of the frame HAS depth ({lower_valid.size} px, "
                f"median {np.median(lower_valid):.0f}mm) but it is {side}: "
                f"{near} px under {Z_RANGE[0]:.0f}mm, {far} px over "
                f"{Z_RANGE[1]:.0f}mm")
            if far > near:
                actions.append(
                    f"the bottom of the frame is looking into open space or at "
                    f"a far wall ({np.median(lower_valid):.0f}mm away). Tilt "
                    f"the camera DOWN until floor within 4.5m fills the bottom "
                    f"third")
            else:
                actions.append(
                    f"the camera is closer than {Z_RANGE[0]:.0f}mm to whatever "
                    f"is below it. Raise it, or back it away from the wall")
        return findings, actions

    # GATE 2 -- is the dominant plane in that band close enough to horizontal?
    #
    # Only reached when the band is populated, so a tilt figure here is a real
    # measurement of a real surface rather than a fit to forty stray pixels.
    pts = FRAME.deproject(depth_mm, intr, in_range)
    got = FRAME.fit_plane(pts)
    if got is None:
        findings.append("no single plane fits the search band -- it is clutter, "
                        "not a surface")
        actions.append("clear the bottom of the frame, or re-aim so a flat "
                       "floor occupies it")
        return findings, actions

    n, d, rms, used = got
    if n @ FRAME.CAM_DOWN < 0:
        n, d = -n, -d
    tilt = math.degrees(math.acos(float(np.clip(n @ FRAME.CAM_DOWN, -1.0, 1.0))))
    findings.append(
        f"the dominant surface below the midline sits {tilt:.1f} deg off the "
        f"camera's own down axis ({used} points, {rms:.0f}mm rms). fit_floor "
        f"rejects anything past {MAX_TILT_DEG:.0f} deg")

    if tilt > MAX_TILT_DEG:
        # A near-perpendicular surface under a camera is a wall the camera is
        # facing, which is what a level-or-upward aim produces.
        if tilt > 60.0:
            findings.append(
                "at that angle the surface is effectively VERTICAL relative to "
                "the camera: the camera is looking ALONG the floor at a wall, "
                "not DOWN at the floor")
            actions.append(
                "tilt the camera DOWN. It needs to be within 30 deg of looking "
                "straight down at the floor plane, i.e. the floor must be "
                "visibly receding in the bottom of the image")
        else:
            actions.append(
                f"tilt the camera down by roughly {tilt - MAX_TILT_DEG + 10:.0f} "
                f"deg more so the floor comes within the 30 deg gate")
        return findings, actions

    findings.append("the band and the tilt gates both pass, so the failure is "
                    "in RANSAC support or the refine step -- the surface is "
                    "flat enough and low enough but too sparse or too noisy")
    actions.append("get more floor into the frame, or improve lighting/texture "
                   "on it")
    return findings, actions


def aim_verdict(depth_mm):
    """What the camera appears to be pointed at, from the depth gradient alone.

    Independent of any plane fit, so it still says something when every fit
    fails. A camera aimed level-and-forward at a room has a near bottom and a
    far top; every other arrangement is a recognisable inversion of that.
    """
    top, mid, bot = thirds(depth_mm)
    lines = [f"per-third median depth: top {top:.0f}mm, middle {mid:.0f}mm, "
             f"bottom {bot:.0f}mm"]

    finite = [v for v in (top, mid, bot) if np.isfinite(v)]
    if len(finite) < 3:
        lines.append("a third of the frame has no depth at all; the camera is "
                     "looking at something out of range or at nothing")
        return lines, "unknown"

    # 65535 is the z16 saturation value: no return, i.e. open space or sky.
    if bot > 8000.0 and top < bot:
        lines.append(
            "the BOTTOM of the frame is the FURTHEST thing in it. On a camera "
            "aimed at a room the floor is the nearest surface and sits at the "
            "bottom, so this is inverted: the camera appears to be pointed UP "
            "at a ceiling, or lying on its back")
        lines.append("aim it at: a person seated ~1-1.5m away, with bare floor "
                     "filling the bottom third of the image")
        return lines, "up"

    # The discriminator is the BOTTOM third alone, not bottom-vs-middle.
    # A subject standing in the centre of the frame pulls the middle third to
    # their own standoff, so on a correctly aimed rig with someone in it the
    # middle can read NEARER than the floor below them. Comparing the two
    # then calls a good aim "unusual" -- measured on the synthetic rig check,
    # which reported top 1000mm / middle 4109mm / bottom 1846mm from a camera
    # that was in fact 1100mm up and pitched 12 deg down onto a real floor.
    # What actually matters is whether floor within fit_floor's window fills
    # the bottom of the frame, and that is a statement about the bottom only.
    if bot < Z_RANGE[1]:
        lines.append(
            f"the bottom of the frame is {bot:.0f}mm away, inside fit_floor's "
            f"{Z_RANGE[0]:.0f}-{Z_RANGE[1]:.0f}mm window: that is what a floor "
            f"under a downward-tilted camera looks like")
        return lines, "down"

    if top > 8000.0 and bot < 8000.0:
        lines.append("open space at the top, something closer at the bottom: "
                     "roughly the aim of a room-facing camera, but the bottom "
                     f"is {bot:.0f}mm out, past the {Z_RANGE[1]:.0f}mm the "
                     "floor search reaches")
        return lines, "level"

    lines.append(f"every third of the frame is far away (nearest {min(finite):.0f}mm); "
                 "the camera is looking across open space, not at a floor")
    return lines, "unclear"


def report_subject(depth_mm, intr, z_range):
    """The subject blob in CAMERA millimetres. Works with no floor.

    Deliberately separate from the world-frame solve. frames.subject_mask is
    a depth window plus a largest-blob pick, and neither step knows where the
    floor is, so this survives a failed fit. What it CANNOT do is report a
    height above the floor or place the body in the frame the rest of scrub3d
    consumes -- that needs the floor, and saying so is the point.
    """
    mask = FRAME.subject_mask(depth_mm, z_range)
    px = int(mask.sum())
    print(f"    depth window {z_range[0]:.0f}-{z_range[1]:.0f}mm -> largest "
          f"blob is {px} px ({100.0 * px / mask.size:.1f}% of frame)")
    if px < 500:
        print("    too small to be a person. Either nobody is in that depth "
              "window, or the window is wrong for this standoff.")
        return None

    pts = FRAME.deproject(depth_mm, intr, mask)
    if not len(pts):
        print("    the blob has no valid depth behind it")
        return None
    c = pts.mean(0)
    print(f"    {len(pts)} points, centroid {c.round(0)} mm in CAMERA "
          f"coordinates (x right, y down, z forward)")
    # np.ptp(), not pts[:, 0].ptp(): the ndarray METHOD was removed in NumPy
    # 2.0 and the box runs 2.1.3. The free function has always worked on both.
    print(f"    extent  x {np.ptp(pts[:, 0]):.0f}mm  y {np.ptp(pts[:, 1]):.0f}mm  "
          f"z {np.ptp(pts[:, 2]):.0f}mm")
    print(f"    standoff {c[2]:.0f}mm, sampling "
          f"{FRAME.mm_per_px(intr, c[2]):.2f} mm per pixel")
    return pts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "recordings", "live01"),
                    help="capture directory to write (and then solve)")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--median-frames", type=int, default=60)
    ap.add_argument("--label", default="live scan")
    ap.add_argument("--rotate", choices=RECORD.ROTATIONS, default="180",
                    help="the rig's camera is mounted upside down")
    ap.add_argument("--preset", default="high_accuracy")
    ap.add_argument("--no-bag", action="store_true", default=True,
                    help="a bag is ~6GB/min and the solve never reads it")
    ap.add_argument("--reuse", action="store_true",
                    help="skip the capture and solve the existing --out dir")
    # scan01's subject sits 600-1300mm out. A different standoff needs a
    # different window, and getting it wrong looks identical to an empty room,
    # so it is a flag rather than a constant.
    ap.add_argument("--subject-range", default="600x1300",
                    help="depth window for the subject blob, mm")
    a = ap.parse_args()

    zlo, zhi = (float(v) for v in a.subject_range.split("x"))

    if a.reuse:
        print(f"reusing capture at {a.out}")
    else:
        cam = require_camera()
        if cam is not None and "no_sdk" in cam:
            # A DIFFERENT PROBLEM FROM AN UNPLUGGED CAMERA, so a different
            # instruction. Nothing anyone does at the tripod fixes this one.
            print("NO REALSENSE SDK ON THIS MACHINE.")
            print(f"  {cam['no_sdk']}")
            print("  Intel ships no pyrealsense2 wheel for Apple Silicon, so "
                  "this venv carries a replay stub instead.")
            print("  Capture has to happen on a machine with librealsense "
                  "(the Linux box). To work on a capture that")
            print(f"  already exists here: --reuse --out {a.out}")
            return 2
        if cam is None:
            print("NO REALSENSE CAMERA IS ATTACHED.")
            print("  librealsense enumerates zero devices, so there is nothing "
                  "to capture from.")
            print("  Check, in this order:")
            print("    * the D455's USB-C cable, at BOTH ends")
            print("    * that it is on a USB3 port -- `lsusb` should list an "
                  "Intel 8086:0b5c device")
            print("    * that no other process holds it (`fuser -v /dev/video*`)")
            print(f"  To work on an existing capture instead: "
                  f"--reuse --out {a.out}")
            return 2
        print(f"camera: {cam['name']} serial {cam['serial']} "
              f"firmware {cam['firmware']}")
        print(f"capturing {a.seconds:.0f}s from the live camera -> {a.out}")
        m = RECORD.record(a.out, seconds=a.seconds,
                          median_frames=a.median_frames,
                          want_bag=not a.no_bag, label=a.label,
                          rotate=a.rotate, preset=a.preset)
        print(f"  {m['frames_captured']} frames, median over "
              f"{m['median_frames']}")
        print(f"  depth valid {100 * m['depth_valid_fraction']:.1f}% ever, "
              f"{100 * m['median_valid_fraction']:.1f}% usable in the median")
        if m.get("gravity_cam"):
            g = np.array(m["gravity_cam"])
            print(f"  IMU gravity (camera frame, up) {g.round(3)} from "
                  f"{m['gravity_samples']} samples")

    with open(os.path.join(a.out, "meta.json")) as fh:
        meta = json.load(fh)
    depth = np.load(os.path.join(a.out, "depth_median_mm.npy"))
    intr = meta["color_intrinsics"]

    print(f"\n  the scene, {depth.shape[1]}x{depth.shape[0]}:")
    valid = depth[depth > 0]
    print(f"    {100.0 * (depth > 0).mean():.1f}% of pixels carry depth, "
          f"{np.percentile(valid, 1):.0f}mm to {np.percentile(valid, 99):.0f}mm "
          f"(1st-99th pct)")
    aim_lines, aim = aim_verdict(depth)
    for line in aim_lines:
        print(f"    {line}")

    # THE SOLVE. fit_floor first and directly, because solve() raises on a
    # failure and the failure is the case this tool exists to explain.
    print("\n  the floor:")
    floor = FRAME.fit_floor(depth, intr)
    if floor is None:
        print("    NO FLOOR PLANE FOUND. Why:")
        findings, actions = diagnose(depth, intr)
        for f in findings:
            print(f"      - {f}")
        print("\n    to fix it, physically:")
        for act in actions:
            print(f"      * {act}")
        print(f"      * target: camera ~1.0-1.3m above the floor, tilted DOWN "
              f"so bare floor between "
              f"{Z_RANGE[0]:.0f}mm and {Z_RANGE[1]:.0f}mm fills the bottom "
              f"{100 * (1 - ROW_FRAC):.0f}% of the image, subject seated "
              f"{zlo:.0f}-{zhi:.0f}mm away")
    else:
        print(f"    normal {np.array(floor['normal_cam']).round(4)}  "
              f"{floor['n_points']} points  {floor['rms_mm']:.1f}mm rms")
        print(f"    camera height {floor['camera_height_mm']:.0f}mm above the "
              f"floor")
        print(f"    pitch {floor['pitch_down_deg']:.2f} deg nose-down, roll "
              f"{floor['roll_deg']:.2f} deg")

    print("\n  the subject, in camera coordinates (needs no floor):")
    subj = report_subject(depth, intr, (zlo, zhi))

    print("\n  the world frame:")
    if floor is None:
        print("    NOT SOLVED -- the world frame is DERIVED from the floor "
              "(frames.world_from_camera takes the floor normal as +Z), so "
              "there is no way to produce one without it.")
        print("    Consequence: the subject above is in camera millimetres "
              "only. No height above the floor, no facing direction, and "
              "nothing that can feed anatomy/partition, which both assume a "
              "body at the world origin facing +X.")
        return 1

    r = FRAME.solve(a.out, z_range=(zlo, zhi))
    T = r["T_world_camera"]
    np.set_printoptions(precision=3, suppress=True)
    for lbl, row in zip(("+X facing", "+Y left  ", "+Z up    "), T[:3, :3]):
        print(f"    {lbl}  {row}")
    print(f"    translation {T[:3, 3].round(1)} mm")
    if "subject_world" in r:
        s = r["subject_world"]
        print(f"\n  the subject, in world millimetres above the floor:")
        print(f"    {s['n']} points, crown {s['z_p99_mm']:.0f}mm "
              f"(max {s['z_max_mm']:.0f}), lowest seen {s['z_min_mm']:.0f}mm")
        print(f"    centroid {np.array(s['centroid_mm']).round(0)}")
        print(f"    lateral extent {s['y_span_mm']:.0f}mm")
    else:
        print("\n    the floor solved but no subject blob was found, so there "
              "is a world frame and nobody in it")
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
