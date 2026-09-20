# scrub3d

Four RoArm-M2-S arms, placed anywhere, scrubbing a seated person the camera
measured — and following them while they move.

Nothing here touches the existing project. `py/`, `web/`, `tests/`, `run.sh`
and `config.json` are unmodified; this is a separate package with its own
entry point.

**It lives in the repo now** (2026-09-20), not in a `/tmp` worktree. It used
to be reachable only through `git worktree add -f /tmp/s3d origin/scrub3d`,
and `tools/export_body.py` loaded it from there -- so the bake behind
`web/assets/body.json`, which the whole territory overlay is drawn from,
depended on a directory that `/tmp` erases on reboot.

## The operator view, in one command

```bash
PATH="$PWD/venv/bin:$PATH" ./venv/bin/python -W ignore \
  scrub3d/viz.py --preview /tmp/viz.png
```

Two things in that line are not optional:

- **`PATH=`** puts the Rerun *viewer binary* in reach. The Python SDK is a
  separate thing from the viewer, and without this the script dies with
  "Failed to find Rerun Viewer executable in PATH" even though `import rerun`
  works fine.
- **`-W ignore`** silences a wall of divide-by-zero and overflow warnings out
  of `bodymodel.py:66`. They are **false**: Apple's Accelerate BLAS raises
  spurious floating-point flags on numpy 2.1.3, and the output of that line
  was measured finite, with values around 1040mm. Do not "fix" the maths
  there; nothing is wrong with it.

What you get is a seated anatomical body with the four real Waveshare arm
meshes posed by the same forward kinematics the collision layer uses, two
synchronised views, and a header that reports what the solver actually
concluded -- how much of the front is reachable, the area each arm owns, and
which arms can move at the same time without colliding.

**Its numbers will not match the projector's, and both are right.** The
operator view solves for the REAL rig in `config.json` -- the four arm
positions someone measured -- and reports `phases [[0, 1, 2], [3]]`. The
bake behind `web/assets/body.json` uses `place_arms.ring_layout(4)`, a
synthetic ring, and gets `[[0, 2, 3], [1]]`. Different rigs, different
schedules; that is the solver doing its job rather than a disagreement.
`tools/export_body.py` says why it uses the ring: the real optimiser did not
return inside 500s, and the ring is the fast stand-in.

**Expect `fused skin unavailable ... drawing the parts`.** The smooth one-piece
surface comes from Open3D's screened Poisson reconstruction, which fails on
this body roughly half the time and does not fail politely: it calls `abort()`
and takes the interpreter with it, printing `Failed to close loop` and
`libc++abi: terminating` with no traceback. The `try/except` written around it
could never run.

It is built in a child process now, so a crash there costs the cosmetic
surface and nothing else. The body draws from its parts instead -- grey rather
than skin-toned -- and every measured thing the view exists to show is
unaffected. Three consecutive runs: all exit 0, all wrote a preview, all lost
the skin.

## The developer console

```bash
PATH="$PWD/venv/bin:$PATH" ./venv/bin/python -W ignore \
  scrub3d/devconsole/app.py --port 8099
```

Twelve tabs at `127.0.0.1:8099`: the pipeline as a diagram, the live loop's
telemetry, every safety verdict, the vision stages, the body and its
territories, the rig, a catalogue generated from the code, jobs, processes and
logs. It renders, and it is a good answer to "what is actually running".

**Its two jobs cannot run from a clean checkout.** Both "Simulate the rig" and
"Replay bag01" want `data/scan01` and `data/bag01`, captures that are not in
this repo -- and `scan01` is not on the scrub3d branch either, so this is not
something the move broke. Pressing the button writes a run directory and the
run dies with `FileNotFoundError: .../data/scan01/meta.json`.

So: the console is a viewer, not a demo. The thing that runs end to end
without any capture is the operator view above.

## Run it, with no camera and no robot

The recorded dataset in `data/` is **not in the repo**: two capture sessions
from camera day, 600MB of RealSense frames, gitignored. The code synthesises a
body when they are absent, which is the path the bake and the operator view
both take.

