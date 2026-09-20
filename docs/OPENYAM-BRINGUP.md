# OpenYAM bring-up — from box to first motion

The arm is an **Anvil OpenYAM**: 6 joints, Damiao servos, 610mm reach,
2.5kg payload. It talks **CAN bus**, not USB serial. This is a different
machine from the Waveshare RoArm-M2-S that `py/arm.py` drives, and none
of that driver's code applies to it.

**Nothing in this document has been run against a physical arm.** The wire
format is verified, and so is the driver's send path end to end over a
real `python-can` bus. The motion is not, and no motor has turned. Read
"What is still unknown" before you let it move.

**The one thing left to do when the adapter arrives is plug it in and run
`sudo bash tools/can-bringup.sh`.** No further code work is needed for the
arm to be commanded; what is missing after that is measurement, not
software. See "Measure what the driver refuses to guess".

---

## What you need

| Thing | Status | Note |
|---|---|---|
| OpenYAM arm | you have it | |
| CANable 2.0 adapter | you have it | enumerates as `canable2 gs_usb`, VID `0x1D50` PID `0x606F`. **Still plugged into the Mac, where it cannot work.** Move it to the Linux box first |
| A Linux host | **you have it** | an NVIDIA GB10, `ssh wg`, Ubuntu 24.04 aarch64. See below |
| 24V supply for the arm | check | the servos do not run off USB |
| CAN termination | check | 120Ω at each end of the bus |

### Why Linux is not optional

The adapter runs **gs_usb** (candleLight) firmware, which is a native USB
CAN protocol rather than a serial port. Linux has a kernel `gs_usb` driver
that turns it into a real SocketCAN interface. macOS has neither that
driver nor SocketCAN, so no `/dev/cu.*` device appears — that absence is
correct behaviour, not a fault.

### The host, and what is not ready on it

The host is an **NVIDIA GB10**, not the Raspberry Pi this document used to
name. Ubuntu 24.04.4, kernel `6.17.0-1022-nvidia`, aarch64, 20 cores,
121GB RAM. Reach it as `ssh wg`; the repo is at `~/Wheelgentic` and the
Python that has the wheels is `~/wg-venv/bin/python`. `python-can` 4.6.1
is already installed there, so step 3 below is a no-op on this box.

Two things stand between it and a first frame, and neither is code:

- **The adapter is not plugged into it.** No CAN interface exists —
  `ip -br link show type can` prints nothing. Until the CANable is moved
  over from the Mac, every step below has nothing to talk to.
- **`sudo` asks for a password.** The `gs_usb` module is present at
  `/lib/modules/6.17.0-1022-nvidia/kernel/drivers/net/can/usb/gs_usb.ko.zst`
  but is not loaded. (The core `can` and `can_raw` modules *are* loaded —
  `python-can` pulls those in — so `lsmod | grep can` returning rows is
  not evidence the adapter driver is up.) Loading `gs_usb` and running
  `ip link set can0 up` both need root. Whoever is at the keyboard has to
  type the password; an agent over ssh cannot. `tools/can-bringup.sh`
  collects every root-only step so it is one password, once.
- **`can-utils` is not installed**, so there is no `candump` or `cansend`
  on this box yet. Installing it also needs root; the bring-up script
  does it.

For multi-line Python over that link, write the file locally and
`scp` it to `/tmp/` first. Nested quoting through `ssh` mangles it.

A spare x86 laptop or a Linux VM with USB passthrough would also work, but
the GB10 is the one that exists.

Reflashing the adapter to `slcan` firmware would give you a serial port
that macOS can open, and `python-can` speaks it. Do not build on that:
slcan is ASCII-framed over a serial line and will not hold 6 joints at the
rate this arm expects. It is acceptable for poking one motor by hand, and
nothing more.

---

## Bring-up, in order

Order matters. Plug CAN **before** powering anything, per Anvil's own
warning: hot-plugging CAN into a live bus can knock the bus out.

### 1. Bring the interface up

Plug the CANable into the GB10, then run the script that does every
root-only step in one pass:

```bash
sudo bash tools/can-bringup.sh
```

**This is the only command in the whole bring-up that needs a password.**
Everything after it runs as the normal user. Re-running it is safe; each
step checks for the state it wants before changing anything.

