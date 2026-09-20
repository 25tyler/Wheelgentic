"""scrub3d/shell.py -- the person as the camera actually saw them.

WHAT THIS IS, AND WHY IT IS NOT skin.py
----------------------------------------
skin.py fuses the THIRTEEN MODELLED PARTS into one surface. It closes the seams
between them, but everything it draws still comes from a table of ellipse
semi-axes, so what you get is a smoothed pile of tubes. It looks like a
mannequin because it is one.

This builds the visual body from the CAPTURE instead. Every vertex here comes
from a depth pixel the D455 returned, coloured by the RGB pixel that saw it and
oriented by a normal an AI model predicted from that same colour image. The
shoulders are that person's shoulders, the shirt has the folds the shirt had,
and the head is that head. Nothing is inferred from a population table.

    anatomy / bodymodel   what the ROBOT reasons about -- cells, areas, normals
    skin.py               those same parts, fused, for when there is no capture
    shell.py              what the CAMERA measured, for a person to look at

WHY THREE SURFACES AND NOT ONE
-------------------------------
Because they answer different questions and merging them would make the answer
to both worse.

The planning surface has to be region-local: every cell belongs to a named body
part, carries an outward normal and an area, and moves with one 4x4 when that
part moves. That is what makes re-posing free and the coverage bookkeeping
honest. A reconstructed mesh has none of that -- it is one connected sheet with
no idea which triangle is a forearm.

The visual surface has to look like the person, and the way to do that is to
stop modelling and start measuring. But it cannot be the planning surface,
because a Poisson mesh has no per-cell area and no guarantee that a triangle
corresponds to anything the arm can reach.

So: NOTHING DOWNSTREAM READS THIS, exactly as with skin.py. The partition, the
controller and the collision layer never see it. What it gains over skin.py is
only that it is real, which is the entire point when somebody is looking at it
and asking "is that me, sitting how I am sitting".

HOW THE RECONSTRUCTION WORKS
-----------------------------
1.  The median depth frame, which is already the average of 60 frames of a
    still subject, so its noise is about 0.3mm rather than 2.3mm.
2.  The person's silhouette from Sapiens -- head, hands and feet INCLUDED here,
    unlike scan.outline, because we draw the face even though we never scrub
    it.
3.  Edge-fattened pixels rejected. Stereo drags depth toward the background
    within a few pixels of any silhouette, and those pixels are what produce
    the "spray of points trailing off the shoulder" that makes a naive
    reconstruction look melted. A 3x3 max-min test at 50mm removes them.
4.  Normals from Sapiens' normal model where the weights are present, from the
    depth neighbourhood where they are not. This matters more than it sounds:
    Poisson reconstruction is driven ENTIRELY by oriented points, so the
    normals decide what the surface does between samples. Normals differentiated
    from depth are noisy exactly where a limb curves away from the camera, and
    that is where a reconstruction goes lumpy.
5.  Screened Poisson, then two crops: by density, which removes the parts of the
    surface Poisson invented where it had no data, and by distance to the
    nearest input point, which removes the balloon it closes behind a
    single-viewpoint capture. What is left is an open front shell that stops
    where the measurement stopped.
6.  Per-vertex colour by projecting each vertex back into the colour image.

WHAT IT DOES NOT DO
--------------------
It does not invent the back. A front camera saw a front, and the mesh ends at
the silhouette rather than wrapping around into a guess. That is the same
honesty rule the rest of the package follows: we never scrub what we never saw,
and we do not draw what we never saw either.
"""
import json
import math
import os

import numpy as np

import frames

try:
    import open3d as o3d
except Exception:                                    # pragma: no cover
    o3d = None

HERE = os.path.dirname(os.path.abspath(__file__))

# Sapiens classes that are not the person. Everything else is drawn, which is
# the difference from scan.outline: we render the face and hands, we just never
# put a sponge on them.
NOT_PERSON = ("Background",)

EDGE_JUMP_MM = 50.0      # a 3x3 window spanning more than this is a fattened edge

# Wide enough to keep a leaning subject whole. A tighter band is tempting --
# the backrest is at 1213-1310mm and the wall at 1452-1568mm -- but `pose_lean`
# puts the person's head past 1300 when they lean back, and cutting it produced
# a headless torso that still passed every size gate. The room is excluded by
# clustering instead, which is the discriminator that actually separates a
# subject from a bystander.
Z_RANGE_MM = (520.0, 1500.0)

# How far from the torso a cluster may sit and still be the same person, and it
# is NOT symmetric. The camera looks at the front of someone sitting down, so
# their own knees and hands come TOWARD it -- measured, 485mm nearer than the
# torso in `pose_forward`. Nothing of theirs is far behind. A symmetric 380mm
# band kept the bystander out and threw the subject's legs away with them.
SUBJECT_NEARER_MM = 750.0     # limbs reaching toward the camera: still them
SUBJECT_BEHIND_MM = 380.0     # past this, it is the chair, the wall, or a person