```bash
python scrub3d/main.py --scan scrub3d/data/scan01 --replay scrub3d/data/bag01
```

Scan once, partition, then follow the person frame by frame while the arms
work. Opens the Rerun viewer.

**Every command on this page that names `scrub3d/data/` needs a capture, and
there is no `scrub3d/data/` in this checkout.** `main.py`, `viz.py --live` and
`shell.py` all stop at the same missing `meta.json`; that is a missing
recording, not a broken module. The live-camera paths need more than that
again: the Mac's `pyrealsense2` is a replay stub that refuses by design, and
Sapiens' depth and normal weights are absent, so `shell.py` falls back to
depth-derived normals.

What DOES run here, with no camera and no capture, on the procedural body:

```bash
./venv/bin/python scrub3d/viz.py --preview /tmp/viz.png   # a still, ~3s
./venv/bin/python scrub3d/viz.py --save /tmp/run.rrd      # then: rerun /tmp/run.rrd
```

Both write real output from the real partition. `--preview` also tries a fused
skin through Open3D's Poisson, which aborts the process rather than raising on
this machine; it runs in a child process for exactly that reason, so the abort
costs the cosmetic surface and nothing else ("fused skin unavailable" is that
guard working, not a failure).

```bash
python scrub3d/main.py --preflight          # the eight checks, then stop
python scrub3d/main.py --record justin      # camera day: scan a person, one command
python scrub3d/shell.py --preview out.png   # look at the reconstruction, no viewer needed
python scrub3d/tools/plan_rig.py            # where should the arms go?
python scrub3d/tools/check_camera_day.py    # camera day, checked without a camera
python scrub3d/devconsole/app.py            # the developer console, http://127.0.0.1:8077
python scrub3d/live/live_body.py --seat-mm 420   # camera: live body model and arms in Rerun
python scrub3d/live/rig_editor.py           # drag the arms into place, http://127.0.0.1:8078
```

**The live view, [live/README.md](live/README.md)**, measures a body model of
whoever sits in front of the camera while it runs, and has the arms scrub it,
every move checked by the fleet governor. It also records and replays, so all
of it can be worked on without a camera. Its rig editor places the arms by
hand, checks and simulates each placement on a recording, and saves it for
the live view, which picks it up while it runs.

**The developer console is for whoever is changing the code**, not for the
person in the chair. Its Overview shows the rig in live 3D (the person, the
four arms, what each has scrubbed) beside a diagram of every data process,
coloured by what each stage did. "Simulate the rig" runs the four arms over
the scanned person at their real speed, with no camera. The other tabs run the
commands above as jobs and show each one as a process: every stage, how long
it took, what it decided and why it refused, the loop's telemetry, every
safety verdict, and what is running on the machine. A running job reaches the
3D because the console reads the recording it saves as it grows, so the job
itself is untouched. The console edits no pipeline code to do any of this, and
it never commands hardware.

### Watching the live demo with it

The console and the demo are two processes that share nothing but the machine,
so the console can be opened and closed during a run without touching it. The
demo serves the page on `:8000` and events on `:8765`; the console takes
`:8077`, and Rerun takes `:9091` and `:9877` only when the Overview's 3D is on.
No pair overlaps, measured on the GB10 with both running.

On the Linux box, with the demo already up:

```bash
ssh wg
~/wg-venv/bin/python scrub3d/devconsole/app.py --no-3d --port 8077
ssh -N -L 8077:127.0.0.1:8077 wg     # from the laptop, then open :8077
```

The console binds `127.0.0.1` only, so the tunnel is how a laptop reaches it.
Use `--no-3d` unless the Overview's 3D is actually wanted: it starts a Rerun
viewer, which is the one part that costs GPU the demo is also using.

**What it can see of a demo it did not start.** The Processes tab lists
`py/scrubbot.py` with its pid, CPU, memory and the ports it holds, next to
everything else Python on the box. That is the whole connection, and it is the
only one that exists for a demo started by `run.sh`. The demo writes no events
the console reads, and its websocket carries display events for the projector,
not telemetry, so the other tabs still describe runs the console itself
started, not the demo.

