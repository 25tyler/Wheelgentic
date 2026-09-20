# WHEELGENTIC — SOFTWARE DIRECTIVE

> **SUPERSEDED AS THE ENTRY POINT. The source of truth is now
> `docs/TRUTH.md`.** Read that first, then `docs/ROADMAP.md` for what to work
> on. This file remains authoritative for ONE thing and it is the most valuable
> thing in the repo: **§3, the hard-won facts** — every trap that cost hours,
> measured. Read §3 when you hit something strange. Do not take §5 (phases, all
> ✅) or §2 (current state) as the work list; `ROADMAP.md` and `STATE.md`
> replace them.
>
> Why it moved: this file's §00 calibration line — *"verification of things
> believed-working has consistently outscored new features"* — was true when
> written and stopped being true the moment Tyler redirected the project on
>. Nothing in the loop could notice. See `TRUTH.md` §7.

If context was compacted and you remember nothing, start at `docs/TRUTH.md`.

Repo: `github.com/25tyler/Wheelgentic` · Working dir: `~/Wheelgentic`
Owner writes ALL software; teammates build hardware.

---

## 00. HOW TYLER WANTS THIS WORKED — READ BEFORE ANYTHING ELSE

**NEVER WAIT ON TYLER'S INPUT. EVER.**

Said twice, in his words:

> "you keep stopping pre emptively when i asked you to keep going without
> stopping. you shouldnt rely on the timer though"

> "never wait on my inputs ever. i told you to do the highest roi thing at any
> point in time and to use your own judgement"

What that means in practice:

1. **Do not ask.** Not "should I?", not "want me to?", not "which would you
   prefer?", not a question dressed as a status update. Decide and do it.
2. **Do not stop to report.** A summary is not a deliverable. If you have
   finished something, the next thing to do is start the next thing. The turn
   ends when the work runs out, not when a milestone looks tidy.
3. **Pick the highest-ROI item yourself, continuously.** Not the next item on
   a list — the item with the most value at this moment. Re-judge after every
   completion. §5's phase list is *input* to that judgement, not a queue you
   drain in order.
4. **A blocker on one thing is not a blocker on everything.** If something
   genuinely needs Tyler (a hardware go/no-go, the bench-mount rule), write it
   in `docs/OPEN-QUESTIONS.md` and IMMEDIATELY go do something else. Never
   idle waiting for an answer.
5. **No timers.** An earlier session leaned on a 20-minute check-in timer.
   Tyler's exact correction: *"you shouldnt rely on the timer though."* The
   fix is not a longer interval. The fix is to not stop.

**The test before ending a turn:** was my last action a tool call that changed
something, or was it prose? If it was prose, the turn ended too early.

**What "highest ROI" has actually meant here**, as calibration: executing a
documented recovery step nobody had ever run (found 2 real bugs); running a
mode that had only been tested in pieces (found the splotch-placement bug);
planting bugs against a new guard (2 of 4 plants passed silently the first
time). Verification of things believed-working has consistently outscored new
features.

---

## 0. THE PROJECT IN FIVE LINES

A Waveshare RoArm-M2-S with a sponge scrubs a person's forearm. A webcam +
MediaPipe finds the forearm. A projector shows a goofy low-poly cartoon that
mirrors the person, with dirt splotches that pop with soap bubbles as the real
arm scrubs. Pitch: aging population, caregiver shortage, bathing is the #1
assistance need and the most undignified — and **all processing is on-device,
so the goofy avatar is a PRIVACY MECHANISM, not just a joke.**

MIT hackathon, ~36h. Demoed on a projector to judges.


---

## 0b. TEAMMATE BRAINSTORM (, `HackMIT Brainstorm.pdf`)

Received from a teammate. **Where it conflicts with what is built, the
conflicts are listed in §0c — those are decisions to settle with the team, not
things to silently pick a side on.**

Their framing: **"Agentic Multi-Arm Robotic AI Bathing Assistant / Shower for
the Disabled and Elderly."**

### The numbers for the pitch — USE THESE, they are better than mine

| Fact | Where it goes |
|---|---|
| Bathing is the **#1 daily activity people lose first** and need most help with | opening line |
| **74.5%** of US residential care residents need help bathing | the stat that lands |
| Bathing disability is the **main reason older people need a home aide**, and strongly predicts **nursing home admission** | the stakes |
| **63M** US family caregivers | scale |
| Nursing assistants are injured at **5x the industry rate**, mostly back/shoulder from handling people | the part nobody expects — it is not only about the person being bathed |
| "Being undressed and scrubbed by someone else feels like being vulnerable" | the dignity beat |

The caregiver-injury figure is the strongest new material. It reframes the
project from "help the elderly" to "this job is physically destroying the
people who do it" — which is a second, independent reason the machine should
exist and is much harder to wave away.

### Their hardware plan

- **4x Waveshare high-torque arms**, each an independent "AI agent"
- Spinning sponge on each tip — flagged by them as "big wow factor, but each
  needs a servo + microcontroller + wiring + power. Might not be enough time."
- **User sits on a chair and presses start**
- **NO WATER** — "will damage the motors"
- **Bench-mounted-only** competition restriction; they are asking whether
  clamping to the chair circumvents it

### Their vision plan

- **Point cloud only**, no video, to address privacy at the sensor
- Split the body into **4 sections, one arm each**
- Plan trajectories from the person's **actual 3D model**
- Their "innovation" pitch to judges: **no preprogrammed paths** — every body
  is different, so paths are generated from the live 3D model, and **arms
  adapt dynamically** (move your leg, the arm follows)
- **User can point at an area** to clean more and the arm focuses there

### Their demo plan

- Let judges experience it — **hovering above the body, not touching**
- A teammate in a swimsuit for a live demo
- **Body visualisation + completion status** (e.g. "50% complete") on a
  laptop/monitor/phone — this is the screen I have already built

---

## 0c. CONFLICTS TO SETTLE WITH THE TEAM

**Full write-up with recommendations: `docs/OPEN-QUESTIONS.md`.**
**The timed demo script is `docs/DEMO-SCRIPT.md`.**
The bench-mount rule is #1 there and is the only item that can disqualify the
project. Nobody has read the actual rule.

Four hard contradictions between the brainstorm and what is built and tested.
**Do not resolve these unilaterally — they change the hardware.**

| # | Brainstorm says | Built + tested | Why it matters |
|---|---|---|---|
| 1 | **NO WATER**, it damages the motors | `scripted.py` dips in a bucket; the demo script opens on it | The bucket dip is the funniest 2 seconds of the demo and makes it legible from 20 feet. Dry sponge keeps it, minus the dip. **Their call — it is their hardware.** |
| 2 | **Hover, do not touch** | `forearm_z_mm - 5.0` deliberately presses 5mm INTO the skin plane, and torque feedback gates the splotch pops | Hovering removes the torque contact sensor entirely, so cleanliness goes back to being a timer. It also removes the safety story's best line. If we hover, `--no-contact-gate` becomes the default. |
| 3 | **4 arms, one per body section** | One arm, one forearm, one homography | 4 arms = 4 serial ports, 4 calibrations, 4 workspace envelopes, and inter-arm collision avoidance. The software scales (the driver is per-instance) but calibration and collision are new work. **One arm done well beats four half-working.** |
| 4 | **Point cloud only, no video** | RGB webcam + MediaPipe; the CARTOON is the privacy mechanism | Both are honest privacy answers. Mine: nothing is stored, nothing leaves the laptop, the only thing anyone sees is a cartoon. Theirs: the sensor never captures an image at all — stronger, but needs a depth camera, which §3 rules out on Apple Silicon (RealSense needs an arm64 rebuild + sudo; ZED needs CUDA). **We can say "on-device only, zero frames stored" truthfully today.** |

### Where the two plans already agree

- Body visualisation + completion percentage on a screen — **built**
- "User sits and presses start" — **built**, and the ARMED consent latch makes
  the press mandatory rather than cosmetic
- Adaptive, not preprogrammed — **built**: the scrub target is computed live
  from the person's own elbow/wrist, so a different arm length or position
  changes the path with no reconfiguration. That IS their innovation claim,
  and it is already true and demonstrable.
- Arms adapt to movement — **built**: move your forearm and the target
  follows, at 240 mm/s under the rate limiter.

### Not yet built, from their list

- **"Point at an area to clean more"** — genuinely nice, and cheap: the SDK
  already computes `t` along the limb, so a pointing gesture is one more
  landmark check. Worth doing IF Phase 2 lands early.
- **Spinning sponge** — their hardware call. Software cost: zero.
- **Bench-mount / clamp question** — **someone must read the actual rules.**
  This is the only item that can disqualify the project, and nobody has
  checked it. Highest-priority open question in the whole document.

---

## 1. HOW TO WORK ON THIS

1. Read this file top to bottom.
2. Find the first phase in §5 not marked ✅. That is your job.
3. Do it. Run things — do not review code and declare victory (see §3).
4. Update §5 status + §6 log **in the same turn you do the work.**
5. Every ~20 min: checkpoint against §5, write a line in §6.

### The one rule that matters

**RUN IT. Every bug that mattered today was invisible to code review.**
Five real defects passed syntax checks, console-error checks, and DOM probes.
They were caught by executing the thing and looking at the output.

A guard that has never been *seen to fail* is decoration. Plant the bug, watch
the guard fire, remove the plant. My own splotch test reported `ON ARM: True`
with the bug deliberately planted, because it printed instead of asserting.

---

## 2. CURRENT STATE

**ALL 37 PHASE ITEMS ✅. Full suite 41 tests. ~747 assertions**
(counted as LINES containing `check(`, which is what the guard in
`test_docs_match_code.py` measures — call-sites read 495 and labelled
`check("` reads 433; three metrics, three numbers, all consistent. They differ
because a multi-line call contributes one line but one call-site, and an
unlabelled `check(f"...")` never reaches the third count — so the three move
independently, and an earlier landing pushed the call-site count DOWN while the
other two went up. Re-measure all three by their own rules; never add a delta.)
Nothing is blocked on hardware: `CAM=fake ./run.sh --no-arm` runs the entire
demo end to end (verified: remote arm → forearm detected → APPROACH → SCRUB →
RETREAT at 100%).

### Works, verified by execution

| Thing | Evidence |
|---|---|
| Browser boots, 19/19 assets serve | zero console errors, zero failed requests |
| Cartoon renders, HUD reads | screenshot; Press Start 2P loaded |
| Counter 0 → 33 → 100 + confetti finale | screenshot + DOM assert |
| Splotches land ON the arm | screen-space assert, middle-70% band |
| Python → browser events | `ARM ● LINKED`, 5 events, correct shape |
| **Survives Python SIGKILL** | cartoon kept **240 app frames/s**, manual keys worked, auto-reconnect |
| Calibration corner-order guard | fires on swap (693mm error caught) |
| Motion envelope + smooth5 | fade-in 0.36mm vs 34.91mm mid; endpoint velocity 1e-11 |
| Arm pump rate | 39.9Hz after deadline-pacing fix (was 34.8; naive sleep re-measures 31.3-31.7 here) |

### NOT yet verified — this is the work

| Gap | Why it matters |
|---|---|
| `arm.py` has never talked to anything | wire format, rate, estop logic all unproven |
| `scripted.py` has never been executed | it is cut-list item #3, the "tracking failed" fallback |
| `vision.py` never run against a real camera | mirror convention unsettled; framing constraint untested |
| `dirt.py` does not exist | the answer to "is the dirt detection real?" |
| No recorded real-pose run | `recordings/synthetic.jsonl` is hand-generated |
| `run.sh` never executed | one-command boot is the demo-day path |
| `homography.pkl` is SYNTHETIC | must be deleted + recalibrated before the arm nears a person |

---

## 3. HARD-WON FACTS — do not relearn these

### THE EMERGENCY STOP HAS PRODUCED **FIVE** SEPARATE CRITICAL BUGS

Read this before touching `estop()`, `clear_estop()`, `_send()`, or the FSM
gate. Every one was found by an adversarial audit AFTER the previous fix, and
every one shared a root cause: **estop state was a bare bool mutated from
three threads with no atomicity.**

1. **It reported success it had not achieved.** `_send` swallowed every
   failure, so the operator saw `*** EMERGENCY STOP ***` while the command
   never left the machine.
2. **It latched on failure, making pressing it WORSE than not pressing it.**
   On a transient stall the pump was silenced forever; when the link
   recovered, 0 commands were sent versus 41 ending at HOME without the
   estop. The arm held the contact pose on a forearm indefinitely.
3. **Clearing it interpolated from a position the arm had left.** `last_sent`
   is what we COMMANDED; the servos release during an estop, so a 60mm droop
   produced **2400 mm/s on the recovery button**.
4. **A failed estop left the FSM scrubbing over its own retreat.**
   `estopped` stays False there so the pump can retreat — but the FSM gate
   read `estopped`, so SCRUB re-targeted the forearm every frame.
5. **The latch was not atomic.** Sweeping 30 offsets across `clear_estop`'s
   61ms window: **25/30 left the hardware latched while software read False**,
   and 1/30 let the arm actually move. Worse, the recovery path's
   `if arm.estopped` guard then refused to re-send `T:999` — the arm sat
   frozen at the contact pose with the recovery key dead and the HUD showing
   no estop, because it renders the same flag.

**Rules that fell out of this:**
- The latch is guarded by `_estop_lock` + `_estop_gen`. A clear that started
  before an estop RE-ASSERTS `T:0` — its own `T:999` may have raced past on
  the wire.
- **Recovery is unconditional.** There is no cost to sending `T:999` when
  nothing is stopped, and the guard was the only thing between the operator
  and a bricked demo.
- Every safety field is declared at CLASS level, so an `Arm` built any way
  still has it — `estop()` must never raise `AttributeError`.
- **A single well-chosen timing offset proves nothing. Sweep the window.**

### Verified by running code on this Mac

- **UV tracer detection is HUE+BRIGHTNESS, not saturation.** Measured HSV:
  skin H=14-16 (S 81-153, V 152-205); Glo Germ H=96 S=105 **V=255**; yellow
  highlighter H=39 S=165 V=255; green paint H=53 S=175 V=255.
  Saturation-only (`S>120`) MISSES Glo Germ — it is blue-WHITE, so its S=105
  sits *below* warm skin at S=153 — while false-positiving on an orange-lit
  forearm. Blue-excess is worse: +63 for Glo Germ but **−140** for the yellow
  highlighter. Correct rule: **bright AND not-skin-hued.**
- `project_to_limb` must measure `perp` to the **infinite axis**, not the
  clamped segment. Measuring to the clamp reports perp=150 for an on-axis blob
  150px past the elbow, so the perpendicular test silently does the t_raw
  test's job and the guards are not independent.

- `time.sleep(0.025)` in a loop → **34.8 Hz, not 40.** Loop body + macOS timer
  granularity both add to the sleep. Use absolute-deadline pacing → 39.9 Hz
  (five runs: 39.9-40.0; the naive loop re-measures 31.3-31.7 Hz here, so the
  recorded 34.8 understates the shortfall rather than overstating it).
  Applies to `arm.py::_pump` and anything else claiming a rate.
- **A 4-point homography cannot self-detect a mis-click.** It is exact by
  construction: residual reads `0.000000` while true error is 14mm (30px
  mis-click), 27mm (60px), 60mm (150px). Tested diagonal ratio, diagonal
  midpoint separation, split-triangle areas — all drift smoothly, none
  discriminate. **Only a 5th independent point (`calibrate.verify()`) works.**
- Corner-order swap (TL,TR,BL,BR) → **693mm silent error.** Guard catches it.
- `websockets.broadcast()` holds **28.9 Hz** with a frozen client attached and
  4KB payloads. No backpressure. Confirmed — the plan was right.
- Kenney `character-a.glb`: `skins:0`, nodes exactly
  `torso head arm-left arm-right leg-left leg-right`, 27 clips incl `idle`,
  texture is **external** (`Textures/texture-a.png`).
  **HISTORICAL — that asset no longer exists.** It was deleted in `397f9c2`
  when the cast moved to the skinned mini pack. Do NOT renumber the 27: it was
  a correct measurement of a file that is gone. Every GLB shipped today reports
  `skins:2` and **32** clips, and the node list gained `root`, `body-mesh` and
  `head-mesh`. Verified against all 12 assets by parsing their GLB JSON chunks.
- Kenney arm geometry in **NODE-LOCAL** space: y from **+0.1 to −1.0**
  (len **1.0**), width **0.4**, mesh centre offset `cx` = ±0.2.
  At rest world direction is exactly `[0,-1,0]` → `REST=(0,-1,0)` is correct.
  `rotation.z = +0.5` swings arm-**left** OUTWARD (tip to x=+0.479).
- **Never `Box3.setFromObject(node)` for limb size** — that is the WORLD box
  and folds in parent transforms. Measure geometry relative to the node.

### Traps confirmed from research (do not re-litigate)

- **ZzFXMicro declares `zzfxX` (its AudioContext) with `let` at SCRIPT
  scope**, so it is NOT on `window`. `unlockAudio()` read `window.zzfxX`, got
  undefined, and resumed nothing — every sound would have been silent on stage
  while the visuals looked perfect. `vendor.sh` now appends
  `window.zzfx = zzfx; window.zzfxX = zzfxX;` on download. Verified: context
  goes suspended → running on the gesture, a pop makes 2 audio nodes, the
  finale 8.

- `mediapipe==1.0.1` **SIGABRTs** on macOS arm64 in `DrishtiMetalHelper`
  before frame one. Exit 134 — `try/except` does NOT catch it. **Pin 1.0.0.**
- `mp.solutions.pose` **deleted** in 1.0.x. Every tutorial online is broken.
- GPU delegate **rejects 3-channel** images → `BGR2RGBA` + `ImageFormat.SRGBA`.
- MediaPipe Pose is a **whole-body model**: a tight crop of a forearm returns
  NO POSE. Camera must see torso/shoulders. Mount at 60–70°, not straight down.
- OpenCV 5.0 **deleted** `calibrateHandEye` but kept the enums → fails at CALL
  time. Hence `opencv-contrib-python==4.12.0.88`.
- `three.core.min.js` is a **separate file** since r165. No HTML names it —
  `three.module.min.js` imports it — so a 404 kills the whole module graph.
  Since `42b296b` that paints `WHEELGENTIC FAILED TO START` rather than a blank
  screen; index.html's classic-script watchdog catches "no module evaluated".
- Kalidokit is deprecated in its own README, no successor. Ready Player Me shut
  down Jan 2026. Do not go down either road.
- `T:1041` has **no nanIK guard** → `reachable()` is mandatory, not defensive.
- `T:104`/`T:100` **block** the firmware's single-threaded `loop()`. Never use
  them during the demo. `T:123` has no position target — unsafe over a forearm.
- Serial needs `setRTS(False)` **and** `setDTR(False)` or the ESP32 resets on
  every port open.
- Never command the arm from `pose_world_landmarks` — hip-centred and
  scale-normalised, drifts as the subject shifts. Use 2D pixels + homography.
  (World landmarks are fine for the CARTOON, where being wrong is invisible.)

