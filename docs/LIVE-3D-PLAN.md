# The system, and the plan to finish it

**Third version, 2026-09-19.** The first two described a projector demo with a
panel on a keyboard shortcut. That was coherent step by step and wrong as a
whole: the product's front door is Crystal's `carechair` UI, and our 3D view is
what her buttons open. This version starts from the system and lets the steps
fall out of it.

Everything asserted here was read from the code or measured on the hardware this
session. Where something is unverified it says so.

---

## 1. What the product is, in one picture

```
   PERSON
     |  presses "Showering" / "Take Meds" / "Eating", or speaks
     v
   ┌──────────────────────────────────────────────┐
   │  carechair UI          (Crystal)             │   browser, :5173
   │  index.html · app.js · voice-client.js       │
   └───────────────┬──────────────────────────────┘
                   │  fetch /api/task · /api/stop · /api/vitals
                   v
   ┌──────────────────────────────────────────────┐
   │  carechair server      (Crystal)             │   node, :5173
   │  server.js · robot-adapter.js                │
   │  ROBOT_MODE=demo|live · ROBOT_BACKEND_URL    │
   └───────────────┬──────────────────────────────┘
                   │  POST/GET over HTTP  ← THE SEAM WE MUST BUILD
                   v
   ┌──────────────────────────────────────────────┐
   │  care backend          (us)                  │   python
   │  serves /api/task /api/stop /api/vitals      │
   │  owns the FSM, the safety governor, vision   │
   └───────┬──────────────────────────┬───────────┘
           │ websocket 15Hz           │ reads (never commands)
           v                          v
   ┌────────────────────┐   ┌──────────────────────────────┐
   │  3D view   (us)    │   │  dimOS stack   (Justin)      │
   │  three.js, the     │   │  on the GB10, owns the arms  │
   │  cartoon + arms    │   │  bridge :7790 · viser :8095  │
   └────────────────────┘   └──────────┬───────────────────┘
                                       │ CAN 1 Mbit/s
                                       v
                                  TWO OpenYAM ARMS
```

Three people are building three parts of one machine. The parts barely overlap,
which is why they have stayed compatible by accident so far. They now have to be
joined deliberately.

---

## 2. The four seams, and which exist

**Seam A — UI to server.** Crystal's. **Built and working.** The browser calls
her Node server; `server.js:63-71` routes `/api/stop`, `/api/task`, `/api/vitals`
to `robot-adapter.js`.

**Seam B — her server to our backend.** `robot-adapter.js` already speaks it:
it POSTs `{command_id, category, action, target, item, source}` to `/api/task`,
and GETs `/api/vitals`. Configured by `ROBOT_MODE` and `ROBOT_BACKEND_URL` in
`.env`. **Her half is built. Our half does not exist.** This is the single
biggest gap in the product.

**Seam C — our backend to the arms.** Justin's dimOS stack owns the arms.
`arm_dimos.py` already exposes `real_joints6()`, `measured_joints()`,
`actual_world()`, `loads()` and `model_error()`. **We read. We never command
through our own driver** — `DIMOS.md` is explicit that two stacks on one bus
produce "random failures", and somebody is running one right now.

**Seam D — our backend to our 3D view.** The websocket at 15Hz. **Built**, and
already carrying governor verdicts and body-tracking facts. It does not yet
carry joint angles, and the page does not yet read what it does carry.

---

## 3. Why this shape, and not the obvious alternatives

**Why our backend serves HTTP rather than her server talking to dimOS.**
Because the safety reasoning lives on our side. `py/governor.py` refuses a
target above head height in 0.01ms. If her UI reached the arms directly, every
command would bypass it. One door into the machine, and the guard stands in it.

**Why we read the arms instead of driving them.** Two dimOS stacks on one CAN
bus answer to the same names. Measured today: Justin's stack is live, the frame
counters climbed 9.96M → 10.98M in half an hour, and his Viser view answers
HTTP 200 on port 8095. Starting a second would break his work and could move a
physical arm unexpectedly.

**Why the 3D view draws from numbers rather than receiving video.**
`py/scrubbot.py:68-70` states the rule the page depends on: *"EVENTS ONLY —
never pose. If this socket dies the cartoon still mirrors the person."* A video
feed inverts that; the picture blanks when the other machine hiccups. Six joint
angles is ~50 bytes. A posed body is 5,824 cells, which `livebody.py` explicitly
refuses to send.

