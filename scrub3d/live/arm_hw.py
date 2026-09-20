"""scrub3d/live/arm_hw.py -- the real arms: find them, name them, and let
the live view drive them.

    python scrub3d/live/arm_hw.py              # which arms are plugged in, and
                                               # where each one is; nothing moves
    python scrub3d/live/arm_hw.py --assign     # say which arm is red, blue, green
    python scrub3d/live/arm_hw.py --selftest   # the driver against fake boards
    python scrub3d/live/live_body.py --drive   # the live view, driving the arms
                                               # named in arm_ports.json

HOW AN ARM CONNECTS (see ARMS.md for the sources). Each RoArm-M2-S has its
own ESP32 driver board. A USB-C data cable from the board's port marked USB
(the middle one; the edge one is for a LiDAR) to the laptop gives a serial
port, COMx on Windows, through a CP2102 (USB id 10C4:EA60, the SiLabs
CP210x driver). 115200 baud, one JSON object per line. The arm runs from
its own 12 V supply, and when powered it drives itself to its start pose.

The firmware (waveshareteam/roarm_m2, 0.84) solves the arm's inverse
kinematics itself. Its x, y, z are millimetres from the SHOULDER PIVOT
(x forward, y left, z up), not from the base: scrub3d's base-frame points
are shifted by FW_ORIGIN_MM on the way out and back. It always bends the
elbow up, as scrub3d's kinematics does.

WHAT THE DRIVER DOES, in order:
  1. opens each port with DTR and RTS held low, so the board's automatic
     download circuit cannot restart the ESP32;
  2. switches the echo and debug text off (T:605 cmd 0), switches the
     ESP-NOW follower mode off (T:301 mode 0: otherwise any nearby leader
     can move the arm), and caps each joint's torque so the arm gives way
     when it meets a person (T:112); all three again every 10 s, because a
     board that reboots comes back with them at the defaults;
  3. asks each arm where it is (T:105) until it answers (a board still
     starting up can take 20 s) and starts from there: nothing moves before
     every arm has answered, and the simulated arms are put where the real
     ones are;
  4. then, 40 times a second, sends the tool point the governor last
     approved for that arm (T:1041, non-blocking, with `t` always set:
     without it the gripper opens), never more than MAX_STEP_MM from the
     one sent before and never one the firmware cannot solve; and asks for
     feedback 20 times a second;
  5. when the live view stops or ends, when a link stops taking commands or
     answering, or when an arm is not where it was sent (TRACK_MM for
     TRACK_S: something holds it back), it HOLDS every arm where it is.

SPEED, ON THE BOARD ITSELF. Right after an arm answers, the driver sends a
T:122 to the joint angles the arm reported, which leaves it where it is
(the firmware turns those angles back into the same servo steps) and sets
the servo speed and acceleration that every later T:1041 uses: at most
SERVO_SPD_DEG degrees a second per joint, speeding up and slowing down at
SERVO_ACC (the firmware's start-up value is an eighth of it, too slow for
an arm to keep to its checked path, or to stop quickly). Whatever the
software asks, the servos move no faster. Every hold sends it again, as a board that restarted has
lost it. The live view keeps its own, lower, limits near the person
(arms_live, THE DEPTH GUARD); the driver matches the pace it is fed, so a
slow approach is not sent in bursts.

WHEN SOMETHING GOES WRONG, beyond 5.: an arm that runs into something
backs off the way it came, slowly, and the others hold; an arm
not fed a new point for WATCHDOG_S (the live view stalled) stops where it
is; a board that restarts (it prints its version, and has driven its arm to
the start pose by itself) holds every arm; an arm pressing on the skin but
held PRESS_BLOCK_MM short of where it was sent, at its torque cap, for
PRESS_BLOCK_S (the person is much nearer than the model) backs off, and the
others hold.

WHAT THE ARMS REPORT, AND WHAT IS DONE WITH IT. Each T:105 makes the
firmware read every servo there and then (RoArmM2_getPosByServoFeedback):
the T:1051 reply carries the joint angles from the servos' own encoders
(b, s, e, t; 4096 steps a turn, 0.09 degrees), the tool point the firmware
works out from those angles (x, y, z), and each servo's load (torB, torS,
torE, torH). A load is the servo's drive effort, -1000 to 1000 in 0.1% of
full, sign for direction, and never beyond its T:112 cap: it is not a
force or pressure sensor, and the firmware sends no current, voltage or
temperature. torS is one shoulder servo's live load minus the other's,
read once at boot, so it carries an unknown offset; torB and torE do not.
If a servo stops answering, the firmware repeats its last reading without
saying so. The driver:
  - checks at connect that the angles and the point agree under scrub3d's
    kinematics (MODEL_TOL_RAD): a board whose frame or joint signs are not
    the ones assumed is refused before it moves;
  - gives the live view each arm's measured joints, which it checks against
    the person and the other arms every frame, as it checks the simulated
    ones, and credits only the skin the real sponge went over
    (live_body.drive_arms);
  - holds every arm when an arm off the skin is stuck off its path
    (LOAD_OFF_MM, barely moving for LOAD_S) with its base or elbow load at
    its cap (LOAD_NEAR_CAP): it has met something;
  - holds every arm when an arm's readings stop changing while it is sent
    somewhere else (FROZEN_MM for FROZEN_S): a servo stopped answering;
  - keeps every sample (DriveLog), so a run can be checked afterwards
    against what was commanded: `arm_hw.py --report LOG`.

WHY HOLD AND NOT T:0. In this firmware T:0 is not a latched stop: it turns
every servo off for 10 s (the arm drops) and then back on, and the board
reads nothing meanwhile; T:999 does nothing. So the driver never sends it.
A held arm keeps its torque (capped) and its place. On the way out each arm
first draws its tool 50 mm back toward its own shoulder, away from the
person, then holds. For a real emergency, switch the 12 V supply off.

What only a real arm can confirm: the frame shift (the vendor's URDF puts
the shoulder 123 mm above the base, the firmware config says 126), the
torque caps being enough for the arm to hold itself up, and the firmware
version (this is written for 0.84, shown on the arm's screen at start). Run
it first with nobody in the chair (ARMS.md).
"""
import argparse
import collections
import json
import math
import os
import re
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(HERE)
for p in (WT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import kinematics as K                          # noqa: E402

BAUD = 115200
# USB-to-serial chips a RoArm-M2-S driver board may carry, by (vendor,
# product): two CP2102s on the boards documented. Anything else (a
# keyboard's serial port, say) is never touched.
BRIDGES = {(0x10C4, 0xEA60): "CP210x", (0x1A86, 0x7523): "CH340",
           (0x1A86, 0x55D3): "CH343", (0x1A86, 0x55D4): "CH9102"}
ESPRESSIF_VID = 0x303A                          # an ESP32's own USB
PORTS_FILE = os.path.join(HERE, "arm_ports.json")
NAMES = ("red", "blue", "green", "orange")

# The firmware's origin in scrub3d's base frame: the shoulder pivot.
FW_ORIGIN_MM = np.array([K.BASE_X_MM, 0.0, K.BASE_H_MM])
FW_ELBOW_MIN = -0.785       # the firmware clamps the elbow here (scrub3d allows -1)
GRIPPER_T = math.pi         # the EoAT angle kept on every move: closed, sponge held

RATE_HZ = 40.0
MAX_STEP_MM = 12.0          # per command: 480 mm/s, the fastest the live view asks
FEEDBACK_HZ = 20.0          # replies travel the other way; the link has room
SETTINGS_EVERY_S = 10.0
TORQUE_CAPS = {"T": 112, "mode": 1, "b": 60, "s": 110, "e": 50, "h": 50}
STALE_S = 1.0               # no feedback this long: the arm is not answering
FAILED_WRITES = 12          # this many in a row: the link is not taking commands
TRACK_MM = 60.0             # this far from anywhere it was sent lately ...
TRACK_S = 0.8               # ... for this long: something holds it back
TRACK_WINDOW_S = 0.5        # "lately": feedback is up to 1/FEEDBACK_HZ old
MODEL_TOL_RAD = 0.06        # reported angles vs the reported point, scrub3d's IK
LOAD_KEYS = ("torB", "torS", "torE", "torH")
LOAD_CAP_KEY = {"torB": "b", "torS": "s", "torE": "e", "torH": "h"}
BUMP_KEYS = ("torB", "torE")  # single-servo loads (torS carries a boot-time offset)
LOAD_NEAR_CAP = 0.9         # a load this close to its cap is the servo giving all it may
LOAD_S = 0.3                # ... while, for this long, the arm
LOAD_OFF_MM = 15.0          # ... is this far off its path, off the skin,
LOAD_STILL_MM = 8.0         # ... and has moved less than this: it met something
FROZEN_MM = 30.0            # sent this far while every reading stays the same ...
FROZEN_S = 0.5              # ... for this long: a servo is not answering
PRESS_BLOCK_MM = 25.0       # on the skin, held this far short of its point ...
PRESS_BLOCK_S = 0.5         # ... at its cap, this long: the person is nearer than modelled
SERVO_SPD_DEG = 90.0        # T:122 spd: degrees a second, each joint, at most
SERVO_ACC = 15.0            # T:122 acc: servo register 171, about 1500 degrees/s2
MIN_FEED_V = 40.0           # mm/s: the slowest the driver paces a point it is fed
PACE_OVER = 1.15            # ... and how much faster than it is fed, to keep up
WATCHDOG_S = 0.5            # no new point this long: the arm stops where it is
BACK_OFF_S = 4.0            # how long a backing-off arm may take
BOOT_TEXT = re.compile(r"version", re.I)
ANSWER_S = 20.0             # a board starting up answers within this
RETREAT_MM = 50.0           # on the way out, back toward the shoulder
MAC = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


class ArmError(RuntimeError):
    pass


def to_fw(p):
    """scrub3d base frame -> firmware frame (mm)."""
    return np.asarray(p, float) - FW_ORIGIN_MM


def from_fw(p):
    return np.asarray(p, float) + FW_ORIGIN_MM


def solvable(p):
    """Can the firmware put the tool at base-frame point `p`? T:1041 has no
    guard: a point it cannot solve becomes NaN angles and a whipped arm."""
    j = K.ik(*p)
    return j is not None and j[2] >= FW_ELBOW_MIN


# --- finding the arms -----------------------------------------------------------
def candidate_ports():
    """Serial ports whose USB chip a driver board uses. -> [dict]"""
    from serial.tools import list_ports
    out = []
    for p in list_ports.comports():
        chip = BRIDGES.get((p.vid, p.pid))
        if chip is None and p.vid == ESPRESSIF_VID:
            chip = "ESP32 USB"
        if chip:
            out.append({"port": p.device, "chip": chip, "serial": p.serial_number or "",
                        "description": p.description or ""})
    return sorted(out, key=lambda c: c["port"])


def open_port(device):
    """The port, opened without restarting the ESP32 behind it."""
    import serial
    ser = serial.Serial()
    ser.port, ser.baudrate = device, BAUD
    ser.timeout, ser.write_timeout = 0.3, 0.2
    # Held low BEFORE opening, so they never change on this port.
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


# --- one link -------------------------------------------------------------------
class Link:
    """One board: JSON lines out; the latest feedback, and any plain text,
    in (a reader thread)."""

    def __init__(self, ser, name):
        self.ser, self.name = ser, name
        self.wlock = threading.Lock()
        self.feedback, self.fb_time = None, None
        self.text = collections.deque(maxlen=50)
        self.failed = 0                 # writes failed in a row
        self.sent = 0
        self.boots = 0                  # start-up banners seen
        self.running = True
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    @property
    def port(self):
        return getattr(self.ser, "port", "?")

    def send(self, d):
        """-> True if the bytes went out. Never raises."""
        data = (json.dumps(d, separators=(",", ":")) + "\n").encode()
        try:
            with self.wlock:
                self.ser.write(data)
            self.failed = 0
            self.sent += 1
            return True
        except Exception:                               # noqa: BLE001
            self.failed += 1
            return False

    def _read(self):
        while self.running:
            try:
                line = self.ser.readline()
            except Exception:                           # noqa: BLE001
                time.sleep(0.05)
                continue
            if not line:
                continue
            text = line.decode("utf-8", "replace").strip()
            if not text.startswith("{"):
                if text:
                    self.text.append(text)              # firmware chatter, the MAC
                    if BOOT_TEXT.search(text):
                        self.boots += 1
                continue
            try:
                d = json.loads(text)
            except ValueError:
                continue
            # Only real feedback counts: with echo on, the board repeats our
            # own lines, T:105 included.
            if d.get("T") == 1051 and all(k in d for k in ("x", "y", "z")):
                self.feedback, self.fb_time = d, time.monotonic()

    def settings(self):
        """Echo off, ESP-NOW off, torque capped. -> all sent?"""
        return all([self.send({"T": 605, "cmd": 0}), self.send({"T": 301, "mode": 0}),
                    self.send(dict(TORQUE_CAPS))])

    def wait_feedback(self, timeout=ANSWER_S):
        """Ask where the arm is until it says. -> feedback or None"""
        end = time.monotonic() + timeout
        self.feedback = None
        while time.monotonic() < end:
            self.send({"T": 105})
            time.sleep(0.15)
            if self.feedback is not None:
                return self.feedback
        return None

    def mac(self, timeout=1.5):
        """The board's MAC address, or ''."""
        self.send({"T": 302})
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for line in list(self.text):
                m = MAC.search(line)
                if m:
                    return m.group(1).upper()
            time.sleep(0.05)
        return ""

    def close(self):
        self.running = False
        try:
            self.ser.close()
        except Exception:                               # noqa: BLE001
            pass


# --- one arm --------------------------------------------------------------------
class RealArm:
    """Follows one simulated arm with the real one (see the module notes).
    Points are scrub3d base-frame millimetres unless named _fw."""

    def __init__(self, link, name):
        self.link, self.name = link, name
        self.T = self.Tinv = None
        self.lock = threading.Lock()
        self.target = self.sent = None
        self.held = False
        self.running = False
        self.far_since = None
        self.load_since = None
        self.on_skin = False                   # the live view says: pressing, now
        self.fed_at = None                     # when the live view last gave a point
        self.fed = None                        # (time, point) it last gave
        self.v_fed = 0.0                       # how fast those points move, mm/s
        self.backing_until = 0.0               # backing off: the watchdog lets it
        self.boots_seen = 0
        self.press_since = None
        self.recent = collections.deque()      # (time, point sent), the last TRACK_WINDOW_S
        self.seen = collections.deque()        # (time, reading, point), the last second
        self.seen_fb = None
        self.frozen_since = self.frozen_key = None
        self.thread = None

    def connect(self, timeout=ANSWER_S):
        """Settings, then where the arm is. -> base-frame tool point"""
        self.link.settings()
        fb = self.link.wait_feedback(timeout)
        if fb is None:
            raise ArmError(f"{self.name} on {self.link.port} did not answer in "
                           f"{timeout:.0f} s: is it powered, and on the USB port?")
        here = self.actual()
        if not solvable(here):
            raise ArmError(f"{self.name} reports its tool at {np.round(here)} mm "
                           f"(scrub3d frame), which scrub3d cannot solve: check the "
                           f"firmware version and FW_ORIGIN_MM")
        err = self.model_error()
        if err is not None and err > MODEL_TOL_RAD:
            raise ArmError(f"{self.name}: the joint angles it reports are "
                           f"{math.degrees(err):.1f} degrees from where its reported "
                           f"tool point puts them under scrub3d's kinematics. Its frame "
                           f"or joint directions are not the ones assumed "
                           f"(FW_ORIGIN_MM, measured_joints): not driving it")
        if not self.cap_speed():
            raise ArmError(f"{self.name}: its speed could not be capped (T:122)")
        time.sleep(0.4)
        if self.link.wait_feedback(timeout=3.0) is None:
            raise ArmError(f"{self.name} stopped answering when its speed was capped")
        moved = self.actual()
        if float(np.linalg.norm(moved - here)) > 3.0:
            self.hold()
            raise ArmError(f"{self.name} moved {np.linalg.norm(moved - here):.0f} mm when "
                           f"its speed was capped: the firmware's angles are not the ones "
                           f"assumed. Not driving it")
        here = moved
        self.boots_seen = self.link.boots
        with self.lock:
            self.target = self.sent = tuple(here)
        return here

    def cap_speed(self):
        """T:122 to the angles the arm last reported, with the speed cap. -> sent?"""
        fb = self.link.feedback
        if fb is None or not all(k in fb for k in ("b", "s", "e", "t")):
            return False
        return self.link.send({"T": 122, **{k: round(math.degrees(float(fb[v])), 2)
                                            for k, v in (("b", "b"), ("s", "s"),
                                                         ("e", "e"), ("h", "t"))},
                               "spd": SERVO_SPD_DEG, "acc": SERVO_ACC})

    def place(self, T_world_base):
        self.T = np.asarray(T_world_base, float)
        self.Tinv = np.linalg.inv(self.T)

    def actual(self):
        """Where the arm says its tool point is, base frame. -> (3,) or None"""
        fb = self.link.feedback
        return None if fb is None else from_fw([fb["x"], fb["y"], fb["z"]])

    def actual_world(self):
        p = self.actual()
        return None if p is None or self.T is None else (self.T @ np.r_[p, 1.0])[:3]

    def measured_joints(self):
        """The encoders' angles in scrub3d's convention (the firmware's base
        and shoulder turn the other way). -> (j0, j1, j2) or None"""
        fb = self.link.feedback
        if fb is None or not all(k in fb for k in ("b", "s", "e")):
            return None
        return (-float(fb["b"]), -float(fb["s"]), float(fb["e"]))

    def loads(self):
        fb = self.link.feedback or {}
        return {k: float(fb[k]) for k in LOAD_KEYS if k in fb}

    def model_error(self):
        """How far the reported angles are from scrub3d's IK of the reported
        point, rad (None if the board sends no angles)."""
        j, p = self.measured_joints(), self.actual()
        if j is None or p is None:
            return None
        k = K.ik(*p)
        if k is None:
            return float("inf")
        return float(np.max(np.abs(np.subtract(j, k))))

    def _note(self, now):
        """Keep the last second of readings (new replies only)."""
        fb = self.link.feedback
        if fb is None or fb is self.seen_fb:
            return
        self.seen_fb = fb
        key = tuple(fb.get(k) for k in ("x", "y", "z", "b", "s", "e"))
        self.seen.append((now, key, self.actual()))
        while self.seen and now - self.seen[0][0] > 1.0:
            self.seen.popleft()

    def moved(self, span, now=None):
        """How far the reported tool point went in the last `span` s, mm
        (None if the readings do not reach that far back)."""
        now = time.monotonic() if now is None else now
        old = [p for t, _, p in self.seen if now - t >= span]
        if not old or not self.seen or old[-1] is None or self.seen[-1][2] is None:
            return None
        return float(np.linalg.norm(self.seen[-1][2] - old[-1]))

    def frozen(self, far, now=None):
        """Every reply the same since the arm got FROZEN_MM off its path,
        FROZEN_S ago or more. -> bool"""
        now = time.monotonic() if now is None else now
        key = self.seen[-1][1] if self.seen else None
        if far < FROZEN_MM or key is None:
            self.frozen_since = None
            return False
        if self.frozen_since is None or key != self.frozen_key:
            self.frozen_since, self.frozen_key = now, key
            return False
        replies = sum(1 for t, _, _ in self.seen if t >= self.frozen_since)
        return now - self.frozen_since >= FROZEN_S and replies >= 4

    def off_path(self, now=None):
        """How far the arm is from anywhere it was sent lately, mm."""
        now = time.monotonic() if now is None else now
        a = self.actual()
        with self.lock:
            sent = ([p for t, p in self.recent if now - t <= TRACK_WINDOW_S]
                    or ([self.sent] if self.sent else []))
        return (min(float(np.linalg.norm(a - np.asarray(p))) for p in sent)
                if a is not None and sent else 0.0)

    def follow(self, world_point):
        """The point the governor approved, world frame."""
        if self.Tinv is None or self.held:
            return
        p = (self.Tinv @ np.r_[np.asarray(world_point, float), 1.0])[:3]
        now = time.monotonic()
        with self.lock:
            if self.fed is not None and now - self.fed[0] > 1e-3:
                v = float(np.linalg.norm(p - self.fed[1])) / (now - self.fed[0])
                self.v_fed = 0.5 * self.v_fed + 0.5 * v
            self.fed = (now, p)
            self.fed_at = now
            self.target = tuple(float(v) for v in p)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _move(self, p):
        q = to_fw(p)
        return self.link.send({"T": 1041, "x": round(float(q[0]), 1),
                               "y": round(float(q[1]), 1), "z": round(float(q[2]), 1),
                               "t": GRIPPER_T})

    def _pump(self):
        period = 1.0 / RATE_HZ
        nxt = time.perf_counter()
        last_fb = last_set = time.monotonic()
        while self.running:
            nxt += period
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.perf_counter()               # fell behind: no spiral
            now = time.monotonic()
            if now - last_fb >= 1.0 / FEEDBACK_HZ:
                last_fb = now
                self.link.send({"T": 105})
            if now - last_set >= SETTINGS_EVERY_S:
                last_set = now
                self.link.settings()
            with self.lock:
                tgt, sent = self.target, self.sent
                fed_at, v_fed = self.fed_at, self.v_fed
            if tgt is None or sent is None:
                continue
            if (fed_at is not None and now - fed_at > WATCHDOG_S
                    and now > self.backing_until and not self.held):
                continue                                # the live view stalled: stop
            d = math.dist(tgt, sent)
            if d < 0.2:
                continue
            pace = min(MAX_STEP_MM, max(v_fed * PACE_OVER, MIN_FEED_V) * period)
            k = min(1.0, pace / d)
            step = tuple(s + (g - s) * k for s, g in zip(sent, tgt))
            if not solvable(step):
                continue                                # hold rather than guess
            if self._move(step):
                with self.lock:
                    self.sent = step
                    self.recent.append((now, step))
                    while self.recent and now - self.recent[0][0] > TRACK_WINDOW_S:
                        self.recent.popleft()

    def fault(self, now=None):
        """-> why the arms must stop, or None."""
        now = time.monotonic() if now is None else now
        if self.link.failed >= FAILED_WRITES:
            return f"{self.name}: the link stopped taking commands"
        if self.link.fb_time is None or now - self.link.fb_time > STALE_S:
            return f"{self.name}: the arm stopped answering"
        self._note(now)
        if self.held:
            return None
        if self.link.boots > self.boots_seen:
            self.boots_seen = self.link.boots
            return (f"{self.name}: its board restarted (and drove the arm to its "
                    f"start pose by itself)")
        far = self.off_path(now)
        loads = self.loads()
        pinned = [k for k in BUMP_KEYS if abs(loads.get(k, 0.0))
                  >= LOAD_NEAR_CAP * float(TORQUE_CAPS[LOAD_CAP_KEY[k]])]
        if pinned and far > LOAD_OFF_MM and not self.on_skin:
            if self.load_since is None:
                self.load_since = now
            went = self.moved(LOAD_S, now)
            if (now - self.load_since >= LOAD_S and went is not None
                    and went < LOAD_STILL_MM):
                return (f"{self.name}: stuck {far:.0f} mm off its path, off the skin, "
                        f"with {' and '.join(pinned)} at the torque cap (it met something)")
        else:
            self.load_since = None
        # An arm pushing at its cap is not frozen: it is held (below).
        if not pinned and self.frozen(far, now):
            return (f"{self.name}: its readings stopped changing while it was sent "
                    f"elsewhere (a servo not answering, or the arm jammed)")
        if pinned and self.on_skin and far > PRESS_BLOCK_MM:
            if self.press_since is None:
                self.press_since = now
            elif now - self.press_since > PRESS_BLOCK_S:
                return (f"{self.name}: pressing on the skin but held {far:.0f} mm short "
                        f"of its point (the person is nearer than the model; it met them)")
        else:
            self.press_since = None
        # Pressing and pinned, the rule above says what to do.
        if far > TRACK_MM and not (self.on_skin and pinned):
            if self.far_since is None:
                self.far_since = now
            elif now - self.far_since > TRACK_S:
                return (f"{self.name}: the arm is {far:.0f} mm from where it was "
                        f"sent (something holds it back)")
        else:
            self.far_since = None
        return None

    def hold(self):
        """Stay where it is: no more moves; the last command is where the arm
        now is. -> did that go out?"""
        self.held = True
        here = self.actual()
        with self.lock:
            if here is not None and solvable(here):
                self.target = self.sent = tuple(here)
            else:
                self.target = self.sent
            p = self.sent
        if p is None:
            return self.link.failed == 0
        for _ in range(3):
            if self._move(p):
                self.cap_speed()                  # a board that restarted lost it
                return True
            time.sleep(0.02)
        return False

    def retreat(self, mm=RETREAT_MM, wait=True, away_from=None):
        """Draw the tool `mm` back, slowly: away from `away_from` (where it was
        being sent when it met something), else toward its own shoulder.
        -> where to, or None"""
        here = self.actual()
        if here is None or self.held or self.link.failed:
            return None
        back = FW_ORIGIN_MM - here
        if away_from is not None and float(np.linalg.norm(here - np.asarray(away_from))) > 1.0:
            back = here - np.asarray(away_from, float)
        n = float(np.linalg.norm(back))
        if n < 1.0:
            return None
        goal = here + back * min(1.0, mm / n)
        if not solvable(goal):
            return None
        # From where the arm is, not from where it was last sent: an arm held
        # against something was sent beyond it, and stops pushing first.
        if solvable(here):
            self._move(here)
        with self.lock:
            if solvable(here):
                self.sent = tuple(here)
            self.target = tuple(goal)
            self.v_fed = MIN_FEED_V * 2.0
            self.backing_until = time.monotonic() + BACK_OFF_S
        if wait:
            self.wait_near(goal)
        return tuple(goal)

    def wait_near(self, goal, tol=5.0, timeout=BACK_OFF_S):
        """Until the arm reports itself within `tol` of `goal`. -> got there?"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            here = self.actual()
            if here is not None and float(np.linalg.norm(here - np.asarray(goal))) < tol:
                return True
            if self.link.failed >= FAILED_WRITES:
                return False
            time.sleep(0.05)
        return False

    def close(self):
        self.retreat()
        ok = self.hold()
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=0.5)
        self.link.close()
        return ok


# --- the fleet ------------------------------------------------------------------
class Hardware:
    """The real arms the live view drives, by rig arm index."""

    def __init__(self, arms):
        self.arms = arms                        # {rig index: RealArm}
        self.reason = None
        self.drive_log = None                   # a DriveLog, when the live view keeps one

    @classmethod
    def connect(cls, ports, log=print):
        """{rig index: port} -> Hardware, every arm answering, or ArmError
        with every port closed again."""
        arms = {}
        try:
            for a, port in sorted(ports.items()):
                name = NAMES[a % 4]
                try:
                    ser = open_port(port)
                except Exception as exc:                # noqa: BLE001
                    raise ArmError(f"{name}: could not open {port} ({exc}). Is the arm "
                                   f"plugged in, and no other program using the port?")
                arm = RealArm(Link(ser, name), name)
                arms[a] = arm
                here = to_fw(arm.connect())
                log(f"  {name} arm on {port}: tool at ({here[0]:.0f}, {here[1]:.0f}, "
                    f"{here[2]:.0f}) mm from its shoulder; torque capped, ESP-NOW off")
        except Exception:
            for arm in arms.values():
                arm.hold()
                arm.link.close()
            raise
        return cls(arms)

    @classmethod
    def fake(cls, n, starts=None, log=print):
        """The same, on FakeESP32 boards (firmware-frame start poses)."""
        from fake_esp32 import FakeESP32, HOME_FW
        arms = {}
        for a in range(n):
            board = FakeESP32(start=starts[a] if starts else HOME_FW, name=NAMES[a % 4],
                              mac=f"AA:BB:CC:00:00:{a + 1:02X}")
            arm = RealArm(Link(board, NAMES[a % 4]), NAMES[a % 4])
            arm.connect(timeout=3.0)
            arms[a] = arm
        log(f"  {n} simulated arm boards connected")
        return cls(arms)

    def place(self, layout):
        """The rig is known: where each arm's base is."""
        for a, arm in self.arms.items():
            arm.place(layout[a])

    def measured(self):
        """{rig index: (joints, world tool point)} from where the arms are."""
        out = {}
        for a, arm in self.arms.items():
            p = arm.actual()
            j = None if p is None else K.ik(*p)
            if j is None:
                raise ArmError(f"{arm.name} reports a tool point scrub3d cannot solve")
            out[a] = (j, arm.actual_world())
        return out

    def start(self):
        t0 = time.monotonic()
        for arm in self.arms.values():
            arm.start()
        # Fresh readings from every arm before anyone looks at them.
        end = t0 + 3.0
        while time.monotonic() < end and any(
                arm.link.fb_time is None or arm.link.fb_time < t0
                for arm in self.arms.values()):
            time.sleep(0.02)

    def joints(self):
        """{rig index: the encoders' joint angles (or, from a board that sends
        none, scrub3d's IK of its reported point), or None}"""
        out = {}
        for a, arm in self.arms.items():
            j = arm.measured_joints()
            if j is None and arm.actual() is not None:
                j = K.ik(*arm.actual())
            out[a] = j
        return out

    def update(self, points, stop=False, on_skin=None):
        """Each arm follows its approved point; `on_skin` says which the live
        view has pressing now. -> why every arm now holds, or None."""
        if self.reason is not None:
            return self.reason
        if stop:
            self.hold_all("the live view stopped the arms")
            return self.reason
        for a, arm in self.arms.items():
            arm.on_skin = bool((on_skin or {}).get(a, False))
            if points.get(a) is not None:
                arm.follow(points[a])
        for arm in self.arms.values():
            why = arm.fault()
            if why:
                if "met something" in why or "met them" in why:
                    # That arm backs off what it met, the way it came; the
                    # others hold.
                    self.hold_all(why, keep=arm)
                    with arm.lock:
                        pushed_to = arm.sent
                    arm.retreat(wait=False, away_from=pushed_to)
                else:
                    self.hold_all(why)
                break
        return self.reason

    def hold_all(self, why, keep=None):
        if self.reason is None:
            self.reason = why
        for arm in self.arms.values():
            if arm is not keep:
                arm.hold()

    def close(self):
        """Every arm draws back and holds; ports closed. -> arms whose hold
        did not go out."""
        if self.reason is None:
            goals = {}
            for arm in self.arms.values():
                goal = arm.retreat(wait=False)
                if goal is not None:
                    goals[arm] = goal
            end = time.monotonic() + BACK_OFF_S
            for arm, goal in goals.items():
                arm.wait_near(goal, timeout=max(0.0, end - time.monotonic()))
        missed = []
        for arm in self.arms.values():
            arm.held = arm.held or self.reason is not None
            if not arm.hold():
                missed.append(arm.name)
            arm.running = False
        for arm in self.arms.values():
            if arm.thread is not None:
                arm.thread.join(timeout=0.5)
            arm.link.close()
        if self.drive_log is not None:
            self.drive_log.close()
        return missed


# --- what the arms really did -----------------------------------------------------
LOG_DIR = os.path.join(HERE, "drive_logs")


class DriveLog:
    """One JSON line per arm per live-view frame: what the governor approved,
    what was sent, and what the arm reported (point, joints, loads)."""

    def __init__(self, path=None):
        if path is None:
            os.makedirs(LOG_DIR, exist_ok=True)
            path = os.path.join(LOG_DIR, time.strftime("drive_%Y%m%d_%H%M%S.jsonl"))
        self.path = path
        self.f = open(path, "w", encoding="utf-8")
        self.t0 = time.monotonic()

    def write(self, hw, approved, modes, on_skin, real_body_mm=None):
        now = time.monotonic()

        def r(v, n=1):
            return None if v is None else [round(float(x), n) for x in v]

        for a, arm in hw.arms.items():
            fb = arm.link.feedback or {}
            with arm.lock:
                sent = arm.sent
            self.f.write(json.dumps({
                "t": round(now - self.t0, 3), "arm": a, "name": arm.name,
                "mode": modes.get(a), "on_skin": bool(on_skin.get(a)),
                "approved_world": r(approved.get(a)),
                "sent_base": r(sent), "actual_base": r(arm.actual()),
                "actual_world": r(arm.actual_world()),
                "joints": r(arm.measured_joints(), 4),
                "loads": {k: fb[k] for k in LOAD_KEYS if k in fb},
                "fb_age": None if arm.link.fb_time is None
                else round(now - arm.link.fb_time, 3),
                "real_body_mm": None if real_body_mm is None
                else (None if real_body_mm.get(a) is None
                      else round(real_body_mm[a], 1)),
                "held": arm.held}) + "\n")

    def note(self, kind, **values):
        """A line that is not a sample: what the live view counted."""
        self.f.write(json.dumps({"kind": kind, "t": round(time.monotonic() - self.t0, 3),
                                 **values}) + "\n")

    def close(self):
        if not self.f.closed:
            self.f.close()


def report(path):
    """What a --drive run's arms really did, from its log. -> text"""
    entries = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    rows = [r for r in entries if "kind" not in r]
    notes = [r for r in entries if r.get("kind") == "coverage"]
    looks = [r for r in entries if r.get("kind") == "sight"]
    out = [f"{path}: {len(rows)} samples over "
           f"{(rows[-1]['t'] - rows[0]['t']) if rows else 0:.0f} s"]
    last = {}
    for r in notes:
        last[r["pass"]] = r
    for p in sorted(last):
        r = last[p]
        out.append(f"pass {p}: the real sponges reached {100 * r['real']:.0f}% of the "
                   f"skin in reach; the plan counted {100 * r['plan']:.0f}%"
                   + ("" if r.get("done") else " (the run ended during this pass)"))
    for a in sorted({r["arm"] for r in rows}):
        rs = [r for r in rows if r["arm"] == a]
        name = rs[0]["name"]
        t = np.array([r["t"] for r in rs])
        act = [r["actual_world"] for r in rs]
        app = [r["approved_world"] for r in rs]
        ok = [i for i in range(len(rs)) if act[i] is not None and app[i] is not None]
        lines = [f"{name}:"]
        if ok:
            A = np.array([act[i] for i in ok])
            P = np.array([app[i] for i in ok])
            T = t[ok]
            err = np.linalg.norm(A - P, axis=1)
            # the lag that best lines the real path up with the approved one
            best = (0.0, float(np.median(err)))
            for lag in np.arange(0.02, 0.6, 0.02):
                idx = np.searchsorted(T, T - lag)
                keep = idx < len(T)
                e = np.linalg.norm(A[keep] - P[idx[keep]], axis=1)
                if len(e) and float(np.median(e)) < best[1]:
                    best = (float(lag), float(np.median(e)))
            lines.append(f"  real tool point vs approved: median {np.median(err):.0f} mm, "
                         f"95% under {np.percentile(err, 95):.0f} mm, worst "
                         f"{err.max():.0f} mm; best matched {best[0] * 1000:.0f} ms late "
                         f"(then median {best[1]:.0f} mm)")
            path_len = float(np.linalg.norm(np.diff(A, axis=0), axis=1).sum())
            lines.append(f"  real tool travelled {path_len / 1000:.1f} m")
        js = [(np.array(r["joints"]), np.array(r["actual_base"])) for r in rs
              if r["joints"] and r["actual_base"]]
        if js:
            model = []
            for j, p in js:
                k = K.ik(*p)
                if k is not None:
                    model.append(float(np.max(np.abs(j - np.array(k)))))
            if model:
                lines.append(f"  encoder angles vs scrub3d's model of the reported point: "
                             f"median {math.degrees(np.median(model)):.2f} deg, worst "
                             f"{math.degrees(max(model)):.2f} deg")
        body = [r["real_body_mm"] for r in rs if r.get("real_body_mm") is not None]
        if body:
            lines.append(f"  real structure's clearance from the person model: closest "
                         f"{min(body):.0f} mm, 5% of the time under "
                         f"{np.percentile(body, 5):.0f} mm")
        for key in ("torB", "torS", "torE"):
            cap = LOAD_NEAR_CAP * float(TORQUE_CAPS[LOAD_CAP_KEY[key]])
            skin = np.array([abs(r["loads"][key]) for r in rs
                             if r["on_skin"] and key in r["loads"]])
            air = np.array([abs(r["loads"][key]) for r in rs if not r["on_skin"]
                            and r["mode"] in ("working", "returning")
                            and key in r["loads"]])
            if not len(skin) and not len(air):
                continue
            part = []
            for label, v in (("moving in the air", air), ("on the skin", skin)):
                if len(v):
                    part.append(f"{label} median {np.median(v):.0f}, at the cap "
                                f"{100 * float(np.mean(v >= cap)):.0f}% of the time")
            lines.append(f"  {key} (cap {TORQUE_CAPS[LOAD_CAP_KEY[key]]}): "
                         + "; ".join(part))
        age = [r["fb_age"] for r in rs if r["fb_age"] is not None]
        if age:
            lines.append(f"  feedback age: median {1000 * np.median(age):.0f} ms, worst "
                         f"{1000 * max(age):.0f} ms")
        mine = [r for r in looks if r["arm"] == a]
        if mine:
            seen = collections.Counter(r["verdict"] for r in mine)
            off = [r["shift"] for r in mine if r["verdict"] == "off"]
            lines.append("  camera check: " + ", ".join(
                f"{k} {v} times" for k, v in seen.most_common())
                + (f"; when off, it matched best {np.round(np.median(off, axis=0))} mm "
                   f"(x toward the camera, y the person's left, z up)" if off else ""))
        if any(r["held"] for r in rs):
            first = next(r["t"] for r in rs if r["held"])
            lines.append(f"  held from {first:.1f} s")
        out += lines
    return "\n".join(out)


# --- which arm is which --------------------------------------------------------------
def identify(ports, timeout=ANSWER_S):
    """Ask each board who it is and where its arm is; nothing moves.
    -> {port: {"mac", "feedback"} or {"error"}}"""
    out = {}
    for c in ports:
        try:
            link = Link(open_port(c["port"]), c["port"])
        except Exception as exc:                        # noqa: BLE001
            out[c["port"]] = {"error": f"could not open it: {exc}"}
            continue
        try:
            link.settings()
            fb = link.wait_feedback(timeout)
            out[c["port"]] = ({"error": "no answer (is the arm powered?)"} if fb is None
                              else {"feedback": fb, "mac": link.mac()})
        finally:
            link.close()
    return out


def load_ports(path=PORTS_FILE):
    """arm_ports.json -> {rig arm index: port}. Each arm is kept with its
    board's MAC address and USB serial number, so a board that comes back
    on another COM number is still found."""
    if not os.path.exists(path):
        raise ArmError(f"no {os.path.basename(path)} yet: run "
                       f"python scrub3d/live/arm_hw.py --assign")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    ports = candidate_ports()
    by_mac = {}
    if any(e.get("mac") for e in doc.get("arms", {}).values()):
        for port, info in identify(ports, timeout=6.0).items():
            if info.get("mac"):
                by_mac[info["mac"]] = port
    serials = [c["serial"] for c in ports if c["serial"]]
    by_serial = {c["serial"]: c["port"] for c in ports
                 if c["serial"] and serials.count(c["serial"]) == 1}
    out = {}
    for key, e in doc.get("arms", {}).items():
        out[int(key)] = (by_mac.get(e.get("mac") or "")
                         or by_serial.get(e.get("serial") or "") or e.get("port"))
    return out


def twitch(port):
    """Open the gripper a little and close it again (T:106), so a person
    sees which arm is on this port. Nothing else moves."""
    link = Link(open_port(port), port)
    try:
        link.settings()
        for ang in (2.6, GRIPPER_T, 2.6, GRIPPER_T):
            link.send({"T": 106, "cmd": ang, "spd": 0, "acc": 0})
            time.sleep(0.6)
    finally:
        link.close()


def assign(ports, rig_arms, path=PORTS_FILE):
    who = identify(ports)
    doc = {"arms": {}}
    left = list(range(rig_arms))
    for c in ports:
        info = who.get(c["port"], {})
        if "error" in info:
            print(f"\n{c['port']}: {info['error']}; skipped")
            continue
        if not left:
            break
        print(f"\n{c['port']} ({c['chip']}, MAC {info.get('mac') or '?'}): its gripper "
              f"will open a little and close, twice.")
        input("  Press Enter, then watch the arms...")
        twitch(c["port"])
        choices = ", ".join(f"{i} = {NAMES[i % 4]}" for i in left)
        ans = input(f"  Which arm was it? ({choices}; Enter to skip) ").strip()
        if ans.isdigit() and int(ans) in left:
            doc["arms"][ans] = {"name": NAMES[int(ans) % 4], "port": c["port"],
                                "mac": info.get("mac", ""), "serial": c["serial"]}
            left.remove(int(ans))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"\nsaved {path}: " + ", ".join(f"{e['name']} on {e['port']}"
                                        for e in doc["arms"].values()))
    if left:
        print("not assigned: " + ", ".join(NAMES[i % 4] for i in left))


