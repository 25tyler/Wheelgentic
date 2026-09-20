# STATE.md — what actually works, and how we know

**Measured, not believed.** Every row says what was run and what it produced.
Companion to `docs/TRUTH.md`.

The distinction this file enforces: **verified** means someone executed it and
read the output. **Assumed** means it is probably fine and nobody has looked.
Most of this project's real bugs lived in the second column.

---

## 1. VERIFIED BY EXECUTION

| Thing | Evidence |
|---|---|
| Whole demo, no hardware | `CAM=fake ./run.sh --no-arm` — remote arm → forearm detected → APPROACH → SCRUB → RETREAT at 100% |
| Browser boots, 19/19 assets serve | zero console errors, zero failed requests |
| Counter 0 → 33 → 67 → 100 + confetti finale | screenshot + DOM assert |
| Splotches land ON the arm | screen-space assert, perpendicular distance to the limb's own axis |
| The recovery card's own sequence works end to end | **MEASURED 2026-09-21.** Started a scrub, pressed `x` to stop, `shift+C` to clear, `r` to reset, then `1` `2` `3` to pop the dirt by hand: the counter reaches **100%** with **zero page errors**. That is the exact path an operator takes when the demo has gone wrong, and it had never been run as one sequence. |
| Render cost has headroom, but not unlimited | **MEASURED 2026-09-21** during a scrub, headless (software rendering, no GPU — a real machine is far faster): **21 fps, 92 draw calls**. The steam costs one draw call and about 1 fps. A `Reflector` wet floor was tried and cost **40%** (21 down to 12) because it renders the scene an extra time on top of `OutlineEffect`'s second pass; it was reverted, see DECISIONS. Anything that adds a whole extra scene pass should be measured before it is kept. |
| The bake solves a ring, not the measured rig | **MEASURED 2026-09-21.** Solving the projector's bake against the four measured arm positions in `scrub3d/config.json` gives arm 0 **zero** cells and arm 3 twelve, with coverage 0.362. The synthetic ring gives 273/135/127/164 and 0.392. Two arms working and two watching is the wrong picture under a panel arguing four agents divide a job, so the ring stays. The operator view solves the real rig, which is why its schedule differs. Re-measure if the arms are ever re-placed. |
| Splotch placement under motion | worst case 0.63 of the allowed distance during a full scrub, 46 samples |
| ~~`test_splotch_placement` fails on 4 of 12 characters~~ | **FIXED 2026-09-21.** It was never a placement bug. The splotch sat **1px off the limb's centre line** -- dead centre -- and five pixels past where the test put the shoulder, giving `u = -0.06` against a window that stopped at `-0.05`. Two measurements of the same bone disagreeing at its end: `avatar.js` places through a fraction of the bone's vertex box, the test projects that box's extremes, and a box corner is not a joint once the limb is at an angle. Window is `0.12` now (half the splotch spacing, so one still cannot be credited to the wrong end) with the reasoning written down. All 12 pass; plant-verified by shoving them off the limb. |
| Sponge meets the dirt | min gap 0.0272 world units at t=3.90s, 46 samples across a scrub |
| Python → browser events | `ARM ● LINKED`, correct event shape |
| Survives Python SIGKILL | cartoon kept 240 app frames/s, manual keys worked, auto-reconnect |
| Estop stops the cartoon too | forearm `rotation.z` stroke stops within one frame of `x` |
| Estop refusal is honoured | server refuses to arm while estopped; page cancels its optimistic choreography |
| Arm pump rate | 39.9 Hz with absolute-deadline pacing (naive sleep measures 31.3–31.7) |
| Rate limiter | 6.00mm per 25ms tick = 240 mm/s, including estop recovery |
| Workspace clamp bounds a bad depth | `contact_depth_mm: -500` → arm goes to `zmin` 25.0, warns at 480mm clamp |
| Calibration corner-order guard | fires on a TL,TR,BL,BR swap (693mm error caught) |
| Scene vs HUD at 3 framings | legs clear the overlay by 13px of painted pixels at the tightest; robot clears the right edge by 228–442px |
| Projector legibility | 1080p / 720p / 4:3, text-extent overlap test, no collisions |
| Projector legibility, re-read | passes at all three. **720p reports `clearance 0px` between the feet and CLEANLINESS and that is fine**: the clearance number compares vertical extents only, and at 720p the label's ink ends at x=220 while the feet start at x=532, so they never share a column. Confirmed by screenshot, not by the number |
| UV detector math | hue+brightness on synthetic frames; catches Glo Germ AND a yellow highlighter |
| UV mode through the real FSM | latches 3 tracers, pops as they fade, reaches 100 |
| `dirt_mode` is case-insensitive | `"UV"` enters UV mode; `"vu"` warns once and runs scripted |
| Numeric config keys coerce | a string or null in any of 7 numeric keys falls back to the default |
| Full suite | **36/36, ALL CHECKS PASSED, 2026-09-19.** Covers the swung-round wide shot, the scan panel clearing between beats, and the splotch fix. The splotch test prints "PLACEMENT ASSERTIONS PASSED for all 12 characters" at 3px off-axis against a 10px allowance, and it is plant-verified both ways: restoring the old stand-off turns all twelve red. ~750s. The arm protocol passes against its 210s budget; its 15% abort rate was diagnosed as load-induced retries, not a wedge. |
| Backup video | `recordings/backup.mp4`, re-recorded against the current build, content-hash stamped |