def _load(capture_dir):
    """Capture directory -> the arrays this module needs, already checked."""
    with open(os.path.join(capture_dir, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    depth = np.load(os.path.join(capture_dir, "depth_median_mm.npy"))
    seg_path = os.path.join(capture_dir, "sapiens_seg.npy")
    seg = np.load(seg_path) if os.path.exists(seg_path) else None
    colour = None
    for name in ("color_median.png", "color_frame0.png"):
        p = os.path.join(capture_dir, name)
        if os.path.exists(p):
            import cv2
            colour = cv2.cvtColor(cv2.imread(p), cv2.COLOR_BGR2RGB)
            break
    if seg is not None and seg.shape != depth.shape:
        raise RuntimeError(
            f"segmentation {seg.shape} does not match depth {depth.shape}; "
            "re-run tools/segment_captures.py")
    return meta, depth, colour, seg


def person_only(depth, seg):
    """The silhouette with NO depth gate. -> boolean mask.

    person_mask() intersects the silhouette with a depth window, which is right
    for choosing points to reconstruct and wrong for finding the holes: a pixel
    with no depth at all is exactly what needs filling, and it has already been
    excluded by the time person_mask has returned.
    """
    if seg is None:
        return np.ones_like(depth, bool)
    import sapiens
    return ~np.isin(seg, [sapiens.IDX[n] for n in NOT_PERSON])


def person_mask(depth, seg, z_range=Z_RANGE_MM):
    """Which pixels are the person, including the parts we never scrub.

    Sapiens where it is available, a depth window where it is not. The depth
    window alone would also catch the chair, which is why the segmentation is
    strongly preferred and the fallback says so.
    """
    inz = (depth > z_range[0]) & (depth < z_range[1])
    if seg is None:
        return inz, "depth window only (no segmentation)"
    import sapiens
    person = ~np.isin(seg, [sapiens.IDX[n] for n in NOT_PERSON])
    return person & inz, "Sapiens silhouette"


def drop_fattened_edges(depth, mask, jump_mm=EDGE_JUMP_MM):
    """Remove pixels whose 3x3 neighbourhood spans a depth cliff.

    This is the anti-edge-fattening test the plan specifies, and visually it is
    the single highest-value line in the file. Stereo matching near a silhouette
    blends foreground and background, producing a skirt of points that trail
    from the person's outline back toward the wall. Poisson then has to explain
    those points, and it does so by growing a web off every edge.

    -> (mask, how many pixels it removed)
    """
    import cv2
    k = np.ones((3, 3), np.uint8)
    valid = depth > 0

    # The cliff is between the person and the room, so the window must see the
    # RAW depth, not the masked depth. Testing inside the mask alone finds
    # nothing, because the fattened pixels are themselves inside the mask and
    # vary smoothly among themselves -- which is exactly how they survive.
    hi = cv2.dilate(depth.astype(np.float32), k)
    lo = cv2.erode(np.where(valid, depth, 1e9).astype(np.float32), k)
    span = hi - lo

    # A window containing a hole is equally untrustworthy: stereo drops out at
    # a silhouette before it fattens, so the pixels bordering a dropout are the
    # same population.
    touches_hole = cv2.erode(valid.astype(np.uint8), k) == 0

    keep = mask & valid & (span < jump_mm) & ~touches_hole
    return keep, int(mask.sum() - keep.sum())


def keep_subject(pc, cam_pos, eps_mm=35.0, min_points=40,
                 nearer_mm=SUBJECT_NEARER_MM,
                 behind_mm=SUBJECT_BEHIND_MM):
    """Keep the person in the chair, drop the room. -> (cloud, what was dropped).

    THE ROOM HAS OTHER PEOPLE IN IT. `pose_lean` has someone standing behind
    the chair at about 1430mm, and Sapiens labels them a person because they
    are one -- 38% of that capture's "person" pixels are the bystander. Poisson
    has no way to know the two are unrelated, so it bridges them into a draped
    sheet that passes every size gate and looks like nothing at all.

    Keeping only the LARGEST cluster is the obvious rule and it is wrong. A
    leaning subject breaks into several clusters where a limb crosses in front
    of the torso, so the largest one is a fragment, and that rule silently
    returned a headless chest.

    What actually separates a subject from a bystander is DEPTH, measured
    against the torso and measured ASYMMETRICALLY. The torso is the biggest
    cluster. Their own knees and hands sit well in FRONT of it, because that is
    what a camera looking at a seated person sees; only the room sits behind.
    A symmetric band excluded the bystander correctly and threw away the
    subject's legs at the same time.
    """
    pts = np.asarray(pc.points)
    if len(pts) < min_points:
        return pc, None
    lbl = np.asarray(pc.cluster_dbscan(eps=eps_mm, min_points=min_points))
    if lbl.max() < 0:
        return pc, None

    d = np.linalg.norm(pts - np.asarray(cam_pos, float), axis=1)
    counts = np.bincount(lbl[lbl >= 0])

    # The anchor is the NEAREST substantial cluster, not the largest. In
    # `pose_lean` the bystander is 18670 points at 1503mm while the subject is
    # split in two by a crossing limb -- 11515 for the legs and torso, 4185 for
    # the head -- so "largest" reconstructs the wrong person, with the right
    # one's head grafted on. The subject is whoever is sitting closest to the
    # camera; that is what the chair is for.
    #
    # The size floor keeps a reaching fingertip from becoming the anchor.
    big = np.flatnonzero(counts >= max(min_points, 0.15 * counts.max()))
    med = np.array([np.median(d[lbl == c]) for c in big])
    anchor = int(big[int(med.argmin())])
    anchor_d = float(med.min())

    keep = np.zeros(len(pts), bool)
    far = []
    for c in range(len(counts)):
        sel = lbl == c
        gap = float(np.median(d[sel])) - anchor_d
        if -nearer_mm <= gap <= behind_mm:
            keep |= sel
        elif counts[c] >= min_points:
            far.append({"points": int(counts[c]), "behind_mm": round(gap)})

    dropped = None
    if not keep.all():
        # Say what was removed, loudly enough to notice. A bystander silently
        # deleted is fine; one silently KEPT is what produced the sheet, and
        # the only difference on the day is which side of this a run lands on.
        dropped = {"points": int((~keep).sum()), "clusters": len(far),
                   "furthest": far}
    return pc.select_by_index(np.flatnonzero(keep)), dropped


def fill_dropouts(depth, person, colour, z_range=Z_RANGE_MM, max_rms_mm=20.0):
    """Fill the holes stereo leaves, using depth predicted from colour.

    -> (depth, inferred mask, stats) -- stats is None when nothing was filled.

    Stereo fails on exactly what a clothed seated person is made of: dark
    fabric and hair return no disparity, so the reconstruction has holes
    through the shirt and across the top of the head. Sapiens predicts depth
    from colour and has no such failure, because it reads shading rather than
    disparity.

    What it returns is AFFINE-INVARIANT -- correct up to one scale and one
    offset for the whole image -- so on its own it is a shape, not a
    measurement. Those two numbers are fitted against the stereo depth that IS
    measured, on the pixels where both exist, and the fit is only worth as much
    as its residual: past max_rms_mm nothing is filled and the run says so. The
    fit is iterated with an outlier trim, because the silhouette pixels that
    disagree worst are the ones stereo got wrong.

    THE FENCE: filled pixels reach the VISUAL surface and nothing else. They
    are not measurements, and an inferred surface is not one an arm may press
    against.

    ON scan01 THIS REFUSES, and the threshold is why it is set where it is. The
    fill works mechanically -- 55993 of 57023 dropout pixels filled, oriented
    points up 71%, and the crown rises 43mm because hair is a textbook stereo
    dropout. But the affine fit lands at 40.8mm rms, and at that residual the
    result is visibly WORSE than the holes were: a horn off the side of the
    head, ragged arm edges, a tendril hanging into space. Fitting in inverse
    depth instead is worse still, at 73.8mm, so the parameterisation is not the
    problem -- the model is simply not that accurate on this capture.

    So the gate is set where it means something rather than where this capture
    would pass it. A capture whose fit is genuinely good gets the holes filled;
    this one keeps them, and keeping a hole is the honest option when the
    alternative is inventing a surface 4cm from where the person is.
    """
    import sapiens
    none = np.zeros_like(person)
    if colour is None or not sapiens.depth_available():
        return depth, none, None
    holes = person & (depth <= 0)
    valid = person & (depth > z_range[0]) & (depth < z_range[1])
    if holes.sum() < 200 or valid.sum() < 2000:
        return depth, none, None

    pred = sapiens.shared(sapiens.SapiensDepth)(colour).astype(np.float64)
    x, y = pred[valid], depth[valid].astype(np.float64)
    keep = np.ones(len(x), bool)
    sol = None
    for _ in range(3):
        A = np.column_stack([x[keep], np.ones(int(keep.sum()))])
        sol = np.linalg.lstsq(A, y[keep], rcond=None)[0]
        r = sol[0] * x + sol[1] - y
        mad = 1.4826 * np.median(np.abs(r - np.median(r)))
        keep = np.abs(r - np.median(r)) < 3.0 * max(mad, 1.0)
    rms = float(np.sqrt(np.mean((sol[0] * x[keep] + sol[1] - y[keep]) ** 2)))
    if rms > max_rms_mm:
        return depth, none, {"filled": 0, "rms_mm": rms,
                             "why": "the affine fit to measured depth is too "
                                    "poor to trust"}

    est = sol[0] * pred + sol[1]
    take = holes & (est > z_range[0]) & (est < z_range[1])
    out = depth.copy()
    out[take] = est[take].astype(depth.dtype)
    return out, take, {"filled": int(take.sum()), "holes": int(holes.sum()),
                       "rms_mm": rms, "scale": float(sol[0]),
                       "offset_mm": float(sol[1])}

def _normals_from_depth(pts, cam_pos):
    """Fallback normals: fit the local neighbourhood, point them at the camera."""
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))
    pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=18.0,
                                                             max_nn=30))
    pc.orient_normals_towards_camera_location(np.asarray(cam_pos, float))
    return np.asarray(pc.normals)


