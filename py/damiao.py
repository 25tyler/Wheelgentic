"""py/damiao.py — Damiao CAN wire protocol for the Anvil OpenYAM.

WHY THIS FILE EXISTS: the OpenYAM is a 6-DOF arm whose joints are Damiao
servos on a 1 Mbit/s CAN bus. There is no ESP32 solving IK for us the way
the RoArm-M2-S did -- we command each joint directly, so the host owns
every guarantee the firmware used to provide.

PROTOCOL PROVENANCE. Every wire constant below was read from TWO
independent open-source implementations and cross-checked byte for byte:

  * cmjang/DM_Control_Python  -- DM_CAN.py (Python, 624 lines)
  * enactic/openarm_can       -- src/openarm/damiao_motor/dm_motor_control.cpp
                                 (C++, Apache-2.0, Anvil's sibling arm)

They agree on the MIT frame layout, on 0xFC/0xFD/0xFE, and on the
kp/kd quantisation ranges. Where they DISAGREE is recorded inline --
see MOTOR_LIMITS. Nothing here was inferred from a datasheet we could
not read, and nothing was guessed.

NOT YET VALIDATED AGAINST A MOVING MOTOR. Wire format is proven by
agreement between two implementations; servo response, gear direction,
joint sign conventions and the real joint limits are NOT. Do not read a
green run of test_damiao.py as "the arm works".
"""
import struct
from enum import IntEnum


class MotorType(IntEnum):
    """Damiao model index.

    ORDER IS LOAD-BEARING AND THE TWO REFERENCES DISAGREE. openarm_can's
    MOTOR_LIMIT_PARAMS starts at DM3507 (dm_motor_constants.hpp:150-164);
    cmjang's Limit_Param starts at DM4310 (DM_CAN.py:71) -- a one-row
    offset. We follow openarm_can because that is the library Anvil's own
    sibling arm ships against. Getting this wrong does not error: it
    silently rescales torque and position by the wrong maxima.
    """
    DM3507 = 0
    DM4310 = 1
    DM4310_48V = 2
    DM4340 = 3
    DM4340_48V = 4
    DM6006 = 5
    DM8006 = 6
    DM8009 = 7
    DM10010L = 8
    DM10010 = 9
    DMH3510 = 10
    DMH6215 = 11
    DMG6220 = 12


# (P_MAX rad, V_MAX rad/s, T_MAX N·m) per model, indexed by MotorType.
# Verbatim from openarm_can dm_motor_constants.hpp:150-164.
MOTOR_LIMITS = {
    MotorType.DM3507:     (12.5, 50, 5),
    MotorType.DM4310:     (12.5, 30, 10),
    MotorType.DM4310_48V: (12.5, 50, 10),
    MotorType.DM4340:     (12.5, 10, 28),
    MotorType.DM4340_48V: (12.5, 10, 28),
    MotorType.DM6006:     (12.5, 45, 20),
    MotorType.DM8006:     (12.5, 45, 40),
    MotorType.DM8009:     (12.5, 45, 54),
    MotorType.DM10010L:   (12.5, 25, 200),
    MotorType.DM10010:    (12.5, 20, 200),
    MotorType.DMH3510:    (12.5, 280, 1),
    MotorType.DMH6215:    (12.5, 45, 10),
    MotorType.DMG6220:    (12.5, 45, 10),
}

# MIT-mode gain quantisation. NOT per-model -- these are fixed ranges the
# firmware assumes. dm_motor_control.cpp:138-139 and DM_CAN.py:104-105.
KP_MAX = 500.0
KD_MAX = 5.0

# Single-byte control commands, sent as 7x 0xFF followed by the opcode.
# dm_motor_control.cpp:26/30/34 and DM_CAN.py:200/223/232.
CMD_ENABLE = 0xFC
CMD_DISABLE = 0xFD
CMD_SET_ZERO = 0xFE