### Testing traps I created myself

- **A screen-space test cannot see error pointing at the camera, and a
  camera angle can hide a real bug for months.** FOUND 2026-09-19.
  `test_splotch_placement.py` measures how far the dirt sits from the arm's
  centre line AFTER projecting both to the screen. The dirt was being pushed
  sideways off the arm by more than the limb is thick, and from the old
  near head-on camera that offset projected to one or two pixels and passed
  every run. Swinging the wide shot round turned the same offset into
  fourteen pixels and twelve of twelve characters went red. A ray cast
  through each splotch showed it had been landing on the `torso` and
  `leg-left` the whole time.
  - **The trap inside the trap**: the window had already been widened from
    0.05 to 0.12 to make four failing characters pass, with a careful
    comment explaining it as two measurements disagreeing. That was wrong.
    The slack was paying for the bug. When a guard is loosened to make a
    test pass, the reason had better be measured, not reasoned.
  - **What the test still cannot do**: tripling the (now correct)
    camera-ward stand-off leaves every character green, because a shift
    along the view vector projects to the same pixel. Depth error needs a
    ray cast. Both plants are written into the test file.
  - **The false alarms this generated**, both worth recognising: a
    colour-threshold pixel counter reported zero dirt on screen and nearly
    got the fix reverted, but it reported zero for the ORIGINAL code too, so
    it was measuring nothing. Toggle the thing and count pixels that CHANGE.
    And a ray reporting the torso in front of the arm was hitting a
    silhouette edge, not proving occlusion: sweeping twenty-one points down
    the bone found every one visible.


- **The arm-protocol watchdog is load-induced RETRIES, not a wedge.**
  DIAGNOSED 2026-09-21 after it aborted 3 times in 20 suite runs (15%, five
  times the 3% the docs quoted). The estop-race block at
  `test_arm_protocol.py:409` sweeps 10 timing offsets and **retries the whole
  sweep up to twice** when any offset comes out inconsistent. Each pass is
  10 x 1.3s, so two retries add **39 seconds**. The budget was 150s with a
  measured ~87s base run, leaving 24 seconds for everything else the machine
  was doing. **Raised to 210s on 2026-09-21**, which leaves 84.

  **THIS WAS ALREADY DIAGNOSED ON AND NOT ACTED ON.**
  `WORK-QUEUE.md:127` records it in the same terms -- *"two 7f retries cost
  157.7s against a 150s arm, so the abort is arithmetic"* -- and the budget
  stayed at 150 for another eight days while the failure was re-explained as
  a known flake in three separate documents. Finding a cause and writing it
  down is not the same as fixing it. If a diagnosis in this repo names an
  arithmetic cause, change the arithmetic.

  First in-suite run after the raise: **PASS at 123s**. That would have fit
  inside 150 too -- but 123 leaves 27 seconds of headroom there, and the
  runs that aborted are the ones that went past it. 210 leaves 87.
  Inconsistency is itself load-sensitive, so a busy machine triggers the
  retries that then push the test past the watchdog. The retained log of an
  aborted run shows `(retry 1: 1 still inconsistent)` and `(retry 2: ...)`;
  a clean solo run fires neither. It has passed at exit 0 on every re-run.

  Do not "fix" it by raising the watchdog or deleting the retries -- the
  retries are what prove the race is not real, and the comment above them
  says that block has produced five separate critical bugs. If it becomes
  intolerable, the honest change is to give that one test its own longer
  budget, not to weaken the sweep.

- **`#link` carries TWO writers, so sampling it once after a sleep races.**
  It shows the socket's link state AND held banners like the estop refusal.
  A link update inside the wait window overwrites the banner, so a check that
  sleeps and then reads gets `ARM LINKED` where it expected
  `STILL STOPPED`. Found 2026-09-21: `test_estop_cartoon` went red in a full
  suite run and passed alone, which is the shape of a race rather than a
  regression. Wait for the text with `wait_for_function`; a timeout still
  fails the check honestly because it then reads whatever is really there.
  **13 other places sample `#link` after a fixed sleep.** They pass today. If
  one of them starts flaking, this is why, and this is the fix.

- **A fixed sleep after `n` is not waiting for the reload.** `n` reloads the
  page. Three seconds is plenty on an idle machine and not enough inside the
  suite, where the check then reads the OLD page and sees pre-swap state.
  Mark the page (`window.__x = true`) and wait for the mark to vanish.

- **`stdout=subprocess.PIPE` with nobody reading it DEADLOCKS the child.**
  Once the OS pipe buffer fills (~64KB) the child blocks forever on write.
  `scrubbot.py` prints continuously and `--headless` silences none of it, so
  `test_integration` hung at 120s with NO output — twice. Redirect to a file.
  **I had two wrong theories first** (a held port, then `networkidle`), both
  plausible, both unproven, both changed code that did not need changing. When
  a subprocess hangs with no output, check the pipe BEFORE theorising.

- **`wait_until="networkidle"` never settles on a page with a reconnecting
  websocket.** It waits for 500ms of NO network activity, and `main.js`
  retries its socket every 500ms while the server is down — so `goto()` waits
  forever. `test_integration` hung under suite load for exactly this reason
  and passed standalone because the server happened to be up. Use
  `wait_until="load"` plus an explicit settle.
- **A test failing only under suite load may be CPU contention, not a race.**
  I saw 1 offset in 10 fail, assumed a real race, and changed the code. A
  dedicated 72-trial sweep of the ORIGINAL code found ZERO failures. Prove it
  in isolation before believing it, and have the test RETRY once — a real race
  fails both times.

- **A GUESSED threshold blesses the bug it was meant to catch.** I asserted an
  aborted routine sends "< 400 commands, a full run is ~500". A full run is
  306, so 305 passed a check meant to prove the routine had been cut short and
  the plant did not fire. MEASURE the reference value in-process and assert a
  RATIO against it. This has now hidden three separate bugs in this project.

- **Do not launch anything on :8765 or :8000 while the suite is running.** I
  did it three times and each time chased a "failure" that was my own port
  collision. Check `pgrep -f run_all.sh` first, or use the lockfile. The
  suite's own error messages are correct and were the only reason it was
  quick to spot.

- **A daemon-thread watchdog does not reliably fire.** I found a test process
  running for **3 hours 21 minutes** under a 90-second `time.sleep` daemon
  watchdog, holding a pty and competing with live suite runs — which is where
  several "failures" I chased actually came from. Use SIGALRM
  (`tests/watchdog.py`): the OS delivers it and Python cannot starve it.
- **Do not `pkill -f "tests/test_"` while a suite is running** — that kills
  the suite's own child and the run reports a bogus failure. Kill `run_all.sh`
  first, or use the lockfile.

- **`pgrep -f "<pattern>"` matches the shell that is evaluating the guard**,
  and `$$` inside `$(...)` is the SUBSHELL'S PARENT — so the obvious
  self-exclusion (`pgrep ... | grep -v "^$$"`) silently never matches. My
  concurrent-suite guard did not fire at all. Use a lockfile (`mkdir` is
  atomic) when you need "only one of these at a time".
- **Two concurrent suite runs fight over ports and produce failures belonging
  to neither.** `tests/run_all.sh` now refuses to start a second instance.

- **When a threaded test is flaky, EXTRACT THE DECISION — do not tune the
  sleeps.** Both the UV pop rule and the occlusion-abort rule were flaky
  through the vision loop because the fake feed's frame rate varies with
  system load. Widening sleeps, counting frames and waiting on observed state
  were all guesses about timing I do not control. Pulling the rule out as a
  pure function (`uv_should_pop`, `should_abort_scrub`) made both 5/5
  deterministic AND plant-verifiable.
- **A test that re-implements the logic it checks is a tautology.** The UV
  test copied the pop rules into itself; it would have passed with the shipped
  version broken in any way the copy did not share. Import the real function.
- **A perfectly static fake pose trips the pose-freshness watchdog** — which
  is CORRECT, that is what a wedged camera looks like. A fake feed must jitter
  the way real landmarks do, or everything downstream of the watchdog is
  untestable.

- **A plant-test restore can silently revert unrelated work.** `cp backup.py
  file.py` after a plant undoes everything committed since that backup was
  taken. It cost the whole remote-arm feature once. Take the backup and
  restore it in the SAME turn, or `git checkout -- <file>` instead.
- **Orphaned headless-Chrome holds :8765 connections open**, which stops the
  next scrubbot binding — so every socket test fails for a reason unrelated to
  the code. Pre-clean the port in the runner AND in each socket test.

- **A probe can measure the wrong thing entirely.** `test_resilience`'s fps
  check counted its OWN `requestAnimationFrame` callbacks — those keep firing
  at 120fps even when the app's `setAnimationLoop` is dead. It reported
  "121 fps" and would have reported 121 with ZERO frames drawn. Read the
  subject's own counter (`renderer.info.render.frame`), never a proxy.
- **Presence is not ordering.** The arm test asserted T:112 (torque caps) was
  *sent*, never that it was sent BEFORE motion — a startup that streamed
  full-torque commands first would have passed.
- **`RunningMode.VIDEO` carries tracking BETWEEN frames**, so a test that
  detects a full body and then feeds a cropped one can still "find" a pose
  from the previous frame's tracking. My framing test passed 3 runs in 5 for
  exactly this reason. Use `RunningMode.IMAGE` when the question is "can this
  model find a pose in THIS image, cold?"
- **A test can exercise one term of a compound condition.** `test_dirt` only
  ever showed DIM skin, so the `V>190` and `S>60` terms of the mask were never
  load-bearing; the hue row alone carried every case.

- **Tests can kill each other.** `test_runsh.py` pkills every process matching
  `http.server 8000` — including the SUITE'S OWN server — so the three browser
  tests after it died with `ERR_CONNECTION_REFUSED` for reasons unrelated to
  the code under test. Fixed by running it LAST and re-checking the server
  before each browser test. **When a test fails, first ask whether an earlier
  test broke the environment.**
- **A test that needs leftovers from a previous run is not a test.**
  `test_record_replay.py` wrote to a bare `/tmp` path and only passed because
  a stale file happened to exist; it failed the moment it ran in a different
  order. Use `tempfile.mkdtemp()` per run.
- **`$?` after a pipe is the LAST command's status.** `cmd | tail` reports
  tail's exit code, so the runner printed `FAIL (exit 0)` for every failure.
  Capture the code before piping.

- **Piping a test into `tail` hides its exit code.** The runner printed
  `ALL CHECKS PASSED` while 4 of 5 tests died on ModuleNotFoundError.
- **A DETECTOR IS A CLAIM, AND IT NEEDS A KNOWN POSITIVE.** Eleven counting
  instruments misfired in one session, every one the same shape: a pattern matching
  more or less than the thing it names. Three worth naming because they nearly
  shipped numbers. (1) An assertion LABEL prints on both `PASS` and `*** FAIL`
  lines, so `grep -l "<label>"` counts every run that EXECUTED the check: it
  reported "6 of 6, rate 100%" when the truth was 3 of 6. (2) The same failure
  renders two ways, standalone `*** FAIL  <label>` versus the suite runner's
  `*** 1 FAILED: [...]`, so a regex built for one returned **0 of 38** while a
  failure I had personally read sat in those logs. (3) The `avoid-ai-writing`
  detector scores ASCII `--` identically to a real em dash; I excused it as
  harmless on about a dozen commit messages before probing it.
  Before believing any count: run the detector against a log you KNOW contains
  the event and one you know does not. Key on the token both output formats
  share. And when a wrapper tails output, missing detail is missing EVIDENCE,
  not absence: all four suite-level failures carried no `consec=` value, so
  they could not evidence a signature I had claimed held for "every failure".

- **A test that only prints always passes.** Assert, then plant the bug and
  watch it fail.
- Replay mode **must not import mediapipe** at module scope — the fallback
  died with the exact thing it exists to survive.

---

## 4. ARCHITECTURE (settled — do not redesign)

```
webcam ─┬─► py/scrubbot.py ──USB serial──► RoArm-M2-S (firmware solves IK)
        │        │                          NOT WiFi
        │        └── ws://127.0.0.1:8765  EVENTS ONLY (~200 bytes)
        │                    │
        └────────────────────▼
             Chrome --kiosk (its OWN MediaPipe + its own camera read)
```

**MediaPipe runs twice, deliberately.** Not for speed — for liveness. The
projector must boot, run and demo with the robot stack entirely dead.
The socket carries `{limb, t, scrub, clean, reset}` and never pose, so a dead
socket costs only splotch-popping; `1/2/3` is the rehearsed manual fallback.

**No depth camera.** Forearm on a table = known plane = homography.
`FOREARM_Z_MM` is a ruler measurement.
**No IK library.** The ESP32 firmware solves it; send millimetres.
**No bundler, no framework, no CDN at runtime.** Everything vendored.

---

## 5. PHASES — the plan

Legend: ✅ done+verified · 🔨 in progress · ⬜ not started · ⚠️ blocked

### Phase 0 — directive + heartbeat ✅
- ✅ `docs/DIRECTIVE.md` (this file)
- ✅ 20-minute checkpoint loop

### Phase 1 — harden what exists ✅
- ✅ **Fake RoArm serial device** speaking the real ESP32 JSON dialect
      (pty-based), so `arm.py` can be tested with no hardware
- ✅ `arm.py` against it: 22 checks — wire format, 40.0Hz rate, 6.00mm limiter,
      `reachable()`, estop `T:0`, `T:999` clear, torque `T:112`, feedback `T:105`
- ✅ Plant-verified: estop-guard removal leaks 24 commands; MAX_STEP_MM=9999
      now fails on an ABSOLUTE 400mm/s ceiling (it did not, at first)
- ✅ `scripted.py` executed end-to-end: 528 commands, dip→travel→scrub→retreat,
      65mm oscillation, 243mm/s, 3/3 splotches, monotonic 33→67→100
- ✅ Config hot-reload: survives truncated JSON + deleted file, keeps last-good,
      4.5us/call

### Phase 2 — real camera path ✅ (fully proven WITHOUT a camera)
The camera on this machine is denied by macOS — which itself reproduced the
documented trap: `isOpened()` returns False with **no exception**. So the path
was proven against REAL MediaPipe using a synthetic rendered person instead.
- ✅ **mediapipe 1.0.0 installed, imports, creates a landmarker — no SIGABRT.**
      GPU (Metal) delegate on an M4 Pro, **139–517 fps**. The pin claim holds.
- ✅ SRGBA conversion accepted (the 3-channel GPU trap)
- ✅ **FRAMING CONSTRAINT PROVEN, not just documented**: whole body in frame →
      pose found; torso cropped away → **pose lost**. Deterministic over 3
      runs. A tabletop camera zoomed on a forearm silently returns nothing.
- ✅ One Euro measured on real jitter: **2.76px → 0.94px** at the shipped
      defaults (it removed 1% before the units fix)
- ✅ Visibility gate rejects a low-confidence limb
- ✅ `recordings/good_run.jsonl` generated through the REAL pipeline
      (`tools/synth_record.py`): 240/240 frames, 8.0s, 151px of travel.
      Replays at 7.97s driving the arm at 243 mm/s max.
- ✅ **Browser pose path proven**: getUserMedia stream delivered, browser
      MediaPipe initialises ("browser pose up"), 238-240 app frames/s with
      pose running, zero page errors. Mirroring proven with INJECTED
      landmarks — a forearm swung from vertical to horizontal moves the
      cartoon's quaternion by 0.707, exactly what the math predicts.
      **This found a demo-killer**: `mixer.update(dt)` ran AFTER `aim()`, so
      the idle clip overwrote every tracked limb on every frame. Measured
      0.008 instead of 0.707 — the cartoon would have played its idle
      animation while a person waved at it, with nothing erroring anywhere.
      Plant-verified.
- ✅ **MIRROR CONVENTION SETTLED** — and I had it backwards in the comments.
      `py/fakecam.py` renders a subject waving ONE arm through the real
      VideoCapture interface (`CAM=fake`), so this is now deterministic and
      re-checked every run. MEASURED, same frame, arm raised:
        as rendered    L15=(442,175) R16=(100,324)  raised = **15**
        cv2.flip(f,1)  L15=(382,316) R16=(183,313)  raised = **16**
      **Mirroring SWAPS the anatomical labels** — MediaPipe infers left/right
      from the IMAGE, not the person. So `mirror` decides WHICH ARM THE ROBOT
      SCRUBS: with `mirror:true` and the code reading `L_*`, the arm goes to
      the subject's RIGHT arm. Plant-verified (5 checks fire when the render
      is inverted).
      Getting the figure detectable needed a FACE: without eyes and a nose the
      figure is left-right symmetric and MediaPipe flip-flops on which way it
      faces (landmark 15 jumped x=102→459 between adjacent frames), which
      makes the convention untestable.
- ✅ `recordings/good_run.jsonl` regenerated through `fakecam` + the REAL
      MediaPipe pipeline: 305/320 frames (95%), 10.6s, a genuine wave. The
      subject is synthetic but the LANDMARKS are real — same model, same One
      Euro, same visibility gate. Replays in exactly 10.6s driving the arm at
      243 mm/s. A live capture would be nicer but is no longer a blocker.

### Phase 3 — UV dirt detector ✅
- ✅ `py/dirt.py`: **hue+brightness**, NOT saturation (see §3 for the measured
      table — saturation-only misses Glo Germ, which is blue-WHITE at S=105,
      BELOW warm skin at S=153)
- ✅ `blobs()` + `project_to_limb()` + `assign()`; perp now measured to the
      INFINITE AXIS so the two rejections are genuinely independent
- ✅ `cleanliness()` guarded: zero-initial-area, and clamped so a growing blob
      cannot run the projector counter backwards
- ✅ `tests/test_dirt.py`: 26 checks on synthetic frames, no lamp needed.
      Tracker curve measured 0 → 39 → 57 → 81 → 100%
- ✅ Plant-verified ×3: sat-only reverts fail 7 checks; clamp removal fails;
      t_raw removal fails (only after the perp fix made them independent)
- ✅ Wired into `scrubbot.py` behind `config.dirt_mode`, hot-swappable
- ✅ `tools/tune_dirt.py` — live trackbar tuning under the real lamp

### Phase 4 — demo-day hardening ✅
- ✅ `run.sh` executed: both children start, page serves 200, ONE SIGINT kills
      both with zero orphans and releases :8000. Two fatal bugs found (see §6)
- ✅ Preflight: names the missing module instead of exiting silently
- ✅ Projector legibility at 1080p / 720p / 4:3 — viewport-relative HUD,
      overlap test measures TEXT extents not element boxes
- ✅ `docs/RECOVERY-CARD.md` — every fix executed and timed
- ✅ `homography.pkl` out of git + `docs/CALIBRATION.md` venue drill;
      `tests/fixture.py` generates a synthetic one for tests
- ✅ Cold-boot rehearsal from a FRESH CLONE: all vendored assets present,
      run.sh names missing deps, README venv command works, mediapipe 1.0.0
      installs from requirements.txt, full stack runs. Found: the websocket
      thread died with a raw traceback on a port clash (see §6)
