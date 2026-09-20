#!/usr/bin/env python3
"""py/scrubbot.py — the whole robot side.

    ./run.sh                                       live
    REPLAY=recordings/synthetic.jsonl ./run.sh    no camera, no robot
    python py/scrubbot.py --no-arm                 vision + screen, arm idle
    python py/scrubbot.py --scripted               canned scrub, no camera
    python py/scrubbot.py --record recordings/good_run.jsonl

Keys (in the OpenCV debug window):
    SPACE = emergency stop     r = clear estop     h = home
    s     = start scrub cycle  1-3 = manual splotch pop     q = quit
"""
import argparse
import signal, asyncio, json, math, os, sys, threading, time
import cv2
from websockets.asyncio.server import serve, broadcast

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibrate as calib
import motion
from config import Config
# Pose-independent contact. Imports numpy/scrub3d LAZILY inside itself and
# degrades to "the fixed threshold stays in charge" when either is absent, so
# this import is safe on a machine running --replay with the heavy stack dead.
import contact as contact_mod

# NO `from arm import Arm` AT MODULE LEVEL. This file drives two different
# arms now and must not know which one it has: importing arm.py eagerly
# would pull pyserial in on a Pi that only has python-can, and importing
# openyam.py eagerly would pull python-can in on a Mac that only has
# pyserial. HOME used to be imported alongside Arm and was never used
# (the only other mention is a comment) -- a RoArm-specific 4-tuple in a
# file that must stay arm-agnostic.


def make_arm(kind, port, dry_run):
    """ONE construction site for every arm. `kind` comes from --arm.

    This is a single function with one return contract, not a dev/prod
    split: both branches produce an object with the same pump, estop and
    feedback behaviour, and every caller below treats them identically.
    The import is inside each branch so the driver you are NOT using
    cannot fail to import on a host that lacks its transport.
    """
    if kind == "openyam":
        from openyam import OpenYamArm
        # --port carries the CAN interface here (`--port can0`). OpenYamArm
        # falls back to $CAN_IFACE and then "can0" when it is None, so the
        # bare `--arm openyam` path needs no extra flag.
        return OpenYamArm(channel=port, dry_run=dry_run)
    from arm import Arm
    return Arm(port=port, dry_run=dry_run)

# vision.py is imported LAZILY, inside vision_loop() only.
#
# REPLAY MODE MUST NOT IMPORT MEDIAPIPE. Replay exists precisely for the case
# where the camera or MediaPipe has failed -- a top-level `import mediapipe`
# means the fallback dies with the thing it is meant to survive. Caught by an
# integration test: `--replay ... --no-arm` exited with ModuleNotFoundError
# before printing a single line.
#
# Landmark indices are duplicated here (not imported) so --replay and
# --scripted need nothing from mediapipe at all.
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW,  R_ELBOW      = 13, 14
L_WRIST,  R_WRIST      = 15, 16

CFG = Config(os.path.join(os.path.dirname(__file__), "..", "config.json"))
CLIENTS = set()

# ONE instance, built at import. It holds the fitted gravity model, so a second
# one would throw away the fit and start collecting free-air samples again --
# and the fit is what decides whether contact is pose-independent or back on
# the threshold that fires at full extension with nothing under the arm.
CONTACT = contact_mod.LiveContact()

# EVENTS ONLY -- never pose. ~200 bytes. If this socket dies the cartoon still
# mirrors the person perfectly; it just stops popping splotches, and 1/2/3 on
# the keyboard is the rehearsed manual fallback.
#
# "pops" is a QUEUE, not a one-shot slot. It used to be a single `scrub: True`
# flag that the 15Hz pump consumed after sending, so any two fire() calls
# inside one 66ms window collapsed into ONE delivered event. Measured against
# the real stack: firing the three end-of-scrub splotches back-to-back
# delivered exactly one. On stage that is the money shot landing as "one
# splotch vanished, two are still stuck on the arm, counter reads 33%".
# Deterministic, not a race. Found by an adversarial audit.
# "phase" is the FSM's own state, published for DISPLAY ONLY. It is STICKY:
# unlike scrub/reset/link/ack/place it is NOT cleared in _pump(), because it is
# a continuous condition rather than an event. It starts at "IDLE" so a client
# that connects before the first frame reads something true instead of
# undefined. DECISIONS.md said the phase indicator reopens exactly when this
# key exists; the browser must still fall back to CYCLE RUNNING without it.
# "solve" is STICKY, like "phase" and unlike every one-shot above it: it is a
# condition ("a live partition exists / is being computed / failed"), not an
# event. A projector page that connects AFTER a solve finished must still learn
# the file is there -- a one-shot would be sent once, to whoever happened to be
# connected, and a browser refresh mid-demo would silently drop back to the
# bake. None until something solves, which is the state the demo ships in.
# "gov" is STICKY, like "phase" and "solve" and unlike every one-shot above
# it: it is a CONDITION -- what the safety governor last said about the arm's
# target -- not an event. A projector that connects mid-scrub must read the
# current verdict rather than wait for the next refusal. It starts None, which
# is what an unwired or absent governor leaves it as, and the page must keep
# working without the key: adding a field to this dict is safe precisely
# because the page ignores keys it does not know.
# "scrub_u" is WHERE THE SPONGE IS ON THE FOREARM RIGHT NOW, 0 = elbow,
# 1 = wrist -- the same units the cartoon's splotch `t` uses. Like "gov",
# "phase" and "solve" it is a CONDITION, not an event: it is the arm's current
# position, so a projector that connects mid-scrub reads where the sponge is
# rather than waiting for the next stroke. It is NOT cleared by _pump().
#
# It exists because the page was animating the scrub with an invented decaying
# roll (robotarm.js strokeAmp) that had nothing to do with this machine. The
# FSM has always computed this number -- see the SCRUB branch, it is already
# derived from the measured forearm and motion.scrub_offset -- and simply
# never sent it. None outside a scrub, which is what the page treats as "no
# real data, fall back to the stroke animation".
#
# "joints" is WHERE THE ARMS ARE, and it is STICKY like "gov", "phase",
# "solve", "body" and "scrub_u" -- a condition, not an event. A projector that
# connects mid-demo must read the current pose rather than wait for the arm to
# move. None until an arm exists, which is what --replay and the pre-main
# window leave it as, and the page must draw exactly as it always has without
# the key. Filled by _sample_joints() below; read that docstring before
# touching the shape, because the "src" field is the honesty claim.
#
# "limbs" is WHERE THE PERSON'S ARMS ARE -- shoulder, elbow and wrist per
# side, in millimetres, from the detection the vision loop already ran. It is
# the one field here that is NOT sticky, and the difference matters: every
# other condition above describes the machine, which stays where it was put,
# while this describes a person, who leaves. See its publish site in
# vision_loop for why a held-over pose is the failure and not the fallback.
EVENT = {"seq": 0, "mode": "live", "limb": "forearm_L", "t": 0.0,
         "scrub": False, "contact": False, "clean": 0, "reset": False,
         "pops": [], "place": None, "ack": None, "phase": "IDLE",
         "solve": None, "gov": None, "body": None, "scrub_u": None,
         "joints": None, "limbs": None,
         # "body" is HOW BIG THE PERSON ACTUALLY IS -- the measurements
         # scrub3d/live/live_body.py takes off the depth camera, carried so
         # the page can rebuild the model at this person's size instead of a
         # typical adult's. STICKY, unlike "limbs": a body's proportions do
         # not change when they lean out of frame, and the last measurement
         # of THIS person stays true until somebody else sits down. See
         # _sample_body() for why it reports its own confidence.
         "body": None}
LOCK = threading.Lock()
RUNNING = True
REC_FH = None

# The safety governor, built once in main() and consulted by govern_target()
# on every arm target. An ABSENT one allows everything and says it did not
# check -- see py/governor.py on why that is one pipeline with an optional
# layer rather than two pipelines. Never None after main() runs; the default
# keeps module-level importers (the tests) from tripping over it.
GOV = None

# THE ONE ARM THIS PROCESS DRIVES, published for the 15Hz pump to sample.
# main() assigns it right after make_arm(); it stays None in every path that
# runs before the arm is built and in the tests, which import this module
# without ever calling main(). _sample_joints() returning None for a None arm
# is what makes the page's no-backend behaviour identical to its old
# behaviour -- see EVENT["joints"] above.
ARM = None

# WHICH MOUNT THE ONE ARM WE DRIVE IS. web/assets/body.json lists the measured
# rig in order -- a0 is the arm on the person's left (y +425mm), a1 faces it
# from the right -- and web/main.js builds its fleet in that same order. This
# process commands ONE arm object, so it may only ever claim one of those two
# mounts. Naming it here rather than at the publish site is what stops a
# future second arm from being added by copying a literal string.
#
# It is "a0" because arm 0 is the mount the whole scrub choreography is
# already wired to (main.js RIG_REGION[0] = 'forearm-right', the forearm the
# splotches sit on). If this process ever drives the other one, this changes
# and nothing else does.
ARM_ID = "a0"

# How many joint angles the wire format carries per arm. SIX, because that is
# what the OpenYAM has and what plan §5 3.1 specifies -- not three, which is
# only what the RoArm's onboard IK can be solved back to. A driver that can
# honestly report fewer publishes nulls in the remaining slots rather than
# zeros: a zero is a claim that a joint is at its origin, and null is the
# truth, "this driver does not know".
N_JOINTS = 6


# The body being measured, accumulated across frames. live_body.dims() gives
# thirteen `Measured` accumulators, each a running median blended against a
# typical-adult prior by sample count -- so this starts as the generic adult
# and becomes THIS person as evidence arrives, and always reports which.
#
# Module-level and built lazily: a projector run with no depth never touches
# it, and importing live_body costs open3d and mediapipe on a machine that
# may have neither.
_BODY_DIMS = None
_BODY_LB = None


def _measure_body(world_mm, measured_names):
    """Feed one frame's MEASURED joints into the body accumulators.

    `world_mm` is vision.PoseFeed.world_mm and `measured_names` is the subset
    that came off a depth sensor this frame. ONLY THE MEASURED ONES ARE USED:
    an estimated joint is MediaPipe's generic-human guess, and feeding that
    into a measurement of this person would launder the guess into a number
    the page then calls measured.

    NEVER RAISES. It rides the vision loop.
    """
    global _BODY_DIMS, _BODY_LB
    if not measured_names:
        return
    try:
        if _BODY_DIMS is None:
            import live_body as _lb                          # noqa: F401
            _BODY_LB = _lb
            _BODY_DIMS = _lb.dims()

        def seg(a, b):
            if a not in measured_names or b not in measured_names:
                return None
            pa, pb = world_mm.get(a), world_mm.get(b)
            if pa is None or pb is None:
                return None
            import numpy as _np
            d = float(_np.linalg.norm(_np.asarray(pa) - _np.asarray(pb)))
            # A SEGMENT LONGER THAN AN ARM IS NOT AN ARM. A depth sample that
            # lands past the person -- on the wall, a doorway, the bunk
            # behind them -- produces a joint metres away, and the distance
            # between it and a good joint is a number no body has. Measured:
            # a shoulder sampled off the person gave a 1683mm shoulder width.
            #
            # live_body's own Measured.add() has per-dimension bounds and
            # would reject most of these. This is the cruder guard in front
            # of it, because a 700mm "forearm" sits inside some of those
            # ranges while still being nonsense, and because a wrong sample
            # should be refused where it is taken rather than where it is
            # stored.
            return d if 20.0 < d < 700.0 else None

        _BODY_DIMS["shoulders"].add(seg("l_shoulder", "r_shoulder"))
        # BOTH ARMS FEED THE SAME ACCUMULATOR. They are the same person's
        # upper arm; two samples per frame is twice the evidence, and the
        # running median handles one arm being occluded.
        for sh, el, wr in (("l_shoulder", "l_elbow", "l_wrist"),
                           ("r_shoulder", "r_elbow", "r_wrist")):
            _BODY_DIMS["upper_arm_len"].add(seg(sh, el))
            _BODY_DIMS["forearm_len"].add(seg(el, wr))
    except Exception:                                        # noqa: BLE001
        # A body that cannot be measured is the generic adult, which is what
        # the page already draws. Never the loop's problem.
        return


def _sample_body():
    """The measured body for the wire. -> dict or None.

    None means nothing has been measured, and the page keeps its baked
    default rather than being handed a prior dressed as a measurement.
    """
    if _BODY_DIMS is None or _BODY_LB is None:
        return None
    try:
        from scene_out import body_measurements
        b = body_measurements(
            _BODY_DIMS, circ_of=_BODY_LB.circ_of,
            limb_p=_BODY_LB.LIMB_P, torso_p=_BODY_LB.TORSO_P,
            limb_flatten=_BODY_LB.AN.ADULT["limb_flatten"],
            torso_flatten=_BODY_LB.AN.ADULT["torso_flatten"])
        # src "prior" means every value is still the typical adult. Sending
        # that would be sending the default twice, and the page would light
        # up "MEASURED" for a body nobody measured.
        return b if b.get("src") == "depth" else None
    except Exception:                                        # noqa: BLE001
        return None


