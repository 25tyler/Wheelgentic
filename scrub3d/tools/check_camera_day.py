"""Everything about camera day that can be checked WITHOUT the camera.

    python scrub3d/tools/check_camera_day.py

The runbook in CAMERA-DAY.md is four commands. Three of them can be exercised
today against the recorded captures, with only the RealSense call itself stubbed
out, and the one that cannot is named at the end rather than assumed fine.

WHY THIS EXISTS AS A SCRIPT AND NOT AS A PARAGRAPH
---------------------------------------------------
The failure this is aimed at has already happened once here: `main.py` logged
every frame to Rerun without ever creating a recording stream, so the documented
command ran perfectly, printed every number, and showed nobody anything. That is
invisible to reading and obvious to running.

The other failures it covers are the same shape. Does `--record` pass rotate=180
so the capture is not sideways? Does the segmentation land at the image's own
resolution rather than the network's? Does the temp file get renamed rather than
left behind? Each is a one-line mistake that surfaces as a whole wasted session
with a volunteer sitting in a chair.
"""
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import frames as FRAME                                          # noqa: E402
import main as MAIN                                             # noqa: E402
import record as REC                                            # noqa: E402
import scan as SCAN                                             # noqa: E402
import shell as SHELL                                           # noqa: E402

SCAN_DIR = os.path.join(FRAME.DATA, "scan01")
FAKE = "camera_day_check"


def _fake_camera(captured):
    """Stand in for the D455: copy a real capture, record what it was asked."""
    def record(out_dir, **kw):
        captured.update(kw)
        os.makedirs(out_dir, exist_ok=True)
        for f in ("depth_median_mm.npy", "depth_valid_count.npy",
                  "color_median.png", "meta.json"):
            shutil.copy(os.path.join(SCAN_DIR, f), os.path.join(out_dir, f))
    return record


def check_record_path():
    """Step 1 of the runbook, with only the camera stubbed. -> list of lines."""
    out = os.path.join(FRAME.DATA, FAKE)
    shutil.rmtree(out, ignore_errors=True)
    asked, real = {}, REC.record
    REC.record = _fake_camera(asked)
    try:
        got = MAIN._record_and_segment(FAKE, 10.0)
    finally:
        REC.record = real

    lines = []
    assert got == out, f"record returned {got}, expected {out}"

    # The rotation is the one that costs a whole session: a sideways capture
    # produces sideways intrinsics and still looks like a plausible cloud.
    assert asked.get("rotate") == "180", \
        f"--record asked for rotate={asked.get('rotate')}, must be 180"
    lines.append(f"rotate={asked['rotate']} preset={asked['preset']}")

    # The scan wants density, not accuracy. Opposite of the live loop.
    assert asked.get("preset") == "high_density", \
        f"the scan needs high_density, asked for {asked.get('preset')}"

    seg = np.load(os.path.join(out, "sapiens_seg.npy"))
    depth = np.load(os.path.join(out, "depth_median_mm.npy"))
    assert seg.shape == depth.shape, \
        f"segmentation {seg.shape} is not registered to the image {depth.shape}"
    assert not os.path.exists(os.path.join(out, "sapiens_seg.tmp.npy")), \
        "the temp mask was left behind; numpy appends .npy to a path lacking it"
    lines.append(f"mask {seg.shape} registered to the image, "
                 f"{len([c for c in np.unique(seg) if c])} classes")

    body, _, _, rep = SCAN.scan(out)
    lines.append(f"scanned {len(body.regions)} regions, "
                 f"{rep['scrubbable_cm2']:.0f}cm2, {rep['clothing']['measuring']}")
    shutil.rmtree(out, ignore_errors=True)
    return lines


def check_reconstruction():
    """Step 2: the reconstruction, and that it refuses when it should."""
    sh = SHELL.build(SCAN_DIR, verbose=False)
    lines = [f"{len(sh['verts'])} vertices, {sh['source']['normals']}"]
    for name, ok, why in sh["gates"]:
        assert ok, f"{name}: {why}"
    lines.append("all four gates pass on scan01")

    # And the refusal is real, not decorative. pose_lean has a bystander.
    lean = os.path.join(FRAME.DATA, "pose_lean")
    if os.path.isdir(lean):
        try:
            SHELL.build(lean, verbose=False)
            lines.append("WARNING: pose_lean did NOT refuse, and it has a "
                         "bystander in it")
        except RuntimeError as exc:
            lines.append(f"refuses a capture with a bystander: {exc}")
    return lines


def check_viewer():
    """Step 3, and the bug that made this script worth writing.

    viz.live only logs. Creating the recording stream is the caller's job, and
    main.py did not, so the run worked and showed nobody anything. What is
    asserted is that the entry point makes a stream and that something lands in
    it -- a file with bytes in it is the only proof that survives refactoring.
    """
    import subprocess
    import tempfile
    rrd = os.path.join(tempfile.gettempdir(), "camera_day_check.rrd")
    if os.path.exists(rrd):
        os.remove(rrd)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(
        [sys.executable, os.path.join(root, "main.py"),
         "--scan", SCAN_DIR, "--replay", os.path.join(FRAME.DATA, "bag01"),
         "--frames", "30", "--save", rrd],
        capture_output=True, text=True, timeout=1800)
    assert os.path.exists(rrd), \
        ("main.py wrote no recording. viz.live only logs; the entry point has "
         "to create the stream, and without it the whole run is invisible.")
    mb = os.path.getsize(rrd) / 1048576.0
    assert mb > 0.1, f"the recording is {mb:.2f}MB, which is empty"
    os.remove(rrd)
    tracked = [l for l in r.stdout.splitlines() if "frames tracked" in l]
    return [f"the operator view received {mb:.1f}MB",
            (tracked[0].strip() if tracked else "no tracking line printed")]


def main():
    print("\ncamera day, checked without a camera\n")
    failed = 0
    for title, fn in (("1. the one-command scan (camera stubbed)", check_record_path),
                      ("2. the reconstruction, and its refusal", check_reconstruction),
                      ("3. the operator view actually receives data", check_viewer)):
        print(f"  {title}")
        try:
            for line in fn():
                print(f"       {line}")
        except Exception as exc:                                # noqa: BLE001
            failed += 1
            print(f"       FAILED: {exc.__class__.__name__}: {exc}")
        print()

    print("  what this CANNOT check, and nothing can until the hardware is here:")
    for line in ("the RealSense call itself, and whether the mount is at 833mm",
                 "any arm: link_ok, torque caps, calibration, the physical E-stop",
                 "the advanced-mode depth sweep against the control flask"):
        print(f"       - {line}")
    print("\n" + ("OK" if not failed else f"{failed} FAILED"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