What it does, and why each part is there:

| Step | Why |
|---|---|
| `modprobe gs_usb` | Belt-and-braces. `gs_usb` already carries a USB alias for the CANable's `1d50:606f` (confirmed in `modinfo` on this kernel), so udev autoloads it on plug-in. Loading it explicitly lets step 2 tell "driver missing" apart from "adapter not plugged in". |
| Append to `/etc/modules-load.d/can.conf` | Survives a reboot. |
| Check `can0` exists | If the interface is absent the fault is the cable or the adapter, and no `ip` command fixes that. Stop there rather than print a confusing error. |
| `ip link set can0 down`, then `up type can bitrate 1000000` | The bitrate can only be set while the link is down; the kernel rejects a change on a running interface. Downing first is also what makes the script re-runnable. |
| Read the state back | `ip link set ... up` exits 0 for a link that then fails to come up, so the script verifies by reading `ip -details link show`, not by trusting the exit code. |
| Install `/etc/udev/rules.d/90-cloak-can.rules` | Persistence, **not** permissions. Everything above lives in the kernel's netdev state and dies the moment the adapter is unplugged, which on a demo table happens by accident. The rule re-raises the link automatically on replug so a knocked cable does not need a root password to recover. |
| `apt-get install can-utils` | `candump` is how you prove the servos are talking before blaming Python. It is not installed on this box by default. Skipped with a warning if the machine is offline. |

**No udev rule is needed to let a normal user drive the arm, and the
script does not pretend to install one.** Opening an `AF_CAN` socket to
send and receive frames needs no privilege at all. Measured on this box
as uid 1000: `bind()` to a missing interface returns `ENODEV` ("no such
device"), not `EPERM` ("not permitted"). Only *configuring* the link
needs `CAP_NET_ADMIN`. A `chmod` rule would also have nothing to act on,
because a SocketCAN interface is a network device, not a `/dev` node.

1 Mbit/s is the rate the reference implementation configures
(`openarm_can` README: `can_configure -d 1000000 --no-fd`). The servos do
not autodetect it; a mismatch is a silent dead bus.

### 2. Prove the arm is talking before running any of our code

```bash
candump can0
```

`can-utils` is **not installed on the GB10** as of this writing; step 1
installs it, or `sudo apt-get install can-utils` does it by hand.

Power the arm. You should see frames. **If nothing appears, stop here** —
no amount of Python fixes a silent bus. Check in this order: termination,
24V present at the servos, CAN-H/CAN-L not swapped, and that every motor
has a distinct ID.

### 3. Install the Python CAN layer

Already done on the GB10: `python-can` 4.6.1 is in `~/wg-venv`. This step
is here for any other host.

```bash
pip install python-can
```

### 4. Read the arm without moving it

```bash
ssh wg 'cd ~/Wheelgentic && ~/wg-venv/bin/python tools/hardware-selftest.py'
```

This enables the joints, reads feedback, and exits. It does **not**
command motion. Feedback from all six joints means the whole chain works.

`py/openyam.py` run directly will **refuse to start** at this point, and
that is correct: its calibration gate blocks the hardware path until
`config-openyam.json` exists. Step 5 is what writes it.

### 5. Measure what the driver refuses to guess

```bash
ssh wg 'cd ~/Wheelgentic && ~/wg-venv/bin/python tools/calibrate-openyam.py'
```

Nothing below "What is still unknown" is a number this project invented
confidence about. Until this tool has written `config-openyam.json`, the
driver will not drive.

---

## Rehearsing before the adapter arrives

The send path can be exercised with **no adapter and no arm**, on any
host with `python-can`:

```bash
ssh wg 'cd ~/Wheelgentic && ~/wg-venv/bin/python tests/test_openyam_bus.py'
```

This builds a real `OpenYamArm` through its normal constructor and points
it at python-can's `virtual` bus instead of `socketcan`. A second bus on
the same channel sits where a motor would and reads back every frame the
driver transmits. `_raw_send`, `_pump`, `_send_joint`, `poll_feedback`
and the e-stop path all run exactly the code the real arm runs — the only
difference is the transport string.

Either of these picks the virtual transport:

```bash
CAN_INTERFACE=virtual CAN_IFACE=some-channel ...     # environment
OpenYamArm(channel="some-channel", interface="virtual")   # constructor
```

`interface` defaults to `socketcan`, so nothing about the hardware path
changes.

---

## What is still unknown — read before allowing motion

Anvil has not published the OpenYAM's URDF (`OpenYAM_description` says
"Coming Soon") or its Damiao frame layouts. The wire protocol below was
recovered from two independent open-source implementations that agree
byte for byte. Everything about the *physical* arm is still a placeholder.