def _sample_joints(arm):
    """What the arms are doing, for the 15Hz wire. -> dict, or None.

    THE "src" FIELD IS THE POINT OF THIS FUNCTION, not the angles.
    `docs/LIVE-3D-PLAN.md` §3 calls "whatever is real" an architectural
    property: "every response says whether it came from hardware. A number
    that cannot be measured is absent, never faked." A commanded angle is
    where we ASKED the arm to be. A measured angle is where an encoder says
    it IS. Those are different claims and the page has to be able to say
    which one it is drawing, because THE ARM HAS NEVER BEEN OBSERVED MOVING
    and a cartoon posed from q_cmd would look identical either way.

    So:

        src "commanded"  these came from what we sent. No encoder was read.
        src "measured"   these came from an encoder.

    Today it is ALWAYS "commanded". Plan §5 3.1 wants the measured angles
    from Justin's dimOS stack via `--attach`, and attaching is FORBIDDEN in
    this run: his stack is live on the bus and DIMOS.md is explicit that two
    bridges are two stacks answering the same names. When attach is allowed,
    the change is one branch here that prefers the attached reader -- nothing
    above or below this function moves, and the page already draws both.

    `model_error_mm` is the honest gap between commanded and measured. It is
    None here and that is not a stub: with no measured source there is no gap
    to report, and a 0.0 would read as "commanded and measured agree
    perfectly", which is a measurement nobody took. Absent, never faked.

    ONE ARM, ONE ENTRY. This process drives a single arm object (make_arm
    returns one), so `arms` carries exactly one key, ARM_ID. It would be two
    lines to mirror those angles onto "a1" and make the picture look
    symmetrical, and that is precisely the invented data the plan forbids --
    the other mount is not being commanded by anything in this process, so
    the honest report is that we have nothing to say about it. The page
    leaves an unlisted arm on its own fallback animation.

    NEVER RAISES. It runs inside the pump, which is the loop that keeps the
    projector alive; a driver that grows a new attribute, or a test double
    with none of them, must cost the demo a field and not its socket.
    """
    if arm is None:
        return None

    # MEASURED FIRST, WHEN AN ENCODER IS ACTUALLY READABLE. This is the one
    # branch the docstring below promises, wired ahead of the hardware so the
    # day the bridge is reachable nothing else has to move.
    #
    # THE SHAPE IS NOT GUESSED. scrub3d/live/dimos_bridge_server.py's own
    # Stack.state() builds it: per side, {"p": [3] metres, "q": [4], "joints":
    # [6] radians}, and joints come from dimOS's ordered joint positions, i.e.
    # an encoder. arm_dimos.py already speaks that protocol and exposes the
    # six as real_joints6(). So "measured" here means a number that came off
    # the metal, and nothing else may write that string.
    #
    # WHY IT IS SAFE TO CALL. `state` is a READ on the bridge -- it returns a
    # snapshot and commands nothing. It is not `target`, `home` or `gripper`.
    # Reading cannot move an arm, which is why this is allowed while driving
    # is not.
    #
    # DUCK-TYPED, NOT ISINSTANCE. The projector runs arm.py and openyam.py,
    # neither of which has this method; only the dimOS client does. hasattr
    # keeps this file from importing a driver it does not otherwise need.
    try:
        real = arm.real_joints6() if hasattr(arm, "real_joints6") else None
    except Exception:                                        # noqa: BLE001
        real = None
    if real:
        qm = [None] * N_JOINTS
        for i in range(min(N_JOINTS, len(real))):
            v = real[i]
            # A joint the stack could not report stays None. dimOS sends null
            # for an arm it has no pose for, and rounding a null to 0.0 would
            # draw a straight arm and call it a measurement.
            if v is not None:
                qm[i] = round(float(v), 4)
        if not all(v is None for v in qm):
            err = None
            try:
                # The honest gap between where we asked and where it is. Only
                # meaningful when BOTH exist; model_error() returns None when
                # the driver has no commanded pose to compare against, and
                # that None must survive rather than become a 0.0.
                err = arm.model_error() if hasattr(arm, "model_error") else None
                if err is not None:
                    err = round(float(err), 1)
            except Exception:                                # noqa: BLE001
                err = None
            return {"src": "measured", "arms": {ARM_ID: qm},
                    "model_error_mm": err}

    q = [None] * N_JOINTS
    try:
        # THE COMMANDED JOINT VECTOR, PREFERRED, because it is what the pump
        # has actually sent rather than where we are heading. openyam.py's
        # own joint_pose() docstring makes the same choice for the same
        # reason: "target is where the arm is heading, q_cmd is what the pump
        # has actually sent this tick. A cartoon posed from target would
        # arrive before the metal."
        #
        # Read as an attribute rather than through a method because no method
        # on either driver returns six: joint_pose() is deliberately the
        # positioning triple (its docstring says the projector "has no wrist
        # to pose"), and this wire format carries the wrist.
        src = getattr(arm, "q_cmd", None)
        if src is not None:
            for i in range(min(N_JOINTS, len(src))):
                q[i] = round(float(src[i]), 4)
        else:
            # A DRIVER THAT SOLVES RATHER THAN COMMANDS ANGLES. arm.py sends
            # millimetres and the ESP32 does the IK, so the closest honest
            # answer is joint_pose() inverting the vendor's own URDF -- and
            # it returns None, meaning "I do not know", on an unreachable
            # target or a host without numpy. None stays None; it is not
            # rounded up into a pose.
            jp = arm.joint_pose() if hasattr(arm, "joint_pose") else None
            if jp:
                for i in range(min(N_JOINTS, len(jp))):
                    q[i] = round(float(jp[i]), 4)
    except Exception:                                        # noqa: BLE001
        # Same rule as _publish_world in vision.py: a side channel must never
        # kill the loop it rides on. All-None is a legible answer -- "an arm
        # exists and we cannot say where it is" -- and the page treats it the
        # same as no key at all.
        q = [None] * N_JOINTS
    if all(v is None for v in q):
        return None
    return {
        # COMMANDED, ALWAYS, TODAY. See the docstring: this string is the
        # whole honesty claim and must never be written as "measured" by a
        # path that did not read an encoder.
        "src": "commanded",
        "arms": {ARM_ID: q},
        # The gap between commanded and measured, in millimetres. None until
        # a measured source exists to differ from.
        "model_error_mm": None,
    }


def govern_target(arm, x, y, z, t=0.0, normal=None):
    """THE ONE DOOR EVERY ARM TARGET GOES THROUGH. -> bool, did it move.

    A refusal does not move the arm and does not silently stall it either:
    the reason goes on EVENT so the projector shows what the governor said.

    Written as a function rather than inlined at each call site so there is
    exactly one place that can forget to ask. `arm.set_target` is still
    called directly by go_home() and the retreat inside arm.py, which is
    correct and deliberate -- fleet.py's own docstring carves out the same
    exception, that a stop must not first ask permission from an arbiter
    that might be wedged.

    BOTH GUARDS RUN, AND THE BOX RUNS FIRST. The box clamp and the governor
    refuse DIFFERENT things, so neither replaces the other -- measured
    against this demo's own limb model over the reachable workspace:

      * 4960 reachable points the governor ALLOWS and the box refuses. It
        has no table, no floor and no side walls: it only knows joint
        limits, the head ceiling, and how close the upper arm comes to the
        person. z=10mm -- through the table the volunteer's arm rests on --
        is CLEAR to the governor and clamped to 25mm by the box.
      * 50 points inside the box that ONLY the governor refuses, every one
        of them "structure would come inside D_BODY": the elbow swinging in
        while the sponge sits correctly on the forearm. Nothing in the demo
        asked that question before.

    Neither set contains the other, so this is defence in depth and not a
    fork. Deleting either one loses real refusals.

    THE ORDER IS THE WHOLE POINT, AND IT IS NOT INTERCHANGEABLE. The
    governor must judge the point that will ACTUALLY BE COMMANDED, which is
    the clamped one, because set_target clamps after we ask. Governing the
    RAW target instead refuses 9369 reachable points whose clamped point is
    clear -- every one reported as "unreachable" while the box was about to
    pull it into reach. That is a volunteer drifting a hand past the table
    edge and the demo freezing with a scary refusal on the projector instead
    of scrubbing, which is the failure this ordering exists to prevent.

    The clamp comes from the ARM, never from `arm.BOX` read here: openyam's
    box is a different box, and reading this module's constant would clamp
    RoArm numbers onto an OpenYAM and then bless a point that driver never
    sends.
    """
    try:
        cx, cy, cz = arm.clamp_to_box(x, y, z)
    except AttributeError:
        # A driver without the method (a test double, a future arm) is not a
        # reason to skip the governor. Judge the raw point instead and say
        # nothing clever about it: that is the pre-clamp behaviour, which is
        # conservative in the refusing direction.
        cx, cy, cz = x, y, z
    v = GOV.check(cx, cy, cz, t=t, normal=normal) if GOV is not None else None
    if v is not None:
        with LOCK:
            EVENT["gov"] = v.as_event()
        if not v.allowed:
            return False
    arm.set_target(x, y, z)
    return True

# Splotch positions along the forearm, 0 = elbow end, 1 = wrist end.
#
# THESE MUST LIE INSIDE THE SPONGE'S ACTUAL TRAVEL. The scrub oscillation
# spans u = 0.5 +/- amp/(2*half), where half = (forearm_mm/2)*0.8. With the
# default 35mm amplitude on a 250mm forearm that is only u = 0.325..0.675 --
# so splotches at 0.22 and 0.78 could NEVER be reached and the demo stalled at
# 33% with the operator faking the rest by hand. Measured across 180-350mm
# forearms; only the middle splotch ever popped from position.
#
# 0.34/0.50/0.66 sits inside the reachable band for every forearm length
# tested, with the 0.10 match tolerance giving margin at both ends.
# If you widen scrub_amp_mm you may spread these back out -- check against
# the u range, do not guess.
SPLOTCH_TS = [0.34, 0.50, 0.66]

# UV mode state. A position counts as CLEANED after the sponge has been over it
# for this many CONSECUTIVE frames with no tracer visible there -- see
# uv_should_pop(). It used to mean the opposite ("frames the spot was MISSING"),
# which made absence of dirt the evidence for cleanliness; the arm occludes the
# exact patch it scrubs, so absence was constant whether or not anything was
# cleaned. At ~30fps this is ~250ms of dwell, against a scrub that sweeps the
# forearm about 1.2 times a second -- a real fraction of one pass.
UV_GONE_FRAMES = 8
UV_LATCH_FRAMES = 5       # frames of a STABLE blob count before latching
# How many CONSECUTIVE frames without a usable pose before a scrub aborts.
# The arm occludes the forearm it is scrubbing and the visibility gate returns
# None on any uncertain landmark, so single-frame dropouts are normal. At
# ~30fps this is ~0.5s of genuine loss.
LOST_FRAMES_ABORT = 15
_uv_targets = []          # t positions actually DETECTED, not assumed
_uv_dwell = {}            # t -> consecutive frames SCRUBBED and clear
_uv_stable = []           # recent frames of detected positions, for latching


def should_abort_scrub(lost_frames, limit=None):
    """Has the person been lost long enough to abort the scrub?

    -> (abort_now, new_lost_count)

    Pure and importable ON PURPOSE. Testing this through the threaded vision
    loop was flaky (2 passes in 3) because the fake feed's frame rate varies
    with system load, so every wall-clock assumption drifts. The BEHAVIOUR
    under test is a counter rule; test it as one.

    The visibility gate returns None on any uncertain landmark and the arm
    occludes the forearm it scrubs, so single-frame dropouts are constant.
    """
    if limit is None:
        limit = LOST_FRAMES_ABORT
    n = lost_frames + 1
    return (n >= limit), n


def uv_should_pop(target_t, live_ts, sponge_u, dwell_count,
                  gone_frames=None, match_tol=0.12, near_tol=0.18):
    """Has this position been CLEANED -- i.e. scrubbed, and observed clear?

    -> (is_clean_now, new_dwell_count)

    TRACKS CLEAN, NOT DIRT. The counter here used to mean "consecutive frames
    this spot was NOT seen", so a position became clean by ACCUMULATING ABSENCE
    of tracer. Absence is the wrong signal: the robot arm occludes the exact
    patch it is scrubbing, so a spot vanishes constantly whether or not anything
    was cleaned -- and the whole `gone_frames` machinery existed to defend a
    signal that should never have carried the claim.

    It now counts DWELL: consecutive frames in which the sponge is over this
    position AND no tracer is observed there. Both halves are things actually
    seen, so "clean" is measured rather than inferred from a missing blob:

      - tracer visible here     -> reset (positive evidence it is NOT clean)
      - sponge over it, nothing
        visible                 -> dwell += 1; clean at gone_frames
      - sponge elsewhere        -> hold (no evidence either way -- a sleeve
                                  moving over a spot is not someone cleaning it)

    Note what did NOT change: the shipping default path (`dirt_mode:
    "scripted"`, scrubbot.py's `abs(st - u) < 0.10` branch) has ALWAYS decided
    clean this way, from sponge position alone. This makes the UV path agree
    with it and keeps fluorescence as corroboration, which is the one thing UV
    can say that geometry cannot.

    Behaviour is deliberately identical at the shipped constants -- all four
    assertions in tests/test_dirt.py pass unmodified. Verified by sweep:
    near_tol is irrelevant to those four (every target sits at distance 0.00 or
    >= 0.20 from the sponge), and the dwell boundary is 6, so UV_GONE_FRAMES = 8
    sits two steps clear of it.

    Pure and importable ON PURPOSE. The test used to RE-IMPLEMENT this logic
    inside itself, which is a tautology: it would pass with the shipped
    version broken in any way that the copy did not share.
    """
    if gone_frames is None:
        gone_frames = UV_GONE_FRAMES
    if any(abs(lt - target_t) < match_tol for lt in live_ts):
        return False, 0                      # seen dirty -> not clean, reset
    if abs(target_t - sponge_u) >= near_tol:
        return False, dwell_count            # sponge elsewhere -> no evidence
    n = dwell_count + 1                      # scrubbed here, nothing visible
    return (n >= gone_frames), n


def _spread_pick(ts, n):
    """Pick up to n positions SPREAD along the limb, not the n lowest.

    sorted(live)[:n] takes the n smallest t values, so with five blobs the two
    nearest the wrist are silently dropped and can never be cleaned. This
    walks evenly through the sorted list instead, so the picks span the limb.
    """
    # DEDUPE FIRST. _uv_dwell is a dict keyed by t, so two targets at the
    # same position collapse into ONE entry -- three "targets" become one and
    # the counter jumps to 100% after a single pop. Blobs at the same
    # parametric position are also physically the same spot, so merging them
    # is correct rather than merely defensive.
    ts = sorted(set(round(float(t), 3) for t in ts))
    if n <= 0 or not ts:
        return []
    if len(ts) <= n:
        return ts
    step = (len(ts) - 1) / float(n - 1) if n > 1 else 0
    picked = [ts[int(round(i * step))] for i in range(n)]
    # rounding can still land twice on the same index near the ends
    out = []
    for t in picked:
        if t not in out:
            out.append(t)
    return out
_cleaned = set()

# CONSENT LATCH. The FSM's IDLE -> APPROACH edge used to be gated ONLY on
# "a forearm is visible", and RETREAT returned unconditionally to IDLE after
# 2.5s. Verified by executing the real vision_loop with a fake pose feed: with
# an arm continuously in frame the machine scrubbed on an endless loop and
# would begin scrubbing ANY person who put an arm on the table -- a judge
# leaning in, someone reaching past. A machine that touches people must not
# start by itself.
#
# The operator presses 's' to arm one cycle. It clears on entry to RETREAT, so
# every scrub is a deliberate act. This costs the operator one keypress they
# were already making (the demo script has them press 's' at 0:52).
ARMED = False
_armed_at = 0.0           # when; ARM_TIMEOUT_S seconds later it lapses

# An arming press EXPIRES. Without this, pressing 's' and then walking away
# leaves the machine armed indefinitely, waiting for ANY forearm -- so the
# next person to reach onto the table starts a scrub nobody asked for. Consent
# is for a moment, not forever. 30s is long enough to seat a volunteer.
ARM_TIMEOUT_S = 30.0

# THE SECOND CLOCK. ARM_TIMEOUT_S above answers "did anything START?" and
# nothing here answered "is this session OVER?". Those are different
# questions and one number cannot hold both: press 's', let the arm begin a
# scrub, and the 30s clock stops applying the moment the FSM leaves IDLE --
# after which the latch is bounded by nothing at all. A cycle that stalls in
# SCRUB (a wedged pose that the freshness watchdog has not yet caught, an arm
# that holds instead of finishing) stays authorised indefinitely on a consent
# given minutes ago.
#
# scrub3d/session.py is that fix and it is why this import exists: ONE latch
# for the whole fleet, TWO clocks, and the stamp written before the flag so a
# crash between them leaves an EXPIRED session rather than an immortal one.
# It is pure stdlib (measured: 0.4ms to import, 0.15us per may_move() call --
# ~200x cheaper than one 30fps frame's own 33ms), so it costs the demo
# nothing and it is importable on a machine with no numpy.
#
# 180s is the outer bound on one volunteer's turn: the demo script budgets
# ~60s of scrub, so this is three times the longest legitimate cycle.
SESSION_BUDGET_S = 180.0

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scrub3d"))
try:
    import session as _session
    # `clock=time.time`, not the module's default time.monotonic, because
    # every other consent site in this file stamps with time.time() and the
    # --scripted path compares against it. Two clocks in one latch is the
    # bug this module exists to prevent; do not mix the bases.
    CONSENT = _session.Consent(arm_timeout_s=ARM_TIMEOUT_S,
                               budget_s=SESSION_BUDGET_S, clock=time.time)
