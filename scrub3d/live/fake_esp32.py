"""scrub3d/live/fake_esp32.py -- a RoArm-M2-S driver board, in software.

Used by arm_hw.py's self-test and by `live_body.py --drive fake`, on
Windows, where tests/fake_roarm.py (a pty) cannot run. It stands in for the
serial port object: the driver writes newline-terminated JSON to it and
reads the replies, as it would from the real board.

It follows the firmware as waveshareteam/roarm_m2 (0.84) has it:
  - positions are in the firmware's frame, whose origin is the shoulder
    pivot, not the base (FW_ORIGIN_MM in arm_hw.py);
  - T:1041 solves the joints for a tool point and sends each servo there
    (ServoModel: each joint at up to the servo speed, speeding up and
    slowing down at the servo acceleration, the firmware's boot values
    until a T:122 sets others), keeps `t`, and has no guard against
    unreachable points (this fake ignores them instead);
  - T:122 sends the joints to angles in degrees, and its spd and acc stay
    in force for every later T:1041, as in the firmware;
  - T:105 is answered by T:1051 with x, y, z, b, s, e, t and the four
    torques (firmware joint angles: base and shoulder the other way round
    from scrub3d's, elbow the same);
  - T:0 makes the arm go limp for 10 s, ignoring everything meanwhile, and
    T:999 does nothing;
  - T:605 cmd 1 (the default after a reboot) echoes every line back;
    T:301 sets the ESP-NOW mode; T:302 prints the MAC address as text.
It can also be made to stop answering (`mute`) or to fail its writes
(`stall`), the two ways a real link dies; to meet a surface (`surface`),
which stops the tool at `give_mm` inside it and loads the shoulder and
elbow up to their torque caps, as a person under the sponge would; and to
report joint angles that disagree with its position (`frame_error_mm`),
as a board whose frame is not the one scrub3d assumes would.

Loads (torB/torS/torE/torH) are in the servo's units, taken here as
thousandths of stall torque, never above the joint's T:112 cap: a small
gravity term that grows with the reach, plus the surface's push.

It proves the wire format, the rates, the frames, the start from where the
arm really is, and the stops. Its servos follow the firmware's numbers
(4096 steps a turn; speed in steps/s, 0 for the servo's top speed;
acceleration in 100 steps/s2, 0 for its top), not measurements.
"""
import json
import math
import os
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

ORIGIN = (K.BASE_X_MM, 0.0, K.BASE_H_MM)       # the firmware's origin, base frame
HOME_FW = (310.2, 0.0, 234.8, math.pi)          # where a powered-up arm goes
LIMP_S = 10.0
STEP_RAD = 2.0 * math.pi / 4096.0               # one servo step
TOP_RAD_S = 40.0 / 60.0 * 2.0 * math.pi         # the servo's own top speed, 40 rpm
TOP_RAD_S2 = 254 * 100 * STEP_RAD               # its top acceleration (register 254)
BOOT_ACC_REG = 20                               # ARM_SERVO_INIT_ACC: the boot value
SPEED_SCALE = 1.0                               # tests slow every servo with this


def reg_speed(reg):
    """A speed register (steps/s, 0: top speed) -> rad/s"""
    return TOP_RAD_S if reg <= 0 else min(reg * STEP_RAD, TOP_RAD_S)


def reg_acc(reg):
    """An acceleration register (100 steps/s2, 0: top) -> rad/s2"""
    reg = int(reg) % 256
    return TOP_RAD_S2 if reg == 0 else reg * 100 * STEP_RAD


class ServoModel:
    """An arm's three joints as their servos move them: each toward its goal
    at up to `speed`, speeding up and slowing down at up to `accel`."""

    def __init__(self, q, speed=TOP_RAD_S, accel=reg_acc(BOOT_ACC_REG)):
        self.q = np.array(q, float)[:3]
        self.qd = np.zeros(3)
        self.goal = self.q.copy()
        self.speed, self.accel = float(speed), float(accel)

    def step(self, dt):
        """Move on by `dt` seconds (in pieces of at most 2 ms)."""
        n = max(1, int(math.ceil(dt / 0.002)))
        h = dt / n
        v_top = self.speed * SPEED_SCALE
        for _ in range(n):
            err = self.goal - self.q
            want = np.sign(err) * np.minimum(v_top, np.sqrt(2.0 * self.accel * np.abs(err)))
            self.qd += np.clip(want - self.qd, -self.accel * h, self.accel * h)
            nq = self.q + self.qd * h
            past = np.sign(self.goal - nq) * np.sign(err) < 0
            nq[past] = self.goal[past]
            self.qd[past] = 0.0
            self.q = nq

    def stop(self):
        self.goal = self.q.copy()
        self.qd[:] = 0.0


