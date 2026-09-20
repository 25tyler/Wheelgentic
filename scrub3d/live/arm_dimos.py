"""scrub3d/live/arm_dimos.py -- the OpenYAM arms, to the live view, exactly
like arm_hw's Hardware -- but through dimOS instead of a serial port.

    python scrub3d/live/live_body.py --drive dimos --rig scrub3d/live/live_rig_openyam.json
    SCRUB3D_ARM=openyam                     # the kinematics the governor uses
    SCRUB3D_DIMOS=10.189.59.208:7790        # where dimos_bridge_server.py runs

WHAT THE LIVE VIEW SEES
-----------------------
The same object it drives RoArms with: `Hardware.connect()`, `place(layout)`,
`measured()`, `start()`, `joints()`, `update(points, stop, on_skin)`,
`hold_all(why)`, `close()`, and per arm `actual()`, `actual_world()`,
`measured_joints()`, `hold()`, `retreat()`, `name`, `link.port`. Nothing in
live_body.py or arms_live.py changes to drive a different arm; that is the
whole point of the contract, and this file keeps it.

WHAT IS DIFFERENT UNDERNEATH
----------------------------
- No firmware IK: a target is a POSE (position and orientation) of the grasp
  frame, in metres, in dimOS's world frame. The live view thinks in world
  millimetres and points; the orientation is derived here (the sponge faces
  the target from the arm's shoulder, or along a surface normal when given).
- No serial link to watch: dimOS's cartesian task holds an arm 0.5 s after
  the last pose it received. So this half streams poses only while it has a
  fresh point from the live view (WATCHDOG_S, as arm_hw), and "hold" is
  simply "stop streaming".
- The "joint angles" the rest of scrub3d reasons about are the three-joint
  planar equivalent from kinematics_openyam (SCRUB3D_ARM=openyam), solved
  from where dimOS says the tool is. dimOS keeps the real six joints; the
  fleet governor and the depth guard keep working on the equivalent.

TWO ARMS, ONE MODEL, ANY PLANK
------------------------------
dimOS models the pair as one robot on one plate: the left arm's base at
(0, +0.31, 0) m and the right at (0, -0.31, 0) m in that plate's frame,
both facing +x. The real arms sit wherever the rig file says -- on a plank
across a wheelchair's armrests, at whatever spacing and facing the drill
produced. So every target is converted through the ARM'S OWN base pose from
the rig and then placed at dimOS's nominal offset for that arm: dimOS solves
the arm relative to its base, which is all that has to agree. What dimOS
cannot see is where the OTHER arm really is; the fleet governor checks the
two real arms against each other, so that hole is covered upstream. Rig
entry a0 is the arm on --left-can-port, a1 the one on --right-can-port.
"""
import collections
import json
import math
import os
import socket
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(HERE)
if WT not in sys.path:
    sys.path.insert(0, WT)
import kinematics as K                              # noqa: E402

DEFAULT_ENDPOINT = "127.0.0.1:7790"
ENV = "SCRUB3D_DIMOS"

RATE_HZ = 40.0
MAX_STEP_MM = 12.0          # per command: 480 mm/s, which is arms_live's MAX_TOOL_V.
                            # This was 6, half of what the planner paces its strokes
                            # for, so every stroke took twice as long as intended.
                            # What an arm may do NEAR the person is V_TOUCH and
                            # V_CLOSE in arms_live, not this; dimOS caps every joint
                            # at 1 rad/s underneath anyway
MIN_FEED_V = 40.0           # mm/s: the slowest a fed point is paced at
FOLLOW_TAU_S = float(os.environ.get("SCRUB3D_FOLLOW_TAU_S", 0.15))
                            # the claw closes what is left of the way in about this
                            # long. At 0.30 it trailed its plan by 5 to 8 cm, which is
                            # over what the plan will lead by, so the plan still waited
                            # a third of the time; and a 1 Hz scrub stroke came out at
                            # half its size. 0.15 still spans the view's 6 points a
                            # second, which is all the smoothing was for
FOLLOW_MAX_V = 480.0        # mm/s: as fast as it chases: MAX_STEP_MM at RATE_HZ
FOLLOW_ACC = 3000.0         # mm/s2: how fast that speed may build (it brakes at twice this)
PACE_OVER = 1.15            # keep up with the feed, a little faster than it comes
WATCHDOG_S = 0.5            # no new point this long: stop streaming, the arm holds
STALE_S = 1.0               # no state from the bridge this long: not answering
TRACK_MM = 80.0             # this far from where it was sent lately ...
TRACK_S = 0.8               # ... for this long: something holds it back
TRACK_WINDOW_S = 0.5
RETREAT_MM = 50.0
BACK_OFF_S = 4.0
STATE_HZ = 20.0
OVER_Z_MM = 400.0           # above the base: the height an arm crosses its own blind spot at
CLAWS_EVERY_S = 3.0         # the claws are told to stay shut this often
AIM_LEAD_RAD = math.radians(20.0)   # how far ahead of the claw's angle a pose may ask
WRIST_EASE_RAD = 1.15       # a wrist joint past this: stop asking the claw to turn

SIDES = ("left", "right")
SIDE_OFFSET_M = {"left": np.array([0.0, 0.31, 0.0]),
                 "right": np.array([0.0, -0.31, 0.0])}
NAMES = {"left": "left", "right": "right"}


class ArmError(RuntimeError):
    pass


def _planner_said(text):
    """Is this dimOS error a planner's answer (no path, a goal or a start it
    will not take) rather than something wrong with the arms?"""
    t = str(text).lower()
    return any(w in t for w in ("plan", "topp", "parametriz", "goal", "trajector",
                                "collision", "configuration", "outside ["))


def _ik_measured(p):
    """Joints for a REPORTED tool point: the nearest legal ones when the
    arm model offers that (the OpenYAM, whose wrist can fold), else ik()."""
    f = getattr(K, "ik_nearest", None)
    return (f or K.ik)(*p)


# --- the wire ---------------------------------------------------------------------