except ImportError as e:
    # scrub3d is a sibling package and --replay must start without it, the
    # same rule the export_body import at _solve_live() follows. A demo with
    # no session module keeps the single-clock latch it always had rather
    # than refusing to boot.
    print(f"[consent] scrub3d/session.py unavailable ({e}); "
          f"falling back to the single {ARM_TIMEOUT_S:.0f}s clock")
    CONSENT = None


def consent_press(given_by="operator"):
    """Arm one cycle. -> the timestamp stamped, for _armed_at.

    STAMP FIRST, THEN LATCH, in ONE place. Both arming sites (the 's' key in
    remote_control_loop and the projector's `arm` command) carried their own
    copy of that ordering with an identical fifteen-line comment explaining
    it; a third site added later would have had to copy it correctly a third
    time. session.Consent.press() already does exactly this, so the ordering
    now lives where the latch does.
    """
    global ARMED, _armed_at
    if CONSENT is not None:
        CONSENT.press(given_by)
    _armed_at = time.time()          # stamp
    ARMED = True                     # then latch
    return _armed_at


def consent_clear(why="operator"):
    """Withdraw consent. Always allowed, never refused."""
    global ARMED
    if CONSENT is not None:
        CONSENT.revoke(why)
    ARMED = False


def consent_expired():
    """Has either clock run out? -> (expired, why).

    Asked once per frame from the vision FSM and from --scripted, so both
    paths get the session budget and not just the arming timeout. Returns
    (False, "") whenever nothing is armed -- an unarmed machine has no clock
    to run out.

    ARMED IS THE TRUTH AND THIS FOLLOWS IT, deliberately. Fifteen sites in
    this file write `ARMED = False` -- the estop gate, the link-lost gate,
    RETREAT, the camera-dropout path, two key handlers, --scripted -- and
    asking each of them to also revoke the latch would be fifteen chances to
    forget, with a sixteenth added later forgetting by default. That is the
    same shape as the _reset_cycle_state bug this file already records. So
    the latch is reconciled HERE, on the one read path every caller shares:
    if the flag went down by any route, the session is finished.
    """
    if not ARMED:
        # Somebody disarmed by writing the flag. Close the session so a later
        # press starts a fresh budget rather than resuming a spent one.
        if CONSENT is not None:
            st, _ = CONSENT.state()
            if st in (_session.ARMED, _session.RUNNING):
                CONSENT.finish("disarmed")
        return False, ""
    if CONSENT is not None:
        # ADOPT A FLAG RAISED BY ANY ROUTE, rather than expiring it.
        #
        # THIS IS THE BUG THIS BRANCH EXISTS TO PREVENT, and it is worth
        # spelling out because the first version of this file shipped it and
        # broke 14 checks in tests/test_consent_latch.py. Arming was a
        # TWO-field invariant -- (ARMED, _armed_at) -- and the latch made it
        # three. Any code that raised the two it knew about, which includes
        # the test harness and any path written before the latch existed,
        # produced ARMED=True beside a latch still sitting at IDLE. may_move()
        # correctly answers False for an IDLE latch, so a perfectly fresh
        # arming expired on the very next frame and no cycle could ever start.
        #
        # A latch only some arming paths can set is worse than no latch: it
        # revokes valid consent silently. So the flag stays authoritative and
        # the latch follows it, exactly as the `not ARMED` branch above makes
        # the latch follow a disarm. The clocks then run from _armed_at, which
        # every arming route does set.
        st, _ = CONSENT.state()
        if st not in (_session.ARMED, _session.RUNNING):
            # AT _armed_at, NOT NOW. Re-stamping would restart both clocks and
            # turn a consent given three minutes ago into a fresh one, which
            # is the stale-consent bug the clocks exist to catch.
            CONSENT.adopt(_armed_at or time.time())
        ok, why = CONSENT.may_move()
        return (not ok), why
    # Fallback path: the single clock, exactly as before.
    if (time.time() - _armed_at) > ARM_TIMEOUT_S:
        return True, f"armed {ARM_TIMEOUT_S:.0f}s ago and never started"
    return False, ""


def consent_started():
    """The first commanded motion of a cycle. Starts the session clock.

    Called on the IDLE -> APPROACH edge, which is the demo's own definition
    of "motion has begun": APPROACH is the first state that calls
    set_target. Before this the 30s arming clock applies; after it the 180s
    budget does, and the arming clock stops -- which is the whole reason the
    two are separate numbers rather than one.
    """
    if CONSENT is not None:
        # ALREADY RUNNING IS NOT A REFUSAL. start_motion() only accepts an
        # ARMED latch, and the demo re-enters APPROACH on every cycle: the
        # second cycle of a session finds the latch already RUNNING from the
        # first and start_motion() answers "cannot start motion while
        # running". That is correct for session.py -- the session DID already
        # start, and re-stamping would restart the budget, which is the whole
        # thing the budget exists to prevent -- but it is not a problem, and
        # printing it made a normal second cycle look like a rejected one on
        # the operator's console.
        st, _ = CONSENT.state()
        if st == _session.RUNNING:
            return True
        ok, why = CONSENT.start_motion()
        if not ok:
            print(f"[fsm] consent refused the start: {why}")
        return ok
    return True


# ------------------------------------------------------------- websockets --
# Commands the PROJECTOR page may send back. Deliberately tiny: the browser
# can ARM a cycle and it can ESTOP. Nothing else.
#
# WHY: the ARMED latch is read by cv2.waitKey in the OpenCV DEBUG window, so
# 's' is only reachable if THAT window has focus. On stage Chrome is fullscreen
# in kiosk mode on the projector, so the operator would have to alt-tab to a
# window hidden behind it to start the demo. The socket already existed; it
# just never carried anything upstream.
_ARM_REQUEST = threading.Event()
_ESTOP_REQUEST = threading.Event()
_CLEAR_REQUEST = threading.Event()
_SOLVE_REQUEST = threading.Event()

# ----------------------------------------------------------- live solve --
# The demo's four-arm partition has always been a RECORDING: tools/export_body.py
# runs the solver offline into web/assets/body.json and the page fetches that.
# Nothing here ever recomputed it, so "the paths come from this person's body"
# was true of the bake and unprovable at run time.
#
# This makes the same solver reachable while the demo is up. It is NOT a second
# pipeline: it calls tools/export_body.export_one -- the exact function the bake
# calls -- and the only difference is the filename it writes. One solver, one
# serialiser, one JSON shape, two filenames.
#
# WHY A SEPARATE FILE AND NOT AN OVERWRITE OF body.json. The bake is the
# fallback and must survive a failed or half-finished live solve. body.json is
# committed, known-good and what the page loads on boot; body-live.json does not
# exist until something solves, and the page only prefers it when it is there.
# Overwriting the bake would mean one bad solve costs the demo its floor.
LIVE_BODY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "web", "assets", "body-live.json")
_solving = False           # one solve at a time; the solver is not reentrant

# WHICH CAMERA THE MEASUREMENT OPENS. Set once from the parsed args in main(),
# because _solve_live runs on its own thread with no access to them, and a
# measurement that silently opened camera 0 while the demo ran on camera 1
# would measure whatever the built-in webcam could see -- usually the operator.
_MEASURE_CAM = 0
_MEASURE_MODEL = "models/pose_landmarker_full.task"


def _measure_person(cam_index=0, model_path="models/pose_landmarker_full.task",
                    n_frames=40):
    """Measure whoever is in front of the camera. -> (measurements, note).

    THIS IS WHAT MAKES "measured body" ON THE PROJECTOR TRUE. Until this
    existed, every body the page drew came from anatomy.ADULT -- a table of
    population-typical adult numbers -- because export_one was called with no
    measurements. The strip said "4 arms, measured body" and the second half
    described a constant.

    Returns ({}, why) rather than raising when it cannot measure. An empty dict
    means export_one falls back to the population table, which is exactly the
    behaviour the demo has today, so a failed measurement costs nothing that
    was not already lost. A WRONG measurement would cost something real: the
    partition sizes the body the arms plan against.

    OPENS ITS OWN CAPTURE, and does not touch the vision loop's. The vision
    loop's PoseFeed.read() returns two landmarks and no segmentation mask, by a
    contract the 30fps servo path depends on; widening it to carry a mask for a
    once-per-person measurement would slow the hot loop for something it never
    uses. The measurement is seconds long and happens between beats, so a
    second short-lived capture is the cheaper trade. It is released before the
    solve starts.
    """
    # IMPORTED HERE, NOT AT MODULE LEVEL, and numpy included. scrubbot has no
    # top-level numpy import on purpose -- --replay exists for the case where
    # the heavy stack has failed, and must start without mediapipe or numpy.
    # A measurement is the one path that genuinely needs both, so it pays for
    # them itself and reports its own failure instead of killing the demo.
    try:
        import numpy as np
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import vision as mpv, BaseOptions
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import measure as MEAS
    except BaseException as e:
        return {}, f"measurement stack unavailable ({e!r})"

    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    mp_path = model_path if os.path.isabs(model_path) else \
        os.path.join(root, model_path)
    cap = None
    try:
        opts = mpv.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=mp_path),
            running_mode=mpv.RunningMode.VIDEO,
            output_segmentation_masks=True, num_poses=1)
        lmk = mpv.PoseLandmarker.create_from_options(opts)
        cap = cv2.VideoCapture(cam_index)
        if not cap.isOpened():
            return {}, "no camera to measure with"

        # BEST OF N FRAMES, NOT THE FIRST. A single frame can catch a blink of
        # low landmark confidence or a half-occluded arm, and measure_person
        # refuses on either -- correctly, but a refusal the operator sees as
        # "it just did not work". Taking the frame that measured the most
        # limbs turns a momentary refusal into a retry that costs ~1.3s.
        t0 = time.time()
        best, best_rep, best_n = {}, None, -1
        for _ in range(n_frames):
            ok, frame = cap.read()
            if not ok:
                continue
            rgba = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA))
            img = mp.Image(image_format=mp.ImageFormat.SRGBA, data=rgba)
            res = lmk.detect_for_video(img, int((time.time() - t0) * 1000))
            if not res.pose_landmarks or not res.segmentation_masks:
                continue
            L = res.pose_landmarks[0]
            h, w = frame.shape[:2]
            lms = np.array([[l.x * w, l.y * h] for l in L])
            vis = np.array([l.visibility for l in L])
            person = res.segmentation_masks[0].numpy_view() > 0.5
            if person.ndim == 3:
                person = person[..., 0]

            # NO DEPTH ON THIS MACHINE, AND THE STANDOFF MUST THEREFORE BE
            # STATED RATHER THAN INVENTED. measure_person refuses outright
            # without one, because an unstated pixel scale is the easiest way
            # to produce a confident, wrong, whole-body size. config.json
            # carries the rig's real camera-to-torso distance; the default is
            # the value girth.py's own test uses for this rig.
            standoff = float(CFG.data.get("standoff_mm", 1050.0))
            fx = float(CFG.data.get("camera_fx_px", 0.0)) or (w * 0.82)
            m, rep = MEAS.measure_person(person, lms, vis, fx,
                                         depth_mm=None,
                                         torso_depth_mm=standoff)
            n_ok = len(rep.get("limbs", {}))
            if n_ok > best_n:
                best, best_rep, best_n = (m or {}), rep, n_ok
            if n_ok >= 4:
                break

        if best_rep is None:
            return {}, "nobody found in frame"
        print("[measure] " + MEAS.summary(best or None, best_rep))
        if not best:
            return {}, "; ".join(best_rep.get("refused", ["refused"]))[:120]
        return best, f"{best_n} limb(s) measured"
    except BaseException as e:
        return {}, f"measurement failed ({e!r})"
    finally:
        if cap is not None:
            cap.release()


def _solve_live():
    """Run the REAL solver and write body-live.json. Blocking; own thread.

    Announced on the socket, not streamed down it. The result is ~670 KB and
    the socket is a 15Hz fire-and-forget broadcast with no backpressure whose
    whole contract is "events only, so the cartoon survives Python dying".
    Putting geometry on it would make the browser need the backend to draw a
    body. So the page is TOLD a new solve exists and fetches it over HTTP,
    which is the same transport it already uses for the bake.
    """
    global _solving
    with LOCK:
        if _solving:
            print("[solve] already solving — ignoring")
            return
        _solving = True
    # Tell the page it started. MEASURED 3.8s on this Mac for body A -- fast
    # enough to sit inside a demo, slow enough that an operator who presses a
    # key and sees nothing for four seconds presses it again, which is the
    # exact double-press swapBody() in main.js is hardened against.
    #
    # Do NOT read 3.8s as a guarantee. It is ring_layout, the FAST placement;
    # export_body's own note records place_arms.search(), the real optimiser,
    # failing to return inside 500s on this machine. If anyone swaps the
    # placement, this stops being an interactive key.
    with LOCK:
        EVENT["solve"] = {"state": "running"}
    t0 = time.time()
    # BOUND BEFORE THE try, because the "ready" event below reads them and the
    # except path must not turn a solver failure into a NameError that hides it.
    measurements, why = {}, "not attempted"
    try:
        # MEASURE FIRST, THEN SOLVE. This is the whole point of the live solve:
        # re-running the partition against the SAME population table it was
        # baked from proves nothing -- it would return the numbers already in
        # body.json. Measuring the person first is what makes "the paths come
        # from this person's body" a claim the run time can support.
        #
        # A refusal is not a failure. measurements stays {}, export_one falls
        # back to anatomy.ADULT, and the solve still runs and still redraws --
        # which is exactly what the 'v' key did before this existed. What
        # changes is that the operator is TOLD which one they got, and the
        # page is told too, so nothing on screen can claim a measurement that
        # did not happen.
        measurements, why = _measure_person(cam_index=_MEASURE_CAM,
                                            model_path=_MEASURE_MODEL)
        if measurements:
            print(f"[solve] measured this person: {why}")
        else:
            print(f"[solve] not measured ({why}) — using the population table")
        with LOCK:
            EVENT["solve"] = {"state": "running", "measured": bool(measurements),
                              "why": why[:120]}
        # IMPORTED HERE, NOT AT MODULE LEVEL. export_body imports numpy and the
        # whole scrub3d package at import time and exits the process outright
        # if scrub3d is missing. scrubbot must start on a machine that has
        # neither -- --replay exists precisely for the case where the heavy
        # stack has failed -- so this cost is paid only when someone asks for a
        # solve, and its failure is caught below instead of killing the demo.
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
        import export_body
        # The measured dict goes in as `measurements`, which is the argument
        # anatomy.anatomical_body has always accepted and nothing has ever
        # passed. ONE body builder, ONE solver, ONE serialiser -- the only
        # thing that changed is where the semi-axes come from, which is the
        # property anatomy.py's own header claims for this pipeline.
        kwargs = {"measurements": measurements} if measurements else {}
        # want_skin=False: the fused Poisson surface (scrub3d/skin.py) measures
        # 1.08s on this Mac, and this is the path an operator triggers with a
        # keypress and then watches. 3.8s is already at the edge of what
        # somebody waits through without pressing the key again -- which is the
        # exact double-press swapBody() is hardened against -- so adding a
        # second of purely cosmetic geometry here is the wrong trade. The
        # offline bake builds it; both paths write the same parts.
        dest = export_body.export_one("body-live", kwargs, dest=LIVE_BODY,
                                      want_skin=False)
        dt = time.time() - t0
        print(f"[solve] wrote {dest} in {dt:.1f}s")
        with LOCK:
            # "measured" RIDES WITH THE RESULT, so the page can say which body
            # it is drawing. A projector that reads "measured body" over a
            # population table is the defect this whole change exists to fix;
            # shipping the flag beside the file is what stops it coming back.
            EVENT["solve"] = {"state": "ready", "file": "body-live.json",
                              "secs": round(dt, 1),
                              "measured": bool(measurements), "why": why[:120]}
    except BaseException as e:
        # BaseException, not Exception: export_body calls sys.exit() when
        # scrub3d is absent, which raises SystemExit -- and SystemExit in a
        # non-main thread would otherwise unwind this thread silently, leaving
        # the page showing "running" forever with no failure ever reported.
        print(f"[solve] FAILED: {e!r} — the page keeps the baked body")
        with LOCK:
            EVENT["solve"] = {"state": "failed", "why": str(e)[:120]}
    finally:
        with LOCK:
            _solving = False


