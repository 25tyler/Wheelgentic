"""scrub3d/viz.py -- the operator view: real body, real arms, live coverage.

    python scrub3d/viz.py                     # open the viewer
    python scrub3d/viz.py --save run.rrd      # write a file to open later
    python scrub3d/viz.py --preview out.png   # a still, checkable over a terminal

WHY RERUN AND NOT THE PROJECTOR PAGE
-------------------------------------
This is a SECOND screen, for whoever is running the machine. The projector
keeps the existing cartoon untouched: that cartoon is the privacy mechanism,
and putting a reconstruction of a real person's body on a wall in front of a
room is the exact thing it exists to prevent. Nothing here touches web/.

REAL GEOMETRY, AND WHY IT IS NOT DECORATION
--------------------------------------------
The arms are Waveshare's own STL meshes (MIT via roarm_description's
package.xml), posed by the SAME forward kinematics the collision layer uses. So
the picture and the safety model cannot silently disagree: if a capsule radius
is wrong, the mesh visibly pokes out of the capsule drawn around it. That is a
correctness check you get by looking, which is this project's own standard --
a screenshot beats an assertion.

The capsules stay as a toggleable overlay rather than being replaced. They are
what the governor actually reasons about, and seeing the real link inside its
capsule is the only way to confirm the radius.

The body is scrub3d/anatomy.py: swept elliptical cross-sections, which is
exactly the representation a D455 scan produces. When the camera is connected
nothing here changes but the source of the semi-axes.
"""
import argparse
import os
import sys
import collections
import math
import time

import numpy as np
import rerun as rr

try:
    from . import kinematics as K
    from . import collide as C
    from . import partition as P
    from . import armmesh
    from .anatomy import anatomical_body, world_meshes
    from .control import CoverageController, PacedTool
except ImportError:
    import kinematics as K
    import collide as C
    import partition as P
    import armmesh
    from anatomy import anatomical_body, world_meshes
    from control import CoverageController, PacedTool

# One colour per arm, reused for its territory, its mesh and its track, so the
# eye can follow a single arm through all three at once.
ARM_COLOURS = [
    (232, 92, 74), (74, 160, 232), (108, 199, 122), (232, 176, 74),
]
SKIN = (176, 148, 132)
OBSTACLE = (92, 92, 104)

# How much of each sponge's recent path is drawn. The whole history used to be
# re-sent every frame, which grows without limit; a few seconds is what an eye
# follows anyway.
TRAIL_POINTS = 160

# The longest step the sponge takes after a gap in tracking. Frames lost or
# frozen in between are time the arm was holding, not time it was travelling.
DT_MAX_S = 0.2

HOME_TCP = (235.11, 0.0, 234.79)         # arm.py HOME, the arm's own frame


def _home_tcp(T_base):
    """Where an arm's tool point sits at HOME, in world mm."""
    return (np.asarray(T_base, float) @ np.r_[HOME_TCP, 1.0])[:3]


def _strips(points):
    """The sponge's trail as line strips, broken wherever it lifted off."""
    out, cur = [], []
    for p in points:
        if p is None:
            if len(cur) > 1:
                out.append(np.array(cur))
            cur = []
        else:
            cur.append(p)
    if len(cur) > 1:
        out.append(np.array(cur))
    return out


def _tf(T):
    T = np.asarray(T, float)
    return rr.Transform3D(translation=T[:3, 3], mat3x3=T[:3, :3])


def _log_arm_static(a):
    """Log each link mesh once. Per frame we then move only transforms."""
    for name, (V, F) in armmesh.meshes().items():
        rr.log(f"world/arm_{a}/links/{name}",
               rr.Mesh3D(vertex_positions=V, triangle_indices=F,
                         albedo_factor=ARM_COLOURS[a % 4]),
               static=True)


def _log_arm_pose(a, T_base, j):
    tf = armmesh.link_transforms(*j, T_world_base=T_base)
    for name in armmesh.LINKS:
        rr.log(f"world/arm_{a}/links/{name}", _tf(tf[name]))
    # Capsules as a separate, toggleable entity: this is what the governor
    # reasons about, and the mesh should sit entirely inside it.
    caps = C.arm_capsules(T_base, *j)
    rr.log(f"world/arm_{a}/capsules",
           rr.LineStrips3D([np.asarray(c, float) for c in caps],
                           colors=[ARM_COLOURS[a % 4]] * len(caps),
                           radii=list(C.CAPSULE_RADII)))


def _pose_changed(prev, body, mm=5.0, rot=0.02):
    """Has the body actually moved since the last logged frame? -> (bool, now).

    Skinning moves every vertex, so a reconstruction cannot be sent once and
    then driven by a transform -- the whole mesh is re-logged whenever it
    changes. The design assumption is that the person sits still, so the right
    saving is to notice when they have: a still subject logs the mesh once and
    never again, and a moving one costs exactly what it must.

    5mm and about a degree, which is ABOVE the tracker's own 1.9mm median
    jitter and below anything a person can see. Thresholds under the jitter
    floor suppress nothing: at 2mm this fired on 198 of 200 frames, because
    noise is motion as far as a comparison is concerned.

    This gates the PICTURE only. The planner, the coverage field and the
    reachability check all still follow the body every frame at full
    resolution; what is being throttled is how often a mesh is re-sent.
    """
    now = np.stack([np.asarray(r.T, float) for r in body.regions])
    if prev is None:
        return True, now
    d = np.abs(now - prev)
    return bool(d[:, :3, 3].max() > mm or d[:, :3, :3].max() > rot), now


