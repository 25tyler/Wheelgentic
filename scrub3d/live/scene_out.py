"""scrub3d/live/scene_out.py -- live_body's scene, as numbers for a renderer.

WHAT THIS IS FOR
----------------
`live_body.py` already works out the whole picture from the depth camera:
how big the person actually is, how they are sitting right now, where their
chair is and how far it leans, and where each arm stands. It draws that as a
point cloud and a wireframe in Rerun.

The projector page wants to draw THE SAME SCENE as a cartoon -- a cartoon
person in a cartoon wheelchair with cartoon arms. Not its own tracking that
happens to look similar: the same numbers, a different skin. If this model
says the person is leaning 12 degrees forward, the cartoon leans 12 degrees
forward, because it is the same 12.

So this file turns `Live` into JSON. It measures nothing and decides
nothing. Every number here was computed by live_body; this only names them
and rounds them for a wire.

WHY A SEPARATE FILE RATHER THAN A METHOD ON Live
-------------------------------------------------
live_body.py is the measuring loop and it is Justin's. A serialiser bolted
into it would put a wire format in the middle of the thing that has to keep
working with no wire at all. This reads the object from outside, so
live_body can be edited without thinking about the page, and the page's
format can change without touching the measuring.

MEASUREMENTS, NOT GEOMETRY
---------------------------
The body goes over as THIRTEEN NUMBERS, not a mesh. The page rebuilds the
model from them with `anatomy.anatomical_body(measurements=...)`, which is
the same function live_body itself calls -- so both sides build the same
body from the same numbers rather than one shipping triangles to the other.
A posed body is 5,824 cells; thirteen floats is about 200 bytes, and
py/scrubbot.py's websocket rule is that it carries facts and never geometry.

EVERY NUMBER SAYS WHERE IT CAME FROM
-------------------------------------
`Measured.value()` blends a typical-adult prior in while there are few
samples, weighted by how many it has. That weight ships too, as
`confidence`, so a page can tell "measured off 200 frames of this person"
from "mostly still the prior". A dimension with no samples is reported as
the prior WITH confidence 0.0, never as a measurement.

The chair is the honest case worth reading. Its POSITION and LEAN are
measured from where the person sits. Its box SIZES are office-chair
constants inside `Body.place_chair` and nothing measures them, so they are
not exported as if they were measurements -- `chair.src` says "placed", not
"measured".
"""
import numpy as np