def _fk_fw(q):
    """scrub3d joints -> firmware x, y, z"""
    x, y, z = (float(v) for v in K.fk(*q))
    return x - ORIGIN[0], y - ORIGIN[1], z - ORIGIN[2]


class SerialTimeout(Exception):
    """What a stalled write raises; arm_hw treats it like pyserial's."""


def _joints(x, y, z):
    """Firmware x, y, z -> scrub3d joints, or None."""
    return K.ik(x + ORIGIN[0], y + ORIGIN[1], z + ORIGIN[2])


class FakeESP32:
    """One arm's board, as a serial port object (write, readline, ...)."""

    def __init__(self, start=HOME_FW, name="fake", mac="AA:BB:CC:00:00:01",
                 serial_number="0001"):
        self.name, self.mac, self.serial_number = name, mac, serial_number
        self.x, self.y, self.z, self.t = (float(v) for v in start)
        self.goal = (self.x, self.y, self.z, self.t)
        self.servo = ServoModel(_joints(self.x, self.y, self.z))
        self.x, self.y, self.z = _fk_fw(self.servo.q)
        self.speed_reg, self.acc_reg = 0, BOOT_ACC_REG
        self.torque_caps = {"b": 1000, "s": 1000, "e": 1000, "h": 1000}
        self.limp_until = 0.0
        self.echo = True                 # T:605 cmd 1 is the firmware default
        self.espnow_mode = 1             # a follower, until told otherwise
        self.muted = False               # stops answering, as a wedged board does
        self.stalled = False             # writes fail, as a full buffer does
        # (point in, fw frame) -> how far inside something it is, mm, or None
        self.surface = None
        self.give_mm = 8.0               # how far the tool can be pushed into it
        self.push_per_mm = 25.0          # load per mm pushed in
        self.frame_error_mm = (0.0, 0.0, 0.0)
        self.frozen = False              # replies repeat, as when a servo stops answering
        self.miss_mm = (0.0, 0.0, 0.0)   # where it really goes, off where it is sent
        self._last_fb = None
        self.is_open = True
        self.port = f"FAKE:{name}"
        self.dtr = self.rts = False
        self.commands, self.times = [], []
        self.bad = []
        self._out = []
        self._last = time.perf_counter()
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

    # --- the serial port face --------------------------------------------
    def write(self, data):
        if not self.is_open:
            raise OSError("port closed")
        if self.stalled:
            raise SerialTimeout("write timeout (fake stall)")
        for line in data.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if line:
                self._handle(line)
        return len(data)

    def readline(self, timeout=0.3):
        end = time.perf_counter() + timeout
        with self._cv:
            while not self._out and self.is_open:
                left = end - time.perf_counter()
                if left <= 0:
                    return b""
                self._cv.wait(left)
            return self._out.pop(0) if self._out else b""

    def reset_input_buffer(self):
        with self._lock:
            self._out.clear()

    def close(self):
        with self._cv:
            self.is_open = False
            self._cv.notify_all()

    # --- the firmware ------------------------------------------------------
    def limp(self):
        return time.perf_counter() < self.limp_until

    def _advance(self):
        now = time.perf_counter()
        dt, self._last = now - self._last, now
        if self.limp():
            return
        q0 = self.servo.q.copy()
        self.servo.step(min(dt, 1.0))
        nx, ny, nz = _fk_fw(self.servo.q)
        if self.surface is not None:
            pen = self.surface((nx, ny, nz))
            was = self.surface((self.x, self.y, self.z))
            was = -math.inf if was is None else was
            # Held back by what it meets only while going further in.
            if pen is not None and pen > self.give_mm and pen > was + 1e-6:
                # held back by what it meets: as far along as it gives
                q1 = self.servo.q.copy()
                lo, hi = 0.0, 1.0
                for _ in range(16):
                    mid = (lo + hi) / 2
                    p = self.surface(_fk_fw(q0 + (q1 - q0) * mid))
                    if p is not None and p > self.give_mm:
                        hi = mid
                    else:
                        lo = mid
                self.servo.q = q0 + (q1 - q0) * lo
                self.servo.qd[:] = 0.0
                nx, ny, nz = _fk_fw(self.servo.q)
        self.x, self.y, self.z = nx, ny, nz
        self.t = self.goal[3]

    def _loads(self):
        """Servo loads, thousandths of stall torque, each within its cap."""
        reach = math.hypot(self.x, self.y)
        load = {"torB": 5.0, "torS": 20.0 + 0.12 * reach, "torE": 10.0 + 0.06 * reach,
                "torH": 5.0}
        pen = self.surface((self.x, self.y, self.z)) if self.surface else None
        if pen is not None and pen > 0:
            load["torS"] += self.push_per_mm * pen
            load["torE"] += self.push_per_mm * pen
        if (self.goal and math.dist(self.goal[:3], (self.x, self.y, self.z)) > 20.0
                and pen is not None and pen > 0):
            for k in load:                             # stalled against it
                load[k] = 1e9
        caps = {"torB": "b", "torS": "s", "torE": "e", "torH": "h"}
        return {k: round(min(v, self.torque_caps[caps[k]]), 1) for k, v in load.items()}

    def _say(self, text):
        with self._cv:
            if not self.muted:
                self._out.append((text + "\r\n").encode())
                self._cv.notify_all()

    def _handle(self, line):
        with self._lock:
            self.times.append(time.perf_counter())
            if self.limp():
                return                   # T:0 blocks the firmware's loop
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                self.bad.append(line)
                return
            self.commands.append(d)
            self._advance()
        if self.echo:
            self._say(line)
        T = d.get("T")
        if T == 1041:
            x, y, z = d.get("x", self.x), d.get("y", self.y), d.get("z", self.z)
            if _joints(x, y, z) is None:
                return                   # the firmware would compute NaN angles
            with self._lock:
                t = float(d["t"]) if "t" in d else 1.08   # no t: the gripper opens
                m = self.miss_mm
                if _joints(x + m[0], y + m[1], z + m[2]) is not None:
                    x, y, z = x + m[0], y + m[1], z + m[2]
                self.goal = (float(x), float(y), float(z), t)
                self.servo.goal = np.array(_joints(x, y, z), float)
        elif T == 122:
            with self._lock:
                b, s_, e = (math.radians(float(d.get(k, 0.0))) for k in ("b", "s", "e"))
                self.servo.goal = np.array([-b, -s_, e], float)
                self.speed_reg = int(round(abs(float(d.get("spd", 0.0))) * 4096 / 360))
                self.acc_reg = int(round(abs(float(d.get("acc", 0.0))) * 4096 / 360)) % 256
                self.servo.speed = reg_speed(self.speed_reg)
                self.servo.accel = reg_acc(self.acc_reg)
                gx, gy, gz = _fk_fw(self.servo.goal)
                self.goal = (gx, gy, gz, math.radians(float(d.get("h", 180.0))))
        elif T == 105:
            if self.frozen and self._last_fb is not None:
                self._say(self._last_fb)
                return
            with self._lock:
                self._advance()
                fe = self.frame_error_mm
                j = (tuple(self.servo.q) if not any(fe) else
                     (_joints(self.x + fe[0], self.y + fe[1], self.z + fe[2])
                      or (0.0, 0.0, 0.0)))
                fb = {"T": 1051, "x": round(self.x, 2), "y": round(self.y, 2),
                      "z": round(self.z, 2), "b": round(-j[0], 4), "s": round(-j[1], 4),
                      "e": round(j[2], 4), "t": round(self.t, 3), **self._loads()}
            self._last_fb = json.dumps(fb)
            self._say(self._last_fb)
        elif T == 112:
            if d.get("mode", 1) == 0:
                self.torque_caps = {k: 1000 for k in self.torque_caps}
            for k in ("b", "s", "e", "h"):
                if k in d and d.get("mode", 1) == 1:
                    self.torque_caps[k] = d[k]
        elif T == 0:
            with self._lock:
                self.limp_until = time.perf_counter() + LIMP_S
                self.goal = (self.x, self.y, self.z, self.t)
                self.servo.stop()
        elif T == 605:
            self.echo = int(d.get("cmd", 0)) == 1
        elif T == 301:
            self.espnow_mode = int(d.get("mode", self.espnow_mode))
        elif T == 302:
            self._say(f"MAC: {self.mac}")

    # --- for tests ----------------------------------------------------------
    def of_type(self, T):
        with self._lock:
            return [c for c in self.commands if c.get("T") == T]

    def position(self):
        """Firmware frame."""
        with self._lock:
            self._advance()
            return (self.x, self.y, self.z)
