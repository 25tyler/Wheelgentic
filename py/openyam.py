"""py/openyam.py — Anvil OpenYAM arm driver over SocketCAN.

DROP-IN FOR py/arm.py::Arm. py/scrubbot.py touches exactly this surface:
    set_target(x,y,z,t) · hold() · go_home() · estop() · clear_estop() ·
    poll_feedback() · over_torque() · close() · .estopped ·
    .abort_requested · .contact · .link_ok · .dry
Anything added here beyond that list is additive, never required.

.target IS NOT PART OF THAT SURFACE AND NO CALLER MAY READ IT. On the
RoArm it is (x,y,z,t) in millimetres; here it is six JOINT RADIANS.
A caller that reads it gets a different quantity depending on which arm
it happens to be driving, with no error -- scrubbot.py:672 did exactly
that (`set_target(*arm.target)`, a 6-tuple into a 4-arg signature) and
scripted.py:63 sliced radians and travelled to them as millimetres.
hold() and cartesian_pose() exist so neither file has to know the shape.

WHAT CHANGED FROM THE ROARM, AND WHY IT MATTERS
-----------------------------------------------
The RoArm-M2-S had an ESP32 that solved IK on board: we sent millimetres
and the firmware produced joint angles. The OpenYAM has no such layer.
Six Damiao servos sit on a CAN bus and each takes a JOINT ANGLE. So:

  * IK is now OUR problem, on the host, every tick.
  * reachable() guarded a NaN-whip hazard specific to RoArm firmware
    (T:1041 had no nanIK guard). That exact hazard is GONE -- but a new
    one replaces it: an IK solution that does not converge yields NaN,
    and float_to_uint would quantise NaN into a garbage angle. So the
    guard moves rather than disappears. See _solve_ik.
  * The firmware used to enforce joint limits. Now JOINT_LIMITS below is
    the only thing standing between a bad solve and a servo driving into
    its own hard stop.

STATUS: WIRE FORMAT VERIFIED, MOTION NOT. damiao.py's frames are
byte-identical to two independent reference implementations. Nothing here
has commanded a physical motor. Joint sign conventions, gear directions,
the real joint limits and the home pose are all PLACEHOLDERS marked
CALIBRATE below -- they must be measured against the real arm before it
is allowed to move near a person.
"""
import json
import math
import os
import threading
import time

import damiao
from damiao import MotorType

# Where tools/calibrate-openyam.py writes its measurements. Repo root, beside
# config.json, because that is where every other operator-editable file in
# this project lives and an operator looking for "the settings" finds it.
CALIB_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config-openyam.json"))

# CAN id convention, from openarm_can README.md:115-119 and
# python/examples/example.py:23-24 -- send ids 0x01..0x08 per joint,
# receive id = send id + 0x10. The gripper sits on its own pair.
SEND_IDS = (0x01, 0x02, 0x03, 0x04, 0x05, 0x06)
RECV_IDS = tuple(i + 0x10 for i in SEND_IDS)
GRIPPER_SEND, GRIPPER_RECV = 0x07, 0x17

# CALIBRATE. Per-joint motor model. The OpenYAM's exact fitment is not
# published -- openarm_can's examples use DM4310 throughout, and the big
# proximal joints on a 610mm / 2.5kg-payload arm are very unlikely to be
# the same model as the wrist. A wrong model here does not error: it
# rescales torque and position by the wrong maxima (damiao.MOTOR_LIMITS).
# READ THE LABELS ON THE ACTUAL MOTORS BEFORE FIRST POWER-ON.
JOINT_MOTORS = (
    MotorType.DM4310,   # J0 base yaw        CALIBRATE
    MotorType.DM4310,   # J1 shoulder pitch  CALIBRATE
    MotorType.DM4310,   # J2 elbow pitch     CALIBRATE
    MotorType.DM4310,   # J3 wrist roll      CALIBRATE
    MotorType.DM4310,   # J4 wrist pitch     CALIBRATE
    MotorType.DM4310,   # J5 wrist yaw       CALIBRATE
)

# CALIBRATE. Soft joint limits in radians. Deliberately TIGHTER than the
# servo's own +-12.5 rad electrical range -- that range is the encoder's,
# not the linkage's, and a joint driven to it will hit metal.
# Anvil publishes no limits (OpenYAM_description is "Coming Soon"), so
# these are conservative placeholders. MEASURE THEM.
JOINT_LIMITS = (
    (-2.6, 2.6),
    (-1.8, 1.8),
    (-2.4, 2.4),
    (-2.8, 2.8),
    (-1.9, 1.9),
    (-2.8, 2.8),
)

# Gains, taken from Anvil's own shipped config for a single OpenYAM
# follower arm: anvil-loader config/openyam_quest_teleop_single_left.yaml
# (teleop_kp / teleop_kd, first six entries -- the seventh is the gripper).
# These are the only OpenYAM-specific tuning numbers Anvil publishes.
TELEOP_KP = (40.0, 65.0, 65.0, 25.0, 12.0, 12.0)
TELEOP_KD = (3.0, 4.5, 4.5, 0.7, 0.2, 0.2)
# Homing uses softer gains so a bad starting pose crawls rather than snaps.
HOMING_KP = (10.0, 10.0, 10.0, 5.0, 5.0, 5.0)
HOMING_KD = (1.0, 1.0, 1.0, 0.5, 0.5, 0.5)