def _visited_global(ctl, n_cells, tools=None):
    """Which cells have been scrubbed, indexed like the concatenated cell list.

    Each controller carries `visited` over its OWN territory, so the masks have
    to be lifted back into the global order before anything can be painted with
    them. Getting this wrong shows arm 0's progress on arm 2's shoulder, which
    looks like a working demo.

    With `tools`, only what each sponge has actually reached counts: the
    controller marks a cell the moment it picks it, before the arm arrives.
    """
    out = np.zeros(n_cells, bool)
    for a, (c, m) in ctl.items():
        idx = np.flatnonzero(m)
        done = tools[a].credited() if tools is not None else c.visited
        out[idx[done]] = True
    return out


def _paint(base, gid, owner, visited, territory=0.22, scrubbed=0.78):
    """The reconstruction's own colour, tinted by who owns each patch.

    Two strengths on purpose. Territory is a WASH, so the person still reads as
    themselves and you can see at a glance which arm has which area. Scrubbed
    is nearly opaque, so progress is unmistakable from across a room.

    Vertices with no cell -- hair, the face, the backs of the hands -- keep
    their photographic colour untouched. That is the honest rendering of "no
    arm will ever go here", and it is why the face appears without ever being
    scrubbed.
    """
    col = base.astype(np.float32).copy()
    have = gid >= 0
    g = np.where(have, gid, 0)
    own = np.where(have, owner[g], -1)
    vis = have & visited[g]
    for a in range(4):
        c = np.asarray(ARM_COLOURS[a % 4], np.float32)
        for sel, w in ((own == a) & vis, scrubbed), ((own == a) & ~vis,
                                                     territory):
            if sel.any():
                col[sel] = col[sel] * (1.0 - w) + c * w
    return np.clip(col, 0, 255).astype(np.uint8)


def _person(scan_dir, body):
    """The person reconstructed from `scan_dir`, bound to `body`'s bones.

    -> {verts, faces, bind, gid, base}, or None where the capture cannot be
    reconstructed: it needs open3d and a segmented capture, and the view has
    to keep working without it rather than refuse to start because a visual
    upgrade is missing.
    """
    try:
        try:
            from . import shell as SHELL
        except ImportError:
            import shell as SHELL
        sh = SHELL.build(scan_dir, verbose=False, max_tris=24000)
        person = {"verts": sh["verts"], "faces": sh["faces"],
                  "bind": SHELL.bind(sh["verts"], body),
                  "gid": SHELL.cell_map(sh["verts"], body)["gid"],
                  "base": (np.clip(sh["colours"], 0, 1) * 255).astype(np.uint8)}
    except Exception as exc:                      # noqa: BLE001 - visual only
        print(f"  no reconstruction ({exc.__class__.__name__}: {exc}); "
              f"drawing the modelled parts instead")
        return None
    print(f"  reconstructed {len(person['verts'])} vertices from the capture, "
          f"{(person['gid'] >= 0).mean():.0%} of them on scrubbable body")
    return person


