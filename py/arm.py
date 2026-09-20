"""py/arm.py — RoArm-M2-S over USB serial.

No SDK: the official roarm-sdk is AGPL-3.0 (despite an MIT classifier on
PyPI), hard-returns -1 for feedback reads over HTTP, and its
drag_teach_replay plays back per-joint-sequentially with a sleep after EACH
joint (4x slower, jerky). Twenty of your own lines is less code than reading
its docs.

THE WHOLE PROTOCOL IS newline-terminated JSON. The ESP32 firmware solves IK
onboard (RoArm-M2_module.h :: simpleLinkageIkRad), so you send MILLIMETRES
and it computes angles. No ikpy, no URDF, no MoveIt, no ROS.

BEFORE DAY 1: macOS 12+ has NO built-in CP210x driver and the RoArm driver
board uses two CP2102 chips. Install the SiLabs VCP driver, approve the
blocked system extension in Privacy & Security, and REBOOT. The port will
not appear otherwise.
  https://www.silabs.com/products/development-tools/software/usb-to-uart-bridge-vcp-drivers
"""
import glob, json, math, threading, time
import serial

# Link lengths from firmware RoArm-M2_config.h (mm). Used ONLY to validate
# reachability before sending -- we never compute joint angles ourselves.
ARM_L1 = 126.06                                   # base height
ARM_L2 = math.hypot(236.82, 30.00)                # shoulder -> elbow  (~238.7)
ARM_L3 = math.hypot(280.15, 1.73)                 # elbow -> EoAT      (~280.2)
REACH_MAX = ARM_L2 + ARM_L3                       # ~519 mm planar
REACH_MIN = abs(ARM_L2 - ARM_L3)

HOME = (235.11, 0.0, 234.79, 3.14)                # exact init pose, json_cmd.h

# Safety box in robot mm. TIGHTEN against YOUR table before the demo.
BOX = dict(xmin=120.0, xmax=420.0, ymin=-260.0, ymax=260.0,
           zmin=25.0, zmax=380.0)
MAX_STEP_MM = 6.0                                 # per 25ms tick -> 240 mm/s

# Torque cutout thresholds, per joint, in this firmware's raw feedback units.
# These bound how hard the arm may press a person before it stops.
#
# WHICH joints are checked is decided below and is NOT obvious: torS and torE
# are both excluded, for two different measured reasons. An earlier version of
# this comment said the elbow "carries the sponge, so checking only the
# shoulder missed the joint most likely to spike on contact" -- which is true
# about the physics and wrong about the SIGNAL, because on torE gravity and
# contact are the same size. See _CUTOUT_JOINTS.
#
# LIVES HERE, NOT IN scrubbot.py, because both the names and the units are
# this firmware's. openyam.py carries its own ceiling in N·m; the two are not
# interchangeable and comparing one against the other never trips -- which is
# what a shared dict in scrubbot.py silently did the moment a second arm
# existed. over_torque() below is how the watchdog asks without knowing either.
#
# DERIVED FROM THE CAPS THIS FILE ITSELF SENDS, NOT TYPED IN.
# ------------------------------------------------------------------
# The previous values were the constants 850/700/900/600. Against the T:112
# caps Arm.TORQUE_CAPS sets at connect -- b=60, s=110, e=50, h=50 -- NONE of
# them was reachable, so the torque watchdog in scrubbot.feedback_loop ran at
# 10Hz for the whole demo and could never fire. A servo's effort register is
# bounded by its own torque limit (SCServo SMS_STS, and ARMS.md:255 says so
# for this board), so capping at 110 and then testing for 850 is the same
# silent no-op that over_torque()'s own docstring was written to end -- one
# level up. No exception, no log line, and code that still looked like it had
# a cutout.
#
# MEASURED, by execution: driving this Arm against scrub3d/live/fake_esp32.py
# (which clamps its reported loads to the T:112 cap exactly as the firmware
# does) with the sponge stalled 60mm INSIDE a surface, the board reported
# torB/torS/torE/torH = 60/110/50/50 -- every joint pinned at its cap -- and
# over_torque() returned None while contact read False.
#
# So the ceiling is now a FRACTION OF EACH JOINT'S OWN CAP, which is the only
# scale the reading actually has. A servo at >=90% of its cap is giving
# nearly all it is allowed to: it is pushing on something. The ratio is
# arm_hw.py's LOAD_NEAR_CAP, the one number in this repo that was chosen
# against this firmware's effort register rather than invented.
#
# Keyed off TORQUE_CAPS so the two can never drift apart again: change a cap
# and the cutout follows it. torS is EXCLUDED -- see NEAR_CAP below.
NEAR_CAP = 0.9              # arm_hw.py:LOAD_NEAR_CAP

# And the contact signal, which must trip STRICTLY BEFORE the cutout or the
# sponge can never touch a forearm without also stopping the demo. That
# ordering is a rule openyam.py's _load_calibration already enforces between
# CONTACT_TAU and ESTOP_TAU and refuses to load a file that inverts; this is
# the RoArm's version of the same number. 0.7 of the cap is a servo working
# hard but not pinned.
CONTACT_NEAR_CAP = 0.7

