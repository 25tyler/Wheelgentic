"""scrub3d/live/drink.py -- a cup, from the claw to the person's mouth.

    python scrub3d/live/drink.py --real               # the camera and the real arm
    python scrub3d/live/drink.py --dry --replay DIR   # all of it on a recording, arm simulated
    python scrub3d/live/drink.py --selftest           # the maths and the protocol, nothing else
    python scrub3d/live/drink.py --check              # read the arms, draw them, move nothing
    python scrub3d/live/drink.py --open | --close     # the right claw, and nothing else

    SCRUB3D_DIMOS=10.189.59.208:7790                  # where dimos_bridge_server.py runs

Stop the scrub view first (python scrub3d/live/stop_live.py): there is one
camera, and the scrub view shuts both claws again every three seconds. Run this
from PowerShell, where SPACE stops the arm at once; in Git Bash keys arrive a
line at a time ("s" and Enter to stop).

WHAT HAPPENS
------------
Hands free, start to end: it counts down to each step and takes it (--ask
makes every count a question that waits for Enter instead).

The person's RIGHT OpenYAM folds to their front and holds its claw open at
chest height: that is where the cup goes in, and the claw shuts on it when the
count ends. The camera finds the mouth (MediaPipe's two mouth corners, placed
in the room by the depth image, exactly as live_body.py places every other
joint). The arm turns and carries the cup to 12 cm in front of the mouth,
comes in slowly, stops short of the lips for the person to lean in, tips 45
degrees toward the face about the rim's lip point, holds, levels, draws back,
takes the cup back to where it got it, opens for it to be taken, and folds
away as it began. A head that has moved since the aim is followed by its
eyes, which still show above the cup when the mouth does not.

The keys are not needed, and all still work: SPACE stops the arm (and holds a
count), Enter cuts a count short, q leaves with the claw shut, and while the
cup waits in front of the mouth 7/1 4/6 8/2 jog the aim, which is the one way
to correct an aim the rig file has wrong.

WHY IT IS NOT PART OF THE SCRUB
-------------------------------
The scrub's governor refuses any tool point above the base of the head, and
keeps 60 mm off the body. That is right for a sponge and rules a cup out, so
this is a separate command with its own, smaller, safety: every move is a slow
planned trajectory, the target is frozen before the cup hides the face, the
cup stops short of the lips and the person closes the gap, nothing goes on
without a key, SPACE stops the arm and levels a tipped cup, and the claw never
opens on a stop. Nothing here edits or imports the scrub's arm code beyond the
bridge's wire (arm_dimos.Link), which other work is changing.

WHY JOINTS AND NOT POSES
------------------------
Streamed, the claw goes where it is sent and is turned loosely or not at all:
the bridge's IK weighs orientation at nothing (or, started with "aim", at a
twentieth of position; dimos_bridge_server.py says why), and a wrist keeps
whatever bend it has. A cup carried that way tips as the arm lifts. The one
route that sets all six joints is the planner: {"op": "home", "joints":
{side: [6]}}, the op that straightens the wrists at the scrub's start. So the
six joints are worked out HERE, from the arm's own URDF (armmesh_openyam's
forward kinematics and a small damped least squares solver), and sent as
planner goals. The bridge is used as it stands and is never restarted: a
restart drops the arms' torque.

Two facts about this arm make that simple. With wrist yaw and roll at zero the
claw's jaws close horizontally, so a cup stood in them upright is upright
whatever the base yaw; and the claw's pitch is a plain sum of the three pitch
joints, so a straight joint-space move between two poses of the same claw
pitch keeps the cup EXACTLY upright on the way. Only the tilt needs all six.

WHAT LIMITS IT
--------------
Reach. On the reference recording the mouth is 684 mm from the right arm's
shoulder and the arm is 723 mm long. It gets there only with the claw pitched
about 45 degrees UP (--elev): level, the wrist would have to stand where the
upper arm cannot put it. A mouth 6 cm higher can still be reached but not
tipped at; then the cup stops short along the way a person leans, and says by
how much. 12 cm higher is refused before anything moves.

Aim. The arm's base is placed from the rig file relative to a seat point the
camera estimates, and that estimate has moved 12 cm between sessions. So the
default stops the rim 10 cm in front of the lips (--standoff), the view shows
the camera's own depth points around the arm so a wrong base can be seen, and
--base-nudge moves it. Lower --standoff once the drawn arm sits on the real one.

Sag. The arm's motors hold a position like a spring, and the arm's own weight
stretches it: the first real run settled with the elbow 0.07 rad and the wrist
0.14 rad low, the claw drooping 12 degrees, which at the mouth is a cup 5 cm
low and leaning. So wherever a cup is carried, and at the hand-over pose where
it is stood upright in the claw, the arm is sent a little PAST its goal by
what it last fell short, and once or twice more if it still reads short
(Rig.move). A move that ends within 0.3 rad is gone on from and said; only
further off than that is it a failure.

Water. Tipped 45 degrees a cup this shape pours whatever is above 60 percent
full, lips or no lips, and gives nothing below that. Fill it to about there.

The 12 V switch is NOT the stop while the cup is up: without power the arm
falls. SPACE is. A joint pushed 0.05 rad past its limit makes dimOS drop torque
on both arms, so poses at the face keep 0.4 rad off every limit.
"""
import argparse
import collections
import json
import math
import os
import queue
import sys
import threading
import time
import traceback

