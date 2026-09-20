#!/usr/bin/env python3
"""tools/export_body.py — bake scrub3d’s body + arm territories for the web.

    ./venv/bin/python tools/export_body.py

WHY BAKE INSTEAD OF COMPUTE LIVE
---------------------------------
`scrub3d` measures a person and solves which arm owns which patch of them.
That is the backend's whole claim, and the projector page had no access to it.

The obvious move is to stream it live over the existing socket. It is the
wrong one for this demo, for two reasons measured here:

  * `place_arms.search()` -- the real optimiser -- did not finish inside 500
    seconds on this machine. Nothing that takes eight minutes belongs between
    a keypress and a picture.
  * The socket carries EVENTS ONLY, by a rule the whole page depends on: if
    Python dies the cartoon keeps mirroring the person. Streaming geometry
    down it would make the browser need the backend to draw a body.

So the geometry is computed once, offline, by the real code, and written to a
file the page fetches. The numbers on screen are genuinely scrub3d's; the page
just does not recompute them 60 times a second.

WHAT COMES OUT
--------------
`web/assets/body.json`:
  regions[]   13 anatomical meshes (torso, head, arms, legs), each with
              vertices and faces, in METRES with Y up, ready for three.js
  cells[]     5824 surface patches, each with a position, a normal, an area
              and the arm that owns it
  arms[]      the arm bases the partition was solved against (the measured
              live rig when there is one; see live_rig_layout below)
  stats       cell counts per arm and per class

COORDINATES. scrub3d works in millimetres, Z up, origin on the floor under
the chair. three.js here works in metres, Y up. The conversion is one matrix
and it is applied ONCE, here, so nothing downstream has to remember it.
"""
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# THE IN-REPO COPY FIRST. scrub3d/ used to live only on its own branch, and
# this reached for a worktree of it at /tmp/s3d -- which /tmp erases on every
# reboot. The bake that produces web/assets/body.json, the file the whole
# territory overlay is drawn from, therefore depended on a directory that
# could vanish between rehearsal and demo day with no warning and no error
# until someone re-ran the export.
#
# The package is now in the repo (see the standing prompt's merge item), so
# that is the default. The env var and the worktree still work, in that order,
# so anyone with an existing /tmp/s3d setup keeps it.
_CANDIDATES = [
    os.environ.get("SCRUB3D"),
    os.path.join(ROOT, "scrub3d"),
    "/tmp/s3d/scrub3d",
]
S3D = next((d for d in _CANDIDATES if d and os.path.isdir(d)), None)
if S3D is None:
    sys.exit("scrub3d not found. It should be at ./scrub3d in this repo.\n"
             "  git checkout origin/scrub3d -- scrub3d/\n"
             "  or set SCRUB3D=/path/to/scrub3d")
sys.path.insert(0, S3D)

import anatomy          # noqa: E402
import control          # noqa: E402
import partition        # noqa: E402
import place_arms       # noqa: E402
import rigconfig        # noqa: E402

# mm -> m, and Z-up -> Y-up. scrub3d puts the floor at z=0 and the person
# standing in +z; three.js wants the floor at y=0 with the person in +y.
MM = 0.001


def to_three(P):
    """(N,3) scrub3d millimetres Z-up -> three.js metres Y-up."""
    P = np.asarray(P, float)
    return np.column_stack([P[:, 1] * MM, P[:, 2] * MM, P[:, 0] * MM])


# The rig the hardware is actually bolted to, as `scrub3d/live/` saves it.
#
# WHY A SECOND RIG FORMAT EXISTS AT ALL, and why this reads it rather than
# rigconfig.load(). There are two rig files in this repo and they are not the
# same rig:
#
#   scrub3d/config.json        FOUR arms, x_mm/y_mm from the WORLD origin,
#                              written by scrub3d/tools/plan_rig.py's search.
#   scrub3d/live/live_rig*.json  THREE arms, x_from_seat_mm/y_from_seat_mm
#                              from the SEAT POINT, written by the rig editor
#                              and search_rig.py against real recordings.
#
# The live files are the measured ones -- they are what the rig editor opens
# by default (rig_editor.py:38), what live_body.py drives, and what
# scrub3d/live/ARMS.md describes the wiring for ("Three arms need three USB
# ports"). So they are the honest answer to "where are the arms".
#
# THE SEAT POINT IS (0, 0) FOR A BAKED BODY. The live view solves the seat
# from the depth camera and passes it to arms_live.place(); a baked body is
# built by anatomy.anatomical_body() sitting at the world origin -- measured,
# its thighs run forward from x=-6 at y=0 -- so seat x and y are both zero and
# the file's numbers are already world millimetres.
LIVE_RIG_DIR = os.path.join(S3D, "live")