# CALIBRATE. Link lengths in mm for the 3-DOF positioning sub-chain.
# Anvil publishes reach (610mm without gripper) but not the segment
# split; these are a plausible decomposition that MUST be replaced with
# measured values or the URDF when it ships.
L_BASE = 130.0    # floor -> shoulder
L_UPPER = 290.0   # shoulder -> elbow
L_FORE = 300.0    # elbow -> wrist

REACH_MAX = L_UPPER + L_FORE          # 590mm, consistent with 610 spec
REACH_MIN = abs(L_UPPER - L_FORE)

# Safety box in arm mm. Same role as arm.py:33's BOX and deliberately
# conservative. TIGHTEN AGAINST YOUR OWN TABLE before any run with a
# person in the workspace.
BOX = dict(xmin=150.0, xmax=520.0, ymin=-300.0, ymax=300.0,
           zmin=30.0, zmax=460.0)

# Rate limit. arm.py used 6mm per 25ms (240mm/s). The OpenYAM is a bigger,
# heavier arm with a 2.5kg payload, so it moves NO FASTER: same 240mm/s
# ceiling, expressed against this driver's own tick.
RATE_HZ = 100.0
TICK_S = 1.0 / RATE_HZ
MAX_STEP_MM = 240.0 * TICK_S          # 2.4mm per 10ms tick

# CALIBRATE. Per-joint torque ceiling in N·m. This is the OpenYAM's
# equivalent of scrubbot.py's TORQUE_ESTOP dict and of arm.py's
# contact thresholds -- the number above which the arm is judged to be
# pressing into something, which is BOTH the safety cutout and the
# honest "the sponge is really touching" signal.
#
# THESE ARE NOT INTERCHANGEABLE WITH THE ROARM'S NUMBERS. TORQUE_ESTOP
# in scrubbot.py holds 600-900 in the RoArm firmware's raw integer
# units; damiao.unpack_feedback returns real N·m scaled against
# MOTOR_LIMITS. Feeding one into the other compares newton-metres
# against a servo-bus integer and never trips -- which is exactly the
# silent no-op this constant exists to end.
#
# The split mirrors the physics: the proximal joints carry the whole
# arm's weight so they read high at rest, the wrist carries only the
# sponge so a small rise there is already contact. CONTACT_TAU must be
# strictly below ESTOP_TAU or contact and the cutout fire together and
# the sponge never touches anything without stopping the demo.
CONTACT_TAU = (2.5, 4.0, 3.0, 1.0, 0.8, 0.8)   # CALIBRATE
ESTOP_TAU   = (5.0, 8.0, 6.0, 2.5, 2.0, 2.0)   # CALIBRATE

# CALIBRATE. Per-joint direction, +1 or -1, applied at exactly TWO
# sites: _send_joint on the way out and poll_feedback on the way back.
# Applying it anywhere else (set_joints, clear_estop's q_fb copy-back)
# multiplies to +1 and silently reverts the correction, so a third
# application looks like a fix and is a no-op. Measure per
# docs/OPENYAM-BRINGUP.md before the arm moves near a person: a wrong
# sign on J1 or J2 puts the elbow BELOW the person instead of above and
# nothing on the host can detect it.
JOINT_SIGN = (1, 1, 1, 1, 1, 1)

# CALIBRATE. Home pose in joint space. Anvil's config ships
# home_positions as three 7-vectors (a staged approach, not one pose);
# until that staging is understood against real hardware this is a plain
# safe tuck with the arm folded up and away from the workspace.
HOME_Q = (0.0, 0.437, 0.437, -0.349, 0.0, 0.0)

# False until a calibration file is loaded over the placeholders above. The
# real path refuses to construct while this is False -- see __init__. This is
# NOT a dev/prod branch: dry_run is already the no-hardware path, and this
# governs only whether the hardware path is permitted at all.
CALIBRATED = False
CALIB_SOURCE = "placeholders (UNCALIBRATED)"