os.environ.setdefault("SCRUB3D_ARM", "openyam")     # before anything imports kinematics

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(HERE)          # scrub3d/: its modules import by bare name
for _p in (WT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                  # noqa: E402

import armmesh_openyam as YAM                       # noqa: E402
import kinematics as K                              # noqa: E402
from arm_dimos import DEFAULT_ENDPOINT, ENV, SIDE_OFFSET_M, Link   # noqa: E402

# --- what may be tuned ----------------------------------------------------------
ELEV_DEG = 45.0             # the claw's pitch while it carries: see WHAT LIMITS IT
TILT_DEG = 45.0             # how far the cup tips toward the face
TILT_STEPS = 3              # in this many moves: the lip point then strays under 3 mm
STANDOFF_MM = 50.0          # the rim stops this far in front of the lips
# The pose the sitter SHOWED as right at the mouth (Viser, 2026-09-19), and what
# it says of this rig: against the mouth the camera had just placed, the cup
# belongs 57 mm further to their left and 17 mm lower than the plain geometry
# puts it, 50 mm off the lips. --offset and --standoff start from that.
TAUGHT_Q = (-0.2105, 1.6285, 1.4717, 0.9042, -0.2005, -0.4965)
TAUGHT_OFFSET = (0.0, 57.0, -17.0)
PRE_MM = 120.0              # and waits this much further out before coming in
CUP_R_MM, CUP_RIM_MM, CUP_H_MM = 40.0, 50.0, 100.0   # radius; rim above the jaws; height
HAND_R_MM, HAND_Z_MM = 420.0, 520.0   # the hand-over point: out from the base axis, above it
SLIDE_MAX_MM = 150          # how far short of the mouth a cup may stop, to be leaned to
SLIDE_DOWN = 0.3            # a person leaning in comes this much down per unit forward

SPEED_FREE = 0.2            # planner speed scale, claw empty (the scrub's park uses 0.2)
SPEED_CARRY = 0.15          # with the cup, in the open
SPEED_NEAR = 0.05           # coming in, tipping, levelling, drawing back

LIMIT_MARGIN = 0.15         # dimOS refuses a goal that sits on a joint stop
FACE_ROOM = 0.4             # poses at the face keep this far off every limit
MIN_STEP_RAD = 0.15         # a planned move smaller than this may be refused as no move
THERE_RAD = 0.02            # already there: no plan is asked for
ARRIVE_RAD = 0.04           # arrived: 1 to 2 cm at the cup
NEAR_RAD = 0.3              # settled this near: gone on from, and said (a real arm sags)
TRIM_GAIN, BIAS_MAX = 0.9, 0.35   # sending the arm past its goal by what it fell short
STRAY_RAD = 0.5             # with a cup: this far off the straight joint line is a stop
                            # (a planner's detour; the real arm's own sag reads up to 0.25)
FRESH_S = 0.5               # joints older than this are not pinned to
HEAD_MOVED_MM = 60.0        # coming in: the head moved this far since the cup was aimed
FACE_LOST_S = 1.5           # ... or the face has not been seen for this long
RE_AIM_MM = 25.0            # moved this far before coming in: aim again first
AIM_TRIES = 4               # a head that will not keep still through this many: give up
CUP_S, LOOK_S, LEAN_S, TAKE_S = 8.0, 2.0, 3.0, 8.0   # the counts: see Drink.ask
JOG_MM = 20.0               # one press of a jog key, at the question before coming in
JOG = {"7": (1, 0, 0), "1": (-1, 0, 0), "6": (0, 1, 0), "4": (0, -1, 0),
       "8": (0, 0, 1), "2": (0, 0, -1)}     # world: x the way they face, y their left, z up
KEEP_BODY_MM = 70.0         # an elbow or forearm nearer the body than this: not planned
SETTLE_S = 1.0              # after the claw shuts on the cup

COMPACT = (0.3, 0.2)        # shoulder, elbow: the claw drawn back to its own base
FRONT_NEAR_RAD = 0.35       # already pointing this near the front: no need to draw back
START_POSE = (1.57, 0.9)    # shoulder, elbow of the scrub view's start pose, at the front
STOP_NEAR_RAD = 0.03        # a shoulder or elbow this near zero is lying on its stop
ARC_RAD_S = 0.25            # a turn the planner refuses is streamed, this fast

UP = np.array([0.0, 0.0, 1.0])
SIDES = ("left", "right")
POSE_FILE = os.path.join(HERE, "drink_pose.json")    # joints, no person in it: kept in git
KEYS_FILE = os.path.join(HERE, "drive_logs", "drink.keys")   # see Keys._tail


# --- the arm, as numbers ------------------------------------------------------------

def _limits():
    """The six joint ranges, from the URDF the arm is drawn from. -> (6, 2) rad"""
    import xml.etree.ElementTree as ET
    out = {}
    for j in ET.parse(YAM.URDF_PATH).getroot().findall("joint"):
        name = j.get("name")
        if j.get("type") == "revolute" and name.startswith(YAM.SIDE + "joint"):
            lim = j.find("limit")
            out[int(name[len(YAM.SIDE + "joint"):])] = (float(lim.get("lower")),
                                                        float(lim.get("upper")))
    assert sorted(out) == [1, 2, 3, 4, 5, 6], out
    return np.array([out[k] for k in sorted(out)])


LIMITS = _limits()


def grasp(q):
    """Six joints -> the grasp frame in the arm's own base frame. 4x4, mm.
    The claw reaches along -z of it and its jaws close along y."""
    return YAM.link_transforms(q)[YAM.TOOL]


def room(q):
    """How far the nearest joint is from its limit, rad."""
    q = np.asarray(q, float)
    return float(np.minimum(q - LIMITS[:, 0], LIMITS[:, 1] - q).min())


def rot(axis, angle):
    """Rotation by `angle` about `axis` (Rodrigues)."""
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    S = np.array([[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]])
    return np.eye(3) + math.sin(angle) * S + (1.0 - math.cos(angle)) * (S @ S)


def _rotvec(R):
    """A rotation as axis times angle: zero when R is no turn at all."""
    c = max(-1.0, min(1.0, (float(np.trace(R)) - 1.0) / 2.0))
    th = math.acos(c)
    if th < 1e-9:
        return np.zeros(3)
    w = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    return w * (th / (2.0 * math.sin(th)))


def _elevation(T, up=UP):
    """How far above level the claw points, rad."""
    return math.asin(max(-1.0, min(1.0, float(-T[:3, 2] @ up))))


def _solve(resid, seed, free, good):
    """Damped least squares on a numeric Jacobian, inside the joint limits.
    -> (joints, did it get there). Forty lines of numpy: the repo has no six
    joint IK, pinocchio and pink live on the Spark only, and ikpy would be a
    new dependency for this much."""
    lo, hi = LIMITS[:, 0] + LIMIT_MARGIN, LIMITS[:, 1] - LIMIT_MARGIN
    q = np.clip(np.asarray(seed, float), lo, hi)
    for _ in range(200):
        r = resid(q)
        if good(r):
            return q, True
        J = np.empty((len(r), len(free)))
        for c, j in enumerate(free):
            dq = q.copy()
            dq[j] += 1e-5
            J[:, c] = (resid(dq) - r) / 1e-5
        step = np.linalg.solve(J.T @ J + 1e-2 * np.eye(len(free)), -J.T @ r)
        n = float(np.linalg.norm(step))
        if n > 0.3:
            step *= 0.3 / n
        q[free] += step
        q = np.clip(q, lo, hi)
    return q, bool(good(resid(q)))


def _reach_words(p):
    yaw = math.atan2(p[1], p[0])
    sh = np.array([K.BASE_X_MM * math.cos(yaw), K.BASE_X_MM * math.sin(yaw), K.BASE_H_MM])
    d = float(np.linalg.norm(np.asarray(p, float) - sh))
    if d > K.REACH_MAX:
        return f"{d - K.REACH_MAX:.0f} mm past this arm's reach"
    return (f"within reach ({d:.0f} of {K.REACH_MAX:.0f} mm) but not with the claw "
            f"pitched as it carries")


def solve_carry(p, elev, seed=None, up=UP):
    """The cup upright at `p` (base frame, mm), the claw pitched `elev` above
    level: joints 1 to 4, wrist yaw and roll zero. -> (joints, None) or
    (None, why)"""
    p = np.asarray(p, float)
    yaw = math.atan2(p[1], p[0])
    if yaw < LIMITS[0, 0]:
        yaw += 2.0 * math.pi

    def resid(q):
        T = grasp(q)
        return np.r_[(T[:3, 3] - p) / 100.0, _elevation(T, up) - elev]

    def good(r):
        return float(np.linalg.norm(r[:3])) < 2e-3 and abs(float(r[3])) < 1e-3

    # One seed in three stalls on this arm when picked at random, so: the pose
    # before it, then three that are known to lead somewhere.
    seeds = [] if seed is None else [np.r_[np.asarray(seed, float)[:4], 0.0, 0.0]]
    seeds += [[yaw, 1.6, 2.2, 0.4, 0.0, 0.0], [yaw, 1.7, 1.7, 0.8, 0.0, 0.0],
              [yaw, 1.9, 1.3, 1.2, 0.0, 0.0]]
    for s in seeds:
        q, ok = _solve(resid, s, [0, 1, 2, 3], good)
        if ok:
            return q, None
    return None, _reach_words(p)


def solve_pose(p, R, seed):
    """The grasp frame at `p`, turned as `R`: all six joints, from `seed`.
    -> (joints, did it get there)"""
    p, R = np.asarray(p, float), np.asarray(R, float)

    def resid(q):
        T = grasp(q)
        return np.r_[(T[:3, 3] - p) / 100.0, _rotvec(R @ T[:3, :3].T)]

    def good(r):
        return float(np.linalg.norm(r[:3])) < 2e-3 and float(np.linalg.norm(r[3:])) < 1e-3

    return _solve(resid, seed, [0, 1, 2, 3, 4, 5], good)


def solve_wrist(R, seed):
    """Turn the claw as `R` with the WRIST ALONE: joints 1 to 3 stay exactly as
    in `seed`, so the arm does not travel. -> (joints, did it get there)"""
    R = np.asarray(R, float)

    def resid(q):
        return _rotvec(R @ grasp(q)[:3, :3].T)

    return _solve(resid, seed, [3, 4, 5], lambda r: float(np.linalg.norm(r)) < 2e-3)


# --- where the cup goes -----------------------------------------------------------

class Plan:
    """One sip, as joints: `pre` (in front of the mouth), `at` (the rim at the
    standoff), `tilts` (the steps of the tip). Points are in the arm's base
    frame, mm. `short_mm` is how far short of what was asked the cup stops."""

    def __init__(self):
        self.pre = self.at = self.lip = self.g_pre = self.g_at = None
        self.tilts, self.g_tilts = [], []
        self.axis_g = self.lip_g = None       # the cup's axis and lip point, grasp frame
        self.short_mm, self.short_way, self.tilt_deg = 0.0, "further out", TILT_DEG

    def legs(self, start):
        """[(label, from, to, is it at the face)] from `start`, in order."""
        out = [("swing", start, self.pre, False), ("approach", self.pre, self.at, True)]
        was = self.at
        for i, q in enumerate(self.tilts):
            out.append((f"tilt {i + 1}", was, q, True))
            was = q
        return out

    def lip_drift(self, n=21):
        """How far the rim's lip point strays while tipping, mm: the joints
        move in straight lines between the steps, the lip point should not."""
        worst, was = 0.0, self.at
        for q in self.tilts:
            for k in np.linspace(0.0, 1.0, n):
                T = grasp(was + (q - was) * k)
                worst = max(worst, float(np.linalg.norm(T[:3, 3] + T[:3, :3] @ self.lip_g
                                                        - self.lip)))
            was = q
        return worst


def _plan_at(lip, f, a, tilt, elev, seed, up):
    """The joints for one sip with the rim's lip point at `lip`. -> (Plan, None)
    or (None, why)"""
    g_at = lip + f * a.cup_radius - up * a.rim_above_grip
    q_at, why = solve_carry(g_at, elev, seed, up)
    if why:
        return None, f"the cup's place in front of the mouth is {why}"
    if room(q_at) < FACE_ROOM:
        return None, (f"at the mouth a joint would be {room(q_at):.2f} rad from its stop, "
                      f"and {FACE_ROOM} is kept there")
    # Where it waits before coming in: further out along the way the face
    # looks, as far as the arm reaches. On a rig whose base stands behind
    # the mouth, further out is further away, and 12 cm may be too far.
    for pre_mm in (PRE_MM, 90.0, 60.0):
        g_pre = g_at + f * pre_mm
        q_pre, why = solve_carry(g_pre, elev, q_at, up)
        if not why:
            break
    if why:
        return None, f"the waiting point in front of the mouth is {why}"
    R0 = grasp(q_at)[:3, :3]
    axis = np.cross(f, up)                   # tipping about this leans the top at the face
    P = Plan()
    for n in range(TILT_STEPS, 0, -1):
        qs, gs, was = [], [], q_at
        for k in range(1, n + 1):
            Rt = rot(axis, tilt * k / n)
            g = lip + Rt @ (g_at - lip)      # about the lip point, which stays put
            q, ok = solve_pose(g, Rt @ R0, was)
            if not ok or room(q) < FACE_ROOM:
                return None, (f"tipped {math.degrees(tilt * k / n):.0f} degrees there the "
                              f"wrist runs out of room")
            qs.append(q)
            gs.append(g)
            was = q
        steps = [float(np.abs(b - c).max()) for b, c in zip([q_at] + qs[:-1], qs)]
        if min(steps) >= MIN_STEP_RAD or n == 1:
            break                            # else fewer, bigger steps
    P.pre, P.at, P.tilts, P.lip = q_pre, q_at, qs, lip
    P.g_pre, P.g_at, P.g_tilts = g_pre, g_at, gs
    P.axis_g, P.lip_g = R0.T @ up, R0.T @ (lip - g_at)
    return P, None


def plan(mouth, f, a, seed=None, up=UP):
    """Mouth and the way the face looks (base frame) -> (Plan, None) or
    (None, why, in words). What cannot be reached as asked is tried again
    further out along the way a person leans in, then with a smaller tip."""
    mouth, f = np.asarray(mouth, float), np.asarray(f, float)
    f = f - up * float(f @ up)
    f = f / np.linalg.norm(f)
    lean = f - SLIDE_DOWN * up
    lean = lean / np.linalg.norm(lean)
    elev = math.radians(a.elev)
    first = None
    # As asked; else further out along a lean; else straight down, for a mouth
    # that is too high (the chin comes down to a cup more easily than a chair).
    slides = [(lean, s, "further out") for s in range(0, SLIDE_MAX_MM + 1, 15)] \
        + [(-up, s, "lower") for s in (20, 40, 60, 80)]
    for tilt in [a.tilt_deg] + ([30.0] if a.tilt_deg > 30.0 else []):
        for way, slide, words in slides:
            lip = mouth + f * a.standoff + way * float(slide)
            P, why = _plan_at(lip, f, a, math.radians(tilt), elev, seed, up)
            if P is not None:
                P.short_mm, P.short_way, P.tilt_deg = float(slide), words, float(tilt)
                return P, None
            first = first or why
    # Never brought NEARER than asked by itself: the standoff is what keeps a
    # wrongly placed base off a face. But say what would work.
    for nearer in range(int(a.standoff) - 20, 20, -20):
        b = argparse.Namespace(**vars(a))
        b.standoff = float(nearer)
        if _plan_at(mouth + f * b.standoff, f, b, math.radians(30.0), elev, seed, up)[0]:
            return None, (f"{first}. The most this arm can stand off from this mouth is about "
                          f"--standoff {nearer}")
    return None, f"{first}, and at no standoff down to 40 mm either"


def check_path(legs, T_world_base, body_pts, cup_r=CUP_R_MM, keep=KEEP_BODY_MM):
    """The arm against the body model along every leg, the joints taken in
    straight lines. -> (None or why, the closest it comes, mm)

    Elbow, wrist and the middles of the two long links, everywhere; the claw
    and the cup as well while the cup is in the open. At the face the claw is
    near the head on purpose, and what keeps it off is the standoff."""
    if body_pts is None or len(body_pts) < 50:
        return None, None
    from scipy.spatial import cKDTree
    tree = cKDTree(np.asarray(body_pts, float))
    T = np.asarray(T_world_base, float)
    closest = 1e9
    for label, q0, q1, at_face in legs:
        for k in np.linspace(0.0, 1.0, 13):
            tf = YAM.link_transforms(np.asarray(q0) + (np.asarray(q1) - np.asarray(q0)) * k)
            s, e, w = (tf[n][:3, 3] for n in ("link2", "link3", "link4"))
            pts, need = [e, w, 0.5 * (s + e), 0.5 * (e + w)], [keep] * 4
            if not at_face:
                pts += [tf["gripper"][:3, 3], tf[YAM.TOOL][:3, 3]]
                need += [keep, keep + cup_r]
            P = np.array(pts) @ T[:3, :3].T + T[:3, 3]
            d, _ = tree.query(P)
            gap = d - np.array(need)
            closest = min(closest, float((gap + keep).min()))
            if (gap < 0.0).any():
                i = int(np.argmin(gap))
                what = ("the elbow", "the wrist", "the upper arm", "the forearm",
                        "the claw", "the cup")[i]
                return (f"on the {label}, {what} would pass {d[i]:.0f} mm from the body model "
                        f"(it keeps {need[i]:.0f})"), closest
    return None, closest


# --- the face ---------------------------------------------------------------------

class Face:
    """The mouth, from the last few frames the person was found in.

    Mouth: the middle of MediaPipe's two mouth corners, each placed by the
    depth under it. Which way the face looks: square to the shoulder line,
    the steadiest of the lines tried on the recording (4.7 +/- 1.2 degrees
    where the ear line said 37 off, because an ear's depth lands on whatever
    is nearest beside it), kept within 25 degrees of straight ahead. The eyes
    are what the head is watched by once the cup hides the mouth."""

    KEEP, WANT, FRESH_S = 15, 8, 4.0
    TURN_MAX = math.radians(25.0)

    def __init__(self, face_deg=None, moved_mm=HEAD_MOVED_MM):
        self.lock = threading.Lock()
        self.mouth = collections.deque(maxlen=self.KEEP)
        self.eyes = collections.deque(maxlen=self.KEEP)
        self.turn = collections.deque(maxlen=self.KEEP)
        self.face_deg, self.moved_mm = face_deg, float(moved_mm)
        self.anchor = None                   # the eyes, when the target was set
        self.frozen = None                   # (mouth, facing) the cup is aimed at

    def add(self, J, now):
        """One POSED frame's joints (live.J keeps the last frame's when the
        person is lost, so only posed frames come here)."""
        with self.lock:
            if "mouth_l" in J and "mouth_r" in J:
                self.mouth.append((now, 0.5 * (J["mouth_l"] + J["mouth_r"])))
            if "l_eye" in J and "r_eye" in J:
                self.eyes.append((now, 0.5 * (J["l_eye"] + J["r_eye"])))
            if "l_shoulder" in J and "r_shoulder" in J:
                v = np.cross(J["l_shoulder"] - J["r_shoulder"], UP)
                if float(np.hypot(v[0], v[1])) > 1.0:
                    self.turn.append((now, math.atan2(v[1], v[0])))

    def target(self, now):
        """-> (mouth, the way the face looks), world, or None if not seen lately."""
        with self.lock:
            m = [p for t, p in self.mouth if now - t < self.FRESH_S]
            w = [v for t, v in self.turn if now - t < self.FRESH_S]
        if len(m) < self.WANT:
            return None
        ang = float(np.median(w)) if w else 0.0
        if self.face_deg is not None:
            ang = math.radians(self.face_deg)
        ang = max(-self.TURN_MAX, min(self.TURN_MAX, ang))
        return np.median(np.array(m), axis=0), np.array([math.cos(ang), math.sin(ang), 0.0])

    def freeze(self, now):
        """Aim at the mouth as it is seen NOW: for when the cup is down and
        the whole face shows. -> (mouth, facing) or None"""
        got = self.target(now)
        with self.lock:
            e = [p for t, p in self.eyes if now - t < self.FRESH_S]
            self.anchor = np.median(np.array(e), axis=0) if len(e) >= 3 else None
            self.frozen = got
        return got

    def _eyes_now(self, now):
        with self.lock:
            e = [p for t, p in list(self.eyes)[-3:] if now - t < FACE_LOST_S]
        return np.median(np.array(e), axis=0) if e else None

    def shift(self, now):
        """How far the head has moved since the cup was aimed, by the eyes.
        -> (3,) mm, or None if the eyes are not seen or were not then"""
        e = self._eyes_now(now)
        with self.lock:
            return None if e is None or self.anchor is None else e - self.anchor

    def follow(self, now):
        """Aim again with the cup already up. The mouth is then behind the
        cup, where a depth reading lands on the cup itself, so the mouth is
        taken to have moved as the eyes did. -> (mouth, facing) or None"""
        d = self.shift(now)
        with self.lock:
            if self.frozen is None:
                return None
            if d is not None:
                self.frozen = (self.frozen[0] + d, self.frozen[1])
                self.anchor = self.anchor + d
            return self.frozen

    def trouble(self, now):
        """While the cup comes in: has the head moved, or gone? -> words or None"""
        if self._eyes_now(now) is None:
            return f"the face has not been seen for {FACE_LOST_S} s"
        d = self.shift(now)
        if d is not None and float(np.linalg.norm(d)) > self.moved_mm:
            return f"the head moved {np.linalg.norm(d):.0f} mm since the cup was aimed"
        return None


# --- keys ------------------------------------------------------------------------

class Keys:
    """Enter, SPACE, and single letters, from a thread. SPACE sets `stop` the
    moment it is pressed, whatever the sequence is waiting on.

    On a Windows console keys come one at a time (msvcrt). Anywhere else they
    come a line at a time: an empty line is Enter, "s" is stop. The end of
    input is NOT an Enter: a closed stdin must never walk a cup to a face."""

    def __init__(self, stop, auto=False):
        self.q, self.stop, self.auto = queue.Queue(), stop, auto
        if not auto:
            threading.Thread(target=self._read, daemon=True).start()
            threading.Thread(target=self._tail, daemon=True).start()

    def _put(self, tok):
        tok = {"s": "space", "stop": "space", "go": "enter", "": "enter"}.get(tok, tok)
        if tok == "space":
            self.stop.set()
        self.q.put(tok)

    def _tail(self):
        """Keys from a file as well, a word a line (enter, space, q, r, a, 7...):
        for a run started where there is no keyboard to it, which must still
        be answerable and, above all, stoppable. Emptied at the start."""
        try:
            os.makedirs(os.path.dirname(KEYS_FILE), exist_ok=True)
            open(KEYS_FILE, "w", encoding="utf-8").close()
        except OSError:
            return
        at = 0
        while True:
            time.sleep(0.15)
            try:
                with open(KEYS_FILE, encoding="utf-8") as f:
                    f.seek(at)
                    lines = f.readlines()
                    at = f.tell()
            except OSError:
                continue
            for line in lines:
                if line.strip():
                    self._put(line.strip().lower())

    def _read(self):
        try:
            import msvcrt
        except ImportError:
            msvcrt = None
        try:
            tty = sys.stdin is not None and sys.stdin.isatty()
        except (AttributeError, ValueError):
            tty = False
        if msvcrt is not None and tty:
            while True:
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    msvcrt.getwch()                        # an arrow or function key
                elif ch == "\x03":
                    import _thread
                    _thread.interrupt_main()               # Ctrl+C, read raw
                else:
                    self._put({"\r": "enter", "\n": "enter", " ": "space"}.get(ch, ch.lower()))
        elif sys.stdin is not None:
            for line in sys.stdin:
                s = line.strip().lower()
                self._put("enter" if not s else s[:1])

    def drain(self):
        """Keys pressed before a question do not answer it."""
        while not self.q.empty():
            self.q.get_nowait()

    def get(self, timeout):
        """-> the next key within `timeout` s, or None."""
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


# --- the bridge ----------------------------------------------------------------------

class Rig:
    """The two arms through the bridge, for ONE thread. Every move is one
    planned trajectory for `side`, with the other arm sent its own joints.

    That pin is not optional: a side left out of a "home" request goes to
    dimOS's own home pose, which on this rig points the claw at the person.
    So no move is asked for without a reading of BOTH arms under half a
    second old, and "close" is always false: true shuts both claws, and one
    of them may be open for the cup."""

    POLL_S, GRACE_S = 0.1, 0.5

    def __init__(self, link, side="right"):
        self.link, self.side = link, side
        self.other = "left" if side == "right" else "right"
        self.cache = {}                  # side -> six joints, for whoever draws
        self.claw = None                 # what this side's claw was last told
        self.bias = np.zeros(6)          # how far past a goal the arm is sent: see move()

    def state(self):
        rep = self.link.call({"op": "state"}, timeout=2.0)
        if not rep.get("ok"):
            return None
        for s, d in (rep.get("arms") or {}).items():
            if d and d.get("joints") and len(d["joints"]) == 6:
                self.cache[s] = [float(v) for v in d["joints"]]
        return rep

    def fresh(self):
        """-> ({side: six joints}, None) read just now, or (None, why)."""
        rep = self.state()
        if rep is None:
            return None, "the bridge did not answer"
        age = rep.get("age")
        if age is None or age > FRESH_S:
            return None, "dimOS has not reported the arms for " + (
                "a while" if age is None else f"{age:.1f} s")
        arms = rep.get("arms") or {}
        out = {}
        for s in SIDES:
            q = (arms.get(s) or {}).get("joints")
            if not q or len(q) != 6:
                return None, f"no joint reading of the {s} arm: nothing is moved without one"
            out[s] = [float(v) for v in q]
        return out, None

    def busy_elsewhere(self):
        """Is something else streaming targets at these arms? -> words or None"""
        a = self.state()
        time.sleep(1.0)
        b = self.state()
        if a is None or b is None:
            return "the bridge did not answer"
        if (b.get("n_targets") or 0) > (a.get("n_targets") or 0):
            return ("something else is driving these arms (a scrub view?): stop it first, "
                    "python scrub3d/live/stop_live.py")
        return None

    def grip(self, pos):
        """0 shut .. 1 open."""
        self.claw = float(pos)
        return bool(self.link.call({"op": "gripper", "arm": self.side, "pos": float(pos)},
                                   timeout=1.0).get("ok"))

    def cancel(self):
        return self.link.call({"op": "cancel"}, timeout=3.0)

    def _idle(self, within):
        end = time.monotonic() + within
        while time.monotonic() < end:
            rep = self.state()
            if rep is not None and rep.get("exec") not in ("EXECUTING", "ACCEPTED"):
                return True
            time.sleep(self.POLL_S)
        return False

    def _send(self, cmd, speed, label, far):
        """One planned move of `side` to `cmd`, the other arm pinned where it
        is. -> (None, where this arm was) or (why not, None); ("", ...) when the
        planner would not plan so small a move, which is no fault."""
        q, why = self.fresh()
        if why:
            return why, None
        req = {"op": "home", "speed": float(speed), "close": False,
               "joints": {self.side: [float(v) for v in cmd], self.other: q[self.other]}}
        rep = self.link.call(req, timeout=30.0)
        if not rep.get("ok") and "current state" in str(rep.get("error", "")).lower():
            self._idle(3.0)                  # the last move had not quite ended
            rep = self.link.call(req, timeout=30.0)
        if rep.get("ok"):
            return None, np.array(q[self.side])
        err = str(rep.get("error"))
        if "Error" in err:                   # the reply was lost, the plan may be running
            self.cancel()
        elif far < NEAR_RAD:
            return "", None
        return f"the planner would not plan the {label}: {err}", None

    def move(self, goal, speed, label, stop, line=True, watch=None, timeout=90.0, trims=0):
        """`side` to `goal` through the planner, and wait. -> None, or why not

        A real arm settles SHORT of what it is sent: its motors hold a
        position like a spring, and the weight of the forearm, the claw and
        the cup stretches it. The first real run ended with the elbow 0.07
        rad and the wrist 0.14 rad low, the claw drooping 12 degrees: a cup
        5 cm under the mouth and leaning. So where it matters (`trims`) the
        arm is sent a little PAST the goal, by what it fell short last time,
        and again if it still reads short: `bias` is that allowance, kept
        from move to move since the next pose loads the joints much the same.
        Arrival is always judged on where the joints READ against the goal
        that was wanted, never against what was sent."""
        goal = np.asarray(goal, float)
        q, why = self.fresh()
        if why:
            return why
        start = np.array(q[self.side])
        if float(np.abs(goal - start).max()) < THERE_RAD:
            return None
        lo, hi = LIMITS[:, 0] + LIMIT_MARGIN, LIMITS[:, 1] - LIMIT_MARGIN
        off, here = None, start
        for k in range(trims + 1):
            if k and off < ARRIVE_RAD:
                break
            if k:
                self.bias = np.clip(self.bias + TRIM_GAIN * (goal - here), -BIAS_MAX, BIAS_MAX)
            cmd = np.clip(goal + (self.bias if trims else 0.0), lo, hi)
            far = float(np.abs(cmd - here).max())
            why, _ = self._send(cmd, speed if k == 0 else min(speed, SPEED_NEAR * 2.0),
                                label, far)
            if why == "":
                break                        # too small a move to plan: as near as it gets
            if why:
                return why
            why, here = self.settle(start if k == 0 else here, goal, label, stop,
                                    line and k == 0, watch, timeout)
            if why:
                return why
            off = float(np.abs(here - goal).max())
        if off is None:
            off = float(np.abs(here - goal).max())
        if off >= NEAR_RAD:
            return f"the arm stopped {off:.2f} rad short on the {label}"
        if off >= ARRIVE_RAD:
            i = int(np.argmax(np.abs(here - goal)))
            print(f"  {label}: settled {off:.2f} rad short (joint {i + 1}); going on", flush=True)
        return None

    def settle(self, q0, q1, label, stop, line, watch, timeout):
        """Until dimOS is done with the move and the arm has stopped.
        -> (None, where the joints read) or (why not, None)

        On the execution status AND the joints, as arm_dimos._wait_joints is:
        dimOS leaves the last plan's COMPLETED standing while the next is
        being accepted, so the status alone comes back before the arm moves.
        `line`: with a cup, straying off the straight joint line from q0 to
        q1 is a stop, since the cup is only level on that line."""
        t0 = time.monotonic()
        was, still, busy_seen, misses = None, 0, False, 0
        d = q1 - q0
        L2 = float(d @ d)
        while True:
            now = time.monotonic()
            if stop.is_set():
                self.cancel()
                return "stopped by hand", None
            why = watch() if watch is not None else None
            if why:
                self.cancel()
                return why, None
            rep = self.state()
            q = (((rep or {}).get("arms") or {}).get(self.side) or {}).get("joints")
            if not q:
                misses += 1
                if misses > 20:
                    self.cancel()
                    return "the bridge stopped answering", None
                time.sleep(self.POLL_S)
                continue
            misses, q = 0, np.array(q, float)
            busy = rep.get("exec") in ("EXECUTING", "ACCEPTED")
            moving = was is not None and float(np.abs(q - was).max()) > 0.002
            busy_seen |= busy or moving
            was = q
            if line and L2 > 1e-9:
                k = min(1.0, max(0.0, float((q - q0) @ d) / L2))
                astray = float(np.abs(q - (q0 + k * d)).max())
                if astray > STRAY_RAD:
                    self.cancel()
                    return (f"on the {label} the arm is {astray:.2f} rad off the straight "
                            f"line it was planned on, and the cup is only level on that line"), None
            if busy or moving or now - t0 < self.GRACE_S:
                still = 0
            else:
                still += 1
                if still >= 3 and (busy_seen or now - t0 > 1.5):
                    return None, q
            if now - t0 > timeout:
                self.cancel()
                return f"the {label} took over {timeout:.0f} s", None
            time.sleep(self.POLL_S)

    # Two things the planner cannot do, done by streaming instead, as
    # arm_dimos.Hardware does them (_off_the_stops, _stream_yaw). Copied, not
    # called: that class is being changed by other work, and these are small.
    def lift_off_stop(self, side):
        """An arm lying on a joint stop reads exactly the limit, and dimOS's
        time parametrization then refuses every plan, for BOTH arms: lift its
        claw 5 cm by streaming, which is not planned."""
        st = ((self.state() or {}).get("arms") or {}).get(side) or {}
        p, qt = st.get("p"), st.get("q")
        if not p or not qt:
            return False
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.8:
            k = min(1.0, (time.monotonic() - t0) / 1.2)
            k = k * k * (3.0 - 2.0 * k)
            self.link.call({"op": "target", "arm": side,
                            "p": [p[0], p[1], p[2] + 0.05 * k], "q": qt}, timeout=1.0)
            time.sleep(0.04)
        self.link.call({"op": "hold", "arm": side}, timeout=1.0)
        time.sleep(0.8)
        return True

    def stream_yaw(self, yaw_goal, stop):
        """Turn `side` about its own base on an arc at the radius it is at.
        -> did it arrive. For a turn, tucked in, that the planner calls a
        collision with the box its model bolts the arms to."""
        st = ((self.state() or {}).get("arms") or {}).get(self.side) or {}
        p, qt, q6 = st.get("p"), st.get("q"), st.get("joints")
        if not p or not qt or not q6:
            return False
        off = SIDE_OFFSET_M[self.side]
        x, y = p[0] - off[0], p[1] - off[1]
        r, th0 = math.hypot(x, y), math.atan2(y, x)
        delta = float(yaw_goal) - float(q6[0])
        span = abs(delta) / ARC_RAD_S
        t0 = time.monotonic()
        while not stop.is_set():
            t = time.monotonic() - t0
            k = min(1.0, t / span) if span > 1e-3 else 1.0
            k = k * k * (3.0 - 2.0 * k)
            a = th0 + delta * k
            # The claw turns with the arc (a bridge started with "aim" heeds
            # the orientation): the turn so far about z, times where it began.
            s, c = math.sin(delta * k / 2.0), math.cos(delta * k / 2.0)
            qx, qy, qz, qw = qt
            turned = [c * qx - s * qy, c * qy + s * qx, c * qz + s * qw, c * qw - s * qz]
            if not self.link.call({"op": "target", "arm": self.side, "q": turned,
                                   "p": [r * math.cos(a) + off[0], r * math.sin(a) + off[1],
                                         p[2]]}, timeout=1.0).get("ok"):
                return False
            if t > span + 1.0:
                break
            time.sleep(0.04)
        self.link.call({"op": "hold", "arm": self.side}, timeout=2.0)
        time.sleep(0.8)
        q, _ = self.fresh()
        return bool(q) and abs(q[self.side][0] - float(yaw_goal)) < 0.25


class DryLink:
    """dimos_bridge_server.py's wire with nothing behind it (--dry, and the
    self-test): joints that move in time, and the refusals that matter. A
    "home" without both arms, past a limit, of no length, or asked while a
    move is running is refused, as the real one refuses them or worse."""

    V_RAD_S = 2.0                         # the URDF's joint speed; a plan's speed scales it
    START = {"left": [1.5708, 1.57, 0.9, 0.0, 0.0, 0.0],      # folded at the front
             "right": [0.25, 1.2, 1.5, 0.4, 0.1, -0.2]}       # left pointing at the person

    def __init__(self, rate=1.0, start=None, refuse_turns=False, sag=None, sag_cup=None):
        """`sag`, `sag_cup`: how far the right arm's joints READ from where
        they are sent, claw empty, and how much more with a cup in it: a real
        arm's motors give under its weight, and the first real run showed it."""
        self.lock = threading.Lock()
        self.port, self.failed, self.rate = "nothing (a dry run)", 0, float(rate)
        self.refuse_turns = refuse_turns
        self.q = {s: np.array(v, float) for s, v in (start or self.START).items()}   # as SENT
        self.sag = np.zeros(6) if sag is None else np.array(sag, float)
        self.sag_cup = np.zeros(6) if sag_cup is None else np.array(sag_cup, float)
        self.opens = 0                    # times the right claw opened: it shuts on a cup once
        self.going = None                 # ({side: from}, {side: to}, since, seconds)
        self.exec, self.claw, self.n_targets = "IDLE", {"left": 0.0, "right": 0.0}, 0
        self.log = []                     # (the request, {side: joints} when it came)

    def read(self, s):
        """Where arm `s` READS: where it was sent, less what it gives."""
        if s != "right":
            return self.q[s].copy()
        cup = self.opens == 1 and self.claw["right"] < 0.5
        return self.q[s] + self.sag + (self.sag_cup if cup else 0.0)

    def _advance(self):
        if self.going is None:
            return
        q0, q1, since, secs = self.going
        k = min(1.0, (time.monotonic() - since) * self.rate / secs)
        for s in q0:
            self.q[s] = q0[s] + (q1[s] - q0[s]) * k
        if k >= 1.0:
            self.going, self.exec = None, "COMPLETED"

    def call(self, req, timeout=2.0):
        with self.lock:
            self._advance()
            self.log.append((json.loads(json.dumps(req)), {s: self.read(s) for s in self.q}))
            return self._handle(req)

    def _handle(self, req):
        op = req.get("op")
        if op == "info":
            return {"ok": True, "mock": True, "dry": True, "arms": list(SIDES)}
        if op == "state":
            arms = {}
            for s in self.q:
                q = self.read(s)
                p = YAM.tool_point(q) / 1000.0 + SIDE_OFFSET_M[s]
                arms[s] = {"p": [float(v) for v in p], "q": [0.0, 0.0, 0.0, 1.0],
                           "joints": [float(v) for v in q]}
            return {"ok": True, "exec": self.exec, "error": None, "age": 0.0, "arms": arms,
                    "sent": {}, "n_targets": self.n_targets}
        if op == "home":
            j = req.get("joints") or {}
            if sorted(j) != ["left", "right"]:
                return {"ok": False, "error": "DRY: a side is missing, and the real bridge "
                                              "would send that arm to dimOS's home"}
            if self.going is not None:
                return {"ok": False, "error": "plan FAILED: Cannot plan in current state"}
            goal = {s: np.array(j[s], float) for s in SIDES}
            for s in SIDES:
                g = goal[s]
                if len(g) != 6 or (g < LIMITS[:, 0]).any() or (g > LIMITS[:, 1]).any():
                    return {"ok": False, "error": f"plan INVALID: {s} goal is outside its limits"}
            far = max(float(np.abs(goal[s] - self.read(s)).max()) for s in SIDES)
            if far < 1e-3:
                return {"ok": False, "error": "plan FAILED: no length"}
            turn = goal["right"] - self.read("right")
            if self.refuse_turns and abs(turn[0]) > 0.3 and float(np.abs(turn[1:]).max()) < 0.08:
                return {"ok": False, "error": "plan FAILED: goal configuration in collision"}
            secs = max(0.05, far / (max(1e-3, float(req.get("speed", 0.2))) * self.V_RAD_S))
            # dimOS plans from where the arm READS, as the real one does.
            self.going = ({s: self.read(s) for s in SIDES}, goal, time.monotonic(), secs)
            self.exec = "EXECUTING"
            if req.get("close"):
                self.claw = {"left": 0.0, "right": 0.0}
            return {"ok": True, "plan": "SUCCESS"}
        if op == "target":
            # Enough of a streamed target for the two things that stream: the
            # arm turns to face the point, and comes off a stop it lay on.
            s = req["arm"]
            p = np.asarray(req["p"], float) - SIDE_OFFSET_M[s]
            self.q[s][0] = math.atan2(p[1], p[0])
            self.q[s][1:3] = np.maximum(self.q[s][1:3], 0.08)
            self.n_targets += 1
            return {"ok": True}
        if op == "gripper":
            was = self.claw[req["arm"]]
            self.claw[req["arm"]] = float(req.get("pos", 0.0))
            self.opens += int(req["arm"] == "right" and was <= 0.5 < self.claw["right"])
            return {"ok": True}
        if op == "cancel":
            self.going, self.exec = None, "IDLE"
            return {"ok": True}
        if op in ("hold", "bye"):
            return {"ok": True}
        return {"ok": False, "error": f"unknown op {op!r}"}

    def close(self):
        pass


# --- the sequence ------------------------------------------------------------------

class Shared:
    """What the camera's thread and the arm's thread tell each other."""

    def __init__(self):
        self.lock = threading.Lock()
        self.T_base = None               # world <- the cup arm's base, once the seat is found
        self.body_pts = None             # the posed body model, world mm
        self.stage, self.note = "starting", ""
        self.plan = None                 # the Plan being carried out, for the view
        self.holding = False             # a cup is in the claw
        self.stop = threading.Event()    # stop the move in hand, now
        self.quit = threading.Event()    # the run is ending


class _Trouble(Exception):
    pass


class _Quit(Exception):
    pass


class Drink(threading.Thread):
    """The arm's thread: the whole sequence, and the only user of the link."""

    def __init__(self, rig, shared, keys, face, a, yaw_front, q_hand, log=None):
        super().__init__(daemon=True)
        self.rig, self.sh, self.keys, self.face, self.a = rig, shared, keys, face, a
        self.yaw_front, self.q_hand = float(yaw_front), np.asarray(q_hand, float)
        self.q_rest = self.q_hand        # where the cup waits between sips
        self.log = log
        self.trail = []                  # goals reached since the hand-over pose, in order
        self.last_plan = None
        self.level_at = None             # index in trail of the level pose at the mouth
        self.failed = None

    # --- saying and asking ---------------------------------------------------
    def say(self, stage, note=""):
        self.sh.stage, self.sh.note = stage, note
        print(f"  {stage}" + (f": {note}" if note else ""), flush=True)
        if self.log is not None:
            self.log.note("stage", stage=stage, note=note)

    def ask(self, words, keys=("enter",), auto="enter", soon=None, wait_s=None):
        """A pause before the next step. -> the key that ended it, or `auto`.

        By itself it is a COUNTDOWN (`soon`, `wait_s`): the sequence runs hands
        free, as it was asked to, and the keys are only for cutting a wait
        short (Enter), holding it (SPACE), or leaving (q). With --ask, or with
        no `wait_s`, it is a QUESTION that waits for a key, and a loud one: the
        first real runs were left at one, unseen among the libraries' start-up
        chatter, and read as the arm having aimed at nothing and given up."""
        self.sh.note = words
        if self.keys.auto:
            time.sleep(0.05)
            return auto
        timed = wait_s is not None and not self.a.ask
        self.keys.drain()
        bar = "  " + "=" * 74
        if timed:
            self.sh.note = soon or words
            print(f"\n  >> {soon or words}  [{wait_s:.0f} s; Enter: now, SPACE: hold, q: leave]",
                  flush=True)
        else:
            print("", bar, f"  WAITING FOR YOU >> {words}", bar, sep="\n", flush=True)
        end, said = time.monotonic() + (wait_s or 0.0), None
        while True:
            if self.sh.quit.is_set():
                raise _Quit()
            if timed:
                left = end - time.monotonic()
                if left <= 0.0:
                    return auto
                if math.ceil(left) != said:
                    said = math.ceil(left)
                    print(f"     {said}", end=" ", flush=True)
            tok = self.keys.get(0.25)
            if tok is None:
                self.rig.state()             # keeps the drawn arm live, and the link warm
            elif tok == "q":
                raise _Quit()
            elif tok == "space" and timed:
                timed = False                # held: from here it is a question
                self.sh.stop.clear()
                print("", bar, f"  HELD >> {words}", bar, sep="\n", flush=True)
            elif tok in ("+", "-"):
                if self.sh.holding:
                    step = 0.05 if tok == "+" else -0.05
                    pos = min(1.0, max(0.0, (self.rig.claw or 0.0) + step))
                    self.rig.grip(pos)
                    print(f"  claw at {pos:.2f} (0 is shut)", flush=True)
            elif tok in keys:
                return tok

    # --- moving -----------------------------------------------------------------
    def go(self, label, goal, speed, line=True, watch=None, trims=0):
        if self.sh.quit.is_set():
            raise _Quit()
        self.sh.stop.clear()
        if self.sh.holding:
            self.rig.grip(self.rig.claw)          # again: a bridge that restarted opens them
        why = self.rig.move(goal, speed, label, self.sh.stop, line=line, watch=watch,
                            trims=trims)
        if self.log is not None:
            self.log.note("move", label=label, goal=goal, speed=speed, why=why,
                          joints=self.rig.cache.get(self.rig.side))
        if why:
            raise _Trouble(why)

    def retrace(self, k, speed):
        """Back along the goals already reached, newest first, to trail[k].
        -> None, or why not"""
        while self.trail:
            self.sh.stop.clear()
            why = self.rig.move(self.trail[-1], speed, "way back", self.sh.stop, line=False,
                                trims=1)
            if why:
                return why
            if len(self.trail) - 1 <= k:
                return None
            self.trail.pop()
        return None

    def recover(self, why):
        """Something stopped a move. The arm holds; a tipped cup is levelled
        first, since tipped it goes on pouring; then it is for the person at
        the keys to say whether it backs out. The claw stays shut."""
        self.rig.cancel()
        self.say("STOPPED", why)
        if self.level_at is not None:        # at the mouth: it may be part way into a tip
            self.say("levelling the cup")
            bad = self.retrace(self.level_at, self.a.speed_near)
            if bad:
                self.say("COULD NOT LEVEL THE CUP", bad)
        if len(self.trail) > 1:              # else it never left where it waits
            self.ask("Enter: back out the way it came. q: leave the arm where it is",
                     soon="backing out the way it came", wait_s=3.0)
            bad = self.retrace(0, self.a.speed_near)
            if bad:
                self.say("could not back out", bad + ". The arm holds where it is, claw shut")
                raise _Quit()
        self.level_at, self.sh.plan = None, None

    # --- the stages ------------------------------------------------------------
    def check(self):
        self.say("checking the arms")
        busy = self.rig.busy_elsewhere()
        if busy:
            raise _Trouble(busy)
        q, why = self.rig.fresh()
        if why:
            raise _Trouble(why)
        for s in SIDES:
            if min(q[s][1], q[s][2]) < STOP_NEAR_RAD:
                self.say(f"the {s} arm lies on a joint stop", "lifting its claw 5 cm: the "
                         "planner refuses every plan, for both arms, while it does")
                self.rig.lift_off_stop(s)

    def ready(self):
        """To the hand-over pose, claw open. Never extend before turning: a
        scrub that ended at the person leaves the claw pointing at them, and
        unfolding from there sweeps it through their lap. So it is drawn back
        to its own base, turned to the front tucked in, and only then let out
        (arm_dimos.Hardware._stages_for, which learned that on a leg)."""
        self.say("shutting the claw and folding to the front")
        self.rig.grip(0.0)
        q, why = self.rig.fresh()
        if why:
            raise _Trouble(why)
        yaw = q[self.rig.side][0]
        if abs(yaw - self.yaw_front) > FRONT_NEAR_RAD:
            tucked = [list(COMPACT) + [0.0, 0.0, 0.0]] * 2
            self.go("drawing back", [yaw] + tucked[0], SPEED_FREE, line=False)
            try:
                self.go("turn to the front", [self.yaw_front] + tucked[1], SPEED_FREE, line=False)
            except _Trouble as t:
                self.say("the planner refuses the turn", f"{t}; streaming it instead")
                if not self.rig.stream_yaw(self.yaw_front, self.sh.stop):
                    raise
        # Trimmed, though the claw is empty: the cup is stood UPRIGHT in it here,
        # so the claw must really be at the pitch it will carry at, not drooping.
        self.go("reach to the hand-over pose", self.q_hand, SPEED_FREE, line=False, trims=2)
        self.trail = [self.q_hand]
        self.rig.grip(1.0)
        self.say("claw open at the hand-over pose")

    def take_cup(self):
        self.ask("Stand the cup in the claw, upright, gripped low. Enter: shut the claw on it",
                 soon="STAND THE CUP IN THE CLAW NOW, upright, gripped low: it shuts when "
                      "the count ends", wait_s=self.a.cup_s)
        self.rig.grip(self.a.grip)
        self.sh.holding = True
        time.sleep(self.a.settle_s)
        self.say("holding the cup", "+ and - loosen and tighten the claw at any question")

    def aim(self, again=False):
        """Wait for the seat and a fresh mouth, freeze them, plan. -> Plan
        `again`: the cup is already up and hides the mouth, so the aim
        follows the eyes from where it was (Face.follow)."""
        said = 0.0
        while True:
            if self.sh.quit.is_set():
                raise _Quit()
            T = self.sh.T_base
            got = None if T is None else (self.face.follow(time.time()) if again
                                          else self.face.freeze(time.time()))
            if got is not None:
                break
            if time.monotonic() - said > 5.0:
                said = time.monotonic()
                self.say("looking for you", "the floor and the seat first" if T is None
                         else "the mouth: face the camera")
            self.rig.state()
            time.sleep(0.3)
        mouth_w, f_w = got
        mouth_w = mouth_w + self.a.offset
        Ti = np.linalg.inv(T)
        up = Ti[:3, :3] @ UP
        P, why = plan((Ti @ np.r_[mouth_w, 1.0])[:3], Ti[:3, :3] @ f_w, self.a,
                      seed=self.trail[-1], up=up)
        if P is None:
            raise _Trouble(f"cannot bring the cup to the mouth at "
                           f"{np.round(mouth_w).tolist()}: {why}")
        legs = P.legs(self.trail[-1])
        if again:                        # the cup is already at the face: only the arm is checked
            legs[0] = legs[0][:3] + (True,)
        why, gap = check_path(legs, T, self.sh.body_pts, self.a.cup_radius)
        if why:
            raise _Trouble(why)
        P.mouth_w = mouth_w
        self.sh.plan = self.last_plan = P
        note = (f"mouth at {np.round(mouth_w).tolist()} mm, rim stops {self.a.standoff:.0f} mm "
                f"in front of it")
        if P.short_mm:
            note += f" and {P.short_mm:.0f} mm {P.short_way}: bring your mouth to it"
        if P.tilt_deg != self.a.tilt_deg:
            note += f"; the tip is {P.tilt_deg:.0f} degrees, all the wrist has room for"
        if gap is not None:
            note += f"; the arm passes {gap:.0f} mm from the body model at its closest"
        far = float(np.linalg.norm(grasp(P.at)[:3, 3] - grasp(np.r_[TAUGHT_Q[:4], 0, 0])[:3, 3]))
        note += f"; the cup's place is {far:.0f} mm from the one that was shown as right"
        self.say("aimed", note)
        if self.log is not None:
            self.log.note("plan", mouth=mouth_w, facing=f_w, base=T, pre=P.pre, at=P.at,
                          tilts=P.tilts, short_mm=P.short_mm, tilt_deg=P.tilt_deg)
        return P

    def sip(self):
        again = len(self.trail) > 1              # a second sip starts with the cup up
        tries = 0
        while True:
            tries += 1
            P = self.aim(again)
            self.say("carrying the cup to in front of the mouth")
            self.go("swing", P.pre, SPEED_CARRY, trims=2)
            self.trail, again = [self.q_rest, P.pre], True
            # Whoever is at the keys can close the loop the rig file cannot: the
            # cup should stand level with the lips, a hand's length in front.
            jogged = False
            while True:
                tok = self.ask("The cup waits in front of the mouth: LOOK at it. Enter: bring it "
                               "in. 7/1 further/nearer, 4/6 to their right/left, 8/2 up/down, "
                               "then a: carry it there", ("enter", "a") + tuple(JOG),
                               soon="the cup is in front of your mouth and comes in next "
                                    "(7/1 4/6 8/2 jog the aim)", wait_s=self.a.look_s)
                if tok not in JOG:
                    break
                jogged = True
                self.a.offset = self.a.offset + JOG_MM * np.array(JOG[tok], float)
                print(f"  aim moved: --offset {','.join(f'{v:.0f}' for v in self.a.offset)}",
                      flush=True)
            d = self.face.shift(time.time())
            moved = 0.0 if d is None else float(np.linalg.norm(d))
            if tok == "a" or jogged:
                continue
            if tries > AIM_TRIES:
                raise _Trouble(f"the head has not kept still through {AIM_TRIES} aims")
            if moved > RE_AIM_MM:
                self.say("aiming again", f"the head moved {moved:.0f} mm since the cup was aimed")
                continue
            self.say("bringing the cup in", "SPACE stops it")
            seen = {}

            def watch():
                seen["why"] = self.face.trouble(time.time())
                return seen["why"]
            try:
                self.go("approach", P.at, self.a.speed_near, watch=watch, trims=2)
                break
            except _Trouble as t:
                if not seen.get("why") or str(t) != seen["why"]:
                    raise
                # People lean toward a cup that is coming. That is not a fault:
                # the arm stops, draws back to where it waited, and aims again.
                self.rig.cancel()
                self.say("the cup stopped coming in", f"{t}; drawing back to aim again")
                why = self.retrace(1, self.a.speed_near)
                if why:
                    raise _Trouble(why)
        self.trail.append(P.at)
        self.level_at = len(self.trail) - 1
        self.ask("Lean in to the rim. Enter, with the lips on it: tip the cup",
                 soon="LEAN IN to the rim: the cup tips when the count ends", wait_s=self.a.lean_s)
        for i, q in enumerate(P.tilts):
            self.say(f"tipping, step {i + 1} of {len(P.tilts)}", "SPACE levels it")
            self.go(f"tilt {i + 1}", q, self.a.speed_near, trims=1)
            self.trail.append(q)
        self.say("sip", f"holding for {self.a.sip_s:.0f} s")
        end = time.monotonic() + self.a.sip_s
        while time.monotonic() < end:
            if self.sh.stop.is_set() or self.sh.quit.is_set():
                raise _Trouble("stopped by hand")
            time.sleep(0.05)
        self.say("levelling the cup and drawing back")
        why = self.retrace(1, self.a.speed_near)
        if why:
            raise _Trouble(why)
        self.level_at, self.sh.plan = None, None

    def sip_at(self, q_mouth):
        """The cup to a pose that was SHOWN (--pose): no camera, no aiming. The
        joints were read off the arm while it was held where the cup belongs
        at the mouth, so that is where it goes: by a level waiting point 12 cm
        further out along the way to the front, then in, a hold, and back."""
        q_mouth = np.asarray(q_mouth, float)
        if self.a.raise_mm:
            # The shown pose put the bottle at the chin: the same pose, turned
            # the same way, this much higher.
            Tm = grasp(q_mouth)
            q_up, ok = solve_pose(Tm[:3, 3] + UP * self.a.raise_mm, Tm[:3, :3], q_mouth)
            if not ok:
                raise _Trouble(f"{self.a.raise_mm:.0f} mm above the shown pose is out of reach: "
                               f"try a smaller --raise-mm")
            q_mouth = q_up
        g = grasp(np.r_[q_mouth[:4], 0.0, 0.0])[:3, 3]
        out = np.array([math.cos(self.yaw_front), math.sin(self.yaw_front), 0.0])
        q_pre, why = solve_carry(g + out * PRE_MM, math.radians(self.a.elev), self.q_rest)
        if why:
            q_pre = None                         # no level point out there: straight in
        if q_pre is not None:
            self.say("carrying the cup to in front of the mouth")
            self.go("swing", q_pre, self.a.speed, line=False, trims=0)
            self.trail = [self.q_rest, q_pre]
        self.say("bringing the cup to the mouth", "SPACE stops it")
        self.go("approach", q_mouth, self.a.speed * 0.6, line=False, trims=2)
        self.trail.append(q_mouth)
        self.level_at = len(self.trail) - 1
        # The sip: the ARM stays where it is and the claw alone turns the
        # bottle's top toward the mouth, about the level line across the face.
        # As far as the wrist has room for, up to --tilt-deg.
        axis = np.cross(out, UP)
        R0, q_tip = grasp(q_mouth)[:3, :3], None
        for deg in range(int(self.a.tilt_deg), 9, -5):
            q, ok = solve_wrist(rot(axis, math.radians(deg)) @ R0, q_mouth)
            if ok and room(q) >= LIMIT_MARGIN:
                q_tip = q
                break
        if q_tip is None:
            self.say("the wrist has no room to tip the bottle here", "holding it as it is")
        else:
            self.say(f"tipping the bottle {deg} degrees toward the mouth", "the claw only")
            self.go("tilt", q_tip, self.a.speed * 0.6, line=False, trims=1)
            self.trail.append(q_tip)
        self.say("sip", f"holding for {self.a.sip_s:.0f} s")
        end = time.monotonic() + self.a.sip_s
        while time.monotonic() < end:
            if self.sh.stop.is_set() or self.sh.quit.is_set():
                raise _Trouble("stopped by hand")
            time.sleep(0.05)
        self.say("drawing back")
        why = self.retrace(0, self.a.speed)
        if why:
            raise _Trouble(why)
        self.level_at = None

    def give_back(self):
        self.say("taking the cup back to the hand-over pose")
        self.go("way back", self.q_hand, SPEED_CARRY, trims=1)
        self.trail, self.q_rest = [self.q_hand], self.q_hand
        self.ask("Hold the cup. Enter: open the claw",
                 soon="TAKE HOLD OF THE CUP: the claw opens when the count ends",
                 wait_s=self.a.take_s)
        self.rig.grip(1.0)
        self.sh.holding = False
        self.ask("Enter, once the cup is out: shut the claw and fold the arm away",
                 soon="take the cup out: the claw shuts and the arm folds away when the count ends",
                 wait_s=self.a.take_s)
        self.rig.grip(0.0)
        if self.a.pose is not None:
            self.say("done", "the arm is back where it rested, claw shut")
            return
        self.go("fold away", [self.yaw_front] + list(START_POSE) + [0.0, 0.0, 0.0], SPEED_FREE,
                line=False)
        self.say("done", "the arm is at the scrub view's start pose, claw shut")

    def run(self):
        a, how = self.a, ("waits for Enter" if self.a.ask else "counts down")
        cup = "." if a.ask else f", within {a.cup_s:.0f} s."
        print("", f"  WHAT IS ABOUT TO HAPPEN (it {how} before each step)",
              "   1. The arm reaches to your FRONT, claw up and open. It is not aiming at",
              "      you yet: that is where the cup goes in" + cup,
              "   2. The claw shuts, and the arm turns and carries the cup to in front of",
              "      your mouth.",
              f"   3. It comes in slowly and stops {a.standoff:.0f} mm short of your lips. "
              "Lean in.",
              f"   4. It tips the cup {a.tilt_deg:.0f} degrees, holds {a.sip_s:.0f} s, levels, "
              "draws back,",
              "      takes the cup back to where it got it, opens, then folds away as it began.",
              "  SPACE stops the arm at any time (and holds a count). q leaves, claw shut.", "",
              sep="\n", flush=True)
        try:
            try:
                self.check()
                if self.a.holding:
                    # A run that ended with the cup still in the claw. It goes on
                    # from where the arm is, and only if the claw is level there:
                    # from a level pose every carry keeps the cup upright, and
                    # from any other the first move would pour it.
                    self.rig.grip(self.a.grip)
                    q, why = self.rig.fresh()
                    if why:
                        raise _Trouble(why)
                    q = np.array(q[self.rig.side])
                    off = abs(math.degrees(_elevation(grasp(q))) - self.a.elev)
                    if max(abs(q[4]), abs(q[5])) > 0.08 or off > 20.0:   # a real claw droops
                        raise _Trouble("--holding wants the claw level, as it carries: its wrist "
                                       f"is turned {max(abs(q[4]), abs(q[5])):.2f} rad and its "
                                       f"pitch is {off:.0f} degrees off")
                    self.sh.holding, self.trail, self.q_rest = True, [q], q
                    self.say("told a cup is already in the claw", "no hand-over; going on from "
                                                                  "where the arm is")
                elif self.a.pose is not None:
                    # --pose: the bottle is taken where the arm RESTS, and goes
                    # back there. No reach to the front first.
                    q, why = self.rig.fresh()
                    if why:
                        raise _Trouble(why)
                    self.q_rest = self.q_hand = np.array(q[self.rig.side])
                    self.trail = [self.q_rest]
                    self.rig.grip(1.0)
                    self.say("claw open where the arm rests")
                    self.take_cup()
                else:
                    self.ready()
                    self.take_cup()
            except _Trouble as t:
                self.failed = str(t)
                self.rig.cancel()
                self.say("NOT STARTED", f"{t}. Nothing more is moved, the claw is left as it is")
                return
            again = "enter"
            while again == "enter":
                try:
                    if self.a.pose is not None:
                        self.sip_at(self.a.pose)
                    else:
                        self.sip()
                except _Trouble as t:
                    self.failed = str(t)
                    self.recover(str(t))
                again = self.ask("Enter: another sip. r: give the cup back", ("enter", "r"),
                                 auto="r", soon="the cup goes back next (Enter: another sip)",
                                 wait_s=self.a.look_s)
            try:
                self.give_back()
            except _Trouble as t:
                self.failed = str(t)
                self.rig.cancel()
                self.say("STOPPED", f"{t}. The arm holds where it is; --open opens the claw")
        except _Quit:
            self.rig.cancel()
            if self.level_at is not None:
                print("  leaving: levelling the cup first", flush=True)
                self.sh.quit.clear()
                self.retrace(self.level_at, self.a.speed_near)
            self.say("left", "the arm holds where it is" + (
                ", claw SHUT on the cup; --open opens it" if self.sh.holding else ""))
        except Exception:                                       # noqa: BLE001
            traceback.print_exc()
            self.rig.cancel()
            self.say("STOPPED BY A BUG", "the arm holds where it is, claw as it was")
        finally:
            self.sh.quit.set()


class RunLog:
    """What a run did, a line each, beside the scrub's drive logs (git ignored)."""

    def __init__(self):
        folder = os.path.join(HERE, "drive_logs")
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, time.strftime("drink_%Y%m%d_%H%M%S.jsonl"))
        self.f = open(self.path, "a", encoding="utf-8")
        self.lock = threading.Lock()

    def note(self, kind, **kw):
        def plain(v):
            if isinstance(v, np.ndarray):
                return np.round(v, 4).tolist()
            if isinstance(v, (list, tuple)):
                return [plain(x) for x in v]
            return v
        with self.lock:
            self.f.write(json.dumps({"t": round(time.time(), 3), "kind": kind,
                                     **{k: plain(v) for k, v in kw.items()}}) + "\n")
            self.f.flush()

    def close(self):
        self.f.close()