**For the full picture, every stage, timing, decision and refusal, start the
demo THROUGH the runner instead:**

```bash
~/wg-venv/bin/python scrub3d/devconsole/runner.py py/scrubbot.py --scripted --no-arm
```

The demo runs unchanged and the console gets a real run directory for it. Every
call into `py/arm.py` is recorded with its arguments and timing. Measured on one
`--scripted --no-arm --auto-arm` cycle on the GB10: 2,084 spans, of which 2,077
were `arm.Arm.set_target`, each carrying its target, for example
`xyz [235.1, 0.0, 234.8]`.

The probes wrap `scrub3d/` modules and `py/arm.py`, nothing else in `py/`. So
what shows up depends on what the demo actually loads. `--scripted` drives the
arm directly and produced only the `driver` stage above; a mode that arms the
governor routes through `fleet.propose()` and fills the Safety tab as well.
`py/scrubbot.py` itself is never wrapped, so its own functions never appear.

**The trap, measured, and the reason this is not the default:** a run started by
the runner cannot open a serial port. The probe guard raises
`RuntimeError: devconsole: opening a serial port is disabled in console jobs`,
and because that happens inside `make_arm()` the whole demo dies at startup with
a traceback. It is harmless with `--no-arm` and fatal with a real arm. Pass
`--allow-hardware` before the script path to lift the guard, or keep the runner
for dry runs and watch a real armed demo from the Processes tab instead.

**Camera day has its own runbook: [CAMERA-DAY.md](CAMERA-DAY.md)** — what to
run, what each step must print, the five checks only hardware can answer, and
the traps that have already cost time.

Every module also runs standalone as its own test:

```bash
python scrub3d/frames.py      # the world, from the floor
python scrub3d/scan.py        # this person, from one capture
python scrub3d/track.py       # pose at 20fps off the bag
python scrub3d/fleet.py       # the governor's refusals
python scrub3d/torque.py      # contact in newtons, scrubbing vs leaning
python scrub3d/handeye.py     # calibration, and what it cannot see
python scrub3d/armlink.py     # the bridge to a real arm, with no arm
python scrub3d/devconsole/check.py   # the console sees what actually ran
```

## The shape of it

| | Module | When | What it produces |
|---|---|---|---|
| World | `frames.py` | once | `T_world_camera` from the floor plane |
| **Shape** | `scan.py` | seconds, GPU | a `BodyModel` whose cells sit on the **measured** surface |
| Territories | `partition.py` | once | one contiguous region per arm, plus separating planes |
| Placement | `place_arms.py` | offline | where to bolt the arms, searched not guessed |
| **Pose** | `track.py` | **every frame, CPU** | the body following the person |
| Motion | `control.py` | 40 Hz | a field re-read live — no stored path |
| Reachability | `adapt.py` | ~1 Hz | handoff, posture advice, or stop |
| Safety | `fleet.py` | every command | the only route to an arm |
| **Hardware** | `armlink.py` | every command | the only route to `py/arm.py`, and it refuses what `BOX` would silently relocate |
| **Reconstruction** | `shell.py` | seconds, GPU | the person as the camera **saw** them, skinned so it follows |
| View | `viz.py` | live | body, arms, territories, what each arm has scrubbed |
| Developer console | `devconsole/` | any time | the rig in live 3D, every data process as a diagram, and every stage of a run as a process: timings, decisions, refusals, jobs, processes |

**Three surfaces, on purpose.** `bodymodel` holds what the robot reasons about:
named regions, per-cell areas and normals, moved by one 4×4 each. `skin.py`
fuses those parts for when there is no capture. `shell.py` is what the camera
measured — real depth, real colour, AI-predicted normals — and it is what a
person looks at. Nothing downstream reads it: a Poisson mesh has no per-cell
area and no idea which triangle is a forearm, so it must never become a
planning input.

