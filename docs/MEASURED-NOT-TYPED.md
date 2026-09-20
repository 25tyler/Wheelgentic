# Everything on screen, measured

**2026-09-19.** The 3D view draws a generic adult in a generic chair with
arms placed from a tape measure. This is the plan to make every visible
dimension trace back to a sensor, and to say so on screen when one cannot.

The cartoon look does not change. A cartoon is a skin over a skeleton, and
this is about the skeleton.

---

## 1. What is typed today, and what measures it

Each row is a thing on screen. "Measured by" names code that ALREADY exists
and already produces the number; the gap is that nothing carries it to the
page.

| On screen | Today | Measured by | Gap |
|---|---|---|---|
| Body proportions | generic adult: shoulders 390mm, upper arm 330, forearm 265, torso 520 | `live_body.dims()` — 13 dimensions, running median off depth | never sent |
| Body pose | 6 joints, real depth (CAM=replay) | `bodydepth.joints_world_mm` | **done** |
| Chair position and lean | not drawn at all | `Body.place_chair()` — from the first seconds of sitting | never sent |
| Chair dimensions | n/a | fixed office-chair boxes in `place_chair` | **nothing measures these** |
| Arm mount positions | tape measure in `live_rig_openyam.json` | `arm_sight.py` — compares drawn arm to depth, shifts to fit | never run |
| Arm link lengths | `kinematics_openyam.py`, off the URDF | the URDF is the manufacturer's spec | a spec, not a measurement |
| Arm joint angles | commanded | `arm_dimos.real_joints6()` | reader built, needs the bridge |
| Vitals | room temperature, real | Crystal's Arduino | **done, hers** |

Two rows are worth reading twice. `live_body.dims()` measures thirteen body
dimensions RIGHT NOW, every frame, and throws them away as far as our page is
concerned. And `arm_sight.py` exists specifically to answer "is the arm where
the rig file says", which is the one question a tape measure cannot.

---

## 2. What "measured" honestly means for each thing

Not everything can be measured, and the plan must not pretend otherwise.

**Measurable from the depth camera:** body proportions, body pose, chair
position, chair lean, whether an arm mount is where the file says.

**Measurable from the arms themselves:** joint angles, through Justin's
bridge. Read-only; we never drive.

**A specification, not a measurement:** the arm link lengths. They come from
the manufacturer's URDF. That is a legitimate source — it is how the metal
was made — but it is not this arm on this day, and the screen should not
imply a caliper was involved.

**Not measured by anything, today:** the chair's own box sizes (seat pan,
backrest, post). `place_chair` positions a chair correctly and sizes it from
office-chair constants. Measuring a wheelchair's frame from depth is real
work nobody has done, and this plan does NOT pretend to do it.

**Never measurable here:** body vitals. No sensor. Stays absent.

---

## 3. The shape of the fix

One new thing on the wire, and one rule.

**The thing: a `body` block carrying measurements, not geometry.** Thirteen
numbers plus a count of how many frames backed each one. Not a mesh, not
5,824 cells. The page rebuilds the model from the numbers, because
`anatomy.anatomical_body(measurements=...)` already accepts exactly this
shape and is already the function both sides use.

```
EVENT["body_measured"] = {
  "src": "depth",                  # or "prior" when nothing is measured yet
  "n_frames": 143,                 # how much evidence is behind it
  "mm": {"biacromial": 372.0, "upper_arm_len": 311.0, ...},
  "confidence": {"biacromial": 0.93, ...}   # samples/(samples+prior weight)
}
```

**The rule: a measurement replaces a default outright, never blends.**
Averaging a measurement with a guess produces a number that is neither and
nothing downstream can say which it was. `Measured.value()` already blends
the prior IN ITS OWN ESTIMATE, weighted by sample count, and that is fine
because it reports the weight. What must not happen is a second blend on our
side.

---

## 4. The phases

### Phase A — the body, measured

**A1. Publish the thirteen.** `live_body.dims()` holds them. Our backend does
not run `live_body`, so the measuring loop has to run where the depth is:
on the arm computer, or here under `CAM=replay`. Add a `body_measured` block
to the 15Hz event, sourced from a `Measured` set fed by the same frames
`bodydepth` already consumes.

**A2. The page rebuilds from them.** `web/main.js` currently fetches a baked
`body.json`. It gains a path that takes the thirteen numbers and rebuilds the
regions. The bake stays as the startup default, so a page with no backend
looks exactly as it does today.

**A3. Say which it is.** The HUD reads `BODY MEASURED (143 frames)` or
`BODY GENERIC`. A generic body drawn as if it were the person in the chair is
the exact failure this document exists to prevent.

*Verify: sit two differently-sized people in front of the camera; the drawn
body changes shape. Screenshot both, read both images.*

### Phase B — the chair, placed where it is

**B1. Publish the seat.** `place_chair` computes seat height, floor position
and lean from the first seconds of sitting. Those three go on the wire.

**B2. The page draws the chair there.** Today the chair is a model positioned
by eye against a `REAL_SEAT_Y` constant of 0.48m. It moves to the measured
seat height and lean.

**B3. Be honest about the frame.** The chair's box SIZES stay constants and
the screen must not imply otherwise: `CHAIR PLACED, NOT MEASURED` until
somebody measures a wheelchair frame.

*Verify: raise or lower the real seat; the drawn chair follows.*

### Phase C — the arms, where they actually are

**C1. Run `arm_sight` against the rig file.** It draws each arm as the camera
would see it and compares against measured depth. Output is per-arm: seen,
not seen, or a suggested shift. This is the only thing that can catch a rig
file that no longer matches the hardware.

**C2. Publish the verdict, not a correction.** A mount that disagrees with
the file is a fact for a human, not something software should quietly move.
The wire carries "a0 seen 94%", or "a0 seen 31%, best match 40mm forward",
and the screen says so.

**C3. Measured joint angles.** The reader is built (`_sample_joints` prefers
`real_joints6()`). It needs Justin's bridge reachable, and his agreement.

*Verify: with the arms powered, the drawn arm matches the photograph of the
real one from the same viewpoint.*

### Phase D — nothing claims more than it measured

One pass over every number the page draws, asking: where did this come from?
Anything that cannot answer either gets a source or gets a label.

---

## 5. What this plan does NOT do

**It does not measure the wheelchair's frame.** Seat height and lean, yes.
The dimensions of the chair itself, no. Nobody has built that and this plan
does not pretend to.

**It does not replace the URDF.** Arm link lengths stay the manufacturer's
numbers. Measuring them off depth would be less accurate than the spec.

**It does not invent a vitals sensor.** There is none. The page stays honest.

**It does not run without a depth camera.** Every measurement here comes from
depth. On a laptop webcam the view falls back to the generic body and says
so. That fallback is the honest behaviour, not a degraded mode to hide.

---

## 6. Why this order

**Body before chair.** The chair is placed relative to where the person sits;
`place_chair` takes the seat from the body's own hips. A wrong body puts the
chair in the wrong place.

**Chair before arms.** The rig file is seat-relative — every arm position is
measured from the seat point. Checking an arm against the camera while the
seat is wrong tests the wrong hypothesis.

**Verdicts before corrections.** `arm_sight` can suggest a shift. Applying it
automatically would mean software silently moving a safety-relevant number.
It reports; a human decides.

---

## 7. What must not break

The demo runs with no hardware at all. Every phase ends with the page loading
on a laptop with no camera, drawing the generic body, and saying it is
generic. If a change makes the no-hardware path worse, it is the wrong
change.