def _normals_from_sapiens(colour, rows, cols, T_wc, pts):
    """Predicted normals, in world coordinates. -> ((N,3), note) or (None, why).

    Sapiens returns a normal per pixel in ITS OWN camera convention, and the
    published material does not state whether that is y-down/z-forward like the
    RealSense optical frame or y-up/z-toward-the-viewer like most graphics. The
    difference is invisible in a normal map image and catastrophic in a Poisson
    reconstruction, which would build the surface inside out.

    So it is not assumed. Every visible point on a front-facing scan must have a
    normal pointing back toward the camera, so both conventions are scored on
    that and the consistent one wins. A convention that cannot reach agreement
    is refused rather than used.
    """
    import sapiens
    if colour is None:
        return None, "no colour frame"
    if not sapiens.normal_available():
        return None, "Sapiens normal weights absent"
    nmap = sapiens.shared(sapiens.SapiensNormal)(colour)
    if nmap is None:
        return None, "normal model returned nothing"

    n_cam = nmap[rows, cols].astype(np.float64)
    to_cam = T_wc[:3, 3] - pts
    to_cam /= np.maximum(np.linalg.norm(to_cam, axis=1, keepdims=True), 1e-9)

    best, best_frac, best_name = None, -1.0, ""
    for name, flip in (("as returned", np.array([1.0, 1.0, 1.0])),
                       ("y,z negated", np.array([1.0, -1.0, -1.0]))):
        w = (n_cam * flip) @ T_wc[:3, :3].T
        ln = np.linalg.norm(w, axis=1, keepdims=True)
        w = np.where(ln > 1e-6, w / np.maximum(ln, 1e-9), 0.0)
        frac = float((np.einsum("ij,ij->i", w, to_cam) > 0).mean())
        if frac > best_frac:
            best, best_frac, best_name = w, frac, name

    if best_frac < 0.80:
        return None, (f"predicted normals disagree with the viewpoint "
                      f"({best_frac:.0%} face the camera) -- refused")
    return best, (f"Sapiens predicted normals, {best_name}, "
                  f"{best_frac:.0%} facing the camera")


