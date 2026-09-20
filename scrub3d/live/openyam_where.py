"""scrub3d/live/openyam_where.py -- is the rig where the software thinks it is?

Photograph the rig and draw on it where the software believes the arms are.

Reads the camera the way the live view does, waits for the floor and the seat,
places the rig from live_rig_openyam.json exactly as the live view would, asks
the bridge where each claw tip is (read only, nothing moves), and projects all
of it back into the picture:

    square  = where the model puts an arm's BASE
    circle  = where the model puts that arm's CLAW TIP (from dimOS's joints)
    cross   = the seat point; short lines from it = world +x (red), +y (green)

If the marks sit on the real things, the geometry is right. If they do not,
the gap between a mark and the real thing is the error, in the picture.

    python scrub3d/live/openyam_where.py [name]

The live view must NOT be running: one program at a time can have the camera.

The bases in live_rig_openyam.json are placed from the CAMERA, so they are right
for anybody in the chair, and wrong as soon as the camera or the chair is moved.
This is the check for that: squares that float off the base blocks mean it is
time to measure the bases again (x_from_camera_mm / y_from_camera_mm).
"""
import json
import os
import socket
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SCRUB3D_ARM"] = "openyam"
sys.path.insert(0, os.path.join(REPO, "scrub3d", "live"))
sys.path.insert(0, os.path.join(REPO, "scrub3d"))
name = sys.argv[1] if len(sys.argv) > 1 else "where"
sys.argv = sys.argv[:1]

import live_body as LB  # noqa: E402

RIG = os.path.join(REPO, "scrub3d", "live", "live_rig_openyam.json")
OUT = os.path.join(os.environ["TEMP"], "cam")
os.makedirs(OUT, exist_ok=True)
SIDE_OFFSET_M = {"left": np.array([0.0, 0.31, 0.0]), "right": np.array([0.0, -0.31, 0.0])}


def bridge_state():
    host, _, port = os.environ.get("SCRUB3D_DIMOS", "10.189.59.208:7790").rpartition(":")
    s = socket.create_connection((host, int(port)), timeout=5)
    f = s.makefile("rwb", buffering=0)
    f.write(b'{"op":"state"}\n')
    st = json.loads(f.readline())
    f.write(b'{"op":"bye"}\n')
    s.close()
    return st


intr, frames = LB.camera_source(upside_down=True)
live = LB.Live(intr, None)
rel = json.load(open(RIG))["arms"]
seat = None
color = depth = None
for i, (color, depth, t) in enumerate(frames):
    ev = live.step(color, depth, t)
    if "seat" in ev:
        seat = live.seat
    if seat is not None and i > 60:
        break
    if i > 400:
        break
frames.close()
if seat is None or live.T_wc is None:
    # Nobody in the chair: borrow the frame of reference from the last shot that
    # had somebody in it. Only right while the camera has not been moved.
    prev = os.path.join(OUT, "where_last_meta.json")
    if not os.path.exists(prev):
        cv2.imwrite(os.path.join(OUT, name + "_raw.jpg"), color)
        raise SystemExit("no seat found and no earlier shot to borrow from")
    m = json.load(open(prev))
    print("nobody in the chair: using the camera pose from the last shot that had somebody in it")
    live.T_wc = np.array(m["T_wc"], float)
    seat = {"xy": m["seat"], "z": 410.0}

T_wc = live.T_wc
T_cw = np.linalg.inv(T_wc)
sx, sy = (float(v) for v in seat["xy"])
print("camera in world:", [round(float(v)) for v in T_wc[:3, 3]])
print("seat point in world:", round(sx), round(sy), " seat height", round(float(seat.get("z", 0))))


def to_px(w):
    c = T_cw[:3, :3] @ np.asarray(w, float) + T_cw[:3, 3]
    if c[2] <= 1:
        return None
    return (int(round(intr["fx"] * c[0] / c[2] + intr["ppx"])),
            int(round(intr["fy"] * c[1] / c[2] + intr["ppy"])))


img = color.copy()
st = bridge_state()
cols = {"left": (0, 0, 255), "right": (255, 128, 0)}
for r in rel:
    side = r["side"]
    T = None
    if T is None:
        import arms_live as AL
        try:
            T = AL.base_pose(r, sx, sy, [float(T_wc[0, 3]), float(T_wc[1, 3])])
        except TypeError:      # the rig is seat-relative again (camera placement was reverted)
            T = AL.base_pose(r, sx, sy)
    base_w = T[:3, 3]
    arm = st["arms"][side]
    p_arm = (np.asarray(arm["p"], float) - SIDE_OFFSET_M[side]) * 1000.0
    tip_w = T[:3, :3] @ p_arm + T[:3, 3]
    print(f"{side}: base in world {[round(float(v)) for v in base_w]}, "
          f"claw tip in world {[round(float(v)) for v in tip_w]}, "
          f"joints {[round(v, 2) for v in arm['joints']]}")
    b, tp = to_px(base_w), to_px(tip_w)
    c = cols[side]
    if b:
        cv2.rectangle(img, (b[0] - 9, b[1] - 9), (b[0] + 9, b[1] + 9), c, 3)
        cv2.putText(img, f"{side} base", (b[0] + 12, b[1] + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, 2)
    if tp:
        cv2.circle(img, tp, 11, c, 3)
        cv2.putText(img, f"{side} tip", (tp[0] + 14, tp[1] + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, 2)
    if b and tp:
        cv2.line(img, b, tp, c, 1)
    # the depth the camera really sees at those two pixels, against the model's
    for label, px_, w in (("base", b, base_w), ("tip", tp, tip_w)):
        if px_ and 0 <= px_[1] < depth.shape[0] and 0 <= px_[0] < depth.shape[1]:
            patch = depth[max(0, px_[1] - 4):px_[1] + 5, max(0, px_[0] - 4):px_[0] + 5]
            seen = float(np.median(patch[patch > 0])) if (patch > 0).any() else float("nan")
            model = float((T_cw[:3, :3] @ w + T_cw[:3, 3])[2])
            print(f"   {side} {label}: pixel {px_}, model says {model:.0f} mm from the camera, "
                  f"the camera sees {seen:.0f} mm there")

o = to_px([sx, sy, float(seat.get("z", 450.0))])
if o:
    cv2.drawMarker(img, o, (255, 255, 255), cv2.MARKER_CROSS, 22, 2)
    for axis, c in (([150, 0, 0], (0, 0, 255)), ([0, 150, 0], (0, 255, 0))):
        e = to_px([sx + axis[0], sy + axis[1], float(seat.get("z", 450.0))])
        if e:
            cv2.line(img, o, e, c, 3)
p = os.path.join(OUT, name + ".jpg")
cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, 90])
np.save(os.path.join(OUT, name + "_depth.npy"), depth)
for meta in (name + "_meta.json", "where_last_meta.json"):
    json.dump({"intr": intr, "T_wc": T_wc.tolist(), "seat": [sx, sy]},
              open(os.path.join(OUT, meta), "w"))
print(p)
if hasattr(os, "startfile"):
    os.startfile(p)                                  # Windows: show it