def _load_calibration(path=CALIB_PATH):
    """Replace the CALIBRATE placeholders above with measured values.

    ONE CODE PATH. These are module globals, not instance state, because
    reachable() / _solve_ik() / the test suite all read them at module level.
    Loading into an instance instead would leave the free functions solving
    against the placeholder arm while the instance clamped against the real
    one -- two geometries in one process, which is the divergence this
    project's ONE PIPELINE rule exists to prevent.

    Absent file = placeholders stay, CALIBRATED stays False. Malformed file =
    same, and it SAYS SO loudly rather than silently half-applying: a partly
    applied calibration (real link lengths, placeholder limits) is more
    dangerous than no calibration, because it looks measured.
    """
    global JOINT_MOTORS, JOINT_LIMITS, L_BASE, L_UPPER, L_FORE
    global REACH_MAX, REACH_MIN, HOME_Q, JOINT_SIGN
    global CONTACT_TAU, ESTOP_TAU
    global CALIBRATED, CALIB_SOURCE
    try:
        with open(path) as fh:
            d = json.load(fh)
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as e:
        print(f"[openyam] !! {path} is unreadable ({e}) — "
              "REFUSING to half-apply it. Placeholders still in force.")
        return False

    try:
        motors = tuple(MotorType[n] for n in d["joint_motors"])
        limits = tuple((float(lo), float(hi)) for lo, hi in d["joint_limits"])
        links = d["link_lengths_mm"]
        lb, lu, lf = (float(links["base"]), float(links["upper"]),
                      float(links["fore"]))
        home = tuple(float(v) for v in d["home_q"])
        signs = tuple(float(v) for v in d["joint_sign"])
        # Torque ceilings are optional because they are measured LAST, against
        # a moving arm. A file written by an operator who has done the
        # geometry but not yet the touch test is a valid, useful file; it
        # simply keeps the conservative placeholder ceilings.
        c_tau = tuple(float(v) for v in d.get("contact_tau", CONTACT_TAU))
        e_tau = tuple(float(v) for v in d.get("estop_tau", ESTOP_TAU))
        if not (len(motors) == len(limits) == len(home) == len(signs)
                == len(c_tau) == len(e_tau) == 6):
            raise ValueError("every vector must have exactly 6 entries")
        # Contact must trip strictly before the cutout. Equal or inverted and
        # the sponge can never touch anything without also stopping the demo,
        # which reads on stage as "the arm is broken".
        if any(c >= e for c, e in zip(c_tau, e_tau)):
            raise ValueError("contact_tau must be strictly below estop_tau")
        if any(v <= 0.0 for v in c_tau + e_tau):
            raise ValueError("torque ceilings must be positive")
        if any(lo >= hi for lo, hi in limits):
            raise ValueError("a joint limit has lo >= hi")
        if min(lb, lu, lf) <= 0.0:
            raise ValueError("link lengths must be positive")
        if any(s not in (1.0, -1.0) for s in signs):
            raise ValueError("joint_sign entries must be +1 or -1")
        # HOME_Q outside the limits would be silently clamped by _pump and the
        # arm would go somewhere other than home on the first move of every
        # run, with no log line. Catch it here instead.
        for i, (v, (lo, hi)) in enumerate(zip(home, limits)):
            if not lo <= v <= hi:
                raise ValueError(
                    f"home_q[{i}]={v} is outside its limit ({lo}, {hi})")
    except (KeyError, TypeError, ValueError) as e:
        print(f"[openyam] !! {path} is invalid ({e}) — "
              "REFUSING to half-apply it. Placeholders still in force.")
        return False

    JOINT_MOTORS, JOINT_LIMITS, HOME_Q, JOINT_SIGN = motors, limits, home, signs
    CONTACT_TAU, ESTOP_TAU = c_tau, e_tau
    L_BASE, L_UPPER, L_FORE = lb, lu, lf
    # Derived, so they MUST be recomputed here. Leaving them at the
    # placeholder values would make reachable() guard a 590mm arm while
    # _solve_ik solved for the real one -- accepting targets that produce NaN.
    REACH_MAX = L_UPPER + L_FORE
    REACH_MIN = abs(L_UPPER - L_FORE)
    CALIBRATED = True
    CALIB_SOURCE = f"{os.path.basename(path)} measured {d.get('measured', '?')}"
    print(f"[openyam] calibration loaded from {path}")
    return True


_load_calibration()


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def reachable(x, y, z):
    """True if the 3-DOF positioning chain can reach (x,y,z).

    STILL MANDATORY, FOR A NEW REASON. The RoArm needed this because its
    firmware would whip on a NaN target. Here the hazard is ours: an
    unreachable point makes the law-of-cosines term in _solve_ik leave
    [-1,1], acos returns NaN, and float_to_uint would quantise NaN into
    an arbitrary angle. Checking first means _solve_ik is only ever
    called on points it can actually solve.
    """
    if not all(map(math.isfinite, (x, y, z))):
        return False
    r = math.hypot(x, y)
    s = z - L_BASE
    d = math.hypot(r, s)
    return REACH_MIN <= d <= REACH_MAX