- ✅ `--record`/`REPLAY=` round trip proven: 150 frames replay in 4.98s (real-
      time pacing preserved, not raced), truncated file recovers 82/150,
      single-frame and empty handled, looping wraps correctly.
      Still needs a REAL recording captured from a camera (Phase 2)

### Phase 5 — adversarial audit ✅ (two rounds)
- ✅ Subagent sweep: 8 lenses, 52 raw findings, ~50 verifiers. THREE CRITICALs
      survived adversarial verification, all reproduced by execution:
      1. **estop clear bypassed the rate limiter** — 237.8mm in one 25ms tick
         = 9,513 mm/s (40x the limit) with the sponge on a person's forearm,
         on the button an operator presses under pressure. Now 239 mm/s.
      2. **the 15Hz pump silently coalesced splotch events** — firing the three
         end-of-scrub pops delivered ONE. The finale would land as 33% with
         two splotches still on the arm. Now a queue; all 3 delivered.
      3. **no consent gate** — IDLE→APPROACH was gated only on "a forearm is
         visible", so the machine scrubbed on an endless loop and would start
         on anyone who reached onto the table. Now an ARMED latch, one cycle
         per `s` press.
      All three plant-verified.
- ✅ Second batch from the same audit (23 findings survived verification):
      **camera dropout** — `continue` on a None frame skipped `cv2.waitKey`, so
      the loop spun at 100% CPU with SPACE/estop UNREADABLE while the arm held
      its last target pressed into a forearm. Now retreats, disarms, and keeps
      servicing keys.
      **no pose-freshness watchdog** — a wedged camera returns the same stale
      frame forever and landmarks stay "valid". Now retreats after 2s frozen.
      **no landmark visibility gate** — MediaPipe GUESSES occluded joints and
      `vision.py` discarded the `visibility` field, so a guessed wrist drove
      the arm confidently wrong. Exactly the mid-scrub case: the robot arm
      occludes the forearm it is scrubbing. Now gated at 0.5.
      **test_calibration.py could not fail** — it printed `*** FAIL: guard did
      NOT fire` and exited 0. My oldest test had the bug it exists to catch.
      **tests/fixture.py wrote a synthetic homography under the PRODUCTION
      filename** — a leftover is indistinguishable from a real calibration and
      puts the sponge ~30cm off. Now drops a sentinel and `calibrate.load()`
      prints a 6-line banner.
- ✅ Third batch: **torque cutout ran only in the LIVE branch**, so
      `--scripted`/`--replay` moved a real arm with no watchdog — and
      `--scripted` is the fallback most likely to run against a human under
      pressure. **Signed `min()`** let a negative `scrub_amp_mm` bypass the
      80% cap. **The recovery card's headline fallback pointed at a file that
      does not exist** (`good_run.jsonl`). **The gate did not gate** — any key
      but Enter/Space fell through and popped a splotch with audio locked.
      **`resetSplotches` left orphaned gsap tweens** that re-hid a restored
      splotch. **`test_resilience`'s fps probe counted its own rAF callbacks**,
      reporting 121fps with a completely dead render loop; now reads
      `renderer.info.render.frame` (240 clean / 0 planted).
      **`test_arm_protocol` never checked ORDERING** (torque caps could land
      after motion); **`test_dirt` never showed BRIGHT skin**, so the V and S
      terms of the mask were never load-bearing. All plant-verified.
- ✅ Fourth batch: **splotches at 0.22/0.78 were UNREACHABLE** — the sponge's
      travel only spans u = 0.5 ± amp/(2·half), i.e. 0.325..0.675 on a 250mm
      forearm, so only the middle one could ever pop and the demo stalled at
      33%. Moved to 0.34/0.50/0.66 and added a reachability test across eight
      forearm lengths plus a cross-file constant check.
      **A single throw in `setAnimationLoop` kills it permanently** — three.js
      never re-arms; verified 30 injected throws now survive at 242fps.
      **NaN passed the `lengthSq() < 1e-8` guard** (every NaN comparison is
      false) and poisoned a limb quaternion forever.
      **UV mode popped from OCCLUSION** — the arm blocks the region it scrubs,
      so every splotch popped on the first pass regardless of cleaning. Now
      needs 8 consecutive missing frames AND the sponge nearby, and latches
      the positions actually detected rather than assumed.
- ✅ Re-run full suite; every guard plant-verified
- ✅ Final README + directive reconciliation
- ✅ **Second audit round, aimed at the FIXES themselves.** Fixes are prime
      territory for new bugs — they are written under the belief that they
      make things safer. It found two more CRITICALs, both in the estop:
      **`estop()` reported success it had not achieved** — it set
      `estopped=True`, called `_send` (which swallows every failure) and
      printed "*** EMERGENCY STOP ***" unconditionally. On a dead link the
      operator saw a confirmation while the command never left the machine,
      and because `estopped=True` also stops the pump, the arm held its last
      target on a forearm. `_send` now returns a bool; `estop()` retries and
      shouts "CUT POWER AT THE 12V SUPPLY" when it fails.
      **The estop stopped the pump but NOT the FSM** — SCRUB re-called
      `set_target` every frame, so `clear_estop()` was overwritten within one
      frame and the arm resumed mid-scrub from a state nobody re-authorised.
      Plant-verified: **142 commands leaked** with the guard removed.
      Also: the **torque watchdog ran at 6.1 Hz, not 10** (poll_feedback has
      its own 50ms sleep) and **checked only the shoulder** while the ELBOW
      carries the sponge — now 9.1 Hz across all four joints.
      Also: **UV mode fired events the browser silently dropped** (I created
      that an hour earlier by tightening the match window to 0.07); the
      server now sends detected positions and the page moves its splotches.
      Also: **the visibility-gate test was a tautology** that would have
      passed with the gate deleted.

---

## 6. WORK LOG — append, never rewrite

Format: `HH:MM — what changed / what was proven / what broke`

- **15:34** — **Audited the build against ALL 23 prompts Tyler has sent**, pulled
  from the session transcript rather than from memory. 19-agent adversarial
  pass; five confirmed gaps, all closed, plus three more found while fixing.
  **The character is now Kenney MINI Characters (CC0)** — rounded chunky
  proportions, a real face, a SKINNED rig and **32 authored animations**. Two
  wrong shapes came first (the blocky pack = Minecraft; a blob I hand-sculpted
  from capsules and lathes) before I actually searched. Tyler's rule, stated
  twice and escalating: *"i cant emphasize enough to not make things yourself
  if you can just take it from already existing things online."*
  Rejected on the way: three.js RobotExpressive (right shape, but a ROBOT being
  washed by a robot kills the dignity pitch) and poly.pizza's Quaternius
  mirrors (their importer strips rigs — `skins:0, anims:0` on every model).
  **The animations were DEAD ON ARRIVAL even after wiring them.** `stepSpring()`
  writes `.quaternion` on every part every frame, so it overwrote the mixer and
  every one-shot was discarded: `emote-yes` moved the head by **0.0042**, which
  is the breath sway and nothing else. A running clip now owns torso/head/legs
  while the ARMS keep their springs — the arms must stay honest to the tracked
  person, because "it is tracking her actual forearm" is the claim the whole
  demo rests on. **0.0042 → 1.9985**, arms still track.
  **`vendor.sh` was fetching the wrong pack.** The script whose entire job is
  making the reuse reproducible still downloaded the blocky pack and
  sanity-checked `character-a.glb` — a file the app had stopped loading. It
  would run to completion and print all-ok having verified nothing about the
  character on screen. Fixed and **RUN from a clean state** to prove it.
  **Three tests were silently dead**, all from the rigid→skinned swap:
  splotch placement crashed (`arm-left` is a leaf BONE with no geometry),
  `test_degraded_boot` blocked a filename the app no longer loads (so it
  blocked nothing), and the robot arm was invisible to every test because it
  was absent from the debug handle.
  **Also: legs now track.** MediaPipe was already sending knees and ankles and
  nothing consumed them, so the legs stayed frozen while the arms mirrored —
  against a brief that says "map the ENTIRE person". Two lines.
  **Refuted by measurement:** "the sponge never travels, it rolls in place" —
  it sweeps 0.265 units along the arm during a real scrub.

- **12:08** — **Audited my OWN six fixes with a second 49-agent pass. It found
  five defects IN THE FIXES.** Reread this before trusting a fix because it
  was carefully made.
  **(1) A second signal aborted the retreat.** `_term_handler` stays installed
  and still raises, so the impatient operator pressing ^C twice — or re-running
  `pkill` — lands INSIDE `main()`'s `finally` and cuts the retreat short.
  Measured: a second SIGTERM at 0.05s AND at 0.30s both gave no stop and no
  clean shutdown, the arm only z~50-65mm into the 0.4s retreat with the skin
  plane at z=45 — **the sponge still on the forearm**, the exact end state the
  SIGTERM fix eliminated, reached through the same key they are already
  pressing.
  **(2) My link-lost gate pre-empted my estop reset.** It runs first and forces
  IDLE, which made the estop gate's own `state != "IDLE"` false and skipped its
  cleanup entirely. Measured: pull the cable, press SPACE, `_cleaned` still
  held `[0.5]` — the bug I fixed an hour earlier, reintroduced by the fix I
  added beside it. Now ONE `_reset_cycle_state()` called from every exit to
  IDLE, so a fourth path cannot repeat it.
  **(3) The Python twin of the UV key bug.** I fixed `1/2/3` in the browser and
  missed `pop()` in `py/scrubbot.py`, which still fired the hardcoded
  `SPLOTCH_TS`. Worse than the browser version: zero splotches pop on the
  projector WHILE THE COUNTER MARCHES TO 100 PERCENT — a finished scrub claimed
  over three splotches still visibly stuck on the arm.
  **(4) `EVENT["link"]` was write-only** — the page never read it, so the
  projector showed nothing when the arm died, and it was never drained so it
  would have stuck forever. Now flashHold'd on the projector and drained with
  the other one-shots.
  **THE TEST LESSON, twice in one guard.** My first check for (1) grepped the
  source for `"signal.SIG_IGN"` — and a plant that EMPTIED the loop that string
  sits in passed, because the text was still there. Fixed by extracting a real
  `disarm_signals()` the test can CALL. Then a second plant — deleting the call
  from `finally` — passed too, because the child called the function directly.
  **Two plants, two different holes, one guard, one fix.** A guard that tests
  for text cannot tell a live loop from an empty one, and a guard that tests a
  function cannot tell whether anything calls it.

- **09:43** — **53-agent adversarial audit: 5 real defects, one of them the worst
  bug this project has had.**
  **(1) SIGTERM left the sponge pressed 5mm into the person's forearm.** The
  shutdown retreat lives in main()'s `finally:`, and SIGTERM never unwinds —
  while `run.sh`'s cleanup, the README, AND scrubbot's own printed `pkill`
  advice all use SIGTERM. Measured at the contact pose: SIGTERM -> 0 stops,
  z=40.0, parked; SIGINT -> 1 stop, z=123.5, lifted (**that SIGINT reading was
  load-dependent and is no longer the mechanism — see the SIGINT flake entry
  below; SIGINT now has its own explicit handler**). `test_runsh.py` already
  sent SIGTERM **and passed**, because it only asserted "no orphaned children"
  — it passed *because* the retreat was skipped.
  **(2) Torque caps were never re-asserted.** `T:112` — the command whose own
  comment calls it "THE single most valuable command for a machine touching
  human skin" — was sent once at construction. The recovery card documents
  brownout-reset as routine ("press r"), and a reset ESP32 returns to the
  firmware default of 1000. Measured: 0 re-assertions after estop+clear.
  **(3) Keys 1/2/3 popped 0 of 3 in UV mode.** UV moves the splotches; the
  keys indexed the hardcoded array. The rehearsed manual fallback, dead in
  exactly the mode a judge asks for.
  **(4) "CUT POWER AT THE SUPPLY" was erased in <150ms** by the reconnect
  loop's `flag()` — the most safety-critical string in the UI, gone at the
  moment the arm did NOT stop.
  **(5) `--scripted` had no consent expiry.** The live path lapses arming at
  30s; scripted checked `ARMED` but never `_armed_at`. Press `s`, walk away,
  and it scrubs to contact depth minutes later — in the mode you fall back to
  when tracking fails.
  All five fixed and plant-verified. Also ordered the ARMED two-field write
  (stamp before latch) — not reproducible in 529 forced trials under the GIL,
  but free to order correctly, and the estop taught this project what bare
  cross-thread flags cost.
  **Four harness bugs died proving the SIGTERM fix**, every one making it look
  broken: signalling before the rate limiter arrived (the child died outside
  its own try/except — its traceback named the line), racing the retreat with
  a fixed sleep + kill, reading `p.stdout` only after `wait()` so all but the
  last child was truncated, and a 1200-char slice that cut past the `raise`.
  Reordering the signals moved which one "passed" — that is how I knew the
  harness, not the product, was deciding it.
  **A separate finding I could NOT confirm:** a 22-minute soak showed Python
  RSS climbing to 8GB, but vision alone is flat at 254MB over 3455 frames and
  `tracemalloc` shows near-zero Python allocation. It only appears with
  `CAM=fake`, whose `read()` has no rate limit, so the loop spins at 138fps
  and MediaPipe's native allocator falls behind. **A real webcam blocks at
  ~30fps**, and at a 0.033s throttle growth drops from +5008MB to +533MB.
  Recorded as a fake-camera artifact, not a demo-day bug — but the throttled
  number is not zero either, so it wants a real-camera soak at hour 20.

- **07:29** — Hardened the two things that had just been built. The backup video
  went stale **within an hour** of first being recorded (the `cycleLive`
  warning landed and the clip still showed the old build), so
  `tools/record_backup.py` now stamps `recordings/backup.sources` — a sha256
  over the `web/` files it was built from — and the docs test fails when they
  drift. **Content hash, not mtime:** I wrote the mtime version first and
  reasoned a fresh clone stamps everything identically so `>` would be safe,
  then actually cloned the repo and measured four `web/` files landing AFTER
  the video on the sub-second checkout spread. A guard that false-fires on a
  clean checkout gets disabled by whoever hits it at 3am.
  Also: `cycleLive` only cleared on `m.reset`, which a SIGKILLed Python never
  sends, so it latched true and every manual 1/2/3 in the crash fallback would
  have warned about a dead cycle. It LOOKED fine only because `flag(false)`
  overwrote the warning 1.6s later — real bug, masked by a timing accident.
  Cleared in `ws.onclose`; `test_cycle_conflict.py` grew a SIGKILL section.
  And four places tell someone to run `tools/record_backup.py` while nothing
  checked it still runs — the docs test now parses every tool it names.
  Suite 23/23, quick 11/11.

- **07:15** — **`recordings/backup.mp4` EXISTS.** The recovery card's last row
  pointed at a file flagged "record this at hour 25" — hour 25 is exactly when
  nobody has time to record anything, and every other failure mode on that card
  had a tested path while the last-resort one had nothing. `tools/record_backup.py`
  records it from the real stack (scrubbot under `CAM=fake`, real FSM, real
  socket, real page; Playwright captures the compositor so it needs no screen-
  recording grant). Committed past `.gitignore` — it has to survive a fresh
  clone — and `test_docs_match_code.py` lost the exemption that had been
  excusing it for being missing.
  **The pixels caught two things the numbers did not.** The first cut printed
  "counter reached 100%" for both cycles and produced a video showing a static
  0%: my loop kept the last `pct` it read, and worse, `scrub_seconds=8` plus a
  synthetic limb that never sweeps the sponge meant all three pops landed in the
  finale at once — nine seconds of nothing, then everything. Nothing was broken;
  the artifact was still unusable. Lesson recorded: extract frames and LOOK.
  **Then the pacing exposed a real product problem.** Driving 1/2/3 while a
  cycle was armed made the count oscillate 1 -> 0 -> 1, because RETREAT fires
  `fire_reset()`. Both paths are right alone; doing both is operator error, and
  an operator under pressure will do both. Fixed as a WARNING, never a block —
  1/2/3 is the crash fallback and must work when Python is dead — so the page
  now tracks `cycleLive` and flashes "CYCLE RUNNING — it will reset this".
  `tests/test_cycle_conflict.py`, plant-verified twice. Suite now 23 tests.

- **04:26** — **FULL SUITE 22/22 GREEN**, and that number means something it did
  not mean this morning: the two mediapipe tests had been failing on every full
  run for an unknown length of time because `run_all.sh` was launching the
  wrong interpreter. Also hardened the directive's own test count — it said 20
  while `run_all.sh` had 22, so `test_docs_match_code.py` now derives both
  figures from the code and asserts EQUALITY on the count (a floor would bless
  drift upward; adding a test must force the doc update). Plant-verified both
  directions. Confirmed scripted mode — the demo default — is untouched by the
  `_uv_targets` fix: with nothing latched the fallback is exactly `SPLOTCH_TS`.

