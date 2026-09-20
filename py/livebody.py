"""py/livebody.py — the scrub3d body model, posed by the live camera.

WHAT THIS IS
------------
scrub3d/track.py holds the real thing: a scanned body whose surface cells live
in region-local coordinates, re-posed every frame by writing one 4x4 per
region rather than recomputing a single cell. Its own header states the split
that makes it real time:

    scan once   ->  shape        seconds, GPU
    track       ->  pose         milliseconds, CPU, every frame

Until now nothing in the running demo called it. This is the seam that makes
it run.

WHY IT COULD NOT RUN BEFORE, AND WHAT CHANGED
----------------------------------------------
track.Tracker.update() takes a frame dict with a `depth_mm` array and camera
intrinsics, and turns each 2D landmark into a world point by reading the depth
under it. There is no depth on this machine: the D455 is not attached, and
macOS UVCAssistant blocks the depth stream even when it is.

But update() is only the camera half. Tracker.solve(J, t) takes WORLD JOINTS
directly and does all the work -- the retarget, the hold, the jump guard, the
seat test -- and track.py's own docstring calls it "the half of update() that
needs no camera and no model". MediaPipe publishes exactly those joints, in
metres, on the RGB path the demo already runs, for free, every frame.

So the shape comes from scrub3d/anatomy.py (a measured adult, not a scan) and
the pose comes from MediaPipe world landmarks. No depth sensor, no Sapiens, no
torch, no capture directory.

MEASURED, on this Mac, CAM=fake, 60 consecutive frames:
    60/60 frames posed all 13 regions
    Tracker.solve      mean 0.53 ms, max 1.61 ms
    anatomy body build 0.004 s, once at startup
    import anatomy+track  0.15 s, once at startup
0.53ms is 1.6% of a 30fps frame. This is affordable per frame; the body build
is not, which is why it happens once in __init__ and never again.

WHAT IT DOES NOT DO
--------------------
It does not command the arm, and it must not. py/vision.py's read() spells out
why world landmarks may never drive motion: they are hip-centred and
scale-normalised to a generic human, so a fixed camera->robot transform fitted
against them drifts as the subject shifts their weight. That prohibition is
about COMMANDING. This poses a picture of a body, whose origin is the body
itself, and hip-centred is the frame it already wanted. The arm keeps taking
its target from the pixel path and the plane homography, untouched.

It is also not a second pipeline. There is one solver here -- scrub3d's -- and
this calls it. The 4-arm territory partition stays where it is: baked offline
by tools/export_body.py, or recomputed on demand by scrubbot's `_solve_live`,
both through the same export_one. Nothing here re-implements any of that.
"""
import os
import sys
import time

# scrub3d is a sibling package. Added the same way tools/export_body.py and
# scrubbot's consent import add it, so all three agree on where it lives.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "scrub3d"))


