#!/usr/bin/env python3
"""tools/tune_dirt.py — live UV threshold tuning. Run it UNDER THE LAMP.

    python tools/tune_dirt.py

Trackbars for V_MIN / S_MIN / hue band / min area. Shows the camera, the mask,
and the blob count live. When the mask lights up ONLY on the tracer and not on
skin, write the numbers into config.json and FREEZE them.

Twenty minutes with this beats four hours of guessing at 3am. And build the
cardboard shroud first -- Glo Germ's own manual says a darkened scene, and a
projector spills white light all over yours.

DO NOT point 365nm at anyone's face. UV-A, photokeratitis. Tape it pointing
down.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py"))
import cv2, numpy as np
import dirt as D

def nothing(_): pass

# CAM=fake renders a synthetic subject with tracer blobs, so the UV threshold
# UI can be learned and its layout checked with no camera and no lamp. The
# REAL thresholds must still be set under the real 365nm light -- a rendered
# blob is not fluorescence -- but nobody has to discover the controls at 3am.
if os.environ.get("CAM", "").lower() == "fake":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "py"))
    from fakecam import FakeCapture, render as _render
    import numpy as _np

    class _TracerCam(FakeCapture):
        """The synthetic subject with fake 'tracer' spots on the forearm."""
        def read(self):
            ok, img = super().read()
            if not ok:
                return ok, img
            h, w = img.shape[:2]
            # three bright green-cyan blobs where a forearm would be
            for fx, fy in ((0.72, 0.42), (0.76, 0.48), (0.80, 0.54)):
                cv2.circle(img, (int(w * fx), int(h * fy)), 11,
                           (90, 255, 205), -1)
            cv2.putText(img, "CAM=fake -- NOT real fluorescence",
                        (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (0, 0, 255), 1)
            return True, img

    cap = _TracerCam()
    print("[tune] CAM=fake -- synthetic tracer, NOT a real lamp. "
          "Set the real thresholds under 365nm.")
else:
    cap = cv2.VideoCapture(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
if not cap.isOpened():
    sys.exit("Camera blocked -- System Settings > Privacy & Security > Camera")

cv2.namedWindow("tune", cv2.WINDOW_NORMAL)
cv2.createTrackbar("V_MIN",    "tune", D.V_MIN, 255, nothing)
cv2.createTrackbar("S_MIN",    "tune", D.S_MIN, 255, nothing)
cv2.createTrackbar("skin_hue_max", "tune", D.SKIN_HUE_MAX, 90, nothing)
cv2.createTrackbar("skin_hue_min", "tune", D.SKIN_HUE_MIN, 179, nothing)
cv2.createTrackbar("min_area", "tune", D.MIN_AREA, 2000, nothing)

print(__doc__)
print("keys: q=quit  p=print current values  SPACE=freeze frame\n")
frozen = None
while True:
    if frozen is None:
        ok, frame = cap.read()
        if not ok: continue
    else:
        frame = frozen.copy()

    v = cv2.getTrackbarPos("V_MIN", "tune")
    s = cv2.getTrackbarPos("S_MIN", "tune")
    hlo = cv2.getTrackbarPos("skin_hue_max", "tune")
    hhi = cv2.getTrackbarPos("skin_hue_min", "tune")
    ma = max(1, cv2.getTrackbarPos("min_area", "tune"))

    mask = D.fluor_mask(frame, v, s, hlo, hhi)
    bl = D.blobs(mask, ma)

    vis = frame.copy()
    for b in bl:
        cv2.circle(vis, (int(b["cx"]), int(b["cy"])), 14, (0, 255, 255), 2)
        cv2.putText(vis, str(b["area"]), (int(b["cx"])+16, int(b["cy"])),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
    cv2.putText(vis, f"blobs={len(bl)}  V>{v} S>{s} hue({hlo},{hhi})",
                (10, 26), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
    cv2.putText(vis, "mask should light ONLY on tracer, never on skin",
                (10, 52), cv2.FONT_HERSHEY_SIMPLEX, .5, (200, 200, 200), 1)

    # sample the hue/sat/val under the cursor-free centre for reference
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[h//2, w//2]
    cv2.putText(vis, f"centre px H={hsv[0]} S={hsv[1]} V={hsv[2]}",
                (10, h-14), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)

    cv2.imshow("tune", np.hstack([vis, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)]))
    k = cv2.waitKey(1) & 0xFF
    if k == ord('q'): break
    if k == ord(' '): frozen = None if frozen is not None else frame.copy()
    if k == ord('p'):
        print(f'  "dirt_v_min": {v}, "dirt_s_min": {s}, '
              f'"dirt_skin_hue_max": {hlo}, "dirt_skin_hue_min": {hhi}, '
              f'"dirt_min_area": {ma}')

cap.release(); cv2.destroyAllWindows()
