"""scrub3d/armmesh_openyam.py -- the Anvil OpenYAM as it is built, for the view.

    python scrub3d/armmesh_openyam.py        # self-test against the kinematics

The live view used to draw an OpenYAM as its sponge alone: a ball in the air.
This places the arm's own meshes from its joint angles, all six of them, so
the view shows the machine that is in the room.

FOR THE VIEW ONLY
-----------------
armmesh.py stays empty under SCRUB3D_ARM=openyam on purpose. The depth guard,
the camera check and the rig editor all read armmesh, and they would start
reasoning about links placed from the three-joint model of a six-joint arm.
Nothing that decides whether an arm may move reads this module.

THE FILES, AND THEIR LICENCE
----------------------------
assets/openyam/ is dimOS's dual_openyam package, copied whole and unchanged:
the URDF, nine STL meshes (7 MB), SOURCE.md and both licences. The arm model
is i2rt's YAM (MIT, I2RT_YAM_LICENSE); only the 0.62 m spacing between the two
bases comes from Amazon's ABC (Apache-2.0), and that spacing is not used here.
The URDF holds two identical arms. The left one is read; each rig entry places
its own copy.

FRAMES AND JOINTS
-----------------
An arm's base frame is the URDF's `left_base` link: the yaw axis through the
origin, +z up, +x the way the arm reaches at home. That is the frame
kinematics_openyam.py measures in and the frame the rig file places.

The joints are the URDF's joint1 to joint6, radians, in that order, which is
the order dimOS reports them in. Three angles are taken as the first three
with the wrist at zero: exactly the arm kinematics_openyam.py models, so the
drawn grasp frame lands on the modelled tool point.

Everything is millimetres, as in the rest of scrub3d.
"""
import os

import numpy as np

try:
    from . import armmesh as _A
except ImportError:
    import armmesh as _A

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "openyam")
URDF_PATH = os.path.join(ASSETS, "dual_openyam.urdf")
SIDE = "left_"                   # the arm that is read; the right one is its copy
BASE = "base"
TOOL = "grasp_frame"             # where kinematics_openyam puts the tool point
N_JOINTS = 6

# What each link is drawn as. The arm's own colour goes on the big structural
# links, so red and blue still read from across the room; the joints and the
# gripper are the dark anodised grey of the real arm.
BODY_LINKS = ("base", "link2", "link3")
GRAPHITE = (58, 60, 66)
FINGER = (196, 200, 208)


def _strip(name):
    return name[len(SIDE):] if name.startswith(SIDE) else name


def _parse(path=URDF_PATH):
    """-> (joints of the one arm, parent before child; {link: (mesh file,
    4x4 placing the mesh in its link's frame)}). Metres become mm here."""
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()

    def origin(el):
        o = el.find("origin") if el is not None else None
        xyz = [float(v) * 1000.0 for v in (o.get("xyz", "0 0 0") if o is not None
                                           else "0 0 0").split()]
        rpy = [float(v) for v in (o.get("rpy", "0 0 0") if o is not None
                                  else "0 0 0").split()]
        return _A._T(xyz, rpy)

    joints = []
    for j in root.findall("joint"):
        parent, child = j.find("parent").get("link"), j.find("child").get("link")
        if not (parent.startswith(SIDE) and child.startswith(SIDE)):
            continue                          # the shared root, and the right arm
        ax = j.find("axis")
        axis = np.array([float(v) for v in ax.get("xyz").split()]) if ax is not None \
            else np.array([0.0, 0.0, 1.0])
        joints.append({"name": _strip(j.get("name")), "type": j.get("type"),
                       "parent": _strip(parent), "child": _strip(child),
                       "origin": origin(j), "axis": axis / np.linalg.norm(axis)})
    visuals = {}
    for ln in root.findall("link"):
        if not ln.get("name").startswith(SIDE):
            continue
        vis = ln.find("visual")
        if vis is None:
            continue
        mesh = vis.find("geometry").find("mesh").get("filename")
        visuals[_strip(ln.get("name"))] = (os.path.basename(mesh), origin(vis))
    return joints, visuals


_MODEL = None


def _model():
    """The parsed URDF, once: (joints, visuals, {movable joint: its index})."""
    global _MODEL
    if _MODEL is None:
        joints, visuals = _parse()
        movable = [j["name"] for j in joints if j["type"] != "fixed"]
        assert len(movable) == N_JOINTS, movable
        _MODEL = (joints, visuals, {n: k for k, n in enumerate(movable)})
    return _MODEL


def links():
    """The links that have a mesh, base first."""
    joints, visuals, _ = _model()
    order = [BASE] + [j["child"] for j in joints]
    return tuple(n for n in order if n in visuals)


def colour(link, arm_colour):
    """What a link is drawn in, given its arm's colour. -> (r, g, b)"""
    if link in BODY_LINKS:
        return tuple(int(v) for v in arm_colour)
    return FINGER if link.startswith("tip_") else GRAPHITE


