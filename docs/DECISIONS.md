# DECISIONS.md — settled calls, so they stop being relitigated

**Check this before proposing anything that sounds new.** Most of it has been
decided, often with measurements that cost hours. Companion to `docs/TRUTH.md`.

Each entry: the decision, why, and what would reopen it.

---

## PRODUCT

### One arm, not four
Four arms means four serial ports, four calibrations, four workspace envelopes,
and inter-arm collision avoidance — where the failure mode is two arms hitting
each other over a person. The software scales (`Arm` is per-instance); the
calibration and collision work does not.
**Say:** "this is one of four; the software is identical for each."
**Reopens if:** the team builds and calibrates four, and someone solves
collision avoidance.

### RGB + cartoon, not point cloud
The teammate's instinct (privacy at the sensor) is genuinely stronger, but every
depth camera is ruled out on Apple Silicon: RealSense needs an arm64 source
rebuild plus sudo, ZED requires CUDA, Azure Kinect is discontinued.
**What we say truthfully today:** every frame is processed on-device, nothing is
stored, nothing leaves the laptop, and the only thing anyone sees is the cartoon.
**Reopens if:** a working depth camera appears. The homography path is unaffected.

**That condition has now half-fired, 2026-09-19.** The D455 gives depth on the
GB10 Linux box — see the entry below and `STATE.md` §3. The paragraph above is
still right about Apple Silicon and still right about what the demo does; it is
wrong if read as "no depth anywhere". Nothing in the demo has changed, because
the demo runs on the Mac and nothing in it consumes depth. This stays decided
until someone moves the demo to the Linux box, which is a separate call.

### Touch (`contact_depth_mm: -5`), not hover
Hovering removes the torque contact sensor, so the cleanliness counter goes back
to being a timer, and it removes the safety story's best line.
**Note:** touch is one config edit; hover is a config edit **plus a restart**,
because `--no-contact-gate` is argparse-only with zero `CFG.data.get()` reads.
**Reopens if:** the team decides hover for safety. Decide before rehearsing.

### Water: the team's call
The brainstorm says NO WATER (damages the motors). The bucket dip is the funniest
two seconds of the demo. A dry sponge keeps everything except the dip.
**Their hardware, their call.**

### "Point at an area to clean more" — CUT
It would work (MediaPipe already carries finger landmarks, `project_to_limb`
already returns the `t` the splotches use). It cannot be demoed: the volunteer
would have to reach across into a moving robot's envelope mid-scrub.
**Reopens if:** a second limb is ever scrubbed — pointing with the free hand at
the *other* arm is a real interaction.

---

### No "SOIL DETECTED" readout. The dirt is authored, and saying otherwise is a fake claim
The vision asks the system to "identify the most dirty areas to clean", and the
screen never shows the machine deciding where dirt is -- a judge sees three
splotches appear and get cleaned. The obvious fix is a readout announcing
detected soil. It was scoped and dropped.

There IS a real detector: `py/dirt.py` finds fluorescent tracer, projects each
blob onto the forearm and sends the positions over the wire, and the splotches
really do move to where the tracer is. But `config.json` ships
`dirt_mode: "scripted"`, so in the demo as run nothing observes anything and
the three positions are constants chosen to sit inside the sponge's reach.

The tempting shortcut is worse. The body bake has a `klass` field per cell with
honest-sounding values (core, fringe, contested, unreachable), but those mean
"which arm can work here, and how well" -- a reach question, with nothing in it
about the person's actual state. Framing 66 core cells as "needs the most
attention" would be a category error wearing a real number, which is the worst
available option: it survives a glance and collapses the moment a judge asks
what the word means.

If it is ever built, the only honest version is gated on a real UV frame having
arrived and reports what the detector measured. `py/dirt.py` says it in its own
header: never claim it is running if it is not, because one caught overclaim
discounts everything else on the screen. This project has already pulled one
detector-derived number off the HUD for being untrustworthy; do not put another
back casually.


## THE THREE STANDING RULES — see `TRUTH.md` §3b for the full text

Repeated here because they are the ones most often violated by accident.