def build(capture_dir, voxel_mm=4.0, poisson_depth=9, density_q=0.12,
          max_orphan_mm=28.0, verbose=True, strict=True,
          max_tris=0, fill=True):
    """A capture -> a coloured surface mesh of that person, in world mm.

    -> dict with verts (V,3), faces (F,3), colours (V,3) in 0..1, plus the
    provenance needed to say where every number came from.
    """
    if o3d is None:
        raise RuntimeError("open3d is required for shell.build()")
    meta, depth, colour, seg = _load(capture_dir)
    intr = meta["color_intrinsics"]
    rig = frames.solve(capture_dir)
    T_wc = rig["T_world_camera"]

    mask, mask_src = person_mask(depth, seg)
    n_raw = int(mask.sum())

    # Fill the stereo dropouts BEFORE the edge test. A filled hole is no
    # longer a hole, so its border stops being rejected as untrustworthy --
    # which is the point: those borders are the ragged edges around every dark
    # patch on a shirt.
    if fill:
        depth, inferred, fill_stats = fill_dropouts(
            depth, person_only(depth, seg), colour)
        mask, _ = person_mask(depth, seg)
    else:
        inferred, fill_stats = np.zeros_like(mask), None
    mask, n_edge = drop_fattened_edges(depth, mask)

    rows, cols = np.nonzero(mask)
    z = depth[rows, cols].astype(np.float64)
    x = (cols - intr["ppx"]) * z / intr["fx"]
    y = (rows - intr["ppy"]) * z / intr["fy"]
    cam_pts = np.column_stack([x, y, z])
    pts = frames.apply(T_wc, cam_pts)

    nrm, normal_src = _normals_from_sapiens(colour, rows, cols, T_wc, pts)
    if nrm is None:
        nrm = _normals_from_depth(pts, T_wc[:3, 3])
        normal_src = f"depth neighbourhood ({normal_src})"

    rgb = (colour[rows, cols].astype(np.float64) / 255.0
           if colour is not None else np.full((len(pts), 3), 0.7))

    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))
    pc.normals = o3d.utility.Vector3dVector(nrm)
    pc.colors = o3d.utility.Vector3dVector(rgb)
    if voxel_mm > 0:
        pc = pc.voxel_down_sample(voxel_mm)
    # A single outlier survives voxelisation and drags a spike out of the
    # surface, because Poisson has to pass near every oriented point.
    pc, _ = pc.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
    pc, crowd = keep_subject(pc, T_wc[:3, 3])
    kept = np.asarray(pc.points)

    mesh, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pc, depth=poisson_depth, scale=1.1, linear_fit=True)
    dens = np.asarray(dens)
    n_poisson = len(mesh.vertices)

    # Crop 1 -- density. Where Poisson had no samples it still produces surface,
    # and that surface is the balloon behind the person.
    if len(dens):
        mesh.remove_vertices_by_mask(dens < np.quantile(dens, density_q))

    # Crop 2 -- distance to the nearest measured point. This is what actually
    # opens the shell: a vertex more than max_orphan_mm from any depth pixel was
    # not measured, it was interpolated across the silhouette.
    from scipy.spatial import cKDTree
    V = np.asarray(mesh.vertices)
    if len(V) and len(kept):
        d, _ = cKDTree(kept).query(V, k=1)
        mesh.remove_vertices_by_mask(d > max_orphan_mm)

    # Crop 3 -- keep the person, drop whatever islands survived.
    tri_lbl, counts, _ = mesh.cluster_connected_triangles()
    tri_lbl = np.asarray(tri_lbl)
    counts = np.asarray(counts)
    if len(counts):
        mesh.remove_triangles_by_mask(tri_lbl != int(counts.argmax()))
        mesh.remove_unreferenced_vertices()

    # Decimate for the LIVE view only. Skinning moves every vertex every
    # frame, so the whole mesh is re-logged each frame and none of it can be
    # sent once as a static transform. At 64k vertices that is about 2MB a
    # frame, which is gigabytes across a three-minute session; at 24k
    # triangles it is under a tenth of that and looks the same on screen.
    n_full = len(mesh.triangles)
    n_full_v = len(mesh.vertices)
    if max_tris and n_full > max_tris:
        mesh = mesh.simplify_quadric_decimation(int(max_tris))
        mesh.remove_unreferenced_vertices()

    mesh.compute_vertex_normals()
    V = np.asarray(mesh.vertices)
    F = np.asarray(mesh.triangles)
    # Colour is re-sampled from the image AFTER decimation rather than carried
    # through it, so a merged vertex takes the colour of where it ended up.
    C = _colour_vertices(V, T_wc, intr, colour)

    out = {
        "verts": V, "faces": F, "colours": C,
        "normals": np.asarray(mesh.vertex_normals).copy(),
        "T_world_camera": T_wc, "intrinsics": intr,
        "source": {
            "capture": os.path.basename(os.path.normpath(capture_dir)),
            "mask": mask_src, "normals": normal_src,
            "depth_px": n_raw, "edge_px_dropped": n_edge,
            "points_used": int(len(kept)), "poisson_verts": n_poisson,
            "tris_before_decimation": int(n_full),
            "verts_before_decimation": int(n_full_v),
            "crowd": crowd,
            "fill": fill_stats,
            "inferred_px": int((inferred & mask).sum()),
        },
    }
    out["gates"] = verify(out)
    if verbose:
        report(out)
    bad = [g for g in out["gates"] if not g[1]]
    if bad and strict:
        raise RuntimeError(
            "the reconstruction does not look like a seated person: "
            + "; ".join(f"{n} ({d})" for n, _, d in bad))
    return out