- **04:20** — Two real defects, both found by executing a documented instruction
  instead of reading it.
  **(1) Flipping `dirt_mode` mid-scrub left every splotch on screen at 100%.**
  The recovery card says: "UV detector finds nothing -> set `dirt_mode:
  scripted`. Hot-reloads, no restart." Nobody had ever done it. Measured: UV
  latches at 0.115/0.254/0.756 and the browser moves its splotches there; flip
  to scripted and the FSM fires the hardcoded 0.34/0.50/0.66, none within the
  page's 0.07 match window, so all three splotches stay visible while the
  counter runs to 100%. A judge sees a full green bar over a dirty arm —
  during the recovery you reached for because something already went wrong.
  The finale had ALREADY learned this and guarded it with `dirt_mode == "uv"`,
  which is exactly what the flip changes; the right predicate is "did I tell
  the browser to move its splotches", so both sites now key on `_uv_targets`.
  `tests/test_mode_switch.py`, verified by reverting: 3 orphans, 3 wrong
  events. Reverse flip (scripted -> uv) measured too: noisier (6 events) but
  every splotch pops — documented, not fixed.
  **(2) The full suite was running the WRONG PYTHON.** `run_all.sh` defaulted
  `PY_CV` to `/tmp/sbtest/bin/python`, which has cv2 but not mediapipe, while
  `quick.sh` prefers `./venv`. So `test_vision_real` and `test_mirror` failed
  on every full run and passed on every quick run, and the suite reported
  19/21 as if the tests were at fault. The preflight checked `cv2` and stopped
  — the guard-checks-the-wrong-thing shape. Now defaults to the venv, checks
  mediapipe too, and `/tmp` is off the default path entirely (it is wiped on
  reboot, which would have taken PY_CV with it eventually). Both guards
  plant-verified by pointing PY_CV at the mediapipe-less interpreter.

- **04:10** — `dirt_mode=uv` had NEVER run through the FSM. Its pieces were each
  tested (`uv_should_pop`, `_spread_pick`, `fluor_mask`) but the mode you
  switch to when a judge asks "is the dirt detection real?" had only ever been
  exercised in parts. Wrote `tests/test_uv_fsm.py`: a fake vision module with
  three tracer blobs, run through the real `vision_loop`. It works — latches
  the detected positions, tells the browser to move its splotches there, and
  the percentage climbs 33/67/100 as spots are wiped one at a time.
  Three harness bugs had to die first, each of which made correct code look
  broken: (1) the fake wrist sat at x=780 in a 640-wide frame, so `cv2.circle`
  silently clipped two of three tracers and the detector honestly reported 2
  blobs — I nearly filed a merge bug against `dirt.py`; (2) the limb mapped to
  robot x=69mm, under the 120mm floor, so every command tripped the workspace
  clamp — the guard working, printing as noise; (3) one global FADE wiped all
  three spots at once, so cleanliness read 100% at the first pop and hid
  whether it tracked partial cleaning at all.
  Then the plants: the FIRST round of four passed two of them. "Latch the
  hardcoded SPLOTCH_TS instead of the detected spots" went green because my
  tracers sat at 0.28/0.50/0.72, within the browser's 0.07 match window of the
  defaults, AND because the assertion compared latched-against-fired — two
  numbers that agree with each other even when both are wrong. Re-anchored
  every check to `SPOTS` (the one fact the FSM cannot derive from its own
  state), moved the tracers >0.08 clear of every default with an `assert` that
  enforces it, and made the harness wait for each pop instead of wiping on a
  timer. All four plants now fail on the right assertion; restored code passes
  7/7. Registered in `run_all.sh` + `quick.sh`. Recovery card's "is the dirt
  detection real?" answer upgraded from "the detector is built" to what is now
  actually verified, keeping the honest caveat that the thresholds still need
  real 365nm.

- **22:23** — Phase 0. Directive written. Baseline: 5/5 tests green, 2 commits
  pushed. Known gaps enumerated in §2.
- **10:00** — `web/juice.js` was the ONLY source file with no test, and it
  owns the demo's payoff. Wrote one; it found the audio dead. ZzFXMicro keeps
  its AudioContext in a `let` at script scope, so `window.zzfxX` was undefined
  and `unlockAudio()` resumed nothing — the #1 documented failure mode, live,
  and every visual still looked perfect. Plant-verified at "0 nodes".
- **09:15** — Suite 19/19 GREEN on the final code. Third audit round found
  five more real defects, four of them in the estop cluster (now five total
  — see §3). The last one took three theories: a held port and `networkidle`
  were both wrong; the actual cause was `stdout=subprocess.PIPE` with nobody
  reading it, which deadlocks the child once the 64KB buffer fills.
- **07:00** — ALL 37 PHASE ITEMS ✅. Kept going on the remaining audit
  findings, and the estop yielded a THIRD distinct way to hurt someone:
  **clearing it interpolated from a position the arm had left.** `last_sent`
  is what we COMMANDED; during an estop the servos release, so gravity moves
  the arm. A 60mm droop produced **2400 mm/s on the recovery button**. Now
  reads the true position first — 240 mm/s. The fix had two ordering traps of
  its own, both caught by running it: `poll_feedback()` no-ops while estopped,
  and toggling that flag to permit the read RELEASES THE PUMP for a tick using
  the stale target.
  Also: **the projector could trigger an estop but not clear one**, while the
  torque watchdog fires autonomously — a cutout deadlocked the demo. `shift+C`
  now clears.
  Also: `tests/test_docs_match_code.py` — the recovery card must not lie. It
  already told an operator to run a file that did not exist.
  Suite 17→19 tests.
- **06:10** — Phase 2 ✅. Built `py/fakecam.py`: a synthetic waving subject
  behind the real VideoCapture interface, so `CAM=fake` runs the entire live
  path with no hardware. That unblocked the last phase item and immediately
  proved my comments WRONG: mirroring SWAPS the anatomical labels, because
  MediaPipe reads left/right from the image. `mirror` therefore decides which
  arm gets scrubbed. Also learned the figure needs a FACE or the model
  flip-flops on its orientation. Suite 16→18 tests, quick 8/8.
- **05:30** — Second audit round, aimed at the FIXES. Two more CRITICALs, both
  in the estop — the one mechanism that must never lie was lying loudest.
  It reported success on a dead link, and it stopped the pump while leaving
  the FSM driving (142 commands leaked in the plant). Also found the torque
  watchdog running at 6.1 Hz instead of 10 and watching only the shoulder
  while the ELBOW carries the sponge. And a UV/browser mismatch I had created
  myself an hour earlier. Lesson: **auditing your own fixes finds more than
  auditing the original code** — every one of these was written while
  thinking about safety. Suite 16/16, 17 tests.
- **04:40** — Post-phase hardening. Four more real defects, all found by
  building the test rather than reading the code:
  **The arming key was unreachable on stage** — `s` is read by cv2.waitKey in
  the OpenCV debug window, and Chrome is fullscreen on the projector, so the
  operator would have had to alt-tab to a hidden window to START THE DEMO.
  The socket now carries `arm`/`estop` upstream; `s` and `x` work from the
  projector with an on-screen confirmation.
  **That handler went in vision_loop**, which `--replay` and `--scripted`
  never run, so those modes could not be armed remotely at all. Its own
  thread now.
  **The browser match tolerance (0.18) overlapped its neighbours** — splotches
  are 0.16 apart, so a duplicate event for an already-popped splotch would pop
  the WRONG dirt. Tightened to 0.07, under half the spacing.
  **A flaky framing test**: `RunningMode.VIDEO` carries tracking between
  frames, so it passed 3 runs in 5. IMAGE mode, 6/6 deterministic.
  Also wrote `docs/DEMO-SCRIPT.md` (the old one described a water dip the team
  vetoed and had no arming step) and `tools/first_run.sh` for the hardware
  handoff. Suite 15→16.
- **03:55** — Phase 5 ✅. Worked the remaining audit findings. The
  splotch-reachability one is the kind I would never have found by reading:
  the positions and the oscillation amplitude were each individually sensible
  and jointly impossible. Suite 15→16 tests.
- **03:20** — Browser pose path proven, and it caught the worst browser bug
  yet: `mixer.update(dt)` ran AFTER `aim()`, so the idle AnimationMixer
  overwrote every tracked limb quaternion every frame. Swinging an injected
  forearm from vertical to horizontal — a 0.707 quaternion change by the math
  — moved the rendered arm by 0.008. The cartoon would have idled while a
  person waved at it and NOTHING would have errored. Invisible to every
  previous browser test because they all ran with no camera, so the avatar
  only ever played its idle clip anyway. Suite 14→15.
- **02:50** — Phase 2 mostly ✅ WITHOUT a camera. The Mac's camera is denied,
  which reproduced the documented trap live (`isOpened()` False, no
  exception). Installed mediapipe 1.0.0 — it imports and creates a landmarker
  with no SIGABRT, GPU/Metal, 139–517fps, so the pin claim is now verified
  rather than quoted. Proved the FRAMING CONSTRAINT empirically with a
  synthetic rendered person: whole body → pose found, torso cropped → pose
  lost, deterministic over 3 runs. Took three attempts to get a detectable
  figure — flat vector art returns NOTHING; MediaPipe needs blur, rounded
  joints and sensor noise. Generated `recordings/good_run.jsonl` through the
  real pipeline (240/240 frames), so the recovery card's headline fallback now
  points at a file that exists. Suite 13→14, quick 7/7.
- **02:15** — Teammate's `HackMIT Brainstorm.pdf` reconciled into §0b/§0c.
  Their statistics are BETTER than mine and now lead the pitch — especially
  "nursing assistants are injured at 5x the industry rate", which reframes the
  project from "help the elderly" to "this job is destroying the people who do
  it". Four hard conflicts logged for the team, NOT resolved unilaterally:
  no-water, hover-vs-touch, 4-arms, point-cloud-vs-RGB. Two of them are now
  config flags (`use_water_bucket`, `contact_depth_mm`) so whichever way the
  team decides is a config edit, not a code change; all three modes are
  tested. The bench-mount competition rule is the highest-priority open
  question in the project and nobody has read the actual rules.
- **01:30** — Phase 5 audit landed. 60 agents, 52 raw findings, 3 CRITICALs
  survived verification and ALL THREE were real, each proven by executing the
  shipped code. The estop one is the worst bug in the project and I would have
  shipped it: clearing an emergency stop threw the arm home at 9,513 mm/s with
  the sponge resting on someone's forearm. Independent adversarial review
  earned its keep — my own tests covered the estop, just never the RECOVERY
  from one. Suite 12→13.
- **00:40** — Phase 4 ✅ + cold boot. Two more silent demo-killers, both
  found by RUNNING rather than reading:
  (1) main.js's TOP-LEVEL await — a 404 on character-a.glb stopped module
      evaluation, so the keyboard listener was never registered: the page sat
      on "PRESS ANY KEY TO START" forever with every key dead and the only
      clue in a console nobody has open in kiosk mode. Now prints the cause
      and the fix on screen and installs a stub so 1/2/3 keep working.
  (2) A cold-boot rehearsal from a fresh clone hit "address already in use" on
      :8765 (leftover process). The ws thread died with a 30-line traceback
      from "Thread-1 (<lambda>)" while the arm kept running and the projector
      silently never connected. Now prints the diagnosis + the exact pkill.
  Suite 11→13.
- **00:05** — Phase 4 mostly ✅. Suite 8→10. `run.sh` had NEVER been executed
  and had two fatal bugs: it called `python` (absent on stock macOS) and
  `[ -d venv ] && source` returned 1 under the EXIT trap, killing the process
  group before anything started — exit 144, no output. Added an interpreter
  resolver + dependency preflight. HUD was fixed-px: the privacy line measured
  11px = 1.0% of height at 1080p, unreadable from ten feet; now viewport-
  relative. A screenshot caught CLEANLINESS overlapping 0% while the
  bounding-box test said "offscreen: none" — fixed with one flex stack, and
  the overlap test now measures TEXT extents (element boxes gave a false
  positive on a full-width block). Also: the runner printed `exit 0` on every
  failure because `$?` after `tail` is tail's status.
- **23:30** — Phase 3 ✅. `py/dirt.py` + 26 synthetic checks + live tuner.
  Found by running it: my "fluorophore-agnostic saturation" claim was FALSE —
  Glo Germ is blue-white (S=105), below warm skin (S=153), so the actual
  hospital tracer was undetected while the highlighter worked. Measured the
  HSV table and switched to hue+brightness. Also fixed `perp` to measure to
  the infinite axis, which is what made the t_raw plant fire. Suite 7→8.
- **23:05** — CHECKPOINT. Phase 1 ✅. Suite 5→7 tests, all green. Reordered:
  Phase 2 needs a human at a webcam, so it is marked ⚠️ and Phase 3 (UV
  detector, fully testable on synthetic images) runs next.
- **22:45** — Phase 1. Built `tests/fake_roarm.py` (pty, real ESP32 JSON) +
  `tests/test_arm_protocol.py` (22 checks). arm.py had never talked to
  anything; now: 40.0Hz exactly, 6.00mm max step, estop blocks all motion.
  THREE real fixes came out of it:
  (1) unguarded `setRTS/setDTR` crashed the whole constructor on any device
      without modem-control ioctls (OSError 25 on a pty);
  (2) the BOX clamp SILENTLY relocated a bad target — `set_target(2000,0,200)`
      returned True while driving to x=420. Added `clamped_hard` + a
      rate-limited warning, because a broken homography must not be silent;
  (3) my own rate-limiter test asserted against `A.MAX_STEP_MM`, so raising
      that constant to 9999 let a 200mm lurch PASS. Now also asserts an
      absolute 400mm/s ceiling. Plant-verified both directions.
  Also fixed the fake's `close()` hanging on a blocked `os.read`.

- **18:56** — **Locked the two estop criticals into the suite as
  `tests/test_estop_cartoon.py`** (25th test). Both bugs were found by an
  adversarial audit of code a 24-test green suite had already passed, and both
  live on the estop — the fifth and sixth defects in that one area.

  (1) `x` froze the real arm and left the CARTOON scrubbing, because the stroke
      interval was a `const` inside `startScrubChoreography()`'s closure that
      nothing outside could reach. Hoisted to module scope + `stopScrubChoreography()`.
  (2) `s` while estopped played a full fake scrub under a green ARMED banner.
      The FSM now answers `{"cmd":"arm","ok":False}` and the page acts on it.

  **THE TEST WAS WRONG THREE TIMES BEFORE IT WAS RIGHT, and every wrong version
  was green.** Recording the sequence because the failure mode repeats:
  - *Wrong file.* Section 9 went into `test_cycle_conflict.py`, whose section 3
    SIGKILLs scrubbot on purpose. Everything after runs with the server dead, so
    `s` was a no-op, every span read `0.0000`, and BOTH estop assertions passed
    vacuously. A span of 0 satisfies "the scrub stopped" when no scrub ever
    started — hence the link precondition that now aborts the run outright.
  - *Wrong keypress.* `pg.keyboard.press("C")` sends KeyC with no shift
    modifier; the page's `e.shiftKey` check correctly rejected it and the server
    log showed no CLEAR arriving. Read as a broken recovery path. `Shift+KeyC`.
  - *Wrong scrub.* Gating on `isCycleLive()` looked rigorous and was the worst
    of the three. Measured in 0.4s windows from the keypress: the BROWSER's
    choreography strokes t+0.4→t+7.8s with `live=False` throughout, and the
    FSM's cycle only flips `live=True` at t+9.8s, ~2s before it ends. The flag
    put every sample in the tail of the wrong scrub. Plant 1 — the exact
    original bug — went GREEN against that version.
  Two other instruments lied on the way: `.replace(str, 1)` planted into strings
  that occur 3 and 4 times in the file, so the first hit was patched while live
  code remained (plants 1 and 2 "passed" having removed nothing — now planted by
  line number); and `timeout` does not exist on macOS, so a run printed nothing
  and the empty output read as no failures.
  All three plants now fire on their OWN assertion, baseline green, tree diffed
  identical to the pre-plant backups.

- **19:24** — **Closed the last two audit findings, both measured before and
  after rather than argued.**

  (a) *Dirt textures shipped untagged.* All three `CanvasTexture`s reported
      `colorSpace: ''`, so three.js treated them as LINEAR and the authored
      gradient stops were not what reached the screen. Tagged `SRGBColorSpace`.
      **The audit's stated symptom was wrong** — it claimed "washed-out beige".
      Measured on the 24,655 pixels that actually change: mean luminance 16.6
      untagged vs 14.7 tagged. Both near-black; the splotches were never beige.
      Fixed as a correctness issue, not the high-severity one it was filed as.
      Two instruments lied first: `drawImage` off the WebGL canvas returned
      921,600 blank pixels (`preserveDrawingBuffer: false`) and would have
      printed "no difference" for any code, and a darkest-pixel probe found the
      black PAGE BACKGROUND at (28,26), identical in both shots.

  (b) *The death gag snapped upright.* Measured on torso deviation from
      upright: 0.2926 at t+0.36s, then **0.0000 at t+0.48s** — the very next
      sample. The character teleported upright inside 120ms, so the one gag
      whose entire joke is lying there had no beat. `clampWhenFinished` is now
      `(name === 'die')` — an emote is a reaction and must hand the body back
      to the springs; a punchline stays down. Now holds ~0.29 indefinitely.

      **The fix introduced a second bug and the check caught it.** Clamping is
      a state nothing else cleared, so `r` — the key the recovery card
      documents as the reset — left the character face-down at 0.2978. Added
      `standUp()` and called it from `resetAll()`. Verified all four paths:
      `d` flops (0.2986), `e` recovers (0.0), `r` recovers (0.0), `d` replays
      (0.2871), no page errors.

  **Decided and NOT done: wiring more of the 32 clips.** The audit listed "30
  of 32 unwired" as an open item. Read against the actual clip list it is not a
  gap. 12 are weapon poses (`holding-*-shoot`, `attack-melee-*`) — a robot arm
  bathing someone is a dignity pitch, and a character brandishing a rifle
  between scrubs kills it. 7 are the wheelchair set, already measured and cut
  (neither `sit` nor `wheelchair-sit` moves the root or torso: rootY 0.000,
  torsoY 0.176 in all three states). Most of the rest are locomotion for a
  character who stands still. The 6 on the `e` cycle are the ones that read as
  reactions from ten feet. Padding the cycle with kicks would make the demo
  worse, so the count stays where it is.

- **16:58** — **Research pass, then three landings from it.** The brief was to
  find the best existing tools/repos/assets rather than write more myself.

  **What the research actually found: the built surface has already absorbed
  its research.** Probed every layer where a library could replace hand-rolled
  code and each one was already correct — the One Euro filter is taken from
  jaantollander/OneEuroFilter with the casiez-fork trap recorded, ruckig and
  toppra were evaluated and rejected with stated reasons, IK is solved in the
  ESP32 firmware so no solver applies, and GSAP + OutlineEffect + confetti +
  zzfx are already vendored and live. So the ROI was not in replacing code. It
  was in **assets already sitting on disk, unused**.

  `/tmp/kenney-mini.zip` — downloaded weeks ago for one character — contains
  **12 characters, 10 mobility aids and 4 wheelchairs**, all CC0, all sharing
  the one `colormap.png` already vendored. Five characters were wired. Now
  twelve are, and the three caregiving props (cane, glasses, crutch) are
  vendored for the prop work.

  **The duplicate that would have shipped.** `vendor.sh` extracts
  `character-male-a.glb` and RENAMES it to `mini-character.glb`. Adding
  male-a to CAST would have put the same person in the cycle twice. Caught by
  hashing every GLB instead of trusting the filenames: identical sha256
  (`77572792...`). All 12 entries are now byte-distinct people, verified by a
  duplicate-group check over the whole set, and male-a's stray copy deleted.

  **A doc defect found on the way.** README's key table listed FOUR browser
  keys while the code had eleven — `x`, `shift+C`, `e`, `d`, `v`, `c` and `s`
  were all missing. It drifted because `test_docs_match_code` guards twelve
  bindings and stops at `r`: grep for KeyE/KeyD/KeyV in that test returned
  EMPTY. The three newest operator keys were promised by the recovery card and
  guarded by nothing. Both halves fixed — the table, and a guard so it cannot
  drift again.

  **My own new guard was vacuous on the first draft, twice over.** It asserted
  `"'die'" in js`, but `'die'` appears TWICE in main.js — once in a comment at
  line 643 and once in the handler — so deleting the handler would still pass.
  It also asserted `"avatar.cast"`, a string that occurs ZERO times (the code
  is `avatar?.cast`). Both retargeted to handler-only text, then plant-verified:
  deleting the `d` handler while leaving its comment now goes RED.

  Verified: all 12 load in a real browser (rig 6 parts, 32 clips, 0 errors
  each), `character-female-d` screenshotted rendering correctly.

- **17:06** — **Four probes of the `v` key disagreed; all four were wrong about
  the product and right about nothing.** Verifying the cast expansion I had
  just committed, a cycle probe reported **7 unique characters of 12, no
  wrap**. That reads as a real bug in shipped code.

  It was not. `v` writes `localStorage` then calls `location.reload()` after
  700ms. Every probe pressed the key again inside that window, so the document
  never turned over, `avatar.current` stayed stale, and
  `indexOf(current) + 1` recomputed the value it had just written. Half the
  presses were no-ops. 7 of 12 is exactly `ceil(13/2)` distinct values.

  Probe 2 read `localStorage` instead of the render, on the theory that
  intent beats appearance: **same 7**, because both reflect the same stale
  handler input. Probe 3 waited for `avatar.current` to be non-null before
  pressing: **1 of 12**, because that returns instantly on the OLD document.
  Three instruments, three different answers — at which point no claim about
  the key was defensible either way.

  What settled it: stamp `window.__VMARK` before the press and refuse to sample
  until the marker is **gone**, which is proof of a new document rather than a
  guess about elapsed milliseconds. That probe printed `NO RELOAD DETECTED` on
  presses 6, 8 and 10 — the even ones — naming the mechanism outright. Running
  the handler's arithmetic in isolation visits all 12 and wraps.

  **The key was correct the whole time.** A person pressing `v` and watching a
  2s reload gets all twelve. Now guarded by a test that waits for document
  turnover and asserts the advance is by exactly one — because the guard I had
  shipped only asserted the handler EXISTS, the same vacuity that let the
  README key table drift seven keys behind.

- **17:41** — **Walked the recovery card executing every row. Three rows were
  wrong, and fixing one of them exposed a fourth.** A runbook row nobody has
  run is untested code; this is queue item one in `docs/WORK-QUEUE.md`.

  **Row: "Camera dead -> REPLAY=recordings/synthetic.jsonl".** Both replay
  files exist and load, so nothing 404s and no guard noticed — but they are
  not equivalent. Measured elbow-x standard deviation: `synthetic.jsonl`
  **5.06** over a 16px range (hand-generated by `tests/fixture.py`, effectively
  a static dot); `good_run.jsonl` **34.94** over 106px, generated through the
  real pipeline. `test_record_replay.py` calls good_run "THE demo safety net"
  and README + DEMO-SCRIPT already named it. Only the card — the artifact taped
  to the laptop and read under pressure — pointed at the fixture.

  **Row: `run.sh` prints "FATAL: missing cv2 …".** It does not. It emits
  `FATAL: <interpreter> is missing: cv2 …`, so an operator grepping their
  terminal for the card's wording finds nothing.

  **Then my own fix exposed the third.** Pointing the card at `good_run.jsonl`
  made its camera-dead fallback trip the card's OWN do-not-ignore row,
  `[arm] WARNING: target … clamped …`. Measured: the fixture homography maps
  **48%** of good_run's targets below the `BOX xmin=120` floor, clamping
  **51-54mm at 0.63/sec**. `arm.py` warns above **50mm** and its own comment
  says small clamps are normal drift while a large one means a broken
  transform — but the card quoted only **400mm** and sent the operator to a
  90-second recalibration. The card now separates the two cases and says keep
  going, with the measured numbers in it.

  **Two alarms of mine that measurement killed before they reached a commit.**
  (1) A probe reported the replay never moved the cartoon — arm travel 0.007.
  It sampled the bone's WORLD POSITION, but the root is fixed and limbs move by
  rotation; on quaternion span every tracked part moves (max 0.0135). Fifth
  probe this session to measure the wrong channel. (2) "59 clamp warnings, the
  1/sec limiter is broken" — timed over a measured 30s run it is **0.63/sec**.
  The limiter holds.

  Both new guards plant-verified: reverting either card row goes red.

- **17:47** — **Queue item 2: ran `REPLAY=` end to end instead of trusting that
  it boots. It boots. It does not run a scrub cycle.**

  `replay_loop()` only calls `arm.set_target()`. The APPROACH and SCRUB states
  and **all three `fire()` calls live inside `vision_loop()`**, which replay
  never runs. Pressing `s` on the camera-dead fallback prints
  `ARMED (from the projector)` and then emits **zero pops**. Measured over 36s
  with the full 30s arm window AND `--no-contact-gate`, to rule out both
  innocent explanations: one `[fsm]` line, no pops, counter at 0%.

  `replay_loop` also opens no OpenCV window, so the **Python-side `1` `2` `3`
  is dead there too**. The BROWSER's `1` `2` `3` does work — verified end to
  end on replay: 0% → 100%, 3/3 splotches gone, no page errors. The fallback is
  usable, but only that way, and nothing said so.

  **The worst part of the shape is that it looks like it is working.** `ARMED`
  appears on the projector exactly as it does in a real run, so an operator
  whose camera just died presses `s`, sees the confirmation, and waits at 0%
  for the rest of the demo.

  **Why it shipped:** `test_remote_arm` armed the FSM on REPLAY data and
  asserted `"the FSM actually armed"` — stopping exactly one line before the
  gap. It now also asserts that no cycle follows, pinning the real behaviour;
  if replay ever grows a cycle that test fails and the card row must be
  rewritten in the same commit.

  Card and README both fixed to say pops are manual on replay, with the
  measured numbers. Three new guards, all plant-verified: deleting the card
  row, deleting the projector-keys line, and giving `replay_loop` a `fire()`
  call each turn one red.

- **18:00** — **Queue item 4: the UV thresholds were wired to config and
  FROZEN at startup.** `config.json` advertises HOT-RELOADS in its own
  `_comment` and the directive calls it the venue escape hatch, so every key
  got exercised rather than assumed.

  `dirt_mode` is hot — read per frame. The five `dirt_*` thresholds were not.
  `DirtTracker` is built ONCE before the frame loop and `observe()` reads the
  attributes it was built with, so they never changed again. **Measured:**
  built at `v_min=190`, edited config to `250`, `CFG.reload()` returned True
  and reported 250, and the tracker still held **190**.

  The comment directly above that build promised *"edit config.json and it
  switches WITHOUT a restart"*, and `tools/tune_dirt.py` tells the operator to
  tune under a real 365nm lamp and paste five keys in. **That paste did nothing
  until a restart** — in the one mode a judge actually asks about.

  The fix assigns the five attributes per frame rather than rebuilding the
  tracker: a rebuild would wipe `initial_area`, the latched spot positions and
  the cleanliness baseline mid-cycle, so the counter would jump BACKWARDS the
  moment anyone saved the file. Verified all five follow a live edit and the
  baseline survives.

  **Two of the five keys were missing from `config.json`, and adding them I
  shipped the wrong numbers.** I took `14`/`20` from the directive's measured
  SKIN-HUE OBSERVATIONS instead of `dirt.py`'s band constants `26`/`160` —
  which would have narrowed the discriminator the whole detector rests on, out
  of the box, with no error anywhere. The new guard caught it on its first run.
  That is also how I found the correction script had failed silently: it ran
  under `python3`, which has no cv2, so `import dirt` raised before it wrote
  anything, and the file still held my invented numbers.

  Three guard families, all plant-verified: freezing the refresh, rebuilding
  the tracker inside the loop, and skewing a config default each turn one red.

- **18:04** — **Queue item 5: hover mode. The doc's "one config edit" was half
  true, and my first reading of the other half was wrong.**

  `contact_depth_mm: +20` does hot-reload and the FSM runs a full cycle on it.
  But **`--no-contact-gate` is argparse-only — zero `CFG.data.get()` reads** —
  so it cannot be set from `config.json` the way the depth can. An operator
  flipping to hover mid-setup, exactly as OPEN-QUESTIONS #2 describes, gets the
  depth change and NOT the gate change. Hover needs a restart.

  **The part I nearly got wrong.** Hover popped all three splotches without
  the flag, which read as the doc's *"or the splotches never pop"* being false
  — a tempting finding, since it would mean the system flatters itself. The
  TOUCH-MODE CONTROL refuted it: same dry run, same 3 pops, same **0.0s**
  spread, `[fsm] splotch` logged **0** times in both. The burst is not caused
  by hover. It is caused by `--no-arm` having no torque stream, so
  `arm.contact` is False either way and the end-of-scrub finale sweeps every
  remaining splotch unconditionally.

  So the honest statement is narrower than the first one I reached for: on this
  laptop the flag's effect **cannot be demonstrated in either direction**, and
  a dry-run rehearsal shows hover "working" whether or not it is set. The
  difference only appears with the arm attached. Both facts are now in the doc.

  Running the control before writing the finding is what kept a wrong claim out
  of the repo — the same discipline that killed the "1/sec limiter is broken"
  alarm two entries ago.

- **18:14** — **Queue item 6: the twelve characters under a real scrub cycle.
  No defect — the rig adapts, which was the claim worth checking rather than
  assuming.**

  The bone TRANSFORMS are byte-identical across models (torso/arm/head
  translations agree to 15 significant figures) and the spring constants are
  module-level, so nothing per-character can diverge there. **The SKIN does
  differ.** `character-female-a`'s arm-left owns **302 vertices spanning
  0.449** against 146 over 0.284 for every other model — a long sleeve
  weighted to the arm bone, confirmed by the vertex histogram (mass spread
  80/78/96 through the middle bins, not a cuff lump at one end).

  That propagates, as it should: her splotches sit at local x
  **0.178/0.225/0.273 at sprite scale 0.208**, against **0.111/0.143/0.174 at
  0.146-0.17** elsewhere — 57% further out, 40% bigger. Screen space is the
  verdict, and all three land inside the rendered arm box for every character
  measured, so this is correct behaviour and not a bug.

  **What was actually wrong is that nothing checked it.**
  `test_splotch_placement` had ZERO character-switch references: it validated
  whichever model happened to load, always `mini-character`, while `v` now
  cycles twelve. It loops the cast now, keeping the same assertion —
  perpendicular distance to the limb's OWN centre line, because the arm hangs
  at an angle and swings under the spring, so an axis-aligned band is wrong at
  most angles.

  Three instruments were needed to get here and the first two could not answer
  it: whole-mesh POSITION bounds (wrong granularity), then per-bone skinned
  vertices from the GLB (right number, no rendering), then screen space (the
  only one that decides "on the arm").

  **The plant is the part worth keeping.** Breaking placement for
  `character-female-c` ALONE — neither first nor last in CAST — catches it by
  name with all three splotches off-limb. A loop that asserted once against
  whichever model loaded last would have passed that, and `syntax ok` had
  already passed a version where the whole verdict sat OUTSIDE the loop: an
  empty-bodied `for` is legal Python, so the parse check proved nothing. Only
  an indentation audit showed the verdict at 8 spaces while the loop body was
  at 12.

- **18:25** — **Queue item 7: the three vendored props, deleted.** Measured
  before deciding: `js_refs 0`, `test_refs 0`, and no user-facing doc mentioned
  them — only my own work-log line saying they were "vendored for the prop
  work", which never happened. An asset with no referent rots.

  Against wiring one: the rehearsed 2 minutes has no prop beat, `DEMO-SCRIPT`
  opens with *"the version that runs cleanly five times beats the version with
  an extra feature that runs twice"*, and the recovery card ends with *"Add
  features after the freeze. Rehearse instead."* A cane would need hand-placed
  attachment per model, untested across 12 rigs, for nothing the pitch does not
  already carry. `vendor.sh` records how to re-add one in a single line —
  `node['head'].add(gltf.scene)`, the head bone is childless and the texture is
  already vendored.

- **18:34** — **Queue item 8: re-measured the codebase's own `# MEASURED`
  claims. All of them reproduced, and one is understated.** 48 such claims
  across `py/` and `web/`; sampled every browser-free one.

  - **Pacing** (`py/arm.py:300`): naive `time.sleep(1/hz)` runs at **31.6 Hz**
    here against the recorded 34.8; deadline pacing hits **40.0 Hz** against
    the recorded 39.9. The claim holds and if anything understates the
    shortfall.
  - **One Euro units** (`py/vision.py:226`): **2.50px** residual filtering in
    pixel space vs **1.20px** normalised, recorded as 2.96 vs 1.02. Same
    direction, same magnitude. **That pass blamed "gaussian-seed variance"
    and this one measured that claim: seed spread is only 0.04-0.07px at
    N=4000. SAMPLE COUNT and WARMUP move it 0.20-0.35px -- the real cause.**
    A plausible attribution closed the question early; see the 54-condition
    sweep in the work log.
  - **Torque re-assert** (`py/arm.py:178`): through the pty fake, `T:112` goes
    **1 → 2** across estop+clear. The comment says it was 1 total before the
    fix — zero re-assertions — so the fix is present and doing what it claims.

  **No comment needed correcting.** That is the useful result rather than a
  disappointment: these were written from measurement, not from belief, which
  is exactly what a comment audit is for.

  **One claim is NOT reproducible here and that is now recorded.**
  `py/arm.py:422` — *"0 commands delivered after recovery with estop pressed,
  versus 41 ending at HOME"* — needs a transient link stall, and
  `tests/fake_roarm.py` has `of_type()` but no `stall()`. Rather than fake it
  with a weaker scenario and call the claim verified, the limit is written
  down: verifying it needs a stall primitive on the fake, or real hardware.

