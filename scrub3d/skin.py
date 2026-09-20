"""scrub3d/skin.py -- one continuous surface instead of a pile of primitives.

WHAT THIS IS FOR
----------------
The body model is a set of swept superelliptical parts: a trunk, four limb
segments, a head, a neck, hands, legs. That representation is the right one to
MEASURE and to PLAN against -- it is exactly what the camera pipeline produces,
every cell carries an outward normal and an area, and the partition and the
coverage controller read it directly.

It is the wrong thing to LOOK at. Rendered as thirteen separate closed
surfaces, a person comes out as a stack of capsules with visible seams where an
arm meets a shoulder, and the honest description of that is eerie. Anyone
looking at it to check "is this the right person in the right posture" is
fighting the picture.

So this builds a second, purely visual surface: the same parts, fused into one
skin. Poisson reconstruction over the union of their oriented points finds the
surface that wraps all of them, which closes the seams and rounds the joints
because that is what a smooth indicator function does where two blobs overlap.

    bodymodel.Region   measured, functional, what the robot reasons about
    skin.build()       derived, visual, what a person looks at

NOTHING DOWNSTREAM READS THIS. It is not an input to the partition, the
controller, or the collision layer, and it must never become one: a fused
surface has no per-cell normal, no area, and no idea which part of the body it
belongs to. Keeping those two jobs in two objects is what stops a rendering
convenience from quietly becoming a safety input.

WHY NOT A DOWNLOADED HUMAN MESH
--------------------------------
Because it would have to be posed to match, and posing a rigged mesh means
skinning weights, a bind pose and a retarget from our bones to its skeleton --
a large amount of machinery whose failure mode is a mesh that looks convincing
while sitting somewhere the person is not. The surface here is generated FROM
the measurement, so it cannot disagree with it.
"""
import math

import numpy as np


def _region_points(reg, n_ax=26, n_th=40):
    """Oriented points over a region's FULL wrap, in world millimetres.

    The region's own cells cover only the front arc, because that is all the
    camera measured and all a front-mounted arm can reach. For a picture we
    want the whole limb, so the surface is re-sampled here over the full 360
    from the same semi-axes.

    Normals come from the implicit form's gradient, the same expression the
    cells use, so this surface and the measured one agree about which way is
    out. Sampling the parametric surface directly also beats estimating
    normals from a triangle soup, which is what Poisson would otherwise have
    to work from.
    """
    pts = np.asarray(reg.pts, float)
    if len(pts) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    nn = float(getattr(reg, "n_exp", 2.0))

    # Recover the semi-axes profile along the axis from the cells themselves,
    # so this stays correct for a scanned body whose taper was measured.
    z = pts[:, 2]
    zlo, zhi = float(z.min()), float(z.max())
    span = max(zhi - zlo, 1e-6)
    su = np.linspace(0.0, 1.0, n_ax)
    a_prof, b_prof = [], []
    for s in su:
        zc = zlo + s * span
        near = np.abs(z - zc) < max(span / (n_ax - 1), 1e-6) * 1.5
        sel = pts[near] if near.sum() >= 3 else pts
        a_prof.append(float(np.abs(sel[:, 0]).max()))
        b_prof.append(float(np.abs(sel[:, 1]).max()))
    A = np.maximum(np.array(a_prof), 1.0)
    B = np.maximum(np.array(b_prof), 1.0)

    th = np.linspace(0.0, 2 * math.pi, n_th, endpoint=False)
    S, TH = np.meshgrid(su, th, indexing="ij")
    Aa = np.interp(S, su, A)
    Bb = np.interp(S, su, B)
    c, s_ = np.cos(TH), np.sin(TH)
    x = np.sign(c) * np.abs(c) ** (2.0 / nn) * Aa
    y = np.sign(s_) * np.abs(s_) ** (2.0 / nn) * Bb
    zz = zlo + S * span

    gx = nn * np.sign(x) * np.abs(x / Aa) ** (nn - 1.0) / Aa
    gy = nn * np.sign(y) * np.abs(y / Bb) ** (nn - 1.0) / Bb
    P = np.stack([x, y, zz], -1).reshape(-1, 3)
    N = np.stack([gx, gy, np.zeros_like(gx)], -1).reshape(-1, 3)

    # Cap the ends, or Poisson closes them with a bulge of its own choosing.
    for end, sign in ((zlo, -1.0), (zhi, +1.0)):
        rr = np.linspace(0.12, 0.92, 4)
        ca, cb = (A[0], B[0]) if sign < 0 else (A[-1], B[-1])
        cs, ct = np.meshgrid(rr, th, indexing="ij")
        cx = np.sign(np.cos(ct)) * np.abs(np.cos(ct)) ** (2.0 / nn) * ca * cs
        cy = np.sign(np.sin(ct)) * np.abs(np.sin(ct)) ** (2.0 / nn) * cb * cs
        cz = np.full_like(cx, end)
        P = np.vstack([P, np.stack([cx, cy, cz], -1).reshape(-1, 3)])
        N = np.vstack([N, np.tile([0.0, 0.0, sign], (cx.size, 1))])

    N = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
    R, t = reg.T[:3, :3], reg.T[:3, 3]
    return (R @ P.T).T + t, (R @ N.T).T