# --- the camera and the view ------------------------------------------------------

def body_points(body):
    """The posed body model as points, world mm: live_body's obstacles()
    without the chair, which the arm's base is clamped to."""
    pts = []
    for name, p in body.parts.items():
        T = body.T.get(name)
        if T is None:
            continue
        s = body.S.get(name, 1.0)
        pts.append((p["V"] * np.array([1.0, 1.0, s])) @ T[:3, :3].T + T[:3, 3])
    return np.vstack(pts).astype(float) if pts else None


def seen_points(depth, mask, intr, T_wc, base, step=6):
    """What the camera's depth shows around the arm's base, the person left
    out: the real arm, so a drawn arm that does not lie on it is a wrong
    base. live_body's live surface is the person only."""
    z = depth[::step, ::step]
    ok = (z > 200.0) & ~mask[::step, ::step]
    v, u = np.nonzero(ok)
    zz = z[v, u]
    P = np.stack([(u * step - intr["ppx"]) * zz / intr["fx"],
                  (v * step - intr["ppy"]) * zz / intr["fy"], zz], 1)
    P = P @ T_wc[:3, :3].T + T_wc[:3, 3]
    near = (np.hypot(P[:, 0] - base[0], P[:, 1] - base[1]) < 750.0) \
        & (P[:, 2] > base[2] - 100.0) & (P[:, 2] < base[2] + 1000.0)
    return P[near]