---

## 2. VERIFIED ON THE OPERATOR'S REAL KEY PATH

The demo script has 12 beats. Most tests call functions directly rather than
pressing keys, so "the function works" and "the beat works" are different
claims. These are the beats actually driven through the keyboard:

| Beat | Status |
|---|---|
| `0:00–0:48` — the cartoon mirrors the presenter | ⚠️ **UNVERIFIABLE HERE** — see below |
| `0:48–0:58` — debug window reads ARMED, elbow/wrist dots on the arm | ✅ driven |
| `0:58–1:06` — three splotches visible on the forearm | ✅ driven: 3 of 3 present, counter 0% |
| `1:06–1:10` — `s` flashes ARMED | ✅ driven: `#link` reads `ARMED`, reverts to `ARM ● LINKED` at 1.6s |
| `1:10–1:44` — the scrub | ✅ driven: sponge travels 0.331 units in Y, 0.287 in X; counter 0→39→75→95→100%; 3 of 3 splotches gone |
| `1:22–1:32` — the `1` rescue | ✅ driven: gone 0→1, counter 33% |
| `1:32–1:44` — "tracking her actual forearm" | ✅ sponge-to-dirt measured |
| `1:44–1:52` — finale, 100% | ✅ driven: 100%, 3 of 3 gone. **The number holds 2.26s, not the 8s the beat is long** (measured 2.20 / 2.41 / 2.17): RETREAT fires `fire_reset()` 2.5s after the scrub ends, on every path. Script corrected to match |
| `1:52–2:00` — `r` returns the splotches | ✅ driven: 0% → 100% → 0%, 0 page errors. `r` restores the splotches and stands the character up; **it does not reset the counter**, which cleared itself during the previous beat |

**Eleven of twelve beats now driven on the real key path**, in one continuous
session, 0 page errors throughout. R1 in `ROADMAP.md`.

### Sound, on the same key path (R6) ✅

Driven by keypress, not by calling `play()` directly, so these are beats rather
than functions:

| Sound | Status |
|---|---|
| `s` → ding | ✅ driven |
| the scrub → a rubbing bed | ✅ driven: 3.3–3.5 per second on the 260ms tick, 29 in one cycle |
| a pop → squish + sparkle | ✅ driven: peak 0.149 + 0.208 |
| `x` → thunk | ✅ driven |
| `shift+C` → ding | ✅ driven |
| `k` / `o` → click | ✅ driven |

**The bed sits under the pop, measured:** bed peaks cluster at 0.074–0.081
across 27 sounds while a pop inside the same cycle reads 0.16. Before the mix
change they were the same loudness and all three pops in a cycle landed within
2–3ms of a bed sound.

Both canvases stayed alive through every cycle, so the sound is not throwing
inside the callback it shares with the foam. 0 page errors.

### Five consecutive rehearsals — the demo script's own bar (R2) ✅

