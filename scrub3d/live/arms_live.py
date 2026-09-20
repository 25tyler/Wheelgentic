"""The four arms on the live model: planned, phased, and every move governed.

PLANNING is partition.solve, exactly as main.py plans a scan: who scrubs what,
and PHASES. Arms whose territories interlock, or whose working poses would
meet, are put in different phases and never move at the same time; the
others wait parked.

MOTION is strokes. Each arm's patch is scrubbed in rows ROW_GAP_MM apart,
laid out in the body part's own frame (along a limb, around it), so the rows
move with the person: a plan is a list of cells, and every target is re-read
from where those cells are now. A turn from one row to the next stays on the
skin; the sponge lifts only over a hole in the patch or to go to another
patch, and patches too small to be worth the flight are left alone.

EVERY STEP IS PROPOSED FIRST to fleet.FleetGovernor, the project's one route
to an arm, before the arm takes it:
  - HOLD when the step would bring the arm within D_HOLD of another arm, and
    a hold that lasts past HOLD_MAX_S becomes a retreat;
  - REFUSE when the arm's structure would come within D_BODY of the person,
    or the sponge would go above the head. The limb being scrubbed may come
    closer, as partition.py allows, but never within D_LIMB_MM: exempting it
    entirely let an arm's links pass through a limb that moved into them;
  - ESTOP when two arms are already inside D_ESTOP, which should never happen.
Moving away from the person or another arm is always allowed. A refused
point is skipped and counted, never forced.

A person moves, and a waiting arm does not: so a waiting arm that the person
comes too close to backs away, through the governor like any other move.

THE DEPTH GUARD. Everything above is checked against the body model, and a
model that is off makes every one of those checks off with it. So, on top:
  - an arm never closes on the person faster than a braking curve allows
    (allowed_close): V_TOUCH for the sponge and V_CLOSE for the rest of the
    arm once within SLOW_BAND_MM, and no faster further out than an arm
    braking at A_BRAKE could shed before that band. The distance is to the
    model's surface or to what the depth camera measured (depth_guard),
    whichever is nearer. A model up to SLOW_BAND_MM off still meets the
    person at those speeds, and one up to about twice that at under 250
    mm/s, the speed industrial standards call reduced;
  - off the skin, within NEAR_MM of the person, the sponge moves at no more
    than V_NEAR and the rest of the arm at no more than V_NEAR_ARM, however
    it moves (a model that is off turns a flight past the skin into a
    graze);
  - the sponge never presses more than MAX_PRESS_MM into the model;
  - no move may bring an arm's moving structure into what the camera
    measured of the person, unless it backs away: the points inside the
    person mask (segmentation, a second detector), and any within
    PERSON_PAD_MM of the model (so neither detector alone can miss them);
  - when the camera does not agree with the model of any scrubbed part
    (AGREE_MM, AGREE_OFF), no part is touched until it agrees again, and an
    arm on the skin lifts off: a fit that is off for one part is seldom right
    for the rest;
  - with the person out of view for LOST_S, an arm on the skin lifts off,
    slowly, and all of them hold;
  - no step moves the sponge faster than MAX_TOOL_V, what the real arms'
    driver can send;
  - driving real arms, one more than LEAD_MM behind its plan is waited for,
    so the real arm keeps to the path that was checked.

Nothing here talks to hardware. It is a picture of what the arms would do.
"""
import collections
import heapq
import itertools
import math
import os
import sys
import threading
import time

import numpy as np
import rerun as rr
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.dirname(HERE)          # scrub3d/: its modules import by bare name
for p in (WT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import armmesh                                  # noqa: E402
import collide as COL
import depth_guard as DG
import control as CTL
import fleet as FLEET
import kinematics as K
import partition as PART
import place_arms as PA
import viz as VIZ

HOME_J = K.ik(*VIZ.HOME_TCP)
# THE START POSE. Each arm starts, waits and goes back to HOME's reach and
# height, its base joint turned `start_deg` from straight ahead (positive to
# the arm's left), as the rig file says; within START_MAX_DEG of ahead.
START_MAX_DEG = 88.0


def start_tcp(start_deg=0.0):
    """The start pose's tool point, in the arm's own frame. -> (3,)"""
    s = math.radians(max(-START_MAX_DEG, min(START_MAX_DEG, float(start_deg))))
    r = VIZ.HOME_TCP[0] - K.BASE_X_MM
    return np.array([K.BASE_X_MM + r * math.cos(s), r * math.sin(s), VIZ.HOME_TCP[2]])


def start_joints(start_deg=0.0):
    return K.ik(*start_tcp(start_deg))
PASS_PAUSE_S = 1.0        # between one finished pass and the next
RETURN_V = 450.0          # mm/s, flying home through free air
AIR_V = 450.0             # mm/s, lifted, in a straight line
LIFT_OFF_MM = 80.0        # straight out from the skin before heading home
# Above the skin between strokes (the sponge's surface 40 mm up): low enough
# to hover where a limb leaves little room, and a short climb.
LIFT_MM = 30.0
# Off the skin when the person goes out of view. Away from them, so no
# closing limit applies; the sooner it is off, the less they can lean into
# a sponge that is holding still.
LIFT_OFF_V = 250.0
RETURN_TIMEOUT_S = 5.0    # a return the governor keeps holding is parked by hand
TREE_EVERY = 3            # frames between rebuilding the body's obstacle trees
# Re-checking which cells are safe costs about 180 ms of a core, so it is
# done no more than every RECHECK_S, and only when the person has moved
# RECHECK_MM since it was last done: holding still, nothing has changed.
RECHECK_S = 1.0
RECHECK_MM = 8.0
# The territories are solved for the person as they were posed then. Once
# the arms can reach REPLAN_FRAC of what they could when it was solved, and
# no sooner than REPLAN_MIN_S after, they are solved again: a person who has
# moved their arms since leaves one arm with nothing it can get to.
WAKE_S = 2.0              # how often a parked arm looks for work again
REPLAN_FRAC = 0.6
REPLAN_MIN_S = 30.0
# A point left alone because the person moved is tried again this long
# after: they move all the time, and a pass that writes off every point it
# missed leaves an arm with nothing to scrub and parks it.
RETRY_S = 6.0
# The most any joint may turn in one checked step. A small move of the sponge
# near the base's axis can swing the whole arm, from clear of a neighbour to
# inside the stop distance in one frame, and the hold never gets to see it.
MAX_SWING_RAD = 0.06
# A step turned in joint space goes to the governor as a tool point, and the
# governor's IK can land this far from the angles asked for. Joint steps are
# kept this much under MAX_SWING_RAD, so what is commanded stays within it.
IK_SLACK_RAD = 0.02
# A frame longer than SUBSTEP_S is moved in up to MAX_SUBSTEPS checked steps.
SUBSTEP_S = 0.045
MAX_SUBSTEPS = 4
# The furthest any point of an arm moves in one checked step: 0.06 rad at the
# arm's full 516 mm reach, or AIR_V over the longest step (0.2 s / 4).
STEP_MM = 35.0
JOINT_V = 1.4             # rad/s, joints turning on a leg through the air
# A WAY AROUND. An air leg the governor refuses both ways (joints turning,
# a straight line) is flown along a path searched in joint space instead
# (joint_path): poses PATH_STEP_RAD apart, PATH_MARGIN_MM more than D_BODY_MM
# from the person, at most PATH_MAX_POSES poses looked at.
# Cells no arm can reach when the plan is made still get an owner (see
# give_rest), which takes them if the person moves so that it can.
GIVE_REST = True
PATH_STEP_RAD = 0.1
PATH_MARGIN_MM = 10.0
PATH_MAX_POSES = 6000
ARRIVED_MM = 5.0          # an air leg is done this close to its (moving) target
PHASE_WAIT_S = 3.0        # how long an arm waits for its patch to come in reach
CHASE_WAIT_S = 0.4        # ...and for a limb that is moving, before it goes elsewhere
NAMES = ("red", "blue", "green", "orange")
# Every arm with work scrubs at once. Arms whose working poses would meet
# are kept apart move by move by the governor (it holds an arm D_HOLD_MM from
# another) instead of by taking turns. True brings the turns back.
TAKE_TURNS = False
# Two working arms in each other's way do not both wait. Once one has been
# held DEFER_S, the one of the two lower in priority (the higher index)
# gives way: it steps back until they are YIELD_GAP_MM apart (at most
# YIELD_MAX_S), then leaves the rest of its stroke for last and goes on with
# the nearest stroke whose points all stay DEFER_CLEAR_MM from the other,
# at most DEFERS_PER_PLAN times a plan. An arm held by one that is not
# working goes elsewhere the same way.
GIVE_WAY = True
DEFER_S = 0.3
DEFER_CLEAR_MM = 160.0
DEFERS_PER_PLAN = 2
YIELD_GAP_MM = 150.0
YIELD_MAX_S = 2.0

# THE SCRUB. Along each stroke the sponge rubs side to side across it, in
# the skin's own plane, SCRUB_AMP_MM each way SCRUB_HZ times a second, while
# its place on the stroke moves on at SCRUB_V (the rub does not slow it). The
# sponge is 80 mm across and rubs a band 104 mm wide, so rows 65 mm apart
# overlap by 39, which still covers a row that the moving body pulls a little
# off its line. The rub is what the real arms' servos can follow at the
# acceleration arm_hw sets (a faster one is smoothed away by them, and
# leaves the real arm off its checked path).
SCRUB_V = 350.0           # mm/s along the stroke
# Each pass scrubs every patch SWEEPS times before moving on: along it, then
# across it, then along it again with the rows halfway between the first.
# Once: the whole of what the arms reach is scrubbed sooner, pass after pass,
# and an arm done before the others goes over its patches again meanwhile
# (AGAIN_WHILE_OTHERS_WORK) rather than wait.
SWEEPS = 1
AGAIN_WHILE_OTHERS_WORK = True
# An arm that has been over its patches goes over them again there and then,
# and a pass ends where the arms are: no flight home, no waiting, no parking
# between passes. They stop only when the depth guard holds them, when the
# person goes out of view, or when the view stops.
ALWAYS_SCRUBBING = True
SCRUB_AMP_MM = 25.0      # across the stroke, on the torso
SCRUB_AMP_LIMB_MM = 12.0  # ... and on an arm, which is narrow and moves
SCRUB_HZ = 1.3
# Every point the sponge reaches is scrubbed where it is for DWELL_S, the
# sponge rubbing across it, before the stroke moves on. What the arms can
# reach of a seated person is often a handful of spots, and touching each
# once looks like nothing at all. An arm that has been over its patches and
# is going round them again stays DWELL_AGAIN times as long on each: the
# first time down the person covers them, the times after work them.
DWELL_S = 0.8
DWELL_AGAIN = 2.0
# Like a printer: rows ACROSS the person, the top row first, each row
# carrying on from where the one above ended, and the highest patch an arm
# owns before the ones below it (a shoulder before the elbow below it, the
# top of a chest before the belly), rather than whatever is nearest the
# sponge.
TOP_DOWN = True
ROW_GAP_MM = 65.0
WAYPOINT_MM = 25.0        # between points along a stroke
JOIN_MM = 150.0           # a turn to the next row this short stays on the skin
MIN_PATCH_CM2 = 10.0      # a patch smaller than this is not worth a flight
HOP_MM = 220.0            # an air leg shorter than this is flown straight
APPROACH_MM = 150.0       # this close to its target, an arm may near that limb
WAYPOINT_REFUSALS = 3     # refused points in a row before a stroke is given up
STUCK_FRAMES = 12         # an air leg refused this long gives up its target
RETREATS_PER_POINT = 2    # backed off this often, the point is given up
MAX_ROUNDS = 3            # plans per phase: the first, then what it missed
# A limb moving faster than this is waited for, not chased: only a limb
# being thrown about, since the person wants the arms to follow them as they
# move. The plan is cells of the model, so a stroke follows the skin under
# it by itself; this is only about a limb moving faster than an arm can
# safely track.
CHASE_V = 400.0           # mm/s
STALL_S = 15.0            # nothing new scrubbed for this long: go home
CLEANUP_FRAC = 0.1        # a later round only for more than this much left
# Measured over this long, not frame to frame: the posed limbs jitter by
# more than that between frames while the person holds still.
SPEED_WINDOW_S = 0.5
REPARK_S = 1.0            # held this long on the way home: wait somewhere nearer
# A parked arm keeps this much more than D_BODY, because the person moves; and
# a waiting arm backs off once the person comes within KEEP_AWAY_MM of D_BODY.
PARK_MARGIN_MM = 60.0
KEEP_AWAY_MM = 20.0
# HOW CLOSE THE SIMULATED ARMS RUN HERE, as asked: moving structure 5 mm
# from the person (the limb being scrubbed too), the sponge pressed 5 mm into
# the skin, arms held 45 mm apart and stopped at 5. collide.py's defaults
# (6 cm, 9 cm, 4 cm) stay for the rest of the project. A checked step moves
# a point at most STEP_MM (35), so no step goes from outside the hold line to
# inside the stop line.
D_BODY_MM = 5.0
D_LIMB_MM = 5.0
D_HOLD_MM = 45.0
D_ESTOP_MM = 5.0
PRESS_MM = 3.0
STANDOFF_MM = COL.R_SPONGE - PRESS_MM     # tool point above the skin
# What an approach needs: a sponge-sized ball this far out along the normal,
# clear of every other part of the body.
CORRIDOR = (STANDOFF_MM + 10.0, COL.R_SPONGE)
# Real arms (arm_hw), where their own encoders put them. A real arm's
# structure REAL_BODY_MM inside the person as the camera sees them, or two
# real arms inside D_ESTOP_MM of each other, holds every arm. Skin counts as
# scrubbed by a real sponge only where it reached it: its centre within
# R_SPONGE of the skin under it, crediting what rub() credits there.
REAL_BODY_MM = -15.0
# The depth guard (see the module notes).
SLOW_BAND_MM = 25.0
V_TOUCH = 80.0            # mm/s, the sponge closing on the person
V_CLOSE = 120.0           # mm/s, the rest of the arm closing on them
A_BRAKE = 1000.0          # mm/s2, what the braking curve assumes an arm can shed
MAX_PRESS_MM = 8.0
MAX_LIFT_OUT_MM = 40.0    # the most a goal is moved out to keep to MAX_PRESS_MM
PRESS_SLACK_MM = 1.0      # ...and how far past it a moved-out goal may still be
NEAR_MM = 60.0
V_NEAR = 200.0            # mm/s, the sponge off the skin, near the person
V_NEAR_ARM = 250.0        # mm/s, the fastest point of the rest of the arm
MAX_TOOL_V = 480.0        # mm/s: arm_hw's MAX_STEP_MM at its RATE_HZ
RAW_MM = 0.0
# No part of an arm may go this far into what the camera measured, whatever
# its base stands in: a link against the person keeps the depth it has.
RAW_FLOOR_MM = -20.0
# No link of an arm may go further than this behind the surface the camera
# measured along its own ray, while the model has it within BEHIND_NEAR_MM
# of the person: that is a link pressing into them where the camera cannot
# see it, as one between the thighs does. Behind them with room to spare is
# an arm reaching round a limb, which is its work. The sponge is left out of
# it, since the skin it scrubs can face away from the camera.
BEHIND_MAX_MM = 20.0
BEHIND_NEAR_MM = 25.0
# Which moving parts that covers: the arm's structure, not the tool.
STRUCTURE = tuple(i for i, k in enumerate(COL.MOVING) if k != 4)
PERSON_PAD_MM = 60.0      # the camera's points this near the model are the person's
MESH_GAP_MM = 25.0        # about the widest spacing of the model's vertices
# A part is not trusted once its median is AGREE_MM off (or AGREE_OFF of it
# has nothing near behind), and trusted again under AGREE_OK_MM. Measured on
# both recordings with the arms working in front of the person: clothes put
# the torso 15 to 28 mm in front of the model and an arm up to 35, while the
# person sits as they were.
AGREE_MM = 35.0
AGREE_OK_MM = 30.0
# A part with this share of its cells seeing nothing within OFF_MM behind
# them is somewhere the person is not. A forearm seen edge on, with gaps
# around the hand, runs at 0.16 to 0.18 while the model sits right on it;
# a forearm the model has in the wrong place ran at 0.79.
AGREE_OFF = 0.30
AGREE_MIN = 20            # cells compared, for a verdict on a part
AGREE_OFF_MIN = 8         # ... and cells with nothing behind them, for that verdict
MODEL_LOST_PARTS = 3      # parts out at once that mean the fit itself is wrong
DISTRUST_AFTER = 2        # looks in a row that disagree
TRUST_AFTER = 5           # looks in a row that agree again
LOST_S = 0.5
# Real arms: one more than LEAD_MM behind its plan is waited for (the plan's
# checks hold for the path, not for a real arm cutting corners to catch up),
# for up to LEAD_WAIT_S at a time. Its reading is up to LEAD_LATE_S old, so
# the plan moving at v may be v * LEAD_LATE_S further on without the arm
# being behind: slow near the person, the limit stays tight.
LEAD_MM = 15.0
LEAD_LATE_S = 0.1
LEAD_WAIT_S = 1.0


def allowed_close(gap, v_min):
    """How fast an arm may close on the person from `gap` mm away. -> mm/s"""
    if gap <= SLOW_BAND_MM:
        return v_min
    return math.sqrt(v_min * v_min + 2.0 * A_BRAKE * (gap - SLOW_BAND_MM))


def body_mesh(body):
    """The posed model as one triangle mesh, world mm. -> (V, F)"""
    Vs, Fs, off = [], [], 0
    for name, p in body.parts.items():
        T = body.T.get(name)
        if T is None:
            continue
        sc = body.S.get(name, 1.0)
        V = (np.asarray(p["V"], float) * np.array([1.0, 1.0, sc])) @ T[:3, :3].T + T[:3, 3]
        Vs.append(V)
        Fs.append(np.asarray(p["F"], np.int64) + off)
        off += len(V)
    return np.concatenate(Vs), np.concatenate(Fs)


def base_pose(r, sx=0.0, sy=0.0):
    """One arm of a seat-relative rig -> its 4x4 base pose in the world.

    x and y from the seat point (sx, sy), z above the floor; the base turned
    `facing_deg` about the vertical, then tipped `tilt_deg` about its own y
    (positive tips its top toward where it faces) and `roll_deg` about its
    own x. A base left level has neither.
    """
    T = PA.pose(sx + float(r["x_from_seat_mm"]), sy + float(r["y_from_seat_mm"]),
                float(r["z_mm"]), math.radians(float(r["facing_deg"])),
                math.radians(float(r.get("tilt_deg", 0.0))))
    roll = math.radians(float(r.get("roll_deg", 0.0)))
    if roll:
        c, s = math.cos(roll), math.sin(roll)
        T[:3, :3] = T[:3, :3] @ np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])
    return T


