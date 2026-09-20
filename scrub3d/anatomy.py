"""scrub3d/anatomy.py -- a body built from swept elliptical cross-sections.

WHY THIS SHAPE AND NOT A DOWNLOADED HUMAN MESH
-----------------------------------------------
This is deliberately THE SAME REPRESENTATION the D455 pipeline produces, not a
stand-in that behaves differently. Part 1b of the plan measures a limb by
taking its width from an RGB silhouette and its front profile from depth, and
closing the two into an elliptical cross-section per station along the bone.
That is exactly what is built here, with population-typical numbers standing in
for measured ones.

The consequences are worth stating, because they are the reason not to just
grab a mesh off Sketchfab:

  - When the camera is connected, nothing downstream changes. The same
    partition, the same controller, the same renderer. Only the source of the
    semi-axes changes, from a table to a measurement.
  - Anything that looks wrong here will look wrong the same way with real data.
    A downloaded mesh would hide representation errors behind nice topology.
  - No licence, no download, no attribution. Every parametric human body model
    in existence (SMPL-X, STAR, SUPR, SKEL) is non-commercial and
    non-redistributable, and MakeHuman's CC0 exception only applies to GUI
    exports.

RENDER THE WHOLE LIMB, SCRUB ONLY THE FRONT
--------------------------------------------
The mesh wraps a full 360 degrees so it reads as a body. The scrubbable CELLS
cover only the arc a front-mounted camera measured. That split is the honest
one: we draw what we believe is there, and we only touch what we saw.

SUPERELLIPSE, NOT JUST ELLIPSE
-------------------------------
Cross-sections use |x/a|^n + |y/b|^n = 1. n=2 is an ellipse, right for limbs.
A torso is closer to n=3, a rounded rectangle. Carrying the exponent now means
the real fit can solve for it later instead of being locked to circles.
"""
import json
import math
import os

import numpy as np

try:
    from . import frames as FRAME
    from .bodymodel import BodyModel, Region
except ImportError:
    import frames as FRAME
    from bodymodel import BodyModel, Region

# --- Population-typical adult measurements, millimetres ---------------------
# Circumferences are the measurable quantity; semi-axes are derived from them
# below, which keeps the table in units a tape measure produces.
ADULT = dict(
    biacromial_mm=390.0,        # shoulder to shoulder, bone to bone
    upper_arm_len_mm=330.0,     # shoulder to elbow
    forearm_len_mm=265.0,       # elbow to wrist
    upper_arm_circ_mm=300.0,    # mid upper arm
    elbow_circ_mm=270.0,
    forearm_circ_mm=280.0,      # proximal forearm, the thickest part
    wrist_circ_mm=170.0,
    chest_circ_mm=980.0,
    waist_circ_mm=860.0,
    torso_len_mm=520.0,         # seat to shoulder
    limb_flatten=0.86,          # b/a: limbs are slightly oval, not round
    # b/a, where a is the ANTERIOR semi-axis and b the LATERAL one, because
    # that is the order _swept consumes them in: a lands on local +X, which
    # region_pose aims at the camera, and b on local +Y, across the body.
    #
    # This was 0.62, which built a chest 356mm deep and 221mm wide. Those are
    # the right two numbers the wrong way round, and the table contradicted
    # itself: biacromial is 390mm, so the shoulders hung 85mm clear of the
    # ribcage on each side. It matters because the torso is the obstacle the
    # body-clearance test reasons about, and a torso too NARROW lets an arm
    # plan closer to a real shoulder than D_BODY allows, which is the unsafe
    # direction. 1.5 gives a 347mm wide, 231mm deep chest.
    torso_flatten=1.5,
    torso_n=3.0,                # superellipse exponent: rounded rectangle
)


def _semi_axes(circ_mm, flatten, n=2.0):
    """Circumference + flattening -> (a, b) semi-axes.

    For an ellipse, Ramanujan's approximation inverts cleanly enough by scaling
    a unit shape, which also works for any superellipse exponent. Solving it
    numerically rather than analytically keeps one code path for all n.
    """
    a0, b0 = 1.0, flatten
    t = np.linspace(0, 2 * math.pi, 512, endpoint=False)
    x = np.sign(np.cos(t)) * np.abs(np.cos(t)) ** (2.0 / n) * a0
    y = np.sign(np.sin(t)) * np.abs(np.sin(t)) ** (2.0 / n) * b0
    p = np.c_[x, y]
    unit_circ = float(np.linalg.norm(np.diff(np.vstack([p, p[:1]]), axis=0),
                                     axis=1).sum())
    s = circ_mm / unit_circ
    return a0 * s, b0 * s