async def _handler(ws):
    CLIENTS.add(ws)
    print(f"[ws] client connected ({len(CLIENTS)})")
    try:
        async for raw in ws:
            try:
                cmd = json.loads(raw).get("cmd")
            except (json.JSONDecodeError, AttributeError):
                continue
            if cmd == "arm":
                _ARM_REQUEST.set()
                print("[ws] ARM requested from the projector")
            elif cmd == "estop":
                _ESTOP_REQUEST.set()
                print("[ws] ESTOP requested from the projector")
            elif cmd == "clear":
                # The projector could TRIGGER an estop but not clear one, and
                # the torque watchdog fires autonomously -- so a torque cutout
                # mid-demo left the operator unable to recover from the window
                # they were actually looking at. Recovery must live wherever
                # the trigger lives.
                _CLEAR_REQUEST.set()
                print("[ws] CLEAR-ESTOP requested from the projector")
            elif cmd == "solve":
                # A FRESH PARTITION, ON DEMAND. Handled in its own thread by
                # remote_control_loop rather than here, for the same reason arm
                # and estop are: this coroutine is the only reader of every
                # client socket, and a solve blocks for tens of seconds.
                # Running it inline would stop the event pump -- the projector
                # would stop receiving splotch events for the whole solve.
                _SOLVE_REQUEST.set()
                print("[ws] LIVE SOLVE requested from the projector")
    except Exception:
        pass                      # a dead client must never kill the server
    finally:
        CLIENTS.discard(ws)
        print(f"[ws] client gone ({len(CLIENTS)})")


async def _pump():
    while RUNNING:
        # SAMPLED HERE AND NOT WHERE THE ARM IS COMMANDED. Every other sticky
        # field on EVENT is written by whichever loop learned the fact; the
        # arm's pose has no such moment, because it changes continuously
        # inside the driver's own 40Hz pump thread. Reading it once per
        # broadcast is what makes the page's 15Hz exactly the wire rate it
        # interpolates from, rather than whatever rate the FSM happened to
        # issue targets at.
        #
        # OUTSIDE `LOCK`, DELIBERATELY. _sample_joints takes the DRIVER's
        # lock (openyam.joint_pose and arm.py's target read both do), and the
        # driver's threads never take LOCK -- but nesting a foreign lock
        # inside ours is how a future edit turns a working pair into a
        # deadlock that only appears on stage. The assignment below is the
        # only part that needs LOCK, and it is a single rebind.
        j = _sample_joints(ARM)
        with LOCK:
            # STICKY, so the last known pose survives a sample that failed.
            # A None here would blank the arm on the projector for one frame
            # every time a driver read raced its own pump -- a flicker with
            # no cause a viewer could name. Only a real answer overwrites.
            if j is not None:
                EVENT["joints"] = j
            EVENT["seq"] += 1
            blob = json.dumps(EVENT)
            # Drain the queue and clear one-shot flags AFTER serialising, so
            # every pop queued since the last tick goes out in this frame.
            EVENT["pops"] = []
            EVENT["place"] = None            # one-shot, like the flags above
            EVENT["ack"] = None
            EVENT["scrub"] = False
            EVENT["reset"] = False
            # ONE-SHOT LIKE THE REST. Left sticky, a long-recovered arm keeps
            # reporting itself lost on every frame forever.
            EVENT["link"] = None
        # broadcast() is FIRE-AND-FORGET. The obvious
        # `for ws in CLIENTS: await ws.send()` applies backpressure and would
        # stall the vision AND robot loops behind one hung browser tab.
        broadcast(CLIENTS, blob)
        await asyncio.sleep(1 / 15)


async def _serve_ws():
    # ping_interval reaps a dead tab before its write buffer grows unbounded --
    # broadcast() has no backpressure, so a zombie connection would otherwise
    # accumulate messages until it times out.
    async with serve(_handler, "127.0.0.1", 8765,
                     ping_interval=5, ping_timeout=5):
        print("[ws] listening on ws://127.0.0.1:8765")
        await _pump()


def start_ws():
    """Start the event socket in its own thread.

    LOUDLY. A previous version passed the coroutine straight to asyncio.run in
    a lambda, so an OSError(48) 'Address already in use' -- a leftover process
    from the last demo run, the single most likely cause -- surfaced as a raw
    30-line traceback from 'Thread-1 (<lambda>)'. The arm kept running, the
    projector silently never connected, and on stage that reads as 'the
    splotches just don't pop' with no clue why. Caught in a cold-boot
    rehearsal from a fresh clone.
    """
    def _run():
        try:
            asyncio.run(_serve_ws())
        except OSError as e:
            print("\n" + "=" * 62)
            print(f"[ws] CANNOT BIND ws://127.0.0.1:8765 — {e}")
            print("     The projector will NOT receive splotch events.")
            print("     Almost certainly a leftover process from the last run:")
            print("       pkill -f 'py/scrubbot.py' ; pkill -f 'http.server 8000'")
            print("     The cartoon still mirrors the person; use 1/2/3 by hand.")
            print("=" * 62 + "\n")
        except Exception as e:                      # never take the arm down
            print(f"[ws] websocket thread died: {e!r} — 1/2/3 still work")

    threading.Thread(target=_run, daemon=True).start()


def fire(limb, t, clean, contact):
    """Queue a splotch pop. Never overwrites a pop that has not been sent."""
    with LOCK:
        # ROUND, NOT int(). int() TRUNCATES: every caller passes a raw float
        # (100 * len(_cleaned) / len(targets)), and with the shipped THREE
        # splotches 2/3 is 66.666..., which int() sends as 66 where the correct
        # value is 67. Measured through this function: raw
        # [33.33, 66.67, 100.0] went out as [33, 66, 100].
        #
        # Nothing rendered it -- web/main.js:385 computes the counter from its
        # own local count and never reads this field -- so the projector has
        # always shown 67. But the field is wrong on the wire, and the next
        # reader of `m.clean` would inherit it.
        #
        # THREE TESTS COULD NOT SEE IT because they replace this function with a
        # stub that rounds: test_uv_fsm.py:101 and test_mode_switch.py:85 both
        # use round(c), and test_consent_latch.py:460 drops the value entirely.
        # The guard in test_dirt.py drives THIS function and reads the queue.
        #
        # Fixed here rather than at the three call sites, so a fourth caller
        # passing a float cannot re-arm it.
        EVENT["pops"].append({"limb": limb, "t": round(float(t), 3),
                              "clean": round(clean)})
        # scrub/t/clean kept for one release so an older projector page still
        # pops SOMETHING rather than nothing.
        EVENT.update(limb=limb, t=round(float(t), 3), scrub=True,
                     clean=round(clean), contact=bool(contact))


def _reset_cycle_state(tracker):
    """Everything a finished cycle must forget. ONE definition, called from
    EVERY exit to IDLE -- RETREAT, the estop gate, and the link-lost gate.

    Three separate paths reach IDLE and each one needs this. Having the estop
    gate carry its own copy is how the link-lost gate (added later, running
    FIRST) silently skipped it: forcing IDLE made the estop gate's own
    `state != "IDLE"` guard false. Measured -- pull the cable, press SPACE,
    and _cleaned still held [0.5], so the next volunteer's scrub opened
    part-done. A fourth path added later would repeat the bug; call this.
    """
    global _cleaned
    tracker.reset()
    _uv_targets.clear()
    _uv_dwell.clear()
    _uv_stable.clear()
    fire_reset()


def fire_reset():
    global _cleaned
    _cleaned = set()
    with LOCK:
        EVENT["pops"] = []          # drop anything queued but unsent
        EVENT["place"] = None
        # THE SPONGE IS NO LONGER ON ANYONE. scrub_u is sticky -- the page
        # reads it as "the arm is at this place on the forearm right now" --
        # so a finished cycle that left the last value standing would have the
        # cartoon arm holding contact on a person after the real one retreated.
        # This is the ONE path every exit to IDLE funnels through (see
        # _reset_cycle_state), which is why it is cleared here and not at the
        # three call sites.
        EVENT["scrub_u"] = None
        EVENT.update(reset=True, clean=0, scrub=False)


def pop(i):
    """Manual splotch pop, bound to keys 1-3. Rehearsed fallback.

    TARGETS THE POSITIONS THE BROWSER ACTUALLY HAS. In UV mode the page moved
    its splotches to the DETECTED tracer positions and the page only pops
    within 0.07 of an event, so firing the hardcoded SPLOTCH_TS popped ZERO of
    three on the projector -- while this function happily added each t to
    _cleaned and drove the counter to 100%. Worse than the browser twin of
    this bug: there the counter sat at 0% and looked broken; here it claims a
    finished scrub with three splotches still stuck on the cartoon arm in
    front of judges. The FSM's scrub path already used _uv_targets; only this
    manual path was missed.
    """
    targets = _uv_targets if _uv_targets else SPLOTCH_TS
    if 0 <= i < len(targets):
        st = targets[i]
        if st in _cleaned:
            return
        _cleaned.add(st)               # keyed by POSITION, see the FSM
        fire("forearm_L", st,
             100 * len(_cleaned) / len(targets), True)
        print(f"[manual] popped splotch {i+1}")


def _num(key, default):
    """config.json's `key` as a number, falling back to `default`.

    MODULE SCOPE, and it used to be nested inside vision_loop's frame loop --
    redefined on every one of ~38 frames a second, and invisible to the four
    other sites that read a numeric key. Those four stayed bare:

        z     = CFG.data.get("forearm_z_mm", 45.0)       # three sites
        depth = CFG.data.get("contact_depth_mm", -5.0)   # two sites

    and a string, None, or nonsense in either one raises TypeError on the very
    next line, which is arithmetic. MEASURED against the shipped expressions:
    '-5.0' -> TypeError, '45.0' -> TypeError, 'deep' -> TypeError, None ->
    TypeError; ints and floats are fine. py/config.py is a bare json.load
    precisely so a malformed edit NEVER crashes the process on stage, and then
    the value it handed back crashed it one line later.

    The type of `default` decides the coercion (DIRECTIVE.md:1657: '190' -> 190,
    'abc' -> 190, None -> 190, True -> 1). Every motion default here is a float,
    so nothing truncates; keep it that way if you add a key.

    `true` in the JSON therefore yields 1.0 rather than the default, so a depth
    of `true` presses to z=2.0 -- which arm.py's BOX clamps to zmin=25.0 before
    anything moves (measured). Left as-is: it matches the documented behaviour
    for the five dirt_* keys, and inventing a second rule for two of seven keys
    is worse than one rule that the box already bounds.
    """
    v = CFG.data.get(key, default)
    try:
        return type(default)(v)
    except (TypeError, ValueError):
        return default


_DIRT_MODES = ("scripted", "uv")
_warned_dirt_modes = set()


def dirt_mode():
    """config.json's dirt_mode, normalised, with a LOUD fallback.

    This was a bare `CFG.data.get("dirt_mode") == "uv"`, and nothing anywhere
    validated the value: py/config.py is a bare json.load by design (it must
    never crash on stage). So "UV", "Uv", "uv " and a fat-fingered "vu" every
    one of them compared False and ran SCRIPTED, in silence.

    That is the one failure the demo script explicitly forbids. A judge asks
    "is the dirt detection real?", the operator edits config.json under
    pressure -- the recovery card tells them to, and it hot-reloads -- and then
    narrates a live detector over a scripted run. DEMO-SCRIPT.md: "Never claim
    it's live if it isn't. A judge who catches one overclaim discounts
    everything else you said."

    Case and stray whitespace now just work. A value that is still not a mode
    warns ONCE PER DISTINCT VALUE and falls back to scripted: once, because the
    caller runs per frame at ~38fps and a per-frame warning buries the log;
    per distinct value, so an operator who fixes "UV" and then mistypes "vu"
    is told about the second one too.

    Reads CFG.data on every call and caches nothing -- dirt_mode is documented
    as hot-reloading mid-scrub (RECOVERY-CARD.md) and two tests assert it.
    """
    raw = CFG.data.get("dirt_mode", "scripted")
    mode = str(raw).strip().lower()
    if mode in _DIRT_MODES:
        return mode
    if raw not in _warned_dirt_modes:
        _warned_dirt_modes.add(raw)
        print(f"[config] dirt_mode {raw!r} is not one of {_DIRT_MODES} -- "
              f"running 'scripted'. The UV detector is NOT active.")
    return "scripted"


def _report_tape_check(arm):
    """Say out loud what the last tape check concluded, before anything moves.

    calibrate.verify() has always been the ONLY check that can catch a
    mis-clicked corner (a 4-point homography is exact by construction, so its
    residual reads 0.000000 whether you clicked well or 150px off -- measured,
    see calibrate.solve). It now writes its answer down instead of printing a
    band and trusting the operator to remember, and this is the thing that
    reads it. Without a reader the record is just another file.

    The bands are scrub3d/handeye.py's GO_MM / HOVER_MM, which is the same
    decision the arm calibration gate makes: may this touch a person.

    ADVISORY ON A DRY RUN, LOUD WITH A REAL ARM. Nothing here blocks the
    demo -- the recovery card's answer to a dead camera must keep working on
    a laptop that never had a tape measure near it. What changes is that an
    operator can no longer reach a scrub with a no-go or never-checked
    calibration and be told nothing at all.
    """
    rec = calib.tape_check()
    if rec is None:
        why = ("no tape check has ever been recorded for this homography")
        verdict = "unchecked"
    else:
        verdict, why = rec.get("verdict", "unchecked"), rec.get("why", "")
    if verdict == "go":
        print(f"[calib] tape check GO ({rec.get('error_mm')}mm) -- {why}")
        return verdict
    bar = "!" * 64
    print(f"\n{bar}")
    print(f"  CALIBRATION TAPE CHECK: {verdict.upper()}")
    print(f"  {why}")
    if verdict == "hover":
        print("  The arm may move. It must NOT be allowed to touch a person:")
        print("    set contact_depth_mm to +20 in config.json (hover, not press)")
    else:
        print("  A mis-clicked corner is invisible to the solve itself.")
        print("  Before anything touches a person:")
        print("    rm homography.pkl && python py/calibrate.py")
        print("  and type the tape reading in when it asks.")
    if getattr(arm, "dry", False):
        print("  (dry run -- nothing physical will move either way)")
    print(f"{bar}\n")
    return verdict


