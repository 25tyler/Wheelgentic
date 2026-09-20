"""tests/test_openyam_calib.py — the calibration file and the gate.

WHAT THIS PROVES: that py/openyam.py loads measured values over its five
CALIBRATE placeholders, that it REFUSES a file it cannot fully trust
rather than half-applying it, and that it refuses to construct against
real hardware while those values are still guesses.

WHY THE REFUSE-RATHER-THAN-HALF-APPLY CASES ARE THE POINT. A file with
real link lengths and placeholder joint limits is more dangerous than no
file at all: it reads as measured, it flips CALIBRATED to True, and that
switches off the only gate stopping an uncalibrated arm from moving near
a person. So every malformed field below must leave BOTH the values and
CALIBRATED untouched -- a loader that applied four of five fields and
warned about the fifth would pass a naive test and ship the hazard.

WHAT IT CANNOT PROVE: that any recorded value is CORRECT. Nothing here
touches a motor. A green run means the plumbing carries measurements
faithfully, not that the measurements were taken.
"""
import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import watchdog; watchdog.arm(60)

import damiao
import openyam
from damiao import MotorType

_fail = []


def check(name, cond, detail=""):
    if cond:
        print(f"       PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        print(f"       FAIL  {name}  [{detail}]")
        _fail.append(name)


TMP = tempfile.mkdtemp(prefix="openyam-calib-")

# Deliberately DIFFERENT from every placeholder in openyam.py, so a load
# that silently did nothing cannot pass by coincidence.
GOOD = {
    "measured": "2026-09-19",
    "joint_motors": ["DM8009", "DM8009", "DM6006",
                     "DM4310", "DM4310", "DM4310"],
    "joint_limits": [[-2.0, 2.0], [-1.5, 1.5], [-2.2, 2.2],
                     [-2.5, 2.5], [-1.7, 1.7], [-2.5, 2.5]],
    "link_lengths_mm": {"base": 142.0, "upper": 305.0, "fore": 288.0},
    "home_q": [0.0, 0.4, 0.4, -0.3, 0.0, 0.0],
    "joint_sign": [1, -1, -1, 1, 1, -1],
    "contact_tau": [3.0, 5.0, 3.5, 1.0, 0.8, 0.8],
    "estop_tau": [6.0, 9.0, 7.0, 2.5, 2.0, 2.0],
}


def write(doc, name="c.json"):
    p = os.path.join(TMP, name)
    with open(p, "w") as fh:
        json.dump(doc, fh)
    return p


def reset():
    """Put the module back on its placeholders between cases.

    Each case must start from a known-uncalibrated module or a later case
    would be reading the previous one's values and pass vacuously.
    """
    openyam.CALIBRATED = False
    openyam.JOINT_MOTORS = tuple([MotorType.DM4310] * 6)
    openyam.JOINT_LIMITS = ((-2.6, 2.6), (-1.8, 1.8), (-2.4, 2.4),
                            (-2.8, 2.8), (-1.9, 1.9), (-2.8, 2.8))
    openyam.L_BASE, openyam.L_UPPER, openyam.L_FORE = 130.0, 290.0, 300.0
    openyam.REACH_MAX = openyam.L_UPPER + openyam.L_FORE
    openyam.REACH_MIN = abs(openyam.L_UPPER - openyam.L_FORE)
    openyam.HOME_Q = (0.0, 0.437, 0.437, -0.349, 0.0, 0.0)
    openyam.JOINT_SIGN = (1, 1, 1, 1, 1, 1)


print("=== a good file replaces every placeholder ===")
reset()
check("loads", openyam._load_calibration(write(GOOD)))
check("CALIBRATED flips true", openyam.CALIBRATED)
check("motor models land",
      [m.name for m in openyam.JOINT_MOTORS] == GOOD["joint_motors"])
check("joint limits land",
      [list(t) for t in openyam.JOINT_LIMITS] == GOOD["joint_limits"])
check("link lengths land",
      (openyam.L_BASE, openyam.L_UPPER, openyam.L_FORE) == (142.0, 305.0,
                                                            288.0))
check("home pose lands", list(openyam.HOME_Q) == GOOD["home_q"])
check("joint signs land", list(openyam.JOINT_SIGN) == GOOD["joint_sign"])
check("torque ceilings land",
      list(openyam.CONTACT_TAU) == GOOD["contact_tau"])

# THE DERIVED VALUES. REACH_MAX/REACH_MIN are computed FROM the link
# lengths, so a loader that set the lengths without recomputing them would
# leave reachable() guarding the placeholder 590mm arm while _solve_ik
# solved for the real 593mm one -- accepting points whose acos goes out of
# domain, which is the exact NaN path reachable() exists to prevent.
print("\n=== derived reach is recomputed, not left stale ===")
check("REACH_MAX follows the measured links", openyam.REACH_MAX == 593.0,
      str(openyam.REACH_MAX))
check("REACH_MIN follows the measured links", openyam.REACH_MIN == 17.0,
      str(openyam.REACH_MIN))

print("\n=== a file it cannot fully trust is REFUSED WHOLE ===")
BAD = {
    "home pose outside its own limits":
        ("home_q", [0.0, 9.9, 0.4, -0.3, 0.0, 0.0]),
    "a joint limit with lo >= hi":
        ("joint_limits", [[2.0, -2.0]] + GOOD["joint_limits"][1:]),
    "a zero-length link":
        ("link_lengths_mm", {"base": 0.0, "upper": 305.0, "fore": 288.0}),
    "a joint sign that is not +-1":
        ("joint_sign", [1, 0, 1, 1, 1, 1]),
    "contact torque at or above the cutout":
        ("contact_tau", [99.0, 5.0, 3.5, 1.0, 0.8, 0.8]),
    "a vector that is not 6 long":
        ("home_q", [0.0, 0.4, 0.4]),
    "a motor model that does not exist":
        ("joint_motors", ["NOPE"] * 6),
}
for label, (key, value) in BAD.items():
    reset()
    doc = copy.deepcopy(GOOD)
    doc[key] = value
    ok = openyam._load_calibration(write(doc, "bad.json"))
    # BOTH halves matter. Returning False while having already mutated
    # some globals is the half-applied hazard this whole test exists for.
    intact = (openyam.L_UPPER == 290.0
              and openyam.JOINT_MOTORS[0] == MotorType.DM4310
              and openyam.HOME_Q == (0.0, 0.437, 0.437, -0.349, 0.0, 0.0))
    check(f"refused: {label}", (not ok) and (not openyam.CALIBRATED)
          and intact)

reset()
mal = os.path.join(TMP, "mal.json")
open(mal, "w").write("{not json")
check("refused: malformed json",
      not openyam._load_calibration(mal) and not openyam.CALIBRATED)
check("absent file is not an error, just uncalibrated",
      not openyam._load_calibration(os.path.join(TMP, "nope.json")))

print("\n=== the gate: uncalibrated hardware is REFUSED ===")
reset()
# dry_run is the rehearsal path and must keep working uncalibrated --
# otherwise nobody can practise before the arm exists, which is the whole
# point of having one.
a = openyam.OpenYamArm(dry_run=True)
check("dry run still constructs while uncalibrated", a.link_ok)
a.close()

gated = False
try:
    openyam.OpenYamArm(channel="can0")
except SystemExit as e:
    gated = "uncalibrated" in str(e).lower()
except Exception:
    pass
check("live construction refuses while uncalibrated", gated)

# The escape hatch exists for tools/calibrate-openyam.py, which obviously
# cannot require the calibration it produces. It must NOT be reachable by
# accident, so it is checked as an explicit keyword, not a default.
import inspect
sig = inspect.signature(openyam.OpenYamArm.__init__)
check("allow_uncalibrated defaults to False",
      sig.parameters["allow_uncalibrated"].default is False)

print("\n=== the register read that makes motor models measurable ===")
frame = damiao.pack_query_param(0x01, damiao.RID_TMAX)
check("query frame targets the read opcode 0x33", frame[2] == 0x33)
check("query frame carries the send id", frame[0] == 0x01)
check("query frame carries the RID", frame[3] == damiao.RID_TMAX)

import struct
reply = bytes([0x01, 0x00, 0x33, damiao.RID_PMAX]) + struct.pack("<f", 12.5)
got = damiao.unpack_query_param(reply)
check("a float register decodes", got is not None and abs(got[2] - 12.5) < 1e-6)
u = bytes([0x01, 0x00, 0x33, damiao.RID_NPP]) + struct.pack("<I", 14)
gotu = damiao.unpack_query_param(u)
check("a uint register decodes as uint, not float",
      gotu is not None and gotu[2] == 14, str(gotu))
check("a normal MIT feedback frame is not mistaken for a register reply",
      damiao.unpack_query_param(bytes([0, 1, 2, 3, 4, 5, 6, 7])) is None)

# The ambiguity must be REPORTED, not resolved by picking the first match.
# DM4340 and DM4340_48V carry identical limits, so the wire genuinely
# cannot separate them; returning one would manufacture a certainty.
check("an unambiguous motor identifies to exactly one model",
      damiao.identify_motor(12.5, 30, 10) == [MotorType.DM4310])
check("a genuinely ambiguous pair reports BOTH, not the first",
      len(damiao.identify_motor(12.5, 10, 28)) == 2)
check("a triple matching no known model identifies nothing",
      damiao.identify_motor(1.0, 1.0, 1.0) == [])

# ---------------------------------------------------------------------------
print("\n=== the IK and the FK describe the SAME arm ===")
# THE ONE GEOMETRY CLAIM THAT IS CHECKABLE WITH NO HARDWARE AND NO
# CALIBRATION. Every other number in this file is a placeholder, so nothing
# here can say a link length is right. But _solve_ik and cartesian_pose are
# two halves of the same chain, and whether they AGREE is pure arithmetic --
# true or false today, on a laptop, whatever the links turn out to be.
#
# They did not agree. _solve_ik used j2 = -(pi - elbow), the interior angle
# of the triangle rather than the joint's deviation from straight, so the
# pose it returned put the tool up to 588mm from the point it was asked for
# and 190mm away at (300,0,300). Nothing raised: set_target returned True and
# the arm would have gone somewhere else, confidently, with the sponge on a
# person. A round-trip check is the cheapest thing that catches it, and no
# test in this repo was making it.
#
# DO NOT relax this to a millimetre tolerance. Both halves are closed-form
# over the same constants, so agreement is exact to float rounding; a
# tolerance wide enough to be "safe" is wide enough to hide the next
# supplementary-angle mistake.
import math as _math


def _fk3(j0, j1, j2):
    """Forward kinematics of the positioning chain, mirroring
    OpenYamArm.cartesian_pose(). Kept here rather than calling that method so
    the check needs no instance, no bus and no CAN library."""
    r = openyam.L_UPPER * _math.cos(j1) + openyam.L_FORE * _math.cos(j1 + j2)
    s = openyam.L_UPPER * _math.sin(j1) + openyam.L_FORE * _math.sin(j1 + j2)
    return (r * _math.cos(j0), r * _math.sin(j0), s + openyam.L_BASE)


_worst, _solved, _refused = 0.0, 0, 0
for _i in range(11):
    _a0 = -1.0 + 0.2 * _i
    for _j in range(11):
        _a1 = 0.1 + 0.13 * _j
        for _k in range(11):
            _a2 = -2.0 + 0.18 * _k
            _p = _fk3(_a0, _a1, _a2)
            _sol = openyam._solve_ik(*_p)
            if _sol is None:
                _refused += 1
                continue
            _worst = max(_worst, _math.dist(_p, _fk3(*_sol)))
            _solved += 1

check("a solved pose lands on the point it was asked for", _worst < 1e-6,
      f"worst {_worst:.3e}mm over {_solved} poses ({_refused} refused)")
check("the sweep actually solved something", _solved > 500, str(_solved))

# REFUSING IS THE SAFE ANSWER AND MUST STAY AVAILABLE. A solver that
# returned a clamped best-effort for an out-of-reach point would hand
# float_to_uint a pose the arm cannot hold.
check("a point past full reach is refused, not clamped",
      openyam._solve_ik(2000.0, 0.0, 200.0) is None)
check("a NaN target is refused", openyam._solve_ik(float("nan"), 0.0, 200.0)
      is None)

print("\n" + "=" * 58)
if _fail:
    print(f"  *** {len(_fail)} FAILED: {_fail}")
    sys.exit(1)
print("  OPENYAM CALIBRATION LOAD + GATE PASSED")
print("  (no motor was moved; nothing here proves a value is CORRECT)")
print("=" * 58)
