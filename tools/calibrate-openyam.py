#!/usr/bin/env python3
"""tools/calibrate-openyam.py — measure the five CALIBRATE values.

    python3 tools/calibrate-openyam.py --dry-run   # rehearse, no hardware
    python3 tools/calibrate-openyam.py             # the real thing

py/openyam.py ships five PLACEHOLDER values: which motor is on each joint,
how far each joint may travel, how long the links are, where home is, and
which way each joint counts as positive. Until they are measured the arm
can produce a confident-looking solve that puts the elbow underneath the
person. This walks an operator through measuring them and writes
config-openyam.json, which py/openyam.py loads at import.

WHY AN INTERACTIVE TOOL AND NOT A SCRIPT. Four of the five can only be
found by moving the arm and watching it. A script that moved on its own
would be moving an arm whose limits are, by definition, not yet known.
So every motion step here is opt-in: the operator types the word, the
motion is torque-capped low, and ENTER stops it.

THE ORDER IS NOT A PREFERENCE. Each step is decoded through the one
before it:

    1. motor models   — every torque and position reading is scaled by
                        MOTOR_LIMITS[model]. Wrong model, wrong newtons.
    2. joint signs    — "which way is positive" has no meaning until the
                        encoder is zeroed and the direction is known.
    3. joint limits   — found by creeping into the stop and watching
                        torque, which needs 1 for the newtons and 2 for
                        the direction.
    4. link lengths   — measured with a tape, not commanded, but recorded
                        against the zero that step 2 established.
    5. home pose      — a consequence of 2, 3 and 4, not a free choice.

SAFETY, AND THIS IS THE WHOLE POINT OF THE FILE:

  * Clamp the base to the bench. An unclamped arm walks itself off.
  * NOBODY within reach. Not "nobody in the way" — nobody within reach.
  * Hand on the supply switch, because estop() cuts TORQUE, and a
    shoulder or elbow holding the arm's weight FALLS when torque is cut.
    On the gravity-loaded joints the supply switch plus a hand catching
    the arm is the safe stop, not the software.
  * Every motion prompt defaults to NO. Bare ENTER skips the step.
"""
import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "py"))

import damiao                                    # noqa: E402
import openyam                                   # noqa: E402
from damiao import MotorType                     # noqa: E402

OUT_PATH = os.path.join(ROOT, "config-openyam.json")

JOINT_NAMES = ("J0 base yaw", "J1 shoulder pitch", "J2 elbow pitch",
               "J3 wrist roll", "J4 wrist pitch", "J5 wrist yaw")

# What "positive" MEANS for each joint, derived from openyam._solve_ik so the
# operator is confirming the solver's convention rather than inventing one:
#   j0 = atan2(y, x)                  -> positive swings counter-clockwise
#   j1 = atan2(s, r) + acos(cos_sh)   -> positive raises the arm
#   j2 = -(pi - elbow), elbow-up      -> positive opens the elbow outward
# Getting J1 or J2 backwards puts the elbow BELOW the person and nothing on
# the host can detect it, which is why these are asked one joint at a time.
SIGN_QUESTION = (
    "Looking DOWN at the arm from above, did it turn COUNTER-CLOCKWISE?",
    "Did the arm RAISE (shoulder lifted the upper link upward)?",
    "Did the elbow OPEN OUTWARD (the forearm straightened away)?",
    "Looking along the forearm from the base, did the wrist roll CLOCKWISE?",
    "Did the wrist PITCH UP (tool tip rose)?",
    "Did the wrist YAW toward the arm's LEFT (its +y side)?",
)

# The creep used to find a hard stop. Torque-capped so the STOP is what
# ends the motion, not the controller winning against metal.
#
# kp=0 is load-bearing: it makes the commanded POSITION irrelevant and turns
# the joint into a damped torque source, so the ceiling below is the only
# thing pushing. A kp>0 creep would drive to the commanded angle with
# whatever torque that took, which is how a joint destroys its own stop.
CREEP_STEP_RAD = 0.02      # ~1.1 deg/s at the 100ms tick below
CREEP_TICK_S = 0.10
CREEP_TAU_FRACTION = 0.10  # of the motor's own T_MAX, read in step 1
CREEP_CEILING_RAD = 3.2    # never creep past this, stop or no stop
STALL_READS = 3            # consecutive stalled reads that call it a stop
LIMIT_MARGIN_RAD = 0.10    # soft limit sits this far inside the hard stop