def _solve_ik(x, y, z):
    """Cartesian mm -> (j0, j1, j2) radians for the positioning chain.

    Planar 2-link solve in the plane containing the base yaw axis:
    j0 swings that plane, j1/j2 place the wrist within it. Elbow-up is
    chosen so the elbow never dips toward the person below the arm.

    Returns None if the solve does not converge. EVERY CALLER MUST CHECK
    -- returning None rather than NaN is what stops a bad target from
    reaching float_to_uint.
    """
    if not reachable(x, y, z):
        return None
    j0 = math.atan2(y, x)
    r = math.hypot(x, y)
    s = z - L_BASE
    d2 = r * r + s * s
    d = math.sqrt(d2)
    # Law of cosines. The clamp guards float error at exactly-full reach,
    # where the ratio can land a hair outside [-1,1] and make acos NaN.
    cos_elbow = (d2 - L_UPPER ** 2 - L_FORE ** 2) / (2 * L_UPPER * L_FORE)
    cos_elbow = clamp(cos_elbow, -1.0, 1.0)
    elbow = math.acos(cos_elbow)
    # ELBOW-UP IS j2 = -elbow, NOT -(pi - elbow). This line used to read
    # `j2 = -(math.pi - elbow)`, the INTERIOR triangle angle at the elbow
    # rather than the joint's deviation from straight. The two are
    # supplementary, so the solve was off by (pi - 2*elbow) at every
    # target, and it did not raise: the arm confidently went somewhere
    # else. MEASURED against cartesian_pose() below (the forward
    # kinematics of this same chain): the old pairing missed by up to
    # 588mm over the joint space and 190mm at (300,0,300), which is
    # further than the sponge is wide. Confirmed by solving the same
    # point in closed form -- both correct branches close to 1e-13mm,
    # neither of the old ones closes at all.
    #
    # The structure below is the standard two-link form, the same one
    # scrub3d/kinematics.py:ik() uses against a real URDF: pick the elbow
    # from the law of cosines, then subtract the elbow's own contribution
    # from the direction of the target. cos_sh / acos(cos_sh) computed the
    # same shoulder offset correctly and is kept as atan2(), which is
    # equivalent here and carries the elbow's sign so BOTH branches work
    # if a future calibration wants elbow-down.
    j2 = -elbow                        # elbow-up
    j1 = math.atan2(s, r) - math.atan2(L_FORE * math.sin(j2),
                                       L_UPPER + L_FORE * math.cos(j2))
    if not all(map(math.isfinite, (j0, j1, j2))):
        return None
    if not (JOINT_LIMITS[1][0] <= j1 <= JOINT_LIMITS[1][1]
            and JOINT_LIMITS[2][0] <= j2 <= JOINT_LIMITS[2][1]
            and JOINT_LIMITS[0][0] <= j0 <= JOINT_LIMITS[0][1]):
        # GRAFTED FROM scrub3d/kinematics.py:ik(). reachable() above tests
        # only a sphere about the shoulder and ignores the joint limits
        # entirely, so it approves points the arm cannot actually hold.
        # _pump would then clamp j1/j2 into range and drive to a DIFFERENT
        # point while set_target had already returned True -- the same
        # "silently relocated, reported as success" failure arm.py's clamp
        # warning exists for. Refusing here makes a True from set_target
        # mean the commanded pose is the requested pose.
        return None
    return (j0, j1, j2)