- **18:34** — **The queue hit zero, which is the refill trigger, not a stop.**
  All eight opening items are ✅. Refilled from the generators in
  `docs/WORK-QUEUE.md` rather than stopping to ask what is next.

- **18:37** — **Refill item 1: built the missing stall primitive and verified
  the one MEASURED claim that could not be reproduced.**

  `py/arm.py:422` claims *"0 commands delivered after recovery with estop
  pressed, versus 41 ending at HOME without it."* Reproducing it needs a
  transient link stall, and `tests/fake_roarm.py` had `of_type()` but no way
  to fail a write — so the claim sat unverifiable, in the estop path, the area
  that has produced five criticals.

  **Where the stall had to go, and why the obvious place is wrong.** Dropping
  bytes inside `_handle()` after the pty delivered them leaves `_send()`
  returning True: it catches `SerialTimeoutException`/`SerialException` into
  `_consec_write_fail`, which is what `link_ok` AND the estop recovery both
  read. A silent drop engages none of that machinery, so the scenario under
  test never happens. `stall()` therefore fails the WRITE, by raising from
  `arm.ser.write` — the code under test is untouched and every counter path
  runs for real.

  **Proved the primitive before trusting it.** A no-op stall would make every
  assertion below it pass vacuously. Measured: 15 stalled writes drive
  `_consec_write_fail` to 15 and `link_ok` to False; one good write clears
  both. Only then the claim itself.

  **The claim reproduces.** Latched: **0** commands after the link returns,
  arm left at z=163 — down on the forearm. Un-latched: **61** commands, ending
  at z=235 against HOME z=235. The comment said 41; that gap is pump timing on
  a different machine, and the load-bearing half — zero versus nonzero, and
  the arm actually reaching HOME — holds exactly. The comment now records both
  figures rather than only the original.

  Incidentally this is why the existing `link_ok` section never caught it: it
  sets `_consec_write_fail` **by hand** and never drives a real failing write.

- **19:03** — **`watchdog.arm(150)` DOES NOT ALWAYS FIRE. Measured twice, at
  4+ minutes, with zero WATCHDOG lines in the log.** This matters beyond the
  plant that exposed it: every test in the suite leans on that watchdog to
  stop a hung run from eating the session.

  Mutating `fake_roarm.stall()` into a no-op and re-running
  `test_arm_protocol.py` wedges it. Three attempts, each hung in the
  PRE-EXISTING estop-race section — the last log line is always *"a NEW estop
  arrived while clearing"* — never in the block I added. Each wedge left
  `fake_roarm.py` mutated in the working tree, because the shell never reached
  its restore line.

  **Why the watchdog starves.** SIGALRM is delivered to the main thread, but
  CPython only runs a signal handler at a bytecode boundary. A main thread
  parked inside a C call — pyserial's read, `os.read` on the pty — never
  reaches one. That is the SAME starvation class `watchdog.py`'s own docstring
  warns about for daemon threads; SIGALRM is more reliable than a thread, not
  immune to this.

  **The other half was my own doing.** The first attempt piped a 70-second
  test into `grep | head -4`; `head` closes the pipe, the producer blocks or
  takes SIGPIPE, and the shell waits forever. Both traps are already in my
  notes — *"a pipeline hides the exit code a harness reads"* — and I walked
  into one anyway. Redirect to a file, then grep the file.

  **What I did NOT do: keep hammering.** Positive proof that `stall()` is not
  a no-op already exists and is measured — 15 stalled writes drive
  `_consec_write_fail` to 15 and `link_ok` to False, one good write clears
  both — so the guard is not vacuous. Chasing a negative plant that wedges the
  machine three times is a worse trade than recording exactly why it wedges.
  `test_arm_protocol.py` now carries that warning at the top of the block, so
  the next person does not lose the same hour.

- **19:10** — **Fresh-clone simulation, and it found that `PORT=` does not do
  what `run.sh` said it did.**

  Cloned the pushed remote to /tmp — 97 files, 233MB. **Everything committed
  travelled**: 12 cast GLBs, every vendor artifact (three.core,
  vision_bundle, the 11MB wasm, both `.task` models), `backup.mp4` playable at
  49.2s through the gitignore negation, config hue keys 26/160, CAST 12.

  **The no-venv path fails honestly**, which is the recovery card's row
  executed on a genuinely fresh tree instead of trusted:
  `FATAL: python3 is missing: cv2 serial`, plus the exact fix command and the
  note that mediapipe is only needed for live mode. After
  `pip install -r requirements.txt` the pins land right (mediapipe 1.0.0,
  cv2 4.12.0) and the page serves.

  **THE FINDING.** `run.sh` said PORT was overridable *"so tests can run
  run.sh without colliding"* — which reads as whole-instance isolation. It is
  not. The websocket is hardcoded at `:8765` in BOTH `py/scrubbot.py` and
  `web/main.js`, so the clone on `PORT=8100` served its page at **http 200**
  and then died with `[ws] CANNOT BIND`. Two instances cannot coexist on any
  PORT.

  The tell was already in the tree and nobody had read it that way:
  `test_runsh.py` **waits for :8765 to be free** before starting rather than
  trusting PORT to separate instances. The workaround existed; the promise it
  contradicted did not.

  **Documented rather than re-architected.** A `WS_PORT` env var means
  touching `scrubbot.py`, `main.js` and every test's `free_8765()` — three
  surfaces, days before a demo, for a benefit only I have ever needed. The
  comment now says PORT moves the page only, and a guard holds it there.

  Also worth recording: the CANNOT-BIND banner is exactly right. It names the
  port, the likely cause, the two pkill commands, and the fallback.

- **19:29** — **Swept every number the docs quote. All of them hold.** Each
  figure an operator acts on or a judge hears is a claim, so each was checked
  against the code rather than against my memory of writing it.

  - **240 mm/s** = `MAX_STEP_MM` 6.0 x the pump's 40 Hz (`nxt += 0.025`).
    Derived, not guessed — and it is the number in the safety answer.
  - **`link_ok` trips below 12** consecutive write failures
    (`py/arm.py:244`), matching what the test asserts.
  - **30s** = `ARM_TIMEOUT_S`. **8s** = `scrub_seconds`. **50mm** is the clamp
    warning threshold, **400mm** its worked example.
  - **74.5% / 63M / 5x** sit under a heading that attributes them to the team
    brainstorm rather than presenting them as our measurements. I cannot
    verify them from code and should not pretend otherwise.

  Two apparent hits were my own instrument: `210x` and `404s` came from a
  regex matching **CP210x** and the phrase *"nothing 404s"*.

