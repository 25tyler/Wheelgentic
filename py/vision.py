"""py/vision.py — MediaPipe Pose for the ROBOT side.

THREE TRAPS, ALL SIGABRT (exit 134), NONE CATCHABLE BY try/except:
  1. mediapipe 1.0.1 aborts on macOS arm64 at create_from_options
     ("Check failed: service_ Service is unavailable", DrishtiMetalHelper).
     -> PIN 1.0.0. Never `pip install mediapipe` unpinned.
  2. delegate=GPU + a 3-channel SRGB numpy array aborts with
     "unsupported ImageFrame format: 1".  -> BGR2RGBA + ImageFormat.SRGBA.
  3. cv2.VideoCapture(0) without Camera permission returns isOpened()==False,
     NO exception, black frames forever.  -> assert loudly.

FRAMING CONSTRAINT (measured, not theoretical): Pose Landmarker is a WHOLE-BODY
model. A tight crop of just a forearm returns NO POSE AT ALL. Arms+torso in a
180x750 band -> DETECTED; the same band with the torso cropped -> nothing.
Mount the tripod at ~60-70 degrees so torso and shoulders are in frame. A
homography maps any oblique view of a plane exactly as well as a nadir one,
so you lose nothing.
"""
import math, os, time
import cv2, numpy as np

# mediapipe is imported LAZILY inside PoseFeed.__init__. The One Euro filter
# below is pure math with no mediapipe dependency, and a module-scope import
# made it untestable without the whole ML stack -- which is how a units bug in
# it survived. Import cost is paid once at PoseFeed construction either way.
mp = mpp = mpv = BaseOptions = None


def _load_mediapipe():
    global mp, mpp, mpv, BaseOptions
    if mp is not None:
        return
    import mediapipe as _mp
    from mediapipe.tasks import python as _mpp
    from mediapipe.tasks.python import vision as _mpv
    from mediapipe.tasks.python import BaseOptions as _BO
    mp, mpp, mpv, BaseOptions = _mp, _mpp, _mpv, _BO

# MIRRORING SWAPS THESE LABELS. MEASURED (tests/test_mirror.py), same frame,
# one arm raised:
#     as rendered    L15=(442,175)  R16=(100,324)   raised = 15
#     cv2.flip(f,1)  L15=(382,316)  R16=(183,313)   raised = 16
# MediaPipe infers left/right from the IMAGE, not from the person -- so with
# `mirror: true` and the code below reading L_*, the arm goes to the limb on
# the image-LEFT, which is the subject's RIGHT arm.
#
# In other words `mirror` decides WHICH ARM GETS SCRUBBED. Settle it before
# anything else: run `CAM=fake python py/vision.py` (or a real camera), wave
# ONE arm, and check the GREEN/ORANGE dots land on the arm you are waving. If
# they land on the other arm, flip `mirror` in config.json.
#
# I had this backwards in comments until the test measured it.
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW,  R_ELBOW      = 13, 14
L_WRIST,  R_WRIST      = 15, 16
L_HIP,    R_HIP        = 23, 24

# The joints scrub3d/track.py names, by the names IT uses. Spelled out here
# rather than imported from scrub3d/pose.py::IDX on purpose: this file must
# import on a machine with no scrub3d and no numpy-heavy stack, the same rule
# that keeps mediapipe itself out of module scope above. These eleven indices
# are MediaPipe's published landmark numbering and do not change.
#
# KNEES ARE INCLUDED AND THAT IS NOT DECORATION. track.LIVE_BONES poses the
# thighs from hip-to-knee, and a seated person's thighs are exactly where an
# arm reaching a resting forearm wants to be (scrub3d/anatomy.py says so in
# those words). A body model missing its legs makes that reach look free.
WORLD_IDX = {
    "l_shoulder": L_SHOULDER, "r_shoulder": R_SHOULDER,
    "l_elbow": L_ELBOW, "r_elbow": R_ELBOW,
    "l_wrist": L_WRIST, "r_wrist": R_WRIST,
    "l_hip": L_HIP, "r_hip": R_HIP,
    "l_knee": 25, "r_knee": 26,
    "nose": 0,
}


# ---------------------------------------------------------------- One Euro ---
# From jaantollander/OneEuroFilter (MIT). NOT casiez/OneEuroFilter -- that repo
# has NO LICENSE FILE, i.e. all rights reserved. Do not vendor it.
#
# Three corrections vs the version circulating online, all load-bearing:
#   (a) the derivative uses the FILTERED previous value, not the raw input.
#       Feeding raw values in lets the noise you are removing drive the
#       adaptive cutoff: noisy dx inflates the cutoff, which DISABLES smoothing
#       exactly when it is needed.
#   (b) dt is clamped -- two frames sharing a timestamp is ZeroDivisionError.
#   (c) NaN is skipped -- one NaN poisons the filter FOREVER, and MediaPipe
#       emits NaN for low-visibility landmarks. Without this a limb silently
#       freezes mid-demo with no error printed.
def _alpha(cutoff, dt):
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class _LowPass:
    __slots__ = ("y",)
    def __init__(self): self.y = None
    def __call__(self, x, a):
        self.y = x if self.y is None else a * x + (1.0 - a) * self.y
        return self.y


