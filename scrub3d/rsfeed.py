"""scrub3d/rsfeed.py -- frames, from the camera or from a recording.

One iterator, two sources, identical output. That is the whole point: every
line downstream of this runs the same whether a D455 is plugged in or a 1.6GB
bag is replaying, so the live path can be built and debugged with no hardware
and no volunteer.

    for f in Feed("data/bag01").frames():     # replay
    for f in Feed().frames():                 # the camera

Each frame is a dict: `color` BGR uint8, `depth_mm` float32 with 0 meaning NO
DATA, `intr` the colour intrinsics, `t` the camera's own timestamp in seconds.

WHY THE TIMESTAMP COMES FROM THE FRAME
---------------------------------------
Not from time.time(). A replayed bag runs at whatever speed the host manages,
and wall-clock dt would make a recorded motion look faster or slower than it
was. The camera stamps each frame; use that, and a replay reproduces the real
motion exactly. It also gives a camera-wedged detector for free -- a timestamp
that stops advancing while wall clock does not is a stalled sensor, which is
better than the frozen-pose heuristic it replaces.

THE ROTATION IS NOT OPTIONAL
-----------------------------
This rig's camera is mounted upside down. record.py rotates the pixels AND the
intrinsics together, and so does this: rotating one without the other leaves
fx, ppx and ppy describing an orientation the pixels no longer have, and every
deprojected point is then wrong while still looking like a plausible cloud.
"""
import os
import threading
import time

import numpy as np

try:
    from . import record
except ImportError:
    import record

# A request to stop, from an entry point's Ctrl-C or console-close handler.
# Every feed ends at its next frame, so the loop reading it returns normally
# and still reports what it did, instead of dying mid-frame. feeding() says
# whether any feed is being read right now, which is how a handler knows the
# request will be seen; when none is, the handler interrupts instead.
STOP = threading.Event()
_FEEDING = [0]
_FEEDING_LOCK = threading.Lock()


def _feeding(delta):
    with _FEEDING_LOCK:
        _FEEDING[0] += delta


def feeding():
    with _FEEDING_LOCK:
        return _FEEDING[0] > 0


class Feed:
    """RealSense frames from a bag directory, a .bag file, or the camera."""

    def __init__(self, source=None, rotate="180", depth_wh=(848, 480),
                 color_wh=(1280, 720), fps=30, preset="high_density",
                 repeat=True):
        self.source = source
        self.rotate = rotate
        self.depth_wh, self.color_wh, self.fps = depth_wh, color_wh, fps
        self.preset, self.repeat = preset, repeat
        self._pipe = None
        self.meta = {}

    def _bag_path(self):
        """The recording to replay, or None for the camera.

        Only `source=None` means the camera. A capture directory with no
        `scan.bag` in it used to mean the camera too, so pointing a replay at
        the wrong capture opened a live stream without a word, or with no
        camera attached failed with a device error that named no file. Now
        start() names the recording that is missing.
        """
        if self.source is None:
            return None
        if os.path.isdir(self.source):
            return os.path.join(self.source, "scan.bag")
        return self.source

    def start(self):
        import pyrealsense2 as rs
        self._rs = rs
        cfg = rs.config()
        bag = self._bag_path()
        if bag:
            if not os.path.exists(bag):
                raise FileNotFoundError(
                    f"{bag} not found. Only bag01 carries a raw recording; the "
                    f"other captures keep the median frames only.")
            cfg.enable_device_from_file(bag, repeat_playback=self.repeat)
        else:
            cfg.enable_stream(rs.stream.depth, *self.depth_wh, rs.format.z16,
                              self.fps)
            cfg.enable_stream(rs.stream.color, *self.color_wh, rs.format.bgr8,
                              self.fps)
        self._pipe = rs.pipeline()
        profile = self._pipe.start(cfg)

        if bag:
            # Replay as fast as the consumer can take frames rather than in
            # real time. A tracking loop that is slower than 30Hz would
            # otherwise silently drop frames and look better than it is.
            profile.get_device().as_playback().set_real_time(False)
        else:
            ds = profile.get_device().first_depth_sensor()
            try:
                ds.set_option(rs.option.visual_preset,
                              int(getattr(rs.rs400_visual_preset, self.preset)))
            except Exception:                                   # noqa: BLE001
                pass

        self._align = rs.align(rs.stream.color)
        self._scale = profile.get_device().first_depth_sensor().get_depth_scale()
        cvs = profile.get_stream(rs.stream.color).as_video_stream_profile()
        self.meta = {
            "source": bag or "camera",
            "color_intrinsics": record.rotate_intrinsics(
                record._intrinsics_dict(cvs), self.rotate),
            "rotation": self.rotate,
        }
        return self

    def frames(self, limit=None, timeout_ms=200):
        """Yield aligned frames. Stops at the end of a bag, or after `limit`."""
        if self._pipe is None:
            self.start()
        n, last_t = 0, None
        _feeding(+1)
        try:
            while limit is None or n < limit:
                if STOP.is_set():
                    break
                try:
                    # NEVER the default 5000ms. A USB drop would otherwise
                    # stall the loop for five seconds while an arm holds
                    # contact.
                    fs = self._pipe.wait_for_frames(timeout_ms=timeout_ms)
                except RuntimeError:
                    break
                fs = self._align.process(fs)
                d, c = fs.get_depth_frame(), fs.get_color_frame()
                if not d or not c:
                    continue

                # DROP REPEATS. Playback re-emits its first frame while it
                # primes: measured, the first 11 frames off bag01 carry one
                # identical timestamp. A tracking loop would process the same
                # image eleven times and read it as a person holding perfectly
                # still, which is the most convincing possible way to look
                # like it is working.
                t = float(d.get_timestamp()) / 1000.0
                if last_t is not None and t <= last_t:
                    continue
                last_t = t

                depth = np.asanyarray(d.get_data()).astype(np.float32) * (
                    self._scale * 1000.0)
                color = np.asanyarray(c.get_data())
                yield {
                    "color": record.rotate_image(color, self.rotate),
                    "depth_mm": record.rotate_image(depth, self.rotate),
                    "intr": self.meta["color_intrinsics"],
                    "t": t,
                    "n": n,
                }
                n += 1
        finally:
            _feeding(-1)

    def stop(self):
        if self._pipe is not None:
            try:
                self._pipe.stop()
            except Exception:                                   # noqa: BLE001
                pass
            self._pipe = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()


