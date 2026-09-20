# Camera day

What to run, in order, and what each step must print. Everything here has been
run against the recorded captures; what has never run is the camera itself and
the arms.

Total time to a person on screen with four coloured territories: about three
minutes, and none of it needs an arm.

---

## Run this the night before, with no camera attached

```bash
python scrub3d/tools/check_camera_day.py
```

It runs three of the four steps below against the recorded captures with only
the RealSense call stubbed out: that `--record` asks for rotation 180 and the
density preset, that the segmentation lands registered to the image and leaves
no temp file, that the reconstruction passes its gates and still refuses a
capture with a bystander in it, and that the operator view actually receives
data.

It must end in `OK`. It also prints what it cannot check, which is the camera
itself and every arm.

---

## 0. Before the person sits down (2 minutes, no volunteer)

**Open the developer console first, and leave it open all day.**

```bash
python scrub3d/devconsole/app.py
```

Then open http://127.0.0.1:8077. Every step below can be started from its Jobs
tab, and shows up there as it runs: which stage it is in, what each gate
decided, the live loop's frame rate and freezes, and every refusal with its
reason. To run a step from a terminal and still see it, put the runner in
front of the same command:

```bash
python scrub3d/devconsole/runner.py scrub3d/main.py --record justin --seconds 10
```

A command run without the runner is not visible to the console. When a step
prints something unexpected, look at the console before reading any code. The
Processes tab also shows whether an old viewer or a forgotten job is still
holding the GPU.

**Plug in the D455 and check the driver sees it.**

```bash
python -c "import pyrealsense2 as rs; ctx=rs.context(); print([d.get_info(rs.camera_info.name) for d in ctx.devices])"
```

Must name a D455. If the list is empty the camera is not enumerated and nothing
below will work; reseat the USB-C and prefer a USB 3 port, because at USB 2 the
1280x720 colour stream will not start at all. The console's header shows the
same thing: `camera none` until the driver enumerates a device, and the Live
camera and Record jobs stay greyed out until it does.

**Point it where the dataset had it.** This is not a preference, it is what
every measured number in the plan assumes:

| | Target | Why |
|---|---|---|
| Height above floor | **833 mm** | measured in all seven captures, 8 mm spread |
| Pitch | **2.5-3 degrees down** | at 4.7 the crown clears the frame by 2 pixels, which is luck |
| Distance to the torso | **1.05 m** | closer than min-Z 0.52 m is the only hard bound |
| Mounting | **upside down** | every capture was shot this way and the code assumes it |

**Mark the floor where the chair legs go.** The scan measures where the person
is; if the chair moves between the scan and the run, every territory is wrong
and nothing in software will notice.

---

## 1. Scan the person (one command)

```bash
python scrub3d/main.py --record justin --seconds 10
```

They sit facing the camera, forearms resting on their thighs, and hold still.
This records, segments, reconstructs and plans in one go, so the rotation flag
cannot be forgotten and the steps cannot run out of order.

**What it must print:**

```
  recording 10s into .../data/justin
  segmenting ...
    wrote a (720, 1280) mask, N classes
  scanning justin ...
    6 regions, ~1100cm2 scrubbable, measuring clothed limbs
    coverage 66.6%, phases [[0, 1], [2, 3]]
```

**If the mask shape is not `(720, 1280)`** the capture was rotated wrongly.
Delete the directory and check the camera is mounted upside down.

**If "scrubbable" is under 600 cm2**, the segmentation found less of the person
than it should. Usual cause is a dark shirt against a dark background.

---

## 2. Look at the reconstruction before trusting any number

```bash
python scrub3d/shell.py --capture scrub3d/data/justin --preview justin.png
```

Four gates print, and all four must say `ok`:

```
    [ok ] enough surface               ~60000 vertices
    [ok ] crown at a seated height     ~1290mm above the floor
    [ok ] has depth, not a sheet       ~600mm front to back
    [ok ] plausible width              ~1600mm across
```

