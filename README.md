# Wheelgentic

A robot arm with a sponge scrubs your forearm. A goofy low-poly cartoon on the
projector mirrors your pose, and the dirt splotches on it pop with soap
bubbles as the real arm actually scrubs.

The pitch: bathing is the #1 assistance need for older adults and the most
undignified. There aren't enough caregivers. Every frame stays on the laptop,
nothing is stored, and the only thing anyone ever sees is the cartoon — so the
goofy avatar is a **privacy mechanism**, not just a joke.

## Run it

```bash
./run.sh                                       # live: camera + arm + projector
CAM=fake ./run.sh --no-arm                     # THE WHOLE DEMO, no hardware at all
REPLAY=recordings/good_run.jsonl ./run.sh      # no camera, no arm; pop splotches with 1/2/3
python py/scrubbot.py --scripted               # canned scrub, forearm on a taped X
```

**Replay is not the same deal as `CAM=fake`.** `REPLAY=` feeds recorded
landmarks straight to the arm and never runs the vision loop, so there is no
APPROACH/SCRUB cycle and no automatic splotch pop — measured, zero pops in 36s
even with `--no-contact-gate`. Pressing `s` prints `ARMED` and then nothing.
Pop by hand with `1` `2` `3` on the projector (verified: 0% → 100%). It is the
camera-dead fallback, not a full run.

### The other arm

There are two arms and the run commands above drive the first one. `./run.sh`
builds the Waveshare RoArm-M2-S over USB serial (`py/arm.py`). The Anvil
OpenYAM — 6 joints, Damiao servos, CAN bus — has its own driver in
`py/openyam.py`, which nothing in `run.sh` reaches yet.

```bash
python3 py/openyam.py    # Linux only: enables the joints, reads them, exits
```

That command does not command motion, and it needs a Linux host: the CANable
2.0 adapter speaks gs_usb, which needs SocketCAN, and macOS has neither. A
Raspberry Pi is on order. Five values in `py/openyam.py` are guesses marked
`CALIBRATE`, including the link lengths the IK solves against, so **do not let
this arm move near a person yet.** `docs/OPENYAM-BRINGUP.md` is the procedure
from box to first motion and lists all five.

### Checking the arms before a run

These never command motion. Run them with a person nearby; that is what they
are for.

```bash
python3 tools/hardware-selftest.py         # every piece of hardware, one table
python scrub3d/live/arm_hw.py              # which boards are plugged in, and where each arm is
python scrub3d/live/arm_hw.py --assign     # say which board is red, blue, green
python scrub3d/live/arm_hw.py --selftest   # the whole driver against simulated boards (~40s)
python scrub3d/live/arm_hw.py --report LOG # what the arms really did in a past run
python scrub3d/live/arm_sight.py --selftest  # the camera-vs-rig check, on a synthetic scene
python scrub3d/live/stop_live.py           # stop a live view left running in the background
```

`hardware-selftest.py` now includes two RoArm rows. **RoArm boards present**
lists every driver board on USB by its chip. **RoArm arms named** checks
`scrub3d/live/arm_ports.json` exists, because four identical boards enumerate
in whatever order the OS felt like and that file is the only thing saying
which one is the red arm. Get it wrong on a four-arm rig and every collision
check is computed for an arm standing somewhere else.

Both rows share `scrub3d/live/arm_hw.py`'s port discovery rather than globbing
`/dev/cu.*` again. That matters: `py/arm.py::find_port()` matches on the
device NAME, so it also matches an Arduino or a GPS puck and returns whichever
sorts first. `arm_hw` matches on the USB vendor/product pair, names the chip,
and can see several boards at once.

The camera is an Intel RealSense D455. On macOS it gives colour only, which is
all `py/vision.py` reads; depth is blocked by the OS and unused. Details in
`docs/STATE.md`.

**`CAM=fake` runs everything with no camera and no arm** — including
`CAM=fake python tools/tune_dirt.py`, so the UV threshold controls can be
learned before the lamp arrives (the real thresholds still need real 365nm). A synthetic subject
(`py/fakecam.py`) is rendered behind the real `VideoCapture` interface, so the
whole path executes — pose, homography, state machine, socket, cartoon.
Verified end to end: remote arm → forearm detected → APPROACH → SCRUB →
RETREAT at 100%. It is not a substitute for the hardware go/no-go, but it
means nothing is blocked on having the arm on your desk.

## Keys