# ---- operator I/O ---------------------------------------------------------

def say(msg=""):
    print(msg, flush=True)


def rule(title=""):
    say()
    say("=" * 68)
    if title:
        say(f"  {title}")
        say("=" * 68)


class Operator:
    """Every prompt in one place so --dry-run can answer them all.

    DRY MODE ANSWERS "NO" TO EVERY MOTION PROMPT AND PRINTS WHAT IT WOULD
    HAVE DONE. That is deliberate: a dry run that auto-confirmed motions
    would rehearse a DIFFERENT session than the live one, and the whole
    value of the rehearsal is that the operator sees the real sequence of
    questions before the arm is powered.
    """

    def __init__(self, dry):
        self.dry = dry

    def confirm(self, prompt, danger=False):
        """Yes/no. DEFAULTS TO NO — bare ENTER never starts a motion."""
        word = "MOVE" if danger else "yes"
        q = f"  {prompt}\n    type {word!r} to proceed, ENTER to skip: "
        if self.dry:
            say(q + f"[dry-run: skipped, would await {word!r}]")
            return False
        return input(q).strip() == word

    def ask(self, prompt, default=None):
        if self.dry:
            say(f"  {prompt} [dry-run: would use {default!r}]")
            return default
        raw = input(f"  {prompt}"
                    + (f" [{default}]" if default is not None else "")
                    + ": ").strip()
        return raw if raw else default

    def ask_float(self, prompt, default=None):
        while True:
            raw = self.ask(prompt, default)
            try:
                return float(raw)
            except (TypeError, ValueError):
                say("    that is not a number.")

    def yesno(self, prompt):
        """A question about what the operator SAW. No default — an
        unanswered 'which way did it move' must not become a recorded
        measurement."""
        if self.dry:
            say(f"  {prompt} (y/n) [dry-run: would await an answer]")
            return None
        while True:
            raw = input(f"  {prompt} (y/n): ").strip().lower()
            if raw in ("y", "yes"):
                return True
            if raw in ("n", "no"):
                return False
            say("    answer y or n — this one cannot be guessed.")

    def pause(self, msg):
        if self.dry:
            say(f"  [dry-run] {msg}")
            return
        input(f"  {msg} — press ENTER when done: ")


# ---- arm access -----------------------------------------------------------