# torS is the SHOULDER, and on this board it is one shoulder servo's live
# load minus the other's, read once at boot (ARMS.md, RoArmM2_getPosByServoFeedback).
# It therefore carries an unknown offset and is NOT a straight fraction of its
# cap. torB and torE are single-servo loads and do not. arm_hw.py excludes torS
# from its own bump test (BUMP_KEYS = torB, torE) for exactly this reason, and
# the cutout has to make the same exclusion or the demo estops on a boot
# offset with nothing under the sponge.
#
# torE is EXCLUDED TOO, and this one was decided by measurement rather than
# by reading the firmware. Sweeping all 58,968 reachable points inside BOX
# through this repo's own gravity model of the arm (fake_esp32._loads) with
# NOTHING under the sponge, the free-air maximum per joint is:
#
#     torB   5.0 of cap  60   ( 8.3%)    headroom 55.0
#     torS  78.3 of cap 110   (71.1%)    headroom 31.7
#     torE  39.1 of cap  50   (78.3%)    headroom 10.9   <-- at (420,-260,25)
#     torH   5.0 of cap  50   (10.0%)    headroom 45.0
#
# torE at full reach already sits at 78% of its own cap holding NOTHING BUT
# ITS OWN WEIGHT. Any fixed fraction low enough to catch a press is also low
# enough to fire on an extended arm in free air -- measured: a 0.9 cutout on
# torE fired on a 2mm graze, and a 0.7 contact test on torE read True at the
# far corner with the surface removed entirely. That is the bug py/contact.py
# exists to explain: raw torque is dominated by gravity, and gravity depends
# on where the arm is, not on what is under it.
#
# 10.9 units of headroom is not a threshold, it is noise. So torE is left to
# py/contact.py, which subtracts the gravity term and tests the RESIDUAL --
# the only way this joint can be read honestly. The cutout keeps torB and
# torH, which have >90% headroom and are therefore separable.
#
# torH is the gripper wrist, which holds the sponge and is not load-bearing
# against a forearm; it is kept because a spike there is still a real jam.
_CUTOUT_JOINTS = ("torB", "torH")

# The caps themselves, at module scope so the cutout can be derived from them.
# Arm.TORQUE_CAPS is built from this so there is ONE set of numbers: the
# previous arrangement had the caps inside the class and the cutout outside
# it, which is how they drifted into incomparable scales in the first place.
TORQUE_CAP_VALUES = {"b": 60, "s": 110, "e": 50, "h": 50}

# joint key -> the cap letter it is bounded by, as in arm_hw.py:LOAD_CAP_KEY.
_CAP_KEY = {"torB": "b", "torS": "s", "torE": "e", "torH": "h"}

TORQUE_ESTOP = {j: NEAR_CAP * TORQUE_CAP_VALUES[_CAP_KEY[j]]
                for j in _CUTOUT_JOINTS}


def reachable(x, y, z):
    """True if the firmware's 2-link IK will find a solution.

    THIS FUNCTION IS MANDATORY, NOT DEFENSIVE. T:1041 has NO nanIK guard
    (uart_ctrl.h dispatches it as a bare baseCoordinateCtrl + goalPosMove).
    Only T:104 checks nanIK. And Arduino's constrain() is a comparison macro
    -- every comparison against NaN is false -- so NaN passes the joint
    clamps untouched, and the (int16_t) cast of NaN yields 0, commanding the
    shoulder toward servo-MIDDLE. An unreachable target would whip the arm.
    """
    r = math.hypot(x, y)
    s = z - ARM_L1
    d = math.hypot(r, s)
    return REACH_MIN <= d <= REACH_MAX


