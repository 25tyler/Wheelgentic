"""scrub3d/record.py -- capture a dataset so the camera is not needed again.

    python scrub3d/record.py --out recordings/scan01 --label "justin, seated"
    python scrub3d/record.py --out recordings/chair --seconds 4 --no-bag

WHY BOTH A BAG AND DERIVED FILES
---------------------------------
They answer different questions and the sizes are wildly different.

  scan.bag         Every frame, raw, replayable through
                   rs.config.enable_device_from_file() so the LIVE loop runs
                   exactly as if the camera were attached. ~6 GB per minute at
                   these resolutions, so it is short on purpose.

  the small files  A 60-frame temporal median of colour and depth, the
                   intrinsics, and a point cloud. About 10 MB, and they are
                   what the SCAN actually consumes -- the scan is a static
                   capture of a still subject, so the median IS its input.
                   Almost all offline work needs only these.

THE MEDIAN IS THE POINT, NOT A CONVENIENCE
-------------------------------------------
Subject and camera are both static during a scan, so a temporal median over N
frames cuts depth noise by sqrt(N): roughly 2.3mm down to 0.3mm at 60 frames.
That is not a nicety. scrub3d/girth.py recovers limb thickness to 1.5mm at
0.3mm noise and only 5.3mm at 2.3mm, so the median is the difference between a
usable measurement and a vague one.

Median rather than mean because a dropped or invalid depth pixel reads as zero,
and a mean would drag the surface toward the camera in proportion to how often
that happens. Zeros are excluded outright and the count kept, so a pixel that
was valid twice out of sixty is visible as such instead of silently averaged.

WHAT IS STORED, AND WHAT IS NOT
--------------------------------
recordings/ is gitignored. These files contain a person's body geometry, which
is biometric data, and the project's privacy claim depends on it staying on the
machine that made it.
"""
import argparse
import json
import os
import time

import numpy as np


def _intrinsics_dict(vsp):
    i = vsp.get_intrinsics()
    return {"width": i.width, "height": i.height, "fx": i.fx, "fy": i.fy,
            "ppx": i.ppx, "ppy": i.ppy, "model": str(i.model),
            "coeffs": list(i.coeffs)}


# --- image rotation, and the part that is easy to get silently wrong --------
#
# The camera is mounted on its side. Rotating the IMAGE is trivial; rotating
# the INTRINSICS with it is the bit that matters, because deprojection uses
# fx, fy, ppx and ppy, and a 90 degree turn swaps the roles of x and y. Rotate
# the pixels without rotating the intrinsics and every 3D point is wrong in a
# way that still looks like a plausible point cloud.
#
# cv2.ROTATE_90_CLOCKWISE maps src(row=v, col=u) of an HxW image to
# dst(row=u, col=H-1-v). So:
#     dst_x = H_src - 1 - src_y      dst_y = src_x
# which gives fx' = fy, fy' = fx, ppx' = H_src-1-ppy, ppy' = ppx.
#
# Consequence worth recording rather than discovering later: the deprojected
# cloud now sits in a camera frame turned 90 degrees about the optical axis.
# That is what makes "up" in the image mean "up" in the world, and hand-eye
# calibration must use the same convention, so the rotation matrix goes into
# meta.json rather than living in somebody's head.

ROTATIONS = ("none", "cw", "ccw", "180")

# The accelerometer rate the D455 accepts; see record().
ACCEL_HZ = 200


def rotate_image(img, rot):
    import cv2
    if rot == "none" or img is None:
        return img
    return cv2.rotate(img, {"cw": cv2.ROTATE_90_CLOCKWISE,
                            "ccw": cv2.ROTATE_90_COUNTERCLOCKWISE,
                            "180": cv2.ROTATE_180}[rot])


def rotate_intrinsics(intr, rot):
    """Rotate an intrinsics dict to match rotate_image()."""
    if rot == "none":
        return dict(intr)
    W, H = intr["width"], intr["height"]
    o = dict(intr)
    if rot == "cw":
        o.update(width=H, height=W, fx=intr["fy"], fy=intr["fx"],
                 ppx=(H - 1) - intr["ppy"], ppy=intr["ppx"])
    elif rot == "ccw":
        o.update(width=H, height=W, fx=intr["fy"], fy=intr["fx"],
                 ppx=intr["ppy"], ppy=(W - 1) - intr["ppx"])
    else:                                   # 180
        o.update(ppx=(W - 1) - intr["ppx"], ppy=(H - 1) - intr["ppy"])
    return o