class Rig:
    """The arm, plus the raw CAN access the calibration needs.

    Constructs OpenYamArm with allow_uncalibrated=True, which is the ONE
    legitimate use of that flag: this is the tool that produces the
    calibration the gate demands, so it cannot require it.

    The pump is stopped immediately. The pump exists to stream a target at
    100Hz under teleop gains; calibration needs single torque-capped frames
    it can stop between. Leaving the pump running would have it fighting
    every creep step with a position command.
    """

    def __init__(self, dry, channel=None):
        self.dry = dry
        self.arm = None
        if dry:
            return
        self.arm = openyam.OpenYamArm(channel=channel,
                                      allow_uncalibrated=True)
        self.arm._running = False          # stop the pump; see docstring
        time.sleep(0.05)

    @property
    def link_ok(self):
        return bool(self.arm and self.arm.link_ok)

    def disable_all(self):
        if self.dry:
            return
        for sid in openyam.SEND_IDS:
            self.arm._raw_send(sid, damiao.pack_command(damiao.CMD_DISABLE))

    def enable(self, idx):
        if self.dry:
            return
        self.arm._raw_send(openyam.SEND_IDS[idx],
                           damiao.pack_command(damiao.CMD_ENABLE))
        time.sleep(0.05)

    def read_param(self, idx, rid, timeout=0.3):
        """Ask one motor for one register. Returns the value or None.

        A motor that does not answer is far more likely to be at the wrong
        BITRATE than dead: Damiao ships units at anything from 125kbit to
        10Mbit and openarm_can carries a whole discovery command for it.
        The caller says so rather than declaring the motor broken.
        """
        if self.dry:
            return None
        sid = openyam.SEND_IDS[idx]
        self.arm._raw_send(damiao.QUERY_ID, damiao.pack_query_param(sid, rid))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.arm.bus.recv(timeout=0.05)
            if msg is None:
                continue
            got = damiao.unpack_query_param(bytes(msg.data))
            if got and got[1] == rid:
                return got[2]
        return None

    def send_torque(self, idx, motor, tau, kd=1.0):
        """One kp=0 MIT frame: pure damped torque, no position authority."""
        if self.dry:
            return
        self.arm._raw_send(
            openyam.SEND_IDS[idx],
            damiao.pack_mit(motor, 0.0, kd, 0.0, 0.0, tau))

    def send_position(self, idx, motor, q, kp, kd):
        if self.dry:
            return
        self.arm._raw_send(openyam.SEND_IDS[idx],
                           damiao.pack_mit(motor, kp, kd, q, 0.0, 0.0))

    def read_joint(self, idx, motor, timeout=0.2):
        """Drain for this joint's feedback. Returns the dict or None."""
        if self.dry:
            return None
        rid = openyam.RECV_IDS[idx]
        deadline = time.monotonic() + timeout
        out = None
        while time.monotonic() < deadline:
            msg = self.arm.bus.recv(timeout=0.02)
            if msg is None:
                continue
            if msg.arbitration_id != rid:
                continue
            fb = damiao.unpack_feedback(bytes(msg.data), motor)
            if fb:
                out = fb
        return out

    def close(self):
        if self.arm:
            self.arm.close()


# ---- step 1: motor models -------------------------------------------------

def step_motors(rig, op):
    """Ask each motor what it is. Falls back to the operator's eyes.

    This is FIRST because MOTOR_LIMITS[model] scales every torque and
    position reading that follows. All 13 models share p_max=12.5 so
    position survives a wrong guess, but t_max spans 1 to 200 N·m — so a
    wrong model does not corrupt the angles, it corrupts exactly the
    torque numbers the limit search depends on. Asking 5 N·m against a
    DM4310 table while the motor is a DM8009 delivers 27 N·m.
    """
    rule("STEP 1 of 5 — MOTOR MODEL PER JOINT")
    say("  Reading PMAX/VMAX/TMAX from each motor over CAN (register 0x7FF).")
    say("  A model that cannot be read is asked for, not assumed.")
    say()
    out = []
    for i, name in enumerate(JOINT_NAMES):
        say(f"  {name}")
        p = rig.read_param(i, damiao.RID_PMAX)
        v = rig.read_param(i, damiao.RID_VMAX)
        t = rig.read_param(i, damiao.RID_TMAX)
        chosen = None
        if None not in (p, v, t):
            say(f"    motor reports  P={p:.3g}  V={v:.3g}  T={t:.3g}")
            cands = damiao.identify_motor(p, v, t)
            if len(cands) == 1:
                chosen = cands[0]
                say(f"    -> {chosen.name} (unambiguous)")
            elif len(cands) > 1:
                # The two known ties: DM4340/DM4340_48V and DMH6215/DMG6220
                # carry identical limit triples, so the wire cannot separate
                # them. Their ARITHMETIC is identical too, so either choice
                # is numerically correct -- but the label is recorded
                # honestly rather than the first match being passed off as
                # a measurement.
                names = ", ".join(c.name for c in cands)
                say(f"    -> ambiguous: {names} (identical limits)")
                say("       Numerically identical, so either is safe. "
                    "Read the label to record the true one.")
                pick = op.ask("which model (or ENTER for the first)",
                              cands[0].name)
                chosen = MotorType[pick] if pick in MotorType.__members__ \
                    else cands[0]
            else:
                say(f"    -> no MOTOR_LIMITS row matches {p},{v},{t}")
                say("       damiao.MOTOR_LIMITS may be out of date for "
                    "this unit. Record the read values in the notes.")
        elif rig.dry:
            # Not a diagnosis. There is no bus in a dry run, so saying
            # "no answer" here would rehearse a fault that is not present
            # and teach the operator to ignore that message live.
            say("    [dry-run] would read PMAX/VMAX/TMAX and name the model")
        else:
            say("    no answer from this joint.")
            say("    A silent motor is MORE OFTEN at the wrong bitrate than "
                "dead.")
            say("    Damiao units ship anywhere from 125kbit to 10Mbit; this "
                "bus is at 1Mbit.")
            say("    Try:  sudo ip link set can0 down && "
                "sudo ip link set can0 up type can bitrate 5000000")
        if chosen is None:
            if not rig.dry:
                say("    Falling back to the label on the motor.")
                say("    Known models: "
                    + ", ".join(m.name for m in MotorType))
            pick = op.ask("model name as printed on the motor", "DM4310")
            chosen = MotorType[pick] if pick in MotorType.__members__ \
                else MotorType.DM4310
            if pick not in MotorType.__members__:
                say(f"    {pick!r} is not a known model — recorded DM4310. "
                    "FIX THIS BEFORE THE ARM MOVES.")
        out.append(chosen)
        say()
    return out