# ------------------------------------------------------------ vision loop --
def vision_loop(arm, args):
    global RUNNING, ARMED, _armed_at
    from vision import PoseFeed          # lazy: see the import note at the top
    # The lunge limit is read off the MODULE, not imported by name, and with a
    # default. tests/test_consent_latch.py and tests/test_uv_fsm.py install a
    # stub `vision` module exporting only PoseFeed and the landmark indices, so
    # `from vision import MAX_TRACK_SPEED_PXS` is an ImportError that kills the
    # vision THREAD -- which is how 21 assertions across those two files failed
    # at once. Anything this loop needs from vision.py must degrade on a stub.
    import vision as _vision
    lunge_limit = getattr(_vision, "MAX_TRACK_SPEED_PXS", float("inf"))
    H = calib.load()
    _report_tape_check(arm)

    # UV dirt detection is OPTIONAL and hot-swappable. config.json
    # "dirt_mode": "scripted" (default) uses the fixed SPLOTCH_TS; "uv" uses
    # the real fluorescence detector. If the venue's lighting kills the
    # detector mid-setup, edit config.json and it switches WITHOUT a restart.
    #
    # THE THRESHOLDS ARE REFRESHED PER FRAME, BELOW. This tracker is built
    # ONCE, so the five dirt_* values it is constructed with are only the
    # STARTING point -- the comment above was true of dirt_mode and false of
    # every threshold until the refresh in the frame loop existed. Measured:
    # built at v_min=190, edited config.json to 250, CFG.reload() returned
    # True and reported 250, and the tracker still held 190. tune_dirt.py
    # tells the operator to paste these numbers under a real 365nm lamp; that
    # paste did nothing until a restart, in the one mode a judge asks about.
    import dirt as D
    tracker = D.DirtTracker(
        v_min=CFG.data.get("dirt_v_min", D.V_MIN),
        s_min=CFG.data.get("dirt_s_min", D.S_MIN),
        min_area=CFG.data.get("dirt_min_area", D.MIN_AREA),
        # tune_dirt.py prints these two; they were read by nothing.
        skin_lo=CFG.data.get("dirt_skin_hue_max", D.SKIN_HUE_MAX),
        skin_hi=CFG.data.get("dirt_skin_hue_min", D.SKIN_HUE_MIN))
    ei, wi = (R_ELBOW, R_WRIST) if args.right_arm else (L_ELBOW, L_WRIST)
    feed = PoseFeed(model_path=args.model, cam_index=args.cam,
                    mirror=CFG.data.get("mirror", True),
                    delegate=args.delegate)

    # THE scrub3d BODY MODEL, POSED BY THIS CAMERA. py/livebody.py holds the
    # whole rationale; the short version is that scrub3d/track.py has always
    # been able to pose a thirteen-region body from world JOINTS without any
    # depth sensor, and nothing in the demo had ever called it.
    #
    # OPTIONAL, AND THAT IS DELIBERATE. It needs numpy and the scrub3d package
    # beside this one, and --replay exists precisely for the machine where the
    # heavy stack has failed. A demo that cannot import this keeps every other
    # thing it does; it just stops publishing the body-tracking event. Same
    # rule the export_body import in _solve_live() follows.
    #
    # MEASURED on this Mac with CAM=fake: 11ms to build the body once, then
    # 1.12ms mean (11.1ms worst) per frame to re-pose it, against a 33ms frame
    # budget at 30fps. It runs inside the existing vision thread rather than
    # its own because at ~1ms it does not need one, and a second thread would
    # need its own copy of the pose to avoid tearing.
    live_body = None
    try:
        from livebody import LiveBody
        live_body = LiveBody()
        print(f"[body] scrub3d body model live: "
              f"{len(live_body.body.regions)} regions, built in "
              f"{live_body.build_s * 1000:.0f}ms — posed from the camera "
              f"every frame")
    except Exception as e:                                   # noqa: BLE001
        print(f"[body] scrub3d body tracking unavailable ({e}); "
              f"the demo runs without it")

    state, t_state = "IDLE", time.time()
    _last_pose, _last_pose_move = None, time.time()
    _lost_frames = 0

    while RUNNING:
        CFG.reload()                                  # edit config live
        scrub_dur = _num("scrub_seconds", 8.0)
        z = _num("forearm_z_mm", 45.0)
        frame, e, w, dt = feed.read(ei, wi)
        now = time.time()

        if frame is None:
            # CAMERA DROPOUT. `continue` here used to skip the cv2.waitKey at
            # the bottom of the loop, so the loop spun at 100% CPU, SPACE/estop
            # was NEVER READ, and the arm held its last target -- pressed into
            # a person's forearm at contact depth -- indefinitely. Found by an
            # adversarial audit.
            #
            # Retreat first, then keep servicing keys so the operator still has
            # an estop.
            if state in ("APPROACH", "SCRUB"):
                print("[fsm] camera lost -> RETREAT")
                state, t_state = "RETREAT", now
                ARMED = False
            arm.go_home()
            if not args.headless:
                k = cv2.waitKey(30) & 0xFF          # keys STILL work
                if   k == ord(' '): arm.estop(); ARMED = False
                elif k == ord('r'): arm.clear_estop(); state = "IDLE"; ARMED = False
                elif k == ord('q'): RUNNING = False; break
            else:
                time.sleep(0.03)                    # never spin
            continue

        if e and w and REC_FH:
            REC_FH.write(json.dumps(
                {"t": now, "elbow": list(e), "wrist": list(w)}) + "\n")

        # LUNGE GUARD (grafted from scrub3d/track.py's MAX_JUMP_MM, which puts
        # it plainly: the difference between tracking someone and lunging at
        # them). The person moved faster than anything a scrub should follow --
        # they stood up, flinched, or a second person crossed the frame and
        # MediaPipe jumped to them. Chasing that puts the sponge somewhere
        # nobody consented to.
        #
        # Only APPROACH and SCRUB retreat. IDLE has nothing to retreat from,
        # and RETREAT is already going home -- re-entering it would reset
        # t_state and restart the retreat timer every frame, so a person who
        # kept moving could hold the arm in RETREAT forever without it
        # finishing. Measured guard speed is 1250px/s against a fastest
        # observed normal motion of 289px/s, so this should never fire during
        # a real scrub; if it does on the day, the person moved.
        # getattr with a default for the same reason as lunge_limit above: a
        # stub feed has no guard, and a missing guard must mean "nothing to
        # report", never an AttributeError on the vision thread.
        if getattr(feed, "lunging", False) and state in ("APPROACH", "SCRUB"):
            print(f"[fsm] person moved "
                  f"{getattr(feed, 'last_speed_pxs', 0.0):.0f}px/s "
                  f"(limit {lunge_limit:.0f}) -> RETREAT")
            state, t_state = "RETREAT", now
            ARMED = False
            _lost_frames = 0
            arm.go_home()
            continue

        # ---- state machine ----------------------------------------------
        # ESTOP MUST STOP THE SOFTWARE TOO. arm.estop() halts the pump, but the
        # FSM used to sail on: SCRUB re-called set_target every frame, so the
        # moment the operator pressed 'r' the arm resumed mid-scrub from a
        # state nobody re-authorised. An estop must end the cycle, not pause
        # it. Verified: without this, clear_estop() was overwritten within one
        # frame by the SCRUB branch.
        # `abort_requested` covers the FAILED-estop case: `estopped` stays
        # False there so the pump can execute the retreat, but the FSM must
        # still stop -- otherwise SCRUB re-targets the forearm every frame and
        # overwrites the HOME retreat. Two correct behaviours combining into a
        # wrong one.
        # THE DEMO MUST NOT LIE ABOUT A SCRUB THAT DID NOT HAPPEN.
        # Measured by pulling the USB cable mid-scrub: the arm received ZERO
        # further commands while the FSM marched the counter 33 -> 67 -> 100
        # and the page threw confetti, with contact=True on every event -- so
        # the projector claimed torque-confirmed contact from an arm that was
        # not plugged in. arm.py knew (link_ok went False); nothing asked.
        #
        # DO NOT ABORT THE DEMO. A dead arm still leaves a working cartoon,
        # and that fallback is the whole resilience story. Fall back to IDLE,
        # say so on the projector, and let the operator drive 1/2/3 by hand.
        if not arm.link_ok and state != "IDLE":
            print("[fsm] ARM LINK LOST -> IDLE. The counter will NOT advance. "
                  "Check the USB cable; 1/2/3 still work by hand.")
            state, t_state = "IDLE", now
            ARMED = False
            # SAME CLEANUP AS EVERY OTHER EXIT TO IDLE. This gate runs BEFORE
            # the estop gate, so forcing IDLE here made the estop gate's own
            # `if state != "IDLE"` false and SKIPPED its reset entirely --
            # measured: pull the cable, then press SPACE, and _cleaned still
            # held [0.5]. That is the bug the estop reset was written to kill,
            # reintroduced through the path added beside it. Every route to
            # IDLE must leave the same clean slate.
            _reset_cycle_state(tracker)
            # TELL THE PROJECTOR. The operator is watching the kiosk-mode
            # projector on stage, not the OpenCV window behind it, so a
            # console line reaches nobody. Set AFTER the reset, because
            # _reset_cycle_state -> fire_reset() clears one-shot fields.
            with LOCK:
                EVENT["link"] = "arm-lost"

        # THE MESSAGE BELOW NAMES BOTH WINDOWS, and this comment sits ABOVE the
        # gate on purpose. `test_consent_latch.py:460` asserts
        # `_reset_cycle_state(tracker)` appears within 900 chars of the
        # `if arm.estopped` marker, and its own note says a 600-char window once
        # failed on correct code. Six lines of comment inside the block pushed
        # the call past the limit and reddened a correct guard. Keep prose out
        # of that window.
        #
        # Why the message changed: 'r' clears the estop in the PYTHON window
        # only. On the projector 'r' calls resetAll() and clears splotches, so
        # an operator reading this log line mid-failure presses a key that does
        # the wrong thing. The ARM REFUSED message already said "Press 'r' or
        # shift+C"; this one said only 'r'. Matched to it.
        if arm.estopped or arm.abort_requested:
            if state != "IDLE":
                print("[fsm] estopped -> IDLE (press 'r' here, or shift+C on "
                      "the projector, then 's' to resume)")
                state, t_state = "IDLE", now
                # CLEAR THE CYCLE STATE, exactly as RETREAT does. An estop
                # jumped straight to IDLE and cleared none of it, so _cleaned
                # survived into the NEXT volunteer's scrub: measured, after
                # x then r then s -- the recovery the card walks you through
                # -- _cleaned still held [0.34, 0.5], so the demo opened at
                # 67% before the arm touched anyone and only one splotch
                # could ever pop.
                _reset_cycle_state(tracker)
            ARMED = False
            if not args.headless:
                k = cv2.waitKey(20) & 0xFF
                if   k == ord('r'): arm.clear_estop()
                elif k == ord('q'): RUNNING = False; break
                elif k == ord(' '): arm.estop()
            else:
                time.sleep(0.02)
            continue

        # PUBLISH THE PHASE FROM THE LOOP, NOT FROM THE TRANSITIONS. There are
        # thirteen sites that assign `state`, and _reset_cycle_state's docstring
        # records what happens when one of several paths forgets a shared step:
        # a fourteenth transition added later would silently ship a stale phase.
        # Writing it once per frame, right before the dispatch, cannot desync --
        # it always reflects the state this frame is about to run. Every frame
        # reaches here, including the camera-dropout path, which retreats and
        # keeps servicing keys rather than skipping the dispatch.
        with LOCK:
            EVENT["phase"] = state

        # POSE THE scrub3d BODY FROM THIS FRAME. feed.world_mm is the 3D
        # skeleton MediaPipe already produced for the pixels above -- it costs
        # no extra inference, only the conversion, because the detection that
        # fills it is the same one the arm's elbow and wrist came from.
        #
        # EVERY FRAME, AND NOT ONLY WHILE SCRUBBING. The body tracker's own
        # guards depend on seeing consecutive frames: the lunge guard compares
        # against the previous frame, and a region the frame missed is held at
        # its last LIVE pose for a second before being called lost. Feeding it
        # only during a cycle would make every cycle's first frame a jump from
        # wherever the body sat last time, which is the exact failure
        # track.py's "a lost forearm went back to the scan pose" note records.
        #
        # ITS RESULT NEVER TOUCHES THE ARM. The arm's target is still the
        # pixel path and the plane homography, three lines up and unchanged.
        # This publishes a fact about the person for the projector to draw.
        # getattr with a default: the FSM tests install a stub `vision` module
        # whose PoseFeed has no world_mm, and an AttributeError here kills the
        # vision THREAD rather than failing loudly, so the whole file's
        # assertions go down together with no pointer to the cause. A feed that
        # cannot supply a 3D skeleton means "no body this frame", which the
        # tracker already handles -- it is not an error.
        if live_body is not None:
            _posed, _binfo = live_body.update(getattr(feed, "world_mm", {}),
                                              t=now)
            with LOCK:
                # STICKY, like "phase" and "solve" and for the same stated
                # reason: this is a CONDITION ("a body is being tracked"), not
                # an event. A projector that connects mid-demo must learn the
                # tracker is running rather than wait for the next change.
                EVENT["body"] = live_body.event()

        # THE PERSON'S OWN ARM JOINTS, plan §5 3.3. Published OUTSIDE the
        # live_body block above on purpose: that block is skipped entirely
        # when the scrub3d tracker failed to build (no numpy, no bake), and
        # the six landmarks below come straight from the MediaPipe detection
        # this frame already paid for. Tying them to the tracker would mean
        # a machine that can see a person but cannot pose a body model
        # reports no person at all.
        #
        # getattr with a default for the same reason the live_body call above
        # uses one: the FSM tests install a stub `vision` module whose
        # PoseFeed has no such method, and an AttributeError here kills the
        # vision THREAD rather than failing loudly.
        #
        # NOT STICKY, unlike "body" beside it. This one IS the person's
        # current pose, so a stale copy is a person standing where they are
        # not -- exactly what vision.read() clears world_mm to prevent ("a
        # stale pose that nothing overwrites ... would hand the body tracker
        # a person who left the room"). An empty dict is published when the
        # detection found nobody, and the page stops drawing them.
        _limbs = getattr(feed, "limb_event", None)
        if _limbs is not None:
            with LOCK:
                EVENT["limbs"] = _limbs() or None

        # HOW BIG THIS PERSON IS, from the same frame. Fed only the joints
        # that were MEASURED this tick (vision sets world_measured), so a
        # generic-human estimate can never be laundered into a measurement.
        # Published sticky: proportions do not change when somebody leans
        # out of frame, unlike the pose above.
        _wm = getattr(feed, "world_mm", None)
        if _wm:
            _measure_body(_wm, getattr(feed, "world_measured", set()))
            _b = _sample_body()
            if _b is not None:
                with LOCK:
                    EVENT["body"] = _b

        # SAME REASON, SAME PLACE: the gravity model may only learn from
        # samples taken while the sponge is provably not on a person. IDLE is
        # the only state with that guarantee -- APPROACH is already descending
        # toward a forearm. Fitting from a pressing sample would teach the
        # model that the press IS gravity and subtract the signal it exists to
        # detect, so this rides the one assignment that cannot desync from the
        # state the frame is about to run.
        CONTACT.note_idle(state == "IDLE")

        # THE CLOCKS, CHECKED EVERY FRAME AND NOT ONLY WHILE IDLE. This test
        # lived inside the `state == "IDLE"` branch below, which meant the
        # only clock the machine had stopped being read the instant the FSM
        # left IDLE. A cycle that reached APPROACH or SCRUB and stayed there
        # -- a pose the freshness watchdog has not yet called stale, an arm
        # holding through a long occlusion -- ran on a consent that nothing
        # could any longer expire.
        #
        # Moved out here, beside the phase publish and for the same reason
        # that is written once per frame: every frame reaches this line,
        # including the camera-dropout path, so no state can opt out of its
        # own expiry. consent_expired() consults BOTH clocks -- the 30s
        # "did anything start" and the 180s "is this over" -- so a session
        # that begins correctly is still bounded.
        lapsed, why = consent_expired()
        if lapsed:
            consent_clear("expired")
            print(f"[fsm] consent lapsed: {why} — press 's' again")
            if state != "IDLE":
                # A latch that expires mid-cycle must also STOP the cycle.
                # Clearing the flag alone would leave SCRUB re-targeting a
                # forearm every frame on consent that has run out, which is
                # the exact shape of the estop bug this file already
                # documents: two correct behaviours combining into a wrong
                # one. Retreat, as every other loss of authority does.
                print("[fsm] -> RETREAT (consent expired mid-cycle)")
                state, t_state = "RETREAT", now
                _reset_cycle_state(tracker)

        if state == "IDLE":
            arm.go_home()
            if ARMED and e and w:
                # MOTION BEGINS HERE, and the clocks change over with it.
                # APPROACH is the first state that calls set_target, so this
                # is the edge the arming timeout was measuring against; past
                # it the session budget is what bounds the cycle.
                consent_started()
                state, t_state = "APPROACH", now
                print("[fsm] armed + forearm detected -> APPROACH")

        elif state == "APPROACH":
            if not (e and w):
                state = "IDLE"
                ARMED = False
                continue
            mx, my = (e[0] + w[0]) / 2, (e[1] + w[1]) / 2
            x, y = calib.px_to_mm(H, mx, my)
            # TELL THE GOVERNOR WHERE THE PERSON IS BEFORE ASKING IT ABOUT A
            # TARGET. Its rule 4 measures the arm's structure against the
            # limb, so a stale limb would be judging this frame's pose
            # against last frame's person. Both endpoints in robot mm, on
            # the known skin plane -- the same numbers the target is built
            # from, so the two cannot disagree about where the arm is.
            if GOV is not None:
                GOV.set_limb(calib.px_to_mm(H, e[0], e[1]),
                             calib.px_to_mm(H, w[0], w[1]), z)
            # The approach comes straight down onto the limb, so the surface
            # normal is +z. The governor needs it only to know which way a
            # retreat would be.
            govern_target(arm, x, y, z + 70.0, t=now,   # hover
                          normal=(0.0, 0.0, 1.0))
            if now - t_state > 1.5:
                state, t_state = "SCRUB", now
                print("[fsm] -> SCRUB")

        elif state == "SCRUB":
            if not (e and w):
                # ONE occluded frame must not abort the cycle. The visibility
                # gate returns None whenever a landmark is uncertain, and the
                # robot arm occludes the very forearm it is scrubbing -- so
                # brief dropouts are CONSTANT and expected. Aborting on the
                # first one meant a normal scrub ended a second in, retreated,
                # and disarmed, requiring the operator to re-arm on stage.
                #
                # Tolerate a short gap; abort only on a sustained loss.
                abort, _lost_frames = should_abort_scrub(_lost_frames)
                if abort:
                    print(f"[fsm] person lost for {_lost_frames} frames "
                          f"-> RETREAT")
                    state, t_state = "RETREAT", now
                    ARMED = False
                    _lost_frames = 0
                else:
                    # hold, do not chase. ASK THE ARM TO HOLD; DO NOT
                    # REPLAY ITS TARGET BACK AT IT. This was
                    # `set_target(*arm.target)`, which only works while
                    # target happens to be the same shape set_target takes
                    # -- true on the RoArm's (x,y,z,t), a TypeError on the
                    # OpenYAM's six joint radians. This line runs on the
                    # first occluded frame of every scrub, so it would have
                    # killed the vision thread with the sponge on a person.
                    arm.hold()
                continue
            _lost_frames = 0

            # POSE FRESHNESS WATCHDOG. A camera can wedge and hand back the
            # same stale frame forever: landmarks stay "valid", so the checks
            # above pass while the sponge presses a position the person left.
            # If the target has not MOVED at all for 2s, treat it as stale.
            if _last_pose is not None and \
               abs(e[0]-_last_pose[0]) + abs(e[1]-_last_pose[1]) < 0.01:
                if now - _last_pose_move > 2.0:
                    print("[fsm] pose frozen 2s (camera wedged?) -> RETREAT")
                    state, t_state = "RETREAT", now
                    ARMED = False
                    continue
            else:
                _last_pose_move = now
            _last_pose = e

            el = now - t_state

            # Compute the forearm axis in MILLIMETRES (not pixels) by
            # transforming two points, so the oscillation amplitude is real mm.
            emm = calib.px_to_mm(H, e[0], e[1])
            wmm = calib.px_to_mm(H, w[0], w[1])
            mid = ((emm[0] + wmm[0]) / 2, (emm[1] + wmm[1]) / 2)
            ax, ay = wmm[0] - emm[0], wmm[1] - emm[1]
            Lmm = math.hypot(ax, ay) or 1.0
            ux, uy = ax / Lmm, ay / Lmm
            # Stay off the elbow and the wrist bone: cap excursion at 80% of
            # the half-length.
            half = (Lmm / 2.0) * 0.8

            off = motion.scrub_offset(
                el, scrub_dur,
                freq_hz=_num("scrub_hz", 1.2),
                # abs(): min() on a SIGNED value lets a negative
                # scrub_amp_mm in config.json bypass the 80%-half-length cap
                # entirely (-500 is "less than" half). Cap the MAGNITUDE.
                amp_mm=min(abs(_num("scrub_amp_mm", 35.0)), half))

            # CONTACT DEPTH is config, not a constant, because the team has
            # not settled contact-vs-hover (see DIRECTIVE.md §0c conflict #2).
            #   contact_depth_mm = -5  -> presses 5mm INTO the skin plane; the
            #       sponge compresses 10-15mm and absorbs the calibration error
            #       budget, and torque feedback gates the splotch pops.
            #   contact_depth_mm = +20 -> HOVERS 20mm above. Safer and matches
            #       the brainstorm's "hovering above the body, not touching",
            #       but there is then no contact to sense: run with
            #       --no-contact-gate or the splotches never pop.
            depth = _num("contact_depth_mm", -5.0)
            # The limb moves under the sponge for the whole scrub, so the
            # governor is re-told where it is every frame. emm/wmm are the
            # elbow and wrist already converted to robot mm just above, so
            # this costs one KD-tree build (0.05ms measured) and no extra
            # transform that could disagree with the target's.
            if GOV is not None:
                GOV.set_limb(emm, wmm, z)
            # A REFUSED SCRUB TARGET MUST NOT LEAVE THE ARM CHASING. When
            # the governor says no, hold the last pose rather than fall
            # through and let the next frame try again from a pose the arm
            # never reached -- arm.hold() is what the occlusion branch above
            # uses for exactly this, and it is the one call that works on
            # both drivers.
            if not govern_target(arm, mid[0] + ux * off, mid[1] + uy * off,
                                 z + depth, t=now, normal=(0.0, 0.0, 1.0)):
                arm.hold()

            # u in [0,1] along the forearm, matching the cartoon's splotch t.
            u = 0.5 + (off / (2.0 * half)) if half else 0.5
            # AND SEND IT. This is the sponge's real position on the real
            # forearm and the cartoon needs it to put its own sponge in the
            # matching place -- without it the page animates the scrub with an
            # invented decaying roll that rocks in place while this sweeps.
            # Clamped because the page maps it straight onto a limb: `off` is
            # capped at `half` so u cannot leave [0,1] today, but a config edit
            # is what widens the amplitude and a u of 1.4 would put the sponge
            # off the end of the cartoon's hand.
            with LOCK:
                EVENT["scrub_u"] = round(min(max(u, 0.0), 1.0), 4)

            # REFRESH THE THRESHOLDS FROM CONFIG, EVERY FRAME. Assign the
            # attributes rather than rebuilding DirtTracker: a rebuild would
            # wipe initial_area, the latched spots and the cleanliness
            # baseline mid-cycle, so the counter would jump backwards the
            # moment anyone saved the file. These five are plain attributes
            # read by observe()/fluor_mask() on each call, so assignment is
            # all that is needed.
            # COERCE. A string or null in any of these five keys raises
            # UFuncTypeError/TypeError inside fluor_mask -- and this runs every
            # frame INSIDE vision_loop, so it would kill the vision thread
            # mid-scrub. Before the per-frame refresh these were read once at
            # startup, where a bad edit surfaced before anyone was watching;
            # making them hot widened the blast radius to exactly the file
            # config.json advertises as "HOT-RELOADS -- edit mid-demo if
            # needed". Found by an adversarial re-read of my own diff.
            # _num() is module scope now; see its docstring for why.
            tracker.v_min = _num("dirt_v_min", D.V_MIN)
            tracker.s_min = _num("dirt_s_min", D.S_MIN)
            tracker.min_area = _num("dirt_min_area", D.MIN_AREA)
            tracker.skin_lo = _num("dirt_skin_hue_max", D.SKIN_HUE_MAX)
            tracker.skin_hi = _num("dirt_skin_hue_min", D.SKIN_HUE_MIN)

            # dirt_mode(), not a bare equality on the raw value: "UV" and
            # "uv " used to compare False and run scripted in silence. See the
            # helper above for why that is the one failure the demo forbids.
            if dirt_mode() == "uv":
                # MEASURED cleanliness. THREE things this must not do:
                #
                # 1. Pop on a single missing frame. The robot arm OCCLUDES the
                #    exact region it is scrubbing, so a spot disappears from
                #    view constantly. An earlier version popped the instant a
                #    spot was not detected, which meant every splotch popped on
                #    the first pass whether or not anything was cleaned.
                #    -> require the sponge over it, with nothing visible there,
                #       for UV_GONE_FRAMES consecutive frames (dwell, not
                #       absence -- see uv_should_pop).
                # 2. Match against hardcoded SPLOTCH_TS. Real tracer lands
                #    where it lands. Latch the positions actually DETECTED on
                #    the first frames, and track those.
                # 3. Pop while the sponge is not near it. A spot vanishing
                #    because someone's sleeve moved is not cleaning.
                spots = tracker.observe(frame, e, w)
                # tracker.cleanliness_pct() is NOT read here any more -- the
                # counter is the count of positions cleaned, computed at the
                # fire site below. observe() above still supplies `spots`.
                live = [sp["t"] for sp in spots]

                # LATCH THE REAL SPOT POSITIONS, but not naively.
                #
                # sorted(live)[:3] was wrong two ways, both measured:
                #   - ONE big blob (tracer smeared, or the camera sees it as a
                #     single region) latched ONE target, so the counter hit
                #     100% after a single pop with the arm visibly still dirty.
                #   - FIVE blobs latched only the three LOWEST t values, so any
                #     tracer nearer the wrist was silently ignored and could
                #     never be cleaned.
                # Instead: take up to len(SPLOTCH_TS) spots SPREAD along the
                # limb, and refuse to latch until the scene looks stable.
                if not _uv_targets and live:
                    _uv_stable.append(sorted(live))
                    if len(_uv_stable) >= UV_LATCH_FRAMES:
                        counts = [len(f) for f in _uv_stable]
                        if max(counts) - min(counts) <= 1:      # stable count
                            picked = _spread_pick(_uv_stable[-1],
                                                  len(SPLOTCH_TS))
                            _uv_targets.extend(picked)
                            _uv_dwell.update({t: 0 for t in _uv_targets})
                            print(f"[fsm] UV latched {len(_uv_targets)} of "
                                  f"{len(_uv_stable[-1])} spots at "
                                  f"t={[round(t,2) for t in _uv_targets]}")
                            # TELL THE BROWSER WHERE THE REAL TRACER IS.
                            # UV latches wherever the tracer actually landed,
                            # but the page places splotches at the fixed
                            # SPLOTCH_TS and matches within 0.07 -- so tracer
                            # at, say, 0.42 fired an event the browser
                            # silently DROPPED: the counter climbed while the
                            # splotch stayed on screen. Now the page moves its
                            # splotches to the detected positions.
                            with LOCK:
                                EVENT["place"] = [round(t, 3)
                                                  for t in _uv_targets]
                        else:
                            _uv_stable.clear()   # scene still settling

                for st in list(_uv_targets):
                    if st in _cleaned:
                        continue
                    pop_now, _uv_dwell[st] = uv_should_pop(
                        st, live, u, _uv_dwell.get(st, 0))
                    if pop_now:
                        _cleaned.add(st)
                        # COUNT, NOT AREA -- and computed HERE, after the add.
                        #
                        # This path used to send cleanliness_pct(), i.e.
                        # 1 - current_area/initial_area, while the scripted path
                        # below sent the count. Two numbers for the same physical
                        # event. They agreed only by accident of the fixture: the
                        # synthetic tracer draws three blobs at identical radius,
                        # measured through the real detector as areas
                        # [609, 609, 609], spread 0. Real tracer is not
                        # equal-area, so one big blob would have read as most of
                        # the progress here and as one third in scripted mode.
                        #
                        # It also drops the last dirt assumption from the
                        # projector: cleanliness_pct() divides by a RUNNING
                        # MAXIMUM of dirt ever seen (dirt.py:190), so the
                        # denominator grows mid-scrub when more tracer comes into
                        # view, and it returns 100.0 when nothing was observed at
                        # all (dirt.py:147). Fluorescence still decides WHETHER a
                        # position got clean -- seeing tracer resets the dwell
                        # counter in uv_should_pop -- it just no longer sets the
                        # number on screen.
                        #
                        # AFTER _cleaned.add(st), matching the scripted path.
                        # Reading the count before the add fires [0, 33, 67]
                        # instead of [33, 67, 100]: the demo would end at 67%
                        # with every splotch gone, and a check that only asserts
                        # "reached 100 at some point" would pass it.
                        _pct = 100 * len(_cleaned) / len(_uv_targets)
                        fire("forearm_L", st, _pct, arm.contact)
                        print(f"[fsm] UV spot cleaned "
                              f"{len(_cleaned)}/{len(_uv_targets)} "
                              f"clean={_pct:.0f}%")
            elif arm.contact or args.no_contact_gate:
                # Key _cleaned by POSITION, not index, so the UV path (which
                # tracks detected positions) and this one cannot disagree
                # about what "already popped" means.
                #
                # TARGET WHERE THE BROWSER'S SPLOTCHES ACTUALLY ARE, which is
                # not always SPLOTCH_TS. The recovery card tells you to flip
                # dirt_mode to "scripted" when the detector finds nothing, and
                # that hot-reloads mid-scrub. If UV had already latched and
                # sent a "place" event, the page moved its splotches to the
                # DETECTED positions -- so firing the hardcoded defaults here
                # matched nothing (0.07 window) and left every splotch on
                # screen with the counter at 100%. Measured: browser at
                # 0.115/0.254/0.756, fired 0.34/0.50/0.66, 3 orphans.
                targets = _uv_targets if _uv_targets else SPLOTCH_TS
                for st in targets:
                    if st not in _cleaned and abs(st - u) < 0.10:
                        _cleaned.add(st)
                        fire("forearm_L", st,
                             100 * len(_cleaned) / len(targets), True)
                        print(f"[fsm] splotch {len(_cleaned)}/{len(targets)}")

            if el > scrub_dur:
                # SWEEP THE POSITIONS THE BROWSER ACTUALLY HAS. In UV mode the
                # page moved its splotches to the DETECTED positions, so
                # firing the hardcoded SPLOTCH_TS here sent events that matched
                # nothing (0.07 window) -- the finale left splotches on screen
                # with the counter at 100%.
                # Keyed on what the browser WAS TOLD, not on the current
                # dirt_mode: the mode can be flipped mid-scrub (recovery card)
                # and the page keeps the splotch positions it was given.
                finale_ts = _uv_targets if _uv_targets else SPLOTCH_TS
                for t_pos in finale_ts:
                    if t_pos not in _cleaned:
                        _cleaned.add(t_pos)
                        fire("forearm_L", t_pos, 100, True)
                state, t_state = "RETREAT", now
                ARMED = False                     # one cycle per keypress
                print("[fsm] -> RETREAT (100%) — press 's' to arm again")

        elif state == "RETREAT":
            arm.go_home()
            if now - t_state > 2.5:
                state = "IDLE"
                tracker.reset()
                _uv_targets.clear()
                _uv_dwell.clear()
                _uv_stable.clear()
                fire_reset()

        # ---- debug window + keys ----------------------------------------
        if not args.headless:
            if e and w:
                cv2.circle(frame, (int(e[0]), int(e[1])), 8, (0, 255, 0), -1)
                cv2.circle(frame, (int(w[0]), int(w[1])), 8, (0, 200, 255), -1)
                cv2.line(frame, (int(e[0]), int(e[1])),
                         (int(w[0]), int(w[1])), (255, 255, 0), 2)
            cv2.putText(frame,
                        f"{state}  {'ARMED' if ARMED else 'SAFE'}"
                        f"  clean={len(_cleaned)}/{len(SPLOTCH_TS)}"
                        f"  contact={arm.contact}"
                        f"{'  ESTOP' if arm.estopped else ''}",
                        (12, 30), cv2.FONT_HERSHEY_SIMPLEX, .7,
                        (0, 0, 255) if arm.estopped else (0, 255, 255), 2)
            cv2.imshow("scrubbot", frame)
            k = cv2.waitKey(1) & 0xFF
            if   k == ord(' '): arm.estop(); state = "IDLE"; ARMED = False
            elif k == ord('r'): arm.clear_estop(); state = "IDLE"; ARMED = False
            elif k == ord('h'): arm.go_home()
            elif k == ord('s'):
                # ARM ONLY. Never jump straight into SCRUB.
                #
                # The old version did `state = "SCRUB"` when pressed outside
                # IDLE, which SKIPS THE APPROACH HOVER -- the arm would
                # descend to contact depth without first travelling above the
                # target, and a second press mid-scrub restarted the timer so
                # the cycle never ended. Arming is a request; the state
                # machine decides when to move, and it always hovers first.
                #
                # STAMP FIRST, THEN LATCH -- see consent_press(). That
                # ordering used to be spelled out here and again at the
                # projector's `arm` command, fifteen identical lines in two
                # places, and it is now written once inside the latch itself.
                consent_press("operator key")
                print(f"[fsm] ARMED ({state}) — put a forearm in frame "
                      f"({ARM_TIMEOUT_S:.0f}s to start, "
                      f"{SESSION_BUDGET_S:.0f}s total)")
            elif k in (ord('1'), ord('2'), ord('3')): pop(k - ord('1'))
            # THE SAME REQUEST THE PROJECTOR'S 'v' MAKES, through the same flag
            # and the same worker. Not a second entry point into the solver:
            # both set _SOLVE_REQUEST and remote_control_loop is the only thing
            # that acts on it, so the OpenCV window and the browser cannot
            # start two solves or disagree about what one did.
            elif k == ord('v'): _SOLVE_REQUEST.set()
            elif k == ord('q'): RUNNING = False; break

    feed.close()