def over_head(ceiling, p):
    """Would a tool point at `p` be above the head ceiling (the governor's
    first refusal)?"""
    return ceiling is not None and float(p[2]) > ceiling


def capsule_gap(caps, many):
    """Closest surface distance between one arm's capsules (5, 2, 3) and any
    of another arm's sampled capsule sets (S, 5, 2, 3). mm."""
    A = caps[None, :, None, :, :]
    B = many[:, None, :, :, :]
    d = COL._seg_seg_dist(A[..., 0, :], A[..., 1, :], B[..., 0, :], B[..., 1, :])
    r = np.asarray(COL.CAPSULE_RADII, float)
    return float((d - (r[:, None] + r[None, :])[None]).min())


def interference_phases(work, planned, n_arms, margin=30.0, tol=0.02):
    """Phases that keep arms apart, not just their patches.

    partition.schedule separates arms whose PATCHES interlock. Two arms with
    cleanly separate patches can still reach through the same air to get to
    them, and then one is always waiting for the other. So also compare each
    pair's sampled working poses: arms that come within D_HOLD + margin of
    each other in more than `tol` of them work in different phases.
    -> (phases, {pair: fraction})
    """
    conflict = {a: set() for a in range(n_arms)}
    for ph_i, ph in enumerate(planned):
        for other in planned[ph_i + 1:]:
            for a in ph:
                for b in other:
                    conflict[a].add(b)
                    conflict[b].add(a)
    frac = {}
    arms = sorted(k for k, w in work.items() if len(w))
    for i, a in enumerate(arms):
        for b in arms[i + 1:]:
            A = work[a][:, None, :, None, :, :]
            B = work[b][None, :, None, :, :, :]
            d = COL._seg_seg_dist(A[..., 0, :], A[..., 1, :],
                                  B[..., 0, :], B[..., 1, :])
            r = np.asarray(COL.CAPSULE_RADII, float)
            gap = (d - (r[:, None] + r[None, :])).min(axis=(2, 3))
            f = float((gap < D_HOLD_MM + margin).mean())
            frac[(a, b)] = f
            if f > tol:
                conflict[a].add(b)
                conflict[b].add(a)
    colour = {}
    for a in sorted(range(n_arms), key=lambda x: -len(conflict[x])):
        used = {colour[b] for b in conflict[a] if b in colour}
        c = 0
        while c in used:
            c += 1
        colour[a] = c
    phases = {}
    for a, c in colour.items():
        phases.setdefault(c, []).append(a)
    return [sorted(v) for _k, v in sorted(phases.items())], frac


def reach_matrix(bm, layout, obs, reg, ceiling, which=None, standoff=STANDOFF_MM):
    """Per scrub cell and arm: can that arm put the sponge there safely, now?
    -> bool (cells, arms). `which` (cells, arms) limits the cells tried.

    Reach, then link clearance against every part except the one scrubbed,
    and D_LIMB_MM from that one; where the sponge works, AND where it comes
    down from: an approach that cannot hover above a cell cannot reach it
    without passing through something on the way.
    """
    P, N, Iw, _A = bm.world_cells()
    n = len(layout)
    ok = np.ones((len(P), n), bool) if which is None else np.array(which, bool)
    trees, own = {None: cKDTree(obs)}, {}
    for rid in np.unique(reg[reg >= 0]):
        trees[int(rid)] = cKDTree(obs[reg != rid])
        own[int(rid)] = cKDTree(obs[reg == rid])
    for lift in (0.0, LIFT_MM):
        tgt = P + N * (standoff + lift)
        for a, T in enumerate(layout):
            idx = np.flatnonzero(ok[:, a])
            if not len(idx):
                continue
            T = np.asarray(T, float)
            h = np.c_[tgt[idx], np.ones(len(idx))] @ np.linalg.inv(T).T
            J, solved = K.ik_many(h[:, 0], h[:, 1], h[:, 2])
            if lift == 0.0 and ceiling is not None:
                solved &= tgt[idx, 2] <= ceiling
            ok[idx[~solved], a] = False
            cand, J = idx[solved], J[solved]
            for rid in np.unique(Iw[cand]):
                sel = np.flatnonzero(Iw[cand] == rid)
                rid = int(rid)
                clear = PART._body_clear(T, J[sel], sel, trees.get(rid, trees[None]),
                                         COL.CAPSULE_RADII, D_BODY_MM, moving=True)
                if rid in own:
                    clear = clear & PART._body_clear(T, J[sel], sel, own[rid],
                                                     COL.CAPSULE_RADII, D_LIMB_MM,
                                                     moving=True)
                ok[cand[sel[~clear]], a] = False
    return ok


def feasible_now(bm, layout, owner, obs, reg, ceiling, standoff=STANDOFF_MM):
    """Per scrub cell: can its OWN arm put the sponge there safely, now?

    partition.solve assigns a cell when it is feasible in most poses of the
    envelope, so at the pose the person is actually in, some assigned cells
    are not. The controller is told to leave those alone rather than find
    out one refusal at a time.
    """
    owner = np.asarray(owner)
    which = owner[:, None] == np.arange(len(layout))[None, :]
    M = reach_matrix(bm, layout, obs, reg, ceiling, which, standoff)
    ok = np.zeros(len(owner), bool)
    has = owner >= 0
    ok[has] = M[np.flatnonzero(has), owner[has]]
    return ok


def give_rest(owner, P, N, layout, standoff=STANDOFF_MM, k=8):
    """Owners for the cells no arm can reach now: an arm whose joints reach
    them (on the skin and hovering), the one owning most of their neighbours
    (nearest base if none). -> owner

    They stay unsafe until a re-check says otherwise: a person who lifts an
    arm opens up the side of the torso, and its owner then scrubs it."""
    owner = np.asarray(owner).copy()
    rest = np.flatnonzero(owner < 0)
    if not len(rest) or not len(layout):
        return owner
    can = np.ones((len(rest), len(layout)), bool)
    for a, T in enumerate(layout):
        Tinv = np.linalg.inv(np.asarray(T, float))
        for lift in (0.0, LIFT_MM):
            h = np.c_[P[rest] + N[rest] * (standoff + lift), np.ones(len(rest))] @ Tinv.T
            can[:, a] &= K.ik_many(h[:, 0], h[:, 1], h[:, 2])[1]
    has = can.any(1)
    if not has.any():
        return owner
    held = owner >= 0
    bases = np.array([np.asarray(T, float)[:3, 3] for T in layout])
    nb = None
    if held.any():
        _d, nb = cKDTree(P[held]).query(P[rest], k=min(k, int(held.sum())))
        nb = np.asarray(nb).reshape(len(rest), -1)
    theirs = owner[held]
    for row in np.flatnonzero(has):
        arms = np.flatnonzero(can[row])
        votes = (np.bincount(theirs[nb[row]], minlength=len(layout))[arms]
                 if nb is not None else np.zeros(len(arms)))
        if votes.max() > 0:
            owner[rest[row]] = arms[int(np.argmax(votes))]
        else:
            d = np.linalg.norm(bases[arms] - P[rest[row]], axis=1)
            owner[rest[row]] = arms[int(np.argmin(d))]
    return owner


def near_still(layout, park_j, still, owner, P, N, standoff=STANDOFF_MM,
               gap=D_HOLD_MM + 10.0):
    """Cells whose arm, working on them (on the skin or hovering), would come
    within `gap` of an arm that stays still. -> bool (cells,)

    An arm that stays still never moves out of the way, so the governor would
    hold the other arm there for good."""
    out = np.zeros(len(P), bool)
    if not still:
        return out
    caps = np.array([COL.arm_capsules(layout[d], *tuple(park_j[d])[:3]) for d in still])
    owner = np.asarray(owner)
    for a in np.unique(owner[owner >= 0]):
        if a in still:
            continue
        idx = np.flatnonzero(owner == a)
        T = np.asarray(layout[a], float)
        for lift in (0.0, LIFT_MM):
            h = np.c_[P[idx] + N[idx] * (standoff + lift), np.ones(len(idx))] \
                @ np.linalg.inv(T).T
            for k, i in enumerate(idx):
                if out[i]:
                    continue
                j = K.ik(*h[k, :3])
                if j is not None and capsule_gap(
                        np.asarray(COL.arm_capsules(T, *j)), caps) < gap:
                    out[i] = True
    return out


def joint_path(fleet, a, T, j_from, j_to, tree, ceiling=None, step=None,
               margin=None, max_poses=None):
    """A way around the person for arm `a`, in joint space. -> [joints, ...]
    ending at `j_to` (the start left out), or None.

    A* over poses `step` apart (26 neighbours). A pose on the way keeps its
    moving structure `margin` more than D_BODY_MM from the person (D_BODY_MM
    only within two steps of either end), the sponge below `ceiling`, and the
    elbow on the side ik() solves for, since every step goes to the governor
    as a tool point; so does the pose halfway to each neighbour. It gives up
    after `max_poses` poses checked. The governor still checks every step.
    """
    step = PATH_STEP_RAD if step is None else step
    margin = PATH_MARGIN_MM if margin is None else margin
    max_poses = PATH_MAX_POSES if max_poses is None else max_poses
    start = np.asarray(j_from, float)[:3]
    goal = np.asarray(j_to, float)[:3]
    T = np.asarray(T, float)
    cache = {}                    # pose on the half-step grid -> clearance
    checked = [0]

    def clearance(Q):
        """(M, 3) poses -> (M,) clearance, -inf for a pose that is not allowed."""
        Q = np.asarray(Q, float).reshape(-1, 3)
        x, y, z = K.fk(Q[:, 0], Q[:, 1], Q[:, 2])
        J, ok = K.ik_many(x, y, z)
        ok &= np.max(np.abs(J - Q), axis=1) <= IK_SLACK_RAD
        if ceiling is not None:
            ok &= T[2, 0] * x + T[2, 1] * y + T[2, 2] * z + T[2, 3] <= ceiling
        out = np.full(len(Q), -np.inf)
        if ok.any():
            checked[0] += int(ok.sum())
            out[ok] = fleet.link_clearance_many(a, Q[ok], tree)
        return out

    def cleared(keys):
        """Half-step grid keys -> their clearances, looking up only new ones."""
        new = [k for k in dict.fromkeys(keys) if k not in cache]
        if new:
            for k, c in zip(new, clearance(start + np.asarray(new, float) * (step / 2.0))):
                cache[k] = c
        return np.array([cache[k] for k in keys])

    def need(q):
        near = min(np.max(np.abs(q - start)), np.max(np.abs(q - goal))) <= 2.0 * step + 1e-9
        return D_BODY_MM if near else D_BODY_MM + margin

    def line_clear(p, q):
        n = max(1, int(math.ceil(float(np.max(np.abs(q - p))) / (MAX_SWING_RAD - IK_SLACK_RAD))))
        Q = p + (q - p) * (np.arange(1, n + 1)[:, None] / n)
        return bool(np.all(clearance(Q) >= D_BODY_MM))

    moves = np.array([d for d in itertools.product((-1, 0, 1), repeat=3) if any(d)])
    cost = step * np.sqrt(np.abs(moves).sum(1))
    k0 = (0, 0, 0)
    came, best = {k0: None}, {k0: 0.0}
    heap = [(float(np.linalg.norm(goal - start)), 0.0, k0)]
    while heap and checked[0] < max_poses:
        _f, g, k = heapq.heappop(heap)
        if g > best[k] + 1e-9:
            continue
        q = start + np.asarray(k, float) * step
        if float(np.max(np.abs(q - goal))) <= 1.5 * step and line_clear(q, goal):
            path = [goal]
            while k != k0:
                path.append(start + np.asarray(k, float) * step)
                k = came[k]
            return path[::-1]
        kn = [tuple(int(v) for v in np.asarray(k) + d) for d in moves]
        ng = g + cost
        fresh = [i for i in range(len(moves)) if ng[i] < best.get(kn[i], np.inf)]
        if not fresh:
            continue
        nodes = cleared([tuple(2 * v for v in kn[i]) for i in fresh])
        mids = cleared([tuple(k[j] + kn[i][j] for j in range(3)) for i in fresh])
        for i, cn, cm in zip(fresh, nodes, mids):
            qn = start + np.asarray(kn[i], float) * step
            if cn < need(qn) or cm < min(need(q), need(qn)):
                continue
            best[kn[i]] = float(ng[i])
            came[kn[i]] = k
            heapq.heappush(heap, (float(ng[i] + np.linalg.norm(goal - qn)), float(ng[i]),
                                  kn[i]))
    return None


def assign_now(owner, M, pts, layout, k=8):
    """Owners for the pose the person is in now. -> (owner, ok)

    A cell keeps the planner's owner while that arm can reach it; otherwise
    it goes to an arm that can, the one owning most of its neighbours
    (nearest base if none), so an arm that cannot get to its cells now does
    not keep them from one that can, and cells the planner left out are
    scrubbed when some arm reaches them.
    """
    owner = np.asarray(owner).copy()
    n = M.shape[1]
    idx = np.arange(len(owner))
    keep = (owner >= 0) & M[idx, np.maximum(owner, 0)]
    move = np.flatnonzero(~keep & M.any(1))
    if len(move):
        tree = cKDTree(pts)
        _d, nb = tree.query(pts[move], k=min(k + 1, len(pts)))
        bases = np.array([np.asarray(T, float)[:3, 3] for T in layout])
        for row, i in enumerate(move):
            can = np.flatnonzero(M[i])
            votes = np.zeros(n)
            for j in np.atleast_1d(nb[row])[1:]:
                if keep[j]:
                    votes[owner[j]] += 1.0
            if votes[can].max() > 0:
                owner[i] = can[int(np.argmax(votes[can]))]
            else:
                owner[i] = can[int(np.argmin(np.linalg.norm(bases[can] - pts[i], axis=1)))]
    ok = (owner >= 0) & M[idx, np.maximum(owner, 0)]
    return owner, ok


# --- strokes ------------------------------------------------------------------

def region_charts(bm):
    """Each scrub cell in its part's own flat chart, in world_cells() order.

    -> (N, 2): along the part (its local +Z, mm) and around it (arc, mm).
    Parts are posed rigidly, so a cell's chart position never changes, and a
    row laid out in the chart stays a row however the person moves.
    """
    out = []
    for r in bm.regions:
        L = np.asarray(r.pts, float)[np.asarray(r.scrubbable, bool)]
        out.append(np.stack([L[:, 2], np.hypot(L[:, 0], L[:, 1])
                             * np.arctan2(L[:, 1], L[:, 0])], 1))
    return np.vstack(out) if out else np.zeros((0, 2))


def region_strokes(chart, cells, flip=False, blocked=(), across=False, between=False):
    """Rows over one patch. -> [(row, [cell, ...])] in the order to scrub them.

    Rows run along the patch's longer side, ROW_GAP_MM apart, and alternate
    direction, so the end of one row is beside the start of the next. A hole
    in a row splits it in two: a gap with two or more `blocked` cells in it
    (the patch's own cells that may not be touched now). A lone blocked cell
    is not worth a lift, and the governor still checks every step over it; a
    gap with none is only where the body's cells are sparse. `flip` starts
    the first row from its other end. `across` runs the rows along the
    shorter side instead, and `between` lays them halfway between the usual
    ones (the later sweeps of a pass).
    """
    cells = np.asarray(cells, int)
    blocked = np.asarray(blocked, int)
    C = chart[cells]
    ext = C.max(0) - C.min(0)
    ax = 0 if ext[0] >= ext[1] else 1
    if across:
        ax = 1 - ax
    s, q = C[:, ax], C[:, 1 - ax]
    lo, hi = float(q.min()), float(q.max())
    inset = min(0.5 * ROW_GAP_MM, 0.5 * (hi - lo))
    n = max(1, int(math.ceil((hi - lo - 2.0 * inset) / ROW_GAP_MM - 1e-9)) + 1)
    centres = (np.linspace(lo + inset, hi - inset, n) if n > 1
               else np.array([0.5 * (lo + hi)]))
    half = 0.5 * (centres[1] - centres[0]) if n > 1 else 0.5 * ROW_GAP_MM
    half = max(half, 0.5 * WAYPOINT_MM) + 1.0
    if between and n > 1:
        centres = 0.5 * (centres[1:] + centres[:-1])
    # How far apart the cells are along a row: a torso's rows of cells are
    # further apart than a limb's.
    us = np.unique(np.round(s))
    ds = float(np.median(np.diff(us))) if len(us) > 1 else WAYPOINT_MM
    tol = max(0.75 * WAYPOINT_MM, 0.6 * ds)
    if len(blocked):
        B = chart[blocked]
        bs, bq = B[:, ax], B[:, 1 - ax]
    out = []
    for row, qc in enumerate(centres):
        ks = np.flatnonzero(np.abs(q - qc) <= half)
        if not len(ks):
            continue
        s_lo, s_hi = float(s[ks].min()), float(s[ks].max())
        ticks = list(np.arange(s_lo, s_hi + 1e-6, WAYPOINT_MM))
        if s_hi - ticks[-1] > 0.4 * WAYPOINT_MM:
            ticks.append(s_hi)
        runs, run = [], []
        for st in ticks:
            near = ks[np.abs(s[ks] - st) <= tol]
            if not len(near):
                continue
            k = int(near[np.argmin(np.abs(s[near] - st) + 2.0 * np.abs(q[near] - qc))])
            if run and k == run[-1]:
                continue
            if run and len(blocked):
                lo_s, hi_s = sorted((s[run[-1]], s[k]))
                inside = (bs > lo_s) & (bs < hi_s) & (np.abs(bq - qc) <= half)
                if np.count_nonzero(inside) >= 2:
                    runs.append(run)       # a hole: lift over it
                    run = []
            run.append(k)
        if run:
            runs.append(run)
        if (row % 2 == 1) != flip:
            runs = [list(reversed(r)) for r in reversed(runs)]
        out += [(row, [int(cells[k]) for k in r]) for r in runs]
    return out