# ---- step 2: joint signs --------------------------------------------------

def step_signs(rig, op, motors):
    """Command a small positive step and ask which way it actually went.

    Empirical, one joint at a time, all others de-energised. The question
    asked is the SOLVER'S convention (see SIGN_QUESTION), so a 'no' means
    the motor is geared against the solver and the recorded sign is -1.

    Zeroing comes first because 'positive' is meaningless against an
    unknown datum. The disable -> set-zero -> disable sequence with gaps
    is openarm_can's; a bare set-zero to an ENABLED motor is not the same
    operation and can leave the encoder offset unwritten.
    """
    rule("STEP 2 of 5 — JOINT DIRECTION (SIGN)")
    say("  One joint at a time. Every other joint is DE-ENERGISED and will")
    say("  hang limp — support the arm by hand before each step.")
    say()
    say("  Each joint is zeroed where it sits, then commanded +0.10 rad")
    say("  (about 6 degrees) at the soft HOMING gains. You say which way")
    say("  it went. 'No' records -1 for that joint.")
    say()
    signs = []
    for i, name in enumerate(JOINT_NAMES):
        say(f"  {name}")
        if not op.confirm(f"Zero and nudge {name} by +0.10 rad?", danger=True):
            say("    skipped — recorded +1 (UNMEASURED)")
            signs.append(1)
            say()
            continue
        rig.disable_all()
        time.sleep(0.1)
        if not rig.dry:
            rig.arm._raw_send(openyam.SEND_IDS[i],
                              damiao.pack_command(damiao.CMD_SET_ZERO))
            time.sleep(0.1)
        rig.disable_all()
        time.sleep(0.1)
        rig.enable(i)
        # Soft gains so a wrong-signed joint crawls the wrong way rather
        # than snapping into whatever is behind it.
        for _ in range(20):
            rig.send_position(i, motors[i], 0.10,
                              openyam.HOMING_KP[i], openyam.HOMING_KD[i])
            time.sleep(0.05)
        fb = rig.read_joint(i, motors[i])
        if fb:
            say(f"    encoder now reads q={fb['q']:+.3f} rad")
            if fb["q"] < 0:
                # The motor reports moving OPPOSITE the commanded sign.
                # That is not a convention question -- the command and the
                # encoder are the same device's own frame, so they must
                # agree. Disagreement means this CAN id is not the motor
                # you think it is.
                say("    !! commanded +0.10 but the encoder went negative.")
                say("    !! That is an ID-MAPPING fault, not a convention.")
                say("    !! STOP and check which motor answers on "
                    f"0x{openyam.SEND_IDS[i]:02X}.")
        moved_positive = op.yesno(f"    {SIGN_QUESTION[i]}")
        rig.disable_all()
        if moved_positive is None:
            signs.append(1)
            say("    unanswered — recorded +1 (UNMEASURED)")
        else:
            signs.append(1 if moved_positive else -1)
            say(f"    recorded sign {signs[-1]:+d}")
        say()
    return signs


# ---- step 3: joint limits -------------------------------------------------