def build(body, meshes, layout, max_frames=1500, realtime=False, scan_dir=None,
          obstacles=None, obstacle_region=None):
    """Simulate a whole session on `body`, no camera. -> summary dict.

    `realtime` paces it at the controllers' own rate, so someone watching live
    sees the arms at the speed they would really move.

    Given the scan's `obstacles` and `obstacle_region`, the territories are the
    ones main.py plans, chair and all. Given the capture the body came from,
    `scan_dir`, the person is drawn from it as live() draws them: their own
    depth and colour, washed by owner and painted as it is scrubbed.
    """
    rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)

    terr, planes, phases, rep = P.solve(body, layout, envelope_k=P.ENVELOPE_K,
                                        obstacle_points=obstacles,
                                        obstacle_region=obstacle_region)
    Pw, Nw, _, A = body.world_cells()
    person = _person(scan_dir, body) if scan_dir else None

    wm = world_meshes(body, meshes)
    scrub_names = [r.name for r in body.regions if r.scrubbable.any()]

    # --- static geometry ---------------------------------------------------
    if person is None:
        for name, (V, F) in wm.items():
            if name not in scrub_names:
                rr.log(f"world/body/{name}",
                       rr.Mesh3D(vertex_positions=V, triangle_indices=F,
                                 albedo_factor=OBSTACLE), static=True)
    if obstacles is not None:
        rr.log("world/obstacles", rr.Points3D(obstacles, colors=[OBSTACLE],
                                              radii=4.0), static=True)
    for a, T in enumerate(layout):
        _log_arm_static(a)

    # --- per-region vertex -> owning arm -----------------------------------
    from scipy.spatial import cKDTree
    tree = cKDTree(Pw)
    vert_owner, vert_cell = {}, {}
    for name in scrub_names:
        V, F = wm[name]
        _, idx = tree.query(V)
        vert_cell[name] = idx
        vert_owner[name] = terr.owner[idx]

    # --- controllers -------------------------------------------------------
    ctl, colours = {}, {}
    for a in range(len(layout)):
        m = terr.owner == a
        if m.sum() < 8:
            continue
        ctl[a] = CoverageController(Pw[m], Nw[m], A[m], np.asarray(layout[a], float))
        colours[a] = ARM_COLOURS[a % len(ARM_COLOURS)]

    # Map each global cell index to (arm, local index) so mesh shading can read
    # the controllers' visited state directly.
    local_of = {}
    for a in ctl:
        gi = np.where(terr.owner == a)[0]
        for li, g in enumerate(gi):
            local_of[int(g)] = (a, li)

    # Each sponge travels at its controller's own speed, from HOME, and a cell
    # counts as scrubbed only once the sponge has reached it (control.PacedTool).
    tools = {a: PacedTool(c, _home_tcp(layout[a])) for a, c in ctl.items()}
    trails = {a: collections.deque(maxlen=TRAIL_POINTS) for a in ctl}
    T_inv = {a: np.linalg.inv(np.asarray(layout[a], float)) for a in ctl}
    joints = {a: K.ik(*HOME_TCP) for a in ctl}
    dt = 1.0 / (next(iter(ctl.values())).hz if ctl else 40.0)
    owned = {a: np.flatnonzero(terr.owner == a) for a in ctl}
    painted, last_paint = None, -99
    started = time.perf_counter()

    frame = 0
    while frame < max_frames and not all(t.finished for t in tools.values()):
        rr.set_time("tick", sequence=frame)
        rr.set_time("time", duration=frame * dt)

        credited = {}
        for a, c in ctl.items():
            tool = tools[a].step(dt) if frame else tools[a].tool
            credited[a] = tools[a].credited()
            sol = K.ik(*(T_inv[a] @ np.r_[tool, 1.0])[:3])
            if sol is not None:
                joints[a] = sol
            trails[a].append(tool.copy() if tools[a].contact else None)

            rr.log(f"world/arm_{a}/sponge",
                   rr.Points3D([tool], colors=[colours[a]],
                               radii=[C.R_SPONGE]))
            strips = _strips(trails[a])
            if strips:
                rr.log(f"world/arm_{a}/track",
                       rr.LineStrips3D(strips, colors=[colours[a]], radii=3.0))

        states = []
        for a in range(len(layout)):
            j = joints.get(a) or K.ik(235.11, 0.0, 234.79)
            states.append(j)
            _log_arm_pose(a, np.asarray(layout[a], float), j)

        # --- body mesh, shaded by territory and progress -------------------
        if person is not None:
            # The person sits still here, so the mesh is sent once and after
            # that only its colours, and those at most four times a second.
            vis = np.zeros(len(Pw), bool)
            for a in ctl:
                vis[owned[a][credited[a]]] = True
            if painted is None or (frame - last_paint >= 10
                                   and not np.array_equal(vis, painted)):
                col = _paint(person["base"], person["gid"], terr.owner, vis)
                rr.log("world/body",
                       rr.Mesh3D(vertex_positions=person["verts"],
                                 triangle_indices=person["faces"],
                                 vertex_colors=col) if painted is None
                       else rr.Mesh3D.from_fields(vertex_colors=col))
                painted, last_paint = vis, frame
        for name in scrub_names if person is None else ():
            V, F = wm[name]
            own, cel = vert_owner[name], vert_cell[name]
            col = np.zeros((len(V), 3), np.uint8)
            for k in range(len(V)):
                o = int(own[k])
                if o < 0:
                    col[k] = OBSTACLE
                    continue
                base = np.array(ARM_COLOURS[o % 4], float)
                loc = local_of.get(int(cel[k]))
                lit = loc is not None and credited[loc[0]][loc[1]]
                col[k] = (base if lit else base * 0.30 + np.array(SKIN) * 0.25)
            rr.log(f"world/body/{name}",
                   rr.Mesh3D(vertex_positions=V, triangle_indices=F,
                             vertex_colors=col))

        for a, c in ctl.items():
            rm = c.reachable_mask()
            pct = (100.0 * c.area[credited[a] & rm].sum() / c.area[rm].sum()
                   if rm.any() else 0.0)
            rr.log(f"coverage/arm_{a}", rr.Scalars(pct))

        gaps = C.Fleet(layout).pair_distances(states)
        fin = [g for g in gaps.values() if np.isfinite(g)]
        if fin:
            rr.log("collision/min_gap_mm", rr.Scalars(float(min(fin))))
        rr.log("collision/threshold_hold", rr.Scalars(C.D_HOLD))
        rr.log("collision/threshold_estop", rr.Scalars(C.D_ESTOP))
        frame += 1
        if realtime:
            ahead = started + frame * dt - time.perf_counter()
            if ahead > 0:
                time.sleep(ahead)

    return {"frames": frame, "arms": len(ctl), "phases": phases,
            "covered_frac": rep["covered_frac"],
            "per_arm_cm2": rep["per_arm_cm2"]}