def float_to_uint(x, x_min, x_max, bits):
    """Quantise a float into `bits` unsigned steps across [x_min, x_max].

    CLAMPS RATHER THAN WRAPPING. An out-of-range value that wrapped would
    become a large opposite-sign command -- the joint would slam the other
    way. Clamping makes an over-range request merely saturate.
    """
    span = x_max - x_min
    if x < x_min:
        x = x_min
    elif x > x_max:
        x = x_max
    return int((x - x_min) * ((1 << bits) - 1) / span)


def uint_to_float(x, x_min, x_max, bits):
    """Inverse of float_to_uint, for decoding feedback frames."""
    span = x_max - x_min
    return float(x) * span / ((1 << bits) - 1) + x_min


def pack_mit(motor_type, kp, kd, q, dq, tau):
    """Build the 8-byte MIT-mode payload.

    Bit layout is byte-identical in both references
    (dm_motor_control.cpp:147-156, DM_CAN.py:113-121):

        byte 0   q      high 8 of 16
        byte 1   q      low 8
        byte 2   dq     high 8 of 12
        byte 3   dq     low 4 | kp high 4 of 12
        byte 4   kp     low 8
        byte 5   kd     high 8 of 12
        byte 6   kd     low 4 | tau high 4 of 12
        byte 7   tau    low 8

    ARGUMENT ORDER IS POSITIONAL AND EASY TO GET WRONG. (kp, kd, q, dq,
    tau) matches MITParam in both references. Swapping kp and q sends a
    gain where a position belongs and the joint drives to a limit.
    """
    p_max, v_max, t_max = MOTOR_LIMITS[motor_type]
    kp_u = float_to_uint(kp, 0, KP_MAX, 12)
    kd_u = float_to_uint(kd, 0, KD_MAX, 12)
    q_u = float_to_uint(q, -p_max, p_max, 16)
    dq_u = float_to_uint(dq, -v_max, v_max, 12)
    tau_u = float_to_uint(tau, -t_max, t_max, 12)
    return bytes([
        (q_u >> 8) & 0xFF,
        q_u & 0xFF,
        (dq_u >> 4) & 0xFF,
        ((dq_u & 0xF) << 4) | ((kp_u >> 8) & 0xF),
        kp_u & 0xFF,
        (kd_u >> 4) & 0xFF,
        ((kd_u & 0xF) << 4) | ((tau_u >> 8) & 0xF),
        tau_u & 0xFF,
    ])


def pack_command(opcode):
    """Build an enable/disable/set-zero frame: 7x 0xFF then the opcode."""
    return bytes([0xFF] * 7 + [opcode])


def unpack_feedback(data, motor_type):
    """Decode a Damiao feedback frame.

    Layout (DM_CAN.py recv path, mirrored in dm_motor_device.cpp):
        byte 0      low nibble = motor id, high nibble = error/status code
        bytes 1-2   position, 16-bit
        bytes 3-4.5 velocity, 12-bit
        bytes 4.5-5 torque, 12-bit
        byte 6      MOSFET temperature, degrees C
        byte 7      rotor temperature, degrees C

    Returns None on a short frame rather than raising: a truncated read on
    a busy bus must not kill the control loop.
    """
    if data is None or len(data) < 8:
        return None
    p_max, v_max, t_max = MOTOR_LIMITS[motor_type]
    q_u = (data[1] << 8) | data[2]
    dq_u = (data[3] << 4) | (data[4] >> 4)
    tau_u = ((data[4] & 0xF) << 8) | data[5]
    return {
        "id": data[0] & 0x0F,
        "err": (data[0] >> 4) & 0x0F,
        "q": uint_to_float(q_u, -p_max, p_max, 16),
        "dq": uint_to_float(dq_u, -v_max, v_max, 12),
        "tau": uint_to_float(tau_u, -t_max, t_max, 12),
        "t_mos": data[6],
        "t_rotor": data[7],
    }