# WHICH OF THE THREE MEASURED FILES, AND WHY THIS ONE. All three describe the
# same three mounts at different heights, and they do NOT score the same on the
# baked body. Measured here on 2026-09-19, three arms each:
#
#   live_rig.json        owned   0 /   0 / 218   covered 0.126
#   live_rig_table.json  owned 222 / 208 / 209   covered 0.361
#   live_rig_clear.json  owned 126 / 220 / 112   covered 0.227
#
# live_rig.json is the hand-placed one ("placed by hand: red low in front, at
# the crotch") and its 405mm front arm sits below the seated body's thighs with
# nothing in reach -- two of three arms get NOTHING. live_rig_clear.json was
# tuned to park clear of the person at rest, which costs it reach. The `table`
# file is the SEARCHED one ("searched on both recordings"), it is
# the only one that divides the body three ways with no starved arm, and its
# coverage is within 0.03 of the synthetic ring's 0.392.
#
# So the default WAS the searched rig, not the first file in the directory.
#
# IT IS NOW live_rig_openyam.json, AND THAT IS A CHANGE OF SOURCE RATHER THAN
# A CHANGE OF OPINION. live_rig_table.json is a SEARCH RESULT: place_arms
# looked for three mounts that divide this body well, and it found them. It
# describes no hardware. live_rig_openyam.json is a TAPE MEASURE -- two arms,
# 850mm apart, 550mm above the floor, 200mm ahead of the seat, on a plank
# across the armrests, facing each other. That is the rig that exists.
#
# A searched rig that scores well and a measured rig that scores badly is not
# a tie. The searched one is a picture of a machine nobody has built, which is
# the exact failure the comment above this one describes for the synthetic
# ring. The page should draw the arms that are bolted on.
#
# AND IT SCORES BADLY. Measured here 2026-09-19, and reported rather than
# worked around:
#
#   live_rig_table.json   (3, searched)  owned 222 / 208 / 209   covered 0.361
#   live_rig_openyam.json (2, MEASURED)  owned   0 /   0         covered 0.000
#
# BOTH MEASURED ARMS GET NOTHING, and the reason is 20mm of safety margin.
# Traced through partition.feasibility stage by stage, per arm:
#
#   1792 scrubbable cells
#   1372 have a clear 110mm approach corridor
#    644 of those are inside the arm's reach envelope AND solve in ik()
#      0 of those clear the body with partition._body_clear
#
# So it is not reach. The arms can get their tool point to 644 patches of
# this person; they cannot get their own ELBOW there without coming inside
# collide.D_BODY, the 60mm shell around the person. Sweeping that shell:
#
#      60mm (shipped) ->   0 of 644 clear
#      40mm           -> 465 of 644 clear
#      20mm           -> 561 of 644 clear
#       0mm           -> 609 of 644 clear
#
# Twenty millimetres of shell is the whole difference between a working rig
# and a starved one. Two arms mounted 425mm to each side of a seated person
# and facing inward have to reach ACROSS that person to work on them, and the
# link that sweeps through is the forearm, not the sponge.
#
# WHAT THIS IS NOT. It is not a reason to lower D_BODY -- that number is the
# safety margin on a machine that touches a person, and moving it to make a
# demo look better is the worst available trade. It is not a reason to keep
# baking the searched rig either, because then the screen shows three arms
# that do not exist doing work the two that do exist cannot do.
#
# It is a REAL FINDING about the measured geometry, and the honest output is a
# bake that says zero. The plank is mounted too close in, or too low, or the
# arms want to face along the person rather than across them. That is a
# question for whoever holds the tape measure, and rig_comparison below now
# carries the numbers to ask it with.
DEFAULT_LIVE_RIG = "live_rig_openyam.json"


def live_rig_path(name=None):
    """-> path of the measured rig file, or None if there is none.

    `name` (or $LIVE_RIG) picks one; otherwise the default below is used.
    """
    name = name or os.environ.get("LIVE_RIG") or DEFAULT_LIVE_RIG
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    p = os.path.join(LIVE_RIG_DIR, name)
    return p if os.path.exists(p) else None


def live_rig_layout(path, seat_xy=(0.0, 0.0)):
    """A seat-relative rig file -> [4x4] world poses, one per arm.

    DELEGATES TO scrub3d/live/arms_live.base_pose RATHER THAN REBUILDING IT.
    That function is what the live view itself uses to turn these files into
    poses, tilt and roll included. A second implementation here would be a
    second answer to "where is arm 1", and the day somebody adds a field to
    the rig editor the projector would silently keep using the old one.
    """
    sys.path.insert(0, LIVE_RIG_DIR)
    import arms_live                                        # noqa: E402
    with open(path) as fh:
        d = json.load(fh)
    sx, sy = float(seat_xy[0]), float(seat_xy[1])
    return [arms_live.base_pose(r, sx, sy) for r in d["arms"]], d


def _coverage_order(Pw_s, terr, bases, body, lookup, n_cells):
    """The order each arm's controller actually reaches its cells. -> [[idx]].

    One list per arm, of indices into the FULL drawn cell array, in the order
    the sponge arrives. The page brightens them in this order, so what an
    audience watches is the controller's own sweep rather than a radial fan.

    WHY PacedTool AND NOT CoverageController.run(). run() answers "where next"
    as fast as it can and finishes a territory in a few hundred ticks, which
    is a plan, not a sweep -- it would hand back an order with no sense of how
    long each part takes. PacedTool moves the sponge at v_scrub and reports
    what it has actually passed over (`credited()`), so cells enter this list
    at the rate a real sponge would reach them. That is the property the
    animation needs and the one web/coverage.js was faking with a rate
    constant tuned to fill 13 seconds.

    Indices are remapped to the drawn set through the SAME `lookup` the owner
    mapping uses. Building a second position->index map here is how the sweep
    and the colours end up disagreeing about which cell is which.
    """
    Nw_s, A_s = None, None
    _, Nw_s, _, A_s = body.world_cells(only_scrubbable=True)
    seqs = []
    for a in range(len(bases)):
        m = terr.owner == a
        # Fewer than 8 cells is not a territory, and CoverageController builds
        # a kNN graph that is meaningless below that. partition can legitimately
        # give an arm almost nothing (measured: the rigconfig layout gives arm 0
        # zero cells), so this is a real case, not a guard against a bug.
        if int(m.sum()) < 8:
            seqs.append([])
            continue
        c = control.CoverageController(Pw_s[m], Nw_s[m], A_s[m], bases[a])
        # Start the sponge at the arm's own base. The real start pose is a
        # calibration this export does not have, and the first move is a
        # traverse either way, so the base is the honest default rather than a
        # invented home position.
        start = np.asarray(bases[a], float)[:3, 3]
        tool = control.PacedTool(c, start)
        # Indices of this arm's cells within the scrubbable array, so a
        # credited mask can be turned back into drawn-cell indices.
        mine = np.flatnonzero(m)
        seen = np.zeros(len(c.pts), bool)
        order = []
        # A ceiling, not a schedule. PacedTool finishes when nothing is left
        # reachable; this only stops a pathological territory from running the
        # export forever. At 15Hz this is 400 seconds of simulated travel,
        # comfortably past the ~20s a real territory takes.
        for _ in range(6000):
            if tool.finished:
                break
            tool.step(1.0 / 15.0)
            now = tool.credited()
            new = np.flatnonzero(now & ~seen)
            if new.size:
                seen[new] = True
                for j in new:
                    i = lookup.get(tuple(np.round(Pw_s[mine[j]], 5)))
                    if i is not None:
                        order.append(int(i))
        seqs.append(order)
    return seqs