_CACHE = {}


def meshes():
    """{link: (V, F, N)}: vertices in the LINK's frame (the URDF's visual
    origin is already applied), mm; triangles; one normal per vertex, the
    face's own, so the flat CAD faces are lit as flat faces."""
    if not _CACHE:
        _, visuals, _ = _model()
        for name in links():
            fname, T = visuals[name]
            V, F = _A.load_stl(os.path.splitext(fname)[0],
                               folder=os.path.join(ASSETS, "assets"))
            V = (T[:3, :3] @ V.T).T + T[:3, 3]
            tri = V.reshape(-1, 3, 3)
            n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
            _CACHE[name] = (V.astype(np.float32), F,
                            np.repeat(n, 3, axis=0).astype(np.float32))
    return _CACHE


def full_joints(q):
    """Three angles (scrub3d's model of the arm) or six (dimOS's) -> six."""
    q = [float(v) for v in q]
    if len(q) < N_JOINTS:
        q = q[:3] + [0.0] * (N_JOINTS - 3)
    return q[:N_JOINTS]


def link_transforms(q, T_world_base=None):
    """Joint angles -> {link: 4x4 placing that link's frame in the world},
    every link and the grasp frame. `q`: three angles or six."""
    q = full_joints(q)
    joints, _, index = _model()
    W = np.eye(4) if T_world_base is None else np.asarray(T_world_base, float)
    out = {BASE: W}
    for j in joints:
        M = out[j["parent"]] @ j["origin"]
        if j["type"] != "fixed":
            M = M @ _A._rodrigues(j["axis"], q[index[j["name"]]])
        out[j["child"]] = M
    return out


def tool_point(q, T_world_base=None):
    """Where the grasp frame is. -> (3,) mm"""
    return link_transforms(q, T_world_base)[TOOL][:3, 3].copy()


def _self_test():
    import math
    try:
        from . import kinematics_openyam as KY
    except ImportError:
        import kinematics_openyam as KY
    print("OpenYAM mesh geometry")
    for n, (V, F, _N) in meshes().items():
        lo, hi = V.min(0), V.max(0)
        print(f"  {n:10s} {len(F):6d} tris  extent "
              f"{hi[0] - lo[0]:6.1f} x {hi[1] - lo[1]:6.1f} x {hi[2] - lo[2]:6.1f} mm")
    lo = meshes()[BASE][0][:, 2].min()
    print(f"  the base mesh starts {lo:.1f} mm above its frame (the plank top)")

    # pinocchio's forward kinematics of this URDF, as kinematics_openyam
    # records it: the chain walk here has to land on the same points.
    cases = {(0.0, 1.047, 1.047): (342.6, 0.0, 402.1),
             (0.0, 1.5708, 0.0): (80.0, 0.0, -77.1),
             (0.0, 0.0, 1.5708): (-304.0, 0.0, 568.1),
             (1.5708, 1.047, 1.047): (0.0, 342.6, 402.1)}
    worst = max(float(np.linalg.norm(tool_point(j) - np.array(p)))
                for j, p in cases.items())
    print(f"  against pinocchio on four poses: within {worst:.2f} mm")
    assert worst < 0.5, "the URDF walk does not land where pinocchio does"

    # And the three-joint model the governor plans with: the drawn gripper
    # must sit on the tool point the rest of scrub3d believes in.
    rng = np.random.default_rng(0)
    worst = 0.0
    for _ in range(400):
        j = [rng.uniform(lo, hi) for lo, hi in KY.JOINT_LIMITS]
        worst = max(worst, float(np.linalg.norm(tool_point(j) - np.array(KY.fk(*j)))))
    print(f"  against kinematics_openyam over 400 poses: within {worst:.2f} mm")
    assert worst < 3.0, "the drawn arm and the planned arm disagree"

    # A wrist that turns moves the tool: six joints are not three.
    moved = float(np.linalg.norm(tool_point([0, 1.047, 1.047, 0.8, 0, 0])
                                 - tool_point([0, 1.047, 1.047])))
    assert moved > 50.0, moved
    print(f"  wrist pitch of 0.8 rad moves the tool {moved:.0f} mm: the wrist is drawn")
    T = np.eye(4)
    T[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
    T[:3, 3] = (100.0, 200.0, 300.0)
    assert np.allclose(tool_point((0, 1.047, 1.047), T),
                       T[:3, :3] @ tool_point((0, 1.047, 1.047)) + T[:3, 3])
    print(f"  {len(links())} links: {', '.join(links())}")
    print(f"  reach at home {math.hypot(*tool_point((0, 1.047, 1.047))[[0, 2]]):.0f} mm")
    print("  OpenYAM meshes: OK")


if __name__ == "__main__":
    _self_test()