Each of these is marked `CALIBRATE` in `py/openyam.py` and **must be
measured against the real arm**:

**Motor models per joint** (`JOINT_MOTORS`). Currently DM4310 throughout,
copied from `openarm_can`'s examples. A 610mm arm carrying 2.5kg almost
certainly does not use the same actuator at the shoulder as at the wrist.
A wrong model here does not raise an error — it silently rescales torque
and position by the wrong maxima. **Read the labels on the actual motors.**

**Joint limits** (`JOINT_LIMITS`). Conservative guesses. The servo's own
±12.5 rad range is the *encoder's*, not the linkage's; a joint driven
there will hit metal.

**Link lengths** (`L_BASE`, `L_UPPER`, `L_FORE`). A plausible split of the
published 610mm reach. Wrong values mean the IK solves for a geometry the
arm does not have, and every cartesian target lands somewhere else.

**Home pose** (`HOME_Q`). Anvil's config ships `home_positions` as three
7-vectors — a staged approach, not one pose. Until that staging is
understood on real hardware, ours is a plain safe tuck.

**Joint sign conventions.** Which direction is positive for each joint is
unverified. A flipped sign sends the joint the wrong way.

Until these are measured, run with the arm clamped down, clear of people,
and with a hand on the power switch.

---

## What was verified, and how

Both suites below were run on the GB10 itself (`ssh wg`,
`~/wg-venv/bin/python`) and both exit 0. Neither moved a motor.

### `tests/test_damiao.py` — 38 assertions, the frame format

Proves `damiao.pack_mit` emits the right bytes. It never constructs an
arm, so it says nothing about whether those bytes reach a bus.

The MIT-mode control frame is byte-identical to two independent
implementations: `enactic/openarm_can`
(`dm_motor_control.cpp:136-156`, Apache-2.0, the library Anvil's sibling
arm ships against) and `cmjang/DM_Control_Python` (`DM_CAN.py:104-121`).
Enable `0xFC`, disable `0xFD`, set-zero `0xFE` confirmed in both.

**One deliberate divergence.** `DM_Control_Python`'s range clamp is
broken: `LIMIT_MIN_MAX` mutates a local and returns `None`, and its caller
discards the result, so an out-of-range value wraps through `uint16`
instead of saturating. On a torque field that is the difference between a
capped push and a full-scale slam. Ours clamps, and the test asserts it: a
50 Nm request on a 10 Nm joint becomes 10, not garbage.

### `tests/test_openyam_bus.py` — 32 assertions, the send path

Everything between `pack_mit` and the wire, which nothing tested before:

- Construction sends enable (`FF FF FF FF FF FF FF FC`) to all six ids
  `0x01`–`0x06`, as standard 11-bit frames.
- The pump transmits, one 8-byte frame per joint per tick, to those six
  ids and no others.
- **The bytes on the bus are byte-identical to `pack_mit`'s**, per joint.
  Measured at the home pose: J0 `7F FF 7F F1 47 99 97 FF`, J1 and J2
  `84 79 7F F2 14 E6 57 FF`, J3 `7C 6C 7F F0 CC 23 D7 FF`, J4 and J5
  `7F FF 7F F0 62 0A 37 FF`.
- The `kp`/`kd` fields decoded back out of those frames are the teleop
  gains for the right joint (J0 39.93 ≈ 40, J1 64.96 ≈ 65, J3 24.91 ≈ 25,
  J4 11.97 ≈ 12). Decoding rather than re-comparing is what catches the
  driver reading the wrong row of the gains table.