> "Rehearse this five times. The version that runs cleanly five times beats
> the version with an extra feature that runs twice."

Run: five rehearsals of `0:48–2:00` on **one page load** (a reload
between runs would hide exactly the leak this looks for).

| Check | Result |
|---|---|
| ARMED flash | every run |
| Splotches reached 3/3 | every run |
| Reset to `0%` and 0 gone | every run |
| Samples with counter > 0 while nothing popped | **0** |
| Page errors | **0** |
| Draw calls | 26, 26, 26, 26, 26 |
| Triangles | 1119 × 5 |
| Geometries / textures | 25 / 13, flat across all five |
| Frames per rehearsal | 4044, 4042, 4050, 4050, 4048 |
| Seconds per rehearsal | 16.9, 16.8, 16.9, 16.9, 16.9 |

Nothing accumulates across cycles and nothing degrades. The counter's
intermediate values differ between runs (`43/76/98` vs `12/52/82`) — that is
sampling phase against a live FSM, not instability; every run still ends at
100% with 3 of 3 gone.

**Scope:** this rehearses the operator-driven half. The `0:00–0:48` mirroring
beats remain unverifiable on this machine, below.

### The one beat that cannot be tested on this machine

`0:00–0:48` is four beats — 40% of the demo, and the entire basis of the
privacy pitch ("that's me, that's the only thing anyone sees"). It claims the
cartoon mirrors the presenter live.

**It cannot be verified headless.** The browser runs its own MediaPipe against
its own camera; headless Chrome has none, so `getUserMedia` throws and
`main.js:708` logs *"camera denied/missing — avatar will idle"*. Chrome's
`--use-fake-device-for-media-stream` makes the pose path initialise (console
prints `browser pose up`) but the synthetic stream is a rolling test pattern
with no person in it, so MediaPipe finds no pose. Measured: limb quaternion
span is **identical to four decimals** with and without the fake camera
(0.0119 / 0.0119 / 0.0144) — that is the idle clip, not mirroring.

**The path itself is proven.** `test_browser_pose.py` injects landmarks through
the shipped `avatar.update()` and measures the arm swinging **0.591** between a
hanging and a swung-out pose, plus 0.0085 of idle-only movement over 2.5s
(which brackets the numbers above). So the code mirrors correctly; only the
end-to-end camera claim is untested.

**What would close it:** a real camera, or a recorded video of a person fed to
Chrome via `--use-file-for-fake-video-capture`. Until then the honest statement
is "the mirroring path is proven by injection; the camera end of it has never
run on this machine."

**Everything else in that window HAS been looked at, and holds
up.** Only mirroring is blocked; the rest of what a judge sees for 48 seconds
was screenshotted rather than asserted on, and four candidate weaknesses all
closed against work already done. The character is not frozen with nobody in
frame: a burst at 300ms changes 4 to 8 percent of its pixels between every
consecutive pair. Sampling it at 11 to 14 second gaps shows an identical pose
in every frame, which is aliasing against a sway period near 3.7s, not
stillness, so do not size an amplitude change off widely spaced screenshots.
The cleanliness bar is right at zero: `#bar` is the dark track and `#fill` is
the fill, genuinely 0px wide. The three splotches read as one brown mass from
the demo camera, which `avatar.js` records as measured and accepted (the
textures are lobed, so no placement or scale separates them, and distinct
silhouettes were tried and did not break the mass up). The robot arm sits
parked and inert for the whole opening, holding 11 percent of frame area;
giving it something to do was built, measured at 6 to 18 px against a 7.6 px
invisibility threshold, and reverted, because the two joints in series cancel.

---

## 3. NOT VERIFIED — the honest column