class Link:
    """One line-based TCP connection to dimos_bridge_server.py, shared by both
    arms. Replies are matched by order; one request at a time.

    A reply that comes late costs that one request and nothing more: the
    connection is dropped and the next request opens a fresh one. It used to
    read through socket.makefile(), and a file object that has timed out once
    raises on every read after it, for good; one half-second stall on the
    venue's WiFi and twelve instant failures later the arms were held for the
    rest of the run ("the bridge is not taking commands"). Dropping also keeps
    the order honest: the late reply would otherwise be read as the answer to
    the next request, and every answer after it would be one behind."""

    def __init__(self, endpoint):
        host, _, port = endpoint.rpartition(":")
        self.host, self.tcp_port = host or "127.0.0.1", int(port or 7790)
        self.port_name = f"dimos@{self.host}:{self.tcp_port}"   # arm_hw prints link.port
        self.lock = threading.Lock()
        self.sock, self.buf = None, b""
        self.failed = 0
        self.reconnects = 0
        self.fb_time = None
        self.feedback = {}             # arm_hw's DriveLog reads link.feedback
        self._connect()                # raises if the bridge is not there at all

    @property
    def port(self):
        return self.port_name

    def _connect(self):
        self.sock = socket.create_connection((self.host, self.tcp_port), timeout=3.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""

    def _drop(self):
        try:
            if self.sock is not None:
                self.sock.close()
        except OSError:
            pass
        self.sock, self.buf = None, b""

    def call(self, req, timeout=2.0):
        with self.lock:
            try:
                if self.sock is None:
                    self._connect()
                    self.reconnects += 1
                    print(f"  link to the bridge reopened ({self.reconnects})", flush=True)
                self.sock.settimeout(timeout)
                self.sock.sendall((json.dumps(req) + "\n").encode())
                while b"\n" not in self.buf:
                    chunk = self.sock.recv(65536)
                    if not chunk:
                        raise ConnectionError("bridge closed the connection")
                    self.buf += chunk
                raw, _, self.buf = self.buf.partition(b"\n")
                rep = json.loads(raw)
                self.failed = 0
                return rep
            except (OSError, ValueError) as exc:
                self.failed += 1
                self._drop()
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def close(self):
        try:
            self.call({"op": "bye"}, timeout=1.0)
        except Exception:                                    # noqa: BLE001
            pass
        with self.lock:
            self._drop()


# --- one arm -----------------------------------------------------------------------

def _qmul(a, b):
    """Quaternion product, xyzw."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def _point_along(q, z_want):
    """The orientation nearest to `q` (xyzw) whose z axis is `z_want`: q turned
    by the shortest arc that carries its z axis there, its roll left alone."""
    x, y, z, w = (float(v) for v in q)
    z_now = np.array([2.0 * (x * z + w * y), 2.0 * (y * z - w * x), 1.0 - 2.0 * (x * x + y * y)])
    zw = np.asarray(z_want, float)
    zw = zw / (np.linalg.norm(zw) or 1.0)
    axis = np.cross(z_now, zw)
    sn, cs = float(np.linalg.norm(axis)), float(z_now @ zw)
    if sn < 1e-9:
        if cs > 0.0:
            return (x, y, z, w)                       # there already
        axis = np.cross(z_now, [1.0, 0.0, 0.0])       # straight back: any axis across it
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(z_now, [0.0, 1.0, 0.0])
        sn = float(np.linalg.norm(axis))
    half = 0.5 * math.atan2(sn if cs > -1.0 + 1e-12 else 0.0, cs) if sn >= 1e-9 and cs > -1.0 + 1e-9 else 0.5 * math.pi
    a = axis / (np.linalg.norm(axis) or 1.0) * math.sin(half)
    return tuple(_qmul([a[0], a[1], a[2], math.cos(half)], [x, y, z, w]))


def _toward(qa, qb, max_rad):
    """The rotation at most `max_rad` of the way round from qa to qb (xyzw)."""
    a, b = np.asarray(qa, float), np.asarray(qb, float)
    a, b = a / (np.linalg.norm(a) or 1.0), b / (np.linalg.norm(b) or 1.0)
    d = float(a @ b)
    if d < 0.0:                                     # the short way round
        b, d = -b, -d
    ang = 2.0 * math.acos(min(1.0, d))              # the angle between them
    if ang <= max_rad or ang < 1e-6:
        return tuple(float(v) for v in b)
    t = max_rad / ang
    half = ang / 2.0
    q = (math.sin((1.0 - t) * half) * a + math.sin(t * half) * b) / math.sin(half)
    q = q / np.linalg.norm(q)
    return tuple(float(v) for v in q)


def _quat_from_axes(z, x_hint):
    """Rotation whose z axis is `z` and whose x axis is `x_hint` made
    perpendicular. -> (x, y, z, w)"""
    z = np.asarray(z, float)
    z = z / (np.linalg.norm(z) or 1.0)
    x = np.asarray(x_hint, float) - z * float(np.dot(x_hint, z))
    if np.linalg.norm(x) < 1e-6:
        x = np.cross([0.0, 1.0, 0.0], z)
        if np.linalg.norm(x) < 1e-6:
            x = np.cross([1.0, 0.0, 0.0], z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.column_stack([x, y, z])
    # Shepperd's method, numerically safe for any proper rotation.
    t = np.trace(R)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        w, qx, qy, qz = 0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w, qx, qy, qz = (R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w, qx, qy, qz = (R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w, qx, qy, qz = (R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s
    return (float(qx), float(qy), float(qz), float(w))


class DimosArm:
    """One OpenYAM through the bridge. Mirrors arm_hw.RealArm's surface."""

    def __init__(self, link, side, hw):
        self.link = link
        self.side = side
        self.name = NAMES[side]
        self.hw = hw
        self.lock = threading.Lock()
        self.T = None                  # world <- this arm's base (mm)
        self.Tinv = None
        self.T_dimos = None            # world <- dimOS plate frame (mm)
        self.T_dimos_inv = None
        self.shoulder_world = None
        self.target = None             # arm frame mm, where the live view wants it
        self.sent = None               # arm frame mm, last pose streamed
        self.fed = None
        self.fed_at = None
        self.v_fed = MIN_FEED_V
        self.v_now = 0.0               # mm/s, what the follower is doing now
        self.normal = None             # world unit normal, out of the body, if known
        self.held = False
        self.running = False
        self.thread = None
        self.backing_until = 0.0
        self.on_skin = False
        self.recent = collections.deque()
        self._state = None             # (t_local, p_world_mm, q_xyzw, joints6)
        self.track_since = None
        self.fault_reason = None
        self.told_blind = False        # said once: folded inside the model's reach

    # --- placement --------------------------------------------------------
    def place(self, T_world_base, T_world_dimos):
        self.T = np.asarray(T_world_base, float)
        self.Tinv = np.linalg.inv(self.T)
        self.T_dimos = np.asarray(T_world_dimos, float)
        self.T_dimos_inv = np.linalg.inv(self.T_dimos)
        self.shoulder_world = (self.T @ np.r_[K.BASE_X_MM, 0.0, K.BASE_H_MM, 1.0])[:3]

    # --- where it is --------------------------------------------------------
    def _take_state(self, d, t_local):
        if not d or d.get("p") is None:
            return
        p_dimos_m = np.asarray(d["p"], float)
        # dimOS reports in its plate frame, with this arm's base at its nominal
        # offset; the real base is where the rig says. Through the arm's own
        # base, a plank of any width and facing stays right.
        p_arm_mm = (p_dimos_m - SIDE_OFFSET_M[self.side]) * 1000.0
        p_world = (self.T @ np.r_[p_arm_mm, 1.0])[:3] if self.T is not None else None
        with self.lock:
            self._state = (t_local, p_world, d.get("q"), d.get("joints"))
            self.link.fb_time = t_local

    def actual_world(self):
        with self.lock:
            s = self._state
        return None if s is None or s[1] is None else s[1].copy()

    def quat(self):
        """The claw's orientation as dimOS reports it, xyzw, or None."""
        with self.lock:
            s = self._state
        return None if s is None or s[2] is None else list(s[2])

    def actual(self):
        """Tool point in this arm's own frame, mm (arm_hw's convention)."""
        w = self.actual_world()
        if w is None or self.Tinv is None:
            return None
        return (self.Tinv @ np.r_[w, 1.0])[:3]

    def measured_joints(self):
        """The planar-equivalent three joints of where the tool is, or None."""
        p = self.actual()
        return None if p is None else _ik_measured(p)

    def real_joints6(self):
        with self.lock:
            s = self._state
        return None if s is None else s[3]

    def loads(self):
        """Servo loads: the RoArm reported them; dimOS reports torques we do
        not read yet. Empty, so the drive log stays well-formed."""
        return {}

    def model_error(self):
        return None

    # --- following ----------------------------------------------------------
    def follow(self, world_point, normal=None):
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
            if normal is not None:
                n = np.asarray(normal, float)
                if np.linalg.norm(n) > 1e-6:
                    self.normal = n / np.linalg.norm(n)

    def _orientation_for(self, p_world):
        """The grasp frame's z axis points OUT of the tool's target surface:
        the tool itself extends along -z, so this presses along -n. Without
        a normal, it faces away from the shoulder."""
        if self.normal is None:
            # No normal from the live view: keep the wrist as it is. Asking for
            # any other orientation makes the IK trade position for it, and
            # on the mock arms that traded 140 mm of position for a 30 mm move.
            with self.lock:
                s = self._state
            if s is not None and s[2] is not None:
                return tuple(float(v) for v in s[2])
            z_world = self.shoulder_world - np.asarray(p_world, float)
            z_world[2] = 0.35 * z_world[2]          # mostly horizontal
            if np.linalg.norm(z_world) < 1e-6:
                z_world = np.array([0.0, 0.0, 1.0])
        else:
            z_world = self.normal
        R = self.Tinv[:3, :3]                        # this arm's base: what dimOS solves in
        z_dimos = R @ z_world
        with self.lock:
            cur = self._state
        if self.normal is not None and cur is not None and cur[2] is not None:
            # Which way the claw POINTS is asked for; how it is ROLLED about that
            # line is not. Asking for the roll too ("keep up as the tool's x")
            # is what wound the wrists: after one aimed run joint 4 stood at
            # -1.67 rad against a stop at -1.69, and joint 6 at -1.65. A sponge
            # pressed flat does not care how it is rolled, so the claw is asked
            # for the smallest turn that points it at the skin and keeps
            # whatever roll it has.
            want = _point_along(cur[2], z_dimos)
        else:
            x_hint = R @ np.array([0.0, 0.0, 1.0])   # keep "up" as the tool's x
            want = _quat_from_axes(z_dimos, x_hint)
        # Square on to the skin, but asked for a little at a time: never more
        # than AIM_LEAD_RAD ahead of where the claw is now. Asked for all at
        # once, a claw that is a quarter turn off gives the solver an error it
        # answers by throwing the wrist at its stop; a small standing lead is
        # a turn it can simply make, and the next pose asks for the next bit.
        with self.lock:
            st = self._state
        if st is None or st[2] is None:
            return want
        # ... and not at all while a wrist joint is far round. Turning the claw
        # 40 degrees about the vertical took joint 5 to 1.33 rad, and its stop
        # is near 1.6: past that dimOS reads a joint outside its limit, drops
        # the torque and ends the session, which is how two of them ended. Near
        # a stop the claw is asked only to stay as it is, and dimOS's own
        # posture term eases the wrist back; square on is given up before
        # power is.
        if st[3] and max(abs(float(v)) for v in st[3][3:6]) > WRIST_EASE_RAD:
            return tuple(float(v) for v in st[2])
        return _toward(st[2], want, AIM_LEAD_RAD)

    def _send(self, p_arm):
        p_world = (self.T @ np.r_[np.asarray(p_arm, float), 1.0])[:3]
        p_dimos = np.asarray(p_arm, float) / 1000.0 + SIDE_OFFSET_M[self.side]
        q = self._orientation_for(p_world)
        rep = self.link.call({"op": "target", "arm": self.side,
                              "p": [float(v) for v in p_dimos], "q": list(q)}, timeout=0.5)
        return bool(rep.get("ok"))

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self):
        period = 1.0 / RATE_HZ
        nxt = time.perf_counter()
        while self.running:
            nxt += period
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.perf_counter()
            now = time.monotonic()
            with self.lock:
                tgt, sent = self.target, self.sent
                fed_at, v_fed = self.fed_at, self.v_fed
            if tgt is None:
                continue
            if sent is None:
                here = self.actual()
                if here is None:
                    continue
                if K.ik(*here) is None:
                    # The real arm has six joints and folds where the model's
                    # three cannot follow: inside its minimum reach, a blind
                    # spot 195 mm across at the shoulder, which is exactly
                    # where a parked arm ends up. Starting from a point the
                    # model cannot solve means every step is refused and the
                    # arm stands still for the whole run. Start from the
                    # nearest pose the model does have and step out of it.
                    if not self.told_blind:
                        self.told_blind = True
                        print(f"  {self.name}: folded "
                              f"{abs(float(K.reach_margin(*here))):.0f} mm inside the reach "
                              f"the model knows; starting it from the nearest pose it does",
                              flush=True)
                    here = K.fk(*K.ik_nearest(*here))
                    # ... and from above the plank, if it has sagged under the
                    # line nothing is proposed below: else it is the same trap.
                    floor = getattr(K, "FLOOR_Z_MM", None)
                    if floor is not None and here[2] < floor + 1.0:
                        here = (here[0], here[1], floor + 1.0)
                sent = tuple(float(v) for v in here)
            if (fed_at is not None and now - fed_at > WATCHDOG_S
                    and now > self.backing_until and not self.held):
                continue                                  # the live view stalled: stop
            d = math.dist(tgt, sent)
            if d < 0.2 and self.held:
                continue
            # One smooth move, not a string of small ones. The speed comes from
            # how far there is to go (d / FOLLOW_TAU_S), so the claw runs when
            # it is behind and eases in as it arrives, and it may only change
            # by FOLLOW_ACC a second, so nothing jerks. It used to be paced by
            # how fast new points ARRIVED: the view sends 5 to 8 a second and
            # the plan pauses often, so the claw stepped to each point, stopped
            # and waited for the next. Measured over a five minute run it stood
            # still 40% of the time, and chased a plan doing 85 to 150 mm/s at
            # 30, which made the plan wait for it, which slowed the points...
            want = min(FOLLOW_MAX_V, d / FOLLOW_TAU_S)
            dv = FOLLOW_ACC * period
            self.v_now = min(want, self.v_now + dv) if want > self.v_now else max(
                want, self.v_now - 2.0 * dv)               # brakes harder than it pulls away
            pace = min(MAX_STEP_MM, self.v_now * period)
            k = min(1.0, pace / d) if d > 1e-9 else 1.0
            step = tuple(s + (g - s) * k for s, g in zip(sent, tgt))
            if K.ik(*step) is None:
                # The line to a good point can clip a place the model cannot
                # solve (its blind spot at the base, the plank's floor). Go over
                # rather than stand there: the same step, no lower than the floor.
                floor = getattr(K, "FLOOR_Z_MM", None)
                if floor is not None and step[2] < floor + 1.0:
                    step = (step[0], step[1], floor + 1.0)
                if K.ik(*step) is None:
                    # Still not: the way THROUGH is shut, so go OVER. An arm
                    # folded at the front whose work lies behind its own base
                    # (a plank at the knees, the sitter's arms behind it) has
                    # the base's blind spot on the straight line, and it stood
                    # there for whole runs while its plan went on without it.
                    # Straight up where it is to OVER_Z_MM, then level toward
                    # the target at that height, which passes above its own
                    # base and plank and nothing else; once the straight line
                    # down to the target can be solved it is taken as before.
                    if sent[2] < OVER_Z_MM - 1.0:
                        step = (sent[0], sent[1], min(OVER_Z_MM, sent[2] + pace))
                    else:
                        gx, gy = tgt[0] - sent[0], tgt[1] - sent[1]
                        g = math.hypot(gx, gy)
                        kk = min(1.0, pace / g) if g > 1e-9 else 0.0
                        step = (sent[0] + gx * kk, sent[1] + gy * kk, OVER_Z_MM)
                if K.ik(*step) is None:
                    self.v_now = 0.0
                    continue                              # hold rather than guess
            if self._send(step):
                with self.lock:
                    self.sent = step
                    self.recent.append((now, step))
                    while self.recent and now - self.recent[0][0] > TRACK_WINDOW_S:
                        self.recent.popleft()

    # --- faults -------------------------------------------------------------
    def fault(self, now=None):
        now = time.monotonic() if now is None else now
        if self.fault_reason:
            return self.fault_reason
        if self.link.fb_time is None or now - self.link.fb_time > STALE_S:
            return f"{self.name}: dimOS stopped answering"
        if self.link.failed >= 12:
            return f"{self.name}: the bridge is not taking commands"
        here = self.actual()
        with self.lock:
            recent = list(self.recent)
        if here is not None and recent:
            near = min(math.dist(here, p) for _, p in recent)
            if near > TRACK_MM:
                if self.track_since is None:
                    self.track_since = now
                elif now - self.track_since > TRACK_S:
                    return (f"{self.name}: {near:.0f} mm from anywhere it was sent "
                            f"for {TRACK_S:.1f} s: something holds it back "
                            + ("(met them)" if self.on_skin else "(met something)"))
            else:
                self.track_since = None
        return None

    def resume(self):
        """Let this arm be driven again after a hold. It forgets where it was
        sent: the tracking check measures the arm against the poses it was
        given, and the ones from before the hold are all far away by now."""
        self.held = False
        self.v_now = 0.0
        self.track_since = None
        self.backing_until = 0.0
        with self.lock:
            self.recent.clear()
            self.target = self.sent = None
            self.fed = self.fed_at = None

    def hold(self):
        """Stop streaming: dimOS holds the arm 0.5 s after the last pose."""
        now = time.monotonic()
        again = self.held and now - getattr(self, "hold_sent_at", 0.0) < 1.0
        self.held = True
        with self.lock:
            self.target = self.sent
        if again:
            return True            # told already: not twice a frame over the WiFi
        self.hold_sent_at = now
        rep = self.link.call({"op": "hold", "arm": self.side}, timeout=1.0)
        return bool(rep.get("ok"))

    def retreat(self, mm=RETREAT_MM, wait=True, away_from=None):
        here = self.actual()
        if here is None or self.held or self.link.failed:
            return None
        shoulder = np.array([K.BASE_X_MM, 0.0, K.BASE_H_MM])
        back = shoulder - here
        if away_from is not None and float(np.linalg.norm(here - np.asarray(away_from))) > 1.0:
            back = here - np.asarray(away_from, float)
        n = float(np.linalg.norm(back))
        if n < 1.0:
            return None
        goal = here + back * min(1.0, mm / n)
        if K.ik(*goal) is None:
            return None
        with self.lock:
            self.sent = tuple(float(v) for v in here)
            self.target = tuple(float(v) for v in goal)
            self.v_fed = MIN_FEED_V * 2.0
            self.fed_at = time.monotonic()
            self.backing_until = time.monotonic() + BACK_OFF_S
        if wait:
            self.wait_near(goal)
        return tuple(goal)

    def wait_near(self, goal, tol=5.0, timeout=BACK_OFF_S):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            here = self.actual()
            if here is not None and math.dist(here, goal) <= tol:
                return True
            time.sleep(0.05)
        return False


