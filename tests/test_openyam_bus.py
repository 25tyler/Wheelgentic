"""tests/test_openyam_bus.py — OpenYamArm's real send path, over a real bus.

WHAT THIS PROVES THAT test_damiao.py CANNOT. test_damiao.py calls
damiao.pack_mit directly and compares bytes. It never constructs a live
OpenYamArm, so it says nothing about whether the driver actually PUTS
those bytes on a bus: not the enable handshake, not the pump thread, not
the arbitration ids, not the joint ordering, not the sign convention, not
the estop's silence. Every one of those sits between pack_mit and the
wire, and every one of them was untested until this file.

HOW, WITH NO ADAPTER PLUGGED IN. python-can ships a 'virtual' bus: an
in-process broadcast channel that speaks the same Bus API as socketcan.
Two Bus objects on the same channel name see each other's frames, and
crucially a sender does NOT see its own -- so a second bus opened here
behaves like a listening motor, and a frame it receives really did travel
out through the driver's transport.

ONE PIPELINE. The arm under test is a plain OpenYamArm built through its
normal constructor. The ONLY thing different from hardware is the string
passed to python-can's interface= argument. _raw_send, _pump, _send_joint,
poll_feedback and the estop path all run byte for byte the code the real
arm runs. This file does not re-implement any of it; if it did, it would
be testing a copy and the copy would drift.

WHAT IT STILL CANNOT PROVE: anything physical. A virtual bus has no
servos, no arbitration, no bus load, no ack, no brownout, no gear
direction. Every frame "sends" successfully because nothing can nack it.
A green run here means "the driver emits the right frames in the right
order to the right ids", not "the arm works".
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py"))

try:
    import can
except ImportError:
    # SKIP, LOUDLY, RATHER THAN FAIL. python-can is in requirements.txt and
    # is installed on the GB10 (4.6.1 in ~/wg-venv), but the Mac venv that
    # runs most of this suite predates that line. Exiting 0 with a visible
    # reason keeps the suite green on a host that cannot run this without
    # ever letting the skip be mistaken for a pass -- the banner at the
    # bottom is the only thing that prints "PASSED".
    print("SKIP  tests/test_openyam_bus.py — python-can is not installed "
          "in this interpreter.")
    print("      This test is NOT passing here; it did not run.")
    print("      pip install python-can   (or use ~/wg-venv on the GB10)")
    sys.exit(0)

import damiao
import openyam

_fail = []


def check(name, cond, detail=""):
    if cond:
        print(f"       PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        print(f"       FAIL  {name}  [{detail}]")
        _fail.append(name)


def hexs(b):
    """Frame bytes as the space-separated hex candump prints, for eyeballing."""
    return " ".join(f"{x:02X}" for x in b)


def drain(bus, budget=0.40):
    """Collect every frame waiting on `bus` within a time budget.

    A TIME BUDGET, NOT A FRAME COUNT. The pump is a real thread on a real
    100Hz clock, so the number of frames in flight depends on scheduling.
    Waiting for "n frames" would hang forever the first time a bug stopped
    the pump -- exactly the bug this file exists to catch.
    """
    out = []
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        m = bus.recv(timeout=0.02)
        if m is None:
            continue
        out.append(m)
    return out


CH = "openyam-test"

# A listening peer on the same virtual channel. This is the motor's seat:
# everything it receives is a frame the driver really transmitted.
motor = can.interface.Bus(channel=CH, interface="virtual")

# The arm under test. allow_uncalibrated because config-openyam.json does
# not exist yet and cannot -- the values in it must be MEASURED off real
# hardware. That gate guards physical motion; there is no physical motion
# here, and skipping the gate is the difference between testing the send
# path today and not testing it until the arm is calibrated.
arm = openyam.OpenYamArm(channel=CH, interface="virtual",
                         allow_uncalibrated=True)

print("=== construction puts the enable handshake on the bus ===")
boot = drain(motor, 0.30)
check("frames reached the bus at all", len(boot) > 0, f"{len(boot)} frames")

enable = damiao.pack_command(damiao.CMD_ENABLE)
enables = [m for m in boot if bytes(m.data) == enable]
enable_ids = sorted({m.arbitration_id for m in enables})
check("an enable frame went to every joint",
      enable_ids == sorted(openyam.SEND_IDS),
      f"ids {[hex(i) for i in enable_ids]}")
check("the enable payload is FF*7 + FC",
      len(enables) > 0 and bytes(enables[0].data) == enable,
      hexs(enable))
check("enable frames are standard (11-bit) ids, not extended",
      all(not m.is_extended_id for m in enables))

print("\n=== link_ok is False, because no motor answered ===")
# THIS IS THE CORRECT RESULT AND IT MATTERS. A virtual bus has no servo
# to reply, so poll_feedback finds nothing and link_ok stays False. If
# this ever passes True, the driver is deciding the link is good from its
# own echo -- which on real hardware means a dead arm reporting healthy.
check("no feedback means no link", arm.link_ok is False)

print("\n=== the pump puts MIT frames on the bus, one per joint per tick ===")
arm.set_joints(list(openyam.HOME_Q))
time.sleep(0.05)
frames = drain(motor, 0.30)
mit = [m for m in frames if bytes(m.data) != enable]
check("the pump is transmitting", len(mit) > 0, f"{len(mit)} MIT frames")

ids = {m.arbitration_id for m in mit}
check("every joint is driven, and only the joints",
      ids == set(openyam.SEND_IDS),
      f"ids {sorted(hex(i) for i in ids)}")

print("\n=== the bytes on the wire ARE damiao.pack_mit's bytes ===")
# The point of the whole file. We recompute what pack_mit produces for the
# pose the driver believes it is holding, and demand the wire match it
# exactly. A mismatch means something between the two -- the sign, the
# gains table, the joint index, the argument order -- is wrong.
latest = {}
for m in mit:
    latest[m.arbitration_id] = bytes(m.data)

for i, sid in enumerate(openyam.SEND_IDS):
    got = latest.get(sid)
    want = damiao.pack_mit(openyam.JOINT_MOTORS[i],
                           openyam.TELEOP_KP[i], openyam.TELEOP_KD[i],
                           arm.q_cmd[i] * openyam.JOINT_SIGN[i], 0.0, 0.0)
    check(f"J{i} (id 0x{sid:02X}) wire bytes match pack_mit",
          got == want, f"wire {hexs(got) if got else 'NONE'} / "
                       f"want {hexs(want)}")

print("\n=== every MIT frame is 8 bytes, standard id ===")
check("all MIT frames are 8 bytes",
      all(len(m.data) == 8 for m in mit),
      f"lengths {sorted({len(m.data) for m in mit})}")
check("all MIT frames use standard ids",
      all(not m.is_extended_id for m in mit))

print("\n=== the gains on the wire are the teleop gains, decoded back ===")
# Decoding the kp/kd fields back out of the frame is a check pack_mit
# cannot do on itself: it confirms the driver picked the RIGHT ROW of the
# gains table for each joint, which a byte comparison against the same
# index would happily agree with if both were wrong.
for i, sid in enumerate(openyam.SEND_IDS):
    d = latest.get(sid)
    if d is None:
        continue
    kp_u = ((d[3] & 0x0F) << 8) | d[4]
    kd_u = (d[5] << 4) | (d[6] >> 4)
    kp = damiao.uint_to_float(kp_u, 0, damiao.KP_MAX, 12)
    kd = damiao.uint_to_float(kd_u, 0, damiao.KD_MAX, 12)
    check(f"J{i} kp/kd decode to the teleop gains",
          abs(kp - openyam.TELEOP_KP[i]) < 0.2
          and abs(kd - openyam.TELEOP_KD[i]) < 0.01,
          f"kp {kp:.2f} want {openyam.TELEOP_KP[i]}, "
          f"kd {kd:.3f} want {openyam.TELEOP_KD[i]}")

print("\n=== the slew limit is visible on the wire, not just in q_cmd ===")
# Command a big joint step and watch the DECODED position climb by no more
# than the per-tick cap. This is the rate limiter proven end to end: a
# regression that dropped the clamp would show here as one frame jumping
# straight to the goal.
arm.set_joints([0.0] * 6)
time.sleep(0.15)
drain(motor, 0.20)
goal = openyam.JOINT_LIMITS[0][1]          # J0 to its positive soft limit
arm.set_joints([goal, 0, 0, 0, 0, 0])
step_frames = [m for m in drain(motor, 0.30)
               if m.arbitration_id == openyam.SEND_IDS[0]]
p_max = damiao.MOTOR_LIMITS[openyam.JOINT_MOTORS[0]][0]
qs = []
for m in step_frames:
    d = bytes(m.data)
    qs.append(damiao.uint_to_float((d[0] << 8) | d[1], -p_max, p_max, 16))
lim = openyam.MAX_STEP_MM / openyam.REACH_MAX
# THE BUDGET IS THE CAP PLUS ONE QUANTISATION STEP, AND THAT IS NOT A
# FUDGE FACTOR. The position field is 16 bits across +-p_max, so the wire
# can only express multiples of 2*p_max/65535 = 0.000381 rad. The slew cap
# is 0.004068 rad, which is 10.66 of those steps -- so a tick that moves
# exactly the cap lands between two codes and float_to_uint rounds it to
# the 11th. Measured on the box: every delta is either 10 or 11 steps,
# never 12. Allowing exactly one step of rounding tests the driver;
# allowing a percentage would let a real regression hide behind it.
quant = 2 * p_max / ((1 << 16) - 1)
worst = max((abs(b - a) for a, b in zip(qs, qs[1:])), default=0.0)
check("J0 never jumps more than one slew step between frames",
      len(qs) > 2 and worst <= lim + quant,
      f"worst {worst:.6f} rad, cap {lim:.6f} + 1 code {quant:.6f}, "
      f"{len(qs)} frames")
check("J0 is actually moving toward the goal, not stuck",
      len(qs) > 2 and qs[-1] > qs[0], f"{qs[0]:+.4f} -> {qs[-1]:+.4f} rad")

print("\n=== estop puts disable on the bus, then SILENCE ===")
drain(motor, 0.20)
check("estop reports it transmitted", arm.estop())
after = drain(motor, 0.30)
disable = damiao.pack_command(damiao.CMD_DISABLE)
dis = [m for m in after if bytes(m.data) == disable]
dis_ids = sorted({m.arbitration_id for m in dis})
check("a disable frame went to every joint",
      dis_ids == sorted(openyam.SEND_IDS),
      f"ids {[hex(i) for i in dis_ids]}")
check("the disable payload is FF*7 + FD",
      len(dis) > 0 and bytes(dis[0].data) == disable, hexs(disable))

# THE ONE THAT MATTERS MOST. Damiao servos hold their last commanded
# state, so silence is what keeps a stopped arm still. If the pump keeps
# transmitting through an estop, the stop does nothing.
quiet = drain(motor, 0.35)
check("the pump sends NOTHING while stopped", len(quiet) == 0,
      f"{len(quiet)} frames leaked during the stop")

print("\n=== a target commanded during the stop still does not move it ===")
arm.set_joints([0.5] * 6)
still_quiet = drain(motor, 0.30)
check("commanding a stopped arm puts no frames on the bus",
      len(still_quiet) == 0, f"{len(still_quiet)} frames")

print("\n=== clear_estop re-enables and the pump resumes ===")
check("clear reports it transmitted", arm.clear_estop())
resumed = drain(motor, 0.30)
re_en = [m for m in resumed if bytes(m.data) == enable]
check("an enable frame went to every joint on clear",
      sorted({m.arbitration_id for m in re_en}) == sorted(openyam.SEND_IDS))
check("MIT frames are flowing again",
      len([m for m in resumed if bytes(m.data) not in (enable, disable)]) > 0)

print("\n=== close() disables the joints on the way out ===")
drain(motor, 0.20)
arm.close()
bye = drain(motor, 0.30)
check("close disables every joint",
      sorted({m.arbitration_id for m in bye
              if bytes(m.data) == disable}) == sorted(openyam.SEND_IDS))

motor.shutdown()

print("\n" + "=" * 58)
if _fail:
    print(f"  *** {len(_fail)} FAILED: {_fail}")
    sys.exit(1)
print("  OPENYAM SEND PATH PASSED, OVER A REAL python-can BUS")
print("  (virtual transport; NO adapter, NO servo, NO motion)")
print("=" * 58)