class Arm:
    # CLASS-LEVEL DEFAULTS for every safety-relevant field. Tests construct
    # Arm via __new__ to bypass the serial port, and a field that only exists
    # after __init__ then raises AttributeError from inside estop() -- the one
    # method that must never fail. Instance assignment in __init__ shadows
    # these; they are the floor, not the source of truth.
    estopped = False
    estop_confirmed = False
    abort_requested = False
    contact = False
    clamped_hard = False
    dry = False
    last_sent = None
    feedback = {}
    _clear_token = 0
    _estop_gen = 0
    _feedback_lock = None
    _allow_feedback_during_estop = False
    _last_clamp_warn = 0.0
    _last_write_warn = 0.0
    _write_fail_count = 0
    # CONSECUTIVE failures, not the running total. The total never resets, so
    # one bad write an hour ago would mark a healthy arm as dead forever.
    _consec_write_fail = 0

    def __init__(self, port=None, baud=115200, dry_run=False):
        self.dry = dry_run
        self.lock = threading.Lock()
        self.target = HOME
        self.last_sent = None
        self.feedback = {}
        self.contact = False
        self.estopped = False
        self.clamped_hard = False       # set when a target was relocated >50mm
        self.estop_confirmed = False    # did the last estop actually go out?
        # Set by a FAILED estop. `estopped` must stay False so the pump can
        # execute the retreat, but the STATE MACHINE must still stop -- or
        # SCRUB re-targets the forearm every frame and overwrites the HOME
        # retreat we just requested. Two correct behaviours combining into a
        # wrong one; found by an adversarial audit.
        self.abort_requested = False
        # Bumped by estop() and by clear_estop(); a mismatch means something
        # estopped while we were mid-clear.
        self._clear_token = 0
        # Guards the estop latch itself. NOT self.lock -- the pump holds that.
        self._estop_lock = threading.Lock()
        # poll_feedback() does reset_input_buffer + write + sleep + readline.
        # The torque watchdog calls it at ~9Hz from ITS thread while
        # clear_estop() calls it too -- two interleaved reads on one serial
        # handle can hand each other the wrong reply, and clear_estop uses
        # that reply to re-seed the rate limiter. Serialise them.
        self._feedback_lock = threading.Lock()
        self._estop_gen = 0
        # Lets clear_estop() read the true position while the pump stays
        # parked. The pump does NOT read this flag.
        self._allow_feedback_during_estop = False
        self._last_clamp_warn = 0.0
        self._last_write_warn = 0.0
        self._write_fail_count = 0
        self._consec_write_fail = 0
        self._run = True
        self.ser = None

        if not self.dry:
            port = port or find_port()
            # write_timeout IS MANDATORY. Without it pyserial blocks FOREVER
            # on a full OS write buffer -- which is exactly what a wedged or
            # unplugged ESP32 produces. The 40Hz pump thread would deadlock
            # holding no lock but never sending again, the feedback thread
            # would block behind it, and estop() -- which also calls _send --
            # would HANG. The operator presses SPACE and nothing happens while
            # the arm rests on someone's forearm. Found by an adversarial
            # audit. 0.2s is ~8 pump ticks: long enough to never trip on a
            # healthy link, short enough that a dead one is obvious.
            self.ser = serial.Serial(port, baud, timeout=0.3, write_timeout=0.2)
            # CRITICAL ON macOS: without BOTH of these the driver board's
            # auto-download circuit yanks the ESP32 into RESET every time you
            # open the port. You will lose an hour thinking the arm is broken.
            # (Waveshare's own SDK sets rts=False but never dtr -- the footgun
            # is in their code too.)
            #
            # Guarded because not every serial-like device supports the modem
            # control ioctls: a pty raises OSError(25) "Inappropriate ioctl for
            # device", and an unguarded call takes the WHOLE constructor down.
            # A real CP210x supports both; anything that does not is a device
            # where the auto-reset circuit does not exist either, so skipping
            # is correct rather than merely tolerable.
            for name, fn in (("RTS", self.ser.setRTS), ("DTR", self.ser.setDTR)):
                try:
                    fn(False)
                except (OSError, IOError, NotImplementedError) as e:
                    print(f"[arm] note: could not clear {name} ({e}) -- "
                          f"fine on a pty/virtual port, unexpected on a CP210x")
            time.sleep(2.0)                        # ESP32 boot settle

            self.apply_settings()
            print(f"[arm] connected on {port}, torque caps applied, "
                  f"ESP-NOW follower mode off")
        else:
            print("[arm] DRY RUN — no serial, commands logged only")

        threading.Thread(target=self._pump, daemon=True).start()

    # T:112 CMD_DYNAMIC_ADAPTATION -- cap per-joint torque so the arm YIELDS
    # to contact instead of pushing through a person's forearm. THE single
    # most valuable command in the protocol for a machine touching human skin.
    # Firmware default is 1000.
    # Built from the module-level TORQUE_CAP_VALUES so that TORQUE_ESTOP,
    # which is a fraction of these, cannot drift out of scale with them. The
    # literal numbers are unchanged: b=60, s=110, e=50, h=50.
    TORQUE_CAPS = {"T": 112, "mode": 1, **TORQUE_CAP_VALUES}

    # T:301 CMD_ESP_NOW_CONFIG, mode 0 = neither leader nor follower.
    #
    # THE FIRMWARE BOOTS AS A FOLLOWER. RoArm-M2_config.h ships ESP-NOW on and
    # this board answers to any leader within radio range -- so a second
    # RoArm, a Waveshare demo remote, or anything else broadcasting on that
    # protocol can drive this arm while it is on a person's forearm, with the
    # USB link healthy and this program none the wiser. Nothing in here would
    # see it: the pump keeps sending T:1041, feedback keeps arriving, and the
    # arm simply goes somewhere it was not sent.
    #
    # scrub3d/live/arm_hw.py has sent this since it was written (its Link
    # .settings(), and ARMS.md "otherwise any nearby leader can move the
    # arm"). py/arm.py never did. Same board, same firmware, same wire
    # protocol -- the demo arm was the one left open.
    ESPNOW_OFF = {"T": 301, "mode": 0}

    # Re-send every settings command this often, not just at connect.
    #
    # A brownout-reset ESP32 comes back at the FIRMWARE DEFAULTS: torque cap
    # 1000, echo on, ESP-NOW follower. apply_torque_caps' own note records
    # that this is a routine failure here ("Arm limp / ESP32 reset. Brownout.
    # Reconnect, press r.") and that the caps were being sent exactly once.
    # Pressing 'r' re-asserts them, but only if the operator NOTICES the
    # reset -- a board that resets mid-scrub and recovers on its own never
    # gets them back. arm_hw.py re-sends on a 10 s timer for this reason
    # (SETTINGS_EVERY_S); matching it costs three JSON lines a minute on a
    # link that carries 40 a second.
    SETTINGS_EVERY_S = 10.0

    def apply_settings(self):
        """(Re-)assert every setting a rebooted board would have lost.

        Torque caps, debug echo, ESP-NOW. Safe to call any number of times.
        """
        self._send({"T": 605, "cmd": 0})       # silence firmware debug spam
        self._send(dict(self.ESPNOW_OFF))
        return self.apply_torque_caps()

    def apply_torque_caps(self):
        """(Re-)assert the torque limits. Safe to call any number of times.

        THIS WAS SENT ONCE AT CONSTRUCTION AND NEVER AGAIN. The recovery card
        documents ESP32 brownout-reset as a routine failure -- "Arm limp /
        ESP32 reset. Brownout. Reconnect, press r." -- and a reset ESP32 comes
        back with the FIRMWARE DEFAULT cap of 1000, not 60/110/50/50. The
        operator pressed r, the arm resumed, and it pushed with ~16x the
        intended shoulder torque into someone's forearm with nothing on screen
        to say the safety limit was gone.

        Measured before the fix: T:112 count after estop+clear was 1 -- the
        same one from connect, zero re-assertions.
        """
        return self._send(dict(self.TORQUE_CAPS))

    # ----------------------------------------------------------- transport --
    def _send(self, d):
        """Write one JSON command. Returns True if the bytes were handed to
        the OS, False on any failure. NEVER raises -- the pump must not die.

        The return value matters for estop(): swallowing the failure there
        meant the operator saw "*** EMERGENCY STOP ***" while the command had
        not reached the arm at all.
        """
        if self.dry:
            return True
        try:
            self.ser.write((json.dumps(d) + "\n").encode())
            self._consec_write_fail = 0
            return True
        except serial.SerialTimeoutException:
            # The write buffer is full: the arm is not draining it. Say so
            # once a second and keep going -- never block the pump.
            self._write_fail_count += 1
            self._consec_write_fail += 1
            now = time.time()
            if now - self._last_write_warn > 1.0:
                self._last_write_warn = now
                print(f"[arm] serial WRITE TIMEOUT ({self._write_fail_count}) "
                      f"— the arm is not reading. Check power and USB.")
            return False
        except serial.SerialException as e:
            # RATE-LIMITED. The pump calls this at 40Hz, so an unplugged USB
            # cable printed 40 lines/second and buried the clamp warning the
            # recovery card tells the operator to watch for. One line per
            # second, with a running count so the scale is still visible.
            self._write_fail_count += 1
            self._consec_write_fail += 1
            now = time.time()
            if now - self._last_write_warn > 1.0:
                self._last_write_warn = now
                print(f"[arm] serial write failing ({self._write_fail_count} so far): {e}")
                print("[arm]   check the USB cable — the arm is NOT receiving commands")
            return False

    @property
    def link_ok(self):
        """False when the arm has stopped acknowledging writes.

        THE DEMO MUST NOT LIE. Measured by pulling the USB cable mid-scrub:
        the arm received ZERO further commands (115 before, 115 after) while
        the FSM marched the counter 33 -> 67 -> 100 and the projector threw
        confetti -- with contact=True on every event, so the page claimed
        TORQUE-CONFIRMED CONTACT from an arm that was not plugged in. arm.py
        knew the whole time; scrubbot.py never asked.

        CONSECUTIVE failures, not the total: the total never resets, so one
        bad write an hour ago would mark a healthy arm dead forever. 12 is
        ~0.3s at the 40Hz pump -- long enough that a hiccup or a single
        timeout never trips it, short enough that a pulled cable is caught
        well inside one scrub.

        A dry-run arm is "ok": --no-arm is a deliberate mode, not a fault.
        """
        if self.dry:
            return True
        return self._consec_write_fail < 12

    def clamp_to_box(self, x, y, z):
        """Where this driver would ACTUALLY send (x,y,z). -> (cx, cy, cz).

        EXISTS SO THE SAFETY GOVERNOR CAN JUDGE THE COMMANDED POINT RATHER
        THAN THE REQUESTED ONE. scrubbot.py's govern_target() asks the
        governor about a target before handing it to set_target, and
        set_target then clamps it -- so without this the governor would rule
        on a point the arm was never going to visit. Measured against this
        box and the demo's own limb model: governing the RAW target refuses
        9369 reachable points whose clamped, actually-commanded point is
        clear, every one of them reported as "unreachable" when the box was
        about to pull it into reach. That is a person drifting past the table
        edge and the demo stalling instead of scrubbing.

        IT MUST BE A DRIVER METHOD, NOT `arm.BOX` READ AT THE CALL SITE.
        openyam.py's box is a different box (150..520 vs 120..420 in x, and a
        different z floor), so a call site that reached for this module's BOX
        would clamp RoArm numbers onto an OpenYAM and the governor would then
        bless a point that driver never sends. Asking the arm keeps the two
        from disagreeing, the same reasoning as over_torque() and hold().
        """
        B = BOX
        return (min(max(x, B["xmin"]), B["xmax"]),
                min(max(y, B["ymin"]), B["ymax"]),
                min(max(z, B["zmin"]), B["zmax"]))

    def set_target(self, x, y, z, t=3.14):
        """Called from the vision loop at any rate. Non-blocking.
        Clamp the CARTESIAN POINT here; the pump rate-limits the MOTION.
        Rate-limiting the point instead lets a small Cartesian step become a
        huge joint step near a singularity."""
        # ONE CLAMP, SHARED WITH THE GOVERNOR. Inlining the three min/max
        # pairs here again would let this box and clamp_to_box's drift, and
        # the whole point of clamp_to_box is that the governor rules on the
        # number this line produces.
        cx, cy, cz = self.clamp_to_box(x, y, z)

        # WARN ON A LARGE CLAMP. The box clamp is the safety net, but it also
        # SILENTLY RELOCATES a bad request: set_target(2000,0,200) used to
        # return True while quietly driving to x=420. A caller with a broken
        # homography would see the arm move confidently to the wrong place and
        # get no signal at all. Surfaced by the protocol test.
        #
        # Small clamps are normal (the forearm drifts past a box edge). A large
        # one means the transform is wrong, so say so -- rate-limited to one
        # line per second so a stuck loop cannot flood the console mid-demo.
        d = math.dist((x, y, z), (cx, cy, cz))
        # "IS THIS HAPPENING NOW", NOT "DID THIS EVER HAPPEN". This used to
        # only ever be set True, never cleared, so it latched for the life of
        # the process -- while openyam.py reassigned it every call. Two
        # drivers, two meanings, one attribute name, and nothing reading it
        # yet: the first consumer would have been correct against one arm and
        # wrong against the other with no test to catch it. "Now" wins because
        # a latched flag can never report recovery, so an operator who fixes
        # the homography mid-demo gets no signal that they fixed it.
        self.clamped_hard = False
        if d > 50.0:
            self.clamped_hard = True
            now = time.time()
            if now - self._last_clamp_warn > 1.0:
                self._last_clamp_warn = now
                print(f"[arm] WARNING: target ({x:.0f},{y:.0f},{z:.0f}) clamped "
                      f"{d:.0f}mm to ({cx:.0f},{cy:.0f},{cz:.0f}) -- "
                      f"check the homography")

        if not reachable(cx, cy, cz):
            return False                            # hold last, never guess
        with self.lock:
            self.target = (cx, cy, cz, t)
        return True

    def hold(self):
        """Stay exactly where the last target put us. Costs nothing, never fails.

        THE POINT IS THAT THE CALLER NEVER READS .target. scrubbot.py held
        position during an occluded scrub frame with `set_target(*arm.target)`,
        which only works while target is the same shape set_target takes --
        true here, a TypeError on the OpenYAM's six joint radians. Both arms
        answer hold() instead, so the scrub loop cannot leak either arm's
        internal pose shape.
        The pump is already re-sending self.target at 40Hz, so this really is
        a no-op on this driver -- but it stays a CALL because a driver that
        streams rather than latches would need to do work here.
        """
        return True

    def joint_pose(self):
        """The commanded positioning angles, as (j0, j1, j2) radians, or None.

        THIS ARM NEVER COMPUTES ANGLES -- the ESP32 solves IK onboard and we
        send millimetres (see the module docstring). So unlike openyam.py,
        where the answer is a slice of what was sent, here it has to be
        SOLVED: scrub3d/kinematics.ik() inverts the vendor's own URDF for
        this exact chain, which is the closest honest answer available
        without reading encoders back.

        Returned as radians in the SAME convention openyam.joint_pose()
        uses, which is what makes one contract out of two different arms.

        None on any failure, and None means "I do not know", never a guess:
        an unreachable target, a solve that misses its limits, or a host
        without numpy/scrub3d installed. Every caller must handle it. The
        only consumer today is the projector's cartoon, which falls back to
        its own posing when the answer is None -- the arm being unsure must
        never blank the demo.

        IMPORTED LAZILY, AND THAT IS NOT STYLE. scrub3d pulls numpy in, and
        arm.py runs on a Pi that may have neither; a module-level import
        would make this driver fail to load over a feature it does not need.
        Same reasoning as scrubbot.py's refusal to import mediapipe at the
        top for replay mode.
        """
        with self.lock:
            x, y, z = self.target[0], self.target[1], self.target[2]
        try:
            import sys, os
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.abspath(__file__)), ".."))
            from scrub3d import kinematics
            return kinematics.ik(x, y, z)
        except Exception:
            return None

    def cartesian_pose(self):
        """Best estimate of the tool point in robot mm, as (x, y, z).

        On this arm target IS cartesian mm, so this is a slice -- but it is
        a slice made HERE, by the driver that knows the shape, instead of at
        the call site. scripted.py used to do `arm.target[:3]` itself, which
        is correct for this arm and silently returns joint radians on the
        OpenYAM, with motion.travel unable to tell the difference because
        both are length 3.
        """
        with self.lock:
            return (self.target[0], self.target[1], self.target[2])

    def over_torque(self):
        """(joint, torque, limit) for the first joint past its cutout, else None.

        THE DRIVER OWNS THE CUTOUT DECISION. scrubbot.py's watchdog used to
        name this firmware's joint keys (torS/torE/torB/torH) directly, which
        made it a silent no-op the moment the arm underneath was not a RoArm:
        every check compared 0 against the limit and passed, with no exception
        and no log line. Asking the arm instead of naming its joints keeps
        that hole from reopening on either driver.

        WHY torS IS NO LONGER CHECKED, and why removing it is safe.
        TORQUE_ESTOP now covers torB/torE/torH and not torS. That is a
        REDUCTION in what is checked, so it needs justifying rather than
        assuming: before this change the shoulder was nominally checked at
        850 against a cap of 110, which no reading could ever reach -- so
        zero real cutouts are lost. What replaces it is a torE cutout at 45
        that CAN fire, on the joint py/arm.py's own comment above already
        names as the one carrying the sponge and "most likely to spike on
        contact". torS is excluded rather than rescaled because this board
        reports it as one shoulder servo's load MINUS the other's, zeroed
        once at boot, so it carries an unknown offset and a fraction of its
        cap is not a meaningful test on it. arm_hw.py excludes it from its
        own bump test for the same reason (BUMP_KEYS). Net: the cutout goes
        from checking four joints that could never fire to checking three
        that can.
        """
        fb = self.feedback or {}
        for joint, limit in TORQUE_ESTOP.items():
            v = abs(fb.get(joint, 0))
            if v > limit:
                return (joint, v, limit)
        return None

    def _pump(self):
        """Fixed 40 Hz. ALWAYS sends the LATEST target and DROPS stale ones --
        a FIFO under a faster producer accumulates visible lag.

        T:1041 (CMD_XYZT_DIRECT_CTRL) is NON-BLOCKING with NO interpolation.
        Waveshare's own doc: 'suitable for the case that a new target point is
        given continuously ... the difference in target point position between
        each command should not be too big.' That IS a scrub oscillation --
        but it means a large delta is a violent throw, so the per-tick step is
        rate-limited below.

        NEVER use T:104 or T:100 during the demo. Both are documented as
        BLOCKING, and the firmware's loop() is a single cooperative thread, so
        a blocking move stalls serial intake too. It looks like a hung robot.

        NEVER use T:123 (CONSTANT_CTRL) for the oscillation: velocity-style
        with no position target, so you cannot bound the sponge's travel over
        a human forearm. Safety, not preference.
        """
        # ABSOLUTE-DEADLINE pacing, not time.sleep(period). MEASURED on this
        # Mac: a bare `while: time.sleep(0.025)` loop runs at 34.8 Hz, not 40
        # -- macOS timer granularity plus loop-body time both add to the sleep.
        # That is a 13% shortfall on EVERY command, which slows the scrub
        # oscillation and silently drops the rate-limiter's real ceiling from
        # 240 mm/s to 209 mm/s. Deadline pacing measures 39.9 Hz.
        # (motion._pace already does this; the pump must too.)
        nxt = time.perf_counter()
        # RE-ASSERT THE SETTINGS BEFORE THE ESTOP GATE, not after it. A board
        # that browns out while the arm is estopped comes back un-capped and
        # answering ESP-NOW, and the pump below `continue`s on every estopped
        # tick -- so putting this after the gate would skip exactly the state
        # in which an un-capped arm matters most. It sends nothing that moves
        # the arm: T:605, T:301 and T:112 are all settings.
        settings_at = time.perf_counter()
        while self._run:
            nxt += 0.025
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.perf_counter()      # fell behind: resync, no spiral
            if not self.dry and time.perf_counter() - settings_at >= self.SETTINGS_EVERY_S:
                settings_at = time.perf_counter()
                self.apply_settings()
            if self.estopped:
                continue
            with self.lock:
                tx, ty, tz, tt = self.target
            if self.last_sent is None:
                # First command after startup only. We genuinely do not know
                # where the arm is, so ask it rather than teleporting: seed
                # from the last feedback if we have one, else accept the jump
                # (startup happens with nobody under the sponge).
                fb = self.feedback
                if fb and all(k in fb for k in ("x", "y", "z")):
                    self.last_sent = (fb["x"], fb["y"], fb["z"])
                    continue                        # rate-limit from next tick
                cx, cy, cz = tx, ty, tz
            else:
                lx, ly, lz = self.last_sent
                dx, dy, dz = tx - lx, ty - ly, tz - lz
                dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                if dist > MAX_STEP_MM:
                    k = MAX_STEP_MM / dist
                    cx, cy, cz = lx + dx * k, ly + dy * k, lz + dz * k
                else:
                    cx, cy, cz = tx, ty, tz
            self.last_sent = (cx, cy, cz)
            self._send({"T": 1041, "x": round(cx, 1), "y": round(cy, 1),
                        "z": round(cz, 1), "t": round(tt, 3)})

    # ------------------------------------------------------------ feedback --
    def poll_feedback(self):
        """T:105 -> {"T":1051,"x","y","z","b","s","e","t",
                     "torB","torS","torE","torH","v"}

        torB/torS/torE/torH are a FREE CONTACT SENSOR with zero extra
        hardware. They spike when the sponge presses the forearm -- both a
        safety cutout AND an honest 'is it actually touching' signal, so the
        cleanliness counter measures something instead of counting a timer.

        Poll at 10 Hz, NOT 40 -- the ~200-byte replies would eat the serial
        headroom the 40 Hz command stream needs (115200 baud sustains ~217
        msg/s of 53-byte commands). Feedback is SERIAL-ONLY; HTTP is
        control-only and hard-returns -1.
        """
        if self.dry:
            return {}
        if self.estopped and not self._allow_feedback_during_estop:
            return {}
        lock = self._feedback_lock
        if lock is None:
            return self._poll_locked()
        with lock:
            return self._poll_locked()

    def _poll_locked(self):
        try:
            self.ser.reset_input_buffer()
            self._send({"T": 105})
            time.sleep(0.05)
            line = self.ser.readline().decode("utf-8", "replace").strip()
            if not line.startswith("{"):
                return {}
            fb = json.loads(line)
            self.feedback = fb
            # SAME SCALE BUG AS THE CUTOUT, SAME FIX. This read `torS > 550
            # or torE > 450` against caps of 110 and 50, so it was False for
            # the whole run no matter how hard the sponge pressed -- measured
            # against fake_esp32 with the tool stalled 60mm inside a surface:
            # every joint pinned at its cap and this still said False.
            # py/contact.py replaces this with a pose-independent residual as
            # soon as its gravity model fits, but until then THIS is what
            # arm.contact holds, and "always False" is the wrong floor for a
            # signal the projector shows as TORQUE-CONFIRMED CONTACT.
            #
            # It reads the SAME joints as the cutout, so it inherits the
            # measured exclusion of torS and torE: on the joints where
            # gravity swamps the signal, a raw threshold cannot tell a press
            # from a long reach, and py/contact.py's residual is the answer.
            # That leaves this test deliberately CONSERVATIVE -- torB and
            # torH only spike on a real jam or a firm press -- which is the
            # right bias for a signal that pops splotches on a projector.
            #
            # Below the cutout, deliberately: contact must trip strictly
            # before the estop or the sponge can never touch anything without
            # also stopping the demo. openyam.py's _load_calibration enforces
            # exactly that ordering between CONTACT_TAU and ESTOP_TAU; this
            # is the RoArm's version of the same rule.
            self.contact = any(
                abs(fb.get(j, 0)) > CONTACT_NEAR_CAP * TORQUE_CAP_VALUES[_CAP_KEY[j]]
                for j in _CUTOUT_JOINTS)
            return fb
        except Exception:
            return {}                               # a dropped read isn't fatal

    # --------------------------------------------------------------- estop --
    def estop(self):
        """Emergency stop. MUST NOT report success it did not achieve.

        The old version set estopped=True, called _send (which swallows every
        failure) and printed "*** EMERGENCY STOP ***" unconditionally. With a
        wedged or unplugged link the operator got a confirmation while the
        command never left the machine -- and because estopped=True also stops
        the pump, nothing further was sent either, so the arm held its last
        target pressed into a forearm. The one mechanism that must never lie
        was the one that lied loudest.

        Sends repeatedly: a single write can fail transiently, and this is the
        command you least want to drop.
        """
        self._clear_token += 1        # invalidate any in-flight clear_estop
        ok = False
        for _ in range(3):
            if self._send({"T": 0}):                # CMD_EMERGENCY_STOP
                ok = True
                break
            time.sleep(0.02)

        self.estop_confirmed = ok
        if ok:
            with self._estop_lock:
                self._estop_gen += 1               # invalidates any in-flight clear
                self.estopped = True               # latch ONLY on success
            print("[arm] *** EMERGENCY STOP SENT *** press 'r' to clear")
            return True

        # THE WRITE FAILED. DO NOT LATCH.
        #
        # Latching here makes pressing the emergency stop STRICTLY WORSE than
        # not pressing it, which a verifier proved by execution: on a
        # transient link stall (a full OS write buffer -- the ESP32's single
        # cooperative loop() stalling, USB backpressure, a brownout under
        # servo load) the T:0 is lost, `estopped=True` silences the pump
        # forever, and when the link RECOVERS zero commands are ever sent.
        # Measured: 0 commands delivered after recovery with estop pressed,
        # versus 41 ending at HOME without it. RE-VERIFIED through a real
        # stalled write (tests/fake_roarm.py stall(), which raises
        # SerialTimeoutException from Arm.ser.write so the whole
        # _consec_write_fail path runs): 0 latched, 61 un-latched, ending at
        # z=235 against HOME z=235. The 61-vs-41 gap is pump timing on a
        # different machine; the load-bearing half -- zero versus nonzero, and
        # the arm reaching HOME -- holds exactly. Guarded by
        # test_arm_protocol.py so it cannot silently regress.
        #
        # The arm holds the contact pose
        # -- 5mm into the skin plane -- on a person's forearm, indefinitely.
        #
        # So: stay un-latched, retarget HOME, and let the pump execute a
        # rate-limited retreat the moment the link drains. A controlled lift
        # beats freezing at contact depth on a person. Then say so, loudly.
        self.estopped = False
        self.abort_requested = True     # stops the FSM without parking the pump
        self.last_sent = None if self.last_sent is None else self.last_sent
        self.set_target(*HOME)
        print("\n" + "!" * 64)
        print("!! ESTOP WRITE FAILED — THE ARM WAS **NOT** STOPPED.")
        print("!! Retreating to HOME under the rate limiter instead; the arm")
        print("!! will lift as soon as the link recovers.")
        print("!! IF IT DOES NOT LIFT, CUT POWER AT THE 12V SUPPLY.")
        print("!" * 64 + "\n")
        return False

    def clear_estop(self):
        """Resume after an emergency stop, SLOWLY.

        -> True if the arm was released, False if the clear was ABANDONED.

        This is the recovery path an operator presses under pressure, with a
        person's arm still under the sponge. It must be the gentlest motion in
        the program, not the most violent -- and it must never claim success
        it did not achieve. See DIRECTIVE section 3 for the five separate
        CRITICALs this method has produced.

        DO NOT set last_sent = None here: the pump's `last_sent is None`
        branch skips the rate limiter entirely, which measured 9,513 mm/s on
        the first tick. Instead the true position is READ below, because
        `last_sent` is only what we COMMANDED and the servos release during an
        estop.
        """
        # SAMPLE THE GENERATION BEFORE ANYTHING ELSE, including the T:999
        # send. Reading it afterwards leaves a gap in which an estop gets a
        # matching generation and is missed.
        #
        # HONESTY NOTE: I changed this after seeing 1 offset in 10 fail inside
        # a loaded test suite, and assumed this gap was the cause. A dedicated
        # 72-trial sweep against the PRE-change code found ZERO failures, so
        # the suite failure was CPU contention, not this. The ordering is
        # still correct and the gap is real in principle; it was just not what
        # I saw. Do not cite that suite failure as evidence for this line.
        gen = self._estop_gen

        # T:999 MUST ACTUALLY SEND. Discarding this return value meant a
        # dropped write reported a successful clear: the operator saw "estop
        # cleared", the hardware stayed latched, and every later command was
        # silently ignored by the firmware.
        # RE-ASSERT THE TORQUE CAPS FIRST. If this clear is recovering from a
        # brownout rather than a keypress, the ESP32 rebooted and its caps are
        # back to the firmware default of 1000. Sending them before T:999
        # means the arm is never un-capped while it is also un-stopped.
        self.apply_torque_caps()
        if not self._send({"T": 999}):              # CMD_RESET_EMERGENCY
            print("\n" + "!" * 64)
            print("!! CLEAR FAILED — the arm did NOT receive the release.")
            print("!! It is still stopped. Check power and USB, then retry.")
            print("!" * 64 + "\n")
            return False
        if self.dry:
            self.estopped = False
            self.abort_requested = False
            self.set_target(*HOME)
            print("[arm] estop cleared (dry run)")
            return True

        # MEASURE the true position before releasing the pump.
        #
        # last_sent is what we COMMANDED, not where the arm IS. During an
        # estop the servos are released, so gravity or a person nudging the
        # arm can move it -- and then the rate limiter interpolates from an
        # origin that is a lie, producing a real-world jump on the one button
        # an operator presses to recover.
        #
        # ORDERING IS LOAD-BEARING, and toggling `estopped` to allow the read
        # is NOT safe: the pump samples that flag every 25ms, so a brief
        # False releases it for a tick or two using the STALE target. Measured
        # -- the first two commands after clearing were the old contact pose
        # (300,120,40) rather than anything near the true position.
        #
        # Use a separate flag the pump does not read, so the feedback path can
        # run while the pump stays parked.
        # A NEW ESTOP DURING THE POLL WINS. poll_feedback() takes ~61ms
        # (reset_input_buffer + write + sleep(0.05) + readline). If the
        # operator hits the emergency stop in that window -- entirely likely,
        # since clearing and re-stopping is exactly what a nervous operator
        # does -- the code below would set estopped = False and RELEASE a stop
        # that was just requested. Take a token and refuse to clear if
        # anything estopped while we were reading.
        self._allow_feedback_during_estop = True
        try:
            fb = self.poll_feedback()
        finally:
            self._allow_feedback_during_estop = False
        if self._estop_gen != gen:
            # A new estop landed mid-clear. Our T:999 may already have raced
            # PAST their T:0 on the wire, so re-assert the stop rather than
            # assuming the firmware is latched.
            with self._estop_lock:
                self.estopped = True
            self._send({"T": 0})
            print("[arm] a NEW estop arrived while clearing — clear ABANDONED, "
                  "arm stays stopped. Press clear again.")
            return False
        if fb and all(k in fb for k in ("x", "y", "z")):
            actual = (fb["x"], fb["y"], fb["z"])
            if self.last_sent:
                drift = math.dist(self.last_sent, actual)
                if drift > 10.0:
                    print(f"[arm] the arm MOVED {drift:.0f}mm during the estop "
                          f"(gravity or a nudge) — re-seeding the limiter")
            self.last_sent = actual             # interpolate from the TRUTH
            with self.lock:
                # Park the target on the true position as well, so the pump's
                # very first tick cannot re-send the stale contact pose.
                self.target = (actual[0], actual[1], actual[2], self.target[3])
        elif self.last_sent is None:
            self.last_sent = HOME[:3]           # never leave it unset

        with self._estop_lock:
            if self._estop_gen != gen:             # last chance to notice
                self.estopped = True
                self._send({"T": 0})
                print("[arm] a NEW estop arrived during recovery — "
                      "clear ABANDONED. Press clear again.")
                return False
            self.estopped = False
            self.abort_requested = False
        self.set_target(*HOME)
        print("[arm] estop cleared — returning home under the rate limit")
        return True

    def go_home(self):
        self.set_target(*HOME)

    def close(self):
        self._run = False
        time.sleep(0.1)
        if self.ser:
            # Best-effort stop on the way out. Unlike estop() this does not
            # need to latch anything -- the process is ending -- but it should
            # still say so if the arm never heard it.
            if not self._send({"T": 0}):
                print("[arm] NOTE: the final stop command did not send. "
                      "Check the arm is not still holding a pose.")
            self.ser.close()


def find_port():
    """RoArm appears as /dev/cu.usbserial-* or /dev/cu.SLAB_USBtoUART."""
    for pat in ("/dev/cu.usbserial-*", "/dev/cu.SLAB_USBtoUART*",
                "/dev/cu.wchusb*"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    raise SystemExit(
        "\nNo serial port found.\n"
        "Install the SiLabs CP210x VCP driver, approve the system extension\n"
        "in System Settings > Privacy & Security, and REBOOT.\n"
        "  https://www.silabs.com/products/development-tools/software/"
        "usb-to-uart-bridge-vcp-drivers\n")


if __name__ == "__main__":
    # Hour 2 GO/NO-GO: the arm physically moves AND returns a JSON blob.
    print("ports:", glob.glob("/dev/cu.usb*") + glob.glob("/dev/cu.SLAB*"))
    a = Arm()
    print("feedback:", a.poll_feedback())
    print("homing..."); a.go_home(); time.sleep(2)
    print("nudging +40mm in X..."); x, y, z, t = HOME
    a.set_target(x + 40, y, z); time.sleep(2)
    print("feedback:", a.poll_feedback())
    a.go_home(); time.sleep(2); a.close()
    print("\nIf the arm moved and feedback printed a dict: GO.")
