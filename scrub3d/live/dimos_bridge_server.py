"""scrub3d/live/dimos_bridge_server.py -- the OpenYAM arms, through dimOS.

    # on the Spark, in the dimos venv:
    python scrub3d/live/dimos_bridge_server.py                       # mock arms
    python scrub3d/live/dimos_bridge_server.py --left-can-port can0 \
                                               --right-can-port can1   # real arms
    python scrub3d/live/dimos_bridge_server.py --attach               # a stack
                                               # someone else started FROM
                                               # build_blueprint() below

WHY THIS EXISTS
---------------
The live view (live_body.py) drives arms through one interface, arm_hw's
`Hardware`, written for the RoArm-M2-S over a serial port. The OpenYAM has no
serial port and no firmware IK: it is six Damiao motors on a CAN bus, and the
thing that knows how to move them is dimOS (github.com/dimensionalOS/dimos),
which runs as its own process, with its own Python environment, on the
machine the CAN adapters are plugged into.

So the bridge has two halves. THIS half runs beside dimOS and is the only
code that imports it. It starts (or attaches to) the dual-arm control stack
and turns a tiny line-based protocol into the two things the arms need:
a stream of tool poses, and a read-back of where the tools actually are.
The other half, arm_dimos.py, speaks that protocol and looks exactly like
arm_hw.Hardware to the live view, so the live view can run on whatever
machine has the camera and the depth guard's libraries, and never imports
dimOS at all.

WHAT DIMOS DOES WITH A POSE
---------------------------
Each arm has a `cartesian_ik` task listening on `/left_cartesian_command`
(and right). Every pose we publish is an ABSOLUTE target for the arm's
grasp frame, in dimOS's world frame (metres; the dual base plate's frame:
+x forward out of the bracket, +y to the left arm, +z up). Pink IK follows
it at 100 Hz with a joint-velocity cap. If no new pose arrives for 0.5 s the
task times out and the arm holds where it is -- which is the watchdog the
live view relies on: a stalled live view is a stopped arm.

THE PROTOCOL, one JSON object per line, over TCP
------------------------------------------------
  -> {"op": "info"}
  <- {"ok": true, "mock": bool, "arms": ["left", "right"], "frame": "world"}
  -> {"op": "target", "arm": "left", "p": [x, y, z], "q": [x, y, z, w]}
  <- {"ok": true}                    metres, XYZW quaternion, dimOS world
  -> {"op": "state"}
  <- {"ok": true, "t": ..., "exec": "IDLE", "error": null,
      "arms": {"left": {"p": [..], "q": [..], "joints": [..6], "age": s}, ..}}
  -> {"op": "hold", "arm": "left"}     stop streaming: the task times out
  <- {"ok": true}                      and the arm holds in 0.5 s
  -> {"op": "cancel"}                  cancel any planned trajectory too
  -> {"op": "gripper", "arm": "left", "pos": 0.0}   0 closed .. 1 open
  -> {"op": "home", "speed": 0.2, "joints": {"left": [..6], "right": [..6]},
      "close": true}                   both arms to those joints (or the model's
  <- {"ok": true, "plan": ..}          home) through the planner; close = claws shut
  -> {"op": "bye"}

"hold" is deliberately just "stop sending": there is no command that could
race with a stream of targets still in flight, and the timeout is the same
mechanism that protects against a dead client.
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback

BRIDGE_PORT = 7790
STATE_HZ = 20.0


# --- the dimOS stack ----------------------------------------------------------

def build_blueprint(left_can_port=None, right_can_port=None, viser=True,
                    max_joint_velocity_rad_s=1.0, orientation_cost=0.0):
    """The dual OpenYAM stack: coordinator with a cartesian target task per arm,
    a gripper task per arm, the trajectory task the planner uses, and the
    manipulation module (IK, planning, the Viser view). Mock hardware when no
    CAN ports are given, exactly as dimOS's own blueprints do."""
    from dimos.control.coordinator import TaskConfig
    from dimos.core.coordination.blueprints import autoconnect
    from dimos.manipulation.manipulation_module import ManipulationModule
    from dimos.manipulation.planning.kinematics.config import PinkKinematicsConfig
    from dimos.manipulation.visualization.viser.config import ViserVisualizationConfig
    from dimos.robot.manipulators.dual_openyam.blueprints.basic import (
        DualOpenYamCoordinator, dual_openyam_trajectory_task)
    from dimos.robot.manipulators.dual_openyam.config import dual_openyam_model_config
    from dimos.robot.manipulators.dual_openyam.joints import (
        DUAL_OPENYAM_ARM_JOINTS, DUAL_OPENYAM_GRIPPER_JOINTS)

    model = dual_openyam_model_config()
    # joint_limit_posture_margin 0.5 (dimOS's teleop uses 0.3): the wrist was
    # being driven to its limit, sagging past it under load, and tripping the
    # adapter's feedback fault, which drops torque and ends the session.
    # orientation_cost 0: position only, and not for want of trying. At 2 the
    # claw held its WORLD orientation through a quarter turn of the base by
    # making the wrist do all the turning, and it ran to its stop within a
    # minute (-1.66 of -1.69 rad). At 0.6, with the joint limit margin at 0.5,
    # the QP had no feasible solution at all and the left arm took no commands
    # for a whole run. So the claw is not aimed: the sponge goes where it is
    # sent and the wrist stays near where the park left it, which means the
    # claw can sit pointing back at the camera. Aiming it wants room at the
    # limits that this arm, on this plank, does not have.
    # --orientation-cost aims the claw (0, the default, leaves it unaimed). It
    # is worth trying again now for a reason: when it froze an arm the wrists
    # were bent however the last run left them and the targets were on the
    # front and the back of the limb, which a claw reaching in from the side
    # cannot face. The live view now sends only the limb's outer side, from
    # straight wrists, and turns its request toward the skin a little at a time.
    pink = PinkKinematicsConfig(dt=0.01, position_cost=8.0,
                                orientation_cost=float(orientation_cost),
                                posture_cost=0.05, joint_limit_posture_margin=0.5,
                                lm_damping=0.01, gain=1.0)
    tasks = []
    for side, gripper_joint in zip(("left", "right"), DUAL_OPENYAM_GRIPPER_JOINTS):
        joints = [j for j in DUAL_OPENYAM_ARM_JOINTS if j.startswith(side + "_")]
        tasks.append(TaskConfig(
            name=f"cartesian_ik_{side}", type="cartesian_ik", joint_names=joints,
            priority=20,
            params={"robot_model": model, "target_frame": f"{side}_grasp_frame",
                    "pink": pink, "timeout": 0.5,
                    "max_joint_velocity_rad_s": float(max_joint_velocity_rad_s),
                    "max_command_tracking_error_deg": 10.0,
                    "joint_command_filter_cutoff_hz": 30.0},
            stream_bind={"cartesian_command": f"{side}_cartesian_command"}))
        tasks.append(TaskConfig(
            name=f"{side}_arm_gripper", type="gripper", joint_names=[gripper_joint],
            priority=20, stream_bind={"gripper_command": f"{side}_gripper_command"}))
    tasks.append(dual_openyam_trajectory_task(priority=10))

    kw = {}
    if viser:
        kw["visualization"] = ViserVisualizationConfig()
    return autoconnect(
        DualOpenYamCoordinator.blueprint(
            instance_name="ControlCoordinator",
            left_can_port=left_can_port, right_can_port=right_can_port,
            tasks=tasks),
        ManipulationModule.blueprint(model=model, kinematics=pink, **kw),
    )