| Gap | Why it matters |
|---|---|
| **No real arm, ever** | Two arms, neither exercised. The RoArm-M2-S driver (`py/arm.py`) has its wire format, rate and estop logic proven only against a pty simulator. The Anvil OpenYAM (`py/openyam.py`, 6 joints, Damiao servos, CAN at 1Mbit/s) has never had a frame reach a motor: its wire format is byte-identical to two independent implementations and `tests/test_damiao.py` passes with no hardware, but that proves the bytes, not the motion. |
| **The OpenYAM's geometry is five guesses** | `JOINT_MOTORS`, `JOINT_LIMITS`, the link lengths, `HOME_Q` and the joint signs are all marked `CALIBRATE` in `py/openyam.py` and all invented. Anvil has not published the URDF (`OpenYAM_description` says "Coming Soon"), so nothing was derived — the numbers are a plausible split of the published 610mm reach. Every IK result and every "that target is reachable" claim is arithmetic against made-up links. A wrong motor model does not raise; it silently rescales torque. Procedure to measure them: `docs/OPENYAM-BRINGUP.md`. |
| **The OpenYAM is not wired into `scrubbot.py`** | `scrubbot.py:1283` builds `arm.Arm` (the RoArm) and nothing selects between the two. `OpenYamArm` was described as a drop-in for `arm.Arm`; checked against every call site, it is not — `arm.target` is a 6-tuple of joint radians where the RoArm's is a 4-tuple of cartesian mm, and `arm.contact` is never assigned, so the sponge-is-touching signal that gates splotch pops would read False forever. The torque cutout also becomes a silent no-op, because it reads keys named for the RoArm's joints. Do not swap the import and assume it runs. The four mismatches are listed at the end of `docs/OPENYAM-BRINGUP.md`. |
| **There is a Linux host now, and CAN is still unrun on it** | **MEASURED 2026-09-19.** The host is an NVIDIA GB10, not the Raspberry Pi this row used to promise: aarch64, 20-core Cortex-X925, 121GB RAM, Ubuntu 24.04.4, kernel `6.17.0-1022-nvidia`. Reachable as `ssh wg` with key auth. The repo is synced to `~/Wheelgentic` and the venv is `~/wg-venv`. All 6 `scrub3d` backend modules import there, against prebuilt aarch64 wheels: mediapipe 1.0.0, opencv-contrib-python 4.12.0.88, numpy 2.1.3, scipy 1.18.1, python-can 4.6.1, websockets 17.1, pyserial 3.5, pyrealsense2 2.58.4. **CAN is still unrun.** The CANable 2.0 is not plugged into the box — it is still on the Mac — so no CAN interface exists (`ip -br link show type can` prints nothing). The kernel module is present at `/lib/modules/6.17.0-1022-nvidia/kernel/drivers/net/can/usb/gs_usb.ko.zst` but is not loaded, and loading it or running `ip link set can0 up` needs a password this account does not have unattended. So CAN bring-up at 1Mbit/s, `candump` seeing Damiao frames, and MediaPipe's frame rate on this CPU are all still written and unrun. |
| **Splotches now DO pop one at a time, as of 2026-09-19** | this row used to say the opposite and told you to accept it. The cause was real: pops gate on `arm.contact` (`scrubbot.py:851`), and `--no-arm` builds the arm with `dry_run=True`, so no serial port opens, `poll_feedback` returns nothing, `contact` stays False for the whole scrub and every splotch arrived together from the end-of-cycle sweep. `--no-arm` now implies `--no-contact-gate` (`scrubbot.py:1266`), so the proximity test does the gating instead. Measured on the documented command: 3 in-scrub pops where there were 0, counter climbing 37/55/70/86/100. **A collapsed scrub IS now a regression** |
| **The D455 gives RGB on macOS, and only RGB** | **MEASURED.** The Intel RealSense D455 streams colour at about 30fps through the ordinary `cv2.VideoCapture` path in `py/vision.py`, and pose landed on 126 of 180 frames. That is real hardware, not the synthetic subject. **Depth is blocked on macOS, and only on macOS.** `UVCAssistant` claims all five of the camera's video interfaces, so librealsense gets `RS2_USB_STATUS_ACCESS` and cannot open the device. Homebrew's librealsense 2.58.4 is installed but ships C++ only, no Python bindings. This row used to read "depth is blocked" without the qualifier, and that was wrong the moment a Linux host existed — see the next row. |
| **Depth works on the GB10** | **MEASURED 2026-09-19 on the Linux box, the thing macOS cannot do.** librealsense claims the D455 there (serial 141322251598, firmware 5.17.3.10) instead of losing it to a system daemon, and `pyrealsense2` 2.58.4 opens it from `~/wg-venv`. One capture gave 848x480 z16 depth with 85–87% of pixels valid, values spanning 380mm to 65535mm, alongside 848x480 bgr8 colour from the same pipeline. **The camera was aimed wrong for that capture** and nothing has re-aimed it: top of frame 1218mm, middle 15061mm, bottom 8901mm, and `frames.solve()` returned "no floor plane found", which is a camera on its back or pointed at a ceiling. So the claim is "the depth stream opens and carries plausible data", not "the body scan solves". **The camera is not plugged into the box right now** — as of this writing `lsusb` shows no `8086:` device and `rs.context().query_devices()` returns 0, so the numbers above are a past reading, not something re-runnable this minute. Nothing in the demo consumes depth today: `PoseFeed.read()` returns `(frame_bgr, elbow_px, wrist_px, dt)` and never imports `pyrealsense2`. |
| **The rest of the vision path is still synthetic** | A real colour stream proves pose detection. It does not prove the scrub: the demo's end-to-end runs use the rendered subject in `py/fakecam.py`, and nobody has stood in front of the D455 and had the arm approach them. |
| **No real 365nm UV tuning** | thresholds are measured on synthetic frames; the venue lamp will differ |
| **`homography.pkl` is SYNTHETIC** | must be deleted and recalibrated before the arm nears a person |
| The scan really does follow the person | **MEASURED 2026-09-20.** The body overlay's lean is 0.646 of the avatar's torso lean, against the 0.65 the code asks for, sampled inside the page over 2.5s. The torso angle itself comes from the live shoulder landmarks, so with a person in frame the scan tilts with them. Standing still it is about a degree and reads as nothing, which is why `DEMO-SCRIPT-V2.md` now has the presenter ask the volunteer to lean at 0:40. This is a DIFFERENT claim from the handoff row below: the tracker follows the one person it has, and does not switch between two. |
| ~~Subject handoff is unmeasured~~ | **MEASURED — tracking does NOT follow the person who moves.** Two separated people in frame, real detector: the pick is identical whether the left or the right one waves (nose x 0.168 vs 0.167). `DEMO-SCRIPT.md` now says one person in frame at a time. Detail in `ROADMAP.md` R3 |
| **Nobody has HEARD the demo** | every sound is measured by level and rate, never by ear. A headless browser emits no audio, so "it sounds right" is a parameter choice, not a finding. Closing it costs one person, one `Enter`, and one cycle |
| ~~Five consecutive rehearsals~~ | **RUN — see §2.** Five rehearsals of `0:48–2:00` on one page load, 0 page errors, draw calls and textures flat across all five |
| ~~Nine of twelve beats~~ | **ELEVEN of twelve now driven — see §2.** The remaining one is `0:00–0:48` mirroring, which needs a real camera |