def cup_mesh(T_world_grasp, axis_g, a, n=20):
    """The cup on the grasp frame. -> (vertices, triangles), world mm"""
    R, g = T_world_grasp[:3, :3], T_world_grasp[:3, 3]
    ax = R @ axis_g
    u = np.cross(ax, [1.0, 0.0, 0.0])
    if np.linalg.norm(u) < 0.2:
        u = np.cross(ax, [0.0, 1.0, 0.0])
    u = u / np.linalg.norm(u)
    w = np.cross(ax, u)
    ang = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    ring = np.outer(np.cos(ang), u) + np.outer(np.sin(ang), w)
    top = g + ax * a.rim_above_grip + ring * a.cup_radius
    bot = g - ax * (a.cup_height - a.rim_above_grip) + ring * a.cup_radius * 0.85
    V = np.vstack([top, bot, [g - ax * (a.cup_height - a.rim_above_grip)]])
    F = []
    for i in range(n):
        j = (i + 1) % n
        F += [[i, n + i, j], [j, n + i, n + j], [n + i, 2 * n, n + j]]
    return V.astype(np.float32), np.array(F, np.uint32)


def run_live(a, link, yaw_of):
    """The camera (or a recording), the view, and the arm's thread."""
    import cv2
    import rerun as rr
    import live_body as LB
    import viz as VIZ
    from arms_live import base_pose

    LB.LM.update({"mouth_l": 9, "mouth_r": 10})      # read at call time by Live.step()
    with open(a.rig, encoding="utf-8") as f:
        rel = json.load(f)["arms"]
    idx = SIDES.index(a.arm)
    if len(rel) < 2 or rel[idx].get("side", a.arm) != a.arm:
        raise SystemExit(f"{a.rig}: entry {idx} is not the {a.arm} arm (a0 left, a1 right)")
    T0 = base_pose(rel[idx])
    tip = math.degrees(math.acos(max(-1.0, min(1.0, float((T0[:3, :3].T @ UP)[2])))))
    if tip > 2.0:
        raise SystemExit(f"the rig file tips this arm's base {tip:.0f} degrees: the cup is only "
                         f"kept upright as the arm turns on a level base")
    yaw_front, q_hand = yaw_of(T0)

    intr, frames = (LB.replay_source(a.replay, loop=True, fps=9.0) if a.replay
                    else LB.camera_source(a.upside_down))
    live = LB.Live(intr, a.seat_mm, True)            # the floor is held once found
    # The less room is left in front of the lips, the less the head may move.
    sh, face = Shared(), Face(a.face_deg, min(a.head_moved_mm, max(20.0, 0.6 * a.standoff)))
    keys = Keys(sh.stop, auto=a.auto)
    rig = Rig(link, a.arm)
    log = RunLog() if not a.check else None
    worker = None
    if a.check:
        def poll():
            while not sh.quit.is_set():
                rig.state()
                time.sleep(0.2)
        threading.Thread(target=poll, daemon=True).start()
        print("  --check: reading the arms and drawing them; nothing is moved. Ctrl+C leaves.\n"
              "  The grey points are what the camera sees around the arm. If the drawn arm is\n"
              "  off them: 7/1 move its base the way the person faces/back, 4/6 to their\n"
              "  right/left, 8/2 up/down, 10 mm a press; pass what is printed as --base-nudge",
              flush=True)
    else:
        worker = Drink(rig, sh, keys, face, a, yaw_front, q_hand, log)
        worker.start()
        print(f"  what this run does goes to {log.path}", flush=True)

    view = not a.no_viewer
    if view:
        rr.init("scrubby3d_drink", spawn=False)
        if not a.no_spawn:
            rr.spawn(port=a.viewer_port, connect=False)
        rr.connect_grpc(f"rerun+http://127.0.0.1:{a.viewer_port}/proxy")
        rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)
        rr.send_blueprint(LB.blueprint())
    layout, drawn, first, last_img, last_txt, last_body = None, False, None, 0.0, 0.0, 0.0
    try:
        for color, depth, t_frame in frames:
            if a.replay:                             # a recording on its own clock
                first = first or (t_frame, time.time())
                ahead = (t_frame - first[0]) - (time.time() - first[1])
                if ahead > 0:
                    time.sleep(ahead)
            if sh.quit.is_set() and (worker is None or not worker.is_alive()):
                break
            now = time.time()
            ev = live.step(color, depth, t_frame)
            if "frame" in ev and layout is not None:
                layout, sh.T_base = None, None       # the world moved under the plan
                sh.stop.set()
                print("  the floor was fitted again: the arm stops, the aim starts over",
                      flush=True)
            if "seat" in ev:
                sx, sy = (float(v) for v in live.seat["xy"])
                layout = [base_pose(r, sx, sy) for r in rel[:2]]
                layout[idx][:3, 3] += a.base_nudge
                sh.T_base = layout[idx]
                print(f"  the seat is found: the {a.arm} arm's base is at "
                      f"{np.round(layout[idx][:3, 3]).tolist()} mm", flush=True)
            if "posed" in ev:
                face.add(live.J, now)
                if now - last_body > 1.0:
                    last_body, sh.body_pts = now, body_points(live.body)
            while a.check and layout is not None and not keys.q.empty():
                tok = keys.q.get_nowait()          # --check: the jog keys move the base
                if tok in JOG:
                    step = 10.0 * np.array(JOG[tok], float)
                    a.base_nudge = a.base_nudge + step
                    layout[idx][:3, 3] += step
                    print(f"  --base-nudge {','.join(f'{v:.0f}' for v in a.base_nudge)}",
                          flush=True)
            if not view:
                continue
            if now - last_img > 0.3:
                last_img = now
                small = cv2.resize(color, (320, 180), interpolation=cv2.INTER_AREA)
                rr.log("camera", rr.Image(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)), static=True)
            if now - last_txt > 0.5:
                last_txt = now
                rr.log("measurements", rr.TextDocument(
                    (LB.waiting_note(live) if live.T_wc is None else "")
                    + f"## Bringing the cup\n\n**{sh.stage}**\n\n{sh.note}\n\n"
                      f"Enter goes on. SPACE stops the arm. q leaves, claw shut.",
                    media_type=rr.MediaType.MARKDOWN), static=True)
            if layout is not None:
                if not drawn:
                    drawn = True
                    for k in range(2):
                        for name, (V, F, N) in YAM.meshes().items():
                            rr.log(f"world/arms/arm_{k}/links/{name}", rr.Mesh3D(
                                vertex_positions=V, triangle_indices=F, vertex_normals=N,
                                albedo_factor=YAM.colour(name, VIZ.ARM_COLOURS[k % 4])),
                                static=True)
                for k, s in enumerate(SIDES):
                    q6 = rig.cache.get(s)
                    if q6 is None:
                        continue
                    tf = YAM.link_transforms(q6, T_world_base=layout[k])
                    for name in YAM.links():
                        rr.log(f"world/arms/arm_{k}/links/{name}", VIZ._tf(tf[name]), static=True)
                    if s == a.arm and sh.holding:
                        ag = (sh.plan.axis_g if sh.plan is not None
                              else grasp(q_hand)[:3, :3].T @ UP)
                        V, F = cup_mesh(tf[YAM.TOOL], ag, a)
                        rr.log("world/arms/drink/cup", rr.Mesh3D(
                            vertex_positions=V, triangle_indices=F,
                            albedo_factor=(250, 214, 100)), static=True)
                if live.T_wc is not None and live.mask is not None:
                    S = seen_points(depth, live.mask, intr, live.T_wc, layout[idx][:3, 3])
                    rr.log("world/arms/drink/seen", rr.Points3D(
                        S, colors=[(190, 196, 206)], radii=3.0), static=True)
            if not sh.holding:
                rr.log("world/arms/drink/cup", rr.Clear(recursive=False), static=True)
            P, got = sh.plan, face.target(now)
            if got is not None:
                rr.log("world/arms/drink/mouth", rr.Points3D(
                    [got[0] + a.offset], colors=[(255, 70, 120)], radii=12.0), static=True)
            if P is not None and sh.T_base is not None:
                T = sh.T_base
                way = np.array([P.g_pre, P.g_at] + P.g_tilts) @ T[:3, :3].T + T[:3, 3]
                rr.log("world/arms/drink/way", rr.LineStrips3D(
                    [way], colors=[(250, 214, 100)], radii=3.0), static=True)
                rr.log("world/arms/drink/lip", rr.Points3D(
                    [T[:3, :3] @ P.lip + T[:3, 3]], colors=[(255, 255, 255)], radii=7.0),
                    static=True)
            if "posed" not in ev:
                continue
            mesh = LB.surface(depth, color, live.mask, intr, live.T_wc)
            if mesh is not None:
                Pm, Fm, Nm, Cm = mesh
                rr.log("world/live", rr.Mesh3D(vertex_positions=Pm,
                                               triangle_indices=Fm.astype(np.uint32),
                                               vertex_normals=Nm, vertex_colors=Cm), static=True)
            PJ = live.body.joints
            segs = [np.stack([PJ[x], PJ[y]]) for x, y in LB.BONES if x in PJ and y in PJ]
            if segs:
                rr.log("world/skeleton", rr.LineStrips3D(segs, colors=[(255, 255, 255)],
                                                         radii=6.0), static=True)
    except KeyboardInterrupt:
        print("  Ctrl+C: stopping the arm; the claw stays as it is", flush=True)
    finally:
        sh.stop.set()
        sh.quit.set()
        # Each on its own, and a second Ctrl+C does not skip the rest: the first
        # real run left by one that landed inside the camera's own shutdown.
        steps = [lambda: worker is not None and worker.join(timeout=40.0),   # cancels, levels
                 frames.close, live.close, link.close,
                 lambda: log is not None and log.close(),
                 lambda: view and rr.disconnect()]
        for step in steps:
            try:
                step()
            except (Exception, KeyboardInterrupt):               # noqa: BLE001
                pass
    return 0 if worker is None or worker.failed is None else 1