class Stack:
    """dimOS, running in this process or reached over its bus."""

    def __init__(self, attach=False, left_can_port=None, right_can_port=None,
                 viser=True, max_joint_velocity_rad_s=1.0, orientation_cost=0.0):
        from dimos.porcelain.dimos import Dimos
        self.mock = left_can_port is None and right_can_port is None
        if attach:
            self.app = Dimos.connect(timeout=10.0)
            self.mock = None                       # unknown: somebody else's
        else:
            self.app = Dimos(viewer="none")      # no Rerun window for a bridge
            self.app.run(build_blueprint(left_can_port, right_can_port, viser,
                                         max_joint_velocity_rad_s, orientation_cost))
        from dimos.manipulation.manipulation_spec import ManipulationSpec
        self.rpc = self.app.find_module_by_spec(ManipulationSpec)
        self.groups = {}
        for info in self.rpc.list_planning_groups():
            if info.tip_frame is None:
                continue
            side = "left" if info.id.startswith("left") else "right"
            self.groups[side] = info
        if set(self.groups) != {"left", "right"}:
            raise RuntimeError(f"expected a left and a right arm, found "
                               f"{[i.id for i in self.rpc.list_planning_groups()]}")

        from dimos.robot.manipulators.dual_openyam.config import dual_openyam_model_config
        cfg = dual_openyam_model_config()
        self.home_by_name = dict(zip(cfg.joint_names, cfg.home_joints))

        from dimos.core.transport_factory import make_transport
        from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
        from dimos.msgs.std_msgs.Float32 import Float32
        self._PoseStamped = PoseStamped
        self._Float32 = Float32
        self.pose_tx = {s: make_transport(f"/{s}_cartesian_command", PoseStamped)
                        for s in ("left", "right")}
        self.grip_tx = {s: make_transport(f"/{s}_gripper_command", Float32)
                        for s in ("left", "right")}
        for t in list(self.pose_tx.values()) + list(self.grip_tx.values()):
            start = getattr(t, "start", None)
            if callable(start):
                start()

    def send_target(self, side, p, q):
        msg = self._PoseStamped(ts=time.time(), frame_id="world",
                                position=(float(p[0]), float(p[1]), float(p[2])),
                                orientation=(float(q[0]), float(q[1]),
                                             float(q[2]), float(q[3])))
        self.pose_tx[side].broadcast(None, msg)

    def send_gripper(self, side, pos):
        self.grip_tx[side].broadcast(None, self._Float32(float(pos)))

    def cancel(self):
        try:
            r = self.rpc.cancel()
            return getattr(r, "status", r).name if hasattr(getattr(r, "status", r), "name") else str(r)
        except Exception as exc:                            # noqa: BLE001
            return f"cancel failed: {exc}"

    def home(self, speed_scale=0.2, joints=None, close=False):
        """Both arms to a joint pose through the planner (a collision-checked,
        velocity-profiled trajectory, not a stream): `joints` {side: [6]} if
        given, else the model's home. `close` shuts both claws as well.

        dimOS's home points the tool 34 cm forward at chest height. With the
        bases beside a seated person's hips that is right in front of them,
        which is what the first real run did; the live view now sends its
        own pose, turned outward, and only falls back to home unplaced."""
        from dimos.msgs.sensor_msgs.JointState import JointState
        targets = {}
        for side, info in self.groups.items():
            names = list(info.joint_names)
            if joints and side in joints:
                q = [float(v) for v in joints[side]]
                if len(q) != len(names):
                    return {"ok": False, "error": f"{side}: {len(q)} joints given, {len(names)} needed"}
            else:
                q = [float(self.home_by_name[n]) for n in names]
            targets[info.id] = JointState(name=names, position=q)
        plan = self.rpc.plan_to_joints(targets, speed_scale=float(speed_scale))
        if not plan.succeeded or plan.plan is None:
            # A plan that was refused is an answer to a question, not a fault.
            # Left alone, dimOS keeps reporting it as its error, and a live
            # view that reads that as "the arms have faulted" stops them both.
            try:
                self.rpc.reset()
            except Exception:                                # noqa: BLE001
                pass
            return {"ok": False, "error": f"plan {plan.status.name}: {plan.message}"}
        r = self.rpc.execute(blocking=False, plan_id=plan.plan.plan_id)
        if close:
            for side in self.groups:
                self.send_gripper(side, 0.0)
        return {"ok": bool(r.succeeded), "plan": plan.status.name,
                "execute": r.status.name, "message": r.message, "closed": bool(close)}

    def state(self):
        """-> dict for the wire: where every tool is, from dimOS's own state."""
        snap = self.rpc.get_state()
        arms = {}
        for side, info in self.groups.items():
            g = snap.groups.get(info.id)
            if g is None:
                continue
            d = {"p": None, "q": None, "joints": None}
            if g.end_effector_pose is not None:
                pos, ori = g.end_effector_pose.position, g.end_effector_pose.orientation
                d["p"] = [float(pos.x), float(pos.y), float(pos.z)]
                d["q"] = [float(ori.x), float(ori.y), float(ori.z), float(ori.w)]
            if g.joints is not None:
                try:
                    from dimos.manipulation.sdk import joint_state_to_ordered_positions
                    d["joints"] = [float(v) for v in joint_state_to_ordered_positions(
                        g.joints, joint_names=info.joint_names)]
                except Exception:                            # noqa: BLE001
                    d["joints"] = [float(v) for v in g.joints.position]
            arms[side] = d
        return {"t": float(snap.timestamp), "exec": snap.execution_status.name,
                "op": snap.operation_status.name, "error": snap.error, "arms": arms}

    def stop(self):
        try:
            self.app.stop()
        except Exception:                                    # noqa: BLE001
            pass