### Take it from online. Building it yourself is the failure case.
Search first, every time: GitHub, Kenney, Poly Pizza, Sketchfab, itch.io,
three.js examples, Quaternius, Mixamo, freesound. CC0 or MIT only. Adapt what
you find — rename, recolour, rescale. Only build when a written search turned
up nothing, and say what you searched for.
**Cost of ignoring it:** two discarded characters (a Minecraft-looking blocky
pack, then a hand-sculpted blob) before anyone searched. The pack that shipped
was found in minutes and came with 32 animations.
**Already downloaded and unused:** 4 wheelchairs, 10 accessibility aids
(canes, crutch, glasses, mask, hearing aid), and a 13th character — 26 models
in the zip, 12 extracted.

### Looking great outranks proving it works.
A judge sees the projector for 120 seconds. They cannot see a test suite.
A feature that looks incredible and is slightly fragile beats one that is
bulletproof and dull. The suite is a regression gate; it does not grow.
The right question is "does it look good on the projector", answered by a
screenshot, not "is it verified", answered by an assertion.

### The character is customizable and that is a feature, not a nicety.
All 12 models share one 512×512 atlas of 162 flat swatches
(`web/assets/Textures/colormap.png`). Skin tones, clothing colours, greys and
hair are separate patches. Recolouring = repaint a swatch or move a UV island.
No new model, no new texture.
**Why it matters beyond polish:** the pitch is that the cartoon protects the
volunteer's dignity. A volunteer who sees their own skin tone gets "that's me",
which is a stronger claim than "that's a generic guy".

---

## VISUAL

### The character is Kenney Mini Characters (CC0), not hand-built
Tyler, twice: *"i cant emphasize enough to not make things yourself if you can
just take it from already existing things online."* Two wrong shapes came first
(the blocky pack = Minecraft; a blob hand-sculpted from capsules) before
actually searching. The mini pack ships a skinned rig and 32 authored clips.
**Rejected on the way:** three.js RobotExpressive (a robot being washed by a
robot kills the dignity pitch); poly.pizza's Quaternius mirrors (importer strips
rigs — `skins:0, anims:0`).

### No wet-floor reflection — TRIED AND REVERTED
A `Reflector` plane over the floor, so the arms and the light shaft would
double in a wet sheen. Vendored, built, measured, reverted the same hour.

**It cost 40% of the frame rate.** Headless, during a scrub: 20 fps with the
steam alone, 12 with the reflector added. It renders the scene an extra time
into a target, and `OutlineEffect` already renders twice, so the page went to
four passes. That is a real risk on venue hardware nobody has profiled, for
decoration.

**And it looked worse.** At the opacity where you could see it at all, it read
as a mirror rather than a wet floor: the floor's warm colour vanished under a
muddy double image and the arm mounting plates appeared to float. A weaker
setting is invisible; a stronger one is a mirror. There was no middle.

The steam through the light shaft was the other half of the same proposal and
it stayed: one draw call, and it makes the beam read as a beam.

### The operator view's arms are Waveshare's own meshes (MIT), not modelled
`scrub3d/assets/roarm/` carries five STLs and the URDF from `roarm_description`,
whose `package.xml` declares MIT. They came in with the scrub3d merge on
2026-09-20 and they are the reason that second screen is worth opening: the
arms are the real RoArm-M2-S geometry, posed by the same forward kinematics the
collision checker uses. If a safety margin were wrong you would see the link
poke out of its own capsule, which is a correctness check you get by looking.

The projector's arms are still the stylised ones, deliberately. A photoreal
arm next to a cartoon person is the wrong register for the dignity pitch, and
the cartoon is the privacy mechanism.

### Anticipation on the arm descent — REVERTED
A 0.50 rad pre-move is 1.45° of shoulder rotation = **7.6px** of sponge travel,
against the stroke roll's **41px**, and a stroke tick fires inside the
anticipation window. Drowned by a motion 5.4x larger. 1.00 rad only reaches
16.4px while costing >17% of descent travel.
**Do not rebuild it.** The code is reverted; the measurement is the reason.

### Per-splotch silhouettes — kept but not load-bearing
Three procedurally seeded textures were verified by the numbers they were
designed for (three distinct uuids) and did **not** work: at 29px spacing each
outline falls inside its neighbour, so only the union's boundary is visible.
The scale change (`0.78 → 0.32`) is what did the work.
**The lesson:** I validated the seeds rendered side by side with gaps — an
arrangement that never occurs on the limb. Test the arrangement that ships.

### NO WHEELCHAIR, AND NO PROP IN ITS PLACE. Tyler

**"why is there a wheelchair? i didnt ask for that"** and then **"i want it
gone"**. It is gone: the load block in `main.js` is removed and nothing in
`web/` references the model.

