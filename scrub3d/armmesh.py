"""scrub3d/armmesh.py -- the real RoArm-M2-S geometry, not a stick figure.

Loads Waveshare's own STL meshes and places each link in space from the joint
angles, so the operator view shows the actual machine.

LICENCE. The meshes come from `roarm_description`, whose `package.xml` declares
`<license>MIT</license>`. There is no repo-level LICENSE file, which is a gap
worth an email to the published maintainer address, but the package manifest is
the operative statement for the package that contains them. ~464 KB for all
five, so they are vendored here rather than downloaded at runtime -- the
project already learned that lesson about hostile venue wifi.

WHY THIS MATTERS BEYOND LOOKS
------------------------------
Two things fall out of having real geometry, and the second is the point.

1. The URDF's joint chain is an INDEPENDENT derivation of the kinematics. If
   chain_fk() and kinematics.fk() disagree, one of them is wrong, and until now
   nothing could tell. That check is in __main__ and it is not decorative.

2. Collision capsule radii can be MEASURED from the mesh instead of typed. The
   shipped values were guessed and two were too small -- R_BASE 45 against a
   49.3mm half-width, R_UPPER 35 against 39.5mm. An undersized capsule is an
   unflagged collision, i.e. wrong in the direction that hurts someone. A
   radius computed from the geometry cannot drift away from the geometry.

FRAMES
------
The URDF chain and our closed-form FK use different conventions, and the
vendor's own two repos disagree with each other on joint axis signs. The
mapping below is from Waveshare's ROS driver, which is the only place the
relationship is written down:

    base     = -urdf[base_link_to_link1]
    shoulder = -urdf[link1_to_link2]
    elbow    = +urdf[link2_to_link3]

Everything here is millimetres, matching the rest of scrub3d.
"""
import functools
import math
import os
import struct

import numpy as np

try:
    from . import kinematics as K
except ImportError:
    import kinematics as K

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "roarm")
URDF_PATH = os.path.join(ASSETS, "roarm_description.urdf")
# The RoArm's links. With SCRUB3D_ARM=openyam this stays empty: everything
# that reads it (the depth guard, the camera check, the rig editor) reasons
# about RoArm links. The OpenYAM is drawn from armmesh_openyam.py, which
# nothing but the view reads.
OPENYAM = os.environ.get("SCRUB3D_ARM", "roarm").lower() == "openyam"
LINKS = () if OPENYAM else ("base_link", "link1", "link2", "link3", "gripper_link")