| Where | Key | Does |
|---|---|---|
| Python window | `SPACE` | emergency stop (`T:0`) |
| | `r` | clear estop |
| | `h` | home |
| | `s` | start a scrub cycle |
| | `1` `2` `3` | pop a splotch manually |
| | `q` | quit |
| Browser | `Enter` | start + **unlock audio** (required — browsers block sound without a gesture) |
| | `s` | arm one scrub cycle, from the projector |
| | `x` | **emergency stop**, from the projector |
| | `shift+C` | clear an emergency stop |
| | `1` `2` `3` | pop a splotch |
| | `e` | emote — cycles the character's reaction clips |
| | `d` | the death gag (flops over and stays down) |
| | `v` | next character — 12 people, same rig. Setup only: reloads the page |
| | `k` | next **skin tone** — 6 tones. Instant, no reload: safe mid-demo |
| | `o` | next **outfit** — 6 shirt + shorts palettes. Instant, no reload |
| | `j` | next **accessory** — glasses, sunglasses, a mask, a hearing aid, three canes, a crutch. Cycles back to none. Instant, no reload |
| | `f` | force the finale |
| | `c` | same as `f` — calls the finale (confetti, fanfare, green counter) |
| | `r` | reset splotches + counter, and stand the character back up |
| | `7` `8` `9` `0` | the four capabilities: shower, feed, vitals, voice |
| | `shift+7` | the drink beat — a glass of water, counted in sips |
| | `shift+8` | the pill beat — a glass rather than a bowl |
| | `b` | the measured body beside the person, on and off |
| | `g` | the handoff camera move: a slow 6s drift for the beat where the presenter and volunteer trade places. Changes no state. |
| | `n` | swap to a differently shaped person; the territories re-solve. **Pressing it mid-cycle ends the cycle** -- counter back to 0%, dirt restored. A new person has not been washed. |
| | `m` | microphone on and off, for voice control |

Every beat makes a sound once `Enter` is pressed: arming, the scrub, each pop,
the estop, the recolour keys, the finale. The scrub is deliberately the
quietest of them, so a pop lands over the top of it rather than beside it.
`docs/RECOVERY-CARD.md` lists what each one should sound like, because silence
in the wrong place is a diagnostic.

## Setup

```bash
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
./vendor.sh                       # downloads + commits every browser asset
python py/calibrate.py            # click 4 corners, then verify with a tape
```

Two things needing a reboot or a dialog, do them first:

1. **SiLabs CP210x VCP driver** — install, approve the extension in Privacy &
   Security, **reboot**. macOS 12+ has no built-in CP210x and the RoArm board
   uses two CP2102 chips; `/dev/cu.usbserial-*` never appears without it.
2. **Camera permission** — grant to Terminal/iTerm/VS Code (the app you launch
   from), not to `python`. macOS denies it *silently*: `isOpened()` returns
   False with no exception.

## Architecture

```
webcam ─┬─► py/scrubbot.py ──USB serial──► RoArm-M2-S (firmware solves IK)
        │        │
        │        └── ws://127.0.0.1:8765  EVENTS ONLY (~200 bytes)
        │                    │
        └────────────────────▼
             Chrome --kiosk (its OWN MediaPipe, its own camera read)
```

MediaPipe runs **twice**, deliberately. Not for speed — for liveness. The
projector page boots, runs, and demos with the entire robot stack dead. The
socket carries events only (`{limb, t, scrub, clean}`), so if it dies the
cartoon still mirrors the person perfectly; it just stops popping splotches,
and `1`/`2`/`3` is the rehearsed fallback. Verified under SIGKILL: cartoon kept
rendering at 240 app frames/s, manual keys still worked, auto-reconnected on restart.

### `scrub3d/` — the solver, and a second screen

The body model, the four-arm partition, the collision checker and the
kinematics. `tools/export_body.py` runs it to produce `web/assets/body.json`,
which is what the territory overlay, the counts panel and the voice spot
answer are all drawn from. It used to live only on its own branch, loaded
from a `/tmp` worktree that a reboot erases.

There is also an operator view worth opening when someone asks how the arms
decide who does what:

```bash
PATH="$PWD/venv/bin:$PATH" ./venv/bin/python -W ignore \
  scrub3d/viz.py --preview /tmp/viz.png && open /tmp/viz.png
```

Real Waveshare arm meshes posed by the same kinematics the collision checker
uses, with a header reporting how much of the person is reachable and which
arms can move at the same time. `scrub3d/README.md` explains the two flags,
both of which are load-bearing.

## Tests

```bash
bash tests/quick.sh                    # ~250s — pure logic, no browser tests
PY_PW=python3 bash tests/run_all.sh    # ~9min — everything, before committing
# Both leave a FAILING test's full log in /tmp/wheelgentic-kept-logs (cleared
# each run); run_all.sh also keeps any block that takes >=100s. The suites
# only tail 12-25 lines, so that directory is where the evidence is.
```

Twelve checks. Each one caught a real bug that syntax checks, console-error
checks and DOM probes all passed:

| Test | Caught |
|---|---|
| `test_calibration.py` | corner-order swap = 693mm silent error — *and this test could not fail* |
| `test_arm_protocol.py` | clearing an estop threw the arm at **9,513 mm/s** with the sponge on a forearm |
| `test_scripted_and_config.py` | splotches at 0.22/0.78 were physically **unreachable** by the sponge |
| `test_dirt.py` | saturation-only detection missed Glo Germ entirely; UV popping on occlusion |
| `test_record_replay.py` | a test that only passed on leftovers from a previous run |
| `test_consent_latch.py` | the arm started scrubbing **anyone** who put an arm in frame |
| `test_vision_real.py` | the framing constraint, proven: torso cropped → no pose |
| `test_mirror.py` | mirroring **swaps** the anatomical labels — it decides which arm gets scrubbed |
| `test_browser_boot.py` | asset 404s, gate, counter 0→33→100 |
| `test_browser_pose.py` | the idle animation **overwrote every tracked limb** |
| `test_splotch_placement.py` | dirt rendered on the shorts, not the arm |
| `test_projector.py` | 11px privacy text at 1080p; CLEANLINESS overlapping 0% |
| `test_degraded_boot.py` | a missing GLB hanging the page forever with dead keys |
| `test_integration.py` | replay mode importing mediapipe and dying |
| `test_resilience.py` | an fps probe reporting 121fps with a **dead** render loop |
| `test_runsh.py` | `run.sh` calling `python`, which does not exist on macOS |

**Every guard is plant-verified** — the bug is reintroduced and the test is
watched to fail. Guards that did *not* fire on the first attempt got fixed:
the splotch test only printed, the rate limiter asserted against its own
constant, and the off-limb check was being done by the wrong guard.

`tests/fake_roarm.py` is a pty-based RoArm simulator speaking the real ESP32
JSON dialect, so the arm driver is testable with no hardware. It proves
protocol only — see `docs/DIRECTIVE.md` §8 for what it cannot prove.

## Docs

| File | What |
|---|---|
| **`docs/TRUTH.md`** | **THE SOURCE OF TRUTH** — read first. What this is, what "done" means, the standing instructions, the two risks that can end the project |
| **`docs/ROADMAP.md`** | **the work source** — what is left, ranked by what a judge sees |
| `docs/STATE.md` | what actually works, measured vs assumed |
| `docs/DECISIONS.md` | settled calls, so they stop being relitigated |
| `docs/DIRECTIVE.md` | the hard-won-facts archive (§3) — every trap that cost hours |
| `docs/DEMO-SCRIPT.md` | the 2-minute demo, timed. This is the acceptance test |
| `docs/RECOVERY-CARD.md` | print it, tape it to the laptop |
| `docs/CALIBRATION.md` | the 90-second venue drill |
| `docs/OPENYAM-BRINGUP.md` | the OpenYAM arm: Linux host, CAN bring-up, and the five values that must be measured before it moves |
| `docs/WORK-QUEUE.md` | an append-only audit log. **Not** a work source |
| `docs/checkpoint.sh` | objective progress against the phase plan |

## Things that will waste your time

- `mediapipe==1.0.1` **SIGABRTs** on macOS arm64 before frame one. `try/except`
  does not catch it (exit 134). Pin `1.0.0`.
- `mp.solutions.pose` was **deleted** in 1.0.x — every tutorial online is broken.
- OpenCV 5.0 **deleted** `calibrateHandEye` while keeping the enums, so it
  fails at *call* time. Hence `opencv-contrib-python==4.12.0.88`.
- `three.core.min.js` is a **separate file** from `three.module.min.js` since
  r165. Nothing in the HTML names it, so a 404 fails an import inside the
  module and no module evaluates at all. The page now says `WHEELGENTIC FAILED TO
  START` and names `./vendor.sh`; before `42b296b` it was a blank screen with
  the only clue in a console.
- Kenney's GLBs reference `Textures/colormap.png` **externally** — all 24 of
  them, and it is the only file in that directory. Missing it = untextured
  white blob. (Older notes say `texture-a.png`; that was the previous pack's
  texture and it went with the asset. `DIRECTIVE.md` §3 keeps that as history
  on purpose.)
- `time.sleep(1/40)` in a loop gives **34.8Hz**, not 40. Use deadline pacing.
- A 4-point homography is exact by construction, so its residual **cannot**
  detect a mis-click. Only `verify()` (a 5th point + a tape measure) can.
- Kalidokit is deprecated in its own README and has no successor. Ready Player
  Me shut down in Jan 2026. Don't go down either road.
- **Saturation cannot detect Glo Germ.** It is blue-*white* (S=105), below warm
  skin (S=153). Blue-excess fails the other way: +63 for Glo Germ but **−140**
  for a yellow highlighter. Use hue (skin is always H=14–16) plus brightness.
- **Top-level `await` in an ES module is a trap.** If it rejects, the rest of
  the module never evaluates — no keyboard listener, no websocket, and the page
  hangs with no visible error.
- `$?` after a pipe is the *last* command's status. `cmd | tail` reports tail's
  exit code, so a failing test reads as a pass.
- **Every comparison against NaN is false**, so `if (n < 1e-8) return;` lets a
  NaN vector straight through. Write `if (!(n > 1e-8))`.
- **A non-interactive bash script started in the background ignores SIGINT**
  (POSIX). `kill -INT` on it does nothing; use SIGTERM. Interactive ^C is fine.
- **three.js never re-arms `setAnimationLoop` after a throw** — one bad frame
  freezes the projector permanently with no visible error.
- **`mixer.update()` must run BEFORE you stamp a tracked pose**, or the idle
  clip overwrites every limb you just posed.

## Credit

Character: [Kenney Mini Characters](https://kenney.nl/assets/blocky-characters) (CC0).