LIGHT = np.array([0.35, -0.55, 0.76])          # over the operator's shoulder
LIGHT = LIGHT / np.linalg.norm(LIGHT)


def _shade(V, F, rgb, ambient=0.34, gamma=0.85):
    """Per-face Lambert shading. -> list of hex colours, one per triangle.

    Poly3DCollection paints every face one flat colour, which is why the
    earlier preview read as cardboard cut-outs: a RoArm and a torso both came
    out as solid silhouettes with no interior form, and you could not tell a
    link from the gap beside it. One dot product per face fixes it, and
    matplotlib's 3D backend has no lighting of its own to do it for us.

    The normal comes from the triangle winding and its SIGN is not trusted.
    These are STL and scan meshes of mixed provenance, and an inverted winding
    would light a surface from behind. abs() lights a face by how square it is
    to the light rather than by which way its author wound it.

    `rgb` is either one colour for the whole mesh or one per triangle, so a
    body region shaded by which arm owns each face needs no separate path.
    """
    T = V[F]
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    n = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    # The normals are already unit length and finite; the flag is Accelerate's
    # spurious (N,3)@(3,) one. See bodymodel.Region.world for the measurement.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        k = (ambient + (1.0 - ambient) * np.abs(n @ LIGHT)) ** gamma
    base = np.asarray(rgb, float)
    if base.ndim == 1:
        base = np.tile(base, (len(F), 1))
    c = np.clip(base * k[:, None], 0, 255)
    return ["#%02x%02x%02x" % tuple(int(v) for v in row) for row in c]


def _fit_axes(ax, pts, pad=60.0):
    """Frame the geometry that is actually there, with equal aspect.

    The bounds were hardcoded at x -250..700, y -650..650, z 450..1250, which
    suited one procedural body at one assumed height and silently cropped
    anything else. A scanned person sits where they sit.
    """
    lo, hi = pts.min(0) - pad, pts.max(0) + pad
    mid, span = (lo + hi) / 2.0, float((hi - lo).max())
    for setter, m in ((ax.set_xlim, mid[0]), (ax.set_ylim, mid[1]),
                      (ax.set_zlim, mid[2])):
        setter(m - span / 2, m + span / 2)
    ax.set_box_aspect((1, 1, 1))


