"""tests/test_damiao.py — the OpenYAM wire protocol and safety envelope.

WHAT THIS PROVES: that py/damiao.py emits the same bytes two independent
open-source Damiao implementations emit, that out-of-range inputs
SATURATE rather than wrap, and that py/openyam.py's clamps, IK guard and
estop token behave as designed.

WHAT IT CANNOT PROVE: anything about a physical motor. No servo dynamics,
no gear direction, no real joint limits, no bus timing under load, no
brownout behaviour. A green run here means "the frames are right", not
"the arm works". First contact with hardware is still a human watching
the arm move.

The reference bytes below were transcribed from:
  enactic/openarm_can  src/openarm/damiao_motor/dm_motor_control.cpp:136-156
  cmjang/DM_Control_Python  DM_CAN.py:104-121
Both agree; where the Python one differs is its broken clamp, which we
deliberately do NOT copy (see test_out_of_range_saturates).
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py"))

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


print("=== MIT frame matches the reference bit-for-bit ===")


def _ref_pack(kp, kd, q, dq, tau, Q, DQ, TAU):
    """Independent transcription of the reference packing."""
    def f2u(x, mn, mx, bits):
        return int((x - mn) * ((1 << bits) - 1) / (mx - mn))
    kp_u, kd_u = f2u(kp, 0, 500, 12), f2u(kd, 0, 5, 12)
    q_u, dq_u, t_u = (f2u(q, -Q, Q, 16), f2u(dq, -DQ, DQ, 12),
                      f2u(tau, -TAU, TAU, 12))
    return bytes([(q_u >> 8) & 0xFF, q_u & 0xFF, (dq_u >> 4) & 0xFF,
                  ((dq_u & 0xF) << 4) | ((kp_u >> 8) & 0xF), kp_u & 0xFF,
                  (kd_u >> 4) & 0xFF,
                  ((kd_u & 0xF) << 4) | ((t_u >> 8) & 0xF), t_u & 0xFF])


MT = MotorType.DM4310
Q, DQ, TAU = damiao.MOTOR_LIMITS[MT]
for kp, kd, q, dq, tau in [(0, 0, 0, 0, 0), (40, 3, 0.5, 0, 0),
                           (65, 4.5, -1.2, 2.0, 0.5), (500, 5, Q, DQ, TAU),
                           (0, 0, -Q, -DQ, -TAU), (12, 0.2, 0.017, -0.5, 1.3)]:
    check(f"MIT kp={kp} kd={kd} q={q} dq={dq} tau={tau}",
          damiao.pack_mit(MT, kp, kd, q, dq, tau)
          == _ref_pack(kp, kd, q, dq, tau, Q, DQ, TAU))

print("\n=== out-of-range SATURATES (the reference library wraps here) ===")
# cmjang's LIMIT_MIN_MAX returns None and its caller ignores it, so an
# over-range value overflows np.uint16. On a torque field that is the
# difference between a capped push and a full-scale slam.
for q in (13.0, 99.0, -13.0, -99.0):
    u = damiao.float_to_uint(q, -Q, Q, 16)
    back = damiao.uint_to_float(u, -Q, Q, 16)
    check(f"q={q} saturates to the limit", abs(abs(back) - Q) < 1e-3,
          f"{back:+.3f} rad, cap {Q}")
for t in (11.0, 500.0):
    u = damiao.float_to_uint(t, -TAU, TAU, 12)
    check(f"tau={t} saturates", abs(damiao.uint_to_float(u, -TAU, TAU, 12)
                                    - TAU) < 1e-2)

print("\n=== round-trip accuracy ===")
for q in (0.0, math.pi / 2, -math.pi / 2, 3.0):
    u = damiao.float_to_uint(q, -Q, Q, 16)
    err = abs(damiao.uint_to_float(u, -Q, Q, 16) - q)
    check(f"q={q:+.4f} survives quantisation", err < 1e-3, f"err {err:.6f} rad")

print("\n=== control frames ===")
check("enable is FF*7 + FC", damiao.pack_command(damiao.CMD_ENABLE)
      == bytes([0xFF] * 7 + [0xFC]))
check("disable is FF*7 + FD", damiao.pack_command(damiao.CMD_DISABLE)
      == bytes([0xFF] * 7 + [0xFD]))
check("set-zero is FF*7 + FE", damiao.pack_command(damiao.CMD_SET_ZERO)
      == bytes([0xFF] * 7 + [0xFE]))

print("\n=== feedback decode ===")
fb = damiao.unpack_feedback(bytes([0x01, 0x80, 0x00, 0x80, 0x08, 0x00,
                                   42, 38]), MT)
check("id and error nibble split", fb["id"] == 1 and fb["err"] == 0)
check("temperatures read", fb["t_mos"] == 42 and fb["t_rotor"] == 38)
check("centre code decodes near zero", abs(fb["q"]) < 0.01, f"{fb['q']:+.4f}")
check("short frame returns None, does not raise",
      damiao.unpack_feedback(bytes([0, 1, 2]), MT) is None)
check("error nibble is described", "over" in
      damiao.describe_error(0xB).lower(), damiao.describe_error(0xB))

print("\n=== reachability guard (stops NaN reaching the wire) ===")
check("home pose is reachable", openyam.reachable(400, 0, 200))
check("far beyond reach rejected", not openyam.reachable(9999, 0, 0))
check("inside the dead zone rejected", not openyam.reachable(0, 0, openyam.L_BASE))
check("NaN rejected", not openyam.reachable(float("nan"), 0, 200))
check("inf rejected", not openyam.reachable(float("inf"), 0, 200))

print("\n=== IK never returns NaN ===")
sol = openyam._solve_ik(400, 0, 200)
check("a reachable point solves", sol is not None)
check("solution is finite", sol is not None and all(map(math.isfinite, sol)))
check("an unreachable point returns None, not NaN",
      openyam._solve_ik(9999, 0, 0) is None)
# Full-extension is where float error historically makes acos produce NaN.
edge = openyam._solve_ik(openyam.L_BASE + 0, openyam.REACH_MAX - 0.01, 0)
check("full-extension edge does not produce NaN",
      edge is None or all(map(math.isfinite, edge)))

print("\n=== workspace clamp + estop, against a dry arm ===")
a = openyam.OpenYamArm(dry_run=True)
check("dry arm reports a link", a.link_ok)
check("starts un-stopped", not a.estopped)

a.set_target(99999, 0, 200)
check("a wild target is clamped, not sent raw", a.clamped_hard)

a.set_target(400, 0, 200)
check("a sane target clears the hard-clamp flag", not a.clamped_hard)

check("estop latches", a.estop() and a.estopped)
check("estop raises abort_requested", a.abort_requested)
check("clear releases", a.clear_estop() and not a.estopped)
check("clear drops abort_requested", not a.abort_requested)

# The token race: a stop landing mid-clear must win.
a.estop()
tok_before = a._clear_token
a.estop()
check("a second estop invalidates an in-flight clear",
      a._clear_token > tok_before, f"{tok_before} -> {a._clear_token}")
a.clear_estop()

print("\n=== joint limits are enforced on direct commands ===")
a.set_joints([99, 99, 99, 99, 99, 99])
ok = all(openyam.JOINT_LIMITS[i][0] <= a.target[i] <= openyam.JOINT_LIMITS[i][1]
         for i in range(6))
check("out-of-limit joint command is clamped", ok, str(
    [round(v, 2) for v in a.target]))
a.close()

print("\n" + "=" * 58)
if _fail:
    print(f"  *** {len(_fail)} FAILED: {_fail}")
    sys.exit(1)
print("  OPENYAM WIRE PROTOCOL + SAFETY ENVELOPE PASSED")
print("  (frames verified against 2 references; NO motor was moved)")
print("=" * 58)