def _superellipse(theta, a, b, n):
    """Points on |x/a|^n + |y/b|^n = 1 at parameter theta. Vectorized."""
    c, s = np.cos(theta), np.sin(theta)
    x = np.sign(c) * np.abs(c) ** (2.0 / n) * a
    y = np.sign(s) * np.abs(s) ** (2.0 / n) * b
    return x, y


def _swept(T, length, sections, n_ax=16, n_th=28, front_arc=None,
           name="part", scrubbable=True):
    """Build one swept-superellipse part.

    `sections` is [(s, a, b)] with s in [0,1] along the axis; semi-axes are
    interpolated between them, which is how a forearm tapers from elbow to
    wrist.

    Returns (Region, (V, F)). The Region carries FRONT-ARC cells for scrubbing;
    the mesh wraps the full 360 for rendering.
    """
    s_key = np.array([k[0] for k in sections], float)
    a_key = np.array([k[1] for k in sections], float)
    b_key = np.array([k[2] for k in sections], float)
    nn = ADULT["torso_n"] if name == "torso" else 2.0

    # --- full-wrap mesh --------------------------------------------------
    su = np.linspace(0.0, 1.0, n_ax)
    tu = np.linspace(0.0, 2 * math.pi, n_th, endpoint=False)
    A = np.interp(su, s_key, a_key)
    B = np.interp(su, s_key, b_key)
    X, Y = _superellipse(tu[None, :], A[:, None], B[:, None], nn)
    Z = np.repeat((su * length)[:, None], n_th, axis=1)
    V = np.stack([X, Y, Z], -1).reshape(-1, 3)

    F = []
    for i in range(n_ax - 1):
        for j in range(n_th):
            j2 = (j + 1) % n_th
            v00, v01 = i * n_th + j, i * n_th + j2
            v10, v11 = (i + 1) * n_th + j, (i + 1) * n_th + j2
            F.append((v00, v10, v11))
            F.append((v00, v11, v01))
    F = np.array(F, np.int32)

    # --- front-arc cells for scrubbing -----------------------------------
    lo, hi = front_arc if front_arc else (-math.pi / 2, math.pi / 2)
    cs = np.linspace(0.02, 0.98, n_ax)
    ct = np.linspace(lo, hi, n_th)
    S, TH = np.meshgrid(cs, ct, indexing="ij")
    ca = np.interp(S, s_key, a_key)
    cb = np.interp(S, s_key, b_key)
    cx, cy = _superellipse(TH, ca, cb, nn)
    pts = np.stack([cx, cy, S * length], -1).reshape(-1, 3)

    # Outward normal is the gradient of the implicit form.
    #
    # The parameterisation x = sign(cos t)*|cos t|^(2/n)*a satisfies
    # |x/a|^n + |y/b|^n = 1, so the gradient is
    #     df/dx = n * |x/a|^(n-1) * sign(x) / a
    # Note the exponent is n-1, NOT 2/n-1. Getting that wrong collapses the
    # normal to a constant direction per quadrant -- for n=2 it yields
    # (sign(x)/a, sign(y)/b), which is the same vector everywhere in a
    # quadrant and therefore useless for deciding which way a sponge should
    # press. The outward-normal assertion in __main__ is what caught it.
    gx = nn * np.sign(cx) * np.abs(cx / ca) ** (nn - 1.0) / ca
    gy = nn * np.sign(cy) * np.abs(cy / cb) ** (nn - 1.0) / cb
    nrm = np.stack([gx, gy, np.zeros_like(gx)], -1).reshape(-1, 3)
    nl = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = nrm / np.maximum(nl, 1e-9)

    # Cell area: arc length along theta times the axial step.
    d_ax = length / n_ax
    r_eff = np.hypot(cx, cy)
    d_th = (hi - lo) / max(n_th - 1, 1)
    area = (r_eff * d_th * d_ax).ravel()

    reg = Region(name=name, T=np.asarray(T, float), pts=pts, nrm=nrm,
                 area=area, scrubbable=np.full(len(pts), scrubbable),
                 arc=(lo, hi), n_exp=nn)
    return reg, (V, F)