def creep_to_stop(rig, op, idx, motor, direction, t_max):
    """Creep one joint one way until it stalls against its stop.

    Returns the angle at the stop, or None if the operator skipped or the
    creep hit its own ceiling without stalling.

    STALL DETECTION IS TORQUE-AND-VELOCITY, NOT POSITION. A joint that has
    stopped moving because it reached the commanded angle looks identical,
    from position alone, to a joint jammed against metal. Torque at the
    ceiling WITH velocity near zero is what separates them. The error
    nibble is also watched: overload (0xE) and overcurrent (0xA) mean the
    stop was found the hard way and the creep must end immediately.
    """
    tau = CREEP_TAU_FRACTION * t_max * direction
    say(f"    creeping {'+' if direction > 0 else '-'} at "
        f"{abs(tau):.2f} N·m ({CREEP_TAU_FRACTION:.0%} of T_MAX)")
    if rig.dry:
        say("    [dry-run] would creep until torque stalls, then back off "
            f"{LIMIT_MARGIN_RAD} rad")
        return None
    rig.enable(idx)
    stalled = 0
    last_q = 0.0
    steps = int(CREEP_CEILING_RAD / CREEP_STEP_RAD)
    for _ in range(steps):
        rig.send_torque(idx, motor, tau)
        time.sleep(CREEP_TICK_S)
        fb = rig.read_joint(idx, motor)
        if fb is None:
            continue
        last_q = fb["q"]
        if fb["err"] in (0xA, 0xE):
            say(f"    stop found via {damiao.describe_error(fb['err'])} "
                f"at q={last_q:+.3f}")
            rig.disable_all()
            return last_q
        if abs(fb["tau"]) >= 0.8 * abs(tau) and abs(fb["dq"]) < 0.05:
            stalled += 1
            if stalled >= STALL_READS:
                say(f"    stalled at q={last_q:+.3f} rad "
                    f"(tau={fb['tau']:+.2f}, dq={fb['dq']:+.3f})")
                rig.disable_all()
                return last_q
        else:
            stalled = 0
    rig.disable_all()
    say(f"    reached the {CREEP_CEILING_RAD} rad creep ceiling without "
        f"stalling (q={last_q:+.3f}).")
    say("    Either this joint has no hard stop in this direction, or the")
    say("    creep torque is too low to move it. Recorded as NOT MEASURED.")
    return None


def step_limits(rig, op, motors):
    """Find each joint's travel by creeping into both stops.

    The recorded soft limit sits LIMIT_MARGIN_RAD inside the measured hard
    stop. That margin is larger than the pump's own per-tick slew
    (MAX_STEP_MM/REACH_MAX ~= 0.004 rad), so the controller cannot step
    from inside the soft limit into the metal in one tick.
    """
    rule("STEP 3 of 5 — JOINT LIMITS")
    say("  Each joint is creeped into its stop under a LOW torque cap, both")
    say("  directions, and the soft limit is set "
        f"{LIMIT_MARGIN_RAD} rad short of the stop.")
    say()
    say("  GRAVITY WARNING for J1 and J2: creeping DOWNWARD, gravity helps")
    say("  and the joint arrives faster than commanded. Orient the arm so")
    say("  gravity OPPOSES the creep, or take the weight by hand.")
    say()
    say("  Skipping a joint keeps its conservative placeholder limit, which")
    say("  is safe but may refuse poses the arm can actually reach.")
    say()
    limits = []
    for i, name in enumerate(JOINT_NAMES):
        placeholder = openyam.JOINT_LIMITS[i]
        say(f"  {name}   placeholder {placeholder}")
        t_max = damiao.MOTOR_LIMITS[motors[i]][2]
        lo = hi = None
        if op.confirm(f"Creep {name} to its POSITIVE stop?", danger=True):
            q = creep_to_stop(rig, op, i, motors[i], +1, t_max)
            if q is not None and op.yesno(
                    "    Did it reach a mechanical stop (not a cable snag)?"):
                hi = q - LIMIT_MARGIN_RAD
        if op.confirm(f"Creep {name} to its NEGATIVE stop?", danger=True):
            q = creep_to_stop(rig, op, i, motors[i], -1, t_max)
            if q is not None and op.yesno(
                    "    Did it reach a mechanical stop (not a cable snag)?"):
                lo = q + LIMIT_MARGIN_RAD
        if lo is None or hi is None:
            say(f"    not fully measured — keeping placeholder {placeholder}")
            limits.append(tuple(placeholder))
        elif lo >= hi:
            # Both stops found but they crossed. That is not a narrow joint,
            # it is a bad measurement (most often the joint was creeped from
            # opposite sides of a wrap point), and recording it would make
            # clamp() return the wrong bound for every angle.
            say(f"    !! measured lo={lo:+.3f} >= hi={hi:+.3f} — impossible.")
            say(f"    !! keeping placeholder {placeholder}. Re-measure.")
            limits.append(tuple(placeholder))
        else:
            say(f"    measured ({lo:+.3f}, {hi:+.3f}) rad")
            limits.append((round(lo, 4), round(hi, 4)))
        say()
    return limits