**Why it is real time.** The expensive half runs once. Segmentation and the
girth fit produce *shape*, which does not change when somebody moves. What
changes is *pose*, and `repose()` writes one 4×4 per region without touching a
single surface cell — which is the whole reason cells are stored region-locally.
Measured off `bag01`: 300/300 frames at 20 fps.

## What is measured and what is not

Being exact about this is the point, not a caveat.

| | Source |
|---|---|
| Where the surface is | **measured** — cells snapped onto the depth image |
| A limb's width | **measured** — from the RGB silhouette |
| Where the joints are | **measured** — MediaPipe, through clothing |
| The world frame | **measured** — floor plane, 3-5 mm residual |
| The back of a limb | **modelled**, errs thick, never scrubbed |
| Anterior semi-axis | **a stated ratio**, because this sensor under-reads curvature threefold |
| Contact depth | **not modelled at all** — found by torque |
| The visual body | **measured** — every vertex is a depth pixel, coloured by the pixel that saw it |
| Holes in it | **left as holes** — the depth model that could fill them fits at 40.8 mm here, and is refused above 20 |

The subject is **clothed**, so what is measured is the clothed limb. That is
the surface the sponge presses on, so it is the right thing to measure — and it
is not the person's girth and is never reported as such.

## Requirements

`pyrealsense2 open3d rerun-sdk scipy trimesh mediapipe opencv-python torch`,
Python **3.11+** (below that the Windows timer cannot hold 40 Hz). The
developer console adds `dash plotly psutil`; the pipeline never imports them.

Model weights are **never committed**. `python scrub3d/fetch_models.py` shows
the CC-BY-NC-4.0 terms and downloads them; the captures carry a cached
segmentation so most work needs neither the weights nor a GPU.

`python scrub3d/fetch_models.py --status` answers "is the scan chain ready"
without starting a download. It exits non-zero when anything is missing, so it
works as a preflight check. It reports `torch` separately, because the weights
alone are not enough — they are TorchScript and will not load without it.

`data/` and `weights/` are gitignored. The captures are a person's body
geometry.

### What the capture chain needs, measured

Checked on both machines, 2026-09-19. `scan.py` is the one module here that
cannot run on either of them today, and it is worth being exact about why
rather than discovering it on camera day:

| | Mac | Linux box (`ssh wg`) |
|---|---|---|
| `pyrealsense2` | replay **stub** — every live call raises | **2.58.4**, real |
| RealSense camera | — | **not attached** (`lsusb` shows no Intel device, no `/dev/video*`) |
| `torch` | missing | **missing** |
| Sapiens weights | missing (3.7GB) | missing |
| captures in `data/` | **none** | **none** |

So the chain blocks in two independent places, and fixing either one alone
changes nothing:

1. **No capture exists.** `frames.solve()` needs `meta.json` +
   `depth_median_mm.npy`; `scan.scan()` needs those plus `sapiens_seg.npy`.
   `data/` is absent on both machines — the two camera-day sessions the
   README describes are gitignored and were never on these disks.
   Making one needs the D455 plugged into the box.
2. **No segmentation is possible.** Even with a capture, `scan.scan()` reads a
   cached `sapiens_seg.npy`. Producing one needs `sapiens-seg-0.3b`
   (1.36GB, CC-BY-NC-4.0) **and** torch, and torch is absent on both machines.
   `scrub3d/tools/segment_captures.py` is what writes that cache.

Verified by building a synthetic capture that satisfies `frames.solve()`:
the solve succeeded in 0.175s and recovered the camera height it was given,
then `scan.scan()` stopped at the missing `sapiens_seg.npy`. The blocker is
the segmentation cache, not the geometry.

To capture, on the box with the camera attached:

```bash
python tools/live-scan.py --out recordings/live01    # capture + solve the world frame
python tools/live-scan.py --reuse --out recordings/live01   # re-solve, no camera
```

`tools/live-scan.py` is a thin wrapper on `record.record()` and
`frames.solve()` — the same functions the rest of the package calls, not a
second capture path. It stops at the world frame, which is everything that
can be had without the segmenter.