def _pose(x, y, z, rx=0.0, ry=0.0, rz=0.0):
    cx, sx, cy, sy, cz, sz = (math.cos(rx), math.sin(rx), math.cos(ry),
                              math.sin(ry), math.cos(rz), math.sin(rz))
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    T = np.eye(4)
    T[:3, :3] = Rz @ Ry @ Rx
    T[:3, 3] = (x, y, z)
    return T


# Bone directions for a seated person AT REST, hands on the thighs. Unit
# vectors in world coordinates, where +X is the way they face and +Z is up.
#
# This replaces arms held out to the sides. That pose was convenient -- every
# limb side-on to the camera, nothing foreshortened, nothing overlapping -- and
# it is not how anybody sits. Someone being washed rests their hands on their
# legs, and the rig has to work for the pose they will actually be in: forearms
# nearly horizontal over the thighs, upper arms close to the trunk. That is a
# harder reach and a much tighter clearance problem than arms in clear air, so
# planning against the easy pose would have flattered every number.
REST_POSE = {
    "upper_arm": (0.10, 0.22, -0.97),    # down, close to the trunk
    "forearm": (0.93, 0.06, -0.36),      # forward, slightly down, onto the thigh
    "thigh": (0.97, 0.14, 0.05),         # forward and roughly level, seated
    "shin": (0.10, 0.02, -0.99),         # down from the knee
}


