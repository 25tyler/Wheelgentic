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
MAX_STEP_MM = 6.0           # per command: 240 mm/s, the standards' "reduced speed";
                            # dimOS caps every joint at 1 rad/s underneath anyway
MIN_FEED_V = 40.0           # mm/s: the slowest a fed point is paced at
PACE_OVER = 1.15            # keep up with the feed, a little faster than it comes
WATCHDOG_S = 0.5            # no new point this long: stop streaming, the arm holds
STALE_S = 1.0               # no state from the bridge this long: not answering
TRACK_MM = 80.0             # this far from where it was sent lately ...
TRACK_S = 0.8               # ... for this long: something holds it back
TRACK_WINDOW_S = 0.5
RETREAT_MM = 50.0
BACK_OFF_S = 4.0
STATE_HZ = 20.0

SIDES = ("left", "right")
SIDE_OFFSET_M = {"left": np.array([0.0, 0.31, 0.0]),
                 "right": np.array([0.0, -0.31, 0.0])}
NAMES = {"left": "left", "right": "right"}


class ArmError(RuntimeError):
    pass


def _ik_measured(p):
    """Joints for a REPORTED tool point: the nearest legal ones when the
    arm model offers that (the OpenYAM, whose wrist can fold), else ik()."""
    f = getattr(K, "ik_nearest", None)
    return (f or K.ik)(*p)


# --- the wire ---------------------------------------------------------------------

class Link:
    """One line-based TCP connection to dimos_bridge_server.py, shared by both
    arms. Replies are matched by order; one request at a time."""

    def __init__(self, endpoint):
        host, _, port = endpoint.rpartition(":")
        self.host, self.tcp_port = host or "127.0.0.1", int(port or 7790)
        self.port_name = f"dimos@{self.host}:{self.tcp_port}"   # arm_hw prints link.port
        self.lock = threading.Lock()
        self.sock = socket.create_connection((self.host, self.tcp_port), timeout=5.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.f = self.sock.makefile("rwb", buffering=0)
        self.failed = 0
        self.fb_time = None
        self.feedback = {}             # arm_hw's DriveLog reads link.feedback

    @property
    def port(self):
        return self.port_name

    def call(self, req, timeout=2.0):
        with self.lock:
            try:
                self.sock.settimeout(timeout)
                self.f.write((json.dumps(req) + "\n").encode())
                raw = self.f.readline()
                if not raw:
                    raise ConnectionError("bridge closed the connection")
                rep = json.loads(raw)
                self.failed = 0
                return rep
            except (OSError, ValueError) as exc:
                self.failed += 1
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def close(self):
        try:
            self.call({"op": "bye"}, timeout=1.0)
        except Exception:                                    # noqa: BLE001
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# --- one arm -----------------------------------------------------------------------

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
        x_hint = R @ np.array([0.0, 0.0, 1.0])       # keep "up" as the tool's x
        return _quat_from_axes(z_dimos, x_hint)

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
                sent = tuple(float(v) for v in here)
            if (fed_at is not None and now - fed_at > WATCHDOG_S
                    and now > self.backing_until and not self.held):
                continue                                  # the live view stalled: stop
            d = math.dist(tgt, sent)
            if d < 0.2 and self.held:
                continue
            pace = min(MAX_STEP_MM, max(v_fed * PACE_OVER, MIN_FEED_V) * period)
            k = min(1.0, pace / d) if d > 1e-9 else 1.0
            step = tuple(s + (g - s) * k for s, g in zip(sent, tgt))
            if K.ik(*step) is None:
                continue                                  # hold rather than guess
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

    def hold(self):
        """Stop streaming: dimOS holds the arm 0.5 s after the last pose."""
        self.held = True
        with self.lock:
            self.target = self.sent
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

    def __init__(self, link, arms, mock):
        self.link = link
        self.arms = arms                        # {rig index: DimosArm}
        self.mock = mock
        self.reason = None
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
        err = rep.get("error")
        if err and "state read failed" not in str(err):
            for arm in self.arms.values():
                arm.fault_reason = f"dimOS: {err}"
        return True

    def _poll(self):
        period = 1.0 / STATE_HZ
        while self._running:
            t0 = time.monotonic()
            self._poll_once()
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
        """{rig index: (equivalent joints, world tool point)} -- after parking
        the arms at their start pose, so the simulated arms begin there too."""
        if not getattr(self, "_parked", False):
            self._parked = True
            rep = self.home(speed=0.2)
            print("  the arms turned away from the person, claws closed" if rep.get("ok")
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
        if park and not getattr(self, "_parked", False) and self._away_stages() is not None:
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

    def update(self, points, stop=False, on_skin=None, normals=None):
        if self.reason is not None:
            return self.reason
        if stop:
            self.hold_all("the live view stopped the arms")
            return self.reason
        for a, arm in self.arms.items():
            arm.on_skin = bool((on_skin or {}).get(a, False))
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
                    self.hold_all(why)
                break
        return self.reason

    def hold_all(self, why, keep=None):
        if self.reason is None:
            self.reason = why
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
            yaw_o = min(max(yaw_o, K.BASE_MIN_RAD), K.BASE_MAX_RAD)
            q = arm.real_joints6()
            now = float(q[0]) if q else yaw_f
            fold[arm.side] = [now, self.PARK_SHOULDER_RAD, self.TURN_ELBOW_RAD, 0.0, 0.0, 0.0]
            front[arm.side] = [yaw_f, self.PARK_SHOULDER_RAD, self.TURN_ELBOW_RAD, 0.0, 0.0, 0.0]
            away[arm.side] = [yaw_o, self.PARK_SHOULDER_RAD, self.TURN_ELBOW_RAD, 0.0, 0.0, 0.0]
            park[arm.side] = [yaw_o, self.PARK_SHOULDER_RAD, self.PARK_ELBOW_RAD, 0.0, 0.0, 0.0]
        return [fold, front, away, park]

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
        stages = self._away_stages()
        if stages is None:
            # NEVER dimOS's home as a fallback: it points the tool along the
            # base's own +x, which on this rig is straight at the person.
            return {"ok": False, "error": "the rig is not placed yet, so which way is away "
                                          "from the person is not known: not moving"}
        rep = {"ok": False, "error": "no stage ran"}
        for i, joints in enumerate(stages):
            rep = self.link.call({"op": "home", "speed": float(speed), "joints": joints,
                                  "close": bool(close)}, timeout=30.0)
            if not rep.get("ok"):
                rep["error"] = f"stage {i + 1} of {len(stages)}: {rep.get('error')}"
                return rep
            st = self.wait_idle(45.0)
            if st not in ("COMPLETED", "IDLE"):
                return {"ok": False, "error": f"stage {i + 1} of {len(stages)} ended {st}"}
        return rep

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