# ---- step 4: link lengths -------------------------------------------------

def step_links(rig, op):
    """Tape measure, axis to axis. No motion.

    THE MEASUREMENT IS AXIS-TO-AXIS, NOT SURFACE-TO-SURFACE, and the axes
    are inside the housings where a tape cannot reach. _solve_ik treats
    these as ideal segments meeting at points, so a surface measurement is
    wrong by half a housing at each end.

    Find each axis by the three-point circle construction: de-energise,
    tape a marker far out on the distal link, swing it by hand through a
    wide arc, mark three well-separated positions, and the perpendicular
    bisectors of the two chords cross at the axis.

    +/-3mm per link. Three links worst-case aligned is 9mm, just inside
    the 10mm budget docs/CALIBRATION.md sets (the sponge compresses
    10-15mm, so under 10mm of error disappears into the foam).
    """
    rule("STEP 4 of 5 — LINK LENGTHS")
    say("  No motion in this step. Power can stay off.")
    say()
    say("  Measure AXIS TO AXIS, not surface to surface. The rotation axes")
    say("  sit inside the housings. To find one: de-energise, tape a marker")
    say("  far out on the link, swing it by hand through a wide arc, mark")
    say("  three spread-out points, and draw the perpendicular bisectors of")
    say("  the two chords. They cross at the axis.")
    say()
    say("  Target accuracy +/-3mm per link. The three errors can add, and")
    say("  the budget is 10mm total (the sponge compresses 10-15mm).")
    say()
    say("  L_BASE is measured from the BENCH MOUNTING PLANE, because the")
    say("  safety box's whole z axis is referenced to it.")
    say()
    lb = op.ask_float("L_BASE  bench plane -> shoulder axis (mm)",
                      openyam.L_BASE)
    lu = op.ask_float("L_UPPER shoulder axis -> elbow axis (mm)",
                      openyam.L_UPPER)
    lf = op.ask_float("L_FORE  elbow axis -> wrist axis (mm)",
                      openyam.L_FORE)
    reach = lu + lf
    say()
    say(f"  reach = L_UPPER + L_FORE = {reach:.0f}mm")
    # Anvil publishes 610mm reach without the gripper. A measurement far
    # from that is more likely a tape error than a surprising arm.
    if not 0.85 * 610 <= reach <= 1.15 * 610:
        say(f"  !! Anvil publishes 610mm reach. {reach:.0f}mm is well off.")
        say("  !! Check you measured axis-to-axis and in millimetres.")
    else:
        say("  consistent with Anvil's published 610mm reach.")
    say()
    say("  VERIFY WITH THREE POINTS, NOT ONE. _solve_ik returns a confident")
    say("  answer for wildly wrong link lengths — it cannot detect its own")
    say("  error, exactly like the 4-point homography in docs/CALIBRATION.md")
    say("  reading residual 0.000000 no matter how wrong it is. Put a")
    say("  pointer in the gripper, command a near point, a far point and an")
    say("  off-axis point, and measure all three with the tape. One point")
    say("  can be hit by two link errors cancelling; three cannot.")
    return lb, lu, lf