---

## 4. THE VISUAL STATE — what a judge actually sees

From `VISUAL-OVERHAUL.md`, which was written after screenshotting the projected
frame instead of reading assertions about it.

| Item | Status |
|---|---|
| P0 — the room (floor, horizon, contact shadow, rim light) | ✅ DONE |
| P1 — the robot arm rebuilt as a machine, not a stick | ✅ DONE |
| P2 — framing: subject fills 54% of frame height (was 38%) | ✅ DONE |
| P3 — the dirt reads as separate spots | ⚠️ **PARTIAL** — three lobes distinguishable, still touching; connected-component analysis finds 2, not 3 |
| P4.1 — droplets during the scrub | ✅ shipped as `sudsAt` |
| P4.2 — suds build-up as cleanliness rises | ✅ shipped |
| P4.3 — sparkle ping on a cleaned patch | ✅ already satisfied by the pop burst |
| P4.4 — anticipation before the descent | ❌ built, measured at 7.6px vs a 41px stroke, **reverted as invisible** |
| P5 — phase indicator | ✅ **DONE.** The projector reads APPROACHING / SCRUBBING / RETURNING, reporting `EVENT["phase"]` from Python's FSM rather than a browser timer. Measured: IDLE, APPROACHING 0.2s, SCRUBBING 1.6s, RETURNING 9.8s, IDLE 12.2s, matching the server log. Falls back to CYCLE RUNNING when the key is absent; killing Python mid-cycle drops it to IDLE inside 1s. This row used to say the richer label "would have been a lie" and that was true until the key existed |
| P6 — the chair reads as a wheelchair | ✅ **DONE 2026-09-19.** It was the camera, not the model. The wide shot sat at about a 17 degree orbit, and the seated body (x -0.89..0.80, z -0.62..0.67) covers the chair (x -0.57..0.68, z -0.66..0.60) almost exactly from there, so two model swaps and an aim change all measured no better. Orbit and height are per shot now; wide swings to 0.62 and drops to 0.74 and the other five shots are unchanged. A footplate was added at the feet's measured position (leg bones at z 0 and -0.24, NOT forward) |