def remote_control_loop(arm):
    """Honour ARM/ESTOP requests from the projector page.

    Its OWN thread, not inside vision_loop: --replay and --scripted never run
    vision_loop, so handling it there meant those modes could not be armed or
    estopped from the projector at all -- and --scripted is the fallback most
    likely to be running when tracking has failed. Caught by a test.
    """
    global ARMED, _armed_at
    while RUNNING:
        time.sleep(0.05)
        # ESTOP WINS, ALWAYS. Draining clear-then-estop in a fixed order let a
        # clear queued microseconds BEFORE an estop undo it: both flags are
        # set by the time this loop looks, and the old order cleared first,
        # then estopped -- but the reverse pairing (estop then clear in the
        # same tick) silently RELEASED the stop. Handle estop first and drop
        # any clear that arrived in the same tick.
        if _ESTOP_REQUEST.is_set():
            _ESTOP_REQUEST.clear()
            _CLEAR_REQUEST.clear()      # a clear in this tick loses
            ok = arm.estop()
            # TELL THE PROJECTOR WHAT ACTUALLY HAPPENED. It used to flash a
            # green "ESTOP SENT" the instant the key was pressed, so a FAILED
            # estop -- the case where the operator must cut power -- looked
            # exactly like a successful one.
            with LOCK:
                EVENT["ack"] = {"cmd": "estop", "ok": bool(ok)}
            ARMED = False
        elif _CLEAR_REQUEST.is_set():
            _CLEAR_REQUEST.clear()
            # `abort_requested` (a FAILED estop) must be clearable too, or the
            # FSM stays gated forever with no way back from the projector.
            # UNCONDITIONAL. A verifier swept 30 timing offsets and found
            # that in 25/30 races the HARDWARE stays latched while
            # arm.estopped reads False -- and this guard then refused to
            # re-send T:999, leaving the arm frozen at the contact pose with
            # the projector's recovery key dead. There is no cost to sending
            # T:999 when nothing is stopped.
            ok = arm.clear_estop()
            arm.abort_requested = False
            with LOCK:
                EVENT["ack"] = {"cmd": "clear", "ok": bool(ok)}
            ARMED = False               # clearing never re-arms
        if _ARM_REQUEST.is_set():
            _ARM_REQUEST.clear()
            # REFUSE TO ARM WHILE STOPPED, and SAY SO.
            #
            # This used to latch ARMED unconditionally. The vision FSM then
            # clamped it straight back to False (the estop gate), so nothing
            # moved -- but the PROJECTOR had already flashed a green ARMED and
            # started a full 9.8s scrub animation. The operator, who is
            # watching the projector, believes the resume worked and stops
            # reaching for shift+C; the audience watches a cartoon arm scrub a
            # volunteer's forearm while the machine is emergency-stopped.
            #
            # The demo script sells the estop as "it refuses to lie". This was
            # the one place it did.
            if arm.estopped or arm.abort_requested:
                with LOCK:
                    EVENT["ack"] = {"cmd": "arm", "ok": False}
                print("[fsm] ARM REFUSED — still estopped. Press 'r' or "
                      "shift+C to clear first.")
                continue
            # STAMP FIRST, THEN LATCH -- see consent_press(). One latch for
            # the whole machine, pressed identically from the key and from
            # the projector, so the two entry points cannot drift.
            consent_press("projector")
            print(f"[fsm] ARMED (from the projector) — put a forearm in frame "
                  f"({ARM_TIMEOUT_S:.0f}s to start, "
                  f"{SESSION_BUDGET_S:.0f}s total)")
        if _SOLVE_REQUEST.is_set():
            _SOLVE_REQUEST.clear()
            # ITS OWN THREAD, not this one. This loop is the only thing that
            # honours ESTOP from the projector, and a solve blocks for seconds
            # (3.8 measured, and unbounded if the placement is ever changed) --
            # running it inline would make the page's emergency stop
            # unreachable for that whole window, on the one path the demo
            # script sells as always working. A solve must never be able to
            # delay a stop.
            #
            # daemon=True so a solve in flight cannot hold the process open
            # after 'q'; the worst case is a half-written .partial file, which
            # the atomic rename in export_one means no reader ever sees.
            threading.Thread(target=_solve_live, daemon=True).start()