# --- the fleet -----------------------------------------------------------------

class Hardware:
    """Both OpenYAMs, by rig arm index: 0 is the left arm, 1 the right."""

    recovers = True            # a hold here clears when its reason passes (update())

    def __init__(self, link, arms, mock):
        self.link = link
        self.arms = arms                        # {rig index: DimosArm}
        self.mock = mock
        self.reason = None
        self.hold_until = 0.0                   # a hold stands at least this long
        self.sticky = False                     # ... and for good, if the stack faulted
        self.drive_log = None
        self._state_thread = None
        self._running = False

    @classmethod
    def connect(cls, endpoint=None, log=print):
        endpoint = endpoint or os.environ.get(ENV, DEFAULT_ENDPOINT)
        if getattr(K, "ARM_MODEL", "roarm") != "openyam":
            raise ArmError("SCRUB3D_ARM=openyam is not set: the governor would plan "
                           "these OpenYAMs as RoArms. Set it and start again.")
        try:
            link = Link(endpoint)
        except OSError as exc:
            raise ArmError(f"dimOS bridge at {endpoint} not reachable ({exc}). Start "
                           f"scrub3d/live/dimos_bridge_server.py on the arm computer, "
                           f"and set {ENV}=host:port here.")
        info = link.call({"op": "info"}, timeout=5.0)
        if not info.get("ok"):
            link.close()
            raise ArmError(f"dimOS bridge at {endpoint}: {info.get('error')}")
        sides = info.get("arms", [])
        if sorted(sides) != ["left", "right"]:
            link.close()
            raise ArmError(f"dimOS bridge reports arms {sides}, expected left and right")
        hw = cls(link, {}, info.get("mock"))
        for a, side in enumerate(SIDES):
            hw.arms[a] = DimosArm(link, side, hw)
        hw._poll_once()
        for a, arm in hw.arms.items():
            p = arm.real_joints6()
            log(f"  {arm.name} arm through dimOS at {link.port}"
                + (f": joints {np.round(p, 2).tolist()}" if p else ": no state yet")
                + ("  [MOCK]" if hw.mock else ""))
        return hw

    @classmethod
    def fake(cls, n=2, starts=None, log=print):
        """The same, against a bridge running mock arms (there is no
        in-process fake: dimOS is the simulator)."""
        return cls.connect(log=log)

    # --- state ------------------------------------------------------------
    def _poll_once(self):
        rep = self.link.call({"op": "state"}, timeout=1.0)
        if not rep.get("ok"):
            return False
        t = time.monotonic()
        for arm in self.arms.values():
            arm._take_state(rep.get("arms", {}).get(arm.side), t)
        err = str(rep.get("error") or "")
        if err and not _planner_said(err) and "state read failed" not in err:
            for arm in self.arms.values():
                arm.fault_reason = f"dimOS: {err}"
        else:
            # What the PLANNER last said is not a condition of the arms. A
            # refused plan at start ("joint2 is outside its limit", by 1e-20,
            # for an arm lying on its stop) was read as a fault and held both
            # arms for a whole run, in which nothing at all was wrong.
            for arm in self.arms.values():
                if arm.fault_reason and _planner_said(arm.fault_reason):
                    arm.fault_reason = None
        return True

    def close_claws(self):
        """Shut both claws (0 is closed). An open claw is 9 cm across where the
        model has a closed one, and the fingers are what reach the person
        first. The start pose used to shut them on its way; it is gone."""
        ok = True
        for arm in self.arms.values():
            ok &= bool(self.link.call({"op": "gripper", "arm": arm.side, "pos": 0.0},
                                      timeout=1.0).get("ok"))
        return ok

    WRIST_STRAIGHT_RAD = 0.15   # nearer zero than this is straight enough
    STOP_NEAR_RAD = 0.03        # a shoulder or elbow this near zero is lying on its stop

    def _off_the_stops(self):
        """Lift an arm that is lying on a joint stop 5 cm straight up, by
        streaming. -> names of the arms lifted

        With the torque off an arm sags until its shoulder rests on its stop,
        and reads exactly the limit. dimOS's planner then refuses ANY plan
        from there: its time parametrization finds the joint "outside" its own
        limit by 1e-20, which is rounding. Streaming does not go through the
        planner, and up is the one direction that is always clear."""
        lifted = []
        for arm in self.arms.values():
            q = arm.real_joints6()
            if not q or (q[1] > self.STOP_NEAR_RAD and q[2] > self.STOP_NEAR_RAD):
                continue
            st = (self.link.call({"op": "state"}, timeout=2.0).get("arms") or {}).get(arm.side) or {}
            p, qt = st.get("p"), st.get("q")
            if not p or not qt:
                continue
            t0 = time.monotonic()
            while time.monotonic() - t0 < 1.8:
                k = min(1.0, (time.monotonic() - t0) / 1.2)
                k = k * k * (3.0 - 2.0 * k)
                self.link.call({"op": "target", "arm": arm.side,
                                "p": [p[0], p[1], p[2] + 0.05 * k], "q": qt}, timeout=1.0)
                time.sleep(0.04)
            self.link.call({"op": "hold", "arm": arm.side}, timeout=1.0)
            lifted.append(arm.name)
        if lifted:
            time.sleep(0.6)
            self._poll_once()
        return lifted

    def straighten_wrists(self, speed=0.5):
        """Turn each claw back in line with its forearm: wrist joints to zero,
        the first three joints left exactly where they are, so the arm does
        not travel and only the claw turns. -> what happened, in words

        dimOS is driven by position alone here (asked to hold an orientation
        as well, its solver found no answer at all), and its posture term only
        says "stay as you are": so a wrist keeps whatever bend the last run
        left in it, and the sponge meets the person at that angle. Straight,
        and coming at a limb from the side, the sponge's flat face lands
        square on it."""
        self._poll_once()
        lifted = self._off_the_stops()
        goal, bent = {}, []
        for arm in self.arms.values():
            q = arm.real_joints6()
            if not q:
                return "no joint reading yet: wrists left as they are"
            goal[arm.side] = [float(q[0]), float(q[1]), float(q[2]), 0.0, 0.0, 0.0]
            if max(abs(float(v)) for v in q[3:6]) > self.WRIST_STRAIGHT_RAD:
                bent.append(arm)
        if not bent:
            return "wrists already straight"
        rep = self.link.call({"op": "home", "speed": float(speed), "joints": goal,
                              "close": True}, timeout=30.0)
        if not rep.get("ok"):
            return f"could not straighten the wrists ({rep.get('error')}): left as they are"
        for arm in bent:
            self._wait_joints(arm, goal[arm.side], 12.0)
        return "wrists straightened: " + ", ".join(a.name for a in bent)

    def _poll(self):
        period = 1.0 / STATE_HZ
        n = 0
        while self._running:
            t0 = time.monotonic()
            self._poll_once()
            n += 1
            if n % int(CLAWS_EVERY_S * STATE_HZ) == 0:
                self.close_claws()         # again: a bridge that restarted opens them
            time.sleep(max(0.0, period - (time.monotonic() - t0)))

    # --- arm_hw.Hardware's surface ------------------------------------------
    def place(self, layout):
        """The rig is known: where each arm's base is. Any spacing, either
        facing (see the module docstring); it only says what it sees."""
        if len(layout) < 2:
            raise ArmError("the OpenYAM rig needs two arms: a0 on --left-can-port, a1 on --right-can-port")
        T_left, T_right = (np.asarray(layout[0], float), np.asarray(layout[1], float))
        span = float(np.linalg.norm(T_left[:3, 3] - T_right[:3, 3]))
        same_way = np.allclose(T_left[:3, :3], T_right[:3, :3], atol=0.05)
        if abs(span - 620.0) > 5.0 or not same_way:
            print(f"  the rig puts the arm bases {span:.0f} mm apart"
                  + ("" if same_way else ", facing different ways")
                  + " (dimOS's own plate is 620 mm, both forward): each arm is driven "
                  "in its own base frame, so this is fine; the governor keeps the two "
                  "real arms apart, since dimOS cannot see where the other one is", flush=True)
        T_dimos = T_left.copy()
        T_dimos[:3, 3] = 0.5 * (T_left[:3, 3] + T_right[:3, 3])
        for a, arm in self.arms.items():
            arm.place(layout[a], T_dimos)

    def measured(self):
        """{rig index: (equivalent joints, world tool point)}: where the arms
        are, so the simulated arms begin there too.

        No start pose first, unless SCRUB3D_PARK=1 asks for one. It was there
        because a live view starting from a folded arm found no first path;
        the pump now starts an arm from the nearest pose the model has, so it
        does. And a canned move before anything is drawn in the view was the
        part of every run that nobody watching could make sense of, and twice
        the part that ran into the person's leg."""
        if os.environ.get("SCRUB3D_PARK") == "1" and not getattr(self, "_parked", False):
            self._parked = True
            rep = self.home(speed=0.2)
            print("  the arms went to their start pose, claws closed" if rep.get("ok")
                  else f"  could not park the arms first: {rep.get('error')}", flush=True)
        self._poll_once()
        out = {}
        for a, arm in self.arms.items():
            p = arm.actual()
            j = None if p is None else _ik_measured(p)
            if j is None:
                where = "unknown" if p is None else np.round(p, 0).tolist()
                raise ArmError(f"{arm.name} reports a tool point scrub3d's OpenYAM model "
                               f"cannot solve: {where} mm in its base frame")
            out[a] = (j, arm.actual_world())
        return out

    def start(self, park=True):
        """Start streaming. First, as arm_hw does at start, put every arm at
        its start pose: dimOS's home, through its planner (checked, paced).
        A live view that begins from wherever the arms were left can find
        no first path from a folded pose and then never moves at all."""
        self._running = True
        self._state_thread = threading.Thread(target=self._poll, daemon=True)
        self._state_thread.start()
        end = time.monotonic() + 3.0
        while time.monotonic() < end and any(arm.link.fb_time is None for arm in self.arms.values()):
            time.sleep(0.02)
        self._poll_once()
        park = park and os.environ.get("SCRUB3D_PARK") == "1"     # see measured()
        print("  claws shut" if self.close_claws() else "  could NOT shut the claws", flush=True)
        if os.environ.get("SCRUB3D_STRAIGHT_WRISTS", "1") != "0":
            print("  " + self.straighten_wrists(), flush=True)
        if not park:
            print("  the arms start from where they are: no start pose first", flush=True)
        if park and not getattr(self, "_parked", False) and self._tucked():
            self._parked = True
            print("  the arms are already folded in by their bases: starting from there, "
                  "no swing with somebody in the chair", flush=True)
        elif park and not getattr(self, "_parked", False) and self._away_stages() is not None:
            self._parked = True
            rep = self.home(speed=0.2)
            print("  the arms turned away from the person, claws closed" if rep.get("ok")
                  else f"  could not park the arms: {rep.get('error')}; driving from where "
                       f"they are", flush=True)
            self._poll_once()
        elif park and not getattr(self, "_parked", False):
            print("  the arms stay where they are until the chair is found: only then is "
                  "it known which way is away from the person", flush=True)
        for arm in self.arms.values():
            arm.start()

    def joints(self):
        return {a: arm.measured_joints() for a, arm in self.arms.items()}

    HOLD_COOLOFF_S = 1.0       # a hold stands this long before the arms may go again

    def update(self, points, stop=False, on_skin=None, normals=None):
        if stop:
            self.hold_all("the live view stopped the arms")
            return self.reason
        if self.reason is not None:
            # A hold is a hold, not a latch. The live view stops the arms for
            # as long as it can see a reason and asks again the next frame;
            # holding for good left it planning away while the real arms stood
            # dead, which from the chair looked like "it stopped for no reason
            # and never scrubbed again". Only a faulted stack sticks.
            if self.sticky or time.monotonic() < self.hold_until:
                return self.reason
            self.reason = None
            for arm in self.arms.values():
                arm.resume()
        for a, arm in self.arms.items():
            arm.on_skin = bool((on_skin or {}).get(a, False))
            if time.monotonic() < getattr(arm, "backing_off_until", 0.0):
                continue                                 # drawing back: see back_off()
            if points.get(a) is not None:
                arm.follow(points[a], None if normals is None else normals.get(a))
        for arm in self.arms.values():
            why = arm.fault()
            if why:
                if "met something" in why or "met them" in why:
                    self.hold_all(why, keep=arm)
                    with arm.lock:
                        pushed_to = arm.sent
                    arm.retreat(wait=False, away_from=pushed_to)
                else:
                    self.hold_all(why, sticky=arm.fault_reason is not None)
                break
        return self.reason

    BACK_OFF_MM = 60.0
    BACK_OFF_EACH_S = 0.8      # how long a drawing-back arm ignores its plan

    def back_off(self, why, index=None):
        """A real arm reads deeper into the person than its plan: THAT arm
        draws back toward its own shoulder and then takes up its plan again
        from wherever the plan has got to. The other arm carries on.

        Not held where it is, and not both. An arm held at the spot where it
        read too deep goes on reading too deep, so that hold could never end:
        the plan carried on down the person's arm, pass after pass, while the
        real claw stood at one point for the rest of the run (held 61% of it,
        15 s at a stretch). And the hold took the other arm with it, which at
        the time read 43 mm clear."""
        arm = self.arms.get(index) if index is not None else None
        if arm is None:
            return self.hold_all(why)
        now = time.monotonic()
        if now < getattr(arm, "backing_off_until", 0.0):
            return None                                  # already on its way back
        arm.backing_off_until = now + self.BACK_OFF_EACH_S
        arm.held = False
        arm.retreat(mm=self.BACK_OFF_MM, wait=False)
        if now - getattr(self, "_told_back_off", 0.0) > 3.0:
            self._told_back_off = now
            print(f"  {why}: drawing back {self.BACK_OFF_MM:.0f} mm and going on",
                  flush=True)
        return None

    def hold_all(self, why, keep=None, sticky=False):
        if self.reason is None:
            self.reason = why
            self.hold_until = time.monotonic() + self.HOLD_COOLOFF_S
            self.sticky = bool(sticky)
        for arm in self.arms.values():
            if arm is not keep:
                arm.hold()

    # --- beyond arm_hw: a planned park -------------------------------------
    # The start pose, in joints: turned OUTWARD (away from the person), upper
    # arm vertical, forearm folded down; the tool ends about 33 cm outboard of
    # the base at plank height. dimOS's own home points the tool 34 cm forward
    # at chest height, which with the bases beside the hips is right in front
    # of the person: that is what the first real run did, and why this exists.
    PARK_SHOULDER_RAD = 1.57
    PARK_ELBOW_RAD = 0.6
    TURN_ELBOW_RAD = 0.9       # while turning: the tool 14 cm above the plank
    YAW_MARGIN_RAD = 0.15      # dimOS refuses a goal that sits on a joint stop
    STAGE_REACHED_RAD = 0.12   # and refuses a plan of no length

    TUCK_R_MM = 340.0          # parked already: the tool this near its own base
    TUCK_Z_MM = 260.0          # and this low is folded in, clear of a seated person

    def _tucked(self):
        """True when both arms are already folded in against their own bases.
        Then the start pose is not worth a swing: the arms are where the park
        would put them, and somebody may already be sitting in the chair."""
        for arm in self.arms.values():
            p = arm.actual()
            if p is None or math.hypot(p[0], p[1]) > self.TUCK_R_MM or p[2] > self.TUCK_Z_MM:
                return False
        return True

    def _away_stages(self):
        """The start pose as a staged plan, or None before the rig is placed:
        fold up where the arm is, turn to the person's FRONT, turn on to
        point away from the other arm, settle. -> [{side: [6]}, ...]

        Through the front on purpose. The bases here face each other, so an
        arm must turn half a circle to point away from the person, and the
        planner knows nothing of the backrest or the push handles behind
        the plank: the front, over the footrests, is the side that is clear
        when nobody sits in the chair."""
        arms = list(self.arms.values())
        if len(arms) != 2 or any(arm.T is None for arm in arms):
            return None
        centre = 0.5 * (arms[0].T[:3, 3] + arms[1].T[:3, 3])
        fold, front, away, park = {}, {}, {}, {}
        for arm in arms:
            R = arm.Tinv[:3, :3]
            f = R @ np.array([1.0, 0.0, 0.0])                    # the person's front, arm frame
            o = R @ (arm.T[:3, 3] - centre)                      # away from the other arm
            yaw_f, yaw_o = math.atan2(f[1], f[0]), math.atan2(o[1], o[0])
            # The same way round as the front, so the swing passes it.
            if yaw_f >= 0.0 and yaw_o < yaw_f - 1e-6:
                yaw_o += 2.0 * math.pi
            elif yaw_f < 0.0 and yaw_o > yaw_f + 1e-6:
                yaw_o -= 2.0 * math.pi
            # Clear of the limit, not at it: dimOS calls a goal exactly on a
            # joint stop an invalid configuration and plans nothing.
            yaw_o = min(max(yaw_o, K.BASE_MIN_RAD + self.YAW_MARGIN_RAD),
                        K.BASE_MAX_RAD - self.YAW_MARGIN_RAD)
            q = arm.real_joints6()
            now = float(q[0]) if q else yaw_f
            fold[arm.side] = [now, self.PARK_SHOULDER_RAD, self.TURN_ELBOW_RAD, 0.0, 0.0, 0.0]
            front[arm.side] = [yaw_f, self.PARK_SHOULDER_RAD, self.TURN_ELBOW_RAD, 0.0, 0.0, 0.0]
            away[arm.side] = [yaw_o, self.PARK_SHOULDER_RAD, self.TURN_ELBOW_RAD, 0.0, 0.0, 0.0]
            park[arm.side] = [yaw_o, self.PARK_SHOULDER_RAD, self.PARK_ELBOW_RAD, 0.0, 0.0, 0.0]
        # The start pose is the FRONT one: folded, pointing the way the person
        # faces, the claw 41 cm ahead of its base over the footrests, clear of
        # the torso. Not turned on outward, though that is further from them:
        # from out there the straight line to the person's chest runs through
        # the arm's own base, where the three-joint model has no solution, and
        # a live view that starts there never gets its first stroke. The two
        # outward stages stay below for whoever wants them by name.
        return [fold, front] if not self.PARK_OUTWARD else [fold, front, away, park]

    PARK_OUTWARD = False

    def _away_joints(self):
        stages = self._away_stages()
        return None if stages is None else stages[-1]

    def home(self, speed=0.2, away=True, close=True):
        """Both arms to the start pose through dimOS's planner (checked and
        paced), not the stream: turned away from the person once the rig is
        placed, dimOS's own home before that. Closes the claws. Stops
        streaming first. -> the bridge's reply"""
        for arm in self.arms.values():
            with arm.lock:
                arm.target = arm.sent = None
                arm.fed = arm.fed_at = None
                arm.recent.clear()
        if not away:                                   # dimOS's own home: asked for by name
            return self.link.call({"op": "home", "speed": float(speed),
                                   "close": bool(close)}, timeout=30.0)
        self._poll_once()
        placed = self._away_stages()
        if placed is None:
            # NEVER dimOS's home as a fallback: it points the tool along the
            # base's own +x, which on this rig is straight at the person.
            return {"ok": False, "error": "the rig is not placed yet, so which way is away "
                                          "from the person is not known: not moving"}
        # One arm at a time, the other pinned where it stands. The bases face
        # each other, so both arms swinging together can meet over the middle
        # of the plank, and dimOS plans each arm without any idea where the
        # other one is.
        rep, missed = {"ok": True}, []
        for one in list(self.arms.values()):
            side = one.side
            stages = self._stages_for(one, placed)
            for i, goal in enumerate(stages):
                now = one.real_joints6()
                if now and max(abs(a - b) for a, b in zip(now, goal)) < self.STAGE_REACHED_RAD:
                    continue                       # dimOS refuses a plan of no length
                joints = {side: goal}
                for other in self.arms.values():
                    q = other.real_joints6()
                    if other.side != side and q:
                        joints[other.side] = list(q)   # pinned: sent its own joints
                plan = self.link.call({"op": "home", "speed": float(speed), "joints": joints,
                                       "close": bool(close)}, timeout=30.0)
                off = self._wait_joints(one, goal, 45.0) if plan.get("ok") else None
                if off is not None and off < 2.0 * self.STAGE_REACHED_RAD:
                    continue
                # The planner would not, or did not get there. A stage that
                # only turns the arm can be streamed instead, and that is the
                # stage dimOS always refuses: it plans against a model with
                # the arms bolted to a box and calls the turn away from the
                # person a collision with that box.
                if now and self._only_yaw(now, goal) and self._stream_yaw(one, goal[0]):
                    continue
                why = plan.get("error") if not plan.get("ok") else (
                    "stopped short" if off is None else f"stopped {off:.2f} rad short")
                missed.append(f"{side} stage {i + 1} of {len(stages)}: {why}")
                break                              # this arm stays put; try the other
        if missed:
            # Never all-or-nothing: an arm that cannot be turned away is a
            # reason to say so, not a reason to leave the other one pointing
            # at the person.
            return {"ok": False, "error": "; ".join(missed)}
        return rep

    # Drawn back: the claw 22 cm from its own base and 21 cm above the plank,
    # the elbow out the far side. About as small as this arm gets, and close to
    # where it lies when the torque is off.
    COMPACT_SHOULDER_RAD = 0.3
    COMPACT_ELBOW_RAD = 0.2
    FRONT_NEAR_RAD = 0.35      # already pointing this near the front: just raise it

    def _stages_for(self, one, placed):
        """This arm's way to the start pose, from where it is NOW. -> [[6], ...]

        The start pose puts the claw 41 cm out from its base, in whatever
        direction the arm points. A run that ends at the person leaves both
        arms pointing AT them, and the base is 40 cm from their midline, so
        "fold up where it stands" swung the claw straight into their lap:
        that is the park that went into somebody's leg. Never extend before
        turning. Pointing anywhere but the front, the claw is first drawn BACK
        to its own base, then turned to the front while it is tucked in, and
        only there raised and let out."""
        front = placed[1][one.side]                # [yaw_f, shoulder, elbow, 0, 0, 0]
        now = one.real_joints6()
        if now is None or abs(float(now[0]) - front[0]) <= self.FRONT_NEAR_RAD:
            return [front]
        c = [self.COMPACT_SHOULDER_RAD, self.COMPACT_ELBOW_RAD, 0.0, 0.0, 0.0]
        return [[float(now[0])] + c, [front[0]] + c, front]

    @staticmethod
    def _only_yaw(now, goal, tol=0.08):
        """True when two poses differ in the base joint alone."""
        return all(abs(a - b) < tol for a, b in zip(now[1:], goal[1:]))

    ARC_RAD_S = 0.25           # how fast a streamed turn goes round the base

    def _stream_yaw(self, one, yaw_goal):
        """Turn one arm about its own base by streaming. -> True if it arrived.

        The tool rides an arc at whatever radius it is already at and the claw
        turns with it, so the wrist joints hold still and nothing is asked to
        go near a joint stop."""
        p0, q0, now = one.actual(), one.quat(), one.real_joints6()
        if p0 is None or q0 is None or not now:
            return False
        r, th0 = math.hypot(p0[0], p0[1]), math.atan2(p0[1], p0[0])
        delta = float(yaw_goal) - float(now[0])
        span = abs(delta) / self.ARC_RAD_S
        t0 = time.monotonic()
        while True:
            t = time.monotonic() - t0
            k = min(1.0, t / span) if span > 1e-3 else 1.0
            k = k * k * (3.0 - 2.0 * k)                       # ease in and out
            a = delta * k
            p_arm = np.array([r * math.cos(th0 + a), r * math.sin(th0 + a), p0[2]])
            p_dimos = p_arm / 1000.0 + SIDE_OFFSET_M[one.side]
            q = _qmul([0.0, 0.0, math.sin(a / 2.0), math.cos(a / 2.0)], q0)
            if not self.link.call({"op": "target", "arm": one.side,
                                   "p": [float(v) for v in p_dimos],
                                   "q": [float(v) for v in q]}, timeout=1.0).get("ok"):
                return False
            if t > span + 1.0:
                break
            time.sleep(0.04)
        self.link.call({"op": "hold", "arm": one.side}, timeout=2.0)
        time.sleep(0.8)
        self._poll_once()
        q6 = one.real_joints6()
        return bool(q6) and abs(q6[0] - float(yaw_goal)) < 0.25

    def _wait_joints(self, one, goal, timeout=45.0):
        """Until this arm's joints really are at goal. -> how far off, or None.

        On the joints AND the execution status, because each alone lies. dimOS
        leaves the last plan's COMPLETED standing while the next one is still
        being accepted, so the status alone comes back before the arm has
        moved. And the joints come within reach of the goal about half a
        second before the trajectory ends, so the joints alone come back while
        dimOS still says EXECUTING, and it refuses the next plan outright
        ("Cannot plan in current state"): that is what every "Planning failed"
        on stage 2 turned out to be."""
        end = time.monotonic() + timeout
        off = None
        while time.monotonic() < end:
            rep = self.link.call({"op": "state"}, timeout=2.0)
            if rep.get("ok"):
                t = time.monotonic()
                for arm in self.arms.values():
                    arm._take_state(rep.get("arms", {}).get(arm.side), t)
            now = one.real_joints6()
            if now:
                off = max(abs(a - b) for a, b in zip(now, goal))
                if (off < self.STAGE_REACHED_RAD
                        and rep.get("exec") not in ("EXECUTING", "ACCEPTED")):
                    return off
            time.sleep(0.2)
        return off

    def wait_idle(self, timeout=30.0):
        """Until dimOS reports no execution in progress. -> the last exec status"""
        end = time.monotonic() + timeout
        st = None
        while time.monotonic() < end:
            st = self.link.call({"op": "state"}, timeout=2.0).get("exec")
            if st not in ("EXECUTING", "ACCEPTED"):
                break
            time.sleep(0.1)
        return st

    def close(self):
        missed = []
        if self.reason is None:
            goals = {}
            for arm in self.arms.values():
                goal = arm.retreat(wait=False)
                if goal is not None:
                    goals[arm] = goal
            end = time.monotonic() + BACK_OFF_S
            for arm, goal in goals.items():
                arm.wait_near(goal, timeout=max(0.0, end - time.monotonic()))
        for arm in self.arms.values():
            arm.held = arm.held or self.reason is not None
            if not arm.hold():
                missed.append(arm.name)
            arm.running = False
        self._running = False
        for arm in self.arms.values():
            if arm.thread is not None:
                arm.thread.join(timeout=0.5)
        self.link.close()
        if self.drive_log is not None:
            self.drive_log.close()
        return missed