def _mm(v, nd=1):
    """A float for the wire, or None. Never NaN: JSON has no word for it."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else round(f, nd)


def _vec(p, nd=1):
    if p is None:
        return None
    a = np.asarray(p, float).ravel()
    if a.size < 3 or not np.all(np.isfinite(a[:3])):
        return None
    return [round(float(a[0]), nd), round(float(a[1]), nd),
            round(float(a[2]), nd)]


# live_body's dims() keys -> the names anatomy.anatomical_body(measurements=)
# takes. Both sides then build the same body from the same numbers; without
# this map the page would have to know live_body's internal vocabulary.
#
# Only the dimensions anatomical_body actually consumes are listed. A
# measurement live_body keeps for its own use but the model cannot take
# (hip_w, head_w, thigh_len, hand_len) is exported under its own name in
# `extra` rather than silently dropped -- a later model may want them.
DIM_TO_MODEL = {
    "shoulders":      "biacromial_mm",
    "upper_arm_len":  "upper_arm_len_mm",
    "forearm_len":    "forearm_len_mm",
    "torso_len":      "torso_len_mm",
}

# Circumference dimensions: live_body stores a WIDTH and the model wants a
# CIRCUMFERENCE, and the conversion is live_body's own circ_of() with its
# flatten constants. Converted at the source rather than on the page, so
# there is one definition of that arithmetic.
CIRC_TO_MODEL = {
    "upper_arm_w": "upper_arm_circ_mm",
    "forearm_w":   "forearm_circ_mm",
    "wrist_w":     "wrist_circ_mm",
    "chest_w":     "chest_circ_mm",
    "waist_w":     "waist_circ_mm",
}

EXTRA_DIMS = ("hip_w", "head_w", "thigh_len", "hand_len")


def body_measurements(D, circ_of=None, limb_p=None, torso_p=None,
                      limb_flatten=None, torso_flatten=None):
    """live_body's `dims()` dict -> measurements for anatomical_body.

    `D` is {name: Measured}. Returns {"mm": {...}, "confidence": {...},
    "n_frames": int, "src": "depth"|"prior"}.

    The circ_of arguments are live_body's own function and constants, passed
    in rather than imported, so this file does not depend on the shape of
    live_body's module-level names.
    """
    mm, conf = {}, {}
    total_n = 0

    for src_key, model_key in DIM_TO_MODEL.items():
        m = D.get(src_key)
        if m is None:
            continue
        mm[model_key] = _mm(m.value())
        # Measured.value() weights the median against the prior by
        # n/(n+PRIOR_WEIGHT). That same weight IS the confidence: at zero
        # samples it is 0.0 and the value is purely the prior.
        conf[model_key] = round(m.n / (m.n + 40.0), 3) if m.n else 0.0
        total_n = max(total_n, m.n)

    if circ_of is not None:
        for src_key, model_key in CIRC_TO_MODEL.items():
            m = D.get(src_key)
            if m is None:
                continue
            p = torso_p if "chest" in src_key or "waist" in src_key else limb_p
            f = torso_flatten if p is torso_p else limb_flatten
            try:
                mm[model_key] = _mm(circ_of(m.value(), p, f))
            except Exception:                                # noqa: BLE001
                continue
            conf[model_key] = round(m.n / (m.n + 40.0), 3) if m.n else 0.0
            total_n = max(total_n, m.n)

    extra = {}
    for k in EXTRA_DIMS:
        m = D.get(k)
        if m is not None:
            extra[k] = _mm(m.value())

    return {
        # "depth" only when something was actually sampled. With no samples
        # every value is the typical-adult prior, and calling that "depth"
        # would be the exact claim this file exists to avoid.
        "src": "depth" if total_n else "prior",
        "n_frames": int(total_n),
        "mm": mm,
        "confidence": conf,
        "extra": extra,
    }


def scene(live, arms=None):
    """The whole drawable scene from a live_body.Live. -> dict.

    Reads. Never mutates `live`, never raises: this rides a loop whose job
    is the arms, and a serialiser must not be what stops it.
    """
    out = {"ok": False}
    try:
        body = getattr(live, "body", None)
        if body is None:
            return out

        # --- the person, as measurements -------------------------------
        D = getattr(live, "D", None)
        if D:
            try:
                import live_body as _lb
            except ImportError:
                from . import live_body as _lb          # type: ignore
            out["body"] = body_measurements(
                D, circ_of=_lb.circ_of, limb_p=_lb.LIMB_P,
                torso_p=_lb.TORSO_P,
                limb_flatten=_lb.AN.ADULT["limb_flatten"],
                torso_flatten=_lb.AN.ADULT["torso_flatten"])

        # --- the person, as a pose -------------------------------------
        # World millimetres, the frame the floor fit defines: +x the way the
        # person faces, +z up. The page's own frame is metres and Y-up, and
        # THAT conversion belongs to the page, not here -- one side has to
        # own it and the page is where the axis convention lives.
        joints = getattr(body, "joints", None) or {}
        out["joints_mm"] = {k: _vec(v) for k, v in joints.items()
                            if _vec(v) is not None}

        lean = getattr(body, "fwd", None)
        if lean is not None:
            out["facing"] = _vec(lean, 4)

        # --- the chair --------------------------------------------------
        # PLACED, NOT MEASURED, and the key says so. place_chair() puts the
        # seat where the person's hips are and leans the back as they lean,
        # both from depth. The box SIZES are office-chair constants and
        # nothing measures them.
        butt = getattr(body, "butt", None)
        if butt is not None:
            out["chair"] = {
                "src": "placed",
                "seat_mm": _vec(butt),
                "sitting_height_mm": _mm(getattr(body, "sitting_height", None)),
            }

        # --- the arms ----------------------------------------------------
        # Where each base stands and what its joints read. Absent when no
        # arms are attached to this run, which is the normal case on a
        # machine that is only watching a person.
        if arms is not None:
            a_out = {}
            for name, a in getattr(arms, "by_name", {}).items():
                try:
                    a_out[name] = {
                        "base_mm": _vec(getattr(a, "base_mm", None)),
                        "joints": [_mm(v, 4) for v in (a.real_joints6() or [])]
                                  or None,
                        "src": "measured" if getattr(a, "real_joints6", None)
                               else "commanded",
                    }
                except Exception:                            # noqa: BLE001
                    continue
            if a_out:
                out["arms"] = a_out

        out["ok"] = True
        return out
    except Exception:                                        # noqa: BLE001
        return out


def _self_test():
    """No camera, no live_body: just the shapes and the honesty rules."""
    print("scene_out self-test")

    class FakeMeasured:
        def __init__(self, v, n):
            self._v, self.n = v, n

        def value(self):
            return self._v

    # Nothing sampled -> src is "prior", confidence 0, never "depth".
    D0 = {"shoulders": FakeMeasured(390.0, 0)}
    b0 = body_measurements(D0)
    assert b0["src"] == "prior", b0["src"]
    assert b0["confidence"]["biacromial_mm"] == 0.0
    print(f"  no samples -> src={b0['src']!r} confidence=0.0")

    # Sampled -> "depth", and confidence climbs with the sample count.
    D1 = {"shoulders": FakeMeasured(372.0, 200)}
    b1 = body_measurements(D1)
    assert b1["src"] == "depth" and b1["n_frames"] == 200
    assert b1["mm"]["biacromial_mm"] == 372.0
    assert b1["confidence"]["biacromial_mm"] > 0.8
    print(f"  200 samples -> src={b1['src']!r} "
          f"biacromial={b1['mm']['biacromial_mm']} "
          f"confidence={b1['confidence']['biacromial_mm']}")

    # NaN never reaches the wire.
    assert _mm(float("nan")) is None and _vec([1, float("inf"), 3]) is None
    print("  NaN and inf are dropped, not shipped")

    # A scene from an object with nothing on it is ok:False, not a crash.
    class Empty:
        pass
    assert scene(Empty())["ok"] is False
    print("  empty live object -> ok:false, no exception")

    print("OK")


if __name__ == "__main__":
    _self_test()