I had added it on my own initiative, reasoning that the screen never showed
what the project is for. That reasoning was wrong on its own terms, separately
from not being asked for. A wheelchair beside the volunteer puts a disability
signifier on a person who never agreed to carry one, and it reads as "this is
for wheelchair users" when the demo is somebody resting a forearm on a table.
The caregiving pitch is the presenter's to make in words. The set does not get
to make it about whoever sits down.

**Do not re-add it, and do not substitute a cane, a crutch or any other
mobility prop as scenery.** That would be the same decision with a different
model. `vendor.sh` still vendors them; nothing loads them, which is correct.

The 8 wheelchair animation clips stay unused for the older reason too: seating
the character needs an authored pose (`sit` and `wheelchair-sit` only rotate
limbs, rootY stays 0.550), and that is the build-it-yourself this project is
told not to reach for.

**The earlier version of this entry argued FOR the prop** and said the chair
"is already in the scene". Kept in this form rather than deleted so the
reversal is legible: I made a call about how to represent disabled people
without being asked, and it was not mine to make.

### "Sprite under the spacing gives a gap" — FALSE RULE
At 1080p the sprite is 26.9px against 29px spacing, so the rule predicts clear
arm, and painted pixels show one contiguous run at every framing. Lobed textures
paint past their nominal box. What separates the splotches is silhouette, not
spacing.

### Phase indicator: REOPENED AND SHIPPED
This entry said CYCLE RUNNING / IDLE only, because Python's FSM state never
crossed the wire and a richer label would have been reporting the browser's own
1600ms timer: a status assertion about a machine, derived from a guess, on the
same screen that claims "0 FRAMES STORED".

**It named its own reopening condition, and that condition is now met.**
`EVENT["phase"]` is published from `vision_loop`, once per frame, immediately
before the state dispatch. Not from the thirteen sites that assign `state`:
`_reset_cycle_state`'s docstring records what happens when one of several paths
forgets a shared step, and a fourteenth transition added later would ship a
stale phase. One write per frame cannot desync.

The projector shows APPROACHING / SCRUBBING / RETURNING while a cycle runs.
Measured end to end: IDLE, APPROACHING at 0.2s, SCRUBBING at 1.6s, RETURNING at
9.8s, IDLE at 12.2s, matching the server's own APPROACH/SCRUB/RETREAT log. Zero
page errors.

**It is a report, not a timer, and it degrades on both axes.** The label falls
back to CYCLE RUNNING whenever the phase is missing or unknown, so an older
server still reads correctly. `cycleLive` still clears on `ws.onclose`.

Both conditions this entry demanded were executed, not argued:
- **Acceptance test:** killed Python mid-cycle with the line reading SCRUBBING.
  It dropped to IDLE inside 1s and stayed there through +8s.
- **Plant-verify:** disabled the publish server-side. No phase label appeared at
  all and the line degraded to CYCLE RUNNING. Restored byte-identical.

**Do not turn this into a timer.** If `EVENT["phase"]` ever stops arriving, the
correct behaviour is the two-state label, which is what the code does today.

---

## ENGINEERING

### The Linux box is required, and macOS cannot be talked into either half
Two separate things the arm and the sensor need are impossible on macOS, for
two unrelated reasons. Neither is a missing driver someone can go install, and
both were re-confirmed on 2026-09-19 rather than taken on faith.

**SocketCAN does not exist on macOS.** The CANable 2.0 runs gs_usb firmware,
which is a native USB CAN protocol, not a serial port. Linux turns that into a
`can0` network interface with a kernel module; macOS has no such concept, so no
`/dev/cu.*` device appears. That absence is correct behaviour and there is
nothing to debug. The escape hatch — reflashing the adapter to slcan so a serial
port shows up — is ruled out separately in `OPENYAM-BRINGUP.md`: ASCII framing
over a serial line will not hold 6 joints at 1Mbit/s.

**librealsense cannot claim the D455 on macOS.** `UVCAssistant`, a system
daemon, takes all five of the camera's video interfaces at plug-in. librealsense
then gets `RS2_USB_STATUS_ACCESS` and cannot open the device. The same camera on
the GB10 opens fine and streams 848x480 depth. This is not an Apple Silicon
build problem — Homebrew's librealsense 2.58.4 is installed on the Mac and still
cannot get the device, because something else already has it.

