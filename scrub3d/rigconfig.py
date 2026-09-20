"""scrub3d/rigconfig.py -- the rig as a file, not as a constant in four modules.

    from scrub3d import rigconfig
    rig = rigconfig.load()            # scrub3d/config.json
    layout = rig.layout()             # [4x4] per arm, world millimetres

WHY THIS EXISTS
---------------
"Put the arms anywhere" is the central claim of the design, and until now the
answer to "where are they?" was a literal list of four matrices repeated in
viz.py, control.py and partition.py's self-test. Three copies of a constant is
not a configurable rig; it is a rig with three places to forget.

So arm placement is a file. Everything downstream reads `T_world_base` and
nothing else, which is what makes tilting, inverting or relocating a mount cost
nothing but a number.

TAPE-MEASURE UNITS, NOT MATRICES
---------------------------------
The file stores x, y, z and a facing angle in degrees, because a person with a
drill measures those. The 4x4 is derived on load. A config nobody can fill in
from a tape measure gets filled in wrong.

THE FRAME THESE NUMBERS ARE IN
-------------------------------
World, as frames.py defines it: +Z up along the floor normal, +X the direction
the person faces, origin on the floor beneath them. So x is how far in front of
the person the mount sits, y is to their left, z is height above the floor.
"""
import json
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "config.json")

SCHEMA_VERSION = 1


class Rig:
    def __init__(self, d):
        self.d = d

    # --- arms --------------------------------------------------------------
    @property
    def arms(self):
        return self.d.get("arms", [])

    def layout(self):
        """-> [4x4] world poses, one per arm."""
        out = []
        for a in self.arms:
            yaw = math.radians(float(a["facing_deg"]))
            pitch = math.radians(float(a.get("pitch_deg", 0.0)))
            c, s = math.cos(yaw), math.sin(yaw)
            cp, sp = math.cos(pitch), math.sin(pitch)
            T = np.eye(4)
            T[:3, :3] = (np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
                         @ np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]]))
            T[:3, 3] = (float(a["x_mm"]), float(a["y_mm"]), float(a["z_mm"]))
            out.append(T)
        return out

    def serials(self):
        """USB serial per arm, so a port never has to be guessed by order."""
        return [a.get("serial_number") for a in self.arms]

    # --- thresholds --------------------------------------------------------
    def thresholds(self):
        return self.d.get("thresholds", {})

    def describe(self):
        print(f"  rig '{self.d.get('name', 'unnamed')}' "
              f"(schema {self.d.get('schema_version')})")
        print(f"    {self.d.get('provenance', 'no provenance recorded')}")
        for i, a in enumerate(self.arms):
            print(f"    arm {i}: x={a['x_mm']:+7.0f}  y={a['y_mm']:+7.0f}  "
                  f"z={a['z_mm']:+6.0f}  facing {a['facing_deg']:+7.1f} deg"
                  f"{'   ' + a['serial_number'] if a.get('serial_number') else ''}")


def default_dict():
    """A rig that at least loads, with the shipped hand-placed layout in it."""
    return {
        "schema_version": SCHEMA_VERSION,
        "name": "shipped hand-placed layout",
        "provenance": ("Hand-placed, never searched. Kept as the thing to "
                       "beat; run scrub3d/tools/plan_rig.py for a searched "
                       "one and paste its numbers here."),
        "arms": [
            {"id": "a0", "x_mm": 21, "y_mm": -500, "z_mm": 760,
             "facing_deg": 80.8, "serial_number": None},
            {"id": "a1", "x_mm": 472, "y_mm": -218, "z_mm": 760,
             "facing_deg": 155.2, "serial_number": None},
            {"id": "a2", "x_mm": 472, "y_mm": 218, "z_mm": 760,
             "facing_deg": -155.2, "serial_number": None},
            {"id": "a3", "x_mm": 21, "y_mm": 500, "z_mm": 760,
             "facing_deg": -130.8, "serial_number": None},
        ],
        "thresholds": {
            "d_hold_mm": 90.0, "d_estop_mm": 40.0, "d_body_mm": 60.0,
            "note": ("These mirror collide.py. Changing them here does not "
                     "change the code; they are recorded so a rig file "
                     "documents the safety margins it was planned under."),
        },
    }


def shipped_layout():
    """The hand-placed rig, as a fixture for self-tests. -> [4x4].

    Self-tests must NOT read config.json. A test whose fixture is a file the
    user is invited to edit stops being a test of the code and becomes a test
    of whatever is in that file today. So: config.json is the RUNTIME source
    and this is the FIXTURE, and there is exactly one of each rather than four
    copies of the same four matrices across four modules.
    """
    return Rig(default_dict()).layout()


def load(path=PATH):
    """-> Rig. Falls back to the shipped layout if there is no file yet."""
    if not os.path.exists(path):
        return Rig(default_dict())
    with open(path) as fh:
        d = json.load(fh)
    v = d.get("schema_version")
    if v != SCHEMA_VERSION:
        raise ValueError(
            f"{path} is schema {v}, this code speaks {SCHEMA_VERSION}. "
            f"A rig file with an unknown schema is not a rig file to guess at: "
            f"the numbers in it place four arms around a person.")
    return Rig(d)


def save(rig_dict, path=PATH):
    """Write it atomically, so an interrupted write cannot leave half a rig."""
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rig_dict, fh, indent=2)
    os.replace(tmp, path)
    return path


def from_layout(layout, name, provenance, serials=None):
    """A searched layout -> a config dict, in tape-measure units."""
    arms = []
    for i, T in enumerate(layout):
        T = np.asarray(T, float)
        arms.append({
            "id": f"a{i}",
            "x_mm": round(float(T[0, 3]), 1),
            "y_mm": round(float(T[1, 3]), 1),
            "z_mm": round(float(T[2, 3]), 1),
            "facing_deg": round(math.degrees(
                math.atan2(T[1, 0], T[0, 0])), 1),
            "serial_number": (serials[i] if serials and i < len(serials)
                              else None),
        })
    d = default_dict()
    d.update({"name": name, "provenance": provenance, "arms": arms})
    return d


if __name__ == "__main__":
    print("rig configuration")
    rig = load()
    rig.describe()

    lay = rig.layout()
    print(f"\n  -> {len(lay)} world poses")

    # A round trip has to be exact, or the file is not the source of truth.
    back = Rig(from_layout(lay, "round trip", "test")).layout()
    worst = max(float(np.abs(a - b).max()) for a, b in zip(lay, back))
    print(f"  layout -> config -> layout, worst element error {worst:.6f}")
    assert worst < 1e-3, "the rig file does not round-trip"

    # And an unknown schema must refuse rather than be interpreted.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "bad.json")
        with open(p, "w") as fh:
            json.dump({"schema_version": 99, "arms": []}, fh)
        try:
            load(p)
            raise SystemExit("a future schema was silently accepted")
        except ValueError as exc:
            print(f"  unknown schema refused: {str(exc).splitlines()[0]}")

    print("\n  arm placement is a file, read in one place. OK")