class OneEuro:
    """Start from Google's OWN world-landmark values (min_cutoff=0.1, beta=40).
    That is the OPPOSITE shape from the 1.5/1.0 most tutorials suggest.
    Tune per Casiez: hold still and LOWER min_cutoff until resting jitter is
    gone; THEN move fast and RAISE beta until lag disappears. Do not invert
    that order -- min_cutoff is the noise floor and beta cannot recover jitter
    rejection."""

    def __init__(self, min_cutoff=0.1, beta=40.0, d_cutoff=1.0):
        self.mc, self.beta, self.dc = min_cutoff, beta, d_cutoff
        self.xf, self.df = _LowPass(), _LowPass()
        self.prev = None

    def __call__(self, x, dt):
        if x != x:                       # (c) NaN guard
            return self.prev if self.prev is not None else 0.0
        dt = max(dt, 1e-6)               # (b) dt clamp
        dx = 0.0 if self.prev is None else (x - self.prev) / dt
        edx = self.df(dx, _alpha(self.dc, dt))
        y = self.xf(x, _alpha(self.mc + self.beta * abs(edx), dt))
        self.prev = y                    # (a) FILTERED, not raw. THE fix.
        return y


class Vec2Filter:
    def __init__(self, **kw): self.f = [OneEuro(**kw) for _ in range(2)]
    def __call__(self, v, dt): return tuple(f(c, dt) for f, c in zip(self.f, v))


# The D455's colour sensor is 1280x800 -- 16:10. Every Mac's built-in camera
# is 16:9. That ratio is the only fingerprint OpenCV gives us: cv2 exposes no
# device name, no vendor id, nothing but an index and the mode it reports.
DEPTH_CAM_ASPECT = 1.60
_ASPECT_TOL = 0.02

# ------------------------------------------------------------ lunge guard ---
# GRAFTED FROM scrub3d/track.py (MAX_JUMP_MM). Its wording is the reason to
# keep it: "that is not a smoothing threshold to be tuned up when it fires; it
# is the difference between tracking someone and lunging at them."
#
# A field-following controller will happily chase a person who stands up. The
# One Euro filter above does NOT protect against this -- it is a smoother, and
# its whole adaptive design is to GET OUT OF THE WAY of fast motion so the
# tracker keeps up. Fast motion is exactly the case that must not be followed.
#
# track.py's number is 750mm/s, expressed as 25mm per frame at 30fps. Framing
# it per frame silently re-tunes the guard with the frame rate: the live feed
# here measured 82fps, where 25mm/frame would mean 2050mm/s and the guard would
# effectively be off. So it is a SPEED here, and dt is the measured wall-clock
# dt, not a nominal 1/30.
#
# In pixels, because this file never leaves pixel space on purpose (see read()
# on why world_landmarks must not command the arm). The conversion is the
# homography's scale, MEASURED at the image centre of the shipped
# homography.pkl: 0.60mm/px across, 0.91mm/px down. Taking the SMALLER figure
# is the conservative direction -- it makes a given pixel speed count as less
# real-world speed, so the guard fires later rather than on normal motion.
#   750mm/s / 0.60mm/px = 1250 px/s
#
# MEASURED against the fake feed waving an arm deliberately fast, 300 frames
# with pose: median wrist speed 26 px/s, p95 119, p99 217, max 289. So the
# threshold sits 4.3x above the fastest normal motion observed. That headroom
# is the point: a guard that fires during a normal scrub would be worse than
# no guard, because the operator would learn to ignore it.
MAX_TRACK_SPEED_PXS = 1250.0


def find_depth_camera(max_index=4):
    """Index of the RealSense's colour stream, or None if it is not attached.

    WHY THIS IS NOT JUST `0`. cv2.VideoCapture indices are assigned by
    enumeration order, which changes with what is plugged in, in what order,
    and across reboots. MEASURED: two `system_profiler` calls minutes apart
    on the same machine listed the two cameras in opposite orders. The D455
    happened to be index 0 the day this was written -- but the failure when
    they swap is the worst kind: the demo runs, MediaPipe tracks a person,
    and the arm reaches for a forearm the RIG's camera never saw. No error,
    no log line, wrong geometry.

    The whole rig is solved in the depth camera's frame (see
    scrub3d/frames.py::world_from_camera), so "which camera" is not a
    preference. Every reach verdict is arithmetic against that sensor's view.

    HOW IT IDENTIFIES, given that cv2 exposes no device name or vendor id.
    Two facts about the D455's colour sensor that no Mac's built-in camera
    shares, checked in order of strength:

      1. It CANNOT do 1920x1080 -- ask for it and you get 1280x800 back.
         Every Mac built-in camera does 1080p natively. This is the
         discriminator, because it is a negative that a laptop camera can
         never satisfy.
      2. Its native mode is 1280x800, which is 16:10. Mac cameras are 16:9.

    A camera that fails (1) is the depth camera. The aspect check is kept as
    corroboration so a future sensor with a different top mode still matches
    on shape.

    Returns None rather than guessing. The caller decides whether a missing
    depth camera is fatal, because on a dev machine it usually is not.
    """
    import cv2
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        try:
            if not cap.isOpened():
                continue
            native_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            native_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            if not native_h:
                continue
            aspect_ok = abs(native_w / native_h - DEPTH_CAM_ASPECT) < _ASPECT_TOL
            # Ask for 1080p. A Mac camera gives it; the D455 substitutes.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            got_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            refuses_1080p = int(got_w) != 1920
            if refuses_1080p and aspect_ok:
                return i
        finally:
            cap.release()
    return None