# --- the wire -------------------------------------------------------------------

class Bridge:
    def __init__(self, host, port):
        """Binds the port at once: two bridges would mean two dimOS stacks
        on one bus answering the same RPC names, which is a mess that looks
        like random failures. The port is the lock."""
        self.stack = None
        self.host, self.port = host, port
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.srv.bind((host, port))
        except OSError as exc:
            raise SystemExit(f"port {port} is taken ({exc}): another bridge is running. "
                             f"Stop it and wait for it to exit before starting this one.")
        self.srv.listen(2)
        self.lock = threading.Lock()
        self.latest = {"t": 0.0, "exec": "UNKNOWN", "op": "UNKNOWN", "error": None,
                       "arms": {}}
        self.latest_at = 0.0
        self.sent = {}                        # side -> (t, p, q) last target
        self.running = True
        self.n_targets = 0

    def attach(self, stack):
        self.stack = stack
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self):
        period = 1.0 / STATE_HZ
        while self.running:
            t0 = time.monotonic()
            try:
                s = self.stack.state()
                with self.lock:
                    self.latest = s
                    self.latest_at = time.time()
            except Exception as exc:                        # noqa: BLE001
                with self.lock:
                    self.latest = dict(self.latest, error=f"state read failed: {exc}")
            time.sleep(max(0.0, period - (time.monotonic() - t0)))

    def handle(self, req):
        op = req.get("op")
        if op == "info":
            return {"ok": True, "mock": self.stack.mock, "arms": sorted(self.stack.groups),
                    "frame": "world", "tips": {s: i.tip_frame for s, i in self.stack.groups.items()}}
        if op == "target":
            side = req["arm"]
            if side not in self.stack.groups:
                return {"ok": False, "error": f"no arm {side!r}"}
            p, q = req["p"], req.get("q") or [0.0, 0.0, 0.0, 1.0]
            if len(p) != 3 or len(q) != 4 or not all(
                    isinstance(v, (int, float)) and v == v for v in list(p) + list(q)):
                return {"ok": False, "error": "target needs p[3] and q[4], finite"}
            self.stack.send_target(side, p, q)
            with self.lock:
                self.sent[side] = (time.time(), list(p), list(q))
                self.n_targets += 1
            return {"ok": True}
        if op == "hold":
            with self.lock:
                self.sent.pop(req.get("arm"), None)
            return {"ok": True, "note": "not streaming: the task times out and holds"}
        if op == "cancel":
            with self.lock:
                self.sent.clear()
            return {"ok": True, "result": self.stack.cancel()}
        if op == "home":
            with self.lock:
                self.sent.clear()
            return self.stack.home(req.get("speed", 0.2), joints=req.get("joints"),
                                   close=bool(req.get("close", False)))
        if op == "gripper":
            self.stack.send_gripper(req["arm"], req.get("pos", 0.0))
            return {"ok": True}
        if op == "state":
            with self.lock:
                s = dict(self.latest)
                s["age"] = time.time() - self.latest_at if self.latest_at else None
                s["sent"] = {k: {"t": v[0], "p": v[1], "q": v[2]} for k, v in self.sent.items()}
                s["n_targets"] = self.n_targets
            s["ok"] = True
            return s
        if op == "bye":
            return {"ok": True, "bye": True}
        return {"ok": False, "error": f"unknown op {op!r}"}

    def serve(self):
        srv = self.srv
        print(f"  bridge listening on {self.host}:{self.port} "
              f"({'MOCK arms' if self.stack.mock else 'REAL arms' if self.stack.mock is False else 'attached stack'})",
              flush=True)
        while self.running:
            conn, addr = srv.accept()
            print(f"  live view connected from {addr[0]}:{addr[1]}", flush=True)
            threading.Thread(target=self._client, args=(conn, addr), daemon=True).start()

    def _client(self, conn, addr):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        f = conn.makefile("rwb", buffering=0)
        try:
            for raw in f:
                try:
                    req = json.loads(raw)
                    rep = self.handle(req)
                except Exception as exc:                    # noqa: BLE001
                    rep = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                f.write((json.dumps(rep) + "\n").encode())
                if rep.get("bye"):
                    break
        except (ConnectionError, OSError):
            pass
        finally:
            # A vanished live view must not leave a target in flight: nothing
            # to do -- the cartesian task times out on its own -- but say so.
            with self.lock:
                had = bool(self.sent)
                self.sent.clear()
            print(f"  live view {addr[0]} gone" + (" (arms hold in 0.5 s)" if had else ""),
                  flush=True)
            conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--left-can-port", default=None)
    ap.add_argument("--right-can-port", default=None)
    ap.add_argument("--attach", action="store_true",
                    help="use a stack somebody already started with dimos run")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=BRIDGE_PORT)
    ap.add_argument("--no-viser", action="store_true")
    ap.add_argument("--max-joint-velocity", type=float, default=1.0,
                    help="rad/s cap dimOS applies to every joint (default 1.0)")
    ap.add_argument("--orientation-cost", type=float, default=0.0,
                    help="how hard dimOS tries to AIM the claw, against 8 for where it is "
                         "(default 0: position only; 0.3 to 1 aims it)")
    a = ap.parse_args()
    if (a.left_can_port is None) != (a.right_can_port is None):
        raise SystemExit("give both --left-can-port and --right-can-port, or neither (mock)")
    if not a.attach and a.left_can_port is None:
        print("  no CAN ports: MOCK arms (dimOS's own simulated hardware)", flush=True)
    bridge = Bridge(a.host, a.port)              # the port first: it is the lock
    stack = Stack(attach=a.attach, left_can_port=a.left_can_port,
                  right_can_port=a.right_can_port, viser=not a.no_viser,
                  max_joint_velocity_rad_s=a.max_joint_velocity,
                  orientation_cost=a.orientation_cost)
    bridge.attach(stack)
    try:
        bridge.serve()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.running = False
        stack.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