def _camera_block(capture_dir=None):
    """The measured D455 pose, in the page's own coordinates.

    Returns {"height_m", "distance_m", "orbit", "measured"}. `orbit` is the
    sideways offset web/main.js applies to its virtual camera; 0 is dead
    head-on, which is where the real sensor sits relative to a person who is
    facing it.

    FALLS BACK RATHER THAN FAILING. A capture is not always present (the page
    is baked on machines with no camera attached), and a missing measurement
    must never blank the projector. The fallback carries measured:false so the
    page can say which it is instead of quietly showing a guess as fact.
    """
    fallback = {"height_m": 1.10, "distance_m": 2.20, "orbit": 0.0,
                "measured": False}
    if capture_dir is None:
        capture_dir = os.environ.get("CAPTURE_DIR", "")
    if not capture_dir or not os.path.isdir(capture_dir):
        return fallback
    try:
        import frames                                    # noqa: E402
        solved = frames.solve(capture_dir)
        floor = solved["floor"] if isinstance(solved, dict) else None
        if not floor:
            return fallback
        # offset_mm is the camera's perpendicular distance from the floor
        # plane, which is exactly its height once the floor IS the ground.
        h = abs(float(floor["offset_mm"])) * MM
        return {"height_m": round(h, 4),
                "distance_m": fallback["distance_m"],
                "orbit": 0.0,
                "measured": True}
    except Exception as e:
        # A broken capture must cost the demo nothing. Say so and move on.
        print(f"  [camera] floor solve unavailable ({e}); using fallback pose")
        return fallback