**Why "whatever is real" is an architectural property, not a promise.**
Crystal's adapter already returns `status: 'simulated'` in demo mode and
`{status: 'disconnected', readings: null}` for vitals it cannot reach — and her
Vitals page shows `—` and "No readings yet" rather than inventing a number. Our
backend must preserve that distinction end to end: every response says whether
it came from hardware. A number that cannot be measured is absent, never faked.

---

## 4. What is true right now

### Working, verified this session

| | |
|---|---|
| MediaPipe pose | 30fps, pose on 126/180 frames against a real person |
| Cartoon mirroring a live person | torso moved 0.0388 rad between samples; HUD `ARM ● LINKED` |
| Safety governor in the live path | allows in-workspace; refuses out-of-reach and above-head in 0.01ms |
| Websocket | 15Hz, page ignores unknown keys so adding fields is free |
| Crystal's UI + voice stack | complete; robot adapter written against three endpoints |
| Justin's arm stack | live on the GB10; ~11M CAN frames, zero errors |
| The measured rig | two arms, 850mm apart, 550mm up, 200mm ahead, facing each other |
| Depth on the GB10 | 848×480, 85% valid pixels — when the camera is plugged in |

### Missing or wrong

| | |
|---|---|
| `/api/task`, `/api/stop`, `/api/vitals` | **do not exist on our side** |
| `ROBOT_MODE` | still `demo`; nothing is plugged into her adapter |
| Vitals hardware | no sensor is wired; her page correctly shows `—` |
| Joint angles on the wire | zero |
| Page reading `m.body` | zero occurrences |
| Arm count | data says 3, HUD says FOUR, tests demand 4 |
| Arm scale on screen | oversized; last screenshot showed them engulfing the person |
| RealSense on the GB10 | unplugged |
| A motor observed moving | never, by me |

---

## 4a. Progress, 2026-09-19

| Phase | State |
|---|---|
| 1 — parts agree | **Landed.** `body.json` now carries two arms; `web/index.html` no longer says FOUR ARMS. Scale work verified by screenshot. Surfaced §6a. |
| 2 — the seam | **Landed.** `py/careapi.py` serves `/api/task`, `/api/stop` and `/api/vitals` on `127.0.0.1:8770`. Every task passes the governor first; a refusal never reaches the FSM. Verified with her own `requestJson` code. `docs/SEAM-B-ENDPOINTS.md`. |
| 3 — live state | **Landed, from commanded state only.** `EVENT["joints"]` carries six angles per commanded arm and `EVENT["limbs"]` carries the person's shoulder, elbow and wrist per side. Every payload says `src`, and today it always says `commanded` -- attaching to Justin's stack stayed forbidden, so no encoder was read. `web/robotarm.js` poses from the angles through the spring it already had; the HUD reads `ARM ● LINKED · CMD`. |
| 4 — her view | **Landed.** `?view=bathe|meds|eat|vitals` frames the scene per task; an unknown key falls back to the full scene. `web/carechair-embed.js` attaches to her UI from the outside and opens it -- her side is ONE line in server.js's static table plus a script tag, and her own code is untouched (verified by loading her page with the script blocked and unblocked: identical). Each view states ARMS live / commanded / not connected. Screenshots read. |

**Closed, 2026-09-19: the slab arms.** `armgeom.json` carried a 968mm-thick
forearm on a 458mm link and both arms drew as panels covering the person. Two
causes, both now fixed. `tools/export_armgeom.py` was re-run so the widths come
from the same robot as the lengths, and `scrub3d/armmesh.py` now REFUSES to
measure the RoArm's meshes against the OpenYAM's axis rather than returning a
radius seventeen times too large. `loadArmGeometry` gained the width check this
section asked for -- a link wider than it is long is rejected and the known-good
fallback stands. Proven by feeding it the exact bad file: the arms render
correctly because the file is refused.

Re-derive this table by running, from the repo root:

```bash
python3 -c "import json;print('arms:',len(json.load(open('web/assets/body.json'))['arms']))"
grep -c 'FOUR ARMS' web/index.html          # 0 = phase 1 landed
ls py/careapi.py 2>/dev/null                # exists = phase 2 started
grep -cE '"joints"' py/scrubbot.py          # >0 = phase 3 started
```

---

## 5. The plan

Ordered so that each step makes the *system* more whole, not just its own file.
Every step ends with the demo running and the page loading.