So the box is not a convenience or a speed thing. It is the only place either
capability can run at all. **Do not re-litigate this by proposing a macOS
workaround**; both paths above have been walked to their ends.

**What is NOT decided here:** whether the demo itself moves to the box. Today it
runs on the Mac, it does not consume depth, and it does not command the arm. The
box is where arm and depth work happens, nothing more.
**Reopens if:** Apple ships SocketCAN, or a way to make UVCAssistant release the
camera turns up. Neither is expected.

### The depth camera decides where the projector camera goes
The opening shot is head-on (`SHOTS.wide.orbit = 0`), and that is the rig
talking, not taste. `scrub3d/frames.py::world_from_camera()` builds the world
frame by taking the subject's facing direction to **be** the camera's backward
axis — "the person faces the camera" is the assumption the body scan, the
four-arm partition and every reach verdict are solved under. A projector view
from anywhere else shows the audience a geometry the solver never used, so the
arms on screen stop matching the arms the governor reasoned about.

This **reverses** the earlier swing to `orbit 0.62`. That angle was chosen for
a real reason: head-on, the seated body covered the chair almost exactly and
nothing read as a wheelchair. The fix for that is the chair's own design, not
a camera angle that disagrees with the sensor.

`tools/export_body.py` writes the measured pose into `body.json` as `camera`
(height from the floor fit, never typed in), and `applyCameraPose()` in
`web/main.js` reads it at boot. No capture present means the defaults stand and
the block is marked `measured:false`, so the page can tell a real measurement
from a placeholder instead of showing a guess as fact.

### MediaPipe runs twice, deliberately
Not for speed — for liveness. The projector page boots, runs and demos with the
entire robot stack dead. The socket carries events only (~200 bytes), so if it
dies the cartoon still mirrors the person; it just stops popping splotches, and
`1`/`2`/`3` is the rehearsed fallback.
**Do not "optimize" this into one pose source.**

### The estop is not a place for polish
Five separate critical bugs, every one found by an adversarial audit *after* the
previous fix, every one sharing a root cause: estop state was a bare bool
mutated from three threads with no atomicity. It is now guarded by
`_estop_lock` + `_estop_gen`, recovery is unconditional, and every safety field
is declared at class level.
**Read `DIRECTIVE.md` §3 before touching `estop()`, `clear_estop()`, `_send()`
or the FSM gate.**

### The test suite is a regression gate, and it does not grow
Tyler: *"do not work on tests work on improving it immensely."*
Run it before committing. Do not add to it for its own sake.

**It has grown anyway: 26 tests when this was written, 32 on 2026-09-20.**
Recording that rather than quietly editing the number. Every one of the
six was added in the same shape: a feature landed on the screen, a way it
could silently stop working was identified, and the check was planted red
before it was trusted green. None were added to raise a count or to cover
code that nothing on the projector shows.

The decision still holds in the direction that matters. A session spent on
tests is a session not spent on the demo, and the standing rule ranks a
visibly broken screen above every bug. But a guard written in the same
hour as the feature it guards is part of landing that feature, not a
separate body of work.
**This decision is the one most likely to be violated by accident**, because
test work always feels productive and always produces a clean green number.

### Config coercion is finished
Seven numeric keys go through `_num()` with a default fallback; `dirt_mode` is
normalised and warns once on an unknown value. Three keys are deliberately left
bare: `mirror` and `use_water_bucket` are truthiness-only, `serial_port` is a
string-or-None by design.
**Do not extend this further.** The workspace box already bounds a typo'd depth.

---

## PROCESS

### `WORK-QUEUE.md` is an audit log, not a work source
It is append-only, self-feeding via 17 generators, and it swallowed 20 hours
after Tyler's redirect. Records go in **after** work, never as the work.
`ROADMAP.md` is the work source.

### Plant-verify, red then green
A plant that has never been red proves nothing. Run the unmodified test first as
a baseline, then plant, then restore and verify the tree is byte-clean.
Plant *in place* — a path-anchored test (`HERE = dirname(__file__)`) cannot be
copied to another directory; it dies at import and the empty log reads exactly
like a plant that failed to fire.

### Two interpreters, pick the right one
`./venv/bin/python` has cv2 + mediapipe + pyserial. `python3` has playwright.
`run_all.sh` picks per test. Running a cv2 test under `python3` gives
`ModuleNotFoundError` and an empty log that looks like a failure.