def _ask_governor(body, bases, P, N, owner, n_probe=140):
    """Ask the REAL FleetGovernor about real targets, return what it said.

    Returns a list of {action, reason} in the order the demo should read
    them, or [] if the governor cannot be built here (the page keeps its
    own fallback lines in that case, so a missing scipy cannot blank the
    safety panel on stage).

    WHAT IS PROBED. Three kinds of target, chosen because they are the three
    a judge would ask about:

      * a cell each arm legitimately owns, which should come back CLEAR
      * a point above the head ceiling, which must be REFUSED -- the plan's
        requirement is that touching a head be geometrically impossible
        rather than planner-conditional, and this is that line being tested
        rather than asserted
      * a cell owned by a DIFFERENT arm, which is how a hold or a refusal
        for reaching into someone else's territory shows up

    NOT A SIMULATION OF THE GOVERNOR. This calls `propose()` on the shipped
    class with the shipped thresholds. Whatever it says is what goes on
    screen, including the wording.
    """
    try:
        import fleet as F
    except Exception as e:                       # pragma: no cover
        print(f"  governor unavailable ({e}); page keeps its fallback lines")
        return []

    try:
        pts, _, _, _ = body.world_cells(only_scrubbable=True)
        # THE CEILING COMES OFF THE MEASURED BODY, not off a number typed
        # here. Measured on this rig: scrubbable cells run z 632..1055mm, so
        # a hardcoded 1000 cut through the person's own shoulders and every
        # single proposal came back "above the head ceiling" -- including
        # cells the arm legitimately owned. The first run of this produced
        # exactly one verdict, which is how that showed up.
        #
        # Sitting it just above the tallest scrubbable cell keeps the rule
        # the plan actually asks for (a head must be unreachable by
        # geometry, not by a planner choosing well) while leaving the body
        # itself workable.
        ceiling = float(pts[:, 2].max()) + 40.0
        gov = F.FleetGovernor(bases, body_points=pts, z_ceiling_mm=ceiling)
    except Exception as e:
        # scipy's cKDTree is the usual missing piece. Say so plainly rather
        # than shipping an empty panel with no explanation in the log.
        print(f"  governor could not be built ({e}); "
              f"page keeps its fallback lines")
        return []

    seen, rows = set(), []

    # PROPOSE TAKES THE ARM'S OWN FRAME, NOT WORLD. fleet.py's docstring says
    # so and its first act is `world = R @ [x,y,z] + t`. Passing world
    # coordinates transforms them a second time, which threw every probe far
    # above the person: the first run of this returned exactly one verdict,
    # "above the head ceiling", for cells an arm legitimately owned.
    inv = []
    for T in bases:
        T = np.asarray(T, float)
        R, t = T[:3, :3], T[:3, 3]
        inv.append((R.T, -R.T @ t))

    def ask(arm, xyz, note, normal=None):
        R_inv, t_inv = inv[int(arm)]
        local = R_inv @ np.asarray(xyz, float) + t_inv
        # The normal is a DIRECTION: rotate it, never translate it.
        nloc = None if normal is None else R_inv @ np.asarray(normal, float)
        try:
            v = gov.propose(int(arm), float(local[0]), float(local[1]),
                            float(local[2]), normal=nloc)
        except Exception:
            return
        key = (v.action, v.reason)
        if key in seen:
            return
        seen.add(key)
        # A CLEAR CARRIES NO REASON, BUT IT CARRIES THE MARGINS. fleet.py
        # returns reason="" on success and puts the numbers in `detail`:
        # clearance_mm is how far the structure stayed outside the body
        # shell, separation_mm how far it stayed from the nearest other arm.
        # Those are better evidence than a sentence, and without them the
        # panel showed a bare "CLEAR" that read like a truncated bug.
        reason = v.reason
        if not reason:
            d = v.detail or {}
            bits = []
            if d.get("clearance_mm") is not None:
                bits.append(f"{float(d['clearance_mm']):.0f}mm off the body")
            if d.get("separation_mm") is not None:
                bits.append(f"{float(d['separation_mm']):.0f}mm arm to arm")
            reason = ", ".join(bits)
        rows.append({"action": v.action, "reason": reason, "probe": note})

    # 1. Cells each arm owns, approached ALONG THE SURFACE NORMAL with
    #    standoff -- which is how a sponge actually arrives, and the only way
    #    a proposal clears.
    #
    #    MEASURED, and worth keeping because it is the safety claim in
    #    numbers. Proposing the tool point AT a body cell is refused every
    #    time: "structure would come inside D_BODY", the 60mm shell around
    #    the person. Sweeping the standoff on arm 0's own cells:
    #
    #        0mm  -> 200 refuse,   0 clear
    #       40mm  -> 200 refuse,   0 clear
    #       70mm  -> 170 refuse,  30 clear
    #       90mm  ->  91 refuse, 109 clear
    #      120mm  ->   0 refuse, 200 clear
    #
    #    D_HOLD is 90mm, and the crossover sits exactly there. That is the
    #    governor enforcing its own documented margin rather than a number
    #    anyone wrote down twice.
    #
    #    110mm is used here: clear of the hold line, close enough that it is
    #    a working scrub pose rather than an arm parked in the air.
    STANDOFF_MM = 110.0
    for a in range(len(bases)):
        mine = np.flatnonzero(owner == a)
        if not len(mine):
            continue
        for idx in mine[:: max(1, len(mine) // 6)][:6]:
            ask(a, P[idx] + N[idx] * STANDOFF_MM, "own cell", normal=N[idx])
        # AND ONE AT THE SKIN, so the panel shows what the margin refuses.
        # A safety readout that only ever says CLEAR proves nothing.
        ask(a, P[mine[0]], "tool point on the skin", normal=N[mine[0]])

    # 2. The head ceiling, which must refuse whatever asked.
    top = P[np.argmax(P[:, 2])] if len(P) else np.zeros(3)
    for a in range(len(bases)):
        ask(a, [top[0], top[1], top[2] + 400.0], "above the head")

    # 3. Someone else's territory, which is where holds live.
    #
    # +1, NOT +2. The stride used to be 2, which picks the arm OPPOSITE on a
    # four-arm ring -- correct while there were four, and silently wrong the
    # moment the rig became the measured two: (a + 2) % 2 == a, so every probe
    # here asked an arm about its OWN cells and the row went on screen labelled
    # "another arm's cell". A probe that lies about what it probed is worse
    # than a missing one, because the panel it feeds is the safety story.
    #
    # +1 is the next arm round for any count of two or more, which is all this
    # needs: it only has to be somebody else.
    for a in range(len(bases)):
        theirs = np.flatnonzero(owner == (a + 1) % len(bases))
        for idx in theirs[: max(1, n_probe // 8)][:8]:
            ask(a, P[idx], "another arm's cell")

    # READ IN THE ORDER A PERSON WOULD WANT IT: what worked, then what was
    # paused, then what was refused outright. The panel is four lines wide.
    rank = {F.CLEAR: 0, F.HOLD: 1, F.RETREAT: 2, F.REFUSE: 3, F.ESTOP: 4}
    rows.sort(key=lambda r: rank.get(r["action"], 9))
    return rows[:6]


def main():
    # TWO BODIES, NOT ONE. The pitch's central technical claim is "no
    # preprogrammed actions -- every body type is different so paths are
    # generated from the person's actual 3D model". A demo that shows ONE
    # scan cannot prove that; it looks exactly like a canned path would.
    #
    # Exporting a second, differently proportioned person and letting the
    # operator switch between them turns the claim into something an audience
    # watches happen: the territories redraw, and the arms that own them
    # change, because the body changed.
    # `--skin` adds the fused Poisson surface from scrub3d/skin.py. Off by
    # default because nothing on the page draws a mesh yet and it costs 1.7 MB
    # of payload -- see export_one's docstring for the measurement. Kept as a
    # flag rather than deleted so the moment the page grows a mesh renderer,
    # the geometry is one word away.
    want_skin = "--skin" in sys.argv
    if want_skin:
        print("building WITH the fused skin (scrub3d/skin.py); "
              "roughly +1.1s and +1.7MB per body")
    for name, kw in (("body", {}), ("body-b", {"scale": 0.86, "arm_drop": 0.42})):
        export_one(name, kw, want_skin=want_skin)


def export_one(out_name, kwargs, dest=None, want_skin=False):
    """Solve one body and write it as the page's body JSON.

    `dest` EXISTS SO THE LIVE SOLVE AND THE BAKE ARE THE SAME CODE. py/scrubbot.py
    calls this with a path to web/assets/body-live.json; the bake leaves it None
    and gets web/assets/<out_name>.json as before. There is no second serialiser
    and no second solver -- a live partition is byte-identical in shape to a baked
    one because it comes out of this function, and only the filename differs.

    `want_skin` adds the fused Poisson surface (scrub3d/skin.py) beside the
    thirteen parts.

    DEFAULT OFF, AND THAT IS A MEASUREMENT, NOT TIMIDITY. It is built and it
    works -- 31715 verts / 62937 tris in 1.08s on this Mac -- but NOTHING ON
    THE PAGE READS IT YET. web/territories.js:69 draws the body as a point
    cloud from `cells`, and the thirteen `regions` meshes this file has always
    written are themselves read by nobody. Turning the skin on grows body.json
    from 679 KB to 2363 KB, so shipping it by default would be 1.7 MB the
    projector fetches on boot and throws away.

    Turn it on with `--skin` at the same moment somebody teaches the page to
    draw a mesh. Until then the honest state is: available, measured, and not
    in the payload.
    """
    print(f"building the anatomical body ({out_name}) ...")
    body, meshes = anatomy.anatomical_body(**kwargs)
    # TWO BUGS LIVED IN THIS ONE LINE, both visible in a screenshot as "the
    # body renders as four coloured tubes with no torso".
    #
    # 1. The return order is (points, normals, REGION INDEX, area), not
    #    (points, normals, area, region). Unpacking it wrong put area values
    #    where a region label belonged.
    # 2. only_scrubbable defaults to TRUE, which keeps just the patches a
    #    sponge would touch. That is right for planning a scrub and wrong for
    #    drawing a person: it deletes the torso and head, so the export held
    #    limbs only. Measured on the output -- every cell sat at |x| 0.2..0.3
    #    with nothing in the middle.
    P, N, region, area = body.world_cells(only_scrubbable=False)
    names = [r.name for r in body.regions]
    print(f"  {len(P)} surface cells across {len(meshes)} regions")

    # WHERE THE ARMS ARE. THE MEASURED RIG WHEN THERE IS ONE, THE RING WHEN
    # THERE IS NOT.
    #
    # This used to be `place_arms.ring_layout(4, body=body)` unconditionally --
    # four SYNTHETIC mounts on an arc, chosen because place_arms.search() did
    # not return inside 500s. The comment that stood here argued the ring was
    # the honest choice because the measured rig starved an arm.
    #
    # THAT ARGUMENT WAS ABOUT THE WRONG FILE. It measured scrub3d/config.json,
    # which is a FOUR-arm world-frame rig from plan_rig.py's search. The rig
    # the hardware is actually bolted to is scrub3d/live/live_rig*.json --
    # THREE arms, seat-relative, saved by the rig editor and search_rig.py
    # against real recordings, and the thing scrub3d/live/ARMS.md wires up.
    # Nobody had ever solved the bake against it. Measured here 2026-09-19:
    #
    #   ring_layout (4, synthetic)  owned 273/135/127/164  covered 0.392
    #   config.json (4, searched)   owned   0/274/310/ 12  covered 0.362
    #   live_rig_table.json (3)     owned 222/208/209      covered 0.361
    #
    # The searched live rig divides the body THREE WAYS WITH NO STARVED ARM
    # and costs 0.031 of coverage against a ring that does not exist. The old
    # argument for the ring -- "two arms working and two watching is the wrong
    # picture" -- is an argument AGAINST the ring once the real rig is the one
    # being solved, because the real rig is the one with no idle arm in it.
    #
    # So: the measured rig is the default whenever a rig file is present. The
    # ring stays reachable (RIG=ring) because a body with no rig file still has
    # to bake, not because production and development should differ.
    rig_name = os.environ.get("RIG", "")
    rig_file = None if rig_name == "ring" else live_rig_path(rig_name or None)
    if rig_file:
        bases, rig_doc = live_rig_layout(rig_file)
        print(f"  rig: {os.path.basename(rig_file)} -- {len(bases)} arms, "
              f"measured ({rig_doc.get('name', 'no name recorded')})")
    else:
        bases, rig_doc = place_arms.ring_layout(4, body=body), None
        print(f"  rig: place_arms.ring_layout -- {len(bases)} arms, SYNTHETIC "
              f"(no measured rig file found)")
    # The measured rig, loaded but deliberately NOT used as the layout. It is
    # read so the baked file can say WHICH rig the operator hardware is at and
    # what it would have cost, instead of that fact living only in the comment
    # above. rigconfig.load() is 0.06ms and layout() 0.013ms -- measured -- so
    # carrying it costs nothing next to the 3.8s partition solve.
    measured_rig = rigconfig.load()
    print(f"solving the {len(bases)}-arm partition ...")
    # THE SCHEDULE, NOT JUST THE SPLIT. solve() returns
    # (territories, planes, phases, report) and this took only [0] for a long
    # time, throwing the rest away.
    #
    # `phases` is the conflict graph coloured: which arms can move AT THE SAME
    # TIME without reaching into each other. On the current rig that is
    # [[0, 1, 2], [3]] -- three can work together and the fourth has to wait.
    # That is the brainstorm's "each arm acts as an independent AI agent"
    # claim as a measured result rather than an assertion, and until now it
    # existed only in the operator view that nobody watching the demo sees.
    terr, _planes, phases, report = partition.solve(body, bases)

    # THE PARTITION RUNS ON THE SCRUBBABLE SUBSET, THE DRAWING NEEDS ALL OF IT.
    # partition.solve() calls world_cells() with its own default, so owner and
    # klass come back sized to the scrubbable cells (1792) while P is every
    # cell (5824). Zipping them would silently colour the first 1792 and leave
    # 4032 undefined.
    #
    # Map them back by matching each scrubbable cell to its index in the full
    # set. Cells not in the partition are exactly the ones no sponge reaches --
    # torso, head, hands -- so they take the unreachable class, which is true.
    Ps, _, _, _ = body.world_cells(only_scrubbable=True)
    full_owner = np.full(len(P), -1, int)
    full_klass = np.array(['unreachable'] * len(P), dtype=object)
    lookup = {tuple(np.round(p, 5)): i for i, p in enumerate(P)}
    hit = 0
    for j, ps in enumerate(Ps):
        i = lookup.get(tuple(np.round(ps, 5)))
        if i is not None:
            full_owner[i] = terr.owner[j]
            full_klass[i] = terr.klass[j]
            hit += 1
    print(f"  mapped {hit}/{len(Ps)} partitioned cells onto {len(P)} drawn cells")

    # THE REAL SWEEP ORDER, FROM THE REAL CONTROLLER.
    #
    # web/coverage.js used to invent this: it sorted each arm's cells by
    # distance from that arm's base and brightened them at a fixed rate tuned
    # to finish in 13 seconds. That is an animation, not coverage. It paints
    # cells the arm cannot physically reach, it has no sponge footprint, and
    # its order is a radial fan rather than a sweep -- so the one picture that
    # shows four agents working through a person was the one part of the story
    # with no solver behind it.
    #
    # scrub3d/control.py IS that solver: a geodesic field over the surface
    # graph, reach margin as a term rather than a test, and a traverse when
    # the neighbourhood is done. Running it here costs milliseconds (measured
    # below) against a partition.solve() that already costs seconds, and it
    # hands the page the order the arms would actually work in.
    #
    # ORDER OF ARRIVAL, NOT A PATH. The page needs "which cell goes bright
    # next"; it does not need waypoints, and shipping waypoints would put
    # geometry on a surface whose whole contract is that it carries none.
    t_cov = time.time()
    cover_seq = _coverage_order(Pw_s=Ps, terr=terr, bases=bases, body=body,
                               lookup=lookup, n_cells=len(P))
    print(f"  coverage order: {sum(len(s) for s in cover_seq)} cells across "
          f"{len(cover_seq)} arms in {time.time() - t_cov:.2f}s")
    owner, klass = full_owner, full_klass

    Pm = to_three(P)
    Nm = to_three(N)          # a direction, so the same axis swap applies
    out = {
        "units": "metres, Y up, floor at y=0",
        "source": "scrub3d anatomy.anatomical_body + partition.solve",
        # WHERE THE REAL D455 IS, so the projector can put its virtual camera
        # in the same place. frames.world_from_camera() builds the world frame
        # on the assumption "the person faces the camera" -- it takes the
        # subject's facing direction to BE the camera's backward axis. That
        # makes head-on the only view that agrees with the physical rig: any
        # orbit away from it shows the cartoon from an angle no real sensor
        # ever occupies, and the arms the page draws stop lining up with the
        # arms the governor solved for.
        #
        # Height is the measured floor-to-camera distance from the floor fit,
        # not a typed-in number. It arrives here in scrub3d millimetres Z-up
        # and converts to three.js metres Y-up like every other point.
        "camera": _camera_block(),
        # Plain ints, not numpy: json cannot serialise np.int64 and the whole
        # bake would die at the write with a TypeError about int64.
        "phases": [[int(a) for a in group] for group in phases],
        # NOT ON SCREEN, AND THAT IS DELIBERATE. This is the solver's own
        # coverage: 0.392 on body A. The counts panel reports OUT OF REACH
        # 5125 of 5824 drawn cells, which is 12% reachable. Both are true
        # and they measure different sets -- the solver runs on the
        # SCRUBBABLE subset (1792 cells, limbs only), the panel draws the
        # whole body including the torso and head no sponge ever touches.
        #
        # Two numbers on one screen that a judge would read as the same
        # claim and that disagree by a factor of three is worse than one
        # number. Exported because it is real and cheap to carry; shown
        # only if someone first reconciles it with the OUT OF REACH line.
        "covered_frac": float(report.get("covered_frac", 0.0)),
        "regions": {},
        "cells": {
            "pos":   [[round(v, 4) for v in p] for p in Pm.tolist()],
            "normal": [[round(v, 3) for v in n] for n in Nm.tolist()],
            "area":  [round(float(a), 2) for a in area],
            "owner": [int(o) for o in owner],
            "klass": [str(k) for k in klass],
            "region": [names[int(i)] for i in region],
            # THE CONTROLLER'S OWN ARRIVAL ORDER, one list per arm, indices
            # into the arrays above. The page reads this when it is present and
            # falls back to its distance-from-base fan when it is not, so an
            # old baked body still animates.
            "cover_order": [[int(i) for i in s] for s in cover_seq],
        },
        "arms": [],
        "stats": {},
    }

    for name, (V, F) in meshes.items():
        Vm = to_three(V)
        out["regions"][name] = {
            "verts": [round(float(v), 4) for v in Vm.ravel()],
            "faces": [int(i) for i in np.asarray(F).ravel()],
        }

    # ---- ONE FUSED SKIN, BESIDE the thirteen parts -----------------------
    #
    # scrub3d/skin.py's own header is the argument for this: the thirteen
    # regions are the right thing to MEASURE and to PLAN against -- every cell
    # carries a normal, an area and which limb it belongs to -- and the wrong
    # thing to LOOK at. Rendered as thirteen closed surfaces a person comes out
    # as a stack of capsules with visible seams where an arm meets a shoulder,
    # and the page has been drawing exactly that. Poisson over the union of
    # their oriented points closes the seams and rounds the joints.
    #
    # ADDED, NOT SUBSTITUTED, and the distinction is the whole safety story.
    # "regions" above stays exactly as it was. A fused surface has no per-cell
    # normal, no area and no idea which limb it belongs to, so nothing may plan
    # against it -- skin.py says "NOTHING DOWNSTREAM READS THIS ... and it must
    # never become one". Writing it under its own key keeps those two jobs in
    # two objects, which is what stops a rendering convenience from quietly
    # becoming a safety input. The page picks it up if it wants a smooth body
    # and falls back to the parts when the key is absent, so an older bake and
    # an older page both keep working.
    #
    # MEASURED on this Mac: 1.08s for ~32k vertices / 63k triangles. That is
    # 32 frames at 30fps, which is why it belongs HERE in the offline bake and
    # not in scrubbot's live loop. It is also why it is skippable: the live
    # re-solve path (scrubbot's `v` key) shares this function, and adding a
    # second of Poisson to a 3.8s interactive solve is a real cost for a
    # cosmetic gain.
    if want_skin:
        try:
            import skin                                       # noqa: E402
            t_skin = time.time()
            SV, SF = skin.build(body)
            if len(SF):
                SVm = to_three(SV)
                out["skin"] = {
                    "verts": [round(float(v), 4) for v in SVm.ravel()],
                    "faces": [int(i) for i in np.asarray(SF).ravel()],
                }
                print(f"  fused skin: {len(SV)} verts, {len(SF)} tris "
                      f"in {time.time() - t_skin:.1f}s")
            else:
                print("  fused skin came out empty; keeping the parts only")
        except Exception as e:                                # noqa: BLE001
            # open3d is the only thing this needs that the rest of the bake
            # does not, and a missing Poisson must never cost the demo its
            # body. The thirteen parts are already written above.
            print(f"  [skin] not built ({e}); the page draws the parts")

    for k, T in enumerate(bases):
        T = np.asarray(T, float)
        p = to_three(T[:3, 3][None, :])[0]
        # FACING, NOT JUST A POINT. A base is a 4x4 and only its translation
        # was ever exported, so the file could not say which way an arm looks.
        # The page draws its own plinths today and does not read this, but a
        # bake that throws away half of each pose cannot be checked against
        # the rig file at all -- and checking that is the whole point of
        # rig_comparison below. Degrees about the world's up axis, measured
        # the same way rigconfig.from_layout() measures it, so the two
        # numbers are directly comparable.
        yaw = math.degrees(math.atan2(float(T[1, 0]), float(T[0, 0])))
        out["arms"].append({"pos": [round(float(v), 4) for v in p],
                            "facing_deg": round(yaw, 1)})

    # ---- EVERY RIG, SCORED, NOT ASSERTED ---------------------------------
    #
    # WHY THIS BLOCK EXISTS. The choice of layout above used to be argued in a
    # comment carrying cell counts somebody measured once. A comment cannot
    # notice when an arm is re-placed -- the rig editor WRITES live_rig.json
    # and plan_rig.py WRITES config.json -- and from then on the file says one
    # thing and the numbers say another with nothing failing.
    #
    # So the bake solves EVERY rig it can find and writes all the results down.
    # It used to solve two (ring and config.json); it now also solves the live
    # rig files, which are the ones the hardware is bolted to and the ones the
    # old two-way comparison never looked at.
    #
    # OFFLINE ONLY: each entry is another partition.solve at ~2.8s measured,
    # and `dest` is set exactly when py/scrubbot.py is running this between an
    # operator's keypress and a picture (3.8s is already at the limit of what
    # somebody will wait through without pressing the key twice). The live
    # solve therefore skips it and says so, rather than paying it n times.
    used_label = (os.path.join("scrub3d", "live", os.path.basename(rig_file))
                  if rig_file else "place_arms.ring_layout")
    if dest is None:
        t_rig = time.time()
        # THE CANDIDATES, BY THE FILE THEY COME FROM. Named so a reader of
        # body.json can go and open the one they want to argue with.
        cands = [("place_arms.ring_layout (synthetic)",
                  place_arms.ring_layout(4, body=body)),
                 (os.path.join("scrub3d", "config.json"),
                  measured_rig.layout())]
        for fn in sorted(os.listdir(LIVE_RIG_DIR)):
            if not (fn.startswith("live_rig") and fn.endswith(".json")):
                continue
            lay, _doc = live_rig_layout(os.path.join(LIVE_RIG_DIR, fn))
            cands.append((os.path.join("scrub3d", "live", fn), lay))

        scored = {}
        for label, lay in cands:
            # The layout in use was already solved above; do not pay for it
            # twice, and do not risk the second solve disagreeing with the
            # numbers the page is actually drawn from.
            if label == used_label:
                o = [int(c) for c in np.bincount(owner[owner >= 0],
                                                 minlength=len(bases))]
                cov, ph_ = float(report.get("covered_frac", 0.0)), phases
            else:
                t_, _p_, ph_, r_ = partition.solve(body, lay)
                o = [int((t_.owner == k).sum()) for k in range(len(lay))]
                cov = float(r_.get("covered_frac", 0.0))
            scored[label] = {
                "arms": len(lay),
                "owned": o,
                # THE NUMBER THAT DECIDES IT. An arm with no territory is an
                # arm the audience watches do nothing, so it is reported as
                # its own field rather than left for a reader to spot in the
                # owned list.
                "starved": [i for i, c in enumerate(o) if c == 0],
                "covered_frac": cov,
                "phases": [[int(a) for a in g] for g in ph_],
            }
        out["rig_comparison"] = {
            "used": used_label,
            "why": ("the measured live rig is where the hardware is actually "
                    "bolted; the ring is synthetic and is only the fallback "
                    "when no rig file is present"),
            "candidates": scored,
            "solved_in_s": round(time.time() - t_rig, 2),
        }
        for label, s in scored.items():
            mark = "  <- USED" if label == used_label else ""
            print(f"  rig {label}: {s['arms']} arms, owned {s['owned']}, "
                  f"covered {s['covered_frac']:.3f}"
                  + (f"  -- arm(s) {s['starved']} get NOTHING"
                     if s["starved"] else "") + mark)
    else:
        # Say why the key is missing rather than leaving the live file looking
        # like a bake that found nothing to compare.
        out["rig_comparison"] = {
            "used": used_label,
            "skipped": "live solve: another partition.solve per candidate "
                       "would multiply the wait between the keypress and the "
                       "picture",
        }

    uniq, counts = np.unique(owner, return_counts=True)
    out["stats"]["per_arm"] = {int(u): int(c) for u, c in zip(uniq, counts)}
    ku, kc = np.unique(klass, return_counts=True)
    out["stats"]["per_class"] = {str(u): int(c) for u, c in zip(ku, kc)}

    # ---- THE GOVERNOR'S OWN VERDICTS ------------------------------------
    #
    # THE LAST HARDCODED THING ON THE DEMO SCREEN. web/main.js's GOV_LINES
    # held four verdict strings written by hand, with a comment saying they
    # were "the real verdict names from fleet.py" and that wiring the live
    # governor was "a backend change on another branch". That branch is this
    # directory now, and fleet.py imports nothing but numpy -- no serial, no
    # hardware -- so it runs right here in the bake.
    #
    # WHY THIS MATTERS MORE THAN IT LOOKS. The safety claim is the one a
    # judge always asks about, and it was the only claim on screen with no
    # evidence behind it. Every other number up there came from the solver.
    # These now do too: the reasons below are strings fleet.py itself
    # produced when asked about real targets on this body, so swapping the
    # body with `n` can change them.
    #
    # BAKED, NOT STREAMED, for the same two reasons the geometry is: the
    # socket carries events only so the cartoon survives Python dying, and
    # nothing that needs the backend alive belongs between a keypress and a
    # picture.
    out["stats"]["verdicts"] = _ask_governor(body, bases, P, N, owner)

    if dest is None:
        dest = os.path.join(ROOT, "web", "assets", f"{out_name}.json")
    # WRITE TO A TEMP FILE AND RENAME. The page fetches this path, and the live
    # caller rewrites it while the demo is running -- a plain open("w") truncates
    # first, so a fetch landing mid-write reads a torn half-file and
    # makeTerritories throws on the JSON parse. os.replace is atomic within a
    # filesystem, so a reader sees either the whole old file or the whole new one
    # and never a partial. The temp file sits beside the destination, not in
    # /tmp, because a cross-device rename is not atomic.
    tmp = dest + ".partial"
    with open(tmp, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    os.replace(tmp, dest)

    # THE ARM'S OWN PROPORTIONS, REFRESHED BESIDE THE BODY.
    #
    # web/robotarm.js used to carry hand-typed link lengths whose fore/upper
    # ratio was 0.833 where the vendor's URDF says 1.174 -- the drawn forearm
    # was 29% too short, so the co-star of the pitch was not the shape of the
    # robot. tools/export_armgeom.py derives the real numbers from
    # scrub3d/kinematics.py and scrub3d/armmesh.py, the same geometry the
    # collision layer places its capsules with.
    #
    # WHY HERE. This function is the ONE thing that refreshes what the page
    # reads, from the bake and from the live solve alike. Writing the arm file
    # anywhere else means a rig whose body is current and whose arm is stale.
    # It costs ~5ms (five STL loads and a percentile) against a 4s solve.
    #
    # NEVER FATAL. A failure here must not lose a body that already wrote
    # successfully above, and the page falls back to its own constants when the
    # file is missing -- so this reports and continues.
    try:
        import export_armgeom
        export_armgeom.main()
    except Exception as e:                                   # noqa: BLE001
        print(f"  arm geometry not refreshed ({e}); "
              f"the page keeps its built-in proportions")
    kb = os.path.getsize(dest) / 1024
    print(f"wrote {dest}  ({kb:.0f} KB)")
    print("  per arm:  ", out["stats"]["per_arm"])
    print("  per class:", out["stats"]["per_class"])
    return dest


if __name__ == "__main__":
    main()