# A seated adult, measured from this rig: the crown sits 1248mm above the floor
# and the acromion at 1012mm, the torso is 245mm deep, and the camera is 833mm
# up. The bounds below are those numbers with room either side, and they exist
# because of a specific failure: `pose_lean` has a bystander standing behind the
# chair, and reconstructing them instead produced a 200mm-deep draped sheet that
# passed a width-and-height gate and looked like nothing human at all.
#
# A refusal is the right outcome there. The live view falls back to the modelled
# body and says why, which is worse-looking and honest, rather than showing a
# confident picture of a surface nobody has.
HEAD_Z_MM = (1050.0, 1450.0)      # crown height above the floor
MIN_DEPTH_MM = 250.0              # a person is not a sheet
MIN_VERTS = 15000


def verify(sh):
    """Does this reconstruction look like a seated person? -> [(name, ok, why)]

    Judged on what was RECONSTRUCTED, not on what is left after thinning it for
    the screen. Decimating to 24k triangles for the live view took the mesh to
    12580 vertices and tripped a gate asking whether a person had been found at
    all -- a question decimation cannot answer either way.
    """
    V = sh["verts"]
    n_v = sh.get("source", {}).get("verts_before_decimation", len(V))
    if not len(V):
        return [("has any surface at all", False, "zero vertices")]
    ext = V.max(0) - V.min(0)
    head = float(V[:, 2].max())
    return [
        ("enough surface", n_v >= MIN_VERTS, f"{n_v} vertices"),
        ("crown at a seated height", HEAD_Z_MM[0] <= head <= HEAD_Z_MM[1],
         f"{head:.0f}mm above the floor"),
        ("has depth, not a sheet", ext[0] >= MIN_DEPTH_MM,
         f"{ext[0]:.0f}mm front to back"),
        ("plausible width", 300.0 <= ext[1] <= 1900.0, f"{ext[1]:.0f}mm across"),
    ]


def _colour_vertices(V, T_wc, intr, colour):
    """Project each vertex back into the colour image and sample it."""
    if colour is None or not len(V):
        return np.full((len(V), 3), 0.72)
    cam = frames.apply(np.linalg.inv(T_wc), V)
    z = np.maximum(cam[:, 2], 1e-6)
    u = np.rint(cam[:, 0] * intr["fx"] / z + intr["ppx"]).astype(int)
    v = np.rint(cam[:, 1] * intr["fy"] / z + intr["ppy"]).astype(int)
    H, W = colour.shape[:2]
    ok = (u >= 0) & (u < W) & (v >= 0) & (v < H)
    C = np.full((len(V), 3), 0.72)
    C[ok] = colour[v[ok], u[ok]].astype(np.float64) / 255.0
    return C