if __name__ == "__main__":
    import argparse

    try:
        from . import frames as FRAME
    except ImportError:
        import frames as FRAME

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=os.path.join(FRAME.DATA, "bag01"))
    ap.add_argument("--limit", type=int, default=120)
    a = ap.parse_args()

    print(f"replaying {a.source}")
    t0 = time.time()
    got, stamps, valid = 0, [], []
    with Feed(a.source, repeat=False) as feed:
        for f in feed.frames(limit=a.limit):
            got += 1
            stamps.append(f["t"])
            valid.append(float((f["depth_mm"] > 0).mean()))
    wall = time.time() - t0

    print(f"  {got} frames in {wall:.2f}s  ({got / max(wall, 1e-6):.1f} fps "
          f"of pure decode)")
    assert got >= 30, f"only {got} frames came back; the bag did not replay"

    dt = np.diff(stamps)
    print(f"  camera timestamps advance {np.median(dt) * 1000:.1f}ms per frame "
          f"({1.0 / max(np.median(dt), 1e-9):.1f} fps as recorded)")
    assert np.all(dt > 0), "camera timestamps went backwards"

    print(f"  depth valid fraction {np.mean(valid):.3f} "
          f"(min {np.min(valid):.3f}, max {np.max(valid):.3f})")

    # Same shape and same intrinsics as the still captures, or nothing that
    # works on those will work here.
    print(f"  colour intrinsics fx {feed.meta['color_intrinsics']['fx']:.1f} "
          f"ppx {feed.meta['color_intrinsics']['ppx']:.1f} "
          f"rotation {feed.meta['rotation']}")

    # A capture with no recording in it is a mistake to report, not a request
    # for the live camera.
    no_bag = os.path.join(FRAME.DATA, "scan01")
    assert not os.path.exists(os.path.join(no_bag, "scan.bag")), \
        "scan01 has a recording now; pick another capture for this check"
    probe = Feed(no_bag, repeat=False)
    try:
        probe.start()
    except FileNotFoundError as exc:
        print(f"  a capture with no recording -> refused: {exc}")
    else:
        probe.stop()
        raise AssertionError(f"{no_bag} has no scan.bag and a live stream "
                             f"started in its place")
    print("\nOK")