# The per-joint cutout thresholds MOVED INTO THE DRIVERS (arm.TORQUE_ESTOP,
# openyam.ESTOP_TAU) and this loop asks arm.over_torque() instead of naming
# joints itself. They had to move: the names torS/torE/torB/torH and the
# numbers 600-900 are RoArm firmware units, so against any other arm every
# check compared 0 to the limit, passed, and left the watchdog running
# forever having checked nothing -- no exception, no log, no cutout, and code
# that still looked like it had one. Thresholds belong with the arm that
# defines the units.


def feedback_loop(arm):
    """Torque watchdog. Spike = contact = a free safety cutout, and an honest
    'the sponge is really touching' signal for the counter.

    RATE: this used to sleep(0.1) and claim ~10Hz, but poll_feedback() has its
    own 50ms sleep waiting for the reply, so the real rate was 6.1 Hz --
    measured -- giving a 163ms worst-case detection latency. Sleeping less here
    puts it near 10Hz for real. Do NOT go much faster: the ~200-byte replies
    would eat the serial headroom the 40Hz command stream needs.
    """
    while RUNNING:
        time.sleep(0.045)                 # + ~55ms inside poll_feedback
        fb = arm.poll_feedback()
        if not fb:
            continue
        # POSE-INDEPENDENT CONTACT, once it has fitted itself. This runs on the
        # sample the watchdog already polled -- no extra serial -- and until the
        # gravity model fits it returns None and changes nothing, so arm.contact
        # keeps whatever arm.py's fixed threshold set. See py/contact.py for why
        # the threshold is wrong (it fires free-air at full extension) and why
        # the model has to fit itself rather than ship coefficients.
        out = CONTACT.update(fb)
        if out is not None:
            arm.contact = CONTACT.contact_from(out)
        hit = arm.over_torque()
        if hit:
            joint, v, limit = hit
            print(f"[safety] {joint} torque {v} > {limit} -> ESTOP")
            arm.estop()      # the vision loop sees arm.estopped and goes IDLE


def replay_loop(path, arm):
    from replay import replay_source
    print(f"[replay] {path} -- no camera, no vision")
    with LOCK:
        EVENT["mode"] = "replay"
        # AND SAY THERE IS NO CYCLE. The browser sets its cycle flag on the
        # `s` keypress and clears it on a phase change, a pop, a reset or a
        # dropped socket -- none of which replay ever sends. Without this the
        # projector's corner read CYCLE RUNNING, green, counter at 0%, for the
        # whole demo, on the fallback the recovery card sends you to when the
        # camera is dead.
        EVENT["phase"] = "IDLE"
    # THE RECOVERY CARD'S CAMERA-DEAD ANSWER MUST NOT NEED A CALIBRATION.
    # "Camera dead / black -> REPLAY=recordings/synthetic.jsonl ./run.sh, 10s"
    # is the headline fallback, and calib.load() raises SystemExit when
    # homography.pkl is missing. Measured on a machine without one: it prints
    # "[replay] ... no camera, no vision" -- looking like it started -- and
    # then dies with "No homography.pkl". A fresh laptop, a wiped checkout, or
    # a tripod recalibration in progress all hit this.
    #
    # Replay feeds RECORDED pixels, so with --no-arm nothing physical moves
    # and the projector is the whole point. Continue with an identity map and
    # SAY SO. With a real arm attached we still refuse: a fabricated
    # calibration would put the sponge ~30cm off a person's forearm, which is
    # exactly what calib.load()'s sentinel exists to prevent.
    try:
        H = calib.load()
    except SystemExit:
        if not arm.dry:
            raise
        # Map the frame onto the CENTRE of the workspace, not identity.
        # Identity sends 640x480 pixel values straight through as millimetres,
        # every one of them outside the box, and the pump printed a clamp
        # warning per frame -- burying the exact line the recovery card tells
        # the operator to watch for. This keeps the log readable.
        import numpy as _np
        import calibrate as _cal
        _B = _cal.BOX if hasattr(_cal, "BOX") else None
        _sx, _sy = 200.0 / 640.0, 200.0 / 480.0
        H = _np.array([[_sx, 0.0, 170.0],
                       [0.0, _sy, -100.0],
                       [0.0, 0.0, 1.0]], dtype=_np.float32)
        print("[replay] NO CALIBRATION — screen-only replay (arm is disabled).")
        print("[replay]   The cartoon is correct; robot coordinates are not.")
        print("[replay]   Run `python py/calibrate.py` before using a real arm.")
    z = _num("forearm_z_mm", 45.0)
    for row in replay_source(path):
        if not RUNNING:
            return
        e, w = row.get("elbow"), row.get("wrist")
        # REPLAY HAS AN ESTOP GATE TOO. It never runs vision_loop, so
        # pressing the emergency stop parked the pump while the replay kept
        # feeding targets -- and clearing the estop resumed the recorded
        # motion instantly, from wherever the recording had reached.
        if arm.estopped or arm.abort_requested:
            continue
        if e and w:
            mx, my = (e[0] + w[0]) / 2, (e[1] + w[1]) / 2
            x, y = calib.px_to_mm(H, mx, my)
            # REPLAY GOES THROUGH THE GOVERNOR TOO. It drives a real arm at
            # real targets -- the estop gate directly above exists for the
            # same reason -- so exempting it would leave the mode most
            # likely to run unattended as the one with no safety layer.
            if GOV is not None:
                GOV.set_limb(calib.px_to_mm(H, e[0], e[1]),
                             calib.px_to_mm(H, w[0], w[1]), z)
            govern_target(arm, x, y, z, t=time.time(),
                          normal=(0.0, 0.0, 1.0))