def report(sh):
    s = sh["source"]
    V, F = sh["verts"], sh["faces"]
    print(f"  reconstruction from {s['capture']}")
    print(f"    silhouette    {s['mask']}")
    print(f"    normals       {s['normals']}")
    print(f"    depth pixels  {s['depth_px']:7d}   "
          f"edge-fattened dropped {s['edge_px_dropped']:6d}")
    print(f"    oriented pts  {s['points_used']:7d}")
    f = s.get("fill")
    if f and f.get("filled"):
        print(f"    AI depth fill  {f['filled']} of {f['holes']} dropout pixels"
              f", affine fit to measured depth {f['rms_mm']:.1f}mm rms"
              f"  ({s['inferred_px']} survive into the surface)")
    elif f:
        print(f"    AI depth fill  refused: {f.get('why', '')}"
              f" ({f['rms_mm']:.1f}mm rms)")
    if s.get("crowd"):
        c = s["crowd"]
        print(f"    NOT ALONE     dropped {c['points']} points; "
              f"{c['clusters']} cluster(s) outside the subject band"
              + (f", furthest {c['furthest'][0]['behind_mm']:+.0f}mm"
                 if c["furthest"] else ""))
    extra = ("" if s.get("tris_before_decimation", len(F)) == len(F)
             else f", decimated from {s['tris_before_decimation']}")
    print(f"    poisson       {s['poisson_verts']:7d} verts  -> cropped to "
          f"{len(V)} verts / {len(F)} tris{extra}")
    if len(V):
        ext = V.max(0) - V.min(0)
        print(f"    extent mm     {ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f}"
              f"   top of head at z={V[:, 2].max():.0f}mm")
    for name, ok, why in sh.get("gates", []):
        print(f"    [{'ok ' if ok else 'NO '}] {name:28s} {why}")


# ---------------------------------------------------------------------------
# Skinning: making the measured shell follow the live pose.
#
# The shell is one connected sheet with no notion of body parts, so it cannot
# be re-posed the way the region model is. Bind each vertex to the body parts
# nearest it and it can: the parts still supply the motion, and the shell
# supplies the shape. This is ordinary linear blend skinning, with the weights
# derived from distance to each part's bone segment rather than painted.
# ---------------------------------------------------------------------------

def _seg_dist(p, a, b):
    """Distance from points p to the segment ab, and the parameter along it."""
    ab = b - a
    L2 = float(ab @ ab)
    t = np.zeros(len(p)) if L2 < 1e-9 else np.clip(((p - a) @ ab) / L2, 0, 1)
    closest = a + t[:, None] * ab
    return np.linalg.norm(p - closest, axis=1), t


def bind(verts, body, n_influences=2, falloff_mm=60.0):
    """Attach each vertex to the body parts nearest it. -> a binding dict.

    Two influences, not four: with thirteen convex parts a vertex is genuinely
    between at most two of them, and more influences only smear a shoulder into
    a chest. The weights fall off over `falloff_mm`, which is roughly the width
    of the blend you want across a joint.
    """
    # The bone is not stored as a length anywhere, so take it from the cells:
    # they live in the region's own frame with +Z along the bone, so their own
    # extent along Z IS the bone. Typing a length here would let the binding
    # disagree with the geometry it is binding to.
    names, A, B = [], [], []
    for r in body.regions:
        T = np.asarray(r.T, float)
        z = np.asarray(r.pts, float)[:, 2]
        names.append(r.name)
        A.append(frames.apply(T, np.array([[0.0, 0.0, float(z.min())]]))[0])
        B.append(frames.apply(T, np.array([[0.0, 0.0, float(z.max())]]))[0])
    A, B = np.array(A), np.array(B)

    D = np.empty((len(verts), len(names)))
    for j in range(len(names)):
        D[:, j], _ = _seg_dist(verts, A[j], B[j])

    order = np.argsort(D, axis=1)[:, :n_influences]
    d = np.take_along_axis(D, order, axis=1)
    w = np.exp(-np.maximum(d - d[:, :1], 0.0) / falloff_mm)
    w /= w.sum(1, keepdims=True)

    rest = [np.asarray(r.T, float) for r in body.regions]
    inv = [np.linalg.inv(T) for T in rest]
    local = np.empty((len(verts), n_influences, 3))
    for k in range(n_influences):
        for j in range(len(names)):
            sel = order[:, k] == j
            if sel.any():
                local[sel, k] = frames.apply(inv[j], verts[sel])
    return {"idx": order, "w": w, "local": local, "names": names,
            "n_regions": len(names),
            "local32": local.astype(np.float32),
            "w32": w.astype(np.float32)}


def pose(binding, body):
    """Move the bound shell to the body's current pose. -> (V,3) world verts.

    This runs once per tracked frame, so it is written as two contractions
    rather than as a loop over influences and regions. The loop version was
    correct and cost 28ms on 64k vertices, which would have set the frame rate
    of the whole live view; gathering the transforms and letting BLAS do the
    work costs a few milliseconds for the same answer.
    """
    # float32 throughout. This is DISPLAY geometry: the error it introduces is
    # under a micron, the screen pixel is 1.6mm, and the planning surface that
    # the robot actually reasons about never passes through here at all. In
    # float64 the same call costs 15ms, which would have set the frame rate of
    # the live view.
    T = np.stack([np.asarray(r.T, np.float32) for r in body.regions])
    M = T[binding["idx"]]                                          # (V,K,4,4)
    p = (np.einsum("vkij,vkj->vki", M[..., :3, :3], binding["local32"])
         + M[..., :3, 3])
    return np.einsum("vk,vki->vi", binding["w32"], p).astype(np.float64)