def run_pose(a, link):
    """--pose: the arm's thread alone. No camera, no view, nothing to aim."""
    with open(a.pose, encoding="utf-8") as f:
        a.pose = np.array(json.load(f)[a.arm], float)
    a.cup_s, a.take_s = min(a.cup_s, 5.0), min(a.take_s, 5.0)
    if room(a.pose) < LIMIT_MARGIN:
        raise SystemExit(f"the shown pose sits {room(a.pose):.2f} rad from a joint stop")
    with open(a.rig, encoding="utf-8") as f:
        rel = json.load(f)["arms"]
    face = math.radians(float(rel[SIDES.index(a.arm)]["facing_deg"]))
    T0 = np.eye(4)
    T0[:3, :3] = rot(UP, face)
    yaw_front, q_hand = hand_over(T0, a)
    sh = Shared()
    keys = Keys(sh.stop, auto=a.auto)
    log = RunLog()
    print(f"  going to the shown pose {np.round(a.pose, 3).tolist()}; log: {log.path}", flush=True)
    w = Drink(Rig(link, a.arm), sh, keys, Face(), a, yaw_front, q_hand, log)
    w.start()
    try:
        while w.is_alive():
            w.join(timeout=0.2)
    except KeyboardInterrupt:
        print("  Ctrl+C: stopping the arm; the claw stays as it is", flush=True)
        sh.stop.set()
        sh.quit.set()
        w.join(timeout=40.0)
    link.close()
    log.close()
    return 0 if w.failed is None else 1


