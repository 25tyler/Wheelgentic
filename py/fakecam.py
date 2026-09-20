"""py/fakecam.py — a synthetic camera, so every path is runnable with no hardware.

    CAM=fake python py/vision.py
    CAM=fake python py/scrubbot.py --no-arm

WHY THIS EXISTS: the whole live path -- the mirror convention, the cartoon
mirroring a person, a recorded run -- was blocked on "needs a human at a
webcam". macOS also denies the camera SILENTLY (isOpened() returns False with
no exception), so the block is common and easy to hit at 3am. This renders a
moving person MediaPipe actually detects, through the real cv2.VideoCapture
interface, so vision.py cannot tell the difference.

It does NOT replace the real go/no-go: a rendered figure is not a human under
venue lighting. It DOES mean everything downstream of the camera can be
exercised, and it makes the mirror convention checkable by anyone, any time.

THE MIRROR CONVENTION is the reason this matters most. Getting it backwards
makes the robot scrub the WRONG ARM, and it is settled by waving one arm and
watching which label moves -- which needs a moving subject, not a still image.
"""
import math
import cv2
import numpy as np


def render(w=480, h=640, phase=0.0, wave_left=True, tag=True):
    """A synthetic person MediaPipe detects, with ONE arm waving.

    Flat vector art is NOT detected -- measured. MediaPipe is trained on
    photographs, so this needs soft edges, rounded joints, a background and
    sensor noise. `wave_left` waves the PERSON'S left arm (their left, which
    appears on the RIGHT of an unmirrored image, exactly like a real subject).
    """
    rng = np.random.default_rng(3)
    img = np.full((h, w, 3), 200, np.uint8)
    cv2.rectangle(img, (0, int(h * 0.75)), (w, h), (170, 170, 175), -1)
    cx = w // 2
    skin = (145, 170, 200)
    S = lambda p: (int(p[0]), int(p[1]))

    # A big, unmistakable wave on ONE side only.
    swing = math.sin(phase) * w * 0.16
    lift = abs(math.sin(phase)) * h * 0.13

    head = (cx, int(h * 0.13)); neck = (cx, int(h * 0.20))
    # PERSON'S left is image-RIGHT in an unmirrored frame (they face us).
    lsh = (cx + int(w * 0.13), int(h * 0.24))
    rsh = (cx - int(w * 0.13), int(h * 0.24))
    if wave_left:
        lel = (lsh[0] + int(w * 0.16) + swing, int(h * 0.40) - lift)
        lwr = (lsh[0] + int(w * 0.22) + swing * 1.6, int(h * 0.52) - lift * 1.8)
        rel = (rsh[0] - int(w * 0.16), int(h * 0.40))
        rwr = (rsh[0] - int(w * 0.19), int(h * 0.55))
    else:
        rel = (rsh[0] - int(w * 0.16) - swing, int(h * 0.40) - lift)
        rwr = (rsh[0] - int(w * 0.22) - swing * 1.6, int(h * 0.52) - lift * 1.8)
        lel = (lsh[0] + int(w * 0.16), int(h * 0.40))
        lwr = (lsh[0] + int(w * 0.19), int(h * 0.55))
    lhp = (cx + int(w * 0.09), int(h * 0.53))
    rhp = (cx - int(w * 0.09), int(h * 0.53))
    lkn = (cx + int(w * 0.10), int(h * 0.72)); lan = (cx + int(w * 0.10), int(h * 0.90))
    rkn = (cx - int(w * 0.10), int(h * 0.72)); ran = (cx - int(w * 0.10), int(h * 0.90))

    cv2.fillPoly(img, [np.array([lsh, rsh, rhp, lhp], np.int32)], (120, 100, 90))
    for a, b, t in [(lsh, lel, 26), (lel, lwr, 22), (rsh, rel, 26), (rel, rwr, 22),
                    (lhp, lkn, 30), (lkn, lan, 26), (rhp, rkn, 30), (rkn, ran, 26)]:
        cv2.line(img, S(a), S(b), skin, t)
        cv2.circle(img, S(b), t // 2, skin, -1)
    cv2.line(img, S(neck), S(head), skin, 22)
    cv2.circle(img, S(head), int(w * 0.075), skin, -1)

    # A FACE. Without it the figure is left-right symmetric and MediaPipe
    # cannot tell which way it faces -- measured: landmark 15 jumped from
    # x=102 to x=459 between adjacent frames as the model flip-flopped on the
    # subject's orientation, which makes the mirror convention untestable.
    # Eyes and a nose fix the facing, and MediaPipe's pose model uses face
    # landmarks (0-10) to disambiguate exactly this.
    hr = int(w * 0.075)
    eye_dy = int(hr * 0.25)
    for sgn in (-1, 1):
        ex = head[0] + sgn * int(hr * 0.38)
        cv2.circle(img, (ex, head[1] - eye_dy), max(2, hr // 7), (40, 40, 50), -1)
    cv2.circle(img, (head[0], head[1] + int(hr * 0.10)),
               max(2, hr // 9), (120, 140, 175), -1)          # nose
    cv2.ellipse(img, (head[0], head[1] + int(hr * 0.42)),
                (int(hr * 0.32), int(hr * 0.16)), 0, 0, 180, (90, 90, 110), 2)

    img = cv2.GaussianBlur(img, (7, 7), 0)
    noise = rng.normal(0, 6, img.shape).astype(np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    if tag:
        # Which side of the IMAGE is the waving arm on? The label is burned in
        # so a screenshot answers the mirror question by itself.
        side = "IMAGE-RIGHT" if wave_left else "IMAGE-LEFT"
        cv2.putText(img, f"waving arm: {side}", (8, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        cv2.putText(img, "(person's " + ("LEFT" if wave_left else "RIGHT") + ")",
                    (8, h - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    return img


class FakeCapture:
    """Quacks like cv2.VideoCapture. vision.py cannot tell the difference."""

    def __init__(self, fps=30.0, wave_left=True):
        self.n = 0
        self.fps = fps
        self.wave_left = wave_left
        self._open = True

    def isOpened(self):
        return self._open

    def set(self, *a):
        return True                      # accept the width/height/exposure sets

    def get(self, *a):
        return 0.0

    def read(self):
        """NOTE: this returns as fast as it can render.

        A real webcam BLOCKS at ~30fps and that is what throttles the whole
        vision loop. The synthetic one does not, so `CAM=fake` runs the loop
        at ~138fps and MediaPipe's native allocator falls behind: measured RSS
        growth of +5008MB over 30s unthrottled versus +533MB at a 0.033s
        throttle, and a long CAM=fake soak gets the process OOM-killed.

        That is a property of the fake camera, not of the demo -- Python-side
        allocation is near zero (tracemalloc) and vision alone is flat at
        254MB over 3455 frames. Do not "fix" a leak here that a real camera
        does not have; if you need a long unattended CAM=fake run, throttle
        the CALLER. A real-camera soak is still worth doing before demo day.
        """
        self.n += 1
        # ~1.4s per wave: fast enough to see, slow enough to track.
        phase = self.n * (2 * math.pi / (self.fps * 1.4))
        return True, render(phase=phase, wave_left=self.wave_left)

    def release(self):
        self._open = False