# Error nibble meanings, for turning a silent stall into a readable line.
ERROR_CODES = {
    0x0: "ok",
    0x8: "overvoltage",
    0x9: "undervoltage",
    0xA: "overcurrent",
    0xB: "MOS over-temperature",
    0xC: "rotor over-temperature",
    0xD: "lost communication",
    0xE: "overload",
}


def describe_error(err):
    """Human-readable error nibble. Unknown codes report their value."""
    return ERROR_CODES.get(err, f"unknown error 0x{err:X}")


# ---- parameter registers -------------------------------------------------
#
# SINGLE-SOURCE WARNING, and it matters more here than anywhere else in this
# file. Everything above was cross-checked against TWO independent
# implementations (see the module docstring). This section was read from
# openarm_can ALONE:
#     src/openarm/damiao_motor/dm_motor_control.cpp:206  (query frame)
#     src/openarm/damiao_motor/dm_motor_control.cpp:120  (reply decode)
#     include/openarm/damiao_motor/dm_motor_constants.hpp:93  (RID values)
# cmjang/DM_Control_Python was not available to confirm it. It is used ONLY
# to READ, never to write, so the worst case is a wrong answer rather than a
# wrong command -- and tools/calibrate-openyam.py treats a read that does not
# match damiao.MOTOR_LIMITS as "ask the operator", not as truth.
QUERY_ID = 0x7FF

RID_ESC_ID = 8
RID_NPP = 16          # pole pairs
RID_GEAR_RATIO = 20
RID_PMAX = 21
RID_VMAX = 22
RID_TMAX = 23

# RIDs whose payload is a little-endian uint32 rather than a float.
# dm_motor_control.cpp:259 is_in_ranges: 7-10, 13-16, 35-36.
_UINT_RIDS = frozenset(list(range(7, 11)) + list(range(13, 17)) + [35, 36])


def pack_query_param(send_id, rid):
    """Build a parameter-read request frame, sent to QUERY_ID (0x7FF).

    Payload is [send_id low, send_id high, 0x33, RID, 0, 0, 0, 0].
    The 0x33 is the read opcode; 0x55 is the write opcode and is
    DELIBERATELY NOT IMPLEMENTED HERE -- a mistyped RID on a write path
    reconfigures a servo permanently, and nothing in this project needs to
    write a register.
    """
    return bytes([send_id & 0xFF, (send_id >> 8) & 0xFF, 0x33, rid & 0xFF,
                  0, 0, 0, 0])


def unpack_query_param(data):
    """Decode a parameter-read reply. Returns (send_id, rid, value) or None.

    Returns None for anything that is not a well-formed read reply, so a
    normal MIT feedback frame arriving on the same drain loop is skipped
    rather than decoded into a nonsense register value.
    """
    if data is None or len(data) < 8:
        return None
    if data[2] not in (0x33, 0x55):
        return None
    rid = data[3]
    raw = bytes(data[4:8])
    if rid in _UINT_RIDS:
        value = struct.unpack("<I", raw)[0]
    else:
        value = struct.unpack("<f", raw)[0]
    return (data[0] | (data[1] << 8), rid, value)


def identify_motor(p_max, v_max, t_max, tol=0.01):
    """Match a motor's self-reported limit triple to a MotorType.

    Returns a LIST because two model pairs are genuinely indistinguishable
    by their limits -- DM4340/DM4340_48V both (12.5,10,28) and
    DMH6215/DMG6220 both (12.5,45,10). Returning the first match would
    manufacture a certainty the wire does not carry; the caller shows the
    operator the tie and asks them to read the label.
    """
    out = []
    for mt, (p, v, t) in MOTOR_LIMITS.items():
        if (abs(p - p_max) <= tol * max(1.0, abs(p))
                and abs(v - v_max) <= tol * max(1.0, abs(v))
                and abs(t - t_max) <= tol * max(1.0, abs(t))):
            out.append(mt)
    return out