def cell_map(verts, body, max_mm=70.0):
    """Nearest scrubbable cell per vertex. -> (region_idx, cell_idx), -1 if far.

    This is what lets the reconstruction show coverage. The robot reasons about
    cells; a person looks at a surface. Attaching each vertex to a cell once,
    at bind time, means the live view can paint "arm 2 has scrubbed here" onto
    the real shoulder rather than onto a cylinder standing in for it.

    Vertices further than max_mm from any cell get -1 and are drawn plain:
    hair, the face, the backs of the hands. Better an honestly unpainted patch
    than a coverage colour on a surface no arm will ever touch.
    """
    from scipy.spatial import cKDTree
    P, R = [], []
    for ri, r in enumerate(body.regions):
        w, _ = r.world()
        # SCRUBBABLE cells only, because that is the index space the territory
        # map and every controller use. Indexing all cells here produces a
        # number that looks right, lands inside no array, and paints coverage
        # onto whichever limb happens to sit at that offset.
        w = w[r.scrubbable]
        P.append(w)
        R.append(np.full(len(w), ri))
    if not P:
        z = np.full(len(verts), -1)
        return {"gid": z, "region": z.copy(), "dist": np.full(len(verts), np.inf)}
    P = np.vstack(P)
    R = np.concatenate(R)
    d, i = cKDTree(P).query(verts, k=1)
    ok = d <= max_mm
    # `gid` indexes the CONCATENATED cell list, which is the same order
    # body.world_cells() and partition's territory map use. Returning a
    # per-region index instead would make every caller redo that arithmetic,
    # and the first one to get the offsets wrong would paint the wrong limb.
    return {"gid": np.where(ok, i, -1), "region": np.where(ok, R[i], -1),
            "dist": d}


LIGHT = np.array([0.35, -0.75, 0.55])
LIGHT = LIGHT / np.linalg.norm(LIGHT)


def render_still(sh, path, yaws=(0, -35, -70), size=(560, 780), colours=None):
    """Rasterise the shell to a PNG so it can be checked over a terminal.

    THE POINT IS THAT SOMEBODY LOOKS. Every gate in verify() can pass on a
    reconstruction of the wrong person -- that is exactly what the bystander in
    `pose_lean` produced -- and the check that catches it is a human glancing at
    the picture and seeing that it is not them. A PLY needs a viewer; a PNG does
    not, so this is the version that actually gets looked at.

    Painter's algorithm rather than a z-buffer: with a single-viewpoint open
    shell there is very little self-occlusion to get wrong, and sorting 125k
    triangles by depth costs milliseconds.
    """
    import cv2
    V, F = sh["verts"], sh["faces"]
    C = sh["colours"] if colours is None else colours
    W, H = size
    tiles = []
    for yaw in yaws:
        a = math.radians(yaw)
        R = np.array([[math.cos(a), -math.sin(a), 0.0],
                      [math.sin(a), math.cos(a), 0.0],
                      [0.0, 0.0, 1.0]])
        P = V @ R.T
        # World is +Z up and +X the way the person faces, so the viewer looks
        # along -X: y goes right on screen, z goes up, x is the depth key.
        u, v, d = P[:, 1], P[:, 2], P[:, 0]
        m = 40
        sc = min((W - 2 * m) / max(np.ptp(u), 1e-6),
                 (H - 2 * m) / max(np.ptp(v), 1e-6))
        px = (u - u.min()) * sc + m
        py = (H - m) - (v - v.min()) * sc
        T = V[F]
        n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        n = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        lam = 0.30 + 0.70 * np.abs(n @ LIGHT)
        rgb = np.clip(C[F].mean(1) * 255.0 * lam[:, None], 0, 255).astype(np.uint8)
        img = np.full((H, W, 3), (24, 26, 30), np.uint8)
        pts = np.column_stack([px, py])[F].astype(np.int32)
        for i in np.argsort(d[F].mean(1)):
            cv2.fillConvexPoly(img, pts[i],
                               tuple(int(x) for x in rgb[i][::-1]))
        tiles.append(img)
    cv2.imwrite(path, np.hstack(tiles))
    return path