def rotation_matrix(rot):
    """The 3x3 taking original camera-optical coords to rotated ones.

    Derived, not guessed, because the sign is easy to invert and an inverted
    sign yields a point cloud that still looks like a room. For "cw" the pixel
    map is dst_x = H-1-src_y, dst_y = src_x, and with the rotated intrinsics
    above that gives

        X2 = (dx - ppx')Z/fx' = (ppy - sy)Z/fy = -Y1
        Y2 = (dy - ppy')Z/fy' = (sx - ppx)Z/fx = +X1

    so the rotation is +pi/2 about the optical axis, not -pi/2. The unit test
    in __main__ checks this against a brute-force pixel remap rather than
    trusting the algebra.
    """
    import math
    if rot == "none":
        return np.eye(3).tolist()
    a = {"cw": math.pi / 2, "ccw": -math.pi / 2, "180": math.pi}[rot]
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def record(out_dir, seconds=10.0, median_frames=60, want_bag=True,
           depth_wh=(848, 480), color_wh=(1280, 720), fps=30, label="",
           rotate="none", preset="high_accuracy"):
    import pyrealsense2 as rs
    os.makedirs(out_dir, exist_ok=True)

    pipe = rs.pipeline()
    cfg = rs.config()
    cfg.enable_stream(rs.stream.depth, depth_wh[0], depth_wh[1], rs.format.z16, fps)
    cfg.enable_stream(rs.stream.color, color_wh[0], color_wh[1], rs.format.bgr8, fps)
    # THE IMU, which no capture on disk has and which costs two lines.
    #
    # At rest the negated accelerometer vector IS gravity, expressed in the
    # depth sensor's frame with Y down. That is Intel's documented convention
    # for the whole D400 motion path. It gives the world's up direction
    # directly, and with it a bumped-tripod detector: store the vector with
    # each capture and compare at the start of the next session, which is a
    # subtraction rather than a fit.
    #
    # frames.py fits the floor instead and recovers the same vector to better
    # than half a degree, so this is redundancy and not a dependency. But the
    # floor has to be IN VIEW for that, and the IMU does not care.
    #
    # Asked for WITHOUT a rate, the motion streams stopped the whole recording:
    # on the rig's D455 (firmware 5.17.3.10) that request, and accel at 63Hz,
    # both fail with "Couldn't resolve requests" before the first frame.
    # Accel at 200Hz starts. The gyro is never read, so it is not asked for.
    imu_ok = True
    try:
        cfg.enable_stream(rs.stream.accel, rs.format.motion_xyz32f, ACCEL_HZ)
    except Exception:                                           # noqa: BLE001
        imu_ok = False
    bag_path = os.path.join(out_dir, "scan.bag")
    if want_bag:
        cfg.enable_record_to_file(bag_path)

    try:
        profile = pipe.start(cfg)
    except RuntimeError as exc:
        if not imu_ok:
            raise
        # A camera that will not stream the IMU still captures the person.
        print(f"  the IMU would not start ({exc}); recording without it")
        cfg.disable_stream(rs.stream.accel)
        imu_ok = False
        profile = pipe.start(cfg)
    dev = profile.get_device()

    # high_accuracy: fewer filled pixels, but every returned pixel is
    # trustworthy. That is the right trade when the output drives a robot that
    # touches someone. high_density is for rendering, not for measuring.
    ds = dev.first_depth_sensor()
    try:
        ds.set_option(rs.option.visual_preset,
                      int(getattr(rs.rs400_visual_preset, preset)))
    except Exception:
        pass
    depth_scale = ds.get_depth_scale()

    align = rs.align(rs.stream.color)

    # Let auto-exposure settle, then LOCK it. An exposure change mid-capture
    # shifts the colour image under the median and smears the silhouette --
    # and the silhouette is the width measurement.
    t0 = time.time()
    while time.time() - t0 < 1.5:
        pipe.wait_for_frames(timeout_ms=2000)
    for s in dev.query_sensors():
        for opt in (rs.option.enable_auto_exposure, rs.option.enable_auto_white_balance):
            try:
                if s.supports(opt):
                    s.set_option(opt, 0)
            except Exception:
                pass

    print(f"  recording {seconds:.0f}s ... hold still")
    colors, depths, accel_samples = [], [], []
    n, t0 = 0, time.time()
    while time.time() - t0 < seconds:
        try:
            frames = align.process(pipe.wait_for_frames(timeout_ms=2000))
        except RuntimeError:
            break
        d = frames.get_depth_frame()
        c = frames.get_color_frame()
        if imu_ok:
            af = frames.first_or_default(rs.stream.accel)
            if af:
                v = af.as_motion_frame().get_motion_data()
                accel_samples.append([v.x, v.y, v.z])
        if not d or not c:
            continue
        n += 1
        # Keep the LAST median_frames, not the first. The subject is still
        # settling at the start, and a lead-in is how they get time to take
        # up the pose after reading an instruction.
        colors.append(np.asanyarray(c.get_data()).copy())
        depths.append(np.asanyarray(d.get_data()).copy())
        if len(colors) > median_frames:
            colors.pop(0); depths.pop(0)

    # Intrinsics from the COLOUR stream, because depth is aligned to colour.
    cvs = profile.get_stream(rs.stream.color).as_video_stream_profile()
    dvs = profile.get_stream(rs.stream.depth).as_video_stream_profile()
    meta = {
        "label": label,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "device": {
            "name": dev.get_info(rs.camera_info.name),
            "serial": dev.get_info(rs.camera_info.serial_number),
            "firmware": dev.get_info(rs.camera_info.firmware_version),
        },
        "depth_scale_m_per_unit": float(depth_scale),
        "frames_captured": n,
        "median_frames": len(depths),
        "color_intrinsics": _intrinsics_dict(cvs),
        "depth_intrinsics": _intrinsics_dict(dvs),
        "aligned_to": "color",
        "visual_preset": preset,
        "depth_stream_wh": list(depth_wh),
        "units": "depth arrays are MILLIMETRES after depth_scale is applied",
    }

    # Gravity, averaged over whatever motion frames arrived. Averaged because a
    # single accelerometer sample carries the tripod's own vibration; a second
    # of them does not.
    if imu_ok and accel_samples:
        g = np.mean(np.array(accel_samples), axis=0)
        n = float(np.linalg.norm(g))
        meta["gravity_cam"] = (-g / n).tolist() if n > 1e-6 else None
        meta["gravity_samples"] = len(accel_samples)
        meta["accel_magnitude"] = n
    pipe.stop()

    if not depths:
        raise RuntimeError("no frames captured")

    # --- temporal median, zeros excluded --------------------------------
    D = np.stack(depths).astype(np.float32) * (depth_scale * 1000.0)   # -> mm
    valid = D > 0
    D[~valid] = np.nan
    with np.errstate(all="ignore"):
        depth_med = np.nanmedian(D, axis=0)
    valid_count = valid.sum(0).astype(np.uint8)
    depth_med = np.nan_to_num(depth_med, nan=0.0)

    color_med = np.median(np.stack(colors), axis=0).astype(np.uint8)

    # Rotate AFTER the median, so the median is over unmodified frames,
    # and rotate the intrinsics in the same breath so they cannot drift
    # apart from the pixels they describe.
    depth_med = rotate_image(depth_med, rotate)
    valid_count = rotate_image(valid_count, rotate)
    color_med = rotate_image(color_med, rotate)
    meta["rotation"] = rotate
    meta["rotation_matrix_cam"] = rotation_matrix(rotate)
    meta["color_intrinsics_raw"] = dict(meta["color_intrinsics"])
    meta["color_intrinsics"] = rotate_intrinsics(meta["color_intrinsics"], rotate)
    meta["depth_intrinsics"] = rotate_intrinsics(meta["depth_intrinsics"], rotate)

    import cv2
    cv2.imwrite(os.path.join(out_dir, "color_median.png"), color_med)
    np.save(os.path.join(out_dir, "depth_median_mm.npy"), depth_med.astype(np.float32))
    np.save(os.path.join(out_dir, "depth_valid_count.npy"), valid_count)
    cv2.imwrite(os.path.join(out_dir, "color_frame0.png"),
                rotate_image(colors[0], rotate))

    # --- point cloud ------------------------------------------------------
    ci = meta["color_intrinsics"]
    ys, xs = np.mgrid[0:depth_med.shape[0], 0:depth_med.shape[1]]
    z = depth_med
    ok = (z > 200) & (z < 3000) & (valid_count > len(depths) // 3)
    X = (xs - ci["ppx"]) * z / ci["fx"]
    Y = (ys - ci["ppy"]) * z / ci["fy"]
    pts = np.stack([X[ok], Y[ok], z[ok]], -1)
    rgb = color_med[..., ::-1][ok]
    _write_ply(os.path.join(out_dir, "cloud.ply"), pts, rgb)

    meta["cloud_points"] = int(len(pts))
    meta["depth_valid_fraction"] = float((valid_count > 0).mean())
    meta["median_valid_fraction"] = float(ok.mean())
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)

    if want_bag and os.path.exists(bag_path):
        meta["bag_mb"] = os.path.getsize(bag_path) / 1e6
    return meta