def live(scan_dir, source, layout=None, max_frames=600, j3=0.0, scanned=None,
         plan=None):
    """Scan once, then follow the person frame by frame. -> summary dict.

    `scanned` is scan.scan()'s (body, meshes, obstacles, report) and `plan` is
    partition.solve()'s (territories, planes, phases, report) for `layout`.
    A caller that already has them passes them in. main.py does, so the loop
    follows exactly the body and territories it printed and pre-flighted --
    including a trunk it was told to scrub -- instead of scanning the capture
    again with the defaults and planning against that.

    This is the whole flow in one loop:

        scan      SHAPE, once, seconds on the GPU
        partition territories, once, from that shape and the arm placements
        track     POSE, every frame, ~20fps on the CPU
        refresh   the coverage field re-evaluated against where the body IS
        tick      each arm's next target, from the field, live

    Nothing here replays a stored trajectory. The cells live in region-local
    coordinates, so when the person moves their limbs the cells move with them
    for free and the controller simply reads the field at the new positions.
    That is why "follow the person" costs one matrix write per region rather
    than a re-plan.

    The freeze guard is wired to what it is for: when the tracker reports a
    jump past MAX_JUMP_MM the arms STOP rather than chase. A person flinching
    is not a tracking input.
    """
    try:
        from . import adapt as ADAPT
        from . import rsfeed
        from . import scan as SCAN
        from . import shell as SHELL
        from . import track as TRACK
        from . import frames as FRAME
    except ImportError:
        import adapt as ADAPT
        import rsfeed
        import scan as SCAN
        import shell as SHELL
        import track as TRACK
        import frames as FRAME

    rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)

    if plan is not None and layout is None:
        raise ValueError("a plan was passed without the layout it was solved for")
    body, meshes, obstacles, rep = scanned or SCAN.scan(scan_dir)
    rig = FRAME.solve(scan_dir)
    layout = layout or default_layout()
    terr, planes, phases, prep = plan or P.solve(
        body, layout, envelope_k=P.ENVELOPE_K, obstacle_points=obstacles,
        obstacle_region=rep["obstacle_region"])
    print(f"  scanned {rep['scrubbable_cm2']:.0f}cm2, "
          f"coverage {100 * prep['covered_frac']:.1f}%, phases {phases}")

    # The person, reconstructed from the same capture and bound to the same
    # bones, or None and the modelled parts instead.
    person = _person(scan_dir, body)

    Pw, Nw, Iw, A = body.world_cells()
    n_cells = len(Pw)          # the index space terr.owner and every mask use
    last_T, last_vis, last_paint = None, None, -99
    n_mesh_logs = 0
    ctl = {}
    for a in range(len(layout)):
        m = terr.owner == a
        if m.sum() >= 8:
            ctl[a] = (CoverageController(Pw[m], Nw[m], A[m],
                                         np.asarray(layout[a], float)), m)
    for a in range(len(layout)):
        _log_arm_static(a)
    rr.log("world/obstacles", rr.Points3D(obstacles, colors=[OBSTACLE],
                                          radii=4.0), static=True)

    tracker = TRACK.Tracker(body, rig["T_world_camera"])
    # The 1Hz band. The coverage field follows the body for free because cells
    # are region-local, so what actually goes stale as somebody moves is
    # REACHABILITY: a cell an arm could reach when they sat down may be behind
    # them now. This is what notices, and it recommends rather than acts.
    adapter = ADAPT.Adapter(layout, terr, obstacles=obstacles)
    joints = {a: K.ik(*HOME_TCP) for a in ctl}
    per_arm_cov = {a: 0.0 for a in ctl}
    # THE ARMS TRAVEL AT THEIR OWN SPEED. This loop used to take a new target
    # from every controller on every video frame, which finished each
    # territory in about a second -- a sponge "moving" at metres a second --
    # and left every arm standing still for the rest of the run. Each sponge
    # now starts at HOME and travels at v_scrub in camera time
    # (control.PacedTool), and a cell counts once the sponge has reached it.
    T_inv = {a: np.linalg.inv(np.asarray(layout[a], float)) for a in ctl}
    tools = {a: PacedTool(c, _home_tcp(layout[a])) for a, (c, m) in ctl.items()}
    trails = {a: collections.deque(maxlen=TRAIL_POINTS) for a in ctl}
    region_of = {a: Iw[m] for a, (c, m) in ctl.items()}   # per cell, per arm
    # Drawn at HOME before the first frame: the loop can hold the arms for a
    # while, and an arm nobody has posed is not drawn at all.
    rr.set_time("tick", sequence=0)
    for a in range(len(layout)):
        _log_arm_pose(a, np.asarray(layout[a], float),
                      joints.get(a) or K.ik(*HOME_TCP))
    for a, tl in tools.items():
        rr.log(f"world/arm_{a}/sponge",
               rr.Points3D([tl.tool], colors=[ARM_COLOURS[a % 4]],
                           radii=[C.R_SPONGE]))
    t_first = t_prev = None
    n, tracked, frozen, away, blocked = 0, 0, 0, 0, 0
    handoffs, stops, last_check = [], 0, -99.0
    with rsfeed.Feed(source, repeat=False) as feed:
        for f in feed.frames(limit=max_frames):
            n += 1
            posed, info = tracker.update(f)
            rr.set_time("tick", sequence=n)
            # Camera time, so a saved run plays back at the speed it happened.
            tnow = float(f.get("t") or n / 30.0)
            if t_first is None:
                t_first = tnow
            rr.set_time("time", duration=tnow - t_first)
            if posed is None:
                rr.log("track/ok", rr.Scalars(0.0))
                t_prev = tnow                    # held, not travelled
                continue
            tracked += 1
            rr.log("track/ok", rr.Scalars(1.0))
            rr.log("track/jump_mm", rr.Scalars(info["jump_mm"]))

            # The body moved: its surface and its cells move with it.
            #
            # Where the capture reconstructed, this is the PERSON -- their own
            # depth, their own colour -- skinned to the same bones the cells
            # hang off, so the picture and the plan cannot drift apart. Where
            # it did not, it falls back to the modelled parts, which look like
            # a stack of tubes but are never wrong about where a surface is.
            if person is not None:
                # Motion is redrawn at once; PROGRESS is redrawn at about 4Hz.
                # The visited set changes on almost every tick, so gating on it
                # alone suppressed nothing, and repainting 12.5k vertices 20
                # times a second to move a few cells from one colour to another
                # is the bulk of the recording for none of the information.
                #
                # And only what changed is sent. The triangles never change and
                # were most of every re-log, so they go once; after that a move
                # sends positions and a repaint sends colours.
                vis_now = _visited_global(ctl, n_cells, tools)
                moved, last_T = _pose_changed(last_T, posed)
                repaint = (not np.array_equal(vis_now, last_vis)
                           and n - last_paint >= 5)
                if moved or repaint:
                    n_mesh_logs += 1
                    fields = {}
                    if moved or last_vis is None:
                        fields["vertex_positions"] = SHELL.pose(person["bind"],
                                                                posed)
                    if repaint:
                        fields["vertex_colors"] = _paint(
                            person["base"], person["gid"], terr.owner, vis_now)
                        last_vis, last_paint = vis_now, n
                    rr.log("world/body",
                           rr.Mesh3D(triangle_indices=person["faces"], **fields)
                           if n_mesh_logs == 1 else rr.Mesh3D.from_fields(**fields))
            else:
                for name, (V, F) in world_meshes(posed, meshes).items():
                    rr.log(f"world/body/{name}",
                           rr.Mesh3D(vertex_positions=V, triangle_indices=F,
                                     albedo_factor=SKIN if "arm" in name
                                     else OBSTACLE))
            Pl, Nl, _, _ = posed.world_cells()

            if info["freeze"]:
                # Do not follow a flinch. Hold, and say so.
                frozen += 1
                rr.log("track/freeze", rr.Scalars(1.0))
                t_prev = tnow
                continue
            rr.log("track/freeze", rr.Scalars(0.0))

            # NOBODY IN THE SEAT THE PLAN IS FOR. The tracker follows whoever
            # it sees, and bag01 opens with the operator at the laptop: the
            # arms used to plan against them. Territories, reach and the 1Hz
            # check all assume the scanned seat, so the arms hold until the
            # person is back in it.
            rr.log("track/away", rr.Scalars(1.0 if info.get("away") else 0.0))
            if info.get("away"):
                away += 1
                t_prev = tnow
                continue
            lost = {i for i, r in enumerate(posed.regions)
                    if r.name in info.get("lost", ())}

            # --- 1Hz: can each arm still reach what it has left? ---------
            if tnow - last_check > 1.0:
                last_check = tnow
                remaining = {}
                for a, (c, m) in ctl.items():
                    full = np.zeros(len(Pl), bool)
                    idx = np.flatnonzero(m)
                    full[idx[(~tools[a].credited()) & c.reachable_mask()]] = True
                    remaining[a] = full
                arep = adapter.check(posed, remaining, tnow)
                for a, d in arep["arms"].items():
                    rr.log(f"reach/arm_{a}",
                           rr.Scalars(100.0 * d["reachable_frac"]))
                for h in arep["handoffs"]:
                    # Gated exactly as adapt.py requires: never mid-stroke,
                    # never while either arm is in contact. In this replay
                    # nothing is in contact, and a segment boundary is where
                    # the controller has just finished a cell.
                    if ADAPT.may_hand_over(in_contact=False,
                                           at_segment_boundary=True):
                        adapter.commit(h)
                        handoffs.append(h)
                        print(f"    handoff at t={tnow:.1f}s: arm {h['from']} "
                              f"-> arm {h['to']} ({h['cells']} cells)")
                if arep["stop"]:
                    stops += 1
                    rr.log("track/stop", rr.Scalars(1.0))

            # Travel time since the last frame the arms moved. A gap of lost or
            # frozen frames was time spent holding, so it does not count.
            dt = 0.0 if t_prev is None else min(max(tnow - t_prev, 0.0), DT_MAX_S)
            t_prev = tnow
            for a, (c, m) in ctl.items():
                c.refresh(Pl[m], Nl[m])          # field re-read at the new pose
                # A limb the camera has lost is still drawn where it was last
                # seen, which is not somewhere to take a sponge. An arm headed
                # there waits for it to show again.
                route = tools[a].route
                wait = bool(route) and int(
                    region_of[a][c.node_idx[route[0][0]]]) in lost
                blocked += wait
                tool = tools[a].step(0.0 if wait else dt)
                sol = K.ik(*(T_inv[a] @ np.r_[tool, 1.0])[:3])
                if sol is not None:
                    joints[a] = sol
                rr.log(f"world/arm_{a}/sponge",
                       rr.Points3D([tool], colors=[ARM_COLOURS[a % 4]],
                                   radii=[C.R_SPONGE]))
                trails[a].append(tool.copy() if tools[a].contact else None)
                strips = _strips(trails[a])
                if strips:
                    rr.log(f"world/arm_{a}/track",
                           rr.LineStrips3D(strips, colors=[ARM_COLOURS[a % 4]],
                                           radii=3.0))

                # WHAT THIS ARM HAS ACTUALLY SCRUBBED, and what is left.
                #
                # Two separate entities rather than one recoloured cloud, so
                # either can be switched off, and so "done" and "to do" cannot
                # be confused at a glance. The cells move with the person, so
                # this is redrawn every frame from the CURRENT pose: the marks
                # stay on the arm they were made on. Only cells the sponge has
                # reached count as done, not the ones the controller has picked.
                rm = c.reachable_mask()
                credited = tools[a].credited()
                done = credited & rm
                todo = (~credited) & rm
                col = ARM_COLOURS[a % 4]
                if done.any():
                    rr.log(f"world/arm_{a}/scrubbed",
                           rr.Points3D(c.pts[done], colors=[col],
                                       radii=6.0))
                if todo.any():
                    rr.log(f"world/arm_{a}/remaining",
                           rr.Points3D(c.pts[todo],
                                       colors=[tuple(int(v * 0.32) for v in col)],
                                       radii=3.0))
                cov = (float(c.area[done].sum() / max(c.area[rm].sum(), 1e-9))
                       if rm.any() else 0.0)
                rr.log(f"coverage/arm_{a}", rr.Scalars(100.0 * cov))
                per_arm_cov[a] = cov
            for a in range(len(layout)):
                _log_arm_pose(a, np.asarray(layout[a], float),
                              joints.get(a) or K.ik(*HOME_TCP))
    tracker.close()
    print(f"    body mesh re-logged {n_mesh_logs} times in {n} frames")
    print(f"    arms held on {away} frames with nobody in the scanned seat, "
          f"and {blocked} arm-frames waiting for a lost limb")
    return {"frames": n, "tracked": tracked, "frozen": frozen,
            "away": away, "blocked": blocked,
            "arms": len(ctl), "covered_frac": prep["covered_frac"],
            "phases": phases, "per_arm_scrubbed": per_arm_cov,
            "handoffs": handoffs, "stops": stops,
            "scrubbed_frac": (float(np.mean(list(per_arm_cov.values())))
                              if per_arm_cov else 0.0)}