def anatomical_body(scale=1.0, arm_drop=0.55, arm_out=0.25, seat_z=1050.0,
                    measurements=None, pose_dirs=None):
    """A seated adult facing +X, at rest. -> (BodyModel, {region: (V, F)}).

    `scale` and `measurements` exist so a test can build a genuinely different
    person without touching anything downstream -- which is the property the
    coverage controller's "nothing is preset" test depends on.

    Head, neck, hands and legs are built, and neither reason is decoration. A
    body missing them does not read as a person, so an operator cannot tell at
    a glance whether the model matches who is in the chair. And the parts a
    robot could hit -- the head above all -- have to EXIST before a clearance
    test can avoid them. Every one of them is `scrubbable=False`: rendered and
    avoided, never scrubbed.
    """
    d = dict(REST_POSE)
    if pose_dirs:
        d.update(pose_dirs)
    m = dict(ADULT)
    if measurements:
        m.update(measurements)
    for k in list(m):
        if k.endswith("_mm"):
            m[k] *= scale

    body, meshes = BodyModel(), {}

    # --- torso ------------------------------------------------------------
    ca, cb = _semi_axes(m["chest_circ_mm"], m["torso_flatten"], m["torso_n"])
    wa, wb = _semi_axes(m["waist_circ_mm"], m["torso_flatten"], m["torso_n"])
    reg, mesh = _swept(
        _pose(0, 0, seat_z - m["torso_len_mm"]), m["torso_len_mm"],
        [(0.0, wa, wb), (1.0, ca, cb)],
        front_arc=(-math.pi / 2.2, math.pi / 2.2),
        name="torso", scrubbable=False)     # chest excluded by policy
    body.regions.append(reg)
    meshes["torso"] = mesh

    ua, ub = _semi_axes(m["upper_arm_circ_mm"], m["limb_flatten"])
    ea, eb = _semi_axes(m["elbow_circ_mm"], m["limb_flatten"])
    fa, fb = _semi_axes(m["forearm_circ_mm"], m["limb_flatten"])
    wra, wrb = _semi_axes(m["wrist_circ_mm"], m["limb_flatten"])

    def _part(name, origin, direction, length, sections, scrubbable=True,
              arc=None):
        """Place one part by its bone DIRECTION. -> its distal end.

        Directions, not Euler angles. A limb is a bone pointing somewhere, and
        saying so directly removes the step where a rotation order decides the
        roll for you -- which is how every limb's scrubbable arc ended up
        facing backwards. region_pose fixes the roll at the front, once.
        """
        T = FRAME.region_pose(origin, direction)
        reg, mesh = _swept(T, length, sections, name=name, front_arc=arc,
                           scrubbable=scrubbable)
        body.regions.append(reg)
        meshes[name] = mesh
        return (T[:3, :3] @ np.array([0.0, 0.0, length])) + T[:3, 3]

    def _mirror(v, sgn):
        return (v[0], sgn * v[1], v[2])

    # --- neck and head, obstacles and never scrubbed -----------------------
    neck_a, neck_b = 58.0 * scale, 52.0 * scale
    head_a, head_b = 98.0 * scale, 82.0 * scale
    top = _part("neck", (0.0, 0.0, seat_z), (0.06, 0.0, 1.0), 70.0 * scale,
                [(0.0, neck_a, neck_b), (1.0, neck_a * 0.95, neck_b * 0.95)],
                scrubbable=False, arc=(-math.pi, math.pi))
    _part("head", top, (0.05, 0.0, 1.0), 225.0 * scale,
          [(0.0, neck_a * 1.05, neck_b * 1.05), (0.28, head_a, head_b),
           (0.75, head_a, head_b), (1.0, head_a * 0.45, head_b * 0.45)],
          scrubbable=False, arc=(-math.pi, math.pi))

    for side, sgn in (("L", +1.0), ("R", -1.0)):
        sh_y = sgn * m["biacromial_mm"] / 2.0
        el = _part(f"upper_arm_{side}", (0.0, sh_y, seat_z),
                   _mirror(d["upper_arm"], sgn), m["upper_arm_len_mm"],
                   [(0.0, ua, ub), (1.0, ea, eb)])
        wr = _part(f"forearm_{side}", el, _mirror(d["forearm"], sgn),
                   m["forearm_len_mm"], [(0.0, fa, fb), (1.0, wra, wrb)])
        # Hands: obstacles, and the thing that makes an arm stop looking like a
        # tube. Never scrubbed -- the plan scrubs arms, not hands.
        _part(f"hand_{side}", wr, _mirror(d["forearm"], sgn), 105.0 * scale,
              [(0.0, wra, wrb * 1.15), (0.5, wra * 1.05, wrb * 1.25),
               (1.0, wra * 0.55, wrb * 0.7)], scrubbable=False,
              arc=(-math.pi, math.pi))

        # --- legs: obstacles. A seated person's thighs are exactly where an
        # arm reaching a resting forearm wants to be, so leaving them out of
        # the model makes that reach look free when it is not.
        hip = (0.0, sgn * m["biacromial_mm"] * 0.26, seat_z - m["torso_len_mm"])
        kn = _part(f"thigh_{side}", hip, _mirror(d["thigh"], sgn),
                   420.0 * scale, [(0.0, 92.0 * scale, 82.0 * scale),
                                   (1.0, 68.0 * scale, 62.0 * scale)],
                   scrubbable=False, arc=(-math.pi, math.pi))
        _part(f"shin_{side}", kn, _mirror(d["shin"], sgn), 400.0 * scale,
              [(0.0, 62.0 * scale, 58.0 * scale),
               (1.0, 42.0 * scale, 40.0 * scale)],
              scrubbable=False, arc=(-math.pi, math.pi))

    return body, meshes


def world_meshes(body, meshes):
    """-> {region name: (V_world, F)} using each region's current transform."""
    byname = {r.name: r.T for r in body.regions}
    out = {}
    # Same spurious-FPE suppression as bodymodel.Region.world, and for the same
    # reason: Accelerate flags (3,3)@(3,N) matmul even when the result is
    # exact. See the comment there for the measurement. Scoped to the loop so
    # it cannot mask arithmetic anywhere else in the module.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        for name, (V, F) in meshes.items():
            T = byname[name]
            out[name] = ((T[:3, :3] @ V.T).T + T[:3, 3], F)
    return out