class OpenYamArm:
    """Drop-in replacement for arm.Arm, driving an OpenYAM over SocketCAN.

    Threading model is deliberately identical to arm.py: one background
    pump owns the bus and always sends the NEWEST target rather than
    queueing, so a slow consumer can never build a backlog of stale
    positions that replay into the person after a pause.
    """

    # CLASS-LEVEL DEFAULTS so a half-constructed instance still answers
    # every attribute scrubbot.py reads. Same reasoning as arm.py:55-77:
    # an exception partway through __init__ must not produce an object
    # whose .estopped raises AttributeError.
    estopped = False
    abort_requested = False
    contact = False
    clamped_hard = False
    link_ok = False
    dry = False
    target = HOME_Q

    def __init__(self, channel=None, bitrate=1_000_000, dry_run=False,
                 allow_uncalibrated=False, interface=None):
        self.dry = bool(dry_run)
        self.lock = threading.RLock()
        self.q_cmd = list(HOME_Q)
        self.q_fb = list(HOME_Q)
        self.target = tuple(HOME_Q)
        self.feedback = {}
        self._clear_token = 0
        self._last_clamp_warn = 0.0
        self._last_limit_warn = 0.0
        self._running = False
        self.bus = None

        if self.dry:
            print("[openyam] dry run — no CAN traffic")
            self.link_ok = True
            # FALL THROUGH TO THE PUMP. This used to `return` here, which
            # left --no-arm exercising a DIFFERENT OBJECT than the live
            # one: no rate limiter, no joint-limit clamp, no
            # estop-silences-the-pump path -- the three behaviours most
            # worth rehearsing before the arm exists. arm.py:159 starts
            # its pump unconditionally for the same reason. _raw_send
            # already returns True without touching the bus when dry, so
            # the pump runs the real code and the traffic goes nowhere.
            self._start_pump()
            return

        # THE GATE. docs/OPENYAM-BRINGUP.md ends with "run with the arm
        # clamped down, clear of people, and with a hand on the power
        # switch" -- advice, and advice gets skimmed. Until the five
        # CALIBRATE values have been measured, the link lengths are a
        # guess, the limits are a guess and the signs are a guess, so a
        # solve that looks confident can put the elbow under the person.
        # Refuse rather than move.
        #
        # allow_uncalibrated is for tools/calibrate-openyam.py ONLY -- the
        # tool that MEASURES these values obviously cannot require them.
        # It is not a dev/prod branch: it changes nothing about how the
        # arm is driven, only whether construction is permitted.
        if not CALIBRATED and not allow_uncalibrated:
            raise SystemExit(
                "\n[openyam] REFUSING to drive real hardware uncalibrated.\n"
                f"  {CALIB_PATH} is missing or invalid, so link lengths,\n"
                "  joint limits, motor models, home pose and joint signs\n"
                "  are all still placeholders. A wrong sign on J1/J2 puts\n"
                "  the elbow BELOW the person and nothing here can see it.\n\n"
                "  Measure them:  python3 tools/calibrate-openyam.py\n"
                "  Rehearse now:  OpenYamArm(dry_run=True)\n")

        channel = channel or os.environ.get("CAN_IFACE", "can0")
        # TRANSPORT NAME ONLY. This selects which python-can backend opens
        # the socket; it does NOT branch any behaviour below it. Every frame
        # still goes through _raw_send -> _pump -> _send_joint unchanged,
        # which is the whole point: "virtual" lets the real send path be
        # exercised with no adapter plugged in, instead of a separate test
        # harness that would re-implement the loop and drift from it.
        # Default stays socketcan, so the hardware path is untouched.
        interface = interface or os.environ.get("CAN_INTERFACE", "socketcan")
        try:
            import can  # python-can, only needed on the real path
        except ImportError:
            raise SystemExit(
                "\npython-can is not installed.\n"
                "  pip install 'python-can'\n")
        try:
            self.bus = can.interface.Bus(
                channel=channel, interface=interface, bitrate=bitrate)
        except Exception as e:
            raise SystemExit(
                f"\nCould not open CAN interface {channel!r} "
                f"({interface}): {e}\n"
                "SocketCAN is Linux-only. On the CANable 2.0 (gs_usb):\n"
                f"  sudo bash tools/can-bringup.sh\n"
                f"  candump {channel}      # should show motor frames\n")

        # Enable each joint, then read back. A motor that never answers
        # leaves link_ok False and the FSM refuses to arm (scrubbot.py:566).
        for sid, mt in zip(SEND_IDS, JOINT_MOTORS):
            self._raw_send(sid, damiao.pack_command(damiao.CMD_ENABLE))
            time.sleep(0.05)
        self.link_ok = self.poll_feedback() is not None
        if not self.link_ok:
            print("[openyam] WARNING: no feedback from any joint — "
                  "check bus wiring, termination and motor power")

        self._start_pump()

    def _start_pump(self):
        """One place that starts the pump, so the dry and live paths cannot
        drift into starting it differently (or, as before, one not at all)."""
        self._running = True
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()

    # ---- transport -----------------------------------------------------

    def _raw_send(self, can_id, data):
        """Put one frame on the bus. NEVER RAISES.

        Same contract as arm.py:184's _send and for the same reason: the
        pump thread calls this at 100Hz and a raised exception would kill
        the thread silently, leaving a live arm with nothing driving it.
        Returns True/False so estop() can refuse to latch a stop it could
        not actually transmit.
        """
        if self.dry or self.bus is None:
            return True
        try:
            import can
            self.bus.send(can.Message(arbitration_id=can_id, data=data,
                                      is_extended_id=False), timeout=0.02)
            return True
        except Exception as e:
            print(f"[openyam] send failed on 0x{can_id:02X}: {e}")
            return False

    def _send_joint(self, idx, q, kp, kd):
        # SIGN APPLIED HERE, AND ONLY HERE ON THE WAY OUT. Everything above
        # this line -- IK, limits, HOME_Q, the pump's slew -- works in the
        # kinematic convention _solve_ik defines. This converts to whichever
        # way the motor is actually wired. See JOINT_SIGN.
        return self._raw_send(
            SEND_IDS[idx],
            damiao.pack_mit(JOINT_MOTORS[idx], kp, kd,
                            q * JOINT_SIGN[idx], 0.0, 0.0))

    # ---- control loop --------------------------------------------------

    def _pump(self):
        """Send the newest commanded pose at RATE_HZ.

        ABSOLUTE-DEADLINE PACING, not sleep(TICK). A naive sleep drifts
        under load -- arm.py measured 31.3Hz where 40 was asked for
        (STATE.md:30). Here the deadline advances regardless of how long
        the body took, so the rate holds.
        """
        nxt = time.monotonic()
        while self._running:
            nxt += TICK_S
            try:
                with self.lock:
                    if self.estopped:
                        # While stopped we send NOTHING. Damiao holds its
                        # last commanded state; silence is what keeps the
                        # arm still. Re-commanding here would defeat the
                        # stop.
                        pass
                    else:
                        goal = list(self.target)
                        for i in range(6):
                            step = self.q_cmd[i]
                            # Joint-space slew limit. Derived from the
                            # cartesian ceiling: at full 590mm extension a
                            # radian of joint travel is ~590mm of tip
                            # travel, so cap per-tick joint motion at
                            # MAX_STEP_MM/REACH_MAX radians. Conservative
                            # at every pose closer in.
                            lim = MAX_STEP_MM / REACH_MAX
                            d = goal[i] - step
                            if d > lim:
                                d = lim
                            elif d < -lim:
                                d = -lim
                            self.q_cmd[i] = step + d
                            lo, hi = JOINT_LIMITS[i]
                            capped = clamp(self.q_cmd[i], lo, hi)
                            # SAY SO WHEN A LIMIT TRUNCATES THE POSE.
                            # set_target already warns when it clamps the
                            # CARTESIAN target, but this clamp was silent, so
                            # limits set too narrow quietly shortened every
                            # reach with no operator feedback at all -- the arm
                            # simply does not go where it was told and looks
                            # like a calibration problem. Rate-limited on the
                            # same 1s budget as the cartesian warning because
                            # this runs at 100Hz.
                            if capped != self.q_cmd[i]:
                                now = time.monotonic()
                                if now - self._last_limit_warn > 1.0:
                                    self._last_limit_warn = now
                                    print(f"[openyam] WARNING: J{i} "
                                          f"{self.q_cmd[i]:+.3f} rad hit its "
                                          f"soft limit ({lo:+.2f},{hi:+.2f}) "
                                          f"— pose truncated")
                            self.q_cmd[i] = capped
                            self._send_joint(i, self.q_cmd[i],
                                             TELEOP_KP[i], TELEOP_KD[i])
            except Exception as e:
                # The pump must outlive any single bad tick.
                print(f"[openyam] pump tick error: {e}")
            slack = nxt - time.monotonic()
            if slack > 0:
                time.sleep(slack)
            else:
                nxt = time.monotonic()      # we fell behind; resync

    # ---- public surface (mirrors arm.Arm) ------------------------------

    def clamp_to_box(self, x, y, z):
        """Where this driver would ACTUALLY send (x,y,z). -> (cx, cy, cz).

        Same contract as arm.Arm.clamp_to_box, and it exists for the same
        reason: scrubbot.py's govern_target() must ask the safety governor
        about the point that will really be commanded, not the raw one the
        vision loop produced, or the governor rules on a point the arm was
        never going to visit and refuses good scrubs as "unreachable".

        IT IS A METHOD ON EACH DRIVER, NOT A MODULE CONSTANT THE CALL SITE
        READS, because THIS BOX IS NOT arm.py's BOX: 150..520 in x against
        its 120..420, and a different z floor. A call site that reached for
        one module's BOX would clamp the wrong numbers onto the other arm and
        the governor would bless a point that driver never sends. Asking the
        arm is the same fix as over_torque() and hold().
        """
        return (clamp(x, BOX["xmin"], BOX["xmax"]),
                clamp(y, BOX["ymin"], BOX["ymax"]),
                clamp(z, BOX["zmin"], BOX["zmax"]))

    def set_target(self, x, y, z, t=None):
        """Command a cartesian target in arm millimetres.

        `t` is the tool pitch in radians, in the SAME convention the RoArm
        uses (arm.py's default 3.14 points the sponge face down). It is what
        keeps the sponge flat against a forearm instead of edge-on.

        IT USED TO BE ACCEPTED AND THROWN AWAY. The signature said `t=0.0`,
        nothing read it, and only sol[0..2] was written -- so the three wrist
        joints stayed frozen at HOME_Q for an entire scrub and the sponge's
        orientation was whatever the tuck pose happened to leave. The call
        site (scripted.py:51 passes 3.14) looked satisfied and was not. A
        default parameter that silently discards its argument is worse than
        no parameter: it makes the caller believe the feature exists.

        Order is deliberate and matches arm.py:246: clamp into the safety
        box FIRST, then solve. Solving first would let a point outside the
        box produce joint angles we then have to undo.
        """
        # ONE CLAMP, SHARED WITH THE GOVERNOR -- see clamp_to_box below.
        cx, cy, cz = self.clamp_to_box(x, y, z)

        moved = math.dist((x, y, z), (cx, cy, cz))
        # "IS THIS HAPPENING NOW", NOT "DID THIS EVER HAPPEN". Reassigned
        # every call, deliberately, and arm.py now does the same. The two
        # drivers used to disagree: this one cleared itself on the next
        # in-box target while arm.py latched forever once set. Nothing reads
        # the flag yet, which is exactly why it had to be settled -- the
        # first consumer would have been correct against one driver and
        # wrong against the other with nothing to catch it. "Now" wins
        # because a latched flag can never report recovery, so an operator
        # who fixes the calibration gets no signal that they fixed it.
        self.clamped_hard = moved > 50.0
        if self.clamped_hard:
            now = time.monotonic()
            # Rate-limited: an out-of-box stream would otherwise print
            # 100 lines a second and bury everything else.
            if now - self._last_clamp_warn > 1.0:
                self._last_clamp_warn = now
                print(f"[openyam] WARNING: target ({x:.0f},{y:.0f},{z:.0f}) "
                      f"clamped {moved:.0f}mm — check the calibration")

        sol = _solve_ik(cx, cy, cz)
        if sol is None:
            # Refusing is the safe action: holding the previous target
            # keeps the arm where it is rather than driving somewhere
            # arbitrary.
            print(f"[openyam] unreachable ({cx:.0f},{cy:.0f},{cz:.0f}) — held")
            return False
        with self.lock:
            j3, j4, j5 = self.target[3], self.target[4], self.target[5]
            if t is not None:
                # WRIST PITCH IS DRIVEN, NOT IGNORED. The 3-link solve puts
                # the forearm at absolute angle (j1 + j2) measured from the
                # horizontal, so the wrist pitch needed to hold the tool at
                # world pitch `t` is the remainder. Without this the sponge
                # tilts with every reach and presents an edge instead of a
                # face.
                #
                # UNVERIFIED, AND IT SATURATES TODAY. scripted.py passes the
                # RoArm's t=3.14 (its firmware's "sponge face down"), and
                # against the PLACEHOLDER JOINT_LIMITS[4] of +-1.9 rad that
                # demand clamps hard: measured t=3.14 and t=1.57 both land on
                # +1.900 while t=0.0 and t=-1.0 solve cleanly. Whether 3.14
                # even means face-down on this arm depends on the real J4 zero
                # and limits, which are two of the five CALIBRATE placeholders.
                # So the sponge angle is DRIVEN but NOT YET CORRECT: measure
                # J4's zero and range, then fix the convention here. Leaving
                # the argument discarded, as it was, would have hidden this.
                #
                # J3 (roll) and J5 (yaw) are NOT solved: doing that properly
                # needs the wrist's real axis geometry, and whether the three
                # wrist axes even intersect at a point is unknown until Anvil
                # ships the URDF. They hold their last commanded value, which
                # is HOME_Q's tuck unless set_joints moved them. Clamped so a
                # pitch demand the wrist cannot meet truncates loudly in the
                # pump rather than driving J4 into its stop.
                j4 = clamp(t - (sol[1] + sol[2]), *JOINT_LIMITS[4])
            self.target = (sol[0], sol[1], sol[2], j3, j4, j5)
        return True

    def set_joints(self, q):
        """Command joint angles directly, bypassing IK. Clamped to limits."""
        with self.lock:
            self.target = tuple(
                clamp(v, *JOINT_LIMITS[i]) for i, v in enumerate(q[:6]))

    def hold(self):
        """Stay exactly where the last target put us. Costs nothing, never fails.

        THIS EXISTS SO NO CALLER HAS TO READ .target. scrubbot.py:672 held
        position during an occluded scrub frame with `set_target(*arm.target)`
        -- fine on the RoArm, where target is the (x,y,z,t) that set_target
        takes, and a TypeError here, where it is six joint radians going into
        a four-argument signature. That line runs on the FIRST dropped
        landmark, which scrubbot.py:658-661 documents as constant because the
        arm occludes the forearm it is scrubbing, and it would kill the vision
        thread mid-scrub with the sponge on a person.
        The pump is already driving self.target every tick, so holding is
        genuinely a no-op here -- but it must stay a CALL, because on a driver
        that streams rather than latches, holding is not free.
        """
        return True

    def joint_pose(self):
        """The COMMANDED positioning angles, as (j0, j1, j2) radians.

        SAME CONTRACT AS cartesian_pose(), AND FOR THE SAME REASON: the
        driver knows its own shape, the caller must not. This arm commands
        joint radians natively so the answer is a slice of q_cmd -- but
        arm.py commands millimetres and has to solve for its angles, and a
        caller that reached into either driver's attributes would get one
        of those two answers while believing it had the other.

        q_cmd, NOT self.target, for the reason cartesian_pose() gives: target
        is where the arm is heading, q_cmd is what the pump has actually sent
        this tick. A cartoon posed from target would arrive before the metal.

        j0/j1/j2 only. The wrist triple is real on this arm but the projector
        has no wrist to pose, and this dict rides a 15Hz broadcast whose whole
        contract is staying small.
        """
        with self.lock:
            return (self.q_cmd[0], self.q_cmd[1], self.q_cmd[2])

    def cartesian_pose(self):
        """Best estimate of the tool point in arm mm, as (x, y, z).

        Forward kinematics of the same 3-link chain _solve_ik inverts, from
        the COMMANDED angles (q_cmd, what the pump has actually sent) rather
        than self.target, which is where we are heading rather than where we
        are. scripted.py needs a starting point in millimetres and used to
        get it by slicing arm.target[:3] -- three joint radians handed to
        motion.travel as a millimetre triple, which its length guard cannot
        catch because both are length 3. The result was an interpolation
        starting ~280mm from the arm's real position, in the fallback mode
        most likely to be running against a real person.
        """
        with self.lock:
            j0, j1, j2 = self.q_cmd[0], self.q_cmd[1], self.q_cmd[2]
        # Mirror of _solve_ik: j1 is measured from the r axis, j2 is the
        # elbow's deviation from straight, so the forearm's absolute angle
        # is j1 + j2. Keep these two functions edited together.
        r = L_UPPER * math.cos(j1) + L_FORE * math.cos(j1 + j2)
        s = L_UPPER * math.sin(j1) + L_FORE * math.sin(j1 + j2)
        return (r * math.cos(j0), r * math.sin(j0), s + L_BASE)

    def go_home(self):
        """Move to the tucked home pose under the softer homing gains."""
        with self.lock:
            self.target = tuple(HOME_Q)
        for i in range(6):
            self._send_joint(i, HOME_Q[i], HOMING_KP[i], HOMING_KD[i])
        return True

    def poll_feedback(self):
        """Drain the bus and return the most recent per-joint state.

        Returns None if nothing arrived, which is how link_ok is decided.
        """
        if self.dry:
            # A DRY ARM REPORTS NO CONTACT, EXPLICITLY. arm.py returns {}
            # here and scrubbot.py's --no-arm branch already implies
            # --no-contact-gate because of it. Saying so in one place beats
            # leaving self.contact at whatever the last live poll wrote.
            self.contact = False
            return {"dry": True, "q": list(self.q_fb)}
        if self.bus is None:
            return None
        got = None
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            msg = self.bus.recv(timeout=0.01)
            if msg is None:
                break
            if msg.arbitration_id not in RECV_IDS:
                continue
            idx = RECV_IDS.index(msg.arbitration_id)
            fb = damiao.unpack_feedback(bytes(msg.data), JOINT_MOTORS[idx])
            if fb is None:
                continue
            if fb["err"]:
                print(f"[openyam] joint {idx}: "
                      f"{damiao.describe_error(fb['err'])}")
            # SIGN APPLIED HERE, AND ONLY HERE ON THE WAY IN -- the mirror
            # of _send_joint. Undoing it here is what makes q_fb comparable
            # to q_cmd, which clear_estop() relies on when it resumes from
            # where the arm actually is. Torque is NOT sign-corrected:
            # every consumer takes abs() of it.
            self.q_fb[idx] = fb["q"] * JOINT_SIGN[idx]
            self.feedback[idx] = fb
            got = self.feedback
        if got is not None:
            # CONTACT IS SET HERE OR IT IS NEVER SET AT ALL. It was a class
            # attribute that nothing ever assigned, so it read False for the
            # whole run: no splotch popped during the scrub, the projector's
            # contact indicator was dead, and scrubbot.py:851's
            # `elif arm.contact` branch never fired on a real arm -- with no
            # error anywhere. arm.py:377 sets it from torS/torE on every
            # poll; this is the same idea against per-joint N·m.
            self.contact = any(
                abs(f.get("tau", 0.0)) > CONTACT_TAU[i]
                for i, f in self.feedback.items() if i < 6)
        return got

    def over_torque(self):
        """(joint_index, tau, limit) for the first joint past ESTOP_TAU, else None.

        THE DRIVER OWNS THE CUTOUT DECISION, NOT THE CALLER. scrubbot.py's
        torque watchdog used to read fb['torS'/'torE'/'torB'/'torH'] --
        RoArm firmware key names that do not exist in this driver's
        {joint_index: {q,dq,tau,err}} feedback. Every check evaluated
        `abs(fb.get(key, 0)) > limit` as `0 > 850`, so the loop ran forever
        at 10Hz and NOTHING was ever checked: no exception, no log line, and
        an arm with no torque cutout that looked like it had one.
        Asking the arm 'are you over torque' instead of naming its joints
        means neither driver can develop that hole again.
        """
        with self.lock:
            items = list(self.feedback.items())
        for i, f in items:
            if i >= 6:
                continue
            tau = abs(f.get("tau", 0.0))
            if tau > ESTOP_TAU[i]:
                return (i, tau, ESTOP_TAU[i])
        return None

    def estop(self):
        """Cut torque on every joint immediately.

        Sends CMD_DISABLE per joint, which drops the servo out of closed
        loop. Returns True only if EVERY disable actually went out --
        a partially-stopped arm must not report success.

        The token bump invalidates any clear_estop() already in flight,
        mirroring arm.py:398. Without it, a clear that started before the
        stop could complete after it and silently re-enable the arm.
        """
        with self.lock:
            self._clear_token += 1
            self.estopped = True
            self.abort_requested = True
        ok = True
        for sid in SEND_IDS:
            if not self._raw_send(sid, damiao.pack_command(damiao.CMD_DISABLE)):
                ok = False
        if ok:
            print("[openyam] *** EMERGENCY STOP *** torque cut on all joints")
        else:
            print("[openyam] !! ESTOP SEND FAILED — CUT POWER AT THE SUPPLY")
        return ok

    def clear_estop(self):
        """Re-enable after a stop. Refuses if a newer stop arrived."""
        with self.lock:
            token = self._clear_token
        for sid in SEND_IDS:
            if not self._raw_send(sid, damiao.pack_command(damiao.CMD_ENABLE)):
                print("[openyam] !! CLEAR FAILED — arm did not re-enable")
                return False
            time.sleep(0.02)
        with self.lock:
            if token != self._clear_token:
                # A stop landed while we were clearing. Honour the stop.
                print("[openyam] clear abandoned — a newer estop arrived")
                return False
            # Resume from where the arm actually IS, not from the stale
            # pre-stop target, or it would snap back on re-enable.
            self.poll_feedback()
            self.q_cmd = list(self.q_fb)
            self.target = tuple(self.q_fb)
            self.estopped = False
            self.abort_requested = False
        print("[openyam] estop cleared — holding current pose")
        return True

    def close(self):
        """Stop the pump and leave every joint de-energised."""
        self._running = False
        try:
            for sid in SEND_IDS:
                self._raw_send(sid, damiao.pack_command(damiao.CMD_DISABLE))
        finally:
            if self.bus is not None:
                try:
                    self.bus.shutdown()
                except Exception:
                    pass


if __name__ == "__main__":
    # Bring-up check. Runs against a real bus if CAN_IFACE is up,
    # otherwise dry. Deliberately does NOT move the arm.
    a = OpenYamArm(dry_run=("--dry" in os.sys.argv))
    print("link_ok:", a.link_ok)
    print("feedback:", a.poll_feedback())
    a.close()