# --------------------------------------------------------------- Pose feed ---
class PoseFeed:
    # CLASS-LEVEL, not only set in __init__, because tests/test_mirror.py and
    # tests/test_vision_real.py drive the REAL read() through
    # `PoseFeed.__new__(PoseFeed)` -- deliberately, so they can test the mirror
    # convention and the filter units with no camera and no model. __init__
    # never runs on those instances, so any state read() needs must have a safe
    # default HERE or read() raises AttributeError on them. Assignment in
    # __init__ still shadows these per instance; these are only the floor.
    lunging = False
    last_speed_pxs = 0.0
    _prev_wrist = None

    def __init__(self, model_path="models/pose_landmarker_full.task",
                 cam_index=0, mirror=True, delegate="GPU",
                 min_visibility=0.5):
        _load_mediapipe()
        self.mirror = mirror
        # 0.5 matches MediaPipe's own min_tracking_confidence default. Raise it
        # if the arm chases occluded joints; lower it if tracking is too twitchy
        # under venue lighting. Never set it to 0 -- that is the bug this gates.
        self.min_visibility = min_visibility
        self.low_vis_frames = 0
        # CAM=fake swaps in a synthetic moving person (py/fakecam.py) through
        # the same VideoCapture interface. Everything downstream -- the mirror
        # convention, the cartoon, a recorded run -- is then exercisable with
        # no hardware. It is NOT a substitute for the real go/no-go; a rendered
        # figure is not a human under venue lighting.
        if os.environ.get("CAM", "").lower() == "fake":
            from fakecam import FakeCapture
            self.cap = FakeCapture(
                wave_left=os.environ.get("WAVE", "left").lower() != "right")
            print("[vision] CAM=fake — synthetic subject, NOT a real camera")
        elif os.environ.get("CAM", "").lower() == "replay":
            # CAM=replay plays a RECORDED SESSION through the same surface: a
            # real person, recorded by the real D455, with a real distance
            # behind every pixel. It is the only source on this machine that
            # exercises depth at all -- the laptop camera has none and
            # CAM=fake renders a figure rather than measuring one.
            #
            # REC=<folder> picks the recording; otherwise the first one under
            # scrub3d/data/ is used. Those folders are gitignored (600MB of
            # captured frames), so this is a no-op on a fresh clone and says
            # so rather than falling through to a camera that is not there.
            from replaycam import ReplayCapture, find_recording
            rec = find_recording(os.environ.get("REC"))
            if rec is None:
                raise SystemExit(
                    "\nCAM=replay but no recording found.\n"
                    "  Put a capture session under scrub3d/data/ (folders of\n"
                    "  <name>_c.png / <name>_d.png plus intr.json), or set\n"
                    "  REC=/path/to/session. The sessions are gitignored, so\n"
                    "  a fresh clone has none.\n")
            self.cap = ReplayCapture(rec)
            print(f"[vision] CAM=replay — {os.path.basename(rec)}, "
                  f"{len(self.cap.names)} frames, REAL D455 depth")
        else:
            # FIND THE DEPTH CAMERA BY IDENTITY, not by index. `cam_index=0`
            # is a default, not a choice -- an explicit --cam N from the
            # operator always wins, and so does CAM_INDEX for a rig that
            # needs pinning. Otherwise we go looking for the D455, because
            # running the demo on the laptop camera fails silently: the
            # cartoon mirrors fine and the arm reaches into a frame the
            # rig was never solved against.
            pinned = os.environ.get("CAM_INDEX")
            if pinned is not None and pinned.strip().isdigit():
                cam_index = int(pinned)
                print(f"[vision] CAM_INDEX={cam_index} — pinned by environment")
            elif cam_index == 0:
                found = find_depth_camera()
                if found is None:
                    print("[vision] WARNING: no 16:10 depth camera found. "
                          "Falling back to index 0, which on a laptop is the "
                          "BUILT-IN camera. The rig is solved in the D455's "
                          "frame, so arm targets will not match what you see.")
                else:
                    if found != 0:
                        print(f"[vision] depth camera found at index {found}, "
                              f"not 0 — using it")
                    cam_index = found
            self.cap = cv2.VideoCapture(cam_index)
            self.cam_index = cam_index
        # TRAP 3.
        assert self.cap.isOpened(), (
            "\nCAMERA BLOCKED OR MISSING.\n"
            "System Settings > Privacy & Security > Camera -> enable for THIS terminal.\n"
            "Grant it to Terminal/iTerm/VS Code, NOT to the python binary.\n")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        # Lock exposure/WB so thresholds don't drift when room lights move.
        # These often return False on macOS AVFoundation and do nothing --
        # which is exactly why every threshold downstream is a RATIO.
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
        self.cap.set(cv2.CAP_PROP_AUTO_WB, 0)

        d = (BaseOptions.Delegate.GPU if delegate.upper() == "GPU"
             else BaseOptions.Delegate.CPU)
        self.landmarker = mpv.PoseLandmarker.create_from_options(
            mpv.PoseLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_path=model_path,
                                             delegate=d),
                # VIDEO mode carries tracking between frames AND applies
                # MediaPipe's OWN internal One Euro (min_cutoff 0.1, beta 40).
                # That is why our filter starts from those same values -- we
                # add a second gentler stage, not a fight. IMAGE mode
                # re-detects every frame and gets no smoothing at all.
                running_mode=mpv.RunningMode.VIDEO,
                num_poses=1,
                min_pose_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        )
        self.t0 = time.time()
        self.prev_t = self.t0
        self.f_elbow = Vec2Filter()
        self.f_wrist = Vec2Filter()
        # Lunge guard state. `lunging` is a REPORT, not a gate: read() still
        # returns the pose. Swallowing it here would be a new failure mode --
        # the FSM's own lost-pose path would start counting toward
        # LOST_FRAMES_ABORT on a person who is in frame and perfectly visible,
        # and the operator would see a scrub abort with no reason given.
        # The decision to stop belongs to the FSM, which is what has the state
        # to retreat correctly; this only supplies the fact.
        self.lunging = False
        self.last_speed_pxs = 0.0
        self._prev_wrist = None
        # THE 3D SKELETON, published beside the return value and NEVER in it.
        #
        # read() returns elbow and wrist PIXELS and that contract must not
        # change -- every caller unpacks exactly four values, and the docstring
        # below spells out why pixels are the only thing allowed to command the
        # arm. So this is an attribute, exactly like `lunging` above: a fact
        # published for whoever wants it, carrying no authority over motion.
        #
        # WHAT IT IS FOR, and why the prohibition below does not apply to it.
        # world_landmarks are hip-centred and scale-normalised, which is fatal
        # for a fixed camera->robot transform (see read()) and is exactly right
        # for POSING A BODY MODEL, whose origin is the body. scrub3d/track.py
        # wants a full 3D skeleton and does not touch the arm; the cartoon on
        # the projector is drawn relative to itself. Hip-centred is not a
        # defect there, it is the frame the model already lives in.
        #
        # This is the ONLY reason scrub3d/track.py is reachable on this
        # machine: the D455 is not attached, UVCAssistant blocks depth on
        # macOS, and track.Tracker.update() needs a depth frame. Tracker
        # .solve() needs world JOINTS, which MediaPipe hands over for free
        # on the RGB path we already run. Measured on the fake feed: 60 of 60
        # frames posed all 13 regions.
        self.world_mm = {}
        self.world_vis = {}

    # MediaPipe's world frame -> scrub3d's world frame, as ONE matrix.
    #
    # MediaPipe world landmarks: metres, origin at the mid-hip, +X toward the
    # subject's LEFT, +Y DOWN, +Z toward the camera.
    # scrub3d (see scrub3d/frames.py): millimetres, floor at z=0, +X ANTERIOR
    # (out of the chest, toward the camera), +Y to the subject's LEFT, +Z UP.
    #
    # So scrub3d_X = mp_Z, scrub3d_Y = mp_X, scrub3d_Z = -mp_Y. Written as a
    # matrix rather than three index swaps because a transposed or negated
    # axis here is invisible -- the body still renders, just facing or leaning
    # wrongly, and there is no error to read. One matrix can be checked
    # against the two comments above in ten seconds.
    _MP_TO_S3D = np.array([[0.0, 0.0, 1.0],
                           [1.0, 0.0, 0.0],
                           [0.0, -1.0, 0.0]])

    # Where the mid-hip sits above the floor for a SEATED subject, mm. The
    # same 1050 that scrub3d/anatomy.py builds its seated body at, so the
    # tracked skeleton and the model share a floor. MediaPipe cannot supply
    # this: its origin IS the hip, so absolute height is the one thing the
    # hip-centred frame throws away. Getting it wrong moves the whole body up
    # or down together and changes nothing about its shape or its pose.
    SEAT_Z_MM = 1050.0

    # The camera-to-world transform, fitted ONCE from the floor plane rather
    # than per frame. The floor does not move; re-fitting every frame would
    # make the world jitter with the fit's own noise and a stationary person
    # would appear to slide around the room. None until a depth frame with a
    # findable floor arrives, and None forever on a colour-only camera.
    _T_world_cam = None
    # The joint names in world_mm that came off a depth sensor this frame.
    world_measured = set()

    def _fit_world_from_depth(self, depth_mm):
        """Fit the floor and the world frame from one depth image.

        Sets self._T_world_cam, or leaves it None. NEVER RAISES: no floor is
        a normal condition (bags and cases around the chair are the usual
        cause, which DIMOS.md already records), and it must cost the estimate
        path nothing.
        """
        try:
            from scrub3d import frames as _F
            floor = _F.fit_floor(depth_mm, self.cap.intr)
            if floor is None:
                return
            subj = _F.deproject(depth_mm, self.cap.intr,
                                _F.subject_mask(depth_mm))
            if not len(subj):
                return                  # nobody in frame yet; try next frame
            # The centroid puts the world origin on the floor beneath the
            # person, which is where anatomy.anatomical_body() assumes they
            # are. Passing None would put it under the CAMERA instead, and
            # every joint would be offset by however far the camera stands
            # from the chair.
            self._T_world_cam = _F.world_from_camera(floor, subj.mean(0))
            print("[vision] floor fitted — body joints are now MEASURED")
        except Exception:                                    # noqa: BLE001
            self._T_world_cam = None

    def _publish_world(self, res):
        """Fill self.world_mm from a detection. Never raises, never returns.

        Kept out of read()'s body because read() is the hot path every caller
        depends on and this is a side-channel: if it ever throws, the arm must
        still get its pixels. Hence the bare guard at the bottom.
        """
        try:
            wl = getattr(res, "pose_world_landmarks", None)
            if not wl:
                self.world_mm, self.world_vis = {}, {}
                return
            W = wl[0]
            out, vis = {}, {}

            # MEASURED DEPTH WINS, WHEN THERE IS ANY. The block below places a
            # joint by scaling MediaPipe's hip-centred landmarks and adding a
            # seated-adult constant -- left/right and up/down are real, the
            # DISTANCE is a guess from average human proportions. read()'s own
            # docstring says never to command an arm from those.
            #
            # A depth source (CAM=replay today, a D455 on the arm computer
            # later) can answer the same question by measurement, so it is
            # preferred and the guess becomes the fallback. py/bodydepth.py
            # owns that arithmetic; this only chooses between them.
            #
            # PARTIAL IS FINE AND IS THE POINT. Depth fills the joints it can
            # actually see and leaves the rest to the block below, so an
            # occluded wrist degrades to the estimate instead of vanishing.
            # `world_src` records which joints were measured, so nothing
            # downstream has to guess whether a number came off a sensor.
            measured = {}
            depth_mm = getattr(self.cap, "depth_mm", None)
            if depth_mm is not None and getattr(self.cap, "intr", None):
                try:
                    import bodydepth as _bd
                    if self._T_world_cam is None:
                        self._fit_world_from_depth(depth_mm)
                    if self._T_world_cam is not None:
                        lm2d = getattr(res, "pose_landmarks", None)
                        if lm2d:
                            h, w = depth_mm.shape
                            px = {n: (lm2d[0][i].x * w, lm2d[0][i].y * h)
                                  for n, i in _bd.JOINT_IDX.items()}
                            measured = _bd.joints_world_mm(
                                px, depth_mm, self.cap.intr, self._T_world_cam)
                except Exception:                            # noqa: BLE001
                    # Same rule as the outer guard: a better source that
                    # fails must leave the working one alone, not take the
                    # frame down with it.
                    measured = {}

            for name, idx in WORLD_IDX.items():
                p = W[idx]
                vis[name] = float(p.visibility)
                # SAME VISIBILITY FLOOR as the arm path above, and for the
                # same reason: MediaPipe GUESSES an occluded joint rather
                # than omitting it. track.Tracker already knows what to do
                # with a joint that is absent -- it holds the region at its
                # last live pose and reports it lost after a second -- and
                # that behaviour only works if absent really means absent.
                # A MEASURED JOINT SURVIVES A LOW VISIBILITY SCORE. The floor
                # below exists because MediaPipe GUESSES an occluded joint
                # rather than omitting it, and a guess must read as absent.
                # But that reasoning is about the GUESS. When depth has put a
                # real surface at that pixel, the joint is not a guess any
                # more, and dropping it throws away the better evidence
                # because the weaker source was unsure.
                #
                # AND THAT REASONING WAS WRONG, MEASURED. Letting a measured
                # joint through the floor put a shoulder at -1739mm: an
                # occluded joint is one MediaPipe placed by GUESSING, so the
                # pixel it names is not on the person, and sampling depth
                # there measures the wall behind them with total confidence.
                # A measured wrong point is worse than an absent one, because
                # it arrives labelled "measured".
                #
                # The floor stays first. Depth can only confirm a joint the
                # detector actually saw; it cannot rescue one it invented.
                if p.visibility < self.min_visibility:
                    continue
                # A measured joint replaces the estimate outright. Not blended:
                # averaging a measurement with a guess produces a number that
                # is neither, and nothing downstream could say which it was.
                if name in measured:
                    out[name] = measured[name]
                    continue
                out[name] = (self._MP_TO_S3D
                             @ np.array([p.x, p.y, p.z]) * 1000.0
                             + np.array([0.0, 0.0, self.SEAT_Z_MM]))
            # The two midpoints track.LIVE_BONES asks for by name. Derived
            # here rather than in the consumer so there is one definition of
            # "mid_hip" in the process.
            for new, a, b in (("mid_shoulder", "l_shoulder", "r_shoulder"),
                              ("mid_hip", "l_hip", "r_hip")):
                if a in out and b in out:
                    out[new] = (out[a] + out[b]) / 2.0
            self.world_mm, self.world_vis = out, vis
            # WHICH JOINTS CAME OFF A SENSOR. limb_event() reports this as
            # `src`, so the page can say "measured" only about the ones that
            # were. Empty means the whole skeleton is the estimate.
            self.world_measured = set(measured)
        except Exception:                                    # noqa: BLE001
            # A side channel must never kill the arm's frame. Empty means
            # "no skeleton this frame", which every consumer already handles.
            self.world_mm, self.world_vis = {}, {}

    # The six joints plan §5 3.3 asks for, and only those. shoulder, elbow
    # and wrist per side is the arm the machine is working on; a knee or a
    # nose is not part of that claim, and this rides a 15Hz broadcast whose
    # whole contract is staying small (scrubbot.py:78).
    # THE HIPS ARE HERE BECAUSE THE SEAT IS. Six arm joints are what the
    # machine works on, and the plan asks for those; the hips are what a
    # chair is under. _publish_world already derives mid_hip and nothing
    # carried it, so the page placed its chair -- and therefore its ARM
    # MOUNTS, which are seat-relative -- from a typed 0.48m constant while
    # a measured hip height sat unused one process away.
    #
    # Two more points on a payload of six is a rounding error against the
    # 15Hz budget, and it is the difference between a chair drawn where
    # this person is sitting and a chair drawn where an average person
    # would be.
    # The longest an arm segment can be. An upper arm is about 300mm and a
    # forearm 265mm on a typical adult; 500 is loose enough for anybody and
    # tight enough to catch a sample that went past the person. Same spirit
    # as _measure_body's 700mm guard, tighter because this rejects a single
    # joint rather than a whole measurement.
    MAX_SEGMENT_MM = 500.0

    LIMB_JOINTS = ("l_shoulder", "l_elbow", "l_wrist",
                   "r_shoulder", "r_elbow", "r_wrist",
                   "l_hip", "r_hip")

    def limb_event(self):
        """The person's arm joints, JSON-safe, for the websocket. -> {} or dict.

        WHY A METHOD HERE RATHER THAN A CONVERSION AT THE CALL SITE. world_mm
        holds numpy arrays in scrub3d's frame, and the one place that knows
        what that frame IS is the matrix twenty lines above (_MP_TO_S3D and
        SEAT_Z_MM). A consumer that reached into world_mm and called tolist()
        would be carrying that knowledge in a second place, and the day the
        frame changes only one of them would be corrected.

        ABSENT MEANS ABSENT, AND THAT IS THE WHOLE CONTRACT. _publish_world
        already drops any joint below the visibility floor rather than
        shipping MediaPipe's guess at an occluded one, and this preserves
        that: a joint that was not seen is simply not a key. It is never a
        last-known position and never a zero. The page must draw only the
        joints that are here.

        `src` is "pose_2d_lifted". It is the honest name for what these are:
        MediaPipe's world landmarks, which are hip-centred and
        scale-normalised to a generic human (see read() on why they must
        never command the arm), placed on the floor by a SEATED-adult
        constant. Plan §5 3.3 wants `lift()`'s depth-backed world points
        instead, and depth needs the RealSense back on the GB10 -- §9, only
        the operator can do that. When it arrives, this string changes with
        the source and the page already prints whichever it is told.
        """
        wm = self.world_mm
        if not wm:
            return {}
        out = {}
        for name in self.LIMB_JOINTS:
            p = wm.get(name)
            if p is None:
                continue
            # Rounded to whole millimetres. The page draws a cartoon at
            # projector distance and the sub-millimetre tail is pure wire
            # cost -- six joints of float64 repr is about four times this.
            out[name] = [round(float(c), 1) for c in p]

        # A JOINT TOO FAR FROM THE ONE IT HANGS OFF IS NOT THAT JOINT. A
        # depth sample that lands past the person -- on the wall, a doorway,
        # the bunk behind them -- comes back perfectly confident about the
        # wrong surface, and the result is an elbow metres from its own
        # shoulder. Measured on the recording: 2.7% of upper-arm segments,
        # the worst of them 1554mm for a link that is about 250.
        #
        # _measure_body already refuses these before they reach a dimension
        # accumulator, so the BODY was safe while the POSE still drew them.
        # The same rule belongs here, at the other consumer.
        #
        # Dropped, not clamped: a joint we cannot place is absent, and the
        # page already handles a missing one. Pulling it to the nearest
        # plausible distance would invent a position and draw it in the same
        # colour as a measured one.
        for parent, child in (("l_shoulder", "l_elbow"), ("l_elbow", "l_wrist"),
                              ("r_shoulder", "r_elbow"), ("r_elbow", "r_wrist")):
            a, b = out.get(parent), out.get(child)
            if a is None or b is None:
                continue
            d = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
            if d > self.MAX_SEGMENT_MM:
                out.pop(child, None)

        if not out:
            return {}
        # THE SOURCE IS PER-FRAME, NOT A CONSTANT. "depth_measured" only when
        # every joint being reported was actually read off a depth sensor;
        # "pose_2d_lifted" when none were; "mixed" when some were and some
        # fell back, which is the honest answer for a partly occluded person
        # and must not round up to "measured".
        n_meas = sum(1 for k in out if k in self.world_measured)
        src = ("depth_measured" if n_meas == len(out) and n_meas
               else "mixed" if n_meas
               else "pose_2d_lifted")
        # WHICH ONES, not just how many. A consumer that wants to draw only
        # what a sensor reached cannot work it out from a count: the payload
        # mixes measured joints with ones placed by the seated-adult
        # constant, and they look identical. Naming them is two dozen bytes
        # and it is the difference between a panel that shows a measurement
        # and one that shows a measurement with a guess drawn beside it in
        # the same colour.
        return {"src": src, "mm": out, "measured": n_meas, "total": len(out),
                "measured_names": sorted(k for k in out
                                         if k in self.world_measured)}

    def read(self, elbow_idx=L_ELBOW, wrist_idx=L_WRIST):
        """-> (frame_bgr, elbow_px, wrist_px, dt). Never raises.

        PIXEL coordinates, NOT world_landmarks. world_landmarks are
        HIP-CENTRED and scale-normalised to a generic human -- the origin
        translates with the person, so a fixed camera->robot transform fitted
        against them DRIFTS as the subject shifts their weight. They also
        degrade under self-occlusion, which is exactly the case when a robot
        arm hovers over the forearm. NEVER command the arm from these.
        """
        ok, frame = self.cap.read()
        if not ok:
            return None, None, None, 0.0
        if self.mirror:
            frame = cv2.flip(frame, 1)
            # THE DEPTH HAS TO FLIP WITH IT. Pose runs on the mirrored colour
            # image, so its pixel x is measured from the mirrored edge, and
            # a depth frame still in camera order is indexed at 1280-x. On a
            # seated person that lands on the wall past their shoulder:
            # measured, a left shoulder came back 1739mm from the right one,
            # a "shoulder width" no body has, and it arrived labelled
            # measured because the depth read was perfectly confident about
            # the wrong pixel.
            #
            # Flipped HERE rather than in ReplayCapture, because the mirror
            # is this class's choice and the capture has no idea it happened.
            _d = getattr(self.cap, "depth_mm", None)
            if _d is not None:
                self.cap.depth_mm = cv2.flip(_d, 1)
        h, w = frame.shape[:2]

        # TRAP 2: the GPU delegate REJECTS 3-channel images.
        rgba = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA))
        img = mp.Image(image_format=mp.ImageFormat.SRGBA, data=rgba)

        # Timestamps MUST be monotonically increasing ms or the graph errors.
        res = self.landmarker.detect_for_video(
            img, int((time.time() - self.t0) * 1000))

        now = time.time()
        dt = max(now - self.prev_t, 1e-3)
        self.prev_t = now

        if not res.pose_landmarks:
            # CLEAR IT, do not leave the last frame's skeleton standing. A
            # stale pose that nothing overwrites is the exact failure the
            # freshness watchdog in scrubbot's SCRUB branch exists to catch,
            # and leaving a dict full of last-seen joints here would hand the
            # body tracker a person who left the room.
            self.world_mm = {}
            self.world_vis = {}
            return frame, None, None, dt

        self._publish_world(res)

        L = res.pose_landmarks[0]
        e, wr = L[elbow_idx], L[wrist_idx]

        # VISIBILITY GATE. MediaPipe still emits coordinates for occluded or
        # off-frame joints -- it GUESSES them, with a low `visibility` score.
        # Discarding that field means a guessed wrist drives the arm
        # confidently to a place the wrist is not. This is the exact case that
        # occurs mid-scrub: the robot arm itself occludes the forearm it is
        # scrubbing. Treat a low-confidence landmark as NO landmark; the state
        # machine already knows how to retreat on that.
        if e.visibility < self.min_visibility or wr.visibility < self.min_visibility:
            self.low_vis_frames += 1
            return frame, None, None, dt
        self.low_vis_frames = 0

        # FILTER IN NORMALISED SPACE, THEN SCALE. beta is a velocity gain and
        # therefore carries units: Google's own world-landmark defaults
        # (min_cutoff=0.1, beta=40) are tuned for 0..1 coordinates. Feeding
        # PIXELS multiplies every velocity by ~1280, which slams the adaptive
        # cutoff wide open on pure noise and disables the filter.
        #
        # MEASURED on a stationary wrist with 3.0px of gaussian jitter.
        # SWEPT, because the absolute residuals are NOT portable: 54
        # conditions (fps 24/30/60 x N 150/1000/4000 x 6 seeds) give
        #   pixels,     beta=40  -> -0.1% .. +1.2% removed   (inert, always)
        #   normalised, beta=40  -> +58.2% .. +75.6% removed (works, always)
        # and the separation never falls below 57 percentage points. THAT is
        # the claim. One worked example, with its conditions, because the bare
        # number moved every time anyone re-measured it (four passes read
        # 2.50 / 2.75 / 2.96 / 3.01 px for the same 'pixels' row): at seed 11,
        # N=150, warmup 40, dt=1/30 -- test_vision_real.py's own conditions --
        # raw 2.76px -> 0.94px normalised (66% removed) vs 2.75px in pixels.
        # Note dt is WALL-CLOCK (self.prev_t), so a fixed fps is a fiction;
        # test_vision_real asserts the >50% ratio, which holds in all 54.
        # for one extra frame of lag on a fast move. Found by an adversarial
        # audit; the comment above already CLAIMED these defaults were right,
        # which is exactly why nobody checked the units.
        elbow_n = self.f_elbow((e.x, e.y), dt)
        wrist_n = self.f_wrist((wr.x, wr.y), dt)
        elbow = (elbow_n[0] * w, elbow_n[1] * h)
        wrist = (wrist_n[0] * w, wrist_n[1] * h)

        # LUNGE GUARD, on the FILTERED wrist. Measured against the raw landmark
        # it would fire on MediaPipe's own single-frame noise; the filtered
        # value is what the arm is actually commanded from, so it is the one
        # whose speed means anything. See MAX_TRACK_SPEED_PXS above.
        if self._prev_wrist is None:
            self.last_speed_pxs = 0.0     # no previous frame, no speed yet
        else:
            d = math.hypot(wrist[0] - self._prev_wrist[0],
                           wrist[1] - self._prev_wrist[1])
            self.last_speed_pxs = d / max(dt, 1e-6)
        self.lunging = self.last_speed_pxs > MAX_TRACK_SPEED_PXS
        # Re-seeded even on a lunge, and even after a dropout, so the guard
        # measures ONE frame of motion rather than the whole gap. Without this
        # the first frame back after any occlusion looks like a lunge, which is
        # the most common moment in a scrub.
        self._prev_wrist = wrist
        return frame, elbow, wrist, dt

    def close(self):
        self.cap.release()


