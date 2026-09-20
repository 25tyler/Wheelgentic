"""py/replaycam.py -- a recorded capture session, as a VideoCapture.

WHY THIS EXISTS
---------------
The projector can run on a laptop webcam (a real person, no depth) or on
`CAM=fake` (a synthetic figure, no person and no depth). Neither exercises
the thing the live rig actually does: a REAL person recorded by the REAL
D455, with a real distance behind every pixel.

The capture sessions on the arm computer are exactly that. This plays one
back through the same `cv2.VideoCapture` surface `vision.PoseFeed` already
uses, so the whole pipeline downstream -- pose, the mirror convention, the
websocket, the cartoon, the governor -- runs on measured frames with no
camera attached to this machine.

It is NOT a substitute for the go/no-go on real hardware. A recording cannot
show a USB drop, a camera that wedges, or a person under different lighting.
It shows that the arithmetic and the wiring are right.

THE DEPTH RIDES ALONG
---------------------
`read()` returns colour, because that is what VideoCapture promises and what
MediaPipe consumes. The matching depth frame is left on `self.depth_mm` for
`vision.PoseFeed` to pick up in the same tick. Kept as an attribute rather
than returned, because changing read()'s signature would change the contract
every caller depends on -- including the real camera path, which has no
depth to give.

A RECORDING IS A FOLDER of <name>_c.png / <name>_d.png pairs plus intr.json,
which is the format scrub3d/live/live_body.py already writes and reads. The
depth PNGs are uint16 millimetres, and 0 means NO DATA -- never zero
distance.

PACING
------
Frames are handed out at the recording's own rate (default 30fps, the D455's
rate), not as fast as the disk allows. A replay that runs at 300fps would
make every downstream timing look wrong: the one-euro filter would see
motion a hundred times faster than it was, and the scrub choreography would
finish before the arm arrived.
"""
import glob
import json
import os
import time

import cv2
import numpy as np


class ReplayCapture:
    """A recorded session, quacking like cv2.VideoCapture."""

    def __init__(self, folder, fps=30.0, loop=True):
        self.folder = folder
        self.loop = loop
        self.period = 1.0 / float(fps)
        self._next_t = None

        self.names = sorted(
            os.path.basename(p)[:-6]
            for p in glob.glob(os.path.join(folder, "*_d.png")))
        # Only keep frames that have BOTH halves. A colour frame without its
        # depth is not a frame this exists to serve, and silently skipping
        # one mid-run is better than a None crash forty seconds into a demo.
        self.names = [n for n in self.names
                      if os.path.exists(os.path.join(folder, n + "_c.png"))]
        if not self.names:
            raise RuntimeError(
                f"no <name>_c.png / <name>_d.png pairs in {folder}")

        intr_path = os.path.join(folder, "intr.json")
        # A DICT, deliberately: scrub3d/frames.py indexes intrinsics rather
        # than reading attributes, and py/bodydepth.py accepts either.
        self.intr = json.load(open(intr_path)) if os.path.exists(intr_path) else None

        self.i = 0
        self.depth_mm = None            # the matching depth, for the caller
        self.frames_served = 0

    # --- the VideoCapture surface vision.PoseFeed uses ----------------------
    def isOpened(self):
        return bool(self.names)

    def set(self, *_a, **_kw):
        # Width, height, exposure and white balance are properties of a
        # recording that already happened. Accepting and ignoring keeps the
        # caller's setup code identical for both sources.
        return False

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_WIDTH and self.intr:
            return float(self.intr.get("width", 1280))
        if prop == cv2.CAP_PROP_FRAME_HEIGHT and self.intr:
            return float(self.intr.get("height", 720))
        return 0.0

    def read(self):
        """-> (ok, colour_bgr). The depth lands on self.depth_mm."""
        if not self.names:
            return False, None
        if self.i >= len(self.names):
            if not self.loop:
                return False, None
            self.i = 0

        # PACE IT. Sleep only the remainder, so a slow consumer is never
        # penalised twice: if pose detection took longer than a frame, the
        # next one is due immediately rather than a frame late forever.
        now = time.time()
        if self._next_t is None:
            self._next_t = now
        if now < self._next_t:
            time.sleep(self._next_t - now)
        self._next_t += self.period

        nm = self.names[self.i]
        self.i += 1
        color = cv2.imread(os.path.join(self.folder, nm + "_c.png"))
        depth = cv2.imread(os.path.join(self.folder, nm + "_d.png"),
                           cv2.IMREAD_UNCHANGED)
        if color is None or depth is None:
            return False, None
        self.depth_mm = depth.astype(np.float32)
        self.frames_served += 1
        return True, color

    def release(self):
        self.names = []


def find_recording(hint=None):
    """A recording folder to replay, or None.

    `hint` wins. Otherwise the first folder under scrub3d/data/ that has the
    pairs, which is where live_body.py writes them.
    """
    if hint:
        return hint if os.path.isdir(hint) else None
    root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scrub3d", "data")
    if not os.path.isdir(root):
        return None
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.isdir(d) and glob.glob(os.path.join(d, "*_d.png")):
            return d
    return None