**Scene cost, measured after the wheelchair was removed:** 29 draw
calls, 1125 triangles, 25 geometries, 13 textures, 10 scene children, 0 page
errors. There is still enormous headroom on a real projector, and nothing
visual is blocked by performance.

This line said "9 draw calls, 777 triangles" for a long time and had drifted
badly. Removing the chair did NOT return it to the pre-chair figure either
(the log recorded 26 calls / 1119 tris before it landed). Both numbers were
worth measuring rather than deriving.

---

## 5. MEASURED NUMBERS WORTH KEEPING

- px per world unit is **per projector shape**: 385.4 at 1920x1080, 256.9 at
  1280x720, 211.2 at 1024x768. `fit()` moves the camera with aspect ratio.
- Splotch sprite paints 26.9 / 18.0 / 14.8 px at those three shapes.
- Splotch neighbour spacing: 29 / 19 / 16 px. Sprite width exceeds spacing at
  every shape, which is why they touch. "Under the spacing gives a gap" is a
  false rule — lobed textures paint past their nominal box.
- Full suite ~700s (measured 624s and 647s of test time across two logs, plus fixtures and server startup). `quick.sh` ~250s. Projector test 11.2s.
- Flake floor: **4 watchdog aborts in 126 suite runs** (counted across every
  retained log, not remembered), plus at least 6 counter-flakes. Every abort is
  `test_arm_protocol.py` exceeding its 150s budget, and every one is the block
  being SLOW rather than wedged: the aborted run reached 57 of the 80 checks a
  complete run prints, was still progressing at the cut, and the same test
  re-run alone passes at exit 0. **A 25/26 with a WATCHDOG line on that test is
  the known flake. Re-run the one test before treating it as a break.**
  The counter-flake half of this line is inherited, not re-measured: the flake
  prints no phrase a log grep can find.
- **A `CAM=fake` scrubbot dies after about six minutes.** Three instances:
  376.4s, 378.2s, 404.6s, all ending in MediaPipe's `Check failed:
  status_or_buffer is OK` (pixel buffer -6662). It is the soak death
  `py/fakecam.py:122` predicts: the synthetic camera never blocks, so the loop
  spins at ~138fps and the native allocator falls behind.
  **Six minutes is the order of magnitude, not a countdown.** This bullet first
  said "about 376 seconds" on two readings that landed 1.7s apart, which looked
  like a constant. The third came 26s later and the real spread is 28s. Two
  agreeing points are not a constant.
  One left running between rehearsals will be dead when you come back, and the
  projector will read `ARM ○ MANUAL`. Restart it before a run. The page server
  on :8000 is unaffected.
  **To keep it up across a long setup, restart it every 5 minutes rather than
  waiting for the death.** A 300s pre-emptive restart ran twice cleanly, and
  300s sits well under the earliest death measured. This is the blunt version
  of the remedy `py/fakecam.py` already prescribes: **throttle the CALLER.**
  Neither is a fix, and the docstring says in as many words not to fix a leak
  here that a real camera does not have. A real webcam blocks at ~30fps and
  never reaches the condition.
- Launch scrubbot the way `run.sh:154` does, with `python -u`. Without it the
  log captures 4 lines instead of 24 and none of the startup prints, so an
  absent line proves nothing about which branch ran.