if __name__ == "__main__":
    # Hour 1 GO/NO-GO: 33 landmarks at >=20fps, no SIGABRT.
    # Wave ONE arm and watch which label moves. Settle mirror convention NOW.
    import sys
    feed = PoseFeed(delegate=sys.argv[1] if len(sys.argv) > 1 else "GPU")
    n, t0 = 0, time.time()
    while time.time() - t0 < 60:
        frame, e, w, dt = feed.read()
        if frame is None:
            continue
        n += 1
        if e and w:
            cv2.circle(frame, (int(e[0]), int(e[1])), 8, (0, 255, 0), -1)
            cv2.circle(frame, (int(w[0]), int(w[1])), 8, (0, 200, 255), -1)
            cv2.line(frame, (int(e[0]), int(e[1])), (int(w[0]), int(w[1])),
                     (255, 255, 0), 2)
            if n % 30 == 0:
                print(f"fps={n/(time.time()-t0):5.1f}  "
                      f"L_elbow={e[0]:6.1f},{e[1]:6.1f}  "
                      f"L_wrist={w[0]:6.1f},{w[1]:6.1f}")
        cv2.putText(frame, "GREEN=elbow ORANGE=wrist -- WAVE ONE ARM",
                    (12, 30), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
        cv2.putText(frame, "dots must land on the arm YOU are waving;",
                    (12, 56), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 200, 255), 1)
        cv2.putText(frame, "if not, flip \"mirror\" in config.json",
                    (12, 78), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 200, 255), 1)
        cv2.imshow("vision check", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    feed.close(); cv2.destroyAllWindows()
    print(f"\naverage fps: {n/(time.time()-t0):.1f}  (need >=20)")