# --- see it work, with no live view --------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser(description="connect to the dimOS bridge and read both arms")
    ap.add_argument("--endpoint", default=None, help=f"host:port (default ${ENV} or {DEFAULT_ENDPOINT})")
    ap.add_argument("--nudge-mm", type=float, default=0.0,
                    help="ALSO move each tool this far straight up and back (moves the arms)")
    ap.add_argument("--home", action="store_true",
                    help="ALSO send both arms to dimOS's home pose through its planner")
    a = ap.parse_args()
    if os.environ.get("SCRUB3D_ARM", "").lower() != "openyam":
        print("  note: SCRUB3D_ARM is not 'openyam'; kinematics is the RoArm's", flush=True)
    hw = Hardware.connect(a.endpoint)
    # A plate 250 mm behind the seat, 700 mm up, facing the way the person does.
    c, s = math.cos(0.0), math.sin(0.0)
    T_plate = np.eye(4)
    T_plate[:3, :3] = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    T_plate[:3, 3] = (-250.0, 0.0, 700.0)
    layout = []
    for s in SIDES:
        T = T_plate.copy()
        T[:3, 3] = T_plate[:3, 3] + T_plate[:3, :3] @ (SIDE_OFFSET_M[s] * 1000.0)
        layout.append(T)
    hw.place(layout)
    for k, (j, w) in hw.measured().items():
        print(f"  arm {k} ({hw.arms[k].name}): tool at world {np.round(w, 0).tolist()} mm, "
              f"equivalent joints {np.round(j, 3).tolist()}")
    if a.nudge_mm > 0:
        hw.start()
        for k, arm in hw.arms.items():
            print(f"  {arm.name}: real joints {np.round(arm.real_joints6() or [], 3).tolist()}")
        w0 = {k: hw.arms[k].actual_world() for k in hw.arms}
        goals = {k: w0[k] + np.array([0.0, 0.0, a.nudge_mm]) for k in w0}
        t_end = time.monotonic() + 4.0
        while time.monotonic() < t_end:
            why = hw.update(goals)
            if why:
                print("  stopped:", why); break
            time.sleep(0.05)
        for k in hw.arms:
            print(f"  {hw.arms[k].name}: moved {np.linalg.norm(hw.arms[k].actual_world() - w0[k]):.1f} mm, "
                  f"{np.linalg.norm(hw.arms[k].actual_world() - goals[k]):.1f} mm from the goal")
        st = hw.link.call({"op": "state"})
        print(f"  dimOS: exec {st.get('exec')}, error {st.get('error')}, "
              f"{st.get('n_targets')} targets sent")
        t_end = time.monotonic() + 4.0
        while time.monotonic() < t_end:
            hw.update(w0)
            time.sleep(0.05)
        for k in hw.arms:
            print(f"  {hw.arms[k].name}: back within {np.linalg.norm(hw.arms[k].actual_world() - w0[k]):.1f} mm")
    if a.home:
        if not hw._running:
            hw.start()
        rep = hw.home(speed=0.2, away=False)
        print(f"  home: {rep}")
        print(f"  home: finished with exec {hw.wait_idle(30.0)}")
        for k, arm in hw.arms.items():
            print(f"  {arm.name}: real joints {np.round(arm.real_joints6() or [], 3).tolist()}")
    missed = hw.close()
    print("  closed" + (f"; hold missed on {missed}" if missed else ""))


if __name__ == "__main__":
    main()