- **19:29** — **A 24/25 suite, and the failure was my own process hygiene.**
  `python -> browser integration` failed on
  `*** BROWSER DID NOT CONNECT TO PYTHON ***`. Not a regression: the isolated
  re-run passed clean, and the only tree change was a **comment-only** diff in
  `run.sh` — zero executable lines.

  The cause: I restart the demo instance on :8000/:8765 between landings, and
  the suite's own `pkill` raced that instance's **startup**.
  `test_integration` pre-cleans :8765 but cannot clean a process that binds it
  a moment later. Re-ran with both ports confirmed at 0 holders first.

  Written into `docs/WORK-QUEUE.md` as a standing rule: stop the instance,
  confirm both ports read zero, start the suite, restart the instance
  afterwards. Never concurrently.

- **19:32** — **Ran the real `--record` flag end to end. It works — and it
  carries a trap that would quietly wreck the demo's last resort.**

  The recorder had never been executed as a flag. `test_record_replay.py`
  writes a JSONL **by hand** and calls it *"the way `--record` does"* — the
  FORMAT was covered, the PRODUCER was not. Same shape as replay's missing
  cycle: the artifact tested, the thing that makes it untested.

  The real run: **1,887 rows**, valid JSONL, exactly the `t`/`elbow`/`wrist`
  keys replay consumes, `[main] clean shutdown` so `REC_FH.close()` in the
  `finally` ran under SIGTERM. Then 120 of those recorded frames drove
  **73 `T:1041` commands** to a fake arm. The round trip closes with real
  recorder output rather than a fixture.

  **THE TRAP.** `tools/synth_record.py` tells the operator, at lines 18 AND
  77, to replace `good_run.jsonl` with
  `python py/scrubbot.py --record recordings/good_run.jsonl`. Do that without
  a camera and `CAM=fake`'s near-motionless subject is what lands: measured,
  **elbow-x stdev 1.74 vs 34.94, wrist-x 0.48 vs 72.26 — 20x and 150x less
  motion**. It would still load, still replay, and show a person standing
  almost still, on the one run where everything else has already failed.

  **Nothing guarded that file's CONTENT.** `grep -c stdev` over
  `test_docs_match_code.py` and `test_record_replay.py` returned **0** — only
  its existence and its filename were checked. There is now a motion floor,
  and both replace-instructions say "with a real camera".

- **19:44** — **Plant sweep of `test_arm_protocol.py`: 11/11 discriminate.**
  No decorative assertions, against the ~1-in-4 rate this repo has shown
  elsewhere. That file guards the protocol the whole safety story rests on, so
  a clean result there is worth as much as a finding.

  **Swept in three passes, split by what each assertion needs**, because the
  file wedges when mutated (see the warning at the top of its estop block):
  - **8 pure-arithmetic**, falsified in-process by monkeypatching constants:
    `reachable()` beyond REACH_MAX / inside REACH_MIN / at HOME,
    `MAX_STEP_MM` against the 10mm-per-tick ceiling, both BOX bounds,
    REACH_MAX's derivation from the link lengths, HOME's own reachability.
  - **2 pty-backed**: T:0 reaching the hardware (falsified by swallowing the
    write) and no-motion-while-estopped (falsified by making the fake forget
    its own latch).
  - **1 feedback**: contact, driven by the torque signal.

  **A verdict I withdrew rather than recorded.** Contact first read
  DECORATIVE — but the probe set the torque flag and slept, and `contact`
  updates on a `poll_feedback()` READ, not a timer. So the CLEAN case never
  passed either, which makes the whole verdict worthless: a decorative-flag on
  an assertion whose positive control is broken says nothing about the
  assertion. Re-probed the way the real test does it: False -> True
  (torS=700) -> False. This is the fifth instrument this session that lied
  before the code did.

- **19:44** — **Queue hit zero a second time.** All 14 items closed. Refilled
  from the generators rather than stopping.

- **19:54** — **Walked `DEMO-SCRIPT.md` minute by minute and found the card
  BANNING the rescue the script rehearses.** The recovery card was walked
  earlier and produced three wrong rows; the script had never been executed
  the same way, and every entry in its operator column is an instruction —
  therefore a claim.

  **The contradiction.** Script, 1:22: *"if a splotch misses: press `1`,
  silently"* — during a cycle armed at 1:06. Card: *"Pick one: arm it and let
  it run, OR tap `1` `2` `3`. **Never both.**"*

  **The code sides with the script.** `main.js` flashes
  `CYCLE RUNNING — it will reset this` and pops anyway, and
  `test_cycle_conflict` pins that as correct: 1/2/3 is the crash fallback and
  must never be gated. The card's symptom is real but MISTIMED as a ban —
  `fire_reset()` runs in the RETREAT→IDLE transition, **2.5s after the scrub
  ends** (`scrubbot.py:701-707`), so a pop at 1:22 survives through the 1:44
  finale and the reset lands after it. Which is what the operator wants by
  1:52 anyway, where the script has them press `r` to prove it is live.

  **Why it mattered.** An operator who read the printed card and hit a missed
  splotch at 1:22 would freeze — the sheet taped to the laptop forbidding the
  thing they had rehearsed, mid-demo, in front of judges.

  **Three other beats checked out**, which is the point of walking all of
  them: the 0:48 debug window exists (the demo path runs without
  `--headless`), `r` at 1:52 resets splotches and counter and now also stands
  the character up, and the seven-item pre-set list matches what `run.sh` and
  the page actually need.

  Guard added so the two documents cannot drift apart again; plant-verified by
  restoring the ban, which turns two assertions red.

- **20:12** — **Adversarial re-read of today's 16 commits. It found a defect I
  introduced myself, in the one change I was proudest of.**

  The surface was small and that was the first finding: of 16 commits, only
  **two** touched `py/arm.py`, `py/scrubbot.py` or `web/main.js`, and the
  entire executable diff across all three was **5 lines** — the per-frame
  threshold refresh. Everything else was docs, tests, comments, assets.

  **THE DEFECT.** Those five lines assign `CFG.data.get(...)` straight onto
  the tracker. A string or `null` in any `dirt_*` key then raises
  `UFuncTypeError`/`TypeError` inside `fluor_mask` — **every frame, inside
  `vision_loop`** — which kills the vision thread mid-scrub. Before my change
  the values were read ONCE at startup, so a bad edit surfaced before anyone
  was watching. **I widened the blast radius while fixing the freeze**, and
  widened it onto precisely the file whose own `_comment` says
  *"HOT-RELOADS — edit mid-demo if needed"*. Measured: `'190'`, `'abc'` and
  `None` all raise; floats are fine.

  Fixed with a `_num()` coercion that falls back to the module default, and
  guarded: `'190'` -> 190, `'abc'` -> 190, `None` -> 190, `190.5` -> 190,
  `True` -> 1, none crashing.

  **And the fix broke my own earlier guard**, which is the smaller lesson.
  The freeze guard greps `tracker.v_min = CFG.data.get("dirt_v_min"` — a
  string that stopped existing the moment I wrapped the call. It failed on
  CORRECT code. A guard that names an expression is a claim about that
  expression, and mine went stale the instant I improved the line it watched.
  Re-targeted to accept either shape.

  Cost check on the refresh while I was there: **0.19us per frame**, 0.006ms/s
  at 30fps. Negligible.