# --- starting ------------------------------------------------------------------------

def connect(a):
    """The bridge, or nothing (--dry). Real arms only when --real says so."""
    if a.dry:
        print("  DRY RUN: no bridge, no arm; the joints are simulated", flush=True)
        return DryLink()
    endpoint = a.endpoint or os.environ.get(ENV, DEFAULT_ENDPOINT)
    try:
        link = Link(endpoint)
    except OSError as exc:
        raise SystemExit(f"dimOS bridge at {endpoint} not reachable ({exc}). Set {ENV}=host:port, "
                         f"or use --dry")
    info = link.call({"op": "info"}, timeout=5.0)
    if not info.get("ok") or sorted(info.get("arms", [])) != ["left", "right"]:
        link.close()
        raise SystemExit(f"dimOS bridge at {endpoint}: {info.get('error') or info}")
    real = info.get("mock") is not True
    print(f"  bridge at {endpoint}: " + ("REAL ARMS" if real else "dimOS's mock arms"), flush=True)
    if real and not a.real and not a.check:
        link.close()
        raise SystemExit("these are real arms, and this moves one to a person's face: say --real")
    if a.replay and real and not a.check:
        link.close()
        raise SystemExit("a recording is not the person in the chair: --replay goes with --dry")
    return link


def hand_over(T_base0, a):
    """-> (yaw of the person's front in this arm's frame, the hand-over joints)"""
    fb = T_base0[:3, :3].T @ np.array([1.0, 0.0, 0.0])
    yaw = math.atan2(fb[1], fb[0])
    r, z = a.handover
    q, why = solve_carry([r * math.cos(yaw), r * math.sin(yaw), z], math.radians(a.elev))
    if why:
        raise SystemExit(f"the hand-over pose ({r:.0f} mm out, {z:.0f} up, claw {a.elev:.0f} "
                         f"degrees up) is {why}: try --handover 420,520")
    return yaw, q