**Then open `justin.png` and look at it** -- three angles, no viewer needed.
It must be recognisably that person in that posture.

This is the single most valuable minute of the whole day, and it is not
optional politeness: **every gate above can pass on a reconstruction of the
wrong person.** That is exactly what the bystander in `pose_lean` produced.
The check that catches it is a human looking and seeing that it is not them.

### If it refuses

The message names the number that failed. The two that actually happen:

- **"crown at a seated height ... 919mm"** — somebody else is standing in
  frame and the reconstruction locked onto the wrong person. This is real:
  `pose_lean` in the recorded set has a bystander behind the chair, and 38% of
  that capture's person-pixels are them. **Clear the room behind the chair and
  re-record.** The subject band will say `NOT ALONE` and how many points it
  dropped whenever anyone else is visible.
- **"has depth, not a sheet"** — the depth stream is mostly invalid. Check the
  person is past 0.52 m and that nothing reflective is behind them.

A refusal is not a blocker for the run: the live view falls back to the modelled
body and says so. It is a blocker for believing the picture.

---

## 3. Follow them live

```bash
python scrub3d/main.py --scan scrub3d/data/justin
```

With no `--replay` this reads the camera. It must report every frame tracked and
the freeze guard quiet:

```
  N/N frames tracked, 0 frozen by the motion guard
```

Frames dropping means the depth stream is stalling; the loop uses a 200 ms
frame timeout on purpose, so it will keep running rather than hang for five
seconds while an arm holds contact.

---

## 4. The five things only hardware can answer

`main.py --preflight` reports these as UNKNOWN, never PASS, and **nothing
moves until they are answered.** That is deliberate: a check that silently
passes because it could not run is worse than no check.

1. **Arms report `link_ok` and a fresh `T:105`.** `link_ok` counts consecutive
   write failures only, so a wedged ESP32 that still accepts writes reads as
   healthy. Add feedback staleness: no successful `T:105` in 1.0 s is
   link-degraded.
2. **Torque caps re-asserted within 10 s.** A brownout-reset arm comes back at
   the firmware default of 1000, about 16x the intended shoulder torque. Four
   arms means four power draws and a higher chance of it.
3. **All arm calibrations agree on one fiducial, within 5 mm.** Until this
   exists the collision model between arms is fiction.
4. **The person is seated as scanned.** Needs a live frame to compare.
5. **A hardwired E-stop cutting 12 V, independent of software.** A human has to
   look at it. Four arms near a person is where a hardware kill stops being
   optional.

Two more that need the camera but no volunteer, both from the plan:

- **The advanced-mode depth sweep.** The four canned presets never varied
  `STCensusRadius`, which is the parameter most likely responsible for the
  3x curvature under-read. Shoot the control flask side-on and report one
  bulge/half-width ratio per setting against its true 1.00.
- **Re-shoot `empty`.** The one on disk has a standing person in the corner.

---

## 5. Traps that have already cost time

- **`--rotate` must be 180.** All eleven captures overrode it, the default is
  now 180, and `main.py --record` does not expose the flag at all. A sideways
  capture produces sideways intrinsics and still looks like a plausible point
  cloud.
- **`depth == 0` means NO DATA, never 0 m.**
- **The scan wants `high_density`, the live loop wants `high_accuracy`.**
  Density for shape, trustworthy pixels for tracking. `--record` picks
  `high_density` for you.
- **Never `hole_filling_filter` on anything reaching an arm.** It fabricates
  surfaces.
- **Sapiens weights are CC-BY-NC-4.0 and body scans are biometric data.**
  Neither is ever committed. `data/` and `weights/` are gitignored.

---

## What "working" means at the end of the day

Three things, in order of how much they prove:

1. A person sits, and ten seconds later their own body is on screen, in colour,
   with four coloured territories on it. **No arm required.**
2. They move, and the reconstruction moves with them at 20 fps.
3. Only then, arms: two far apart, then two overlapping, then four. Never
   1 to 4 in one step, and a calibration plus an offline collision self-test
   between each.