### Phase 1 — Make the parts agree (no hardware, no coordination needed)

**1.1 Settle the arm count at two.** `body.json`, `web/index.html` and
`tests/test_scrub3d_merge.py` currently give three different answers. Bake from
`live_rig_openyam.json` — the measured file. The tests enforce the old invented
ring; correcting them is fixing a stale expectation, and the commit must say so.

**1.2 Fix the arm scale and mount height.** Positions come from the measured rig;
size does not. Derive it from the same numbers — 550mm up, 200mm ahead, 850mm
apart — so the chair, the person and the arms read as one machine.

*Verify: screenshot, read the image, arms mounted rather than swallowing.*

### Phase 2 — Build the seam that does not exist (seam B)

**2.1 Serve the three endpoints.** A small HTTP server beside the websocket:

- `POST /api/task` → `{command_id, category, action, target, item, source}`.
  Validate, pass through the governor, hand to the FSM. Return whether it was
  accepted, refused (with the governor's reason), or could not be attempted.
- `POST /api/stop` → the existing estop path. This must work even when
  everything else is broken.
- `GET /api/vitals` → real readings, or `{status:'disconnected', readings:null}`.
  **Never a plausible-looking number.** No vitals sensor is wired; saying so is
  the correct output.

**2.2 Make her UI point at us.** `ROBOT_MODE=live`, `ROBOT_BACKEND_URL=<us>`.
Idempotency matters: her adapter sends a `command_id` and deliberately does not
retry, *"a lost response must not duplicate a physical task."* We must honour
that — the same `command_id` twice is one task.

*Verify: press Showering in her UI, watch our backend receive it and the FSM
respond. That press is the first time the two halves of the product touch.*

### Phase 3 — Show what is really happening (seam D)

**3.1 Joint angles on the wire.** `EVENT["joints"] = {a0:[6], a1:[6]}`, plus
`model_error()` — the honest gap between commanded and measured. Sourced from
Justin's stack via `--attach` when it is up; from our commanded state when it is
not. **Attach only. Never start a second stack.**

**3.2 The page draws them.** `web/robotarm.js` poses from received angles,
interpolated 15Hz→60fps using the spring already in that file. With no backend
the page must look exactly as it does today.

**3.3 The body.** Publish shoulder/elbow/wrist per side. 2D pixels without
depth; `lift()`'s world points with it. MediaPipe gives landmarks *and* a
person/background mask in one pass; depth supplies distance, taking the nearest
surface rather than the average — an elbow over a stomach must not land inside
the torso.

*Verify: wave one arm, the correct cartoon arm moves. `tests/test_mirror.py`
exists because this was once backwards.*

### Phase 4 — The view her buttons open

**4.1 The 3D view as an embeddable surface.** Her UI opens it when Showering,
Take Meds or Eating is pressed. It shows the real arms doing the real thing,
framed for that task. The main projector view is unaffected.

**4.2 Honest per-component state.** Each view says what it is showing: live
hardware, commanded-not-measured, or not connected. Her adapter already models
this distinction; the 3D view must not quietly lose it.

*Verify: press each button in her UI, screenshot each resulting view, read every
image.*

---

## 6. What breaks if the order changes

Stated because the order is the part that is easy to get wrong.

**Endpoints before joint angles.** Seam B is what makes this one product rather
than two. Without it the 3D view is a demo nobody can reach from the UI.

**Arm count before drawing.** The wire format carries a per-arm array. Publishing
three arms and then changing to two means rewriting the consumer.

**Scale before the embedded view.** Framing a broken rig hides nothing.

**Governor before endpoints go live.** The moment her UI can send a task, every
task must pass the guard. Wiring the door before the lock is the wrong order on
a machine that touches a person.

**Attach before publish.** There is nothing real to publish until joints arrive,
and starting our own stack would break Justin's.

---

## 6a. THE ZERO WAS A SOFTWARE BUG, NOT THE HARDWARE — corrected 2026-09-19

**The first version of this section was wrong, and the operator was right to
reject it.** It reported that the measured rig reaches nothing and asked for the
mounts to be moved. The arms reach fine in real life. The bake was modelling a
different, smaller robot.

`scrub3d/kinematics.py` describes the RoArm-M2-S: 527mm of reach, base limited
to +-90deg. The arms on the chair are Anvil OpenYAMs: 742.6mm of reach, base
-150deg..+180deg. `scrub3d/kinematics_openyam.py` had measured them correctly
off dimOS's URDF and exports `SURFACE` for exactly this substitution, and its
docstring names `SCRUB3D_ARM=openyam` — but **nothing ever read that variable**,
so all ten importers silently got the smaller arm.

A 600mm target solves under OpenYAM and fails under RoArm. The mounts sit 425mm
off centre, so a body lies squarely in the difference. That is the whole of the
zero.

Wired at the foot of `kinematics.py`; default stays RoArm so no existing run
changes by surprise. Re-baked with the flag:

```
  before (RoArm model)   owned 0 / 0        covered 0.000
  after  (OpenYAM model) owned 52 / 199     covered 0.173   (body-b)
```

**Still open, and now an honest question rather than a false one.** The
full-size default body still bakes to zero; only `body-b` (86% scale, arms
dropped 0.42) gets coverage. Reach is not the limit — probing around the mount,
every point from 200 to 500mm out and -300 to +300mm vertically is reachable.
So what remains is the 60mm body shell and the mount height, which is a real
geometry question worth asking. It is not a reason to re-mount anything yet.

**The lesson worth keeping:** the original trace said the tool point reached 644
patches while the elbow cleared none of them. That pattern is what a wrong arm
model looks like, and it was read as bad news about the chair instead. Question
the model before questioning the hardware.

---

### The original (incorrect) finding, kept for the record

Baking against the measured OpenYAM rig gave **both arms zero owned cells**:

```
  live_rig_table.json   (3 arms, SEARCHED)  owned 222 / 208 / 209   covered 0.361
  live_rig_openyam.json (2 arms, MEASURED)  owned   0 /   0         covered 0.000
```

It is **not a reach problem**. Traced through `partition.feasibility` stage by
stage, per arm:

```
  1792 scrubbable cells
  1372 have a clear 110mm approach corridor
   644 are inside the reach envelope AND solve in ik()
     0 clear the body with partition._body_clear
```

The arms can put their **tool point** on 644 patches of a person. They cannot
get their own **elbow** there without entering `collide.D_BODY`, the 60mm shell
around the body. Sweeping that shell:

```
  60mm (shipped) ->   0 of 644 clear
  40mm           -> 465 of 644 clear
  20mm           -> 561 of 644 clear
   0mm           -> 609 of 644 clear
```

**That conclusion was wrong. DO NOT ACT ON THE THREE OPTIONS BELOW.** They were
"move the mounts", "justify a smaller shell", and "accept reduced coverage" —
all three answers to a question the hardware was never asking. The 644-reaching
/ 0-clearing split was the RoArm's short forearm putting the elbow where the
OpenYAM's does not go, not a fact about the plank.

Nothing about the shell should be touched on the strength of these numbers. They
were measured against the wrong robot and are kept only so the mistake stays
legible.

---

## 7. What will still be untrue at the end

**No vitals sensor exists.** `/api/vitals` will honestly return disconnected.
Her page already handles that. Do not fill it with a number.

**Body depth needs the RealSense back on the GB10.** Arms can be live without
it; the body cannot be correctly placed in the same space.

**`py/openyam.py` is not the arm path.** dimOS is. Our driver keeps the verified
Damiao protocol and the safety envelope as a documented fallback, and its five
CALIBRATE values stay invented until somebody measures them.

**No motor has been observed moving.** ~11M frames says the arms have *talked*.
Talking is not moving, and I have seen neither.

---

## 8. Coordination — three people, one machine

**Justin owns the arms.** We attach and read. Tell him before attaching; a
reader connecting mid-calibration can still confuse what he is watching.

**Crystal owns the UI and voice.** We become the backend her adapter already
expects. Her `.env` changes; her code should not need to.

**We own the safety layer, the vision, and the 3D view.** Every command from her
UI passes our governor before it reaches Justin's stack.

The one thing that must not happen: two paths to the arms. If her
`robot-adapter.js` ever points at dimOS directly, the governor is bypassed and
this architecture is gone.

---

## 9. What only the operator can do

**Plug the RealSense back into the GB10.** Depth works there and only there.

**Power the arms.** ~11M clean frames then silence points at power or an idle
stack, not at wiring.

**Decide the vitals hardware.** The UI has a page for it and nothing to fill it.
Either a sensor arrives, or that page honestly stays empty.