- **20:25** — **Comment audit of `web/`: 25 MEASURED/VERIFIED claims,
  re-measured. Three were stale and one was flatly false.** The `py/` claims
  all held, so this was the untested half.

  **FALSE — the file's own header.** `avatar.js:3` said *"WHY THIS MODEL:
  skins:0. VERIFIED by inspecting the GLB — it is NOT a skinned mesh, it is
  six RIGID mesh parts."* The shipped model reports **skins=2, 32
  animations**. That text was true of the BLOCKY pack this file first loaded
  and survived the swap to the mini pack — while `limbLocalBox()`, thirty
  lines below it, exists precisely BECAUSE the mesh is skinned ("SKINNED
  MODELS HAVE NO MESH UNDER THE LIMB NODE"). A VERIFIED claim in a header is
  the one a future reader is least likely to re-check. Corrected and guarded.

  **STALE, conclusion intact** — the seated-mode figures at `avatar.js:161`
  read `rootY 0.000 / torsoY 0.176`; today all three poses measure
  **0.550 / 1.008**. Same blocky-pack era. The finding survives exactly: all
  three still agree to 3dp, so `sit` and `wheelchair-sit` rotate limbs and
  never lower the body.

  **OVERSTATED** — `avatar.js:186` claimed the rest limb direction is
  *exactly* `[0,-1,0]`; measured **[-0.014, -1, 0.011]**, and the z=+0.5 tip
  at **x=+0.467** against a claimed +0.479. Right direction, right magnitude,
  wrong word: the figure carries the A-pose splay the comment sets two lines
  earlier.

  **MISREADABLE** — `main.js:75` quotes sponge (-1.41, 2.31, 1.25) vs splotch
  (-0.80, 2.07, 0.39) without saying those are the BUG, not the state. Today,
  with the offset in place: **(0.68, 1.40, 0.21) vs (0.63, 1.33, 0.34), a gap
  of 0.16** — contact lands on the dirt. Labelled.

  **I broke the new guard three times before it worked**, each a different
  way, all mine: `NameError: _m2` (referenced state defined 144 lines later),
  then `NameError: json` (a conditional import whose condition was wrong, and
  which I never verified), and in between I "planted" it and read the restore
  as success — **a plant against a test that crashes either way proves
  nothing**. It now runs, prints three PASS lines, and goes red under the
  plant with a real FAIL line.

- **20:30** — **Drove a fresh clone all the way to a finished scrub: 3/3
  splotches, counter 100%, zero page errors.** The earlier clone test stopped
  at a served page, which is where the interesting part starts.

  **FINDING — a fresh clone serves the page and then EXITS.** `homography.pkl`
  is gitignored, so every clone has none, and `vision_loop`'s `calib.load()`
  refuses rather than fabricating a transform: page 200, socket never comes up,
  `[main] clean shutdown`, `No homography.pkl. Run: python py/calibrate.py`.
  That refusal is right — a made-up calibration puts the sponge ~30cm off a
  person's forearm, which is what the sentinel exists to prevent — and the
  asymmetry with replay is deliberate too: `replay_loop` has an
  `except SystemExit` fallback and says on screen that the coordinates are not
  real, because screen-only replay is safe and driving a real arm is not.
  **What was missing was the note.** The card's COLD BOOT block said `./run.sh`
  and footnoted only the OTHER missing prerequisite ("No venv?"), so a fresh
  machine hits a dead socket with no printed explanation. Fixed and guarded in
  both directions.

  **AND MY OWN ALARM WAS WRONG AGAIN — the fifth today.** After calibrating,
  the probe read `0/3 splotches` and I was one step from filing "a fresh clone
  cannot scrub". The clone's own log said it outright:
  `[fsm] ARM REFUSED — still estopped`, cleared by one `shift+C`. Not a
  fresh-boot condition: `estopped` defaults False at class AND instance level,
  my own boot log has **0** estop mentions, and the clone's FIRST boot had 0
  too — only the second, which inherited state from my `pkill -9` of the first
  mid-flight. My process, not the product. The refusal message did exactly what
  I built it to do earlier today: it named the state and the key that clears it.

- **20:40** — **Ran `tools/tune_dirt.py`, and swept every filename the docs
  name. Both clean — and the only fault was my own instrument again.**

  **`tune_dirt.py` works as documented.** The README promises
  `CAM=fake python tools/tune_dirt.py` teaches the UV controls with no camera
  and no lamp; it does. Banner prints, `_TracerCam` opens, and the loop finds
  **3 of 3** synthetic blobs every frame at the shipped thresholds (area 373
  each). `cv2.namedWindow` and `createTrackbar` both work in this environment,
  so the GUI path is real rather than skipped. The `p` key emits exactly the
  five keys that match `config.json`: 190 / 60 / 26 / 160 / 80.

  **My first run captured ZERO BYTES** — no banner, no traceback — which reads
  equally well as "silently fine" or "died instantly", so it said nothing at
  all. It was stdout buffering under `nohup` with the process killed before
  flush. Re-run with `-u` plus a liveness sample: alive at t+2s AND t+6s, 752
  bytes of output. That is the sixth instrument today to lie before the code
  did, and the second to do it by staying silent.

  **Filename sweep: 34 distinct paths named across the card, the demo script
  and the README — every one exists.** This is the class that cost the card its
  headline fallback earlier today (the replay file pointed at the weaker
  artifact), so it was worth sweeping the rest rather than assuming.

- **20:58** — **Plant-swept `test_docs_match_code.py` — the file that guards
  every other document — and found a section pasted into it twice.**

  The ZZFX patch block existed at lines **311 AND 427**: byte-identical, same
  sha256 `382f9577`, 19 lines each, **3 duplicated `check()` labels**. A sweep
  across every test file found exactly those three and nothing else, so it was
  one accident rather than a pattern — almost certainly an insertion of mine
  landing twice. Deleted the second copy, which also tidies the ordering since
  the survivor sits with the other `5x` sections.

  **Why the existing guard could never have caught it.** It counts `check(`
  lines live and compares them to the directive's figure inside a ±15 band, so
  a block pasted in twice inflates BOTH sides identically and the arithmetic
  agrees with itself. There is now a guard that fails on any duplicated
  assertion label in any test file — the thing a count cannot see.

  **AND I GOT THE CONSEQUENCE WRONG, which measurement caught before the
  commit.** I wrote that the directive had been claiming 429 while the honest
  number was 426. Measured both sides: **429 before the deletion, 429 after**.
  Removing 3 duplicated assertions and adding one guard whose own body mentions
  `check(` three times while scanning for duplicates nets out to zero. Deriving
  it a third way gave **448 call-sites** and **377 labelled** — three metrics,
  three numbers, each internally consistent. The duplicates were real; the
  count claim was not. The count line now says which metric it is, so the next
  person can re-derive it instead of trusting it.

- **21:01** — **Walked `docs/CALIBRATION.md`. Two real gaps — and the numbers
  I thought were wrong were my own probe, not the doc.**

  **Gap 1: it never named the sentinel.** The doc says `rm -f homography.pkl`
  and stops there, while `tests/fixture.py`'s own message says *"Delete both
  files"*. `calibrate.py:118` already retires `.homography-is-synthetic` on a
  successful solve — the right fix — but the doc never said so, so an operator
  following it watches a `THIS IS A TEST FIXTURE` banner print over a
  calibration that IS real. Someone who learns to ignore that banner has
  learned to ignore it on the day it is true, and it is the banner standing
  between the sponge and a forearm 30cm off target.

  **Gap 2: it never said the procedure needs a real camera.** `calibrate()` and
  `verify()` open `cv2.VideoCapture` directly — **0** fake-cam references,
  against **7** in `tools/tune_dirt.py`, which has a `CAM=fake` branch exactly
  so its UI can be learned dry. The clicking cannot be rehearsed on this
  laptop at all. Both gaps documented and guarded (4 assertions).

  **AND MY PROBE WAS THE LIAR AGAIN — the seventh today.** I measured
  6.4 / 13.3 / 37.4 mm against the doc's 14 / 27 / 60, and **298mm** against
  its **693mm**, and was one step from "correcting" a document whose other
  claims had all held. Every one of my figures was almost exactly HALF — a
  uniform ratio, which is the signature of a different setup rather than four
  independent errors. The doc uses the real `WORLD_MM` (A4 297x210, offset
  150/-105) viewed OBLIQUELY; I invented a flat axis-aligned rectangle and
  sampled at the frame centre, which halves the projective error.
  `test_calibration.py` reproduces **693 mm** and **14.1 mm** exactly, and it
  had corroborated those figures in the repo the whole time.

  The tell I should have read first: a consistent ratio across four independent
  measurements is an instrument fault. Four separately-wrong doc figures that
  all land on 2x is not a thing that happens.

- **21:12** — **Comment audit of `tests/`: found both docs citing a number the
  repo had ALREADY discredited — then wrote a guard against it that was a
  tautology.**

  **The finding.** `DIRECTIVE.md:202` and `README.md:93` both quoted
  **"121fps"** as PROOF the cartoon survives a SIGKILL. That figure came from
  the old probe which counted its OWN `requestAnimationFrame` callbacks — those
  keep firing at 60-120fps even when `renderer.setAnimationLoop` is completely
  dead, so it would have reported 121 with **zero** application frames drawn.
  Both files explain exactly that elsewhere (DIRECTIVE:403 and :603,
  README:121) **while still citing the number as success**. Corrected to the
  measured **240 app frames/s** — stable, 64 samples across five suite runs
  reading 240/242/254, never anything near 121.

  `test_signals`' contact-pose claim re-derived clean on the way:
  `forearm_z_mm 45.0 + contact_depth_mm -5.0 = 40.0`, matching
  "300,120,40 = 5mm INTO the skin plane" exactly.

  **AND THE GUARD I WROTE FOR IT COULD NEVER FAIL.** It asked
  `(not cites) or explains` — but both docs KEEP the explanatory passages, so
  `explains` is permanently True and the expression is `X or True`. Plant-
  verified the wrong way round: putting 121fps back in the SIGKILL row still
  PASSED. Rewritten to test the CITATION SITES — the specific lines that offer
  a number as proof — and re-planted: both now go red.

  **My plant was also wrong, separately.** Reverting `test_resilience` to the
  rAF probe "passed" because `info.render.frame` occurs **3 times** and
  `.replace(str, 1)` hit a COMMENT. Replacing all three turns it red. Same
  occurring-N-times trap that fooled me hours ago on `run.sh` and
  `fake_roarm.py` — third time today.

- **Adversarial re-read of today's guards: four tautologies, one of them
  older than today.** Method: for every check shaped
  `"literal" in <whole-file-text>`, count that literal's occurrences in its
  target. Two or more means a plant can delete one occurrence and the check
  stays green. 35 such checks, 7 multi-occurrence candidates, 5 cleared on
  inspection (the `ord()` key literals appear in the main FSM loop, the
  camera-dropout loop and the scripted loop — three occurrences is the correct
  number there). Two were dead:

  | Guard | Plant | Result |
  |---|---|---|
  | `"skinned" in _av_hdr` | delete the `isSkinnedMesh` traverse | **PASS** — the word survived 12x in prose I wrote myself |
  | `"resetAll()" in js` | delete the whole `r`-key handler | **PASS** — the symbol survives as a definition and a socket call |

  Retargeted at `"o.isSkinnedMesh" in _av_hdr` and
  `"e.key === 'r') resetAll()" in js`; both plant-verified red, both files
  byte-identical afterward. The `skinned` one was my **fourth** tautology
  today, which is a rate, not an accident: every guard I wrote against a
  file's full text risked matching the comment I wrote beside the code. The
  sharper finding is the other one — `resetAll()` predates today. A guard can
  sit green for weeks while the behaviour it names is gone, so the plant is
  not a step for new guards, it is the only thing that distinguishes a guard
  from a comment.


- **A green safety test went red in-suite and passed 3/3 alone: SIGINT was the
  one signal nobody registered.** The full suite came back **24/25** with three
  failures, all in `test_signals.py`, all SIGINT: the retreat never ran, **0**
  `T:0` stops were sent, and the arm ended at **z=40.0 — the contact plane,
  sponge parked on a forearm** — while SIGTERM and SIGHUP both retreated to
  z=123.5 on that same run. The tell was the command count: **480** commands
  after the signal versus 17 for the other two. The child kept scrubbing
  straight through it.

  **Two separate causes, and the environmental one nearly hid the structural
  one.**

  *Structural.* `main()` installed `_term_handler` for SIGTERM and SIGHUP and
  **not** for SIGINT — that one rode on Python's default KeyboardInterrupt
  disposition, which is **deferred while the main thread sits in a C call**, and
  this loop lives inside `cv2.waitKey` and pyserial reads. The asymmetry was
  invisible because `disarm_signals()` already covers all three, so the
  *second*-signal protection handled SIGINT while the *first*-signal path did
  not — and the test's registration loop checked `("SIGTERM", "SIGHUP")`, i.e.
  exactly the signals that were registered. A guard shaped like the code it
  guards cannot see what the code omits. `_term_handler`'s own docstring records
  `SIGINT -> 1 stop, z=123.5, lifted` as the *working* baseline that justified
  never registering it; that measurement was load-dependent all along, and today
  is the first time it lost. Fixed by registering SIGINT explicitly (all three
  signals now take the identical path instead of one depending on interpreter
  timing) and widening the loop to three. Plant: deleting the new line turned
  exactly one check red — `main() installs a handler for SIGINT` — with
  SIGTERM/SIGHUP still green and the file byte-identical after restore.

  *Environmental.* `tests/test_arm_protocol.py` had been alive **2h45m**, PPID
  1, still holding a pty, plus an `http.server` orphan from 19:08 and another
  from **two days ago**. `run_all.sh`'s preflight reaps `chrome-headless`,
  `py/scrubbot.py` and :8765 holders — a stale `tests/test_*.py` matches none of
  those, so nothing had ever reaped one. That is the best explanation for a
  retreat that failed under suite load and passed 3/3 in isolation on identical
  code.

  **The obvious fix for it was wrong, and this project's own notes caught it
  twice in five minutes.** Filtering orphans by `PPID == 1` would kill the
  process under test: this suite's own `py/scrubbot.py --replay` children
  reparent to init within seconds (measured — pid 57669, PPID 1, 39s old, gone
  before I could `ps` it again). That is the bogus-failure landmine
  `DIRECTIVE.md` already warns about for bare `pkill -f tests/test_`. **Age is
  the safe discriminator**, because the preflight runs before any child of this
  run exists, so nothing legitimate can be old. Plant: a 1-second-old process is
  spared, and the threshold reads `09:59 → fresh, 10:00 → stale, 01:02:03 →
  stale, 2-11:22:33 → stale`. Both directions proven, not just the killing one.

  After killing the orphans, `test_signals.py` is 19/19 with SIGINT at
  `17 commands, z=123.5` — the same shape as the other two.

- **Converted the four pollable safety sleeps, and plant-verified the polls cost
  bounded time rather than hanging.** `test_arm_protocol.py` had no deadline-poll
  idiom at all; four of its six safety-gating fixed sleeps are monotone-positive
  and convert cleanly (135 `T:0` count, 183 `fake.estopped`, 399 `T:999` rose,
  537 `T:112` rose). **Two must NOT convert:** 140 (`NO motion commands sent while
  estopped`) and 287 (`clear_estop REFUSES when a new estop raced it`) are
  negative assertions — the condition is already true at t=0 and the sleep exists
  to give a leak or a race time to APPEAR. Polling them would break out instantly
  and test nothing, a self-inflicted tautology. Both now carry a comment saying
  so.

  Plant: suppressed `estop()`'s `T:0` (sent `T:99999` instead) so line 135's
  observable never arrives. **Six assertions red** — `T:0 sent on estop`,
  `fake hardware is estopped`, `and the hardware really stopped`, and all three
  `a LATER estop still works` checks from the 7e2 block — at **exit 1, wall 132s**
  against a ~30s clean baseline. That cost is the polls each burning their full
  5-6s deadline and compounding. It is the right trade: a broken estop path now
  fails in bounded time instead of hanging, and the clean path is unaffected.
  `py/arm.py` restored identical.

  Also plant-verified the row-scoped card check: removing
  `` `1` `2` `3` on the projector `` from the camera-dead row **only** left one
  copy elsewhere on the card — so the old file-wide check would have stayed green
  — and exactly one assertion went red, with its precondition still passing.

- **Deleted a cross-surface coupling instead of guarding it, and the plant had to
  be inverted to prove it.** `_SPONGE` found the robot's sponge by matching
  `material.color` within 2 of `robotarm.js`'s accent constant — two surfaces
  stating one number with nothing pinning them, the same shape as the pump rate,
  and the **only** value-matched traversal in the repo (7 exist outside
  `web/vendor`; the rest match by type or name, which do not move silently). The
  better fix than a guard was to remove the duplicate: `makeRobotArm` now exposes
  `sponge` on its handle, and the probe reads `S.robot.sponge` with an explicit
  throw if it is absent. Literal and `getHex()` lookup both 1 → 0.

  **The plant is inverted** — success is GREEN, not red. Retinted `SPONGE` to
  `0x22ff88`: exit 0, 26 assertions, 0 failing. A retint is now invisible to the
  probe, which is exactly the claim.

  **And the plant harness lied to me first.** I wrote it to run the test under
  `venv/bin/python` — but this is a *browser* test and only `python3` carries
  playwright, so it exited 1 with `ModuleNotFoundError` and **zero assertions**,
  while its PASS-grep printed nothing. For a normal plant that silence looks like
  a pass; for an *inverted* one it looks like the green I was hoping for. Twelve
  tests need `python3`, four need `venv/bin/python`, and mismatching them produces
  failure that is indistinguishable from success. The harness now uses the right
  interpreter and **refuses to render a verdict unless ≥10 assertions ran**.

- **Two sliced negatives, one one-directional table check, and a fix of mine that
  was too weak to catch the failure it was written for.**

  A negative assertion over a *sliced* region passes when the region is right AND
  when the region silently shrinks. `"fire(" not in _replay_fn` (2,820 chars) and
  `"except SystemExit" not in _vl` (23,301 chars) both had that shape with no size
  bound — the same defect as `_fin`'s `[-1]` slice, which collapsed 1157 → 14 chars.
  A third looked identical and was **not** affected: `_cal_src` is the whole of
  `calibrate.py` with no slice, so its negative can only red by someone adding the
  thing. Left alone.

  **My first bound was `0 < len(...) < N`, and simulating it before shipping showed
  it was nearly useless.** A realistic truncation — a top-level `def` inserted a
  few lines into `replay_loop` — drops the slice to **152 chars** and `0 < len() <
  6000` stays green. The empty case is barely reachable; the plausible-fragment
  case is the one that bites. Lower bounds (`1500 < _replay_fn`, `10000 < _vl`) catch
  it, pass on the clean tree at 2,835 / 23,301 / 2,820, and leave 47–57% shrink
  headroom. Plant: the truncation reds both replay size checks while `vision_loop`'s
  stays green — the slices are independent.

  **Guard 1b asserted six browser keys appeared *somewhere* in the README** — a
  whole-file substring, one direction only, and silent about the eight Python keys.
  A key vanishing from the *card* passed. Now it extracts the key COLUMN of each
  table and asserts set equality both ways. Plant: renaming README's `v` to `vv`
  reds both directions.

  **The extractor was the defect before the docs were.** My first comparison read
  column 2 of README's three-column table (`| Where | Key | Does |`), found exactly
  one "key" (`test_dirt.py`), and swept `NO LINK`, `ARMED` and `run.sh` in from the
  card as keys. The tables had agreed all along — 16 distinct keys each, identical
  sets. **Count the columns before diffing two tables.**

  One thing I nearly mis-filed as a bug: the assertion-count guard read
  `~459 vs actual 462` and still passed. That is deliberate — its comment says
  "assertions drift constantly; allow a band but catch a big divergence," with a
  ±15 tolerance. It is a divergence detector, not an exactness guard, and re-syncing
  the number by hand after every landing is what keeps it meaningful.

- **A recovery-card row described a bug that had already been fixed, so its
  symptom could never occur.** The row read *"Nothing happens, no output at all →
  You are not in `~/Wheelgentic`"*. Executed, from a foreign directory:
  `~/Wheelgentic/run.sh` works and lands in the repo; `bash ~/Wheelgentic/run.sh` works;
  `./run.sh` fails with a loud **`no such file or directory`**. Nothing is silent,
  because `run.sh:15` is `cd "$(dirname "$0")"` — it self-locates.

  **The symptom text is a fossil of `run.sh`'s own documented bug #2**, quoted in
  its header: `[ -d venv ] && source ...` as a bare statement returned 1, the EXIT
  trap killed the process group, and you got *"Exit code 144, no output, nothing to
  debug"*. Lines 18-24 replaced that with a real `if/elif/else` and the symptom
  went with it — but the card kept describing it.

  That is the worst shape a card row can take: the **fix was right** while the
  **symptom was impossible**, so an operator scanning for what they actually saw on
  screen skips the row that would have helped. Rewritten to name the real error,
  and to mention that the full-path invocation works from anywhere. Four checks now
  pin the row to the behaviour — `run.sh` still self-locates, the card does not
  claim a silent failure, the row exists exactly once, and it names the real
  symptom.

  **My first plant of the self-location check passed, and the guard was the
  reason.** I wrote `'cd "$(dirname "$0")"' in runsh` — a bare substring — then
  planted by *commenting the line out*. The string survives inside
  `# cd "$(dirname "$0")"`, so the check stayed **green** while `run.sh` no longer
  self-located at all. My plant was clumsy and the guard was weak, and the clumsy
  plant is what exposed the weak guard: a real refactor that comments that line out
  would have kept it green forever. Retargeted at a **live, uncommented** line
  (`l.strip().startswith('cd "$(dirname "$0")"')`, exactly one match), and the
  re-plant reds it. Tenth tautology of this shape today — a guard matching its own
  commented-out target.

- **Converting a sleep to a poll exposed two decorative SAFETY assertions that the
  sleep had been hiding.** `test_consent_latch.py` waited a fixed 2.5s for the
  camera-death retreat, then asserted
  `any("camera lost" in t or "RETREAT" in t for t in transitions)`. **The FSM prints
  FOUR different RETREAT reasons** — camera lost (`scrubbot.py:403`), occlusion
  (519), pose frozen (535), and ordinary completion at 100% (714) — so the
  `or "RETREAT"` clause accepted a normal finish as proof of a camera failure.

  Plant: set the fake camera to never die at all. The assertion stayed **GREEN** on
  `[fsm] -> RETREAT (100%) — press 's' to arm again`. And on a clean run BOTH lines
  sit in the same transition list, so it had been passing on whichever matched
  first — the label was never what it tested.

  Its sibling was worse. `check("disarms on camera loss", S.ARMED is False)` —
  line 713 clears `ARMED` on ordinary completion (`# one cycle per keypress`), so
  that assertion could **never** distinguish a camera death from a normal cycle
  ending. Both now require `"camera lost"`, which is unique to line 403, and the
  re-plant reds both.

  **The poll did not cause this; it made it findable.** A fixed sleep and a poll
  pass identically on a green run, but converting one forced me to name the
  observable — and naming it is what showed the condition was not the one the label
  claimed. That is the argument for the whole conversion exercise: not that fixed
  sleeps are flaky (they are), but that writing down *what you are waiting for*
  audits the assertion underneath it.

- **A regex that matches nothing passes every assertion it was written to make.**
  `for x in set(re.findall(...)): check(...)` reports success on an empty
  population: the body never runs, nothing is asserted, and the section prints
  clean. Five such loops existed with no population floor —
  `test_docs_match_code.py` (recordings **4**, scripts **12**, CLI flags **3**,
  config keys **1**) and **`test_dirt.py:250`**, which enumerates 5 `dirt_*` keys
  out of `tools/tune_dirt.py` and lives in a file with no floor pattern at all.

  **The fifth one is the lesson.** I found the first four by auditing the file I
  happened to be working in; a sweep of the whole suite for the *shape* found the
  fifth. Scoping a fix to the file you opened is the enumerate-don't-name failure,
  and this one guards the hue controls the entire dirt detector rests on.

  Plant-verified **regex-side** rather than data-side: narrowing the pattern
  empties the population identically, while mutating a doc would have meant five
  operator-facing files and five byte-identical restores. Each of the five reds
  **only its own floor**, the other four staying green. And the fifth plant showed
  the vacuity directly — with the tuner population emptied, the five
  `is read by the FSM` assertions **vanished from the output** instead of failing:
  six assertions down to one, and without the floor that reads as a pass.

  Two floors sit at zero headroom on purpose. The config-key population is **1**
  (only `dirt_mode` is named in the card or demo script), and the flag floor
  equals its union at **3** — so a documented flag disappearing from every doc reds
  the guard, which is precisely the drift the section exists to catch rather than a
  false positive.

- **Three watchers, three wrong targets, one cause: I re-derived the pid instead
  of using the one I recorded.** Measured: `pgrep -f "bash tests/run_all.sh"`
  returns **four** pids — because my own watcher shells carry that string in their
  command lines. So `pgrep ... | head -1` picks a *watcher*, not the suite, and
  "pid-specific" was never specific. The launcher had already written the real pid
  to `/tmp/suiteN.pid`; I ignored it and re-derived one, twice, after supposedly
  fixing exactly this.

  `DIRECTIVE.md` already records the parent of this trap — *"`pgrep -f <pattern>`
  matches the shell that is evaluating the guard"* — which is the sharp part: a
  documented hazard, re-committed three times in one session, in three different
  costumes (generation-blind; pid-from-pgrep; pid-from-pgrep again).

  **A resolving anchor is not proof a landing is unapplied.** I built a classifier
  to tell applied staged-edit scripts from live ones by counting whether each
  anchor still resolves 1x, and it labelled **two already-applied** ones LIVE --
  because their replacement text deliberately quotes the anchor's own heading (the
  new entry is inserted *above* the old one, which it names). Re-running either
  would have double-applied it. Check for the REPLACEMENT's presence, not the
  anchor's absence.

  **RULE: read the identifier the thing under test already publishes.** I landed
  on "capture the pid at launch" and then found `run_all.sh` had been publishing it
  all along — `/tmp/.wheelgentic-suite.lock/pid` held **93580**, byte-identical to the
  pid I had separately recorded. My `/tmp/suiteN.pid` was redundant from the start,
  and the directive's own advice two hundred lines up already said to use the
  lockfile. Never re-derive an identifier from a pattern your own process matches;
  and before inventing one, check whether it is already on disk. And the corollary that cost the most
  time: a watcher's silence is not evidence — I could not distinguish "died before
  acting" from "still waiting" from "watching the wrong pid", and I guessed wrong
  each time.

- **A waiter cannot tell WHICH suite it is waiting for, and mine sat live for
  twenty minutes holding a stale apply.** I armed a background shell to poll
  `until ! pgrep -f "bash tests/run_all.sh"` and then apply two staged landings.
  It applied nothing, its output file stayed at zero bytes, and a "completed"
  notification arrived -- so I concluded it had died and wrote that down as the
  lesson.

  **It had not died.** It was still spinning at pid 85156, twenty minutes later,
  because I had launched ANOTHER suite in the meantime and its condition --
  "no run_all.sh is running" -- was true of no generation in particular. My
  `pgrep` pattern then failed to match it, which is how I convinced myself it was
  gone. Had it fired, it would have re-applied landings that were already in the
  tree; the anchors were consumed, so it would have exited 1 harmlessly, but the
  timing was entirely unpredictable.

  Two real lessons, neither the one I first recorded. **A waiter keyed on "is any
  X running" is keyed on nothing** when you relaunch X -- generation-blind
  conditions re-arm silently. And **a payload-carrying waiter is
  unverifiable**: I could not distinguish "died before acting" from "still
  waiting" from "acted and printed nothing", and I picked the wrong one. Wait in
  the background; act in the next turn after reading the tree.

- **The cartoon arm's spring CREEPS, and I nearly built a guard on a constant no
  test asserts.** Chasing the one unguarded number left in `web/` — the
  sponge-vs-splotch gap at the contact pose (0.16, stated at `main.js:80`, echoed
  in this file, measured by nothing) — I planned to drive `setPhase('scrub')`,
  wait the `wait_for_timeout(1400)` that two existing tests use, and read the
  world positions.

  **Simulated the actual spring first** (`robotarm.js:71`, `K = 9.0, DAMP = 0.80`,
  20% velocity retention per frame). It is heavily overdamped and creeps: the
  shoulder's −0.55 → 0.46 travel sits at **−0.097 at 1017ms** and **0.053 at
  1517ms** — barely half way — and still carries a **4.5% residual after a full
  5s**. Successive-frame delta drops under 0.1% of travel only at **~3817ms**. So
  **1400ms is nowhere near settled**, and a gap read there would have measured a
  mid-travel sponge — exactly the trap `test_estop_cartoon.py:21` records, where
  three probes mistook `setPhase('rest')` travel for a scrub.

  **No test ever claimed 1400ms meant arrival.** `test_cycle_conflict.py`'s
  assertion is `_done < 0.55` — "most of the travel is still ahead after 120ms" —
  which is true *because* the spring creeps. I would have inherited a number by
  pattern-matching its neighbourhood and called it settled.

  My first simulation "failed" by never reaching a 1% threshold and I read that as
  a broken instrument; it was the honest answer. A position guard here must poll
  **successive deltas** with a deadline — never a fixed wait, and never
  distance-to-target, because the residual never vanishes. Queued, unbuilt: the
  right guard is worth more than a fast one.

  Also found while there: **`0xffd94a` lives on two surfaces with no guard.**
  `robotarm.js:25` defines `const SPONGE`; `test_cycle_conflict.py:34` finds the
  sponge by matching `getHex()` within 2 of that literal — the only colour-match
  hunt in the suite, and the exact fragility `__wheelgentic`'s own comment says the
  `robot` handle exists to prevent. `makeRobotArm` returns
  `['placeNear','root','setPhase','strokeNow','update']` and no sponge. Exposing
  it deletes the coupling rather than guarding it. Queued.