def _rodrigues(axis, ang):
    """Rotation about an arbitrary unit axis. 4x4."""
    x, y, z = axis
    c, s, t = math.cos(ang), math.sin(ang), 1.0 - math.cos(ang)
    M = np.eye(4)
    M[:3, :3] = [
        [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
        [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
        [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]
    return M


def _parse_urdf(path=URDF_PATH):
    """Read the vendor's URDF. -> ordered list of joint dicts.

    PARSED, NOT TRANSCRIBED, and the difference already cost a degree of
    freedom. The previous version copied five joint ORIGINS into a dict by hand
    and silently dropped everything else in the file: the axes, the limits, and
    the fact that `link3_to_gripper_link` is `type="revolute"`. The gripper was
    therefore bolted on with a fixed transform and the arm modelled as 3-DOF.
    The vendor's own file says 4.

    Dropping the axes cost something too. Their signs had to be reconstructed
    from Waveshare's ROS driver and written into this module's docstring as a
    note about `-urdf[base_link_to_link1]`. They were in the file all along.

    Units: URDF is metres, scrub3d is millimetres. Converted here, once.
    """
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    out = []
    for j in root.findall("joint"):
        o = j.find("origin")
        raw_xyz = o.get("xyz", "0 0 0") if o is not None else "0 0 0"
        raw_rpy = o.get("rpy", "0 0 0") if o is not None else "0 0 0"
        ax = j.find("axis")
        lim = j.find("limit")

        def _f(el, key, default):
            if el is None or el.get(key) in (None, ""):
                return default
            return float(el.get(key))

        out.append({
            "name": j.get("name"),
            "type": j.get("type"),
            "parent": j.find("parent").get("link"),
            "child": j.find("child").get("link"),
            "xyz": [float(v) * 1000.0 for v in raw_xyz.split()],
            "rpy": [float(v) for v in raw_rpy.split()],
            "axis": ([float(v) for v in ax.get("xyz", "0 0 1").split()]
                     if ax is not None else [0.0, 0.0, 1.0]),
            "lower": _f(lim, "lower", None),
            "upper": _f(lim, "upper", None),
        })
    return out


_JOINTS = None


def joints():
    """The URDF's joints, in file order. Parsed once."""
    global _JOINTS
    if _JOINTS is None:
        _JOINTS = _parse_urdf()
    return _JOINTS


def movable():
    """The revolute joints, in chain order. Four on an M2-S.

    base yaw, shoulder, elbow, and the EoAT joint at link3_to_gripper_link.
    """
    return [j for j in joints() if j["type"] != "fixed"]


def urdf_limits():
    """-> [(lower, upper)] per movable joint, from the vendor's file.

    Worth comparing against kinematics.JOINT_LIMITS rather than assuming they
    agree. Waveshare's two repos disagree with each other on the elbow upper
    bound -- 3.1416 here, 2.95 in roarm_ws -- and on the axis signs of the
    first two joints. This is the em0 variant, the one the IKFast plugin and
    the serial driver were generated against.
    """
    return [(j["lower"], j["upper"]) for j in movable()]


def _rpy(r, p, y):
    cr, sr, cp, sp, cy, sy = (math.cos(r), math.sin(r), math.cos(p),
                              math.sin(p), math.cos(y), math.sin(y))
    Rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    Ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _T(xyz, rpy):
    M = np.eye(4)
    M[:3, :3] = _rpy(*rpy)
    M[:3, 3] = xyz
    return M


def _Rz(a):
    M = np.eye(4)
    c, s = math.cos(a), math.sin(a)
    M[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    return M


def load_stl(name, folder=ASSETS):
    """-> (V, F) with V in millimetres. Binary STL only, which is what ships."""
    path = os.path.join(folder, f"{name}.stl")
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 84:
        raise ValueError(f"{path}: too short to be an STL")
    n = struct.unpack("<I", data[80:84])[0]
    if 84 + n * 50 != len(data):
        raise ValueError(f"{path}: not a binary STL, or truncated "
                         f"(header says {n} triangles)")
    tri = np.frombuffer(data, offset=84, count=n,
                        dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]))
    V = tri["v"].reshape(-1, 3).astype(np.float64) * 1000.0    # m -> mm
    F = np.arange(len(V), dtype=np.int32).reshape(-1, 3)
    return V, F


_CACHE = {}


def meshes():
    """All five link meshes, loaded once. -> {name: (V, F)}"""
    if not _CACHE:
        for n in LINKS:
            _CACHE[n] = load_stl(n)
    return _CACHE


def link_transforms(j0, j1, j2, j3=0.0, T_world_base=None):
    """Joint angles -> {link name: 4x4 placing that link's mesh in the world}.

    FOUR angles, because the arm has four joints. j3 is the EoAT joint at
    `link3_to_gripper_link`, which the URDF declares revolute over 0 to 1.5 rad
    and which this module used to attach with a fixed transform.

    The walk is generic over whatever the file contains: each child sits at its
    parent's frame, times the joint origin, times a rotation about that joint's
    OWN axis. Nothing here knows that the axes happen to be plus or minus Z, so
    a vendor file with a different convention would still place correctly.

    WHAT j3 DOES AND DOES NOT MOVE, because it is not what you would guess.
    `hand_tcp` is parented to **link3, not to gripper_link** -- it is a sibling
    of the jaw, not a child of it. So driving j3 does NOT move the tool centre
    point, which is why the 3-DOF closed-form FK in kinematics.py was right all
    along and still agrees to 1e-6 mm. What j3 moves is the physical jaw, and
    that is a body which sweeps through space next to a person.
    """
    W = np.eye(4) if T_world_base is None else np.asarray(T_world_base, float)
    angles, out = [j0, j1, j2, j3], {}
    origins, index = _chain()
    for j in joints():
        parent = out.get(j["parent"])
        if parent is None:
            parent = W.copy()
            out[j["parent"]] = parent
        M = parent @ origins[j["name"]]
        if j["type"] != "fixed":
            M = M @ _rodrigues(j["axis"], float(angles[index[j["name"]]]))
        out[j["child"]] = M
    return out


_CHAIN = None


def _chain():
    """-> ({joint: its fixed origin transform}, {movable joint: its angle's
    index}). The file does not change while running, so both are built once:
    rebuilding them per call was most of the time the collision layer spent."""
    global _CHAIN
    if _CHAIN is None:
        _CHAIN = ({j["name"]: _T(j["xyz"], j["rpy"]) for j in joints()},
                  {m["name"]: k for k, m in enumerate(movable())})
    return _CHAIN


def posed(j0, j1, j2, j3=0.0, T_world_base=None):
    """-> {link name: (V_world, F)}. Ready to hand to a renderer."""
    tf = link_transforms(j0, j1, j2, j3, T_world_base)
    out = {}
    for name, (V, F) in meshes().items():
        T = tf[name]
        out[name] = ((T[:3, :3] @ V.T).T + T[:3, 3], F)
    return out


def eoat_pivot(j0, j1, j2, T_world_base=None):
    """Where the EoAT joint sits. -> (3,) position, base frame unless posed.

    The fourth joint's axis passes through here. It is a function of the first
    three joints only, because gripper_link hangs off link3.
    """
    if T_world_base is None:
        return np.array(_pivot_in_base(float(j0), float(j1), float(j2)))
    return link_transforms(j0, j1, j2, 0.0, T_world_base)["gripper_link"][:3, 3]


def eoat_pivot_many(J):
    """eoat_pivot for many poses at once. (N, 3) joints -> (N, 3), base frame."""
    J = np.asarray(J, float).reshape(-1, 3)
    n = len(J)
    origins, index = _chain()
    frames = {}
    for j in joints():
        parent = frames.get(j["parent"])
        if parent is None:
            parent = np.broadcast_to(np.eye(4), (n, 4, 4))
            frames[j["parent"]] = parent
        M = parent @ origins[j["name"]]
        if j["type"] != "fixed":
            k = index[j["name"]]
            M = M @ _rodrigues_many(j["axis"], J[:, k] if k < 3 else np.zeros(n))
        frames[j["child"]] = M
    return frames["gripper_link"][:, :3, 3].copy()


def _rodrigues_many(axis, ang):
    """_rodrigues for an array of angles. -> (N, 4, 4)"""
    x, y, z = axis
    c, s = np.cos(ang), np.sin(ang)
    t = 1.0 - c
    M = np.zeros((len(ang), 4, 4))
    M[:, 0, 0] = t * x * x + c
    M[:, 0, 1] = t * x * y - s * z
    M[:, 0, 2] = t * x * z + s * y
    M[:, 1, 0] = t * x * y + s * z
    M[:, 1, 1] = t * y * y + c
    M[:, 1, 2] = t * y * z - s * x
    M[:, 2, 0] = t * x * z - s * y
    M[:, 2, 1] = t * y * z + s * x
    M[:, 2, 2] = t * z * z + c
    M[:, 3, 3] = 1.0
    return M


@functools.lru_cache(maxsize=8192)
def _pivot_in_base(j0, j1, j2):
    """eoat_pivot in the base frame, remembered: the collision layer asks for
    the same poses over and over within a frame."""
    return tuple(float(v) for v in link_transforms(j0, j1, j2)["gripper_link"][:3, 3])


def eoat_radius(percentile=100.0):
    """A sphere at the EoAT pivot that contains the jaw at EVERY j3. -> mm.

    The jaw is a RIGID body rotating about that pivot, so every vertex keeps a
    constant distance from it no matter what the fourth joint is doing. One
    sphere therefore bounds the jaw over the whole 0 to 1.5 rad range, and the
    collision layer never has to know j3 -- which is just as well, because
    nothing reads the fourth servo today.

    The maximum, not a percentile, by default. This is the term that was
    missing entirely; there is no case for shaving it.
    """
    tf = link_transforms(0.0, 0.0, 0.0, 0.0)
    T = tf["gripper_link"]
    V, _ = meshes()["gripper_link"]
    Vw = (T[:3, :3] @ V.T).T + T[:3, 3]
    return float(np.percentile(np.linalg.norm(Vw - T[:3, 3], axis=1), percentile))


# --- capsule radii, measured rather than typed ------------------------------

# Which mesh belongs to which collision capsule. link1 is small and sits inside
# the base column, so it is folded in there rather than given its own capsule.
CAPSULE_MESHES = {
    "base": ("base_link", "link1"),
    "upper": ("link2",),
    "fore": ("link3",),
}


def measured_radii(percentile=99.0):
    """Capsule radii from the actual meshes. -> {"base","upper","fore"}

    For each capsule, take its mesh vertices in the frame where that capsule's
    axis lies along a known direction, and measure the perpendicular distance
    from the axis. Use a high percentile rather than the maximum so a single
    stray mounting lug does not inflate every radius -- but a HIGH one, because
    this is a safety margin and the failure direction is undersizing.
    """
    tf = link_transforms(0.0, 0.0, 0.0, 0.0)
    out = {}
    for cap, names in CAPSULE_MESHES.items():
        pts = []
        for nm in names:
            V, _ = meshes()[nm]
            T = tf[nm]
            pts.append((T[:3, :3] @ V.T).T + T[:3, 3])
        P = np.vstack(pts)

        # Capsule axis endpoints in the world, at the zero pose.
        base, shoulder, elbow, tcp = K.link_points(0.0, 0.0, 0.0)
        seg = {"base": (base, shoulder), "upper": (shoulder, elbow),
               "fore": (elbow, tcp)}[cap]
        a, b = np.array(seg[0], float), np.array(seg[1], float)
        d = b - a
        L2 = float(d @ d)
        if L2 < 1e-9:
            out[cap] = 0.0
            continue
        t = np.clip((P - a) @ d / L2, 0.0, 1.0)
        perp = np.linalg.norm(P - (a + t[:, None] * d), axis=1)
        out[cap] = float(np.percentile(perp, percentile))
    return out


if __name__ == "__main__":
    print("RoArm-M2-S mesh geometry")
    for n, (V, F) in meshes().items():
        lo, hi = V.min(0), V.max(0)
        print(f"  {n:14s} {len(F):5d} tris  extent "
              f"{hi[0] - lo[0]:6.1f} x {hi[1] - lo[1]:6.1f} x {hi[2] - lo[2]:6.1f} mm")

    # --- the check that matters: two independent kinematics must agree ------
    print("\n  URDF joint chain vs closed-form FK (independent derivations):")
    worst = 0.0
    rng = np.random.default_rng(0)
    for _ in range(400):
        j0 = rng.uniform(K.BASE_MIN_RAD, K.BASE_MAX_RAD)
        j1 = rng.uniform(K.SHOULDER_MIN_RAD, K.SHOULDER_MAX_RAD)
        j2 = rng.uniform(K.ELBOW_MIN_RAD, K.ELBOW_MAX_RAD)
        j3 = rng.uniform(*urdf_limits()[3])
        chain = link_transforms(j0, j1, j2, j3)["hand_tcp"][:3, 3]
        closed = np.array([float(v) for v in K.fk(j0, j1, j2)])
        worst = max(worst, float(np.linalg.norm(chain - closed)))
    print(f"    worst disagreement over 400 random poses: {worst:.6f} mm")
    print(f"    j3 was swept over its full URDF range in that test and changed")
    print(f"    nothing, which is the point: hand_tcp hangs off link3, not "
          f"off the jaw.")
    assert worst < 1e-6, (
        "the URDF chain and the closed-form FK disagree. One is wrong, and "
        "every collision capsule depends on whichever it is.")

    # --- capsule radii, measured --------------------------------------------
    print("\n  capsule radii MEASURED from mesh (99th pct perpendicular):")
    r = measured_radii()
    try:
        from . import collide as C
    except ImportError:
        import collide as C
    shipped = {"base": C.R_BASE, "upper": C.R_UPPER, "fore": C.R_FORE}
    for k in ("base", "upper", "fore"):
        flag = "UNDERSIZED" if shipped[k] < r[k] - 0.5 else "ok"
        print(f"    {k:6s} measured {r[k]:6.1f}   shipped {shipped[k]:5.1f}   {flag}")
    print("\n  an undersized capsule is an unflagged collision; adopt the "
          "measured values")
    print("OK")

# With SCRUB3D_ARM=openyam the RoArm URDF chain says nothing about where the
# jaw is. Raising here sends collide.py down its documented fallback: the EoAT
# sphere sits at the tool point, which is the conservative reading.
if OPENYAM:
    # The OpenYAM gripper: its body sits behind the grasp frame, which is
    # 100 mm out from the gripper origin along the tool axis. The jaw sphere
    # (collide.R_EOAT, 69 mm) is centred there. Centring it at the tool point
    # instead put it inside every limb at sponge standoff, and the planner
    # could reach nothing, from anywhere.
    GRIPPER_BACK_MM = 100.0

    def eoat_pivot(j0, j1, j2, T_world_base=None):
        pts = np.asarray(K.link_points(j0, j1, j2), float)
        elbow, tcp = pts[2], pts[3]
        d = tcp - elbow
        n = float(np.linalg.norm(d))
        p = tcp - d / n * GRIPPER_BACK_MM if n > 1e-9 else tcp
        if T_world_base is not None:
            T = np.asarray(T_world_base, float)
            p = T[:3, :3] @ p + T[:3, 3]
        return p

    def eoat_pivot_many(J):
        P = K.link_points_many(J)
        elbow, tcp = P[:, 2], P[:, 3]
        d = tcp - elbow
        n = np.linalg.norm(d, axis=1, keepdims=True)
        n[n < 1e-9] = 1.0
        return tcp - d / n * GRIPPER_BACK_MM