def _triple(text):
    v = [float(x) for x in str(text).split(",")]
    if len(v) != 3:
        raise argparse.ArgumentTypeError("wants x,y,z")
    return np.array(v)


def _pair(text):
    v = [float(x) for x in str(text).split(",")]
    if len(v) != 2:
        raise argparse.ArgumentTypeError("wants r,z")
    return v


def parser():
    ap = argparse.ArgumentParser(description="bring a cup to the person's mouth and tip it")
    ap.add_argument("--real", action="store_true", help="yes, move the REAL arm")
    ap.add_argument("--dry", action="store_true", help="no bridge: simulated joints")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="read the arms and draw them over the camera's depth; move nothing")
    ap.add_argument("--open", action="store_true", help="open this arm's claw, and leave")
    ap.add_argument("--close", action="store_true", help="shut this arm's claw, and leave")
    ap.add_argument("--holding", action="store_true",
                    help="a cup is already shut in the claw: no hand-over, never open it first")
    ap.add_argument("--pose", nargs="?", const=POSE_FILE, default=None,
                    help="no camera: take the cup to the six joints in this file (read off "
                         "the arm while it was held at the mouth; --learn-pose writes it)")
    ap.add_argument("--raise-mm", type=float, default=50.0,
                    help="--pose: go this much higher than the shown pose (chin to mouth)")
    ap.add_argument("--speed", type=float, default=0.5,
                    help="--pose: planner speed scale for the carry (it comes in at 0.6 of it)")
    ap.add_argument("--learn-pose", action="store_true",
                    help="read this arm's joints NOW and keep them as the pose at the mouth")
    ap.add_argument("--auto", action="store_true",
                    help="no waits at all (dry runs only)")
    ap.add_argument("--ask", action="store_true",
                    help="wait for Enter before each step, instead of counting down to it")
    ap.add_argument("--cup-s", type=float, default=CUP_S,
                    help="s the open claw waits for the cup before it shuts")
    ap.add_argument("--look-s", type=float, default=LOOK_S,
                    help="s the cup waits in front of the mouth before it comes in")
    ap.add_argument("--lean-s", type=float, default=LEAN_S,
                    help="s at the lips, to lean in, before the cup tips")
    ap.add_argument("--take-s", type=float, default=TAKE_S,
                    help="s to take hold of the cup before the claw opens, and to take it out")
    ap.add_argument("--arm", choices=SIDES, default="right", help="the PERSON's right or left")
    ap.add_argument("--endpoint", default=None, help=f"host:port (default ${ENV})")
    ap.add_argument("--standoff", type=float, default=STANDOFF_MM,
                    help="mm the rim stops in front of the lips")
    ap.add_argument("--grip", type=float, default=0.0, help="claw on the cup: 0 shut .. 1 open")
    ap.add_argument("--cup-radius", type=float, default=CUP_R_MM)
    ap.add_argument("--rim-above-grip", type=float, default=CUP_RIM_MM)
    ap.add_argument("--cup-height", type=float, default=CUP_H_MM)
    ap.add_argument("--elev", type=float, default=ELEV_DEG, help="the claw's pitch, degrees up")
    ap.add_argument("--tilt-deg", type=float, default=TILT_DEG)
    ap.add_argument("--sip-s", type=float, default=4.0)
    ap.add_argument("--speed-near", type=float, default=SPEED_NEAR,
                    help="planner speed scale at the face")
    ap.add_argument("--offset", type=_triple, default=np.array(TAUGHT_OFFSET),
                    help="x,y,z mm added to the mouth (world: x the way they face, y their left)")
    ap.add_argument("--base-nudge", type=_triple, default=np.zeros(3),
                    help="x,y,z mm added to this arm's base, when the drawn arm is off the "
                         "real one")
    ap.add_argument("--head-moved-mm", type=float, default=HEAD_MOVED_MM,
                    help="coming in, the head moving this far since the aim stops the arm")
    ap.add_argument("--face-deg", type=float, default=None,
                    help="which way the face looks, degrees to their left of straight ahead")
    ap.add_argument("--handover", type=_pair, default=[HAND_R_MM, HAND_Z_MM],
                    help="r,z mm: the hand-over point out from and above the base")
    ap.add_argument("--rig", default=os.path.join(HERE, "live_rig_openyam.json"))
    ap.add_argument("--replay", help="a folder written by live_body.py --dump")
    ap.add_argument("--seat-mm", type=float, default=None)
    ap.add_argument("--upside-down", action="store_true")
    ap.add_argument("--no-viewer", action="store_true")
    ap.add_argument("--no-spawn", action="store_true",
                    help="send to a viewer already listening on --viewer-port")
    ap.add_argument("--viewer-port", type=int, default=9876)
    return ap