# ---- step 5: home pose ----------------------------------------------------

def step_home(rig, op, motors, limits):
    """Choose and TEST the tucked home pose.

    HOME_Q is a consequence of steps 1-4, not a free choice, and it is the
    highest-exposure of the five: scrubbot.py constructs the arm and calls
    go_home() on the very next line, BEFORE the torque watchdog thread
    exists. A bad home pose is the arm's first physical act of every run
    with nothing watching it.

    Tested at HOMING gains, not teleop gains. HOMING_KP is 10 where
    TELEOP_KP is 65 — a pose the arm holds firmly at 65 can sag at 10, and
    10 is what go_home() actually sends.
    """
    rule("STEP 5 of 5 — HOME POSE")
    say("  Home must be: inside every measured limit with margin, folded")
    say("  clear of the workspace, and HOLDABLE at the soft homing gains.")
    say()
    default = list(openyam.HOME_Q)
    home = []
    for i, name in enumerate(JOINT_NAMES):
        lo, hi = limits[i]
        v = op.ask_float(f"{name} home angle (rad), limits "
                         f"({lo:+.2f},{hi:+.2f})", default[i])
        # 0.15 rad of margin so the pump's clamp never silently truncates
        # the home pose -- that clamp logs nothing and the arm would simply
        # go somewhere else forever.
        if not lo + 0.15 <= v <= hi - 0.15:
            say(f"    !! {v:+.3f} is within 0.15 rad of a limit "
                f"({lo:+.2f},{hi:+.2f}).")
            say("    !! _pump clamps silently, so the arm would home "
                "somewhere else with no log line.")
            v = max(lo + 0.15, min(hi - 0.15, v))
            say(f"    !! pulled in to {v:+.3f}")
        home.append(round(v, 4))
        say()

    say("  HOLD TEST: command this pose at the homing gains and leave it for")
    say("  10 seconds. Drift over 0.05 rad means the pose sags at kp=10 and")
    say("  must be folded tighter — it is not holdable where go_home() puts")
    say("  it.")
    if op.confirm("Run the 10-second hold test?", danger=True):
        for _ in range(6):
            for i in range(6):
                rig.enable(i)
                rig.send_position(i, motors[i], home[i],
                                  openyam.HOMING_KP[i], openyam.HOMING_KD[i])
            time.sleep(0.05)
        say("    holding — watching for 10s")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 10.0:
            for i in range(6):
                rig.send_position(i, motors[i], home[i],
                                  openyam.HOMING_KP[i], openyam.HOMING_KD[i])
            time.sleep(0.05)
        worst = 0.0
        for i in range(6):
            fb = rig.read_joint(i, motors[i])
            if fb:
                drift = abs(fb["q"] - home[i])
                worst = max(worst, drift)
                flag = "  SAGS" if drift > 0.05 else ""
                say(f"    {JOINT_NAMES[i]}: drift {drift:.3f} rad{flag}")
        rig.disable_all()
        if worst > 0.05:
            say(f"    !! worst drift {worst:.3f} rad exceeds 0.05.")
            say("    !! Fold the pose tighter and re-run this step.")
        else:
            say(f"    holds at homing gains (worst drift {worst:.3f} rad)")
    else:
        say("    hold test SKIPPED — home pose is UNVERIFIED")

    say()
    say("  APPROACH PATH: go_home() slews all six joints at once, so the")
    say("  path is a straight line in joint space that BULGES OUTWARD in")
    say("  cartesian space. From every joint extreme, watch where that bulge")
    say("  goes. If it sweeps through where a person stands, the answer is a")
    say("  STAGED go_home (Anvil's own config ships three home vectors, not")
    say("  one — that staging probably exists for exactly this reason), not")
    say("  a less-folded home pose.")
    return home


# ---- output ---------------------------------------------------------------

