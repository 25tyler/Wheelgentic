#!/usr/bin/env python3
"""tools/live-bake.py -- measure a real person with the D455 and bake the
page's body file from what the camera actually saw.

    ~/wg-venv/bin/python tools/live-bake.py                 # on the GB10
    ~/wg-venv/bin/python tools/live-bake.py --seconds 20
    ~/wg-venv/bin/python tools/live-bake.py --aim-only     # just check aim

WHAT THIS CLOSES
----------------
web/assets/body.json is a RECORDING of a past solve: tools/export_body.py calls
anatomy.anatomical_body() with no arguments, which builds a population-typical
adult out of a table of numbers. Nothing on the projector has ever come off a
real person. The pitch's central claim -- "no preprogrammed actions, every body
type is different so paths come from the person's actual 3D model" -- was being
made by a file that contains the same adult every time it is baked.

This measures a person who is sitting in front of the camera right now, and the
partition on screen is solved against THEIR proportions.

ONE PIPELINE, TWICE OVER
------------------------
Nothing here measures a body and nothing here solves a partition. Both already
exist and both are used unmodified:

  * scrub3d/live/live_body.py's `Live` is the measurement engine. It is what
    the Rerun live view runs, frame for frame: MediaPipe for the joints and the
    outline, the floor fitted live for the world frame, and every dimension a
    running median over the frames that showed it clearly. Calling
    `Live.step()` in a loop is exactly what live_body.main() does; this file
    just does not draw the result.
  * tools/export_body.py's `export_one` is the serialiser AND the solver. It
    takes the same kwargs anatomy.anatomical_body() takes, so handing it the
    measured dimensions is a one-argument difference from the bake. The real
    partition.solve() runs, the real FleetGovernor is asked for its verdicts,
    and the file that comes out is byte-identical in SHAPE to body.json.

So there is no second measurement path that can disagree with the live view,
and no second solver that can disagree with the bake. A live body file differs
from a baked one in its numbers and in nothing else.

WHY THE SOLVE IS ALLOWED TO BE SLOW HERE
-----------------------------------------
partition.solve() takes 3.8 seconds, measured (3.87/3.77/3.76/3.76 across four
runs). That is 115 times too slow for a 30fps frame and it does not go near the
loop. It runs ONCE, after the capture window closes, while the operator is
standing up. Between beats, never during one.

HOW IT REACHES THE PROJECTOR
----------------------------
web/main.js already swaps body files by name: bodyFile() prefers `liveBody`
when the backend has set it, and the 'n' key reaches the alternate body. The
default output here is web/assets/body-live.json, which is precisely the name
bodyFile() already looks for and py/scrubbot.py already announces. No new
channel, no new key, no socket change -- the mechanism was built for this and
had nothing real to carry.

WHY IT DIAGNOSES INSTEAD OF PRODUCING GARBAGE
----------------------------------------------
The world frame comes from the floor, and a camera that cannot see the floor
produces a body with no height, no seat and a partition solved in a frame that
means nothing. That failure is silent: you get a JSON file, it loads, and the
numbers are wrong. Measured on this rig with the camera as aimed: top of frame
1218mm, middle 15061mm, floor solve fails.

So every run checks the aim FIRST, using tools/live-scan.py's `diagnose` and
`aim_verdict` -- the same gates frames.fit_floor() applies, in the order it
applies them -- and refuses to write a body file it cannot stand behind. What
it prints instead is the physical action the operator has to take at the
tripod.
"""
import argparse
import collections
import importlib.util
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
S3D = os.path.join(ROOT, "scrub3d")
LIVE = os.path.join(S3D, "live")
# scrub3d's modules import each other by bare name (`import anatomy`), and
# live/ does the same, so both directories go on the path exactly as
# live_body.py itself puts them there.
for p in (ROOT, S3D, LIVE):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_live_scan():
    """tools/live-scan.py, imported despite the hyphen in its name.

    The aim diagnosis lives there and is the right diagnosis: it walks
    fit_floor()'s own gates in fit_floor()'s own order. Re-deriving it here
    would be a second opinion about the floor, and the two would drift the
    first time frames.py changed a threshold. A hyphenated filename is not a
    reason to copy 140 lines.
    """
    path = os.path.join(HERE, "live-scan.py")
    spec = importlib.util.spec_from_file_location("live_scan_tool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# How much of the capture window must have found a person before the
# measurements are worth solving against. Below this the medians are made of a
# handful of frames and the prior (a typical adult, PRIOR_WEIGHT=8 frames in
# live_body) still dominates them -- which would quietly hand back the same
# baked adult under a filename that claims it was measured.
MIN_POSED_FRAMES = 40
# The dimensions that decide the partition: a torso and two arms. A capture
# that never measured these has nothing the solver can use, whatever else it
# saw. Legs are left out on purpose -- a seated person's thighs are often
# hidden by their own lap and the partition does not scrub them.
REQUIRED = ("shoulders", "chest_w", "waist_w", "torso_len",
            "upper_arm_len", "forearm_len", "forearm_w")
# A dimension counts as measured once its own median is carrying more weight
# than the adult prior. live_body blends med*n/(n+8) + prior*8/(n+8), so at
# n=8 the answer is half prior; at 24 it is three quarters measured.
MIN_SAMPLES = 24


def camera_or_why():
    """The attached D455, or a printed explanation and None.

    pipe.start() on a missing camera raises RuntimeError("No device connected")
    out of librealsense, which cannot tell an unplugged cable from a device
    another process is holding. Those need opposite actions from whoever is
    standing at the tripod, so the context is asked first.
    """
    import pyrealsense2 as rs
    devs = list(rs.context().query_devices())
    if not devs:
        print("NO REALSENSE CAMERA IS ATTACHED.")
        print("  librealsense enumerates zero devices, so there is nothing to")
        print("  measure. Check, in this order:")
        print("    * the D455's USB-C cable, at BOTH ends")
        print("    * that it is on a USB3 port: `lsusb` should list an Intel")
        print("      8086:0b5c device, and /dev/video* should exist")
        print("    * that no other process holds it: `fuser -v /dev/video*`")
        return None
    d = devs[0]
    return {"name": d.get_info(rs.camera_info.name),
            "serial": d.get_info(rs.camera_info.serial_number),
            "firmware": d.get_info(rs.camera_info.firmware_version)}


def check_aim(depth, intr, scan):
    """Is this camera pointed somewhere a body can be measured from? -> bool.

    Prints the finding either way. A pass here is not a promise the capture
    will work; it is the removal of the one failure that produces a plausible
    file full of meaningless numbers.
    """
    print(f"\n  the scene, {depth.shape[1]}x{depth.shape[0]}:")
    valid = depth[depth > 0]
    if not valid.size:
        print("    NO PIXEL IN THE FRAME CARRIES DEPTH.")
        print("    The lens is blocked, or all of it is out of range.")
        return False
    print(f"    {100.0 * (depth > 0).mean():.1f}% of pixels carry depth, "
          f"{np.percentile(valid, 1):.0f}mm to "
          f"{np.percentile(valid, 99):.0f}mm (1st-99th pct)")
    for line in scan.aim_verdict(depth)[0]:
        print(f"    {line}")

    print("\n  the floor (the world frame comes from it):")
    import frames as FRAME
    floor = FRAME.fit_floor(depth, intr)
    if floor is not None:
        print(f"    found: {floor['n_points']} points, "
              f"{floor['rms_mm']:.1f}mm rms")
        print(f"    camera {floor['camera_height_mm']:.0f}mm above the floor, "
              f"pitch {floor['pitch_down_deg']:.2f} deg nose-down, "
              f"roll {floor['roll_deg']:.2f} deg")
        return True

    print("    NO FLOOR PLANE FOUND -- a body measured now would be posed")
    print("    in a world frame that does not exist. Why:")
    findings, actions = scan.diagnose(depth, intr)
    for f in findings:
        print(f"      - {f}")
    print("\n    THE OPERATOR MUST, PHYSICALLY:")
    for act in actions:
        print(f"      * {act}")
    print(f"      * target: camera about 1.0-1.3m up, tilted DOWN "
          f"so that bare floor between {scan.Z_RANGE[0]:.0f}mm and "
          f"{scan.Z_RANGE[1]:.0f}mm fills the bottom "
          f"{100 * (1 - scan.ROW_FRAC):.0f}% of the image, with the person "
          f"seated 1.0-1.5m out")
    return False


def measured_dims(D, LB):
    """live_body's measurements -> anatomy.anatomical_body's `measurements`.

    THE SAME ARITHMETIC live_body.Body.build() DOES, and it has to be: the
    Rerun view and the projector must describe one person. Widths measured
    across the outline become circumferences through the same superellipse
    perimeter the builder uses, and the upper arm is clipped against the
    forearm for the same reason -- an upper arm beside a torso is rarely
    measured cleanly and the forearm nearly always is.
    """
    v = {k: m.value() for k, m in D.items()}
    import anatomy as AN
    lf, tf = AN.ADULT["limb_flatten"], AN.ADULT["torso_flatten"]
    v["upper_arm_w"] = float(np.clip(v["upper_arm_w"], 0.95 * v["forearm_w"],
                                     1.3 * v["forearm_w"]))
    return {
        "biacromial_mm": v["shoulders"],
        "upper_arm_len_mm": v["upper_arm_len"],
        "forearm_len_mm": v["forearm_len"],
        "torso_len_mm": v["torso_len"],
        "upper_arm_circ_mm": LB.circ_of(v["upper_arm_w"], LB.LIMB_P, lf),
        # The elbow is below any sleeve, so it is sized from the forearm.
        "elbow_circ_mm": 0.96 * LB.circ_of(v["forearm_w"], LB.LIMB_P, lf),
        "forearm_circ_mm": LB.circ_of(v["forearm_w"], LB.LIMB_P, lf),
        "wrist_circ_mm": LB.circ_of(v["wrist_w"], LB.LIMB_P, lf),
        "chest_circ_mm": LB.circ_of(v["chest_w"], LB.TORSO_P, tf),
        "waist_circ_mm": LB.circ_of(v["waist_w"], LB.TORSO_P, tf),
    }


def capture(seconds, upside_down, seat_mm, keep_floor, scan):
    """Watch a person for `seconds` and return live_body's measurements.

    -> (D, report) where D is the dict of Measured dimensions, or (None, why).

    THE CAMERA IS OPENED BY live_body.camera_source, not by a pipeline set up
    here. It is the one that turns an upside-down mount's pixels AND its
    intrinsics over together, and getting only one of those right gives a body
    that is subtly wrong in a way nothing downstream can detect.
    """
    import live_body as LB

    intr, frames = LB.camera_source(upside_down)
    live = LB.Live(intr, seat_mm, keep_floor)
    posed = 0
    aimed = None                  # the aim check, run once on a real frame
    t0 = time.time()
    last_say = 0.0
    try:
        for color, depth, t_frame in frames:
            now = time.time()
            if now - t0 > seconds:
                break
            # THE AIM IS CHECKED ON THE FIRST FRAME AND ONLY THEN. Doing it
            # before the loop would need a second pipeline open on the same
            # device, which librealsense refuses; doing it every frame would
            # cost a plane fit per frame for an answer that cannot change
            # while the tripod is still.
            if aimed is None:
                aimed = check_aim(depth, intr, scan)
                if not aimed:
                    return None, "the camera cannot see the floor"
                print(f"\n  measuring for {seconds:.0f}s -- SIT IN FRAME, "
                      f"facing the camera, arms clear of the torso")
            ev = live.step(color, depth, now)
            if "posed" in ev:
                posed += 1
            if now - last_say >= 2.0:
                last_say = now
                ready = sum(1 for k in REQUIRED if live.D[k].n >= MIN_SAMPLES)
                print(f"    {now - t0:5.1f}s  posed {posed:4d} frames, "
                      f"{ready}/{len(REQUIRED)} key dimensions measured",
                      flush=True)
    finally:
        live.close()

    if posed < MIN_POSED_FRAMES:
        return None, (f"only {posed} frames found a person (need "
                      f"{MIN_POSED_FRAMES}). Nobody was in frame long enough, "
                      f"or MediaPipe could not see them -- check the lighting "
                      f"and that the whole torso is in view")
    short = [k for k in REQUIRED if live.D[k].n < MIN_SAMPLES]
    if short:
        detail = ", ".join(f"{k} ({live.D[k].n})" for k in short)
        return None, (f"these dimensions were never measured cleanly enough "
                      f"(under {MIN_SAMPLES} good frames each): {detail}. "
                      f"They would fall back to a typical adult's value, "
                      f"which is what this tool exists to stop shipping "
                      f"as a measurement")
    return live.D, {"posed": posed, "seat": live.seat}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=25.0,
                    help="how long to watch the person (default 25)")
    ap.add_argument("--out", default="body-live",
                    help="web/assets/<NAME>.json (default body-live, which is "
                         "the name web/main.js already prefers)")
    ap.add_argument("--upside-down", action="store_true",
                    help="the camera is mounted upside down: turn its frames "
                         "and its intrinsics over together")
    ap.add_argument("--seat-mm", type=float, default=None,
                    help="the chair's seat height if it was measured by tape; "
                         "otherwise it is taken from where the torso ends")
    ap.add_argument("--keep-floor", action="store_true",
                    help="hold the floor once found, whatever later fits say")
    ap.add_argument("--aim-only", action="store_true",
                    help="check the aim and print the verdict, write nothing")
    a = ap.parse_args()

    scan = _load_live_scan()

    cam = camera_or_why()
    if cam is None:
        return 2
    print(f"camera: {cam['name']} serial {cam['serial']} "
          f"firmware {cam['firmware']}")

    if a.aim_only:
        import live_body as LB
        intr, frames = LB.camera_source(a.upside_down)
        # A few frames of settling: the first ones out of a D455 are darker and
        # sparser than the stream it reaches, and judging the aim on them
        # reports a floor problem the camera does not have.
        depth = None
        for i, (_c, d, _t) in enumerate(frames):
            depth = d
            if i >= 15:
                break
        return 0 if check_aim(depth, intr, scan) else 1

    D, why = capture(a.seconds, a.upside_down, a.seat_mm, a.keep_floor, scan)
    if D is None:
        print(f"\nNO BODY FILE WRITTEN: {why}")
        print("The page keeps the body it already has, which is the point of "
              "refusing.")
        return 1

    import live_body as LB
    meas = measured_dims(D, LB)
    print(f"\n  measured this person, over {why['posed']} posed frames:")
    for k, m in D.items():
        sp = m.spread()
        sp = "" if sp is None else f", spread {sp:.0f}mm"
        print(f"    {m.label:<28} {m.value():7.1f}mm  ({m.n} frames{sp})")
    if why.get("seat"):
        print(f"    seat height                  {why['seat']['z']:7.1f}mm")

    # THE REAL SOLVER, ON THE MEASURED PERSON. export_one takes the kwargs
    # anatomy.anatomical_body takes, so this is the bake with one argument
    # different. partition.solve() runs (3.8s, measured), the FleetGovernor is
    # asked its verdicts, and the file that lands has the same shape body.json
    # has -- because it came out of the same function.
    import export_body as EX
    dest = os.path.join(ROOT, "web", "assets", f"{a.out}.json")
    print(f"\n  solving the four-arm partition on the measured body "
          f"(about 4 seconds) ...")
    t0 = time.time()
    EX.export_one(a.out, {"measurements": meas}, dest=dest)
    print(f"  solved and written in {time.time() - t0:.1f}s")

    # A NOTE IN THE FILE SAYING IT IS REAL. The page does not read this, and
    # that is fine: the reason it is here is that a body-live.json on disk is
    # otherwise indistinguishable from a bake someone renamed, and the next
    # person to find one has no way to tell whether the numbers on the
    # projector came off a person or out of a table.
    with open(dest, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["source"] = ("scrub3d live_body.Live (D455 + MediaPipe) measurements "
                     "-> anatomy.anatomical_body + partition.solve")
    doc["measured"] = {"frames_posed": int(why["posed"]),
                       "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "dimensions_mm": {k: round(float(v), 1)
                                         for k, v in meas.items()},
                       "samples": {k: int(m.n) for k, m in D.items()}}
    tmp = dest + ".partial"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    os.replace(tmp, dest)

    print(f"\n{a.out}.json is a REAL PERSON. To put it on the projector:")
    print(f"  * the page prefers assets/body-live.json the moment the backend "
          f"announces a solve (web/main.js bodyFile())")
    print(f"  * or press 'b' to show the measured body, 'n' to compare it "
          f"against the baked one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