def main():
    a = parser().parse_args()
    a.settle_s = SETTLE_S
    if a.selftest:
        return self_test()
    if a.auto and not a.dry:
        raise SystemExit("--auto answers the questions that keep a person safe: --dry only")
    link = connect(a)
    if a.learn_pose:
        rep = link.call({"op": "state"}, timeout=3.0)
        q = {s_: d["joints"] for s_, d in (rep.get("arms") or {}).items()}
        with open(POSE_FILE, "w", encoding="utf-8") as f:
            json.dump(q, f)
        print(f"  kept as the pose at the mouth: {np.round(q[a.arm], 3).tolist()}", flush=True)
        link.close()
        return 0
    if a.pose is not None:
        return run_pose(a, link)
    if a.open or a.close:
        ok = Rig(link, a.arm).grip(1.0 if a.open else 0.0)
        print(f"  the {a.arm} claw was told to " + ("open" if a.open else "shut")
              + ("" if ok else ": NOT confirmed"), flush=True)
        link.close()
        return 0 if ok else 1
    return run_live(a, link, lambda T0: hand_over(T0, a))


# --- does it hold together, with nothing connected ------------------------------------

def self_test():
    a = parser().parse_args([])
    a.settle_s, a.sip_s, a.auto = 0.05, 0.2, True
    elev = math.radians(a.elev)
    print("drink: the maths")
    assert LIMITS.shape == (6, 2) and abs(LIMITS[3, 0] + 1.69297) < 1e-4, LIMITS
    worst_p = worst_e = 0.0
    poses = []
    for yaw in (-1.57, -0.6, 0.1):
        for r in (300.0, 400.0, 480.0):
            for z in (500.0, 560.0, 620.0):
                p = np.array([r * math.cos(yaw), r * math.sin(yaw), z])
                q, why = solve_carry(p, elev)
                assert why is None, (p, why)
                T = grasp(q)
                worst_p = max(worst_p, float(np.linalg.norm(T[:3, 3] - p)))
                worst_e = max(worst_e, abs(math.degrees(_elevation(T) - elev)))
                assert q[4] == 0.0 and q[5] == 0.0
                poses.append(q)
    print(f"  carry poses across the band: within {worst_p:.2f} mm and {worst_e:.3f} degrees")
    assert worst_p < 0.5 and worst_e < 0.1
    axis_g = grasp(poses[0])[:3, :3].T @ UP
    worst = 0.0
    for q0, q1 in zip(poses[:-1], poses[1:]):
        for k in np.linspace(0.0, 1.0, 9):
            ax = grasp(q0 + (q1 - q0) * k)[:3, :3] @ axis_g
            worst = max(worst, math.degrees(math.acos(max(-1.0, min(1.0, float(ax[2]))))))
    print(f"  between them, joints in straight lines: the cup is off upright by "
          f"{worst:.3f} degrees")
    assert worst < 0.1

    # The person on live_rec_20260917f300, in the right arm's base frame.
    mouth, f = np.array([460.0, 125.0, 629.0]), np.array([0.0, -1.0, 0.0])
    for standoff in (100.0, 40.0):
        a.standoff = standoff
        P, why = plan(mouth, f, a)
        assert P is not None and P.short_mm == 0.0 and P.tilt_deg == 45.0, why
        top = grasp(P.tilts[-1])[:3, :3] @ P.axis_g
        lean = math.degrees(math.atan2(float(top @ -f), float(top[2])))
        rooms = min(room(q) for q in [P.at] + P.tilts)
        print(f"  standoff {standoff:.0f}: {len(P.tilts)} tilt steps, the cup ends {lean:.1f} "
              f"degrees toward the face, lip point strays {P.lip_drift():.1f} mm, "
              f"{rooms:.2f} rad off the nearest limit")
        assert abs(lean - 45.0) < 0.2 and P.lip_drift() < 3.0 and rooms >= FACE_ROOM
        assert abs(float(top @ np.cross(f, UP))) < 1e-3           # and not sideways
    a.standoff = STANDOFF_MM
    P, why = plan(mouth + [0.0, 0.0, 60.0], f, a)
    print("  a mouth 60 mm higher: " + (why if P is None else
          f"stops {P.short_mm:.0f} mm {P.short_way}, tips {P.tilt_deg:.0f} degrees"))
    assert P is not None and (P.short_mm > 0.0 or P.tilt_deg < 45.0)
    P, why = plan(mouth + [0.0, 0.0, 260.0], f, a)
    print(f"  a mouth 260 mm higher: refused ({why})")
    assert P is None and "reach" in why
    a.standoff = 600.0
    P, why = plan(mouth, f, a)
    print(f"  asked to stand 600 mm off: refused, and says what would do ({why})")
    assert P is None and "--standoff" in why
    a.standoff = STANDOFF_MM

    print("drink: the sequence, on simulated joints")
    T_base = np.eye(4)
    T_base[:3, :3] = rot(UP, math.pi / 2.0)          # the right arm: its +x is the person's left
    T_base[:3, 3] = (13.0, -442.0, 475.0)
    mouth_w = (T_base @ np.r_[mouth, 1.0])[:3]
    yaw_front, q_hand = hand_over(T_base, a)
    assert abs(yaw_front + math.pi / 2.0) < 1e-6

    def run(stop_at=None, holding=False, **dry):
        link = DryLink(rate=40.0, **dry)
        sh, face = Shared(), Face()
        aa = argparse.Namespace(**vars(a))
        aa.holding = holding
        sh.T_base = T_base
        rig = Rig(link, "right")
        rig.POLL_S, rig.GRACE_S = 0.01, 0.03
        rig.busy_elsewhere = lambda: None            # its one second wait, not its logic
        J = {"mouth_l": mouth_w + [0, 27, 0], "mouth_r": mouth_w - [0, 27, 0],
             "l_eye": mouth_w + [10, 31, 65], "r_eye": mouth_w + [10, -31, 65],
             "l_shoulder": mouth_w + [-90, 190, -140], "r_shoulder": mouth_w + [-90, -190, -140]}

        def feed():
            while not sh.quit.is_set():
                face.add(J, time.time())
                if stop_at and sh.stage.startswith(stop_at):
                    sh.stop.set()
                time.sleep(0.01)
        for _ in range(Face.WANT):
            face.add(J, time.time())
        threading.Thread(target=feed, daemon=True).start()
        w = Drink(rig, sh, Keys(sh.stop, auto=True), face, aa, yaw_front, q_hand)
        w.start()
        w.join(timeout=120.0)
        assert not w.is_alive(), "the sequence did not end"
        return link, w

    def homes(link):
        return [(r, q) for r, q in link.log if r.get("op") == "home"]

    link, w = run()
    assert w.failed is None, w.failed
    hs = homes(link)
    assert len(hs) >= 10, len(hs)
    for r, q in hs:
        assert sorted(r["joints"]) == ["left", "right"] and r["close"] is False, r
        assert np.allclose(r["joints"]["left"], q["left"], atol=1e-9), "the left arm was not pinned"
    claws = [r["pos"] for r, _ in link.log if r.get("op") == "gripper"]
    assert claws[0] == 0.0 and claws[1] == 1.0 and claws[-2:] == [1.0, 0.0], claws
    assert set(claws[2:-2]) == {a.grip}, claws
    def off_upright(r):
        return math.degrees(math.acos(max(-1.0, min(1.0, float(
            (grasp(r["joints"]["right"])[:3, :3] @ axis_g)[2])))))

    def with_cup(link):
        """The planned moves made between the claw shutting on the cup and
        opening again: only there does the cup's axis mean anything."""
        out, opened = [], 0
        for i, (r, _) in enumerate(link.log):
            if r.get("op") == "gripper" and r["pos"] == 1.0:
                opened += 1
            elif r.get("op") == "home" and opened == 1:
                out.append((i, r))
        return out

    carried = [off_upright(r) for _, r in with_cup(link)]
    tipped = max(carried)
    assert carried[0] < 0.1 and carried[-1] < 0.1, carried
    end = link.read("right")
    print(f"  a whole run: {len(hs)} planned moves, the left arm pinned in every one, the claws "
          f"never shut by a plan; the cup tipped to {tipped:.1f} degrees and came back")
    assert abs(tipped - 45.0) < 0.5
    assert np.allclose(end, [yaw_front] + list(START_POSE) + [0, 0, 0], atol=0.02), end

    link, w = run(stop_at="tipping, step 2")
    i_stop = next(i for i, (r, _) in enumerate(link.log) if r.get("op") == "cancel")
    before = [off_upright(r) for i, r in with_cup(link) if i < i_stop]
    after = [off_upright(r) for i, r in with_cup(link) if i > i_stop]
    assert max(before) > 25.0, before                 # it was stopped on the way to 30 degrees
    assert after and after[0] < max(before) and after[-1] < 0.1, after
    assert all(b <= c + 0.1 for b, c in zip(after[1:], after[:-1])), \
        f"after a stop while tipping the cup must only get more level: {after}"
    opened = [r for r, _ in link.log[i_stop:] if r.get("op") == "gripper" and r["pos"] > a.grip]
    assert len(opened) == 1, opened                   # only the hand-back, asked for by a key
    print("  stopped while tipping: the move was cancelled, the cup levelled, the arm backed out, "
          "and the claw opened only when the cup was given back")

    # An arm that gives under its own weight, by what the first real run read
    # at the hand-over pose, and as much again with a cup in the claw.
    gave = [0.0, 0.013, -0.07, -0.136, 0.0, 0.0]
    link, w = run(sag=gave, sag_cup=[0.0, 0.01, -0.05, -0.10, 0.0, 0.0])
    assert w.failed is None, w.failed
    P = w.last_plan
    tilt1 = next(i for i, (r, _) in enumerate(link.log) if r.get("op") == "home"
                 and abs(r["joints"]["right"][5]) > 0.05)
    at_mouth = link.log[tilt1][1]["right"]            # where it READ when the tip was asked for
    short = float(np.abs(at_mouth - P.at).max())
    lean = math.degrees(math.acos(max(-1.0, min(1.0, float(
        (grasp(at_mouth)[:3, :3] @ P.axis_g)[2])))))
    low = float(np.linalg.norm(grasp(at_mouth)[:3, 3] - P.g_at))
    unfixed = grasp(P.at + np.array(gave) * 1.75)
    print(f"  an arm that sags as the real one did: at the mouth it reads {short:.3f} rad from "
          f"the plan, the cup {low:.0f} mm off and {lean:.1f} degrees off upright (uncorrected: "
          f"{np.linalg.norm(unfixed[:3, 3] - P.g_at):.0f} mm and "
          f"{math.degrees(math.acos(float((unfixed[:3, :3] @ P.axis_g)[2]))):.0f} degrees)")
    assert short < ARRIVE_RAD and lean < 3.0 and low < 15.0, (short, lean, low)

    link, w = run(holding=True)                       # the simulated wrist starts bent
    assert w.failed and "level" in w.failed and not homes(link), w.failed
    level = {"left": DryLink.START["left"], "right": [float(v) for v in q_hand]}
    link, w = run(holding=True, start=level)
    claws = [r["pos"] for r, _ in link.log if r.get("op") == "gripper"]
    assert w.failed is None and claws.count(1.0) == 1 and claws[0] == a.grip, (w.failed, claws)
    print("  --holding: refused with a bent wrist; from a level claw it goes on and the claw is "
          "opened only to give the cup back")

    link, w = run(refuse_turns=True)
    assert w.failed is None, w.failed
    assert any(r.get("op") == "target" for r, _ in link.log)
    print("  a turn the planner refuses is streamed instead, and the run goes on")
    print("  drink: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