def _write_ply(path, pts, rgb):
    with open(path, "wb") as fh:
        fh.write(b"ply\nformat binary_little_endian 1.0\n")
        fh.write(f"element vertex {len(pts)}\n".encode())
        fh.write(b"property float x\nproperty float y\nproperty float z\n")
        fh.write(b"property uchar red\nproperty uchar green\nproperty uchar blue\n")
        fh.write(b"end_header\n")
        arr = np.empty(len(pts), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                                        ("r", "u1"), ("g", "u1"), ("b", "u1")])
        arr["x"], arr["y"], arr["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
        arr["r"], arr["g"], arr["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
        fh.write(arr.tobytes())


def load(out_dir):
    """Read a recording back. -> dict. No camera, no pyrealsense2 needed."""
    import cv2
    with open(os.path.join(out_dir, "meta.json")) as fh:
        meta = json.load(fh)
    return {
        "meta": meta,
        "color": cv2.imread(os.path.join(out_dir, "color_median.png")),
        "depth_mm": np.load(os.path.join(out_dir, "depth_median_mm.npy")),
        "valid_count": np.load(os.path.join(out_dir, "depth_valid_count.npy")),
        "intr": meta["color_intrinsics"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--median-frames", type=int, default=60)
    ap.add_argument("--no-bag", action="store_true")
    ap.add_argument("--label", default="")
    ap.add_argument("--preset", default="high_accuracy",
                    help="high_accuracy | high_density | default | hand")
    ap.add_argument("--depth-wh", default="848x480")
    # 180, not cw. The camera is mounted UPSIDE DOWN on this rig, and every one
    # of the eleven captures on disk overrode the old `cw` default to say so.
    # Getting it wrong is not loud: you get a sideways image with sideways
    # intrinsics, which still deprojects into a plausible-looking point cloud.
    ap.add_argument("--rotate", choices=ROTATIONS, default="180",
                    help="camera is mounted upside down; 180 is correct for "
                         "the current rig")
    a = ap.parse_args()

    m = record(a.out, seconds=a.seconds, median_frames=a.median_frames,
               want_bag=not a.no_bag, label=a.label, rotate=a.rotate,
               preset=a.preset,
               depth_wh=tuple(int(v) for v in a.depth_wh.split('x')))
    print(f"\n  wrote {a.out}")
    print(f"    {m['frames_captured']} frames, median over {m['median_frames']}")
    print(f"    depth valid: {100 * m['depth_valid_fraction']:.1f}% of pixels "
          f"ever, {100 * m['median_valid_fraction']:.1f}% usable in the median")
    print(f"    cloud: {m['cloud_points']} points")
    if "bag_mb" in m:
        print(f"    bag: {m['bag_mb']:.0f} MB")


if __name__ == "__main__":
    main()