def downness(rows, targets):
    """How much the strokes of `rows` run up and down rather than around,
    0 to 1: the share of each stroke's length that is height."""
    out = []
    for _row, cells in rows:
        a, b = targets[cells[0]], targets[cells[-1]]
        L = float(np.linalg.norm(np.asarray(b, float) - np.asarray(a, float)))
        if L > 1e-6:
            out.append(abs(float(b[2]) - float(a[2])) / L)
    return float(np.mean(out)) if out else 0.0


def top_down(rows, targets):
    """The strokes of `rows` as a printer lays out lines: the highest row
    first, each row running the way that carries on from where the row above
    ended, so the sponge goes across, down, back across."""
    def height(rc):
        return float(np.mean([targets[c][2] for c in rc[1]]))

    out, here = [], None
    for row, cells in sorted(rows, key=height, reverse=True):
        cs = list(cells)
        if here is not None and float(np.linalg.norm(targets[cs[-1]] - here)) < \
                float(np.linalg.norm(targets[cs[0]] - here)):
            cs.reverse()
        out.append((row, cs))
        here = np.asarray(targets[cs[-1]], float)
    return out


def build_plan(chart, region, usable, area, targets, start, min_points=1, sweeps=1):
    """Every patch of one arm, as strokes, the highest patch first and each
    stroke from its top down (TOP_DOWN; otherwise the nearest patch first),
    each patch scrubbed `sweeps` times over before the next (see SWEEPS).

    -> (segments, dropped). A segment is {"region", "row", "cells",
    "joined", "sweep"}; `joined` means the sponge stays on the skin from the
    end of the segment before, and `sweep` numbers the sweeps of the plan.
    `dropped` marks the cells of patches too small to visit. Strokes of fewer
    than `min_points` points are left out: a single dab costs a flight, a
    landing and a lift.
    """
    variants, dropped = {}, np.zeros(len(usable), bool)
    for r in np.unique(region[usable]):
        cells = np.flatnonzero(usable & (region == r))
        if area[cells].sum() < MIN_PATCH_CM2 * 100.0:
            dropped[cells] = True
            continue
        blocked = np.flatnonzero(~usable & (region == r))
        per_sweep = []
        for k in range(max(1, sweeps)):
            ways = []
            sides = (False, True) if TOP_DOWN else (k % 2 == 1,)
            for across in sides:
                for flip in (False, True):
                    # a later sweep is strokes, not dabs: a lone point is a flight,
                    # a landing and a lift for one sponge's width
                    rows = [(row, c) for row, c in region_strokes(
                                chart, cells, flip, blocked, across=across,
                                between=k > 0 and k % 2 == 0)
                            if len(c) >= (min_points if k == 0 else max(min_points, 2))]
                    if rows:
                        ways.append(rows)
            if not ways:
                continue
            if TOP_DOWN:
                # the way whose rows run most ACROSS the person, top row first
                vs = [top_down(min(ways, key=lambda rows: downness(rows, targets)),
                               targets)]
            else:
                vs = []
                for rows in ways:
                    vs.append(rows)
                    vs.append([(row, list(reversed(c))) for row, c in reversed(rows)])
            per_sweep.append(vs)
        if per_sweep:
            variants[int(r)] = per_sweep
    plan, here, sweep = [], np.asarray(start, float), 0

    def nearest(vs):
        return min(vs, key=lambda v: float(np.linalg.norm(targets[v[0][1][0]] - here)))

    def highest(r):
        return max(float(targets[c][2]) for vs in variants[r] for v in vs
                   for _row, cells in v for c in cells)

    while variants:
        r = (max(variants, key=highest) if TOP_DOWN else
             min(variants, key=lambda r: float(np.linalg.norm(
                 targets[nearest(variants[r][0])[0][1][0]] - here))))
        for vs in variants.pop(r):
            v = vs[0] if TOP_DOWN else nearest(vs)
            for i, (row, cells) in enumerate(v):
                joined = False
                prev = v[i - 1] if i > 0 else None
                if prev is not None and row != prev[0]:
                    gap = float(np.linalg.norm(chart[cells[0]] - chart[prev[1][-1]]))
                    joined = gap <= JOIN_MM
                elif prev is None and plan and plan[-1]["region"] == r:
                    # the next sweep of the same patch, on from where the last ended
                    gap = float(np.linalg.norm(chart[cells[0]]
                                               - chart[plan[-1]["cells"][-1]]))
                    joined = gap <= JOIN_MM
                plan.append({"region": r, "row": row, "cells": cells, "joined": joined,
                             "sweep": sweep})
            here = targets[v[-1][1][-1]]
            sweep += 1
    return plan, dropped


class StrokeTool:
    """One arm's way through its plan: strokes on the skin, lifts between.

    The plan is cells, so every target is re-read from where the person is
    now. Credit follows the sponge: the cells under it count once it gets
    there, never before.
    """

    def __init__(self, ctl, start, ceiling=None):
        self.c = ctl
        self.tool = np.asarray(start, float).copy()
        self.ceiling = ceiling
        self.credit = np.zeros(len(ctl.pts), bool)
        self.count = np.zeros(len(ctl.pts), int)    # sweeps over each cell this pass
        self.last_sweep = np.full(len(ctl.pts), -1)  # the sweep that counted it last
        self.real_count = np.zeros(len(ctl.pts), int)    # the same, under the real sponge
        self.real_sweep = np.full(len(ctl.pts), -1)
        self.plan_no = 0
        self.avoid = np.zeros(len(ctl.pts), bool)   # points refused just now
        self.avoid_at = np.zeros(len(ctl.pts))      # ... and when each was
        self.refused = []
        self.contact = False
        self.base = None             # on the skin: the sponge's place on the stroke
        self.last = None             # the last point the sponge touched
        self.rounds = 0
        self.progress_t = 0.0        # when it last scrubbed something new
        self.dwell = 0.0             # seconds spent scrubbing a short stroke where it is
        self.new_plan([])

    def new_plan(self, plan):
        self.plan_no += 1
        self.plan = plan
        self.seg, self.wp = 0, -1
        self.target = None           # the point being gone to
        self.route = []              # legs still to go before reaching it
        self.broken = True           # the next point needs an approach
        self.refusals = 0            # points refused in a row
        self.stuck = 0               # frames an air leg was refused in a row
        self.retreats = 0            # times backed off from the current point
        self.chase = 0.0             # seconds spent waiting for a moving limb
        self.held = 0.0              # seconds held by another arm on this leg
        self.defers = 0              # strokes left for later this plan
        self.finished = not plan

    def credited(self):
        return self.credit

    def level(self):
        """Per cell, how much of this pass's scrubbing it has had, 0 to 1."""
        return np.minimum(self.count, SWEEPS) / float(SWEEPS)

    def real_level(self):
        """level(), for what the real sponge touched."""
        return np.minimum(self.real_count, SWEEPS) / float(SWEEPS)

    def new_pass(self):
        self.credit[:] = False
        self.count[:] = 0
        self.last_sweep[:] = -1
        self.real_count[:] = 0
        self.real_sweep[:] = -1

    def real_rub(self, p):
        """The real sponge is at `p` (world mm, from its arm's encoders).
        If it reaches the skin under it, credit what rub() would credit
        there, once a sweep. -> how many cells."""
        c = self.c
        p = np.asarray(p, float)
        dist, k = c.tree.query(p)
        if not np.isfinite(dist):
            return 0
        h = float((p - c.pts[k]) @ c.nrm[k])
        if abs(h) > COL.R_SPONGE:
            return 0                             # not touching
        foot = p - c.nrm[k] * h                  # its centre, dropped onto the skin
        on = np.asarray(c.tree.query_ball_point(foot, c.sponge_r), int)
        key = self._sweep_key()
        fresh = on[self.real_sweep[on] != key]
        self.real_count[fresh] += 1
        self.real_sweep[fresh] = key
        return len(on)

    def _sweep_key(self, seg=None):
        seg = self.seg if seg is None else seg
        s = self.plan[seg].get("sweep", 0) if 0 <= seg < len(self.plan) else 0
        return self.plan_no * 10000 + s

    def where(self, cell, lift):
        """Where the tool goes for `cell`, `lift` mm above its standoff."""
        c = self.c
        p = c.pts[cell] + c.nrm[cell] * (c.standoff + lift)
        if lift > 0 and self.ceiling is not None and p[2] > self.ceiling - 15.0:
            p = p.copy()
            p[2] = self.ceiling - 15.0
        return p

    def footprint(self, cell, r=None):
        c = self.c
        return np.asarray(c.tree.query_ball_point(
            c.pts[cell], c.sponge_r if r is None else r), int)

    def rub(self, cell):
        """The sponge is on `cell`: credit what it covers, once a sweep.
        -> anything new?"""
        near = self.footprint(cell)
        new = bool(np.any(~self.credit[near]))
        self.credit[near] = True
        key = self._sweep_key()
        fresh = near[self.last_sweep[near] != key]
        self.count[fresh] += 1
        self.last_sweep[fresh] = key
        return new or bool(len(fresh))

    def advance(self, usable):
        """The next point still worth going to. -> (cell, on_skin) or None.

        A point that may not be touched now is passed over on the skin, as
        the plan passes over a lone blocked cell; two in a row are a hole.
        """
        seg, wp = self.seg, self.wp + 1
        joined = not self.broken
        missed = 0
        while seg < len(self.plan):
            cells = self.plan[seg]["cells"]
            if wp >= len(cells):
                seg, wp = seg + 1, 0
                if seg < len(self.plan):
                    joined = joined and self.plan[seg]["joined"]
                continue
            k = cells[wp]
            if usable[k]:
                self.seg, self.wp, self.broken = seg, wp, False
                return k, joined and self.contact
            missed += 1
            if missed >= 2:
                joined = False          # a hole: lift over it
            wp += 1
        self.seg, self.wp = len(self.plan), -1
        self.finished = True
        return None

    def defer(self, clear_of):
        """Another arm is in the way: leave the rest of this stroke for last
        and go on with the nearest later stroke whose tool points all pass
        `clear_of`. -> found one?"""
        seg = self.seg
        if seg >= len(self.plan):
            return False
        c = self.c
        options = []
        for s in range(seg + 1, len(self.plan)):
            cells = self.plan[s]["cells"]
            if clear_of(c.pts[cells] + c.nrm[cells] * c.standoff):
                d = float(np.linalg.norm(self.where(cells[0], 0.0) - self.tool))
                options.append((d, s))
        if not options:
            return False
        s = min(options)[1]
        cur = self.plan[seg]
        key = self._sweep_key(seg)
        rest = [k for k in cur["cells"][max(self.wp, 0):] if self.last_sweep[k] != key]
        later = [p for i, p in enumerate(self.plan) if i > seg and i != s]
        self.plan = (self.plan[:seg + 1] + [dict(self.plan[s], joined=False)] + later
                     + ([dict(cur, cells=rest, joined=False)] if rest else []))
        self.wp = len(cur["cells"]) - 1       # this stroke is over, for now
        self.target = None
        self.route = []
        self.broken = True
        self.held = 0.0
        self.retreats = 0
        self.defers += 1
        return True

    def skip_stroke(self):
        """Give up the rest of the current stroke."""
        if self.seg < len(self.plan):
            self.wp = len(self.plan[self.seg]["cells"]) - 1
        self.broken = True

    def forget(self, t):
        """Points left alone RETRY_S ago are worth trying again."""
        stale = self.avoid & (t - self.avoid_at > RETRY_S)
        if stale.any():
            self.avoid[stale] = False

    def refuse(self, cell, t=0.0):
        """`cell` may not be touched now: leave it, and lift over it."""
        self.refused.append(cell)
        near = self.footprint(cell, 0.6 * self.c.sponge_r)
        self.avoid[near] = True
        self.avoid_at[near] = t
        self.target = None
        self.route = []
        self.broken = True
        self.retreats = 0


THERE = FLEET.Verdict(FLEET.CLEAR, reason="already there")


class Arms:
    """See the module docstring."""

    def __init__(self, layout=None, rel=None):
        """layout: world poses (the rig file). rel: mounts relative to the
        seat, placed once the chair is (a searched rig, search_rig.py)."""
        self.rel = rel
        self.fixed = None if layout is None else [np.asarray(T, float) for T in layout]
        self.meshes_logged = False
        # Real arms (arm_hw): called with the placed rig, -> {arm: (joints,
        # world tool point)} of where each real arm is. The simulated arms
        # start there, and travel to where they wait instead of appearing
        # there.
        self.start_from = None
        self.hw_note = ""
        self.driving = False             # real arms follow: show what they did
        self.substep_dt = None
        self.sight_note = ""             # what the camera sees of the real arms
        self.part_names = None           # the scrubbed parts, in words, by region
        # Plans and rechecks run on threads; one that finishes after a reset
        # (a new floor, a new rig) is for arms that are gone, and is dropped.
        self.lock = threading.Lock()
        self.gen = 0
        self.reset()

    # --- where the arms are ---------------------------------------------
    def _derive(self):
        L = self.layout or []
        self.T_inv = [np.linalg.inv(T) for T in L]
        self.home = [(T @ np.r_[start_tcp(self.start_deg(a)), 1.0])[:3]
                     for a, T in enumerate(L)]
        self.joints = [start_joints(self.start_deg(a)) for a in range(len(L))]
        self.park_j = list(self.joints)
        self.sponge = [h.copy() for h in self.home]
        self.trails = {a: collections.deque(maxlen=VIZ.TRAIL_POINTS * MAX_SUBSTEPS)
                       for a in range(len(L))}

    def start_deg(self, a):
        """Arm `a`'s start pose turn (the rig file's start_deg), degrees."""
        if self.rel is None or a >= len(self.rel):
            return 0.0
        return float(self.rel[a].get("start_deg", 0.0) or 0.0)

    def place(self, seat):
        """A seat-relative rig goes where the chair is."""
        if self.rel is None:
            return
        sx, sy = (float(v) for v in seat["xy"])
        self.layout = [base_pose(r, sx, sy) for r in self.rel]
        self._derive()
        self._start_where_real()

    def _start_where_real(self):
        if self.start_from is None or not self.layout:
            return
        for a, (j, w) in self.start_from(self.layout).items():
            self.joints[a] = tuple(float(v) for v in j)
            self.sponge[a] = np.asarray(w, float).copy()

    def new_rig(self, rel):
        """Take a seat-relative rig saved while running (the rig editor).
        Everything starts over; place() and start_plan() follow.
        -> how many arms there were."""
        was = len(self.layout or self.rel or [])
        self.rel, self.fixed = rel, None
        self.reset()
        self.meshes_logged = False
        return was

    def reset(self):
        with self.lock:
            self.gen += 1
            self.result = self.checked = None
        self.layout = self.fixed          # a seat-relative rig waits for the seat
        self._derive()
        self.ctl, self.tools, self.skipped = {}, {}, {}
        self.skip_at = {}                # arm -> when each cell was skipped
        self.seen_ok = {}                # arm -> cells it could reach at some time this pass
        self.cell_region, self.cell_chart, self.cell_speed = {}, {}, {}
        self.cell_seen = collections.defaultdict(collections.deque)
        self.wait_since, self.blocked, self.reparked = {}, {}, {}
        self.woke_at = {}                # arm -> when a parked arm last looked for work
        self.back_tried = {}             # arm -> looked for a way home round the person
        self.disabled = {}               # arm -> why it stays still this run
        self.work_caps = {}              # arm -> its sampled working poses
        self.yielding = {}               # arm -> (the arm it gives way to, since)
        self.owner = self.report = self.result = self.worker = None
        self.t_prev = self.area = self.gov = self.trees = None
        self.limbs = {}
        self.mode, self.back_route, self.back_since = {}, {}, {}
        self.phases, self.phase_i = [], 0
        self.passes, self.pause_for = 1, None
        self.verdicts = collections.Counter()
        self.why, self.why_mm = collections.Counter(), {}
        self.history = []
        self.waiting = {}
        self.phase_t0 = 0.0
        self.gap_now = self.gap_min = float("inf")
        self.estopped = False
        self.jumped = 0                  # checked steps that turned a joint too far
        self.frame = 0
        self.home_note = ""
        self.settling = False            # real arms on their way to where they wait
        self.real_clear, self.real_gap = {}, float("inf")
        self.plan_clear = {}             # arm -> the same for the planned pose
        self.raw = None                  # depth_guard.RawGuard for this frame
        self.scene = None                # ... and the frame it came from
        self.mesh = None                 # the posed model, for exact distances
        self.mesh_at = None              # ... the frame it was built for
        self.mesh_tree = None            # ... and its vertices
        self.distrust, self.agree, self.agree_runs = set(), {}, {}
        self.placed = False              # the arms have been put where they wait
        self.no_park = set()             # arms with nowhere to wait: they stay lifted off
        self.paused = set()              # arms sent back by distrust, their pass not over
        self.again = set()               # arms going over their patches again this pass
        self.done_pass = set()           # arms that have been over their patches this pass
        self.lost_since = None
        self.slowed = collections.Counter()
        self.lifted = collections.defaultdict(float)   # arm -> mm its last goal was moved out
        self.real_at, self.lead_since = {}, {}
        self.plan_v = {}                 # arm -> how fast its sponge moves, mm/s
        self.waited_real = collections.Counter()
        self.checker = self.checked = self.checked_at = None
        self.checked_pose = None         # the pose safe_now was worked out for
        self.safe_now = self.still_block = None
        self.status = "waiting for you to settle in the chair"
        self.replanning = False          # a plan is being solved over a plan
        self.kept = None                 # what was scrubbed before it, to carry over
        self.planned_at, self.planned_reach = 0.0, None

    # --- planning ---------------------------------------------------------
    def start_plan(self, body):
        """Plan the territories on the model as it is posed now."""
        bm = body.as_model()
        obs, reg = body.obstacles()
        ceiling = body.head_ceiling()
        layout, gen = self.layout, self.gen
        self.status = "planning which arm scrubs what"
        self.result = None

        def work():
            t0 = time.time()
            try:
                terr, _planes, phases, rep = PART.solve(
                    bm, layout, envelope_k=PART.ENVELOPE_K,
                    obstacle_points=obs, obstacle_region=reg, moving=True,
                    d_body=D_BODY_MM, standoff=STANDOFF_MM, corridor=CORRIDOR)
                Pw, Nw, Iw, A = bm.world_cells()
                M = reach_matrix(bm, layout, obs, reg, ceiling)
                terr.owner, ok = assign_now(terr.owner, M, Pw, layout)
                if GIVE_REST:
                    terr.owner = give_rest(terr.owner, Pw, Nw, layout)
                rep = dict(rep, per_arm_cm2=[float(A[(terr.owner == a) & ok].sum() / 100.0)
                                             for a in range(len(layout))],
                           covered_frac=float(A[ok].sum() / max(A.sum(), 1e-9)))
                result = (terr, phases, rep, Pw, Nw, Iw, A, ceiling, obs,
                          ok, region_charts(bm), time.time() - t0)
            except Exception as e:                          # noqa: BLE001
                result = e
            with self.lock:
                if gen == self.gen:
                    self.result = result

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def adopt(self):
        """Take a finished plan. -> a line to print, or None."""
        if self.result is None or (self.owner is not None and not self.replanning):
            return None
        r = self.result
        if isinstance(r, Exception):
            self.status = f"planning failed: {r}"
            self.worker = None
            return f"  arm planning failed: {r!r}"
        terr, phases, rep, Pw, Nw, Iw, A, ceiling, obs, ok, chart, took = r
        self.ceiling = ceiling
        self.safe_now = ok
        self.checked_at = None
        self.area = np.asarray(A, float)
        self.cell_seen.clear()           # the cells themselves are new
        kept = self.kept if self.kept is not None and len(self.kept) == len(terr.owner) \
            else None
        self.kept = None
        self.gov = FLEET.FleetGovernor(self.layout, z_ceiling_mm=ceiling,
                                       body_points=obs, moving=True,
                                       d_body=D_BODY_MM, d_hold=D_HOLD_MM,
                                       d_estop=D_ESTOP_MM)
        work = {}
        for a in range(len(self.layout)):
            m = terr.owner == a
            if m.sum() < 8:
                continue
            c = CTL.CoverageController(Pw[m], Nw[m], A[m], self.layout[a],
                                       standoff=STANDOFF_MM)
            c.margin[~ok[m]] = 0.0            # not now: see feasible_now
            self.ctl[a] = (c, m)
            self.cell_region[a] = np.asarray(Iw)[m]
            self.cell_chart[a] = chart[m]
            self.cell_speed[a] = np.zeros(int(m.sum()))
            self.skipped[a] = np.zeros(int(m.sum()), bool)
            self.skip_at[a] = np.zeros(int(m.sum()))
            usable = ok[m] & (c.margin > 0.0)
            plan, _dropped = build_plan(chart[m], self.cell_region[a], usable,
                                        c.area, Pw[m], Pw[m].mean(0))
            cells = sorted({k for s in plan for k in s["cells"]})
            work[a] = self._work_samples(a, Pw[m][cells], Nw[m][cells])
        self._park(ceiling, work)
        self.work_caps = work
        self.owner = terr.owner.copy()
        self.planned_at = self.t_prev or 0.0
        self.planned_reach = None        # measured on the next frame
        self.replanning = False
        self.disabled = self._crowded()
        if self.start_from is not None:
            for a in self.disabled:          # a real arm that stays still stays put
                self.park_j[a] = tuple(self.joints[a])
                self.home[a] = np.asarray(self.sponge[a], float).copy()
        for a in self.disabled:
            # It stays where it waits: the others plan around it, and what it
            # would have scrubbed is nobody's.
            self.ctl.pop(a, None)
            work.pop(a, None)
            self.owner[self.owner == a] = -1
        # Nor do the others work where it would hold them for good.
        self.still_block = near_still(self.layout, self.park_j, sorted(self.disabled),
                                      self.owner, Pw, Nw)
        for a, (c, m) in self.ctl.items():
            c.margin[self.still_block[m]] = 0.0
            self.seen_ok[a] = c.margin > 0.0
            self.tools[a] = StrokeTool(c, self.home[a], ceiling)
            if kept is not None:
                # What this pass has already scrubbed stays scrubbed: the arms
                # carry on down the person rather than start again at the top.
                tl = self.tools[a]
                tl.count[:] = kept[np.flatnonzero(m)]
                tl.credit[:] = tl.count > 0
        self.report = rep
        # Phases of arms that have work: all of them at once, or (TAKE_TURNS)
        # apart where their arms would meet. An arm with no work stays parked.