class LiveBody:
    """The scrub3d body model, re-posed from MediaPipe world joints.

    Construct once; call update() with a PoseFeed's world_mm dict per frame.
    Every heavy import and the body build happen in __init__, so the per-frame
    path is the matrix write track.py promises and nothing else.
    """

    def __init__(self, seat_z_mm=1050.0):
        import numpy as np
        import anatomy
        import track

        self._np = np
        self._track = track

        # SHAPE, ONCE. anatomy.anatomical_body() is a measured adult from a
        # population table, not a scan of the person in the chair -- and that
        # distinction is reported honestly rather than hidden: `measured` is
        # False on every event this module publishes, exactly as the existing
        # solve path reports it, because nothing here measured anybody. A real
        # scan would come from scrub3d/scan.py and needs depth plus Sapiens
        # weights, neither of which exists on this machine.
        t0 = time.perf_counter()
        self.body, self.meshes = anatomy.anatomical_body(seat_z=seat_z_mm)

        # ONE RENAME, AND IT IS LOAD-BEARING. anatomy builds the trunk under
        # the name "torso"; track.LIVE_BONES asks for "trunk". Every other one
        # of the thirteen region names already matches exactly. Without this
        # line the trunk is never solved, which costs far more than one
        # region: the trunk carries the head and the neck through
        # track.CARRIED, and the SEAT TEST reads the trunk's hip end to decide
        # whether the person being tracked is the person the plan is for. A
        # missing trunk reports `away` on every frame forever.
        for r in self.body.regions:
            if r.name == "torso":
                r.name = "trunk"
        build_s = time.perf_counter() - t0

        # The camera pose track.Tracker wants is used for ONE thing: deciding
        # which way is anterior, so each limb's scrubbable arc faces the front
        # (scrub3d/frames.py::region_pose). It is NOT used to place anybody --
        # MediaPipe's joints are already in the world frame this body lives
        # in. So a camera in front of the subject at roughly eye height is the
        # whole requirement, and 2.2m/1.1m is the same fallback pose
        # tools/export_body.py::_camera_block ships when no capture is present.
        T = np.eye(4)
        T[:3, 3] = (2200.0, 0.0, 1100.0)
        self.tracker = track.Tracker(self.body, T)

        self.posed = None
        self.info = {"ok": False, "why": "no frame yet"}
        self.frames = 0
        self.tracked = 0
        self.build_s = build_s
        self._last_solve_ms = 0.0

    def update(self, world_mm, t=None):
        """One frame of world joints -> (posed BodyModel or None, info).

        `world_mm` is py/vision.py's PoseFeed.world_mm: {joint name: (3,)
        array in scrub3d world millimetres}. An empty dict means no person
        this frame, which is not an error -- it is the normal state between
        volunteers -- so it returns without touching the tracker, leaving the
        last pose standing for whoever is drawing it.
        """
        self.frames += 1
        if not world_mm:
            self.info = {"ok": False, "why": "no person in frame"}
            return None, self.info
        if t is None:
            t = time.monotonic()
        t0 = time.perf_counter()
        # THE WHOLE INTEGRATION IS THIS LINE. Everything track.py documents --
        # retarget direction-live/length-from-shape, a missed limb held at its
        # last live pose, the 25mm-per-frame lunge guard measured across every
        # CELL rather than region origins, the seat test -- happens in here.
        posed, info = self.tracker.solve(world_mm, t)
        self._last_solve_ms = (time.perf_counter() - t0) * 1000.0
        if info.get("ok"):
            self.tracked += 1
            self.posed = posed
        self.info = info
        return posed, info

    def event(self):
        """A small dict for the websocket. -> {} when there is nothing to say.

        DELIBERATELY TINY, and never geometry. scrubbot.py's EVENT contract is
        "events only, never pose -- if this socket dies the cartoon still
        mirrors the person", and a posed body is ~5800 cells. Streaming that
        at 15Hz would make the browser need the backend to draw a person,
        which is the one thing that contract exists to prevent. So this
        publishes FACTS ABOUT the tracking -- how many regions solved, whether
        the lunge guard fired, whether the subject is in the scanned seat --
        and the page may render them or ignore them.

        Adding a key to EVENT is safe: web/main.js reads a fixed set and
        ignores everything else.
        """
        i = self.info
        if not i.get("ok"):
            return {"ok": False, "why": i.get("why", "")}
        return {
            "ok": True,
            "regions": int(i.get("regions", 0)),
            # jump_mm is how far the fastest CELL moved since the last frame.
            # Rounded, because the page shows it and 0.1mm of float tail is
            # noise on a projector.
            "jump_mm": round(float(i.get("jump_mm", 0.0)), 1),
            # freeze is the guard track.py calls "the difference between
            # tracking someone and lunging at them". Published so the page can
            # say so; this module does not act on it, because the thing with
            # the authority to stop is the FSM.
            "freeze": bool(i.get("freeze", False)),
            "away": bool(i.get("away", True)),
            "held": len(i.get("held", []) or []),
            "lost": len(i.get("lost", []) or []),
            "solve_ms": round(self._last_solve_ms, 2),
            # NEVER CLAIM A MEASUREMENT THAT DID NOT HAPPEN. The shape is a
            # population-table adult; only the pose is this person. main.js
            # already distinguishes these two cases for the solve event and
            # prints a different banner for each.
            "measured": False,
        }


if __name__ == "__main__":
    # Drives the whole path on the fake camera: render a person, detect,
    # convert, pose thirteen regions. No hardware, no depth, no network.
    import numpy as np

    sys.path.insert(0, _HERE)
    os.environ.setdefault("CAM", "fake")
    from vision import PoseFeed

    print("live body tracking, posed from MediaPipe world landmarks")
    lb = LiveBody()
    print(f"  shape: {len(lb.body.regions)} regions built in "
          f"{lb.build_s * 1000:.0f}ms (a population adult, NOT a scan)")

    feed = PoseFeed(delegate="CPU")
    ms, regions = [], []
    for i in range(60):
        frame, e, w, dt = feed.read()
        if frame is None:
            continue
        posed, info = lb.update(feed.world_mm, t=i / 30.0)
        if info.get("ok"):
            ms.append(lb._last_solve_ms)
            regions.append(info["regions"])
    feed.close()

    print(f"  {lb.tracked}/{lb.frames} frames posed a body")
    print(f"  regions solved per frame: median {np.median(regions):.0f} of 13")
    print(f"  Tracker.solve: mean {np.mean(ms):.2f}ms, max {np.max(ms):.2f}ms "
          f"(a 30fps frame is 33ms)")
    print(f"  last event: {lb.event()}")

    assert lb.tracked > 0.8 * lb.frames, "most frames did not pose a body"
    assert np.median(regions) >= 13, "the full body did not solve"
    assert np.mean(ms) < 5.0, "solving is too slow for the live path"
    print("\n  shape from anatomy, pose from the live camera, no depth. OK")