- **A 24/25 suite, and the one red was a fixed sleep — not the tolerance, not
  the code.** `'the retreat target survives (FSM stopped overwriting it)'` is the
  guard for `6740a88` (*"CRITICAL: a FAILED estop left the FSM scrubbing over its
  own retreat"*). It asserts `math.dist(arm.target, HOME) < 2.0` after a failed
  estop.

  **Isolated, it reads HOME to a tenth of a millimetre — 5/5 runs, exactly
  `(235.1, 0.0, 234.8)`.** Not a near-miss. That is the signature of a scheduling
  miss, not a tight tolerance: the assertion slept `time.sleep(1.2)` and the FSM
  thread ran **no IDLE frame** inside that window while a browser suite loaded the
  machine (load 4.00). One IDLE frame calls `arm.go_home()` and restores HOME
  exactly, which is precisely why isolation is clean and the suite was not.

  **Widening 2.0mm would have hidden a real overwrite.** Replaced the sleep with
  a deadline poll on the observable — the transition printed *and* the target back
  at HOME — the same shape three other assertions in that file already use.
  Plant-verified by reverting `vision_loop`'s gate (line 462 only) to the
  pre-`6740a88` `if arm.estopped:`, which a failed estop leaves False: the
  assertion went **red at `target (261.2, 42.0, 40.0)`** — a forearm contact pose
  238mm from HOME, the exact bug — and it failed in **bounded time** (48s), not by
  hanging. Green on a good tree, red on the real defect, bounded either way.

  **My planter silently skipped a third time.** The gate string occurs **4x**
  (`vision_loop` 462, `remote_control_loop` 834, `replay_loop` 936, `main` 1105),
  so a whole-file uniqueness assert raised and the plant never applied — and the
  clean-tree PASS lines printed afterwards read exactly like "the guard survived."
  I had already written "plant by line number, not by unique string" earlier today
  and violated it anyway. The rule is now mechanical: a plant helper takes a line
  number.

  **Scope of the underlying problem: 30 sleep-then-check sites across 7 test
  files** (`test_arm_protocol.py` 13, `test_consent_latch.py` 8, the rest 1-4
  each), of which **10 gate a safety claim**. Every one is a latent red on a busy
  machine, and a flaky safety test gets ignored — which is worse than not having
  it. Queued.

  **I inflated that figure first and caught it on re-read.** The initial sweep
  said 39 across 8 files, because it counted nine sleeps that are the *body* of an
  existing deadline poll (`while time.time() < deadline: ... time.sleep(0.05)`) —
  the very pattern being proposed, not a defect. A second classifier then reported
  **"12 safety of 11 total"** for one file: `arm` was in its keyword list, which
  matches nearly every label in a robot-arm test, and overlapping 5-line windows
  double-counted. The impossible arithmetic is what exposed it. Two instrument
  errors inside one measurement of my own work — which is the argument for
  printing the population and the per-file lines rather than a single number.

- **The emergency stop's own guard could not fail, and walking the card's SPACE
  row is what exposed it.** The row (`SPACE` → sends `T:0`, arm freezes) is true
  as written — executed 10/10 against the pty fake: exactly one `T:0`, **zero**
  motion commands while estopped, hardware stopped, latch agreeing, `r` clearing
  it, 33 commands resuming after. All three SPACE bindings call `arm.estop()`.

  **The guard was the defect.** `SPACE -> estop (python)` asserted
  `"ord(' ')" in py and "arm.estop()" in py`. I deleted the **main FSM's** SPACE
  binding — the one the card documents, the one an operator reaches for with a
  sponge on someone's forearm — and the check stayed **GREEN**, because those
  strings survive 3x and 6x in the camera-dropout and scripted loops. An
  occurrence audit of all fifteen entries in that block:

  | entry | needle counts | verdict |
  |---|---|---|
  | `SPACE -> estop` | [3, 6] | tautology |
  | `r -> clear estop` | [3, 5] | tautology |
  | `q -> quit` | [3] | tautology |
  | `1/2/3 -> pop` | [2] | tautology |
  | the other eleven | each has a 1x anchor | discriminate |

  Fixed by slicing to the main-FSM handler block (all six keys are 1x inside it)
  behind an explicit **"the slice resolved"** precondition — measured: renaming
  the end marker empties the slice and every key check would pass vacuously,
  the identical failure `_fin`'s `[-1]` slice had. Each key plant-verified
  individually: SPACE reds only SPACE, r only r, q only q, 1/2/3 only 1/2/3.

  **The plant method itself failed twice before it worked, and both failures
  looked like passes.** First, my planter asserted the target string was unique
  file-wide; `r` occurs 2x and `q` 3x, so it raised and **skipped the plant
  entirely** — and the clean-tree PASS lines printed afterwards read exactly like
  "the guard survived." Second, my slice's START marker *was* the SPACE binding,
  so planting SPACE collapsed the whole slice and reddened all six keys plus the
  precondition — a plant that cannot isolate its target proves nothing either.
  Fixed by planting **by line number** and moving the start marker to the
  `cv2.waitKey` line above the block, which is 1x and is not itself asserted.

  **And the card never documented the third NO-LINK message.** `main.js` shows
  three, each naming a different Python key: `s` (532), SPACE (562), `r` (579).
  The card explained the first two. The `shift+C` → "press r in the python
  window" case — which fires precisely when someone is trying to clear an estop
  from the projector with the socket down — appeared nowhere. Only the SPACE one
  was pinned by any test. Card fixed; a 6-check citation guard now ties all three
  messages to their remedies, plant-verified by removing the card's remedy and
  watching only that one check red.

- **Cross-surface signal sweep: the surfaces agreed, but nothing held them
  together.** Four surfaces must stay in lockstep — `run.sh:71` traps
  `INT TERM HUP`, `main()` registers `_term_handler` three times,
  `disarm_signals()` ignores the same three, `test_signals.py` drives and checks
  three. All four agree today. **No guard pinned them to each other**, which is
  precisely how SIGINT sat unregistered for weeks while three of the four
  surfaces already named it: the disarm loop covered SIGINT (second-signal
  protection) while the first-signal path did not, and the test's own loop
  checked exactly the two that were registered. Added an 8-check agreement
  guard — registrations as a SET equal to the disarm set, run.sh's trap covering
  each signal by name, and the test driving all three. Plant-verified twice, and
  the plants show it discriminates rather than merely detecting absence:

  | Plant | Went red | Stayed green |
  |---|---|---|
  | drop `SIGINT` from the disarm tuple | `covers the same three`, `SAME set` | registrations, all three trap checks |
  | drop `HUP` from `run.sh`'s trap | `trap still covers HUP` | INT, TERM, registrations, SAME set |

  The sweep also caught **my own prose going stale the same day I wrote it**:
  `test_signals.py:91` and `:144` still said "two signals" / "both signals",
  four lines above the loop I had just widened to three. Nothing guards comments,
  which is the argument for reading your own diff as a stranger would.

- **Untested-path hunt on the estop/clear surface: no defect, and a probe I
  should never have built.** `test_arm_protocol.py:277-340` already races
  estop-vs-clear across ten offsets of `clear_estop`'s ~61ms window, with two
  retries to tell a real race from suite load. I built a standalone probe for
  that exact path before grepping the suite for it. **Grep the directory before
  building the experiment** — the cost here was only a scratch file, but the
  habit is what matters.

  The one genuinely uncovered variant: **clear-vs-clear.** Every other
  `clear_estop` call site in the suite is serial, yet two entry points can fire
  together — the python `r` key and the projector's `shift+C` over the socket,
  on different threads. Measured clean at gaps 0.0/0.01/0.05: the latch always
  agrees with the hardware, `_estop_gen` reaches **2** (both clears bump it), and
  a LATER estop still lands. That last one is the assertion worth having: a
  generation counter running ahead of the hardware is exactly how a future
  emergency stop would be refused for no reason. The `clear_estop` re-seed path
  really executes under the race (`the arm MOVED 12mm during the estop —
  re-seeding the limiter` fires on the second clear), so the test is not
  short-circuiting.

  Landed as section **7e2 with no retry wrapper**, deliberately. 7f needs two
  retries because its `threading.Timer` gets scheduled late under load; 7e2 uses
  `Thread.start()`/`join()` with no timer, so there is nothing for load to delay
  — 9/9 across three trials at load 3.7 with the full suite running. Recorded in
  the block itself: if this one ever flakes, the timer-free shape means a real
  race, not a busy machine, so it must not be papered over with retries.

- **Comment audit of the `MEASURED` blocks: a figure no guard covered, and a
  prior audit's explanation that was a guess.** 41 claims enumerated across 15
  files; re-derived the load-bearing ones.

  **The pump rate disagreed with itself across five surfaces.** `py/arm.py:305`
  says deadline pacing measures 39.9 Hz; this repo's own earlier comment-audit
  entry recorded 40.0; five fresh runs here read **39.9-40.0** (naive
  `time.sleep` loop: **31.3-31.7 Hz**, against the recorded 34.8). But
  `DIRECTIVE.md`'s summary table row and its lessons list both said **39.5 Hz** —
  and the audit pass that re-measured 40.0 never noticed the table nine hundred
  lines above it still said 39.5. **No guard covered any pump-rate figure**, so
  it could drift forever. Both sites corrected; a five-check citation-site guard
  added, plant-verified three ways (39.5 back into the row → only the row check
  red; into the lesson → only the lesson check red; `arm.py`'s 39.9 → 41.2 → only
  the code-consistency check red). I left `arm.py:301`'s 34.8 alone: the earlier
  pass deliberately kept it, on the grounds that the real 31.3 makes the claim
  *understate* the problem, and re-litigating a settled call is not an audit.

  **The One Euro residuals are not portable numbers, and the reason the earlier
  pass gave was wrong.** That pass read 2.50px pixel-space vs the recorded 2.96
  and attributed the gap to "gaussian-seed variance". Measured: across six seeds
  at N=4000 the spread is **0.04-0.07px** — seed cannot explain it. **Sample
  count and warmup move it 0.20-0.35px**, and frame rate moves the normalised
  figure by 0.2px. Four passes have now read 2.50 / 2.75 / 2.96 / 3.01 px for the
  same row. So I swept 54 conditions (fps 24/30/60 × N 150/1000/4000 × 6 seeds):
  pixel-space removes **−0.1% to +1.2%** (inert, always), normalised removes
  **+58.2% to +75.6%** (works, always), and the separation never falls below
  **57 percentage points**. That is the claim the comment now makes — with one
  worked example carrying its own conditions, because `dt` is wall-clock
  (`self.prev_t`), so a fixed fps was a fiction from the start. Under
  `test_vision_real.py`'s exact conditions the original 66% reproduces exactly.
  I had drafted "state the ratio, it's portable" — then measured the ratio
  swinging 61→76% and deleted my own fix before writing it.

  **`web/avatar.js` stated two different limb windows four lines apart:**
  `[0.15, 0.85]` and `[0.20, 0.85]`, while both code paths use `0.20 + t*0.65`.
  The code settles it; the stale prose now says so.

  Confirmed exact, no change: `MAX_STEP_MM` 6.0 × 40 Hz → 240 mm/s (measured
  239.5-239.9); `scrubbot.py:364`'s per-frame threshold refresh; `main.js`'s
  0.16 contact gap; and `arm.py:422`'s "guarded by `test_arm_protocol.py`" —
  which is true: `_stall_run()` drives a real stalled write and asserts the
  estop does not latch. A claim naming a test is a claim to check, and this one
  held.

- **Plant sweep of `test_signals.py`: 7 plants, one decorative guard, one
  latent-fragile slice.** The file guarding the worst bug this project has had,
  swept assertion by assertion.

  | Plant | Expected red | Result |
  |---|---|---|
  | `HOME` z 234.79 → 60 (retreat moves, stays low) | ended ABOVE the skin plane | ✅ red at z=55.7, others green |
  | `go_home()` → no-op | MOVED after the signal | ❌ **stayed GREEN at 18 commands** |
  | `disarm_signals()` loop emptied | disarms all three + both second-signal | ✅ 3 red, control green |
  | `disarm_signals()` moved after `go_home()` | calls it BEFORE the retreat | ✅ exactly 1 red |
  | `close()`'s `T:0` suppressed | a stop was sent | ✅ 3 red at 0 T:0, retreat green |
  | `disarm_signals()` call deleted from `finally` | finally calls it + ordering | ✅ 2 red, function check green |
  | `go_home()` → no-op, AFTER the fix | MOVED after the signal | ✅ red: `z=40.0 vs contact 40.0` |

  **The decorative one: `the arm MOVED after the signal`.** It asserted
  `moves > 0` — a raw count of `T:1041` commands. With `go_home()` a *total
  no-op* it still read 17-18 commands and passed, because the 40Hz pump emits
  whether or not anything retreats. No plausible break of the retreat could
  ever turn it red, while its label promised the opposite. Retargeted at
  displacement: `abs(final_z - CONTACT[2]) > 1.0`. Same plant, same code,
  opposite verdict — `18 commands, final z=40.0 vs contact 40.0`, **red**. That
  is what distinguishes a retarget that works from one I merely believe in.

  **The latent one: `_src.split("    finally:")[-1]`.** It reads main()'s
  `finally` block correctly today, but only because main()'s is the *last* of
  the two in a 1144-line file. Measured: appending one `try/finally` after
  `main()` collapses that slice from **1157 chars to 14**, and both
  `disarm_signals()` and `arm.go_home()` vanish from it — check 205 would go red
  for a reason unrelated to the code and 207's ternary would silently yield
  `False`. Now sliced from `def main()` first. Nobody broke this; it was one
  future edit away from breaking itself, which is the same shape as the SIGINT
  registration it sits beside.

  Two of the three `T:0`/retreat assertions turn out to test *different
  functions* — `a stop was sent` exercises `arm.close()`, `ended ABOVE the skin
  plane` exercises `arm.go_home()`. Planting one and watching only the other is
  how a sweep talks itself into a false pass.

- **The key-binding sweep found the docs correct and my instrument broken.**
  Browser handles 11 keys, Python 7. I grepped the recovery card for
  `` | `key` `` rows, concluded five keys were undocumented — including `x`, the
  projector-side **emergency stop** — and was one edit from "fixing" a card that
  was already right. The card **bolds** its important rows (`| **`x`** |`), so
  my own pattern skipped exactly the entries I then reported missing. Ninth
  instrument today to lie before the code did, and the most dangerous kind:
  it would have turned a true document into a false one. **Never grep a doc
  table with a pattern that assumes uniform cell formatting** — enumerate the
  rows, then match.

- **00:09** — **Walked the recovery card's last two rows. Both TRUE, both
  unguarded, now pinned — and the queue's "needs a browser" premise was wrong;
  neither needed a port.** "Browser blank" is right for a subtler reason than it
  reads: `web/index.html`'s import map names ONLY `./vendor/three.module.min.js`
  and never mentions `three.core.min.js`, but that module carries
  `from"./three.core.min.js"` **2×** — so the browser fetches a file no HTML
  references, which is exactly why a missing core reads as "blank page, no
  obvious cause". `vendor.sh:14` already called it "THE ONE PEOPLE MISS".
  "No sound" is true **by construction**: `play()` early-returns on
  `!audioReady` and all four audio call sites are individually try/catch-wrapped,
  so a skipped `Enter` reaches no zzfx call at all. Sections **5d + 5e**, 13
  assertions. **Two of my OWN draft guards were blind and were caught before
  landing.** A `>= 2` whole-file floor survived deleting the very site it
  guarded — the curl line names the file twice, so the occurrence count is 3, and
  **`grep -c` reported "2" because it counts LINES, not occurrences**. And a
  whole-file needle was answered by the wrong site: deleting the fetch left the
  sanity-loop mention, satisfying `"x" in whole_file` on its own. Both retargeted
  to the site making the claim and asserted by **equality**, which also catches a
  duplicated fetch. 9 live plants, 9 reds, each firing only its own assertion,
  tree restored byte-identical.
- **00:09** — **`web/style.css` NEVER EXISTED, and the backup-video digest was
  blind to two real assets.** The staleness guard sha256s a tuple naming
  `web/style.css`; the page loads `./hud.css`, and the digest loop skips missing
  files **silently** — so `hud.css` and `robotarm.js` (imported by `main.js:6`,
  holds the arm spring) sat outside the digest entirely. A demo-visible
  regression in either would land while the guard said the video still matched.
  The same ghost tuple was duplicated in `tools/record_backup.py:199`; both fixed
  together, because a drifting pair stamps a digest the test can never reproduce.
  Video re-recorded (49.1s, two full cycles) so the stamp covers all six files
  for the first time. Third instrument-shaped defect of the day found by asking
  what a guard does NOT cover rather than whether it passes.
- **00:09** — **`web/avatar.js`'s splotch comment described the pack the file no
  longer loads.** Two stacked MEASURED blocks stated the rigid Kenney geometry
  ("arm runs y=+0.1 .. y=-1.0", "the limb extends in -Y — which is why REST is
  (0,-1,0)") in present tense, directly above code whose primary branch measures
  the axis dynamically because the shipped skinned mini pack's arm bone runs
  **+X** — a split the file already recorded thirty lines above. **Qualified, not
  deleted:** the -Y branch is still live, because `limbLocalBox`'s rigid return
  supplies `along:'y'` and **no** `from`, and the branch test gates on `L.from`.
  Section **1i2** pins that split plus the `0.20 + t*0.65` range both branches
  share (4 assertions, 5 plants — including range-drift from either side).
  Count triple re-synced **474/478/416 → 491/495/433**, re-measured by each
  metric's own rule rather than by adding a delta.

---

## 7. COMMANDS

```bash
cd ~/Wheelgentic
PY_PW=python3 bash tests/run_all.sh          # full suite (5 checks)
./run.sh                                      # live
REPLAY=recordings/synthetic.jsonl ./run.sh    # no camera, no arm
python py/vision.py                           # hour-0 go/no-go 1
python py/arm.py                              # hour-0 go/no-go 2
python py/calibrate.py                        # 4 corners + tape verify
```

Test interpreters differ: `PY_CV=./venv/bin/python` has cv2 AND mediapipe,
`PY_PW=python3` has playwright. The suite checks both and refuses to run
half-blind.

The old `PY_CV` default was `/tmp/sbtest/bin/python`, which has cv2 but NOT
mediapipe -- so `test_vision_real` and `test_mirror` failed on every full run
while `quick.sh` (which prefers `./venv`) passed them, and the suite reported
19/21 as if the tests were at fault. The preflight only checked `cv2`. It now
checks mediapipe too, and `/tmp` is no longer on the default path at all --
it is wiped on reboot, which would eventually have taken PY_CV with it.

---

## 8. THE HARDWARE GAP — state this honestly

**No RoArm-M2-S is present.** It cannot be tested against real hardware here.
Phase 1 builds a protocol-level fake that speaks the real JSON dialect over a
pty. That catches wire-format, rate, rate-limiting and estop-logic bugs.

It does NOT catch: real servo dynamics, brownout under load, actual reach
envelope, CP210x driver behaviour, or firmware quirks. **First contact with
real hardware is Tyler's hour-2 go/no-go** (`python py/arm.py`), and the
directive must not imply otherwise.