# --- the self-test --------------------------------------------------------------------
def selftest():
    """Fake boards through the whole driver."""
    import fake_esp32 as FE
    import place_arms as PA
    starts = [FE.HOME_FW, (250.0, 60.0, 150.0, math.pi), (200.0, -80.0, 100.0, math.pi)]
    hw = Hardware.fake(3, starts=starts, log=lambda s: None)
    boards = {a: arm.link.ser for a, arm in hw.arms.items()}
    for b in boards.values():
        assert not b.echo, "echo left on"
        assert b.espnow_mode == 0, "ESP-NOW follower mode left on"
        assert b.torque_caps["s"] == TORQUE_CAPS["s"], "torque not capped"
    assert np.allclose(to_fw(hw.arms[0].actual()), FE.HOME_FW[:3], atol=0.05), \
        "the firmware frame was not undone"
    for b in boards.values():
        caps = b.of_type(122)
        assert caps and caps[-1]["spd"] == SERVO_SPD_DEG and caps[-1]["acc"] == SERVO_ACC
        assert abs(b.servo.speed - math.radians(SERVO_SPD_DEG)) < 0.01, b.servo.speed
        assert b.acc_reg == round(SERVO_ACC * 4096 / 360), b.acc_reg
    print(f"  each board's servos capped at {SERVO_SPD_DEG:.0f} degrees a second "
          f"(T:122 to where the arm already was: it did not move)")
    layout = [PA.pose(500, 0, 600, math.pi), PA.pose(200, 500, 600, -math.pi / 2),
              PA.pose(200, -500, 600, math.pi / 2)]
    hw.place(layout)
    got = hw.measured()
    assert not any(b.of_type(1041) for b in boards.values()), "moved before starting"
    hw.start()
    goals = {a: w + np.array([0.0, 0.0, 60.0]) for a, (j, w) in got.items()}
    t0 = time.monotonic()
    while time.monotonic() - t0 < 2.0:
        k = min(1.0, (time.monotonic() - t0) / 0.5)     # fed as the live view feeds
        assert hw.update({a: got[a][1] + (goals[a] - got[a][1]) * k
                          for a in goals}) is None, hw.reason
        time.sleep(0.05)
    for a, arm in hw.arms.items():
        cmds = boards[a].of_type(1041)
        assert cmds and all(c.get("t") == GRIPPER_T for c in cmds), "a move without t"
        pts = [np.array([c["x"], c["y"], c["z"]]) for c in cmds]
        steps = [float(np.linalg.norm(q - p)) for p, q in zip(pts, pts[1:])]
        assert max(steps, default=0.0) <= MAX_STEP_MM + 0.2, max(steps)
        here = (arm.T @ np.r_[from_fw(boards[a].position()), 1.0])[:3]
        assert np.linalg.norm(here - goals[a]) < 2.0, (arm.name, here, goals[a])
    print(f"  started from where each arm was, followed 60 mm in steps of at most "
          f"{MAX_STEP_MM:.0f} mm, in the firmware's shoulder frame")
    # a board that reboots comes back with its defaults; the driver re-sends them
    b = boards[1]
    b.echo, b.espnow_mode, b.torque_caps = True, 1, {k: 1000 for k in b.torque_caps}
    global SETTINGS_EVERY_S
    old, SETTINGS_EVERY_S = SETTINGS_EVERY_S, 0.3
    time.sleep(0.8)
    SETTINGS_EVERY_S = old
    assert not b.echo and b.espnow_mode == 0 and b.torque_caps["s"] == TORQUE_CAPS["s"]
    print("  a rebooted board gets its settings back within the re-send period")
    # a board that stops answering: every arm holds, and none is sent T:0
    b.muted = True
    t0 = time.monotonic()
    why = None
    while why is None and time.monotonic() - t0 < 3.0:
        why = hw.update(goals)
        time.sleep(0.05)
    assert why and "answering" in why, why
    n = {a: len(boards[a].of_type(1041)) for a in boards}
    hw.update({a: g + 50.0 for a, g in goals.items()})
    time.sleep(0.3)
    assert all(len(boards[a].of_type(1041)) - n[a] <= 0 for a in boards), \
        "a held arm was still moved"
    assert not any(bd.of_type(0) for bd in boards.values()), "T:0 was sent"
    print(f"  a silent board makes every arm hold: {why!r}")
    assert not hw.close()
    # held back: an arm that cannot get where it is sent
    hw = Hardware.fake(1, log=lambda s: None)
    hw.place([PA.pose(0, 0, 0, 0.0)])
    (j, w), = hw.measured().values()
    hw.start()
    old, FE.SPEED_SCALE = FE.SPEED_SCALE, 0.004
    try:
        t0 = time.monotonic()
        why = None
        while why is None and time.monotonic() - t0 < 5.0:
            why = hw.update({0: w + np.array([0.0, 0.0, -120.0])})
            time.sleep(0.05)
    finally:
        FE.SPEED_SCALE = old
    assert why and "holds it back" in why, why
    print(f"  an arm held back makes every arm hold: {why!r}")
    hw.close()
    # on the way out: draw back toward the shoulder, then hold
    hw = Hardware.fake(1, starts=[(300.0, 0.0, 100.0, math.pi)], log=lambda s: None)
    hw.place([PA.pose(0, 0, 0, 0.0)])
    hw.measured()
    hw.start()
    board = hw.arms[0].link.ser
    before = np.array(board.position())
    assert not hw.close()
    after = np.array(board.position())
    moved = float(np.linalg.norm(after - before))
    assert RETREAT_MM - 5 < moved < RETREAT_MM + 5 and np.linalg.norm(after) < \
        np.linalg.norm(before), (before, after)
    assert not board.of_type(0)
    print(f"  on the way out an arm draws {moved:.0f} mm back toward its shoulder "
          f"and holds")
    # an unsolvable target is never sent
    hw = Hardware.fake(1, log=lambda s: None)
    hw.place([PA.pose(0, 0, 0, 0.0)])
    hw.measured()
    hw.start()
    n0 = len(hw.arms[0].link.ser.of_type(1041))
    hw.update({0: np.array([2000.0, 0.0, 0.0])})
    time.sleep(0.4)
    sent = hw.arms[0].link.ser.of_type(1041)[n0:]
    assert all(solvable(from_fw([c["x"], c["y"], c["z"]])) for c in sent)
    hw.close()
    print("  a point the firmware cannot solve is never sent")
    # a board whose angles disagree with its point is refused before it moves
    board = FE.FakeESP32(name="odd")
    board.frame_error_mm = (0.0, 0.0, 40.0)
    arm = RealArm(Link(board, "odd"), "odd")
    try:
        arm.connect(timeout=3.0)
        raise AssertionError("a board with the wrong frame was accepted")
    except ArmError as exc:
        assert "not the ones assumed" in str(exc), exc
    assert not board.of_type(1041)
    arm.link.close()
    print("  a board whose encoder angles disagree with its point is refused")
    # something in the way while the arm flies: every arm holds, on the load
    hw = Hardware.fake(2, log=lambda s: None)
    hw.place([PA.pose(0, 0, 0, 0.0), PA.pose(0, 800, 0, 0.0)])
    got = hw.measured()
    hw.start()
    board = hw.arms[0].link.ser
    ceiling = board.position()[2] + 40.0                  # 40 mm over red's tool
    board.surface = lambda q: q[2] - ceiling
    # red flies up into it
    w0 = got[0][1]
    goal = {0: w0 + np.array([0.0, 0.0, 150.0]), 1: got[1][1]}
    t0 = time.monotonic()
    why = None
    while why is None and time.monotonic() - t0 < 5.0:
        why = hw.update(goal, on_skin={0: False, 1: False})
        time.sleep(0.05)
    assert why and "torque cap" in why, why
    stuck_at = np.array(board.position())
    n_blue = len(hw.arms[1].link.ser.of_type(1041))
    time.sleep(1.5)
    back = np.array(board.position())
    # it pushed up into the ceiling: it comes back down, off it
    assert back[2] < stuck_at[2] - 30.0, (stuck_at, back)
    assert len(hw.arms[1].link.ser.of_type(1041)) <= n_blue + 1, "blue kept moving"
    print(f"  an arm that meets something in the air backs off it "
          f"({np.linalg.norm(back - stuck_at):.0f} mm) and the others hold: {why!r}")
    assert not hw.arms[1].link.ser.of_type(0)
    hw.close()
    # the live view stalls: an arm not fed a new point stops where it is
    hw = Hardware.fake(1, log=lambda s: None)
    hw.place([PA.pose(0, 0, 0, 0.0)])
    (j, w), = hw.measured().values()
    hw.start()
    start = np.array(hw.arms[0].link.ser.position())
    hw.update({0: w + np.array([0.0, 0.0, 150.0])})       # far, and then nothing
    time.sleep(1.5)
    went = float(np.linalg.norm(np.array(hw.arms[0].link.ser.position()) - start))
    assert went < 30.0, went
    print(f"  fed nothing for {WATCHDOG_S:.1f} s, an arm stops where it is "
          f"({went:.0f} mm along)")
    hw.close()
    # a board that restarts: every arm holds
    hw = Hardware.fake(2, log=lambda s: None)
    hw.place([PA.pose(0, 0, 0, 0.0), PA.pose(0, 800, 0, 0.0)])
    got = hw.measured()
    hw.start()
    hw.arms[1].link.ser._say("RoArm-M2 version: 0.84")
    t0 = time.monotonic()
    why = None
    while why is None and time.monotonic() - t0 < 2.0:
        why = hw.update({a: w for a, (j, w) in got.items()})
        time.sleep(0.05)
    assert why and "restarted" in why, why
    assert hw.arms[1].link.ser.of_type(122)[-1]["spd"] == SERVO_SPD_DEG
    print(f"  a board that restarts makes every arm hold, speed capped again: {why!r}")
    hw.close()
    # a servo that stops answering: the board repeats its last reading
    hw = Hardware.fake(1, log=lambda s: None)
    hw.place([PA.pose(0, 0, 0, 0.0)])
    (j, w), = hw.measured().values()
    hw.start()
    board = hw.arms[0].link.ser
    board.frozen = True
    t0 = time.monotonic()
    why = None
    while why is None and time.monotonic() - t0 < 5.0:
        why = hw.update({0: w + np.array([0.0, 0.0, 90.0])})
        time.sleep(0.05)
    assert why and "stopped changing" in why, why
    print(f"  a servo that stops answering is caught: {why!r}")
    hw.close()
    # sent 30 mm into a surface it can only be pushed 8 mm into: stuck at
    # its cap, off its path. On the skin that is pressing; off it, a bump.
    for skin in (True, False):
        hw = Hardware.fake(1, log=lambda s: None)
        hw.place([PA.pose(0, 0, 0, 0.0)])
        (j, w), = hw.measured().values()
        hw.start()
        board = hw.arms[0].link.ser
        floor = board.position()[2]
        board.surface = lambda q, f=floor: f - q[2]
        t0 = time.monotonic()
        why = None
        while why is None and time.monotonic() - t0 < 1.5:
            why = hw.update({0: w - np.array([0.0, 0.0, 30.0])}, on_skin={0: skin})
            time.sleep(0.05)
        arm = hw.arms[0]
        if skin:
            assert why is None, why
            assert arm.off_path() > LOAD_OFF_MM and abs(arm.loads()["torE"]) >= 45, (
                arm.off_path(), arm.loads())
        else:
            assert why and "torque cap" in why, why
        hw.close()
    print("  stuck at the cap while pressing on the skin is contact; off the skin, a bump")
    # pressing, but held far short of the point: the person is much nearer
    hw = Hardware.fake(1, log=lambda s: None)
    hw.place([PA.pose(0, 0, 0, 0.0)])
    (j, w), = hw.measured().values()
    hw.start()
    board = hw.arms[0].link.ser
    floor = board.position()[2]
    board.surface = lambda q, f=floor: f - q[2]
    t0 = time.monotonic()
    why = None
    while why is None and time.monotonic() - t0 < 5.0:
        k = min(1.0, (time.monotonic() - t0) / 0.8)
        why = hw.update({0: w - np.array([0.0, 0.0, 70.0 * k])}, on_skin={0: True})
        time.sleep(0.05)
    assert why and "nearer than the model" in why, why
    print(f"  pressing but held {PRESS_BLOCK_MM:.0f}+ mm short: it backs off: {why!r}")
    hw.close()
    # the log and the report
    import tempfile
    hw = Hardware.fake(1, log=lambda s: None)
    hw.place([PA.pose(0, 0, 0, 0.0)])
    (j, w), = hw.measured().values()
    hw.start()
    path = os.path.join(tempfile.mkdtemp(), "drive.jsonl")
    log = DriveLog(path)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.5:
        k = (time.monotonic() - t0) / 1.5
        tgt = w + np.array([0.0, 80.0 * k, 0.0])
        hw.update({0: tgt}, on_skin={0: k > 0.5})
        log.write(hw, {0: tgt}, {0: "working"}, {0: k > 0.5}, {0: 30.0})
        time.sleep(0.04)
    log.note("coverage", **{"pass": 1, "done": True, "plan": 0.9, "real": 0.7})
    log.note("sight", arm=0, pixels=300, seen=0.2, shift=[0.0, 40.0, 0.0],
             seen_there=0.95, verdict="off")
    log.close()
    hw.close()
    text = report(path)
    assert "real tool point vs approved" in text and "encoder angles" in text, text
    assert "pass 1: the real sponges reached 70%" in text, text
    assert "camera check: off 1 times" in text, text
    print("  a run's log reports what the arm really did:")
    print("    " + "\n    ".join(text.splitlines()[1:]))
    print("  arm_hw self-test OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--assign", action="store_true",
                    help="name the arms plugged in (each gripper twitches)")
    ap.add_argument("--rig", default=os.path.join(HERE, "live_rig.json"))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--report", metavar="LOG",
                    help="what the arms really did in a --drive run (a drive_logs file)")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    if a.report:
        print(report(a.report))
        return 0
    ports = candidate_ports()
    if not ports:
        print("No arm boards found. Connect each arm's driver board to the laptop "
              "with a USB-C data cable, on the board's port marked USB, and power "
              "the arm. If Windows shows no new COM port, install the SiLabs CP210x "
              "driver (see scrub3d/live/ARMS.md).")
        return 1
    print("arm boards found:")
    for c in ports:
        print(f"  {c['port']}: {c['chip']}, USB serial {c['serial'] or '?'}")
    if a.assign:
        with open(a.rig, encoding="utf-8") as f:
            n = len(json.load(f)["arms"])
        assign(ports, n)
        return 0
    print("\nasking each board where its arm is (nothing moves; a board still "
          "starting up can take 20 s):")
    for port, info in identify(ports).items():
        if "error" in info:
            print(f"  {port}: {info['error']}")
            continue
        fb = info["feedback"]
        p = from_fw([fb["x"], fb["y"], fb["z"]])
        print(f"  {port}: MAC {info['mac'] or '?'}; tool {fb['x']:.0f}, {fb['y']:.0f}, "
              f"{fb['z']:.0f} mm from the shoulder; joints b {fb.get('b')} s {fb.get('s')} "
              f"e {fb.get('e')}" + ("" if solvable(p) else "  <- scrub3d cannot solve this"))
    if os.path.exists(PORTS_FILE):
        try:
            print("\nnamed in arm_ports.json:",
                  {NAMES[k % 4]: v for k, v in load_ports().items()})
        except ArmError as exc:
            print(exc)
    else:
        print("\nnext: python scrub3d/live/arm_hw.py --assign")
    return 0


if __name__ == "__main__":
    sys.exit(main())