def write_config(path, motors, limits, links, home, signs, dry):
    lb, lu, lf = links
    doc = {
        "_comment": "Measured by tools/calibrate-openyam.py. "
                    "py/openyam.py loads this at import and refuses to "
                    "drive real hardware without it.",
        "measured": time.strftime("%Y-%m-%d"),
        "joint_motors": [m.name for m in motors],
        "joint_limits": [[round(lo, 4), round(hi, 4)] for lo, hi in limits],
        "link_lengths_mm": {"base": lb, "upper": lu, "fore": lf},
        "home_q": home,
        "joint_sign": signs,
        "_torque_note": "contact_tau and estop_tau are in N·m and are "
                        "measured LAST, against a moving arm: press the "
                        "sponge into a scale, read the per-joint tau. "
                        "Omit them and openyam.py keeps its conservative "
                        "placeholders. contact_tau must be strictly below "
                        "estop_tau.",
    }
    body = json.dumps(doc, indent=2) + "\n"
    if dry:
        rule("WOULD WRITE " + os.path.relpath(path, ROOT))
        say(body)
        return
    with open(path, "w") as fh:
        fh.write(body)
    say(f"  wrote {path}")


# ---- main -----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Measure the five CALIBRATE values in py/openyam.py.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the steps without touching CAN or the arm")
    ap.add_argument("--port", default=None,
                    help="CAN interface (default: $CAN_IFACE or can0)")
    ap.add_argument("--out", default=OUT_PATH,
                    help="where to write the calibration")
    args = ap.parse_args()

    op = Operator(args.dry_run)

    rule("OpenYAM CALIBRATION")
    if args.dry_run:
        say("  DRY RUN — no CAN, no motion. Every motion prompt is skipped")
        say("  and prints what it would have done.")
    say()
    say("  BEFORE ANYTHING MOVES:")
    say("    * the base is CLAMPED to the bench")
    say("    * NOBODY is within reach of the arm — not just out of the way")
    say("    * your hand is on the SUPPLY SWITCH")
    say()
    say("  The supply switch, not the software estop, is the stop that")
    say("  matters here. estop() cuts TORQUE, and a shoulder or elbow")
    say("  holding the arm's weight FALLS when torque is cut. On J1 and J2")
    say("  the safe stop is killing power while a hand takes the weight.")
    say()
    say("  Every motion prompt below defaults to NO. Bare ENTER skips it.")

    rig = Rig(args.dry_run, args.port)
    try:
        if not args.dry_run and not rig.link_ok:
            # REFUSE. A calibration session against an arm that is not
            # answering would record placeholder-derived values under a
            # "measured" timestamp, which is worse than no file at all --
            # openyam.py would drop its gate and drive real hardware on
            # numbers nobody measured.
            say()
            say("  *** NO JOINT IS ANSWERING ON CAN. REFUSING TO RUN. ***")
            say()
            say("  A session run now would write a file stamped 'measured'")
            say("  containing placeholders, and that file switches OFF the")
            say("  gate that currently stops the arm moving. Fix the bus.")
            say()
            say("    ip -details link show can0    # up? bitrate 1000000?")
            say("    candump can0                  # any frames at all?")
            say("    sudo ip link set can0 up type can bitrate 1000000")
            say()
            say("  Silence is more often a BITRATE mismatch than a dead")
            say("  motor — Damiao units ship from 125kbit to 10Mbit.")
            return 2

        motors = step_motors(rig, op)
        signs = step_signs(rig, op, motors)
        limits = step_limits(rig, op, motors)
        links = step_links(rig, op)
        home = step_home(rig, op, motors, limits)
        write_config(args.out, motors, limits, links, home, signs,
                     args.dry_run)

        rule("DONE")
        if args.dry_run:
            say("  Nothing was measured and nothing was written. This was a")
            say("  rehearsal of the questions, so nobody meets them for the")
            say("  first time with a live arm on the bench.")
        else:
            say("  py/openyam.py will load this on its next import and stop")
            say("  refusing to drive hardware.")
            say()
            say("  STILL UNMEASURED after this tool: contact_tau and")
            say("  estop_tau. They need a moving arm pressing on a scale.")
            say("  Until they are set, the torque cutout uses conservative")
            say("  placeholders — it will trip EARLY, not late.")
            say()
            say("  Verify before going near a person: command three known")
            say("  points and measure all three with a tape.")
        return 0
    finally:
        rig.close()


if __name__ == "__main__":
    sys.exit(main())