# ------------------------------------------------------------------ main --
def disarm_signals():
    """Make the shutdown retreat un-interruptible by another signal.

    A REAL FUNCTION, not an inline loop, so a test can CALL it. The inline
    version was covered by grepping the source for "signal.SIG_IGN" -- and a
    plant that emptied the loop kept the string, so the check passed on code
    that disarmed nothing. A guard that tests for text cannot tell a live loop
    from an empty one.

    Why it exists: _term_handler stays installed and still raises, so a SECOND
    signal -- the impatient operator hitting ^C again when the arm does not
    visibly move, or re-running pkill -- lands INSIDE main()'s finally and
    aborts the retreat. Measured: a second SIGTERM at 0.05s and at 0.30s both
    produced no stop and no clean shutdown, with the arm only z~50-65mm into
    the 0.4s retreat. The skin plane is z=45, so the sponge is STILL on the
    volunteer's forearm -- the exact end state the SIGTERM fix eliminated,
    reached through the same key the operator is already pressing.

    SIGKILL still wins. Nothing can change that.
    """
    n = 0
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            signal.signal(sig, signal.SIG_IGN)
            n += 1
        except (ValueError, OSError):
            pass              # not the main thread, or unsupported here
    return n


def _term_handler(signum, _frame):
    """SIGTERM MUST RETREAT THE ARM. It did not, and that is the worst bug
    this project has had.

    The shutdown retreat lives in main()'s `finally:`. Python's default SIGTERM
    disposition terminates the interpreter WITHOUT unwinding, so no `finally`
    and no `atexit` ever runs. Every non-interactive stop path sends SIGTERM:
    run.sh's cleanup() traps EXIT/INT/TERM and does `kill -TERM` on each child,
    README says to use SIGTERM for the background case, and scrubbot itself
    PRINTS `pkill -f 'py/scrubbot.py'` to the operator -- pkill defaults to
    SIGTERM -- at exactly the moment they are already scrambling.

    MEASURED against the pty simulator with the arm at the demo contact pose
    (300,120,40 = 5mm into the skin plane):

        SIGTERM -> finally ran: False, T:0 stops: 0, final pos z=40.0  (parked)
        SIGINT  -> finally ran: True,  T:0 stops: 1, final pos z=123.5 (lifted)

    DO NOT read that second row as "SIGINT is safe by default" -- that reading is
    why SIGINT went unregistered for weeks, and it is load-dependent. Python
    defers the default KeyboardInterrupt while the main thread is inside a C
    call, and this loop lives in cv2.waitKey and pyserial reads. Under a loaded
    suite the same pty test inverted completely: SIGINT gave 0 stops, `finally`
    never ran, 480 commands went out after the signal, and the arm ended at
    z=40.0 -- parked on the contact plane -- while SIGTERM and SIGHUP lifted to
    z=123.5 on that same run. In isolation it still passes 3/3. main() now
    registers all three signals on this handler so none of them depends on
    interpreter timing.

    The process vanishes, taking the 40Hz pump, the torque watchdog and the
    estop key with it, while the ESP32 holds its last commanded pose forever.
    The sponge keeps pressing a person's forearm and NOTHING in software can
    lift it -- the only recovery left is cutting the 12V supply, which the
    recovery card lists as a last resort, not a routine shutdown.

    Raising KeyboardInterrupt reuses the retreat path that already works and
    is already tested, rather than duplicating it.
    """
    raise KeyboardInterrupt


def main():
    global RUNNING, REC_FH, ARMED, _armed_at
    # Install BEFORE anything can move the arm.
    signal.signal(signal.SIGTERM, _term_handler)
    # SIGHUP too: closing the terminal a demo was launched from is the same
    # class of accident, and run.sh traps HUP alongside TERM.
    signal.signal(signal.SIGHUP, _term_handler)
    # SIGINT EXPLICITLY, even though Python's default disposition already
    # raises KeyboardInterrupt. That default is DEFERRED while the main thread
    # sits in a C call -- and this loop lives in cv2.waitKey and pyserial
    # reads. Measured under a loaded suite: SIGINT gave 0 stops, finally never
    # ran, and the arm ended at z=40.0 -- the contact plane, sponge parked on
    # the forearm -- while SIGTERM and SIGHUP retreated to z=123.5 on the same
    # run. In isolation the same test passes 3/3, which is what a deferred
    # signal looks like: fine until the machine is busy, and the machine is
    # busiest at a demo. An explicit handler is delivered through the same C
    # call the default one waits behind, so all three signals now take the
    # identical path instead of one depending on interpreter timing.
    signal.signal(signal.SIGINT, _term_handler)
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default=os.environ.get("REPLAY"))
    ap.add_argument("--record")
    ap.add_argument("--no-arm", action="store_true")
    ap.add_argument("--no-contact-gate", action="store_true",
                    help="pop splotches on position alone (torque flaky)")
    ap.add_argument("--scripted", action="store_true",
                    help="canned routine, no camera, taped-X forearm")
    ap.add_argument("--auto-arm", action="store_true",
                    help="skip the consent latch in --scripted (bench testing "
                         "with NOBODY under the arm)")
    # THE NAME IS INVERTED UNDER THE SHIPPED CONFIG, AND THAT IS NOT A BUG HERE
    # -- it is what `mirror` does. MediaPipe reads left/right from the IMAGE, so
    # with `mirror: true` (shipped) the L_* pair at :422 lands on the image-LEFT
    # limb, which is the subject's RIGHT arm. Passing this flag selects R_*, the
    # image-RIGHT limb = the subject's LEFT arm. Measured all four cells of
    # wave_left x mirror: a volunteer waving their RIGHT arm under mirror:true
    # moves landmark 15 (L15 112px vs R16 8px), the pair you get WITHOUT this
    # flag. tests/test_mirror.py proves the label swap but never runs that cell,
    # and nothing in the repo ever sets this True, so the branch is untested.
    # Operator rule that does not require knowing any of the above: wave one arm
    # and check the dots land on it; if not, flip `mirror` in config.json.
    ap.add_argument("--right-arm", action="store_true",
                    help="read the R_* landmark pair. Under the shipped "
                         "mirror:true this targets the subject's LEFT arm; the "
                         "default already targets their RIGHT. Verify by "
                         "waving, not by the name.")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--delegate", default="GPU", choices=["GPU", "CPU"])
    ap.add_argument("--model",
                    default=os.path.join(os.path.dirname(__file__), "..",
                                         "models",
                                         "pose_landmarker_full.task"))
    ap.add_argument("--port", default=CFG.data.get("serial_port"),
                    help="serial device for --arm roarm, or the SocketCAN "
                         "interface (e.g. can0) for --arm openyam")
    # `choices=` IS LOAD-BEARING. A typo'd ARM=openyarm must fail at argv
    # parse, with nobody under the sponge, rather than falling through to
    # the RoArm silently -- which is exactly the failure dirt_mode()
    # documents at :437-448. Env default matches --replay's pattern at
    # :1219 so `ARM=openyam ./run.sh` works with no flag.
    #
    # DEFAULT STAYS roarm. Nothing that works today changes behaviour;
    # the OpenYAM has never driven a motor and must be asked for by name.
    ap.add_argument("--arm",
                    default=os.environ.get("ARM",
                                           CFG.data.get("arm_kind", "roarm")),
                    choices=["roarm", "openyam"],
                    help="which arm driver. roarm = RoArm-M2-S over USB "
                         "serial; openyam = Anvil OpenYAM over SocketCAN.")
    ap.add_argument("--no-governor", action="store_true",
                    help="skip scrub3d's safety governor. The arm then has "
                         "only arm.py's box clamp and torque cutout, which "
                         "is how the demo ran before the governor was wired "
                         "in. For isolating a governor problem on stage.")
    args = ap.parse_args()

    # The live measurement opens its own capture (see _measure_person), so it
    # has to be told which one. Same camera and same model the vision loop
    # uses, set here because the solve thread never sees args.
    global _MEASURE_CAM, _MEASURE_MODEL
    _MEASURE_CAM, _MEASURE_MODEL = args.cam, args.model

    # --no-arm IMPLIES --no-contact-gate, because there is no contact to sense.
    #
    # The pop check at the SCRUB branch is gated on `arm.contact`, which comes
    # from the torque watchdog on a real servo. With --no-arm there is no
    # servo, so it is false for the entire scrub and NOT ONE splotch pops while
    # the arm is working: all three get dumped by the end-of-cycle finale in a
    # single socket frame, and the counter's whole run from 0 to 100 happens in
    # one tween. Measured on the exact command seven docs give as the demo:
    # 0 in-scrub pops without this line, 12 with it.
    #
    # The comment at the contact_depth_mm block already said "run with
    # --no-contact-gate or the splotches never pop", and RECOVERY-CARD.md lists
    # the flag as something to reach for when the demo looks wrong. Neither
    # helps an operator who does not yet know it looks wrong. --no-arm is
    # exactly the case the flag exists for, so it should not need remembering.
    #
    # Deliberately set here on the parsed args rather than in config: the guard
    # in test_docs_match_code.py requires this flag to stay CLI-only with zero
    # CFG.data reads, and this keeps that true.
    if args.no_arm and not args.no_contact_gate:
        args.no_contact_gate = True
        print("[args] --no-arm implies --no-contact-gate "
              "(no servo, so no torque contact to gate on)")

    if args.record:
        REC_FH = open(args.record, "w")
        print(f"[record] -> {args.record}")

    start_ws()

    # BUILD THE GOVERNOR BEFORE THE ARM EXISTS, so nothing can move before
    # the layer that judges movement is up. It costs 0.13s once, measured,
    # and it is the only construction here that can be slow -- propose()
    # itself is 0.08ms, which is why it can sit in the frame loop.
    global GOV
    from governor import Governor
    GOV = Governor.build(enabled=not args.no_governor)
    if GOV.armed:
        print(f"[gov] scrub3d safety governor ARMED ({GOV.why}). "
              f"Every arm target goes through fleet.propose().")
        # SAY WHICH RULES ACTUALLY FIRE. Two of the governor's four are about
        # coordinating several arms and this demo drives one, so a line that
        # just said ARMED would claim more than is true. See py/governor.py.
        print("[gov]   firing: joint limits + reach, head ceiling, "
              "upper-arm clearance to the person")
        print("[gov]   inert with one arm: territory half-spaces, "
              "arm-to-arm separation")
    else:
        print(f"[gov] safety governor ABSENT ({GOV.why}). The arm keeps "
              f"arm.py's box clamp and torque cutout, exactly as before.")

    # SEAM B, AND IT STARTS *AFTER* THE GOVERNOR ON PURPOSE. This is the port
    # Crystal's robot-adapter.js POSTs /api/task to, and every task it accepts
    # goes through GOV first. Starting it before GOV was built would open a
    # window where a task could arrive with no judge -- careapi refuses in
    # that window rather than passing it through, but not opening the door
    # early is the stronger guarantee. Plan §6: "Governor before endpoints go
    # live. Wiring the door before the lock is the wrong order on a machine
    # that touches a person."
    import careapi
    careapi.start(sys.modules[__name__],
                  port=int(CFG.data.get("care_api_port", 8770)))

    arm = make_arm(args.arm, args.port, args.no_arm)
    # PUBLISH THE ARM TO THE PUMP. Assigned here, immediately after the one
    # construction site, so there is no window in which the pump could sample
    # a half-built driver -- make_arm has already returned and go_home() below
    # only moves it. Before this line ARM is None and _sample_joints answers
    # None, which is the same wire as the demo has always sent.
    global ARM
    ARM = arm
    arm.go_home()

    # TORQUE CUTOUT FOR EVERY MODE THAT MOVES A REAL ARM. This used to start
    # only in the live branch, so --scripted and --replay drove the arm into a
    # person's forearm with NO torque watchdog at all -- and --scripted is
    # exactly the mode you fall back to when tracking fails, i.e. the one most
    # likely to run against a real human under time pressure. Found by an
    # adversarial audit.
    if not args.no_arm:
        threading.Thread(target=feedback_loop, args=(arm,), daemon=True).start()
    # EVERY mode gets remote control -- replay and scripted included.
    threading.Thread(target=remote_control_loop, args=(arm,), daemon=True).start()

    try:
        if args.replay:
            replay_loop(args.replay, arm)
        elif args.scripted:
            from scripted import run_canned
            print("[scripted] waiting to be ARMED — press 's' on the "
                  "projector (or use --auto-arm to skip)")
            while RUNNING:
                # CONSENT APPLIES HERE TOO. --scripted used to scrub to
                # contact depth the moment it started, with nobody arming it.
                # It is the fallback you switch to when tracking fails, i.e.
                # the mode most likely to be running against a real person.
                # THE CONSENT TIMEOUT APPLIES HERE TOO. The live path
                # expires arming after ARM_TIMEOUT_S (scrubbot.py:420) so a
                # press from minutes ago cannot drive the sponge into whoever
                # happens to be at the table now. --scripted checked ARMED but
                # never _armed_at, so a consent press had no expiry at all:
                # press s, get distracted, and ten minutes later the arm
                # scrubs to contact depth on its own. --scripted is the mode
                # you fall back to when tracking fails, i.e. the one most
                # likely to be running against a real person under pressure.
                #
                # SAME LATCH AS THE LIVE PATH, not a second copy of its
                # arithmetic. This branch re-derived `now - _armed_at >
                # ARM_TIMEOUT_S` by hand, so it had the arming clock and not
                # the session budget -- and a mode that runs canned motion
                # against a real person is the last one that should be the
                # laxer of the two.
                if not args.auto_arm:
                    lapsed, why = consent_expired()
                    if lapsed:
                        consent_clear("expired")
                        print(f"[scripted] consent lapsed: {why}"
                              " — press 's' again")
                if not (ARMED or args.auto_arm):
                    time.sleep(0.1)
                    continue
                if arm.estopped or arm.abort_requested:
                    time.sleep(0.1)
                    continue
                run_canned(arm, fire, fire_reset, SPLOTCH_TS,
                           z_mm=_num("forearm_z_mm", 45.0),
                           dur=_num("scrub_seconds", 8.0),
                           use_water=CFG.data.get("use_water_bucket", False),
                           contact_depth=_num("contact_depth_mm", -5.0))
                ARMED = False              # one cycle per arming, as live
                if not RUNNING:
                    break
                time.sleep(3)
        else:
            vision_loop(arm, args)
    except KeyboardInterrupt:
        pass
    finally:
        # DISARM THE HANDLERS BEFORE RETREATING. _term_handler stays installed
        # and still raises, so a SECOND signal -- the classic impatient
        # operator hitting ^C again when the arm does not visibly move, or
        # re-running pkill -- lands INSIDE this block and aborts the retreat.
        # Measured: a second SIGTERM at 0.05s and at 0.30s both produced no
        # stop and no clean shutdown, and the arm reaches only z~50-65mm of
        # the 0.4s retreat. The skin plane is z=45, so the sponge is still on
        # the volunteer's forearm and the ESP32 holds that pose forever --
        # the exact end state the SIGTERM fix was written to eliminate,
        # reached through the same key the operator is already pressing.
        # From here on, the retreat finishes no matter how many times they
        # press it. SIGKILL still wins, and nothing can change that.
        disarm_signals()
        RUNNING = False
        arm.go_home(); time.sleep(0.4); arm.close()
        if REC_FH:
            REC_FH.close()
        cv2.destroyAllWindows()
        print("[main] clean shutdown")


if __name__ == "__main__":
    main()