- The slew limit holds **on the wire**, not just in `q_cmd`: stepping J0
  to its soft limit, no two consecutive frames differ by more than the
  0.004068 rad cap plus one 0.000381 rad quantisation code. Every
  measured delta was 10 or 11 codes, never 12.
- E-stop sends disable (`…FD`) to all six, and then **the pump goes
  completely silent** — zero frames, including when a new target is
  commanded during the stop. That silence is the stop: Damiao servos hold
  their last commanded state.
- `clear_estop` re-enables all six and MIT frames resume. `close()`
  disables all six on the way out.
- `link_ok` stays **False**, correctly: nothing answered. A driver that
  reported a good link from its own echo would report healthy on a dead
  arm.

**What it cannot prove.** A virtual bus has no servos, no arbitration, no
bus load, no ack, no brownout, no gear direction. Every frame "sends"
because nothing can nack it. This says the driver emits the right frames
in the right order to the right ids. It says nothing about the arm.

---

## Safety, and what changed from the RoArm

The old driver's `reachable()` guarded a hazard specific to RoArm
firmware: `T:1041` had no NaN check, so an unreachable target produced NaN,
Arduino's `constrain()` passed it through (every comparison against NaN is
false), and the int16 cast made it 0 — whipping the shoulder to
servo-middle.

That exact hazard is gone, because we now command joints directly. **A new
one replaces it.** IK moved from the ESP32 to the host, so a non-converging
solve is now *our* NaN, and `float_to_uint` would quantise it into an
arbitrary angle. So the guard moved rather than disappeared: `_solve_ik`
returns `None` instead of NaN, and every caller checks.

Carried over from `py/arm.py`, intact:

- **Workspace clamp** with a warning above 50mm, rate-limited to one line
  per second so a bad stream cannot bury the log.
- **Rate limiting.** 240mm/s ceiling, same as the RoArm — the OpenYAM is
  heavier with more payload, so it moves no faster.
- **A pump thread that always sends the newest target** rather than
  queueing, so a stalled consumer cannot replay stale positions into the
  person after a pause.
- **`_raw_send` never raises.** The pump calls it 100×/second; an
  exception would kill the thread and leave a live arm with nothing
  driving it.
- **The e-stop token.** A stop bumps a counter that invalidates any
  in-flight `clear_estop()`, so a clear that began before the stop cannot
  complete after it and silently re-enable the arm.
- **`clear_estop` resumes from measured position**, not the stale
  pre-stop target, so the arm does not snap on re-enable.

One behaviour is new: **while stopped, the pump sends nothing at all.**
Damiao servos hold their last commanded state, so silence is what keeps
the arm still. Re-commanding during a stop would defeat it.

---

## Integration

It is **not** wired into `scrubbot.py` yet. Doing that before the arm's
geometry is measured would mean the vision pipeline driving a robot whose
kinematics are guesses.

**It is also not a drop-in, and this document said it was.** The method
names line up, but four of them behave differently enough to break the
scrub, and two of those are safety signals that fail silently:

- `arm.target` is six joint angles in radians here, four cartesian
  values in millimetres on the RoArm. `scrubbot.py:672` holds position
  during an occlusion with `arm.set_target(*arm.target)`, which raises
  against the six-tuple. Occlusion is constant during a scrub — the arm
  covers the forearm it is washing — so this fires on the first dropped
  landmark and kills the vision thread mid-scrub.
- The torque cutout at `scrubbot.py:1041` looks for keys named for the
  RoArm's joints. `poll_feedback` here returns a dict keyed by joint
  number. Every check compares against a default of zero and passes, so
  the arm has **no torque limit** and the code looks like it does. No
  exception, no log line.
- `contact` is declared and never assigned, so it is always False. That
  is the "the sponge is really touching" signal that gates every splotch
  pop and drives the projector's contact indicator.
- `scripted.py` reads `arm.target`, slices the first three values and
  travels to them as millimetres. Against this arm those are radians, so
  a scripted run interpolates from a point that is nowhere near where the
  arm is. Scripted mode is the fallback most likely to run against a real
  person under time pressure.

Fix these in `py/openyam.py`, not by widening the call sites — a caller
that has to know which arm it is holding is the shape this project's
one-pipeline rule exists to prevent.