def _skin_in_subprocess(body, timeout=120):
    """Build the fused skin in a child process. -> (verts, faces) or (None, None).

    Open3D's screened Poisson aborts the process rather than raising, so this
    cannot be done with a try/except in-process. The child writes an npz and
    exits; if it dies, we lose a cosmetic surface and keep the whole view.

    The body is rebuilt from scratch in the child rather than pickled: it
    carries meshes and KD-trees, and anatomical_body() is deterministic, so
    re-deriving it is both simpler and guaranteed to match.
    """
    import subprocess
    import tempfile

    out = os.path.join(tempfile.mkdtemp(prefix="s3dskin-"), "skin.npz")
    code = (
        "import sys, numpy as np;"
        f"sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r});"
        "from scrub3d import anatomy, skin;"
        "b, _ = anatomy.anatomical_body();"
        "V, F = skin.build(b);"
        f"np.savez({out!r}, V=V, F=F)"
    )
    try:
        r = subprocess.run([sys.executable, "-W", "ignore", "-c", code],
                           capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  fused skin timed out after {timeout}s; drawing the parts")
        return None, None
    if r.returncode != 0 or not os.path.exists(out):
        # The abort message is the interesting half and it goes to stderr.
        why = (r.stderr or b"").decode("utf8", "replace").strip().splitlines()
        tail = why[-1] if why else f"exit {r.returncode}"
        print(f"  fused skin unavailable ({tail}); drawing the parts")
        return None, None
    import numpy as _np
    with _np.load(out) as z:
        return z["V"], z["F"]


def preview(body, meshes, layout, path, frames=2000, j3=0.0, fused=True):
    """A still of the finished run. Checkable at a glance, over a terminal."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    terr, _, phases, rep = P.solve(body, layout, envelope_k=P.ENVELOPE_K)
    Pw, Nw, _, A = body.world_cells()
    wm = world_meshes(body, meshes)
    scrub_names = [r.name for r in body.regions if r.scrubbable.any()]
    from scipy.spatial import cKDTree
    tree = cKDTree(Pw)

    skinV = skinF = None
    if fused:
        # IN A SUBPROCESS, because Open3D's Poisson reconstruction does not
        # raise -- it calls abort(). The try/except below it was written to
        # degrade to "drawing the parts", and it could never run: the whole
        # interpreter died first, with
        #   Failed to close loop [6: 52 64 88] ... libc++abi: terminating
        # and no traceback. Measured: it aborts on roughly half of runs on
        # this machine, from identical input, so the view worked or vanished
        # depending on luck.
        #
        # The skin is a cosmetic fused surface. Everything the operator view
        # exists to show -- the arms, the territories, the reachable
        # percentage, the phase grouping -- is drawn without it. A crash in
        # the nicest-looking part must not take the useful parts with it.
        skinV, skinF = _skin_in_subprocess(body)

    fig = plt.figure(figsize=(15.5, 7.4), facecolor="#14141a")
    for k, (elev, azim, title) in enumerate((
            (14, -66, "operator view"), (74, -90, "from above"))):
        ax = fig.add_subplot(1, 2, k + 1, projection="3d", facecolor="#14141a")
        seen = []

        # --- the fused skin: one surface, so the person reads as a person
        # rather than a stack of capsules with seams at every joint. Drawn
        # UNDER the territory colouring, which stays on the measured parts.
        if skinV is not None:
            ax.add_collection3d(Poly3DCollection(
                skinV[skinF], facecolors=_shade(skinV, skinF,
                                                np.array(SKIN, float)),
                edgecolors="none", alpha=1.0))
            seen.append(skinV)

        # --- body mesh, coloured by owning arm --------------------------
        for name, (V, F) in wm.items():
            if skinV is not None and name not in scrub_names:
                continue          # the skin already shows it
            seen.append(V)
            if name in scrub_names:
                _, idx = tree.query(V)
                own = terr.owner[idx]
                fc = []
                for tri in F:
                    o = int(np.bincount(np.maximum(own[tri], 0)).argmax()) \
                        if (own[tri] >= 0).any() else -1
                    fc.append("#%02x%02x%02x" % (ARM_COLOURS[o % 4] if o >= 0
                                                 else OBSTACLE))
            else:
                fc = "#%02x%02x%02x" % OBSTACLE
            ax.add_collection3d(Poly3DCollection(
                V[F], facecolors=fc, edgecolors="none", alpha=0.95))

        # --- arms: real meshes ------------------------------------------
        for a in range(len(layout)):
            Tw = np.asarray(layout[a], float)
            col = "#%02x%02x%02x" % ARM_COLOURS[a % 4]
            m = terr.owner == a
            j = K.ik(235.11, 0.0, 234.79)
            if m.sum() >= 8:
                c = CoverageController(Pw[m], Nw[m], A[m], Tw)
                pts, last, reached = [], None, []
                for _ in range(frames):
                    out = c.tick()
                    if out is None:
                        break
                    w, arm_xyz, tr = out
                    if not tr and last is not None:
                        pts.append((last, w))
                    last = w
                    if K.ik(*arm_xyz) is not None:
                        reached.append(arm_xyz)
                for p, q in pts:
                    ax.plot([p[0], q[0]], [p[1], q[1]], [p[2], q[2]],
                            c="#ffffff", lw=0.7, alpha=0.5)
                if reached:
                    j = K.ik(*reached[len(reached) // 2]) or j

            for name, (V, F) in armmesh.posed(*j, j3,
                                              T_world_base=Tw).items():
                ax.add_collection3d(Poly3DCollection(
                    V[F], facecolors=_shade(V, F, np.array(
                        ARM_COLOURS[a % 4], float)),
                    edgecolors="none", alpha=1.0))
                seen.append(V)

        ax.set_title(title, color="#c8c8d0", fontsize=10)
        for pane in (ax.xaxis, ax.yaxis, ax.zaxis):
            pane.set_pane_color((0.08, 0.08, 0.10, 1.0))
        ax.tick_params(colors="#50505a", labelsize=6)
        ax.set_xlabel("x mm"); ax.set_ylabel("y mm"); ax.set_zlabel("z mm")
        _fit_axes(ax, np.vstack(seen))
        ax.view_init(elev=elev, azim=azim)

    fig.suptitle(
        f"scrub3d   {100 * rep['covered_frac']:.0f}% of the front reachable"
        f"   per-arm {['%.0f' % v for v in rep['per_arm_cm2']]} cm2"
        f"   phases {phases}", color="#e0e0e8", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=115, facecolor="#14141a")
    plt.close(fig)


def default_layout():
    """Where the arms are. FROM THE RIG FILE, not from a literal here.

    This used to be four matrices written out in full, and the same four were
    written out again in control.py and again in partition.py's self-test.
    Three copies of a constant is not a configurable rig; it is a rig with
    three places to forget. scrub3d/config.json is the one place now.
    """
    try:
        from . import rigconfig
    except ImportError:
        import rigconfig
    return rigconfig.load().layout()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--save", metavar="PATH")
    ap.add_argument("--preview", metavar="PNG")
    ap.add_argument("--frames", type=int, default=1500)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--live", metavar="BAG_OR_EMPTY", nargs="?", const="",
                    help="scan once, then FOLLOW the person frame by frame "
                         "from this bag (or the camera if given no value)")
    ap.add_argument("--capture", metavar="DIR",
                    help="show a REAL scanned person from this capture "
                         "directory instead of the procedural body")
    ap.add_argument("--realtime", action="store_true",
                    help="run the simulation at the arms' real speed instead "
                         "of as fast as the machine can, for watching it live")
    args = ap.parse_args()

    if args.live is not None:
        import os
        scan_dir = args.capture or os.path.join(
            __import__("frames").DATA, "scan01")
        src = args.live or None
        if args.save:
            rr.init("wheelgentic3d", spawn=False); rr.save(args.save)
        else:
            rr.init("wheelgentic3d", spawn=True)
        s = live(scan_dir, src, max_frames=args.frames)
        print(f"  {s['tracked']}/{s['frames']} frames tracked, "
              f"{s['frozen']} frozen by the motion guard")
        print(f"  {s['arms']} arms following, coverage "
              f"{100 * s['covered_frac']:.1f}%, phases {s['phases']}")
        if args.save:
            print(f"  wrote {args.save} -- open with:  rerun {args.save}")
        return

    if args.capture:
        # The procedural body is a stand-in with population-average numbers.
        # Given a capture, show the actual person: same representation, real
        # semi-axes, and a head, which is what makes an operator able to tell
        # at a glance that the model is of the right person in the right pose.
        try:
            from . import scan as SCAN
        except ImportError:
            import scan as SCAN
        body, meshes, obstacles, rep = SCAN.scan(args.capture)
        print(f"  scanned {args.capture}: "
              f"{rep['scrubbable_cm2']:.0f}cm2 scrubbable, "
              f"measuring {rep['clothing']['measuring']}")
        # The measured room, chair and all, as main.py plans against it.
        seen = {"scan_dir": args.capture, "obstacles": obstacles,
                "obstacle_region": rep["obstacle_region"]}
    else:
        body, meshes = anatomical_body(scale=args.scale)
        seen = {}
    layout = default_layout()

    if args.preview:
        preview(body, meshes, layout, args.preview)
        print(f"  wrote {args.preview}")

    if args.save:
        rr.init("wheelgentic3d", spawn=False)
        rr.save(args.save)
    else:
        rr.init("wheelgentic3d", spawn=True)

    s = build(body, meshes, layout, max_frames=args.frames,
              realtime=args.realtime, **seen)
    print(f"logged {s['frames']} frames, {s['arms']} arms")
    print(f"  reachable {100 * s['covered_frac']:.1f}%   "
          f"per-arm cm2 {['%.0f' % v for v in s['per_arm_cm2']]}")
    print(f"  phases {s['phases']}")
    if args.save:
        print(f"  wrote {args.save} -- open with:  rerun {args.save}")


if __name__ == "__main__":
    main()