def build(body, depth=8, density_quantile=0.06):
    """A BodyModel -> (V, F) of one fused skin, world millimetres.

    `density_quantile` trims the vertices Poisson had least evidence for.
    Those are the ones it invented to close the surface out in empty space,
    and left in they appear as a translucent shell floating around the body.
    """
    import open3d as o3d

    P, N = [], []
    for r in body.regions:
        p, n = _region_points(r)
        if len(p):
            P.append(p)
            N.append(n)
    if not P:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int32)
    P, N = np.vstack(P), np.vstack(N)

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(P)
    pcd.normals = o3d.utility.Vector3dVector(N)

    mesh, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=depth, linear_fit=False)
    dens = np.asarray(dens)
    if len(dens):
        mesh.remove_vertices_by_mask(dens < np.quantile(dens, density_quantile))
    # Poisson extends past the data; clip back to where the body actually is.
    lo, hi = P.min(0) - 25.0, P.max(0) + 25.0
    mesh = mesh.crop(o3d.geometry.AxisAlignedBoundingBox(lo, hi))
    mesh.compute_vertex_normals()
    return (np.asarray(mesh.vertices), np.asarray(mesh.triangles, np.int32))


if __name__ == "__main__":
    import time

    try:
        from .anatomy import anatomical_body
    except ImportError:
        from anatomy import anatomical_body

    print("fused body skin")
    body, meshes = anatomical_body()
    t0 = time.time()
    V, F = build(body)
    print(f"  {len(body.regions)} parts -> one surface: {len(V)} vertices, "
          f"{len(F)} triangles, in {time.time() - t0:.2f}s")
    assert len(F) > 5000, "the fused surface came out empty or trivial"

    lo, hi = V.min(0), V.max(0)
    print(f"  extent  x {lo[0]:6.0f}..{hi[0]:6.0f}   y {lo[1]:6.0f}..{hi[1]:6.0f}"
          f"   z {lo[2]:6.0f}..{hi[2]:6.0f} mm")

    # It has to enclose the measured body, or it is a picture of something
    # else. Every scrubbable cell should sit at or inside the skin.
    P, _, _, _ = body.world_cells()
    import open3d as o3d
    m = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(V),
                                  o3d.utility.Vector3iVector(F))
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(m))
    d = scene.compute_distance(
        o3d.core.Tensor(P.astype(np.float32))).numpy()
    print(f"  measured cells sit within {d.max():.1f}mm of the skin "
          f"(median {np.median(d):.1f}mm)")
    assert d.max() < 60.0, ("the fused skin does not follow the measured body; "
                            "it is a picture of something else")

    print(f"\n  the skin is DERIVED and VISUAL. The partition, the controller "
          f"and\n  the collision layer read the Regions, not this. OK")