# Leaving a stroke for another one looks like the arm losing its place, so
# it is a last resort (DEFERS_PER_PLAN), after waiting for the other arm.
        phases, self.interference = interference_phases(work, phases, len(self.layout))
        if not TAKE_TURNS:
            phases = [sorted(self.ctl)]
        self.phases = [p for p in ([a for a in ph if a in self.ctl] for ph in phases) if p]
        self.mode = {a: "parked" for a in range(len(self.layout))}
        self.settling = False
        if self.start_from is not None:
            self._settle()
        if self.phases and not self.settling:
            self._start_phase(0)
            self.status = "scrubbing"
        elif self.phases:
            self.status = "moving to where each arm waits"
        else:
            self.status = "no arm can reach anything"
        cm2 = ", ".join(f"{v:.0f}" for v in rep["per_arm_cm2"])
        still = "; ".join(f"{NAMES[a % 4]} stays still: {why}"
                          for a, why in sorted(self.disabled.items()))
        return (f"  arms planned in {took:.1f}s: {100 * rep['covered_frac']:.0f}% of "
                f"the scrub area reachable, per arm [{cm2}] cm2, phases "
                f"{self.phases}" + (f"; {self.home_note}" if self.home_note else "")
                + (f"; {still}" if still else ""))

    def _settle(self):
        """Real arms: each one with work goes from where it is to where it
        waits, through the governor, before any of them starts; the others
        wait where they are."""
        t = self.t_prev if self.t_prev is not None else 0.0
        for a in range(len(self.layout)):
            if a in self.tools and a not in self.disabled:
                self.tools[a].tool = np.asarray(self.sponge[a], float).copy()
                self.mode[a] = "returning"
                self.back_route[a] = [("joints", None, None)]
                self.back_since[a] = t
                self.blocked[a] = 0.0
                self.back_tried[a] = False
                self.reparked[a] = set()
                self.settling = True
            else:
                self.park_j[a] = tuple(self.joints[a])
                self.home[a] = np.asarray(self.sponge[a], float).copy()

    def _crowded(self, area=None):
        """Arms that cannot wait safely, and so must not move. -> {arm: why}

        Every other arm's move is checked against where these wait, so they
        are obstacles, not hazards. Moving them is. Two arms that wait within
        one step (STEP_MM) of D_ESTOP stop the fleet with their first move,
        so both stay. Two that wait inside D_HOLD can only be held, so the
        one with less to scrub stays, and the governor holds the other off
        An arm whose waiting pose is inside the person, but which has skin to
        scrub, goes on working: every move it makes is checked as any other
        arm's is, and it waits lifted off the skin where it is (self.no_park)
        rather than folding back into them. It only stays still when it has
        nothing to scrub either, since then it would just sit in them.
        """
        n = len(self.layout)
        out = {}
        if area is None:
            area = {a: float(c.area.sum()) for a, (c, _m) in self.ctl.items()}
        tree = self.gov._tree
        self.no_park = set()
        for a in range(n):
            if tree is not None and \
                    self.gov._structural_clearance(a, self.park_j[a]) < 0.0:
                if area.get(a, 0.0) >= MIN_PATCH_CM2 * 100.0:
                    self.no_park.add(a)
                else:
                    out[a] = "it has nowhere to wait outside the person or the chair"
        pairs = self.gov.fleet.pair_distances(list(self.park_j))
        for (i, k), d in sorted(pairs.items(), key=lambda kv: kv[1]):
            other = {i: NAMES[k % 4], k: NAMES[i % 4]}
            if d < D_ESTOP_MM + STEP_MM:
                for a in (i, k):
                    out.setdefault(a, f"it cannot wait clear of {other[a]}")
            elif d < D_HOLD_MM and i not in out and k not in out:
                a = min((i, k), key=lambda x: (self._still_cost(x, k if x == i else i,
                                                                area), -x))
                out[a] = f"it cannot wait clear of {other[a]}"
        return out

    def _still_cost(self, x, y, area):
        """Area lost if arm `x` stays still: its own, and what arm `y` could no
        longer work on beside it (see near_still)."""
        lost = area.get(x, 0.0)
        if y in self.ctl:
            c = self.ctl[y][0]
            blocked = near_still(self.layout, self.park_j, [x],
                                 np.full(len(c.pts), y), c.pts, c.nrm)
            lost += float(c.area[blocked & (c.margin > 0.0)].sum())
        return lost

    def _work_samples(self, a, P, N, n=48, seed=0):
        """Capsules of arm `a` at a sample of its own working poses."""
        if not len(P):
            return np.zeros((0, 5, 2, 3))
        rng = np.random.default_rng(seed + a)
        pick = rng.choice(len(P), size=min(n, len(P)), replace=False)
        out = []
        for i in pick:
            for lift in (0.0, LIFT_MM):
                tgt = P[i] + N[i] * (STANDOFF_MM + lift)
                j = K.ik(*(self.T_inv[a] @ np.r_[tgt, 1.0])[:3])
                if j is not None:
                    out.append(COL.arm_capsules(self.layout[a], *j))
        return np.asarray(out) if out else np.zeros((0, 5, 2, 3))

    def _park(self, ceiling, work=None):
        """Where each arm waits: HOME's reach, lowered under the head ceiling,
        and clear of the person by more than D_BODY.

        arm.py's HOME holds the tool 235mm above the base, which for a base
        mounted at shoulder height is above the person's head, and the
        governor rightly refuses to go there. So each arm parks with the tool
        at HOME's reach but low enough. The person moves while an arm waits,
        so a parked arm keeps PARK_MARGIN_MM more than D_BODY from them.
        """
        want_body = D_BODY_MM + PARK_MARGIN_MM
        want_gap = D_HOLD_MM + 40.0
        n = len(self.layout)
        chosen = {}
        unsafe = []
        for a, T in enumerate(self.layout):
            best, best_key = None, None
            for tcp in self._park_candidates(self.start_deg(a)):
                world = (T @ np.r_[tcp, 1.0])[:3]
                if ceiling is not None and world[2] > ceiling - 60.0:
                    continue
                j = K.ik(*tcp)
                if j is None:
                    continue
                body = self.gov._structural_clearance(a, j)
                if self.raw is not None:
                    # Clear of the person as the CAMERA sees them too: a model
                    # that is off must not park an arm against them, nor
                    # behind what the camera measured of them.
                    body = min(body, self.raw.clearance(T, j),
                               self.raw.sponge_gap(world))
                if self.scene is not None:
                    # Nothing hidden behind a measured surface, the arms
                    # themselves included: where an arm waits must be space
                    # the camera can see is free, whatever the model says.
                    body = min(body, BEHIND_MAX_MM
                               - self._behind(a, j, tool=True, everything=True))
                states = [chosen[b][1] if b in chosen else start_joints(self.start_deg(b))
                          for b in range(n)]
                states[a] = j
                pairs = self.gov.fleet.pair_distances(states)
                gap = min([d for (i, k), d in pairs.items()
                           if a in (i, k) and (i in chosen or k in chosen)],
                          default=float("inf"))
                # ...and against where every OTHER arm works: a parked arm
                # sits in the next phase's way otherwise.
                mine = np.asarray(COL.arm_capsules(T, *j))
                for b, caps in (work or {}).items():
                    if b != a and len(caps):
                        gap = min(gap, capsule_gap(mine, caps))
                if body >= want_body and gap >= want_gap:
                    best = (world, j, body, gap)
                    break                              # nearest to HOME wins
                key = min(body / want_body, gap / want_gap)
                if best_key is None or key > best_key:
                    best, best_key = (world, j, body, gap), key
            if best is None:
                best = (self.home[a], start_joints(self.start_deg(a)), float("nan"),
                        float("nan"))
            if not (best[2] >= D_BODY_MM and best[3] >= D_HOLD_MM + 40.0):
                unsafe.append(a)
            if not (best[2] >= 0.0):
                # Nowhere to wait that is not in the person: the arm is left
                # where it stands rather than put there, and works from there.
                chosen[a] = best
                self.home[a] = np.asarray(self.sponge[a], float).copy()
                self.park_j[a] = tuple(self.joints[a])
                self.gov.joints[a] = tuple(self.joints[a])
                continue
            chosen[a] = best
            self.home[a], self.park_j[a] = best[0], best[1]
            if self.start_from is None and not self.placed:
                self.joints[a], self.sponge[a] = best[1], best[0].copy()
            self.gov.joints[a] = tuple(self.joints[a])
        self.placed = True
        self.unsafe_park = unsafe
        self.home_note = ("" if not unsafe else "no safe place to wait for arm " +
                          ", ".join(str(u) for u in unsafe))

    def _work_gap(self, a, j):
        """How close arm `a`, at joints `j`, comes to where the OTHER arms
        work. mm. A waiting arm that steps into that space holds them up."""
        mine = np.asarray(COL.arm_capsules(self.layout[a], *tuple(j)[:3]))
        return min((capsule_gap(mine, caps) for b, caps in self.work_caps.items()
                    if b != a and b not in self.disabled and len(caps)),
                   default=float("inf"))

    @staticmethod
    def _park_candidates(start_deg=0.0):
        """Tool points to wait at, in an arm's own frame. Turned as the start
        pose is (start_deg) first, lower or drawn in as needed; turned
        further from it only when no such pose is safe. The base turns +/-90
        degrees."""
        tcp0 = np.array(VIZ.HOME_TCP, float)
        s = max(-START_MAX_DEG, min(START_MAX_DEG, float(start_deg)))
        yaws = sorted({s, 0.0, 30.0, -30.0, 60.0, -60.0, 85.0, -85.0},
                      key=lambda y: (abs(y - s), y))
        cands = []
        for yaw_deg in yaws:
            y = math.radians(yaw_deg)
            # about the base joint's own axis, BASE_X_MM ahead of the base
            for r in (130.0, 180.0, tcp0[0] - K.BASE_X_MM, 280.0, 100.0):
                for z_rel in (tcp0[2], 160.0, 100.0, 40.0, -40.0, -120.0, -200.0):
                    cands.append(np.array([K.BASE_X_MM + r * math.cos(y), r * math.sin(y),
                                           z_rel]))
        return cands

    def _repark(self, a):
        """Somewhere else for arm `a` to wait, near where it is, clear of the
        person and of the other arms where they are now. -> joints or None."""
        T = self.layout[a]
        cur = np.asarray(self.joints[a], float)
        tree, fleet = self.trees[None], self.gov.fleet
        tried = self.reparked.setdefault(a, set())
        best, best_d = None, None
        for tcp in self._park_candidates(self.start_deg(a)):
            if self.ceiling is not None and (T @ np.r_[tcp, 1.0])[2] > self.ceiling - 60.0:
                continue
            j = K.ik(*tcp)
            if j is None or tuple(np.round(j, 3)) in tried:
                continue
            if fleet.link_clearance(a, j, tree, moving=True) < D_BODY_MM + PARK_MARGIN_MM:
                continue
            states = list(self.joints)
            states[a] = j
            gap = min((d for (p, q), d in fleet.pair_distances(states).items()
                       if a in (p, q)), default=float("inf"))
            if gap < D_HOLD_MM + 40.0 or self._work_gap(a, j) < D_HOLD_MM + 40.0:
                continue
            dist = float(np.max(np.abs(np.asarray(j, float) - cur)))
            if best_d is None or dist < best_d:
                best, best_d = j, dist
        if best is not None:
            tried.add(tuple(np.round(best, 3)))
        return best

    def _plan(self, a, cleanup=False, resume=False):
        """Lay out arm `a`'s strokes over what it can scrub now; `resume`:
        only what this pass has not finished. -> any?"""
        tl, c = self.tools[a], self.ctl[a][0]
        usable = (c.margin > 0.0) & ~self.skipped[a] & ~tl.avoid
        if resume:
            usable &= tl.count < SWEEPS
        if cleanup:
            # Only what is worth another round: leftovers are scattered, and
            # each stroke over them costs a flight.
            usable &= ~tl.credit
            if c.area[usable].sum() < CLEANUP_FRAC * c.area.sum():
                return False
        plan, dropped = build_plan(self.cell_chart[a], self.cell_region[a], usable,
                                   c.area, c.pts + c.nrm * c.standoff, tl.tool,
                                   min_points=2 if cleanup else 1,
                                   sweeps=1 if cleanup else SWEEPS)
        if not cleanup:
            self.skip_at[a][dropped & ~self.skipped[a]] = 1e9   # too small: for good
            self.skipped[a] |= dropped
        tl.new_plan(plan)
        if plan:
            tl.rounds += 1
        return bool(plan)

    # --- phases -----------------------------------------------------------
    def _start_phase(self, i):
        self.phase_i = i
        self.phase_t0 = self.t_prev if self.t_prev is not None else 0.0
        for a in self.phases[i]:
            self.mode[a] = "working"
            self.wait_since[a] = None
            tl = self.tools[a]
            tl.rounds = 0
            tl.avoid[:] = False
            tl.refused = []
            tl.progress_t = self.phase_t0
            self._plan(a)

    def _wake(self, a, t):
        """A parked arm looks for work again: what it could not reach when it
        parked may be in reach now that the person has moved."""
        if t - self.woke_at.get(a, -1e9) < WAKE_S or a not in self.ctl:
            return
        self.woke_at[a] = t
        tl = self.tools[a]
        tl.forget(t)
        stale = self.skipped[a] & (t - self.skip_at[a] > RETRY_S)
        if stale.any():
            self.skipped[a][stale] = False
        self.seen_ok[a] |= self.ctl[a][0].margin > 0.0
        if self._plan(a, resume=True):
            self.mode[a] = "working"
            tl.progress_t = t
            self.wait_since[a] = None

    def _resume(self, a, t):
        """Arm `a` was sent back while the camera and the model disagreed;
        they agree again: on with its part of the pass."""
        self.paused.discard(a)
        tl = self.tools[a]
        c = self.ctl[a][0]
        tl.progress_t = t
        tl.refused = []
        self.wait_since[a] = None
        self.seen_ok[a] |= c.margin > 0.0
        if self._plan(a, resume=True):
            self.mode[a] = "working"
        elif AGAIN_WHILE_OTHERS_WORK and self._others_working(a) and self._plan(a):
            self.mode[a] = "working"
            self.again.add(a)

    def _new_pass(self):
        for a in self.ctl:
            self.tools[a].new_pass()
            self.skipped[a][:] = False
            self.seen_ok[a] = self.ctl[a][0].margin > 0.0
        self.passes += 1
        self.pause_for = None
        self.paused.clear()
        self.again.clear()
        self.done_pass.clear()
        self._start_phase(0)

    # --- one frame --------------------------------------------------------
    def _stilled_can_work(self):
        """Is an arm that was left standing still clear of the person now?"""
        if not self.disabled or self.trees is None:
            return False
        for a in self.disabled:
            if a >= len(self.park_j):
                continue
            j = self.park_j[a]
            if self.gov.fleet.link_clearance(a, tuple(j), self.trees[None],
                                             moving=True) > D_BODY_MM + PARK_MARGIN_MM:
                return True
        return False

    def scrubbed_cells(self):
        """How much of this pass every cell of the person has had, over all
        the arms, in the model's own cell order. -> (cells,) or None"""
        if not self.ctl or self.owner is None:
            return None
        out = np.zeros(len(self.owner))
        for a, (_c, m) in self.ctl.items():
            out[np.flatnonzero(m)] = self.tools[a].count
        return out

    def reach_now(self):
        """How much skin the arms can reach as the person is posed now, cm2."""
        return sum(float(c.area[c.margin > 0.0].sum()) / 100.0
                   for c, _m in self.ctl.values())

    def _maybe_replan(self, body, t):
        """Solve the territories again when the arms can reach much less
        than when they were solved: the person has moved since."""
        if self.owner is None or body is None or self.replanning:
            return
        if self.worker is not None and self.worker.is_alive():
            return
        if self.planned_reach is None:
            self.planned_reach = self.reach_now()
            return
        if t - self.planned_at < REPLAN_MIN_S or self.planned_reach <= 1.0:
            return
        if self._stilled_can_work():
            # An arm was left standing still because it had nowhere to wait
            # clear of the person. They have moved since, and it has.
            self.status = "planning again: an arm can work again"
            self.replanning = True
            self.kept = self.scrubbed_cells()
            self.start_plan(body)
            return
        if self.reach_now() < REPLAN_FRAC * self.planned_reach:
            self.status = "planning again: you have moved"
            self.replanning = True
            self.kept = self.scrubbed_cells()
            self.start_plan(body)

    def step(self, body, t, hold=False):
        """Move the arms for one frame. `hold`: the person is not in view."""
        dt = 0.0 if self.t_prev is None else min(max(t - self.t_prev, 0.0),
                                                 VIZ.DT_MAX_S)
        self.t_prev = t
        if self.gov is None or self.estopped or not self.phases:
            return
        if hold:
            if self.lost_since is None:
                self.lost_since = t
            if t - self.lost_since >= LOST_S and self.trees is not None:
                self._lift_off_all(dt, t)
            return
        if self.lost_since is not None:
            for tl in self.tools.values():
                tl.progress_t += t - self.lost_since      # held, not stalled
        self.lost_since = None
        self.frame += 1
        self._maybe_replan(body, t)
        Pl, Nl, Il, _A = body.scrub_cells()
        untrusted = (np.isin(Il, np.fromiter(self.distrust, int, len(self.distrust)))
                     if self.distrust else np.zeros(len(Il), bool))
        for a, (c, m) in self.ctl.items():
            c.refresh(Pl[m], Nl[m])
            c.margin[~self.safe_now[m] | self.still_block[m] | untrusted[m]] = 0.0
            self.seen_ok[a] |= c.margin > 0.0
            seen = self.cell_seen[a]
            seen.append((t, c.pts))
            while len(seen) > 2 and t - seen[1][0] >= SPEED_WINDOW_S:
                seen.popleft()
            t0, p0 = seen[0]
            if t - t0 > 1e-3:
                self.cell_speed[a] = np.linalg.norm(c.pts - p0, axis=1) / (t - t0)
        self._recheck(body, t)
        if self.trees is None or self.frame % TREE_EVERY == 0:
            obs, reg = body.obstacles()
            self.trees = {None: cKDTree(obs)}
            self._model_mesh(body, at=id(body))
            self.limbs = {}
            for rid in np.unique(reg[reg >= 0]):
                self.trees[int(rid)] = cKDTree(obs[reg != rid])
                self.limbs[int(rid)] = cKDTree(obs[reg == rid])

        # A slow frame is moved in several checked steps, each as short as a
        # fast frame's: the arms keep their speed, and no point of an arm moves
        # further between two checks than it would at SUBSTEP_S.
        k = min(MAX_SUBSTEPS, max(1, math.ceil(dt / SUBSTEP_S - 1e-9)))
        for i in range(k):
            self._substep(dt / k, t - dt + dt * (i + 1) / k)
            if self.estopped:
                break

        pairs = self.gov.fleet.pair_distances(list(self.joints))
        self.gap_now = min(pairs.values()) if pairs else float("inf")
        self.gap_min = min(self.gap_min, self.gap_now)
        self.gov._tree = self.trees[None]

    def _substep(self, dt, t):
        """One checked step of every arm, `dt` seconds long, ending at `t`."""
        self.substep_dt = dt
        if self.settling:
            for a in range(len(self.layout)):
                if self.mode.get(a) == "returning":
                    if not self._lagging(a, t):
                        self._return(a, dt, t)
                elif a not in self.disabled and not self.estopped:
                    self._keep_away(a, t)
            if all(m != "returning" for m in self.mode.values()):
                self.settling = False
                self.status = "scrubbing"
                self._start_phase(0)
            return
        if self.pause_for is not None:
            if not self.model_lost():
                self.pause_for += dt             # the next pass waits for trust
            if self.pause_for >= PASS_PAUSE_S:
                self._new_pass()
        else:
            phase = self.phases[self.phase_i]
            if not self.model_lost():
                for a in phase:
                    if a in self.paused and self.mode[a] == "parked":
                        self._resume(a, t)
            for a in phase:
                if self.estopped:
                    return
                if self.mode[a] == "parked" and a not in self.paused:
                    self._wake(a, t)
                if self._lagging(a, t):
                    continue
                if self.mode[a] == "working":
                    self._work(a, dt, t)
                elif self.mode[a] == "returning":
                    self._return(a, dt, t)
            phase_over = (all(a in self.done_pass for a in self.phases[self.phase_i])
                          if ALWAYS_SCRUBBING else
                          all(self.mode[a] == "parked" and a not in self.paused
                              for a in self.phases[self.phase_i]))
            if phase_over:
                prog = self.progress()
                self.history.append((self.passes, self.phase_i + 1, t,
                                     {a: prog[a][0] for a in self.phases[self.phase_i]}))
                if self.phase_i + 1 < len(self.phases):
                    self._start_phase(self.phase_i + 1)
                else:
                    self.pause_for = 0.0
        for a in range(len(self.layout)):
            tl = self.tools.get(a)
            idle = self.mode.get(a) == "parked" or (
                self.waiting.get(a) and tl is not None and not tl.contact)
            if idle and a not in self.disabled and not self.estopped:
                self._keep_away(a, t)

    def _recheck(self, body, t):
        """Refresh safe_now from the current pose, off the frame loop."""
        busy = self.checker is not None and self.checker.is_alive()
        if busy or (self.checked_at is not None and t - self.checked_at < RECHECK_S):
            return
        J = np.array([body.joints[k] for k in sorted(body.joints)], float)             if body.joints else None
        if J is not None and self.checked_pose is not None                 and J.shape == self.checked_pose.shape                 and float(np.abs(J - self.checked_pose).max()) < RECHECK_MM:
            self.checked_at = t          # they have not moved: it still holds
            return
        self.checked_pose = J
        if self.checker is not None and self.checked is not None:
            self.safe_now = self.checked
        self.checked_at = t
        bm = body.as_model()
        obs, reg = body.obstacles()
        owner, layout, ceiling = self.owner, self.layout, body.head_ceiling()
        gen = self.gen

        def work():
            ok = feasible_now(bm, layout, owner, obs, reg, ceiling)
            with self.lock:
                if gen == self.gen:
                    self.checked = ok

        self.checker = threading.Thread(target=work, daemon=True)
        self.checker.start()

    def _propose(self, a, goal, t, normal=None, tree=None, limb=None):
        """Ask the governor. `tree` is the body to clear; `limb`, the part it
        leaves out, which must still be cleared by D_LIMB_MM."""
        self.gov._tree = tree if tree is not None else self.trees[None]
        x, y, z = (self.T_inv[a] @ np.r_[goal, 1.0])[:3]
        n = None if normal is None else self.T_inv[a][:3, :3] @ normal
        v = self._limb_check(a, (x, y, z), limb)
        if v is None:
            v = self._raw_refused(a, (x, y, z))
        if v is None:
            v = self.gov.propose(a, x, y, z, t=t, normal=n)
        self.verdicts[v.action] += 1
        if v.action != FLEET.CLEAR:
            key = (a, self.mode.get(a), v.action, v.reason)
            self.why[key] += 1
            if v.action == FLEET.REFUSE and "clearance_mm" in v.detail:
                self.why_mm.setdefault(key, []).append(v.detail["clearance_mm"])
        if v.action == FLEET.ESTOP:
            self.estopped = True
            self.status = ("STOPPED: two arms came inside the stop distance. "
                           "This should never happen")
        return v

    def _limb_check(self, a, xyz, limb):
        """A REFUSE if the pose at `xyz` passes through `limb`, else None.

        Backing out of the limb is allowed, as the governor allows backing
        away from the person.
        """
        tree = self.limbs.get(limb)
        if tree is None:
            return None
        j = K.ik(*xyz)
        if j is None:
            return None                     # the governor says why
        fleet = self.gov.fleet
        after = fleet.link_clearance(a, j, tree, moving=True)
        if after >= D_LIMB_MM:
            return None
        if after > fleet.link_clearance(a, self.gov.joints[a], tree,
                                        moving=True) + FLEET.SEPARATING_MM:
            return None
        return FLEET.Verdict(FLEET.REFUSE,
                             reason="structure would pass through the limb it scrubs",
                             detail={"clearance_mm": after})

    def _tree_for(self, a, cell, near):
        """-> (the body arm `a` must clear, the part left out of it or None).

        All of the body, except, once the arm is near, the part it is about
        to touch; that part is still checked against D_LIMB_MM.
        """
        if cell is None or not near:
            return self.trees[None], None
        rid = int(self.cell_region[a][cell])
        if rid not in self.trees:
            return self.trees[None], None
        return self.trees[rid], rid

    def _world(self, a, tcp):
        return (self.layout[a] @ np.r_[np.asarray(tcp, float), 1.0])[:3]

    def _moved(self, a, goal, joints, trace=False):
        self._count_jump(a, joints)
        if self.substep_dt:
            v = float(np.linalg.norm(np.asarray(goal, float) - self.sponge[a])) / self.substep_dt
            self.plan_v[a] = 0.7 * self.plan_v.get(a, v) + 0.3 * v
        tl = self.tools.get(a)
        if tl is not None:
            tl.tool = np.asarray(goal, float).copy()
        self.joints[a] = joints
        self.sponge[a] = np.asarray(goal, float).copy()
        self.trails[a].append(self.sponge[a].copy() if trace else None)

    # --- moving -----------------------------------------------------------
    def _line_step(self, a, target, speed, dt, t, normal, tree, trace=False,
                   limb=None):
        """One checked step in a straight line. -> (verdict or None, arrived)

        None: the line needs a pose the arm cannot take.
        """
        tl = self.tools[a]
        d = np.asarray(target, float) - tl.tool
        dist = float(np.linalg.norm(d))
        if dist < 0.5:
            return THERE, True
        stride = min(dist, max(speed * dt, 1.0))
        goal = tl.tool + d * (stride / dist)
        for _ in range(8):
            j = K.ik(*(self.T_inv[a] @ np.r_[goal, 1.0])[:3])
            if j is None:
                return None, False
            swing = float(np.max(np.abs(np.subtract(j, self.joints[a]))))
            if swing <= MAX_SWING_RAD or stride <= 1.0:
                break
            stride = max(stride * MAX_SWING_RAD / swing * 0.9, 1.0)
            goal = tl.tool + d * (stride / dist)
        if swing > MAX_SWING_RAD:
            return None, False                # the solver flipped the elbow
        goal, f = self._slowed(a, goal, dt, contact=bool(tl.contact), normal=normal)
        if f <= 0.0:
            return None, False                # it would press too far in
        stride *= f
        self.gov.contact[a] = bool(tl.contact)
        v = self._propose(a, goal, t, normal=normal, tree=tree, limb=limb)
        if v.action != FLEET.CLEAR:
            return v, False
        self._moved(a, goal, v.joints, trace)
        if stride >= dist - 1e-6:
            return v, True
        if self.lifted[a] > 0.0 and normal is not None:
            # As deep as it may go: there once over the target in the skin's plane.
            n = np.asarray(normal, float)
            rest = np.asarray(target, float) - goal
            return v, float(np.linalg.norm(rest - n * float(rest @ n))) < ARRIVED_MM
        return v, False

    def _sweep_step(self, a, k, dt, t, tree, limb=None):
        """One checked step along a stroke, on the skin. -> (verdict or None,
        arrived at cell `k`)

        The sponge's place on the stroke (tl.base) moves on toward `k` at
        SCRUB_V, and the sponge rubs across the stroke around that place. A
        step the joints cannot take at once is shortened, rub and all.
        """
        tl = self.tools[a]
        way = tl.where(k, 0.0)
        if tl.base is None or float(np.linalg.norm(tl.base - tl.tool)) > \
                SCRUB_AMP_MM + 5.0 + self.lifted[a]:
            tl.base = tl.tool.copy()          # moved some other way meanwhile
        d = way - tl.base
        dist = float(np.linalg.norm(d))
        stride = min(dist, SCRUB_V * dt)
        on = tl.base + (d * (stride / dist) if dist > 1e-9 else 0.0)
        off = self._rub(tl, k, t, self._rub_amp(a, k))
        if tl.ceiling is not None:
            # Not above the head: rub level where the stroke runs close to it.
            off[2] = min(off[2], max(tl.ceiling - 2.0 - on[2], 0.0))
        full = on + off
        frac = 1.0
        for _ in range(8):
            goal = tl.tool + (full - tl.tool) * frac
            j = K.ik(*(self.T_inv[a] @ np.r_[goal, 1.0])[:3])
            if j is None:
                return None, False
            swing = float(np.max(np.abs(np.subtract(j, self.joints[a]))))
            if swing <= MAX_SWING_RAD:
                break
            frac *= MAX_SWING_RAD / swing * 0.9
        if swing > MAX_SWING_RAD:
            return None, False                # the solver flipped the elbow
        goal, f = self._slowed(a, goal, dt, contact=True, normal=tl.c.nrm[k])
        if f <= 0.0:
            return None, False                # it would press too far in
        frac *= f
        self.gov.contact[a] = True
        v = self._propose(a, goal, t, normal=tl.c.nrm[k], tree=tree, limb=limb)
        if v.action != FLEET.CLEAR:
            return v, False
        tl.base = tl.base + (on - tl.base) * frac
        self._moved(a, goal, v.joints, trace=True)
        return v, frac >= 1.0 and dist - stride < 0.5

    def _joint_step(self, a, target, dt, t, normal, tree, limb=None):
        """One checked step through the air, in JOINT space.

        A straight line for the sponge from where the arm waits to where it
        works can drag the arm's links through a thigh; turning each joint
        toward the pose that reaches the target unfolds the arm instead.
        """
        jt = K.ik(*(self.T_inv[a] @ np.r_[target, 1.0])[:3])
        if jt is None:
            return None, False
        cur = np.asarray(self.joints[a], float)
        s = min(MAX_SWING_RAD - IK_SLACK_RAD, JOINT_V * max(dt, 1e-3))
        nxt = cur + np.clip(np.asarray(jt, float) - cur, -s, s)
        nxt, f = self._slowed_joints(a, cur, nxt, dt, v_max=AIR_V)
        if f <= 0.0:
            return None, False
        tcp = np.array(K.fk(*nxt), float)
        back = K.ik(*tcp)
        if back is None or float(np.max(np.abs(np.subtract(back, nxt)))) > IK_SLACK_RAD:
            return None, False                  # would need the other elbow
        goal = self._world(a, tcp)
        self.gov.contact[a] = False
        v = self._propose(a, goal, t, normal=normal, tree=tree, limb=limb)
        if v.action == FLEET.CLEAR:
            self._moved(a, goal, v.joints)
        return v, (v.action == FLEET.CLEAR
                   and float(np.linalg.norm(goal - target)) < ARRIVED_MM)

    def _clear_of(self, arms):
        """-> test(points): are all of them DEFER_CLEAR_MM or more from the
        capsules of `arms`, as they are now?"""
        A, B = [], []
        for b in arms:
            for p, q in COL.arm_capsules(self.layout[b], *tuple(self.joints[b])[:3]):
                A.append(p)
                B.append(q)
        if not A:
            return lambda P: True
        A, B = np.asarray(A, float), np.asarray(B, float)
        AB = B - A
        L2 = np.maximum((AB * AB).sum(1), 1e-9)

        def test(P):
            P = np.asarray(P, float)[:, None, :]
            t = np.clip(((P - A[None]) * AB[None]).sum(2) / L2[None], 0.0, 1.0)
            d = np.linalg.norm(P - (A[None] + t[..., None] * AB[None]), axis=2)
            return bool(d.min() >= DEFER_CLEAR_MM)
        return test

    def _go_elsewhere(self, a, away_from):
        """Do another stroke first, one clear of the arms `away_from`.
        -> did it?"""
        tl = self.tools[a]
        if tl.defers >= DEFERS_PER_PLAN or not tl.defer(self._clear_of(away_from)):
            return False
        self.verdicts["went elsewhere"] += 1
        return True

    def _next_clear(self, a):
        """Between strokes: if the next one starts beside another working
        arm, do one that does not first."""
        tl = self.tools[a]
        if not GIVE_WAY or tl.defers >= DEFERS_PER_PLAN:
            return
        seg = tl.seg
        if seg >= len(tl.plan) - 1 or tl.wp < len(tl.plan[seg]["cells"]) - 1:
            return                               # mid-stroke, or nothing after
        if tl.contact and tl.plan[seg + 1]["joined"]:
            return                               # it goes on along the skin
        others = [b for b in range(len(self.layout))
                  if b != a and self.mode.get(b) == "working"]
        if not others:
            return
        test = self._clear_of(others)
        c = tl.c
        cells = tl.plan[seg + 1]["cells"]
        if not test(c.pts[cells] + c.nrm[cells] * c.standoff) and tl.defer(test):
            self.verdicts["went elsewhere"] += 1

    def _gap(self, a, b, joints_a=None):
        states = list(self.joints)
        if joints_a is not None:
            states[a] = tuple(joints_a)
        return self.gov.fleet.pair_distances(states)[(min(a, b), max(a, b))]

    def _nearest_arm(self, a):
        pairs = self.gov.fleet.pair_distances(list(self.joints))
        near = [(d, q if p == a else p) for (p, q), d in pairs.items() if a in (p, q)]
        return min(near)[1] if near else None

    def _held(self, a, t, dt):
        """The governor held arm `a`. Wait a moment; then one of the two
        arms gives way (see YIELD_GAP_MM)."""
        tl = self.tools[a]
        tl.held += dt
        if not GIVE_WAY or tl.held <= DEFER_S:
            return
        b = self._nearest_arm(a)
        if b is None:
            return
        if self.mode.get(b) == "working" and b not in self.disabled:
            low, high = max(a, b), min(a, b)
            if low not in self.yielding:
                self.yielding[low] = (high, t, False)
                self.verdicts["gave way"] += 1
            if low != a:
                return                    # the other one steps aside
        elif a not in self.yielding:
            # The arm in the way is not going to move (it waits, or stays
            # still): this one backs off it, then does a stroke clear of it.
            self.yielding[a] = (b, t, True)
            self.verdicts["gave way"] += 1
        tl.held = 0.0

    def _step_away(self, a, b, t):
        """One checked joint step that opens the gap from arm `a` to `b`
        without closing on the person. -> moved?"""
        cur = np.asarray(self.joints[a], float)
        tree = self.trees[None]
        fleet = self.gov.fleet
        states = list(self.joints)

        def gaps(j):
            """-> (gap to b, gap to the nearest arm)"""
            states[a] = tuple(j)
            pairs = fleet.pair_distances(states)
            return (pairs[(min(a, b), max(a, b))],
                    min(dd for (p, q), dd in pairs.items() if a in (p, q)))

        g_now, w_now = gaps(cur)
        body_now = fleet.link_clearance(a, tuple(cur), tree, moving=True)
        s = MAX_SWING_RAD - IK_SLACK_RAD
        best = None
        for d in itertools.product((-s, 0.0, s), repeat=3):
            if not any(d):
                continue
            nxt = cur + np.asarray(d)
            if not K.within_limits(*nxt):
                continue
            g, w = gaps(nxt)
            if g <= g_now + FLEET.SEPARATING_MM or (best is not None and g <= best[0]):
                continue
            if w < D_HOLD_MM and w <= w_now + FLEET.SEPARATING_MM:
                continue                  # the governor would hold it
            # What the governor will allow: clear of the person, or backing off.
            body = fleet.link_clearance(a, tuple(nxt), tree, moving=True)
            if body < D_BODY_MM and body <= body_now + FLEET.SEPARATING_MM:
                continue
            tcp = np.array(K.fk(*nxt), float)
            if self.gov.z_ceiling is not None and \
                    self._world(a, tcp)[2] > self.gov.z_ceiling:
                continue                  # the sponge stays below the head
            back = K.ik(*tcp)
            if back is None or float(np.max(np.abs(np.subtract(back, nxt)))) > IK_SLACK_RAD:
                continue
            best = (g, nxt, tcp)
        if best is None:
            return False
        dt = self.substep_dt or SUBSTEP_S
        nxt, f = self._slowed_joints(a, cur, best[1], dt)
        if f <= 0.0:
            return False
        best = (best[0], nxt, np.array(K.fk(*nxt), float))
        self.gov._tree = tree
        self.gov.contact[a] = False
        v = self._raw_refused(a, tuple(best[2])) or self.gov.propose(a, *best[2], t=t)
        self.verdicts[v.action] += 1
        if v.action != FLEET.CLEAR:
            return False
        self._moved(a, self._world(a, best[2]), v.joints)
        return True

    def _give_way(self, a, dt, t):
        """A frame of arm `a` giving way. -> still giving way?"""
        b, since, still = self.yielding[a]
        tl = self.tools[a]
        if (not still and self.mode.get(b) != "working") or t - since > YIELD_MAX_S + 1.0:
            del self.yielding[a]
            return False
        if tl.contact and tl.last is not None:
            # Off the skin first.
            if not tl.route or not tl.route[0].get("rise"):
                tl.route = [{"kind": "line", "cell": tl.last, "lift": LIFT_MM,
                             "rise": True}]
            self._leg(a, dt, t)
            return True
        if self._gap(a, b) < YIELD_GAP_MM and t - since < YIELD_MAX_S \
                and self._step_away(a, b, t):
            return True
        del self.yielding[a]
        tl.route = []                 # come at its target again, from here
        tl.held = 0.0
        self._go_elsewhere(a, [b])
        return False

    def _retreated(self, a, v, t):
        """The governor backed a held arm off. Take it if it does not close on
        another arm; either way, come at the point again (or at another
        stroke, clear of the arm that held it)."""
        tl = self.tools[a]
        swing = (float(np.max(np.abs(np.subtract(v.joints, self.joints[a]))))
                 if v.joints is not None else float("inf"))
        # The back-off is 20 mm, but near the base's axis 20 mm can swing the
        # whole arm: take it only if it is one checked step.
        if v.target is not None and swing <= MAX_SWING_RAD and \
                self._retreat_ok(a, v.joints):
            self._moved(a, self._world(a, v.target), v.joints)
        else:
            self.gov.joints[a] = self.joints[a]      # stays where it is
            self.trails[a].append(None)
        tl.contact = False
        tl.route = []
        if GIVE_WAY and str(v.reason).startswith("held"):
            b = self._nearest_arm(a)
            if b is not None and self._go_elsewhere(a, [b]):
                return
        tl.retreats += 1
        if tl.target is not None and tl.retreats > RETREATS_PER_POINT:
            tl.refuse(tl.target, t)

    def _retreat_ok(self, a, j):
        """The governor backs a held arm off without asking the other arms.
        Take the back-off only if it does not close on any of them."""
        now = list(self.joints)
        then = list(self.joints)
        then[a] = j

        def gap(states):
            pairs = self.gov.fleet.pair_distances(states)
            return min((d for (p, q), d in pairs.items() if a in (p, q)),
                       default=float("inf"))

        g_then = gap(then)
        return g_then >= D_HOLD_MM or g_then >= gap(now)

    def _work(self, a, dt, t):
        tl = self.tools[a]
        tl.forget(t)
        stale = self.skipped[a] & (t - self.skip_at[a] > RETRY_S)
        if stale.any():
            self.skipped[a][stale] = False
        if a in self.yielding and self._give_way(a, dt, t):
            self.waiting[a] = False
            return
        if a in self.again and not self._others_working(a):
            self.again.discard(a)              # the others are done: so is this
            self.waiting[a] = False
            self._go_home(a, t)
            return
        if self.model_lost():
            # The camera does not agree with the model as a whole: nothing is
            # touched. Back to where it waits; its part of the pass goes on
            # once they agree. One part on its own is left alone instead (see
            # _leg), and the arms go on scrubbing the rest of the person.
            self.paused.add(a)
            self.waiting[a] = False
            self._go_home(a, t)
            return
        if t - tl.progress_t > STALL_S:
            # Nothing new scrubbed for a long while: the person keeps
            # moving, or what is left keeps being refused. Stop here.
            self.waiting[a] = False
            self._go_home(a, t)
            return
        if not tl.route:
            on_skin = False
            if tl.target is None:
                self._next_clear(a)
                usable = (tl.c.margin > 0.0) & ~self.skipped[a] & ~tl.avoid
                nxt = tl.advance(usable)
                if nxt is None:
                    self._plan_done(a, t)
                    return
                tl.target, on_skin = nxt
                tl.dwell = 0.0
            tl.route = self._legs(tl, tl.target, on_skin)
        self.waiting[a] = False
        self._leg(a, dt, t)

    def _legs(self, tl, cell, on_skin):
        if on_skin:
            return [{"kind": "line", "cell": cell, "lift": 0.0}]
        legs = []
        if tl.contact and tl.last is not None:
            legs.append({"kind": "line", "cell": tl.last, "lift": LIFT_MM, "rise": True})
        return legs + [{"kind": "air", "cell": cell, "lift": LIFT_MM},
                       {"kind": "line", "cell": cell, "lift": 0.0}]

    def _leg(self, a, dt, t):
        """One frame of the leg the arm is on."""
        tl = self.tools[a]
        c = tl.c
        leg = tl.route[0]
        k, lift = leg["cell"], leg["lift"]
        if not leg.get("rise") and int(self.cell_region[a][k]) in self.distrust:
            # The camera does not agree with the model of that part: off it,
            # and on with a part they do agree about.
            self.waiting[a] = False
            if tl.contact and tl.last is not None:
                tl.route = [{"kind": "line", "cell": tl.last, "lift": LIFT_MM,
                             "rise": True}]
            else:
                tl.route = []
                tl.target = None
            return
        near = tl.contact or lift == 0.0 or float(
            np.linalg.norm(tl.where(k, lift) - tl.tool)) < APPROACH_MM
        if near and not leg.get("rise") and k == tl.target \
                and self.cell_speed[a][k] > CHASE_V:
            # The limb is moving: wait for it rather than chase it, and wait
            # off the skin. Far from it, the arm flies on meanwhile.
            if tl.contact and tl.last is not None:
                tl.route = self._legs(tl, k, False)
                return
            self.waiting[a] = "still"
            tl.chase += dt
            if tl.chase > CHASE_WAIT_S:
                tl.refuse(k, t)
                tl.skip_stroke()
                tl.chase = 0.0
            return
        tl.chase = 0.0
        if leg.get("path"):
            self._path_leg(a, leg, dt, t)
            return
        target = tl.where(k, lift)
        sweeping = lift == 0.0 and tl.contact
        dist = float(np.linalg.norm(target - tl.tool))
        tree, limb = self._tree_for(a, k, lift == 0.0 or dist < APPROACH_MM)
        # A far leg turns the joints, a near one flies straight, decided when
        # the leg starts; a leg that is refused one way tries the other.
        if "joint" not in leg:
            leg["joint"] = leg["kind"] == "air" and dist > HOP_MM
        if sweeping:
            v, arrived = self._sweep_step(a, k, dt, t, tree, limb)
        elif leg["joint"]:
            v, arrived = self._joint_step(a, target, dt, t, c.nrm[k], tree, limb)
        else:
            v, arrived = self._line_step(a, target, AIR_V, dt, t, c.nrm[k], tree,
                                         limb=limb)
        if v is None or v.action == FLEET.REFUSE:
            if v is None:
                self.verdicts[FLEET.REFUSE] += 1
                self.why[(a, "working", FLEET.REFUSE, "no pose reaches it")] += 1
            self._refused(a, leg, t)
        elif v.action == FLEET.CLEAR:
            tl.held = 0.0
            if lift > 0.0:
                tl.contact = False
            dwell_for = DWELL_S * (DWELL_AGAIN if a in self.again else 1.0)
            if (arrived and lift == 0.0 and tl.dwell < dwell_for
                    and self.cell_speed[a][k] <= CHASE_V):
                # Scrub this spot before moving on along the stroke, unless
                # that patch of the person is on the move: then the sponge
                # goes on rather than wait for them to lean into it.
                tl.contact = True
                tl.dwell += self.substep_dt or 0.0
                if tl.rub(k):
                    tl.progress_t = t
                return
            if arrived:
                tl.route.pop(0)
                tl.stuck = 0
                if lift == 0.0:
                    tl.contact = True
                    if tl.rub(k):
                        tl.progress_t = t
                    tl.last = k
                    tl.target = None
                    tl.refusals = 0
                    tl.retreats = 0
        elif v.action == FLEET.RETREAT:
            self._retreated(a, v, t)
        elif v.action == FLEET.HOLD:
            # Wait where it is, for a moment; then one of the two gives way.
            # The governor turns a long hold into a retreat.
            self._held(a, t, dt)

    def _way_around(self, a, leg):
        """A joint path to the pose this air leg is for. -> list or []"""
        tl = self.tools[a]
        goal = tl.where(leg["cell"], leg["lift"])
        jt = K.ik(*(self.T_inv[a] @ np.r_[goal, 1.0])[:3])
        if jt is None:
            return []
        path = joint_path(self.gov.fleet, a, self.layout[a], self.joints[a], jt,
                          self.trees[None], self.gov.z_ceiling)
        if not path:
            return []
        self.verdicts["went around"] += 1
        return [tuple(float(v) for v in q) for q in path]

    def _path_step(self, a, path, dt, t):
        """One checked joint step toward the first pose of `path`, which is
        dropped once reached. -> the verdict, or None if the step would need
        the other elbow."""
        cur = np.asarray(self.joints[a], float)[:3]
        w = np.asarray(path[0], float)
        s = min(MAX_SWING_RAD - IK_SLACK_RAD, JOINT_V * max(dt, 1e-3))
        nxt = cur + np.clip(w - cur, -s, s)
        nxt, f = self._slowed_joints(a, cur, nxt, dt, v_max=AIR_V)
        if f <= 0.0:
            return None
        tcp = np.array(K.fk(*nxt), float)
        back = K.ik(*tcp)
        if back is None or float(np.max(np.abs(np.subtract(back, nxt)))) > IK_SLACK_RAD:
            return None
        self.gov.contact[a] = False
        goal = self._world(a, tcp)
        v = self._propose(a, goal, t, tree=self.trees[None])
        if v.action == FLEET.CLEAR:
            self._moved(a, goal, v.joints)
            if f >= 1.0 and float(np.max(np.abs(w - nxt))) < 1e-6:
                path.pop(0)
        return v

    def _path_leg(self, a, leg, dt, t):
        """One checked joint step along an air leg's path; the leg itself
        takes over once the path is done."""
        tl = self.tools[a]
        v = self._path_step(a, leg["path"], dt, t)
        if v is not None and v.action == FLEET.CLEAR:
            tl.held = 0.0
            tl.contact = False
        elif v is None or v.action == FLEET.REFUSE:
            # The person moved into the way: fly at the pose again as usual.
            leg["path"] = []
            self._refused(a, leg, t)
        elif v.action == FLEET.RETREAT:
            self._retreated(a, v, t)
        elif v.action == FLEET.HOLD:
            self._held(a, t, dt)

    def _rub_amp(self, a, k):
        """How wide the rub is on cell `k` of arm `a`: narrow on a limb."""
        reg = self.cell_region.get(a)
        if reg is None or k >= len(reg):
            return SCRUB_AMP_MM
        return SCRUB_AMP_MM if int(reg[k]) == 0 else SCRUB_AMP_LIMB_MM

    @staticmethod
    def _rub(tl, k, t, amp=SCRUB_AMP_MM):
        """Where the sponge is across its stroke now: side to side in the
        skin's plane, square to the way the stroke runs, or across the body
        where the stroke is one point (the sponge rubs there as well, which
        is what it is for). -> offset (3,)"""
        c = tl.c
        along = None
        if tl.last is not None and tl.last != k:
            along = np.asarray(c.pts[k] - c.pts[tl.last], float)
        if along is None or float(np.linalg.norm(along)) < 1e-6:
            along = np.array([0.0, 0.0, 1.0])        # up the person
        for ax in (along, np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])):
            side = np.cross(c.nrm[k], ax)
            L = float(np.linalg.norm(side))
            if L > 1e-6:
                return side / L * (amp * math.sin(2.0 * math.pi * SCRUB_HZ * t))
        return np.zeros(3)

    def _refused(self, a, leg, t):
        tl = self.tools[a]
        if leg["lift"] == 0.0:
            # A point on the skin the arm may not touch now.
            tl.refuse(leg["cell"], t)
            tl.refusals += 1
            if tl.refusals >= WAYPOINT_REFUSALS:
                tl.skip_stroke()
                tl.refusals = 0
            self.trails[a].append(None)
            return
        tl.stuck += 1
        if leg["kind"] == "line" and len(tl.route) > 1:
            tl.route.pop(0)           # it cannot rise straight up: fly from here
            return
        if leg["kind"] == "air" and tl.stuck >= 2 and "path" not in leg:
            # Refused turning and refused flying straight: find a way round.
            leg["path"] = self._way_around(a, leg)
            if leg["path"]:
                tl.stuck = 0
                return
        leg["joint"] = not leg.get("joint", False)
        if tl.stuck >= STUCK_FRAMES and tl.target is not None:
            tl.refuse(tl.target, t)
            tl.skip_stroke()
            tl.stuck = 0

    def _plan_done(self, a, t):
        """Arm `a` reached the end of its plan."""
        tl, c = self.tools[a], self.ctl[a][0]
        if tl.rounds < MAX_ROUNDS and self._plan(a, cleanup=True):
            self.wait_since[a] = None
            return
        # Nothing left it can reach now. A limb that is raised or turned
        # comes back, so wait a while before calling the patch done: for
        # what it could reach earlier this pass, not what it never could.
        left = (~tl.credit & ~self.skipped[a] & ~tl.avoid & (c.margin <= 0.0)
                & self.seen_ok[a])
        if c.area[left].sum() > 0.1 * c.area.sum():
            if self.wait_since.get(a) is None:
                self.wait_since[a] = t
            if t - self.wait_since[a] < PHASE_WAIT_S:
                self.waiting[a] = True
                if tl.contact and tl.last is not None:
                    # Wait off the skin, not on it.
                    tl.route = [{"kind": "line", "cell": tl.last, "lift": LIFT_MM,
                                 "rise": True}]
                return
        self.waiting[a] = False
        self.wait_since[a] = None
        self.done_pass.add(a)
        if ALWAYS_SCRUBBING and self._plan(a):
            # Round again where it is: an arm is never idle while it can reach
            # the person at all.
            self.again.add(a)
            tl.progress_t = t
            return
        if AGAIN_WHILE_OTHERS_WORK and self._others_working(a) and self._plan(a):
            # Its part of the pass is done and another arm's is not: over
            # its patches again rather than wait.
            self.again.add(a)
            tl.progress_t = t
            return
        self._go_home(a, t)

    def model_lost(self):
        """Is the model as a whole not to be trusted? The torso is the anchor
        of the fit, so the person is left alone when it is out, or when most
        of the parts are. A limb out is that limb's own: a forearm the person
        is moving is the part the fit gets wrong, and it is left alone while
        the arms go on with the rest of them."""
        return 0 in self.distrust or len(self.distrust) >= MODEL_LOST_PARTS

    def _others_working(self, a):
        """Is another arm of this phase still on its own part of the pass?"""
        if not self.phases or self.pause_for is not None:
            return False
        return any(b != a and self.mode.get(b) == "working" and b not in self.again
                   and b not in self.paused for b in self.phases[self.phase_i])

    def _go_home(self, a, t):
        """Lift straight off the skin, then fold back to the parking pose.

        The fold is in JOINT space: a straight line for the sponge from the
        body to the parking pose can run the arm's links through the person,
        where turning each joint toward its parked angle folds the arm away.
        """
        tl, c = self.tools[a], self.ctl[a][0]
        if a in self.no_park:
            # Nowhere to fold back to that is not in the person: off the skin
            # and hold there, where every move was checked on the way in.
            self.park_j[a] = tuple(self.joints[a])
            self.home[a] = np.asarray(self.sponge[a], float).copy()
        self.mode[a] = "returning"
        self.yielding.pop(a, None)
        self.back_since[a] = t
        self.blocked[a] = 0.0
        self.back_tried[a] = False
        self.reparked[a] = set()
        # What it was refused is not held against it.
        for k in tl.refused:
            near = tl.footprint(k)
            sk = near[~tl.credit[near]]
            self.skipped[a][sk] = True
            self.skip_at[a][sk] = t
        way = []
        if tl.last is not None and \
                float(np.linalg.norm(tl.tool - tl.where(tl.last, 0.0))) < LIFT_OFF_MM:
            n = c.nrm[tl.last]
            p_ = tl.tool + n * LIFT_OFF_MM
            if tl.ceiling is not None and p_[2] > tl.ceiling - 15.0:
                p_[2] = tl.ceiling - 15.0          # off the skin, not over the head
            if self._reachable(a, p_):
                way.append(("xyz", p_, n))
        way.append(("joints", None, None))
        self.back_route[a] = way
        tl.contact = False
        tl.route = []
        tl.target = None
        self.trails[a].append(None)

    def _reachable(self, a, p):
        return K.ik(*(self.T_inv[a] @ np.r_[p, 1.0])[:3]) is not None

    def _count_jump(self, a, joints):
        """A checked step turning a joint further than MAX_SWING_RAD should
        never happen; count it if it does (the tests fail on any)."""
        swing = float(np.max(np.abs(np.subtract(tuple(joints)[:3],
                                                tuple(self.joints[a])[:3]))))
        if swing > MAX_SWING_RAD + 1e-6:
            self.jumped += 1

    def _park_now(self, a):
        self._count_jump(a, self.park_j[a])
        tl = self.tools[a]
        tl.tool = self.home[a].copy()
        self.joints[a] = self.park_j[a]
        self.gov.joints[a] = self.park_j[a]
        self.sponge[a] = tl.tool.copy()
        self.mode[a] = "parked"
        self.trails[a].append(None)

    def _return(self, a, dt, t):
        way = self.back_route[a]
        if not way:
            self._park_now(a)
            return
        if t - self.back_since[a] > RETURN_TIMEOUT_S:
            # Held on the way home for too long. It waits where it stopped,
            # which every checked step so far says is safe, rather than
            # jumping home. Say so.
            self.verdicts["stopped short of home"] += 1
            self.park_j[a] = tuple(self.joints[a])
            self.home[a] = np.asarray(self.sponge[a], float).copy()
            self._park_now(a)
            return
        kind, target, n = way[0]
        if kind == "path":
            v = self._path_step(a, target, dt, t)
            if v is None or v.action == FLEET.REFUSE or not target:
                way.pop(0)                 # done, or refused: fold from here
            elif v.action == FLEET.RETREAT:
                self._retreated(a, v, t)
            return
        if kind == "xyz":
            v, arrived = self._line_step(a, target, RETURN_V, dt, t, n, self.trees[None])
            if v is None or v.action == FLEET.REFUSE or arrived:
                way.pop(0)                 # refused: fold from here instead
            elif v.action == FLEET.RETREAT:
                self._retreated(a, v, t)
            return
        cur = np.asarray(self.joints[a], float)
        goal = np.asarray(self.park_j[a], float)
        if float(np.max(np.abs(goal - cur))) < 1e-3:
            way.pop(0)
            self._park_now(a)
            return
        # Toward the parked angles. If the whole turn is refused, draw the
        # arm in first, then turn the base: the arm is shortest that way.
        s = min(MAX_SWING_RAD - IK_SLACK_RAD, JOINT_V * max(dt, 1e-3))
        full = cur + np.clip(goal - cur, -s, s)
        arm_only = cur.copy()
        arm_only[1:3] = full[1:3]
        base_only = cur.copy()
        base_only[0] = full[0]
        # Then any other step that still closes on the parked angles: the
        # straight fold can swing the tool over the head or into a neighbour.
        d_now = float(np.abs(goal - cur).sum())
        others = sorted(
            (cur + np.asarray(d) for d in itertools.product((-s, 0.0, s), repeat=3)
             if any(d) and float(np.abs(goal - cur - np.asarray(d)).sum()) < d_now - 1e-6),
            key=lambda j: float(np.abs(goal - j).sum()))
        last = None
        self.gov._tree = self.trees[None]
        tried = 0
        for nxt in [full, arm_only, base_only] + others:
            if float(np.max(np.abs(nxt - cur))) < 1e-9 or not K.within_limits(*nxt):
                continue
            nxt, f = self._slowed_joints(a, cur, nxt, dt, v_max=RETURN_V)
            if f <= 0.0:
                continue
            tcp = np.array(K.fk(*nxt), float)
            if over_head(self.gov.z_ceiling, self._world(a, tcp)):
                continue
            back = K.ik(*tcp)
            if back is None or float(np.max(np.abs(np.subtract(back, nxt)))) > IK_SLACK_RAD:
                continue
            tried += 1
            if tried > 6:
                break
            goal_w = self._world(a, tcp)
            self.gov.contact[a] = False
            v = self._raw_refused(a, tuple(tcp)) or self.gov.propose(a, *tcp, t=t)
            last = v
            if v.action in (FLEET.CLEAR, FLEET.ESTOP):
                break
        if last is not None and last.action == FLEET.CLEAR:
            self._moved(a, goal_w, last.joints)
            self.blocked[a] = 0.0
        else:
            # Held or refused all three ways. The fold may run through the
            # person: go round them, once a return. Or the way home may run
            # through another arm that is waiting where it was told to: wait
            # nearer.
            self.blocked[a] = self.blocked.get(a, 0.0) + dt
            if self.blocked[a] > 0.3 and not self.back_tried.get(a):
                self.back_tried[a] = True
                path = joint_path(self.gov.fleet, a, self.layout[a], cur, goal,
                                  self.trees[None], self.gov.z_ceiling)
                if path:
                    way.insert(0, ("path", [tuple(float(v) for v in q) for q in path],
                                   None))
                    self.verdicts["went around"] += 1
                    self.blocked[a] = 0.0
            if self.blocked[a] > REPARK_S:
                self.blocked[a] = 0.0
                j = self._repark(a)
                if j is not None:
                    self.park_j[a] = j
                    self.home[a] = self._world(a, K.fk(*j))
                    self.verdicts["waited nearer"] += 1
        if last is None:
            return
        # Count the frame once, whatever was tried in it.
        self.verdicts[last.action] += 1
        if last.action != FLEET.CLEAR:
            self.why[(a, "returning", last.action, last.reason)] += 1
        if last.action == FLEET.ESTOP:
            self.estopped = True
            self.status = ("STOPPED: two arms came inside the stop distance. "
                           "This should never happen")

    def _keep_away(self, a, t):
        """A waiting arm backs off when the person comes to it. -> moved?"""
        tree = self.trees[None]
        fleet = self.gov.fleet
        cur = np.asarray(self.joints[a], float)
        now = fleet.link_clearance(a, tuple(cur), tree, moving=True)
        if now >= D_BODY_MM + KEEP_AWAY_MM:
            return False

        def gap(states):
            pairs = fleet.pair_distances(states)
            return min((d for (p, q), d in pairs.items() if a in (p, q)),
                       default=float("inf"))

        states = list(self.joints)
        gap_now = gap(states)
        if gap_now < D_ESTOP_MM:
            return False          # any proposal would stop the fleet
        s = MAX_SWING_RAD - IK_SLACK_RAD
        options = []
        for d in itertools.product((-s, 0.0, s), repeat=3):
            if not any(d):
                continue
            nxt = cur + np.asarray(d)
            if not K.within_limits(*nxt):
                continue
            cl = fleet.link_clearance(a, tuple(nxt), tree, moving=True)
            if cl <= now + FLEET.SEPARATING_MM:
                continue
            states[a] = tuple(nxt)
            if gap(states) < min(D_HOLD_MM, gap_now):
                continue
            tcp = np.array(K.fk(*nxt), float)
            back = K.ik(*tcp)
            if back is None or float(np.max(np.abs(np.subtract(back, nxt)))) > IK_SLACK_RAD:
                continue
            options.append((cl, nxt))
        states[a] = tuple(cur)
        # The clearest step that keeps out of where the other arms work (or
        # comes no nearer to it than the arm already is).
        want = min(D_HOLD_MM + 40.0, self._work_gap(a, cur)) if options else 0.0
        best = next((nxt for _cl, nxt in sorted(options, key=lambda o: -o[0])
                     if self._work_gap(a, nxt) >= want), None)
        if best is None:
            return False
        tcp = np.array(K.fk(*best), float)
        self.gov._tree = tree
        self.gov.contact[a] = False
        v = self._raw_refused(a, tuple(tcp)) or self.gov.propose(a, *tcp, t=t)
        if v.action != FLEET.CLEAR:
            return False
        goal = self._world(a, tcp)
        self._moved(a, goal, v.joints)
        self.verdicts["moved out of the way"] += 1
        if self.mode.get(a) == "parked":
            self.park_j[a] = v.joints
            self.home[a] = goal.copy()
        return True

    # --- the depth guard ----------------------------------------------------
    def sense(self, scene, body=None):
        """What the depth camera measured this frame (a depth_guard.Scene
        made with the arms where they are now), or None."""
        if scene is None:
            self.raw = self.scene = None
            return
        self.scene = scene
        if body is not None:
            self._model_mesh(body, at=id(body))
        P = scene.P
        if self.mesh_tree is not None and len(P):
            # The model's vertices are at most MESH_GAP_MM apart: a point that
            # far past the pad from every vertex is past the pad from the model.
            d, _ = self.mesh_tree.query(P, distance_upper_bound=PERSON_PAD_MM + MESH_GAP_MM)
            P = P[np.isfinite(d) | scene.in_person]
        else:
            P = P[scene.in_person]
        self.raw = DG.RawGuard(P)
        if body is None or self.owner is None:
            return
        P, N, I, _A = body.scrub_cells()
        self.agree = DG.agreement(scene, P, N, I, int(I.max()) + 1 if len(I) else 0,
                                  mesh=self.mesh)
        for part, (n, med, off) in self.agree.items():
            if n < AGREE_MIN:
                continue                         # too little seen to say
            limit = AGREE_OK_MM if part in self.distrust else AGREE_MM
            bad = abs(med) > limit or (off > AGREE_OFF and off * n >= AGREE_OFF_MIN)
            was, runs = self.agree_runs.get(part, (None, 0))
            runs = runs + 1 if was == bad else 1
            self.agree_runs[part] = (bad, runs)
            if bad and runs >= DISTRUST_AFTER:
                self.distrust.add(part)
            elif not bad and runs >= TRUST_AFTER:
                self.distrust.discard(part)

    def _model_mesh(self, body, at=None):
        """The posed model, for exact distances (self.mesh). `at`: a stamp
        for the frame it is for, so a frame builds it once."""
        if at is not None and at == self.mesh_at:
            return
        self.mesh_at = at
        try:
            import open3d as o3d
            V, F = body_mesh(body)
            mesh = o3d.t.geometry.RaycastingScene()
            mesh.add_triangles(o3d.core.Tensor(V.astype(np.float32)),
                               o3d.core.Tensor(F.astype(np.uint32)))
            self.mesh = mesh
            self.mesh_tree = cKDTree(V)
        except Exception:                               # noqa: BLE001
            self.mesh = self.mesh_tree = None

    def _gaps(self, a, P, J):
        """Sponge-surface gaps at tool points `P` and structure clearances at
        joints `J` (None allowed), each the nearer of the model and the
        camera's points. -> (gaps, clearances)"""
        T = self.layout[a]
        P = np.asarray(P, float).reshape(-1, 3)
        caps = [COL.moving_capsules(T, *tuple(j)[:3]) if j is not None else None
                for j in J]
        tt = np.linspace(0.0, 1.0, DG.SAMPLES)[:, None]
        radii = np.asarray(COL.CAPSULE_RADII)[list(COL.MOVING)]
        samples = [np.concatenate([p + (q - p) * tt for p, q in c]) if c is not None
                   else np.zeros((0, 3)) for c in caps]
        pts = np.concatenate([P] + samples)
        if self.mesh is not None:
            import open3d as o3d
            d = self.mesh.compute_distance(o3d.core.Tensor(pts.astype(np.float32))).numpy()
        else:
            d = self.trees[None].query(pts)[0]
        gaps = d[:len(P)] - COL.R_SPONGE
        if self.raw is not None:
            gaps = np.minimum(gaps, [self.raw.sponge_gap(p) for p in P])
        clear, k = [], len(P)
        for a_caps, smp, j in zip(caps, samples, J):
            if a_caps is None:
                clear.append(float("inf"))
                continue
            dd = d[k:k + len(smp)].reshape(len(a_caps), DG.SAMPLES).min(axis=1)
            k += len(smp)
            c = float((dd - radii).min())
            if self.raw is not None:
                c = min(c, self.raw.clearance(T, j))
            clear.append(c)
        return gaps, clear

    def _slowed(self, a, goal, dt, v_max=MAX_TOOL_V, contact=False, normal=None):
        """How much of the step toward `goal` arm `a` may take now (THE
        DEPTH GUARD); `contact`: the sponge is meant to be on the skin.
        `normal`: the skin's, out of it; a goal deeper than MAX_PRESS_MM is
        then moved out along it (self.lifted[a], mm), so the sponge goes on
        along the surface as deep as it may, where clothes stand in front of
        the model. -> (goal, fraction); a fraction of 0 means not at all"""
        cur = np.asarray(self.sponge[a], float)
        goal = np.asarray(goal, float)
        self.lifted[a] = 0.0
        d = goal - cur
        L = float(np.linalg.norm(d))
        if dt <= 0.0 or L < 1e-9:
            return goal, 1.0
        j_next = K.ik(*(self.T_inv[a] @ np.r_[goal, 1.0])[:3])
        (g0, g1), (c0, c1) = self._gaps(a, [cur, goal], [self.joints[a], j_next])
        slack = 0.0
        if normal is not None and g1 < -MAX_PRESS_MM and g1 < g0:
            n = np.asarray(normal, float)
            for _ in range(2):
                up = min(-MAX_PRESS_MM - g1, MAX_LIFT_OUT_MM - self.lifted[a])
                if up <= 0.1:
                    break
                goal = goal + n * up
                self.lifted[a] += up
                j_next = K.ik(*(self.T_inv[a] @ np.r_[goal, 1.0])[:3])
                (g0, g1), (c0, c1) = self._gaps(a, [cur, goal], [self.joints[a], j_next])
            slack = PRESS_SLACK_MM
            d = goal - cur
            L = float(np.linalg.norm(d))
            if L < 1e-9:
                return goal, 1.0
        f = min(1.0, v_max * dt / L)
        if self.lifted[a] > 0.0 and j_next is not None:
            # The move out was not in the caller's swing check.
            swing = float(np.max(np.abs(np.subtract(j_next, self.joints[a]))))
            if swing > MAX_SWING_RAD:
                f = min(f, MAX_SWING_RAD / swing * 0.9)
        if g1 < g0:
            v = allowed_close(g1, V_TOUCH)
            if (g0 - g1) > v * dt:
                f = min(f, v * dt / (g0 - g1))
            if g1 < -MAX_PRESS_MM - slack:
                f = min(f, max(0.0, (g0 + MAX_PRESS_MM + slack) / (g0 - g1)))
        if j_next is not None and c1 < c0:
            v = allowed_close(c1, V_CLOSE)
            if (c0 - c1) > v * dt:
                f = min(f, v * dt / (c0 - c1))
        if not contact:
            if min(g0, g1) < NEAR_MM and L > V_NEAR * dt:
                f = min(f, V_NEAR * dt / L)
            if j_next is not None and min(c0, c1) < NEAR_MM:
                moved = self._structure_travel(a, self.joints[a], j_next)
                if moved > V_NEAR_ARM * dt:
                    f = min(f, V_NEAR_ARM * dt / moved)
        if f < 1.0 - 1e-9:
            self.slowed[a] += 1
            goal = cur + d * f
        return goal, f

    def _structure_travel(self, a, j0, j1):
        """How far the furthest-moving point of arm `a`'s moving structure
        goes from joints j0 to j1, mm."""
        T = self.layout[a]
        tt = np.linspace(0.0, 1.0, 5)[:, None]
        pts = []
        for j in (j0, j1):
            caps = COL.moving_capsules(T, *tuple(j)[:3])
            pts.append(np.concatenate([p + (q - p) * tt for p, q in caps]))
        return float(np.linalg.norm(pts[1] - pts[0], axis=1).max())

    def _slowed_joints(self, a, cur, nxt, dt, v_max=MAX_TOOL_V):
        """_slowed for a step in joint space. -> (joints, fraction)"""
        cur = np.asarray(cur, float)
        nxt = np.asarray(nxt, float)
        goal = self._world(a, K.fk(*nxt))
        _g, f = self._slowed(a, goal, dt, v_max)
        return cur + (nxt - cur) * f, f

    def _lagging(self, a, t):
        """Is arm `a`'s real sponge more than LEAD_MM behind its plan? Then
        the plan waits for it, LEAD_WAIT_S at most at a time."""
        p = self.real_at.get(a)
        limit = LEAD_MM + self.plan_v.get(a, 0.0) * LEAD_LATE_S
        if p is None or float(np.linalg.norm(p - self.sponge[a])) <= limit:
            self.lead_since.pop(a, None)
            return False
        since = self.lead_since.setdefault(a, t)
        if t - since > LEAD_WAIT_S:
            return False
        self.waited_real[a] += 1
        return True

    def _raw_refused(self, a, xyz):
        """A REFUSE if the pose at `xyz` (arm frame) takes a part of the arm's
        moving structure deeper into what the camera measured.

        Part by part, not by whichever part of the arm is nearest. An arm
        mounted where the camera sees the person, as one between the thighs
        is, has a link against them wherever it goes: judged by the nearest
        part it could never move at all, and judged part by part it may move
        as long as no part of it presses further in.
        """
        if self.raw is None:
            return None
        j = K.ik(*xyz)
        if j is None:
            return None
        after = self.raw.clearances(self.layout[a], j)
        before = self.raw.clearances(self.layout[a], self.joints[a])
        deeper = (after < RAW_MM) & (after < before - FLEET.SEPARATING_MM)
        floor = (after < RAW_FLOOR_MM) & (after < before)
        if (deeper | floor).any():
            return FLEET.Verdict(FLEET.REFUSE, reason="the camera sees something there",
                                 detail={"clearance_mm": float(after.min())})
        # Nor may a link press into them behind what the camera measured.
        deep = self._behind(a, j)
        if (deep > BEHIND_MAX_MM).any() and                 (self._model_clear(a, j) < BEHIND_NEAR_MM).any():
            was = self._behind(a, self.joints[a])
            worse = (deep > BEHIND_MAX_MM) & (deep > was + FLEET.SEPARATING_MM)                 & (self._model_clear(a, j) < BEHIND_NEAR_MM)
            if worse.any():
                return FLEET.Verdict(FLEET.REFUSE,
                                     reason="the camera measured the person there",
                                     detail={"clearance_mm": float(-deep.max())})
        return None

    def _model_clear(self, a, joints):
        """Per link of arm `a` at `joints` (STRUCTURE), how far it is from the
        model of the person. mm, negative inside it. -> (links,)"""
        caps = COL.moving_capsules(self.layout[a], *tuple(joints)[:3])
        caps = [caps[i] for i in STRUCTURE]
        radii = np.asarray(COL.CAPSULE_RADII)[[COL.MOVING[i] for i in STRUCTURE]]
        tt = np.linspace(0.0, 1.0, DG.SAMPLES)[:, None]
        pts = np.concatenate([p + (q - p) * tt for p, q in caps])
        if self.mesh is not None:
            import open3d as o3d
            d = self.mesh.compute_distance(o3d.core.Tensor(pts.astype(np.float32))).numpy()
        elif self.trees is not None:
            d = self.trees[None].query(pts)[0]
        else:
            return np.full(len(caps), np.inf)
        return d.reshape(len(caps), DG.SAMPLES).min(axis=1) - radii

    def _behind(self, a, joints, tool=False, everything=False):
        """Per link of arm `a` at `joints` (STRUCTURE; `tool`: the sponge
        too, and then the deepest of them as one number), how far the
        deepest of it is behind what the camera measured along its own ray.
        mm, negative in front of it. -> (links,) or a float"""
        caps = COL.moving_capsules(self.layout[a], *tuple(joints)[:3])
        which = list(range(len(caps))) if tool else list(STRUCTURE)
        caps = [caps[i] for i in which]
        radii = np.asarray(COL.CAPSULE_RADII)[[COL.MOVING[i] for i in which]]
        tt = np.linspace(0.0, 1.0, DG.SAMPLES)[:, None]
        pts = np.concatenate([p + (q - p) * tt for p, q in caps])
        b = self.scene.behind(pts, everything).reshape(len(caps),
                                                       DG.SAMPLES).max(axis=1)
        return float((b + radii).max()) if tool else b + radii

    def _lift_off_all(self, dt, t):
        """The person is out of view: an arm on the skin lifts straight off
        it, slowly; every arm holds."""
        for a, tl in self.tools.items():
            if not tl.contact or tl.last is None or a in self.disabled:
                continue
            target = tl.where(tl.last, LIFT_MM)
            v, arrived = self._line_step(a, target, LIFT_OFF_V, dt, t, tl.c.nrm[tl.last],
                                         self.trees[None])
            if v is None or arrived or v.action != FLEET.CLEAR:
                tl.contact = False
                tl.route = []
                tl.base = None

    # --- the real arms ----------------------------------------------------
    def real_frame(self, joints, points):
        """One frame of the real arms (arm_hw). `joints`: {arm: joint angles
        from its encoders, or None}; `points`: {arm: its real sponge, world
        mm, or None}. Credits the skin the real sponges reach, and checks the
        real arms against the person and each other.
        -> why every arm must hold, or None"""
        self.driving = True
        if self.gov is None or self.trees is None or not self.layout:
            return None
        self.real_at = {a: np.asarray(p, float) for a, p in points.items() if p is not None}
        for a, p in points.items():
            if p is not None and a in self.tools:
                self.tools[a].real_rub(p)
        fleet, tree = self.gov.fleet, self.trees[None]
        self.real_clear = {a: fleet.link_clearance(a, tuple(j), tree, moving=True)
                           for a, j in joints.items() if j is not None}
        states = [joints.get(a) or self.joints[a] for a in range(len(self.layout))]
        pairs = {k: d for k, d in fleet.pair_distances(states).items()
                 if joints.get(k[0]) is not None or joints.get(k[1]) is not None}
        self.real_gap = min(pairs.values(), default=float("inf"))
        if self.raw is not None:
            for a, j in joints.items():
                if j is not None:
                    self.real_clear[a] = min(self.real_clear[a],
                                             self.raw.clearance(self.layout[a], j))
                    self.plan_clear[a] = min(
                        fleet.link_clearance(a, tuple(self.joints[a]), tree, moving=True),
                        self.raw.clearance(self.layout[a], self.joints[a]))
        if self.real_clear:
            # Deeper into the person than the plan is, not deeper than zero:
            # an arm mounted against them (one between the thighs) has a link
            # inside what the camera sees wherever it goes, and the plan's own
            # guard keeps that depth from growing.
            worst, why = 0.0, None
            for a, d in self.real_clear.items():
                room = min(0.0, self.plan_clear.get(a, 0.0))
                if d - room < worst:
                    worst, why = d - room, (
                        f"{NAMES[a % 4]}: the real arm is {room - d:.0f} mm deeper "
                        f"into you than its plan, as the camera sees you")
            if worst < REAL_BODY_MM:
                return why
        if pairs:
            (i, k), d = min(pairs.items(), key=lambda kv: kv[1])
            if d < D_ESTOP_MM:
                return (f"{NAMES[i % 4]} and {NAMES[k % 4]}: the real arms are "
                        f"{max(d, 0.0):.0f} mm apart")
        return None

    # --- what the view shows ----------------------------------------------
    def cell_state(self, n):
        """Per scrub cell: owning arm (-1 none), scrubbed, and still reachable.
        Driving real arms, scrubbed is what the real sponges reached."""
        own = np.full(n, -1) if self.owner is None else self.owner
        done = np.zeros(n)                   # how much of this pass's sweeps, 0 to 1
        reach = np.zeros(n, bool)
        for a, (c, m) in self.ctl.items():
            idx = np.flatnonzero(m)
            ok = ~self.skipped[a]
            tl = self.tools[a]
            done[idx[ok]] = (tl.real_level() if self.driving else tl.level())[ok]
            reach[idx[c.reachable_mask() & ok]] = True
        return own, done, reach

    def progress(self, real=False):
        """-> {arm: (scrubbed fraction of what it has been able to reach this
        pass, cm2 it owns, that cm2)}; `real`: what the real sponge reached.

        Against what it can reach at this instant, the figure jumps: the
        person moves, all that is left in reach is what was scrubbed a
        moment ago, and the arm reads 100%.
        """
        out = {}
        for a, (c, _m) in self.ctl.items():
            rm = (c.reachable_mask() | self.seen_ok[a]) & ~self.skipped[a]
            tl = self.tools[a]
            got = (c.area * (tl.real_level() if real else tl.level()))[rm].sum()
            out[a] = (float(got / max(c.area[rm].sum(), 1e-9)) if rm.any() else 0.0,
                      float(c.area.sum() / 100.0), float(c.area[rm].sum() / 100.0))
        return out

    def summary(self):
        """Markdown for the side panel."""
        if self.owner is None:
            return f"**Arms:** {self.status}.\n\n"
        if self.estopped:
            head = f"**Arms: {self.status}.**"
        elif self.settling:
            head = "**Arms:** moving from where they are to where each waits."
        elif self.pause_for is not None:
            head = f"**Arms:** pass {self.passes} done; the next starts in a moment."
        elif not self.phases:
            head = f"**Arms:** {self.status}."
        else:
            moving = self.phases[self.phase_i]
            names = [NAMES[a % 4] for a in moving]
            together = (", ".join(names[:-1]) + " and " + names[-1]
                        if len(names) > 1 else names[0])
            if len(self.phases) == 1:
                head = (f"**Arms:** pass {self.passes}: {together} "
                        + ("scrubbing together." if len(names) > 1 else "scrubbing."))
            else:
                waiting = [a for a in range(len(self.layout)) if a not in moving]
                head = (f"**Arms:** pass {self.passes}, turn {self.phase_i + 1} of "
                        f"{len(self.phases)}: {together} scrubbing"
                        + ("; " + ", ".join(NAMES[a % 4] for a in waiting)
                           + " wait their turn" if waiting else "") + ".")
        prog = self.progress()
        real = self.progress(real=True) if self.driving else None
        reach = sum(r for _f, _o, r in prog.values())
        done = sum(f * r for f, _o, r in prog.values())
        grey = float(self.area.sum() / 100.0) - reach
        cover = (f"This pass: **{100 * done / reach:.0f}%** of the {reach:.0f} cm2 the "
                 f"arms can reach from where they stand." if reach >= 1.0 else
                 "Nothing is in the arms' reach from where they stand.")
        if real and reach >= 1.0:
            got = sum(f * r for f, _o, r in real.values())
            cover = (f"This pass, as the real arms' own joint sensors report it: "
                     f"**{100 * got / reach:.0f}%** of the {reach:.0f} cm2 the arms can "
                     f"reach (the plan: {100 * done / reach:.0f}%). Skin counts only "
                     f"where a real sponge reached it.")
        if grey >= 1.0:
            cover += (f" Faded or grey skin ({grey:.0f} cm2) is out of their reach for "
                      f"now; an arm takes its faded patch once you move so it can.")
        gap = ("" if not np.isfinite(self.gap_min)
               else f" Closest two arms so far: {self.gap_min / 10:.1f} cm.")
        safety = (f"Every move is checked before it is made: the arms keep "
                  f"{D_HOLD_MM / 10:.1f} cm apart and {D_BODY_MM:.0f} mm off you, "
                  f"and wait for each other when they meet.{gap} Near you the sponge "
                  f"comes in at no more than {V_TOUCH / 10:.0f} cm/s and the rest of "
                  f"an arm at {V_CLOSE / 10:.0f} cm/s, measured to the model or to what "
                  f"the camera sees, whichever is nearer.")
        if self.distrust:
            names = self.part_names or []
            parts = ", ".join(names[p] if p < len(names) else f"part {p}"
                              for p in sorted(self.distrust))
            safety += (f" **The camera does not agree with the model of {parts}: "
                       f"the arms keep off you until it does.**")
        if self.lost_since is not None:
            safety += " **You are out of view: the arms have lifted off and wait.**"
        if self.driving and self.real_clear:
            safety += (f" The real arms, checked every frame where their sensors put "
                       f"them: closest to you {min(self.real_clear.values()):.0f} mm"
                       + (f", to each other {self.real_gap / 10:.1f} cm"
                          if np.isfinite(self.real_gap) else "") + ".")
        rows = (["| arm | scrubbed (real) | plan | in reach | now |",
                 "|---|---|---|---|---|"] if real else
                ["| arm | scrubbed | in reach | now |", "|---|---|---|---|"])
        for a, (frac, _cm2, r) in sorted(prog.items()):
            now = self.mode.get(a, "")
            if now == "working" and self.waiting.get(a) == "still":
                now = "waiting for you to hold still"
            elif now == "working" and self.waiting.get(a):
                now = "waiting for its patch to come in reach"
            elif now == "working" and a in self.yielding:
                now = "making room for " + NAMES[self.yielding[a][0] % 4]
            elif a in self.paused:
                now = "waiting for the camera and the model to agree"
            else:
                now = {"working": "scrubbing", "returning": "going back",
                       "parked": "done" if frac >= 0.9 else "waiting"}.get(now, now)
            if real:
                rows.append(f"| {NAMES[a % 4]} | **{100 * real[a][0]:.0f}%** | "
                            f"{100 * frac:.0f}% | {r:.0f} cm2 | {now} |")
            else:
                rows.append(f"| {NAMES[a % 4]} | **{100 * frac:.0f}%** | {r:.0f} cm2 | "
                            f"{now} |")
        for a, why in sorted(self.disabled.items()):
            rows.append(f"| {NAMES[a % 4]} | | | " + ("| " if real else "")
                        + f"stays still: {why} |")
        return (head + " " + cover + " Each patch is its arm's colour, brighter with "
                "each sweep.\n\n" + "\n".join(rows) + "\n\n" + safety + "\n\n"
                + (self.hw_note + "\n\n" if self.hw_note else "")
                + (self.sight_note + "\n\n" if self.sight_note else ""))

    def log_static(self):
        if self.layout is None or self.meshes_logged:
            return
        self.meshes_logged = True
        for a in range(len(self.layout)):
            for name, (V, F) in armmesh.meshes().items():
                rr.log(f"world/arms/arm_{a}/links/{name}",
                       rr.Mesh3D(vertex_positions=V, triangle_indices=F,
                                 albedo_factor=VIZ.ARM_COLOURS[a % 4]), static=True)

    def log(self):
        if self.layout is None:
            return
        for a, T in enumerate(self.layout):
            tf = armmesh.link_transforms(*self.joints[a], T_world_base=T)
            for name in armmesh.LINKS:
                rr.log(f"world/arms/arm_{a}/links/{name}", VIZ._tf(tf[name]),
                       static=True)
            col = VIZ.ARM_COLOURS[a % 4]
            core = np.clip(np.asarray(col, float) * 1.4 + 40.0, 0, 255).astype(np.uint8)
            rr.log(f"world/arms/arm_{a}/sponge",
                   rr.Points3D([self.sponge[a], self.sponge[a]],
                               colors=[np.asarray(col, np.uint8), core],
                               radii=[COL.R_SPONGE, 0.45 * COL.R_SPONGE]),
                   static=True)
            strips = VIZ._strips(self.trails[a])
            if strips:
                # The newest stroke brightest and thickest, the older ones
                # fading back: where the sponge has just been reads at a glance.
                n = len(strips)
                base = np.asarray(col, float)
                cols, radii = [], []
                for i in range(n):
                    f = 0.35 + 0.65 * (i + 1) / n
                    cols.append(np.clip(base * f + 30.0 * f, 0, 255).astype(np.uint8))
                    radii.append(1.6 + 2.4 * f)
                rr.log(f"world/arms/arm_{a}/track",
                       rr.LineStrips3D(strips, colors=cols, radii=radii), static=True)
            else:
                rr.log(f"world/arms/arm_{a}/track", rr.Clear(recursive=False),
                       static=True)

    def clear(self, was=0):
        """Take the planned state off the screen, and arms the rig no longer
        has (`was`: how many there were)."""
        n = len(self.layout or self.rel or [])
        for a in range(n):
            rr.log(f"world/arms/arm_{a}/track", rr.Clear(recursive=False), static=True)
        for a in range(n, was):
            rr.log(f"world/arms/arm_{a}", rr.Clear(recursive=True), static=True)