def save(sh, path):
    """Write the shell as a coloured PLY, so it can be opened anywhere."""
    m = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(sh["verts"]),
        o3d.utility.Vector3iVector(sh["faces"]))
    m.vertex_colors = o3d.utility.Vector3dVector(sh["colours"])
    m.compute_vertex_normals()
    o3d.io.write_triangle_mesh(path, m)
    return path


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--capture", default=os.path.join(HERE, "data", "scan01"))
    ap.add_argument("--out", default="")
    ap.add_argument("--preview", metavar="PNG", default="",
                    help="write a still to look at, three angles")
    a = ap.parse_args()

    print(f"\nreconstructing {a.capture}")
    sh = build(a.capture)
    V = sh["verts"]

    assert len(V) > 5000, f"only {len(V)} vertices -- that is not a person"
    assert len(sh["faces"]) > 10000, "too few triangles for a body"

    # The check that matters: the reconstruction must be the size of a seated
    # person, in world millimetres, sitting on the floor plane we fitted.
    # The scan protocol holds the arms about 20 degrees clear of the torso to
    # seal the axilla, so the SCANNED pose is much wider than the resting pose
    # this person will actually be scrubbed in -- 1564mm across the fingertips,
    # measured. Gating at shoulder width would reject a correct reconstruction.
    ext = V.max(0) - V.min(0)
    assert 400.0 < ext[1] < 1900.0, f"width {ext[1]:.0f}mm is not a person"
    assert 400.0 < ext[2] < 1300.0, f"height {ext[2]:.0f}mm is not a person"
    print(f"\n  size gate: {ext[1]:.0f}mm across the arms, {ext[2]:.0f}mm tall"
          f"  OK")

    # And it must be OPEN, not a closed balloon. A watertight mesh here would
    # mean Poisson invented a back, which is the failure this crops against.
    m = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(V), o3d.utility.Vector3iVector(sh["faces"]))
    assert not m.is_watertight(), \
        "the shell closed behind the person -- it is inventing the back"
    print("  open shell: the back is absent, not guessed  OK")

    # --- it has to FOLLOW the person, not just depict them ------------------
    #
    # A reconstruction that cannot be re-posed is a photograph. Bind it to the
    # scanned skeleton and it becomes the thing the live loop can drive: the
    # parts supply the motion, the shell supplies the shape.
    import time

    import bodymodel
    import scan as SCAN

    print("\n  binding the shell to the scanned skeleton")
    body, _, _, _ = SCAN.scan(a.capture)
    bnd = bind(V, body)

    # Gate 1: bind then pose, unmoved, must return the same vertices. If this
    # is not exact the binding is not a change of coordinates, it is a filter,
    # and every later pose inherits whatever it smeared.
    back = pose(bnd, body)
    err = np.linalg.norm(back - V, axis=1).max()
    print(f"    round trip at the bind pose: {err:.2e} mm")
    # A micron, not zero, because the re-pose runs in float32 on purpose. The
    # screen pixel is 1.6mm and the planning surface never comes through here.
    assert err < 1e-2, "bind/pose is not the identity at the bind pose"

    # Gate 2: moving a bone must move the vertices bound to it, and must NOT
    # move the trunk. A binding that drags the chest when an elbow bends is
    # worse than no binding, because it looks plausible.
    moved = {}
    for r in body.regions:
        if r.name.startswith("forearm"):
            T = np.asarray(r.T, float).copy()
            T[:3, 3] = T[:3, 3] + np.array([0.0, 0.0, -120.0])
            moved[r.name] = T
    V2 = pose(bnd, bodymodel.repose(body, **moved))
    shift = np.linalg.norm(V2 - V, axis=1)
    # Which region each vertex is BOUND to, not which cell is nearest. The
    # trunk carries no scrubbable cells, so a cell-map test finds no trunk
    # vertices at all and silently averages an empty set.
    primary = bnd["idx"][:, 0]
    names = bnd["names"]
    fore = np.array([names[j].startswith("forearm") for j in primary])
    trunk = np.array([names[j] == "trunk" for j in primary])
    print(f"    forearm vertices moved {shift[fore].mean():.0f}mm, "
          f"trunk vertices moved {shift[trunk].mean():.1f}mm")
    assert shift[fore].mean() > 60.0, "the forearm did not follow its bone"
    assert shift[trunk].mean() < 12.0, "moving a forearm dragged the chest"

    # Gate 3: fast enough to drive from the live loop. track.py runs at 20fps,
    # so a re-pose costing more than about 10ms would set the frame rate.
    t0 = time.perf_counter()
    for _ in range(20):
        pose(bnd, body)
    ms = (time.perf_counter() - t0) / 20 * 1000.0
    print(f"    re-pose cost: {ms:.1f}ms for {len(V)} vertices")
    assert ms < 25.0, f"re-posing at {ms:.0f}ms cannot keep up with tracking"

    # Gate 4: coverage can be painted onto it.
    # A QUARTER, not most, and that is the honest number. Only the four limb
    # regions carry scrubbable cells: the torso, head, hands and legs are drawn
    # but never touched. An earlier gate here demanded half the shell map to a
    # cell, which was written when cell_map indexed every cell rather than the
    # scrubbable ones, and would now be satisfied only by painting coverage
    # onto surfaces no arm will ever reach.
    cm = cell_map(V, body)
    painted = float((cm["gid"] >= 0).mean())
    reg = cm["region"]
    scrubbable_regions = {i for i, r in enumerate(body.regions)
                          if r.scrubbable.any()}
    seen = {int(j) for j in np.unique(reg) if j >= 0}
    missing = sorted(body.regions[i].name for i in scrubbable_regions - seen)
    assert not missing, f"no shell vertex maps to {missing}"

    # The global index must line up with the concatenated cell list, or the
    # live view paints arm 2's progress onto arm 0's territory.
    n_cells = len(body.world_cells()[0])
    assert cm["gid"].max() < n_cells, "cell index runs past the cell list"
    print(f"    {painted:.0%} of vertices map to a scrubbable cell "
          f"(the rest is hair, face and hands, which are never scrubbed)")
    assert painted > 0.10, "almost none of the shell has a cell to colour"

    if a.preview:
        print(f"  wrote {render_still(sh, a.preview)}"
              f" -- LOOK AT IT. Every gate above can pass on a"
              f" reconstruction of the wrong person.")
    if a.out:
        print(f"  wrote {save(sh, a.out)}")
    print("\nOK")


if __name__ == "__main__":
    main()