if __name__ == "__main__":
    body, meshes = anatomical_body()
    print("anatomical body, swept superelliptical sections")
    tot_v = tot_f = 0
    for r in body.regions:
        V, F = meshes[r.name]
        tot_v += len(V); tot_f += len(F)
        tag = "scrub" if r.scrubbable.any() else "OBSTACLE"
        print(f"  {r.name:14s} {len(V):5d} verts {len(F):5d} tris  "
              f"{r.n:4d} cells  {tag}")
    print(f"  total mesh: {tot_v} verts, {tot_f} tris")

    # --- the measurements must come back out ------------------------------
    # A body whose arm is the wrong thickness is the one error that would
    # invalidate everything downstream, so check the tape measure closes.
    print("\n  circumference round-trip (built from a table, measured back):")
    for label, key, flat in (("upper arm", "upper_arm_circ_mm", ADULT["limb_flatten"]),
                             ("forearm", "forearm_circ_mm", ADULT["limb_flatten"]),
                             ("chest", "chest_circ_mm", ADULT["torso_flatten"])):
        n = ADULT["torso_n"] if label == "chest" else 2.0
        a, b = _semi_axes(ADULT[key], flat, n)
        t = np.linspace(0, 2 * math.pi, 2048, endpoint=False)
        x, y = _superellipse(t, a, b, n)
        p = np.c_[x, y]
        got = float(np.linalg.norm(np.diff(np.vstack([p, p[:1]]), axis=0),
                                   axis=1).sum())
        err = abs(got - ADULT[key])
        print(f"    {label:10s} target {ADULT[key]:6.1f}  got {got:6.1f}  "
              f"err {err:4.1f}mm  semi-axes a={a:5.1f} b={b:5.1f}")
        assert err < 1.0, f"{label} circumference is off by {err:.1f}mm"

    # --- normals: outward AND actually perpendicular to the surface -------
    #
    # Two separate properties, and testing only the first is what let a wrong
    # gradient through earlier. Note a normal is NOT parallel to the radial
    # direction on any non-circular section -- for an ellipse it is
    # (x/a^2, y/b^2), not (x, y) -- so demanding near-1 alignment with the
    # radius is simply the wrong test. The real check is perpendicularity to
    # the surface tangent.
    print("\n  normals:")
    for r in body.regions:
        radial = r.pts.copy(); radial[:, 2] = 0.0
        nz = r.nrm.copy(); nz[:, 2] = 0.0
        d = (radial * nz).sum(1) / (np.linalg.norm(radial, axis=1) + 1e-9)
        assert d.min() > 0.0, \
            f"{r.name}: normal points INTO the body (min {d.min():.3f})"

        # Perpendicular to the ANALYTIC tangent, not a chord between samples.
        #
        # A finite-difference tangent reads |n.t| ~= 0.08 here, which looks
        # like a broken gradient and is not: it is the chord across a coarse
        # angular step diverging from the true tangent, worst where a
        # superellipse's curvature spikes near its corners. The analytic
        # derivative settles it. For x = sign(c)|c|^(2/n)*a the tangent is
        #     dx/dt = a*(2/n)*|c|^(2/n-1)*(-sin t)
        #     dy/dt = b*(2/n)*|s|^(2/n-1)*( cos t)
        # and g.dP/dt reduces to -2cs + 2sc = 0 exactly, for any n.
        # Read from the region, not guessed from its name. Guessing worked
        # while everything was a limb or the torso; a full-wrap neck broke it,
        # and the failure looked like bad normals rather than a bad test.
        nn, (lo, hi) = r.n_exp, r.arc
        n_ax, n_th = 16, 28
        th = np.tile(np.linspace(lo, hi, n_th), n_ax)
        p = 2.0 / nn
        c, s = np.cos(th), np.sin(th)
        eps = 1e-12
        tx = p * np.abs(c) ** (p - 1.0) * (-s)
        ty = p * np.abs(s) ** (p - 1.0) * c
        # Scale by the local semi-axes, recovered from the point itself.
        ax = np.abs(r.pts[:, 0]) / np.maximum(np.abs(c) ** p, eps)
        by = np.abs(r.pts[:, 1]) / np.maximum(np.abs(s) ** p, eps)
        T = np.stack([tx * ax, ty * by, np.zeros_like(tx)], -1)
        T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), eps)
        dot = np.abs((T * r.nrm).sum(1))
        ok = np.isfinite(dot)
        print(f"    {r.name:14s} outward min {d.min():5.3f}   "
              f"analytic |n.t| worst {dot[ok].max():.2e}")
        assert dot[ok].max() < 1e-6, \
            f"{r.name}: normals are not perpendicular to the surface"

    # --- round trip against the measurement, which is the real check ------
    #
    # anatomy.py turns a circumference into a shape; girth.py turns a shape
    # back into a circumference. They should be inverses. Testing each alone
    # proves neither, because each could be self-consistently wrong.
    import girth as G
    # The rig, not a hypothetical one. Take fx from the capture's own stored
    # intrinsics rather than typing it, so the test cannot validate at a
    # sampling the camera does not deliver: z/fx at the measured 1050mm torso
    # standoff is 1.65mm per pixel. The earlier 0.9 came from a 1.35m standoff
    # that was never built, and it tested at 1.8x finer sampling than the rig.
    import frames as FRAME
    Z_TORSO_MM = 1050.0
    _meta = os.path.join(FRAME.DATA, "scan01", "meta.json")
    if os.path.exists(_meta):
        with open(_meta, encoding="utf-8") as fh:
            _intr = json.load(fh)["color_intrinsics"]
        _src = "scan01's stored intrinsics"
    else:
        _intr = {"fx": 637.6}          # measured, for a checkout with no data/
        _src = "the measured fx (data/ absent)"
    MM_PER_PX, WIDTH = FRAME.mm_per_px(_intr, Z_TORSO_MM), 420
    print(f"\n  sampling {MM_PER_PX:.3f}mm/px at {Z_TORSO_MM:.0f}mm, "
          f"from {_src}")
    print("\n  circumference round trip through the MEASUREMENT "
          "(render, then measure back):")
    rng = np.random.default_rng(11)
    worst_pct = 0.0
    for label, key in (("upper arm", "upper_arm_circ_mm"),
                       ("forearm", "forearm_circ_mm"),
                       ("elbow", "elbow_circ_mm"),
                       ("wrist", "wrist_circ_mm")):
        target = ADULT[key]
        a, b = _semi_axes(target, ADULT["limb_flatten"])
        errs = []
        for _ in range(25):
            m, d = G.render_section(a, b, Z_TORSO_MM, MM_PER_PX, WIDTH,
                                    noise_mm=0.30, edge_fatten_px=3, rng=rng)
            pr = G.profile_from_row(m, d, MM_PER_PX)
            f = G.fit_cross_section(*pr[:2], pr[2])
            if f:
                errs.append(f["circumference"] - target)
        e = np.array(errs)
        pct = 100.0 * np.abs(e).max() / target
        worst_pct = max(worst_pct, pct)
        print(f"    {label:10s} true {target:6.1f}mm   bias {e.mean():+5.2f}mm   "
              f"worst {np.abs(e).max():5.2f}mm  ({pct:.2f}%)")

    # 5.5% is a RESOLUTION limit, not slack, and it is the number the real
    # camera earns. At 1.65mm per pixel a wrist is 17 pixels across, so half
    # a pixel of silhouette quantisation is already 3% of its radius. The
    # half-pixel edge correction is applied in PIXELS before scaling
    # (girth.py:160), so it stays correct here -- what grows is the
    # quantisation it cannot remove.
    #
    # Read the BIAS column, not just the worst case. At 0.9mm/px it changed
    # sign between limbs, which is scatter. At the rig's real sampling every
    # limb reads THICK, by +6 to +9mm of circumference (about +1.5mm on the
    # semi-axis). That is a systematic error and it is in the safe direction:
    # a body modelled slightly thick spends clearance margin rather than
    # eating it, which is the same argument that justifies b = 0.86a.
    #
    # Going below this needs more pixels on the limb, not better fitting:
    # crop to the limb and run the segmentation on the crop, or take the
    # sub-pixel class boundary from Sapiens' logits instead of a binary mask.
    # Neither is needed for contact, because depth is found by torque.
    assert worst_pct < 5.5, "circumference round trip outside 5.5%"

    # --- a different person, no code change -------------------------------
    big, _ = anatomical_body(scale=1.25)
    thin, _ = anatomical_body(measurements=dict(upper_arm_circ_mm=240.0,
                                                forearm_circ_mm=220.0))
    print(f"\n  scrubbable area: default {body.total_area() / 100:6.0f} cm2   "
          f"+25% {big.total_area() / 100:6.0f} cm2   "
          f"thin arms {thin.total_area() / 100:6.0f} cm2")
    print("OK")
