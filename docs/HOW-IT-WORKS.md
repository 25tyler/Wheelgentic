# How it works

One page, three programs, and a rule about what the screen is allowed to
claim.

---

## 1. Run it

```bash
bash tools/compare.sh
# then open http://localhost:8000/compare.html
```

That brings up five things:

| port | what |
|---|---|
| 5173 | the carechair UI (Crystal's) |
| 8765 | our backend's websocket, at 15Hz |
| 8770 | the care API her server posts to |
| 9090 | the Rerun web viewer |
| 9876 | the gRPC port `live_body.py` logs into |
| 8000 | the page that frames the two panels |

With no hardware at all it runs against a recording under
`scrub3d/data/`. With a depth camera and the arms powered it is the same
code on live frames.

---

## 2. What is on the screen

**Left: her UI.** Press Showering, Eating or Take Meds and our 3D view
opens over it. That is `web/carechair-embed.js`, which attaches from the
outside and touches none of her files: her side is one line in her static
table and a script tag.

**Right: his program.** `scrub3d/live/live_body.py` in its own Rerun
viewer: his point cloud of the person, his fitted body model, his
measurements panel, his camera feed. Not a drawing of his data. His
window, hosted over HTTP so it can sit beside hers.

The point is the comparison. A body rebuilt from measurements can look
convincing and be somewhere the measurement is not; two panels from one
set of numbers make that visible.

---

## 3. Where every number on screen comes from

| what you see | measured by | how it travels |
|---|---|---|
| the person's size | `live_body.py`, 13 dimensions off the silhouette and depth | `/tmp/wheelgentic-dims.json`, rewritten every print |
| the person's pose | `py/bodydepth.py`, 6 arm joints off the depth image | `EVENT["limbs"]` at 15Hz |
| the arms' shape | the OpenYAM URDF and its nine STL meshes | baked to `web/assets/armgeom.json` |
| where the arms are bolted | the depth camera, with a person in the chair | `scrub3d/live/live_rig_openyam.json` |
| the arms' joint angles | the arms' own encoders, through dimOS | `py/armbridge.py` -> `EVENT["joints"]` |
| where the feeding arm reaches | the character's head bone, solved against the arm's own linkage | `robotarm.js solveFeedMouth` |
| how many mouthfuls were given | the arm completing a round trip to that point | `main.js stepFeedWait` |
| room temperature | Crystal's Arduino | her `/api/vitals` |
| body vitals | **nothing** | absent, and the page says so |

Two things on that list are specifications rather than measurements, and
the difference is written down rather than blurred: the arm link lengths
come from the manufacturer's URDF, and the wheelchair's own frame
dimensions are office-chair constants because nobody has built a way to
measure a wheelchair. Its POSITION is measured; its size is not.

---

## 4. The rule

**A number that cannot be measured is absent, never invented.**

It is not a slogan. It is why several things on screen look the way
they do.

- The HUD reads `BODY MEASURED 9 OF 9 DIMENSIONS`, or `PART-MEASURED 3 OF
  9`. Never just "measured", because six of those nine were the typical
  adult until his measurements arrived.
- Arm angles say `CMD` or `MEAS`. A commanded angle and a measured one
  are the same number on screen, so the page carries which it is rather
  than letting a viewer assume.
- `/api/vitals` returns `{"status":"disconnected","readings":null}`. No
  sensor exists. Her page already renders that honestly as a dash.
- A joint depth could not reach is missing from the payload rather than
  filled in. `measured_names` says which ones were real.
- The coverage bake reported 0% owned cells for weeks. That was a bug,
  since it was modelling a different and shorter arm, but it reported zero
  honestly instead of relaxing the safety margin until the number looked
  better.
- The feed readout says `2 SPOONS GIVEN`, never `2 of 4`. There is no four.
  Nothing counts pills in a dispenser or food in a bowl, so the count only
  goes up and never implies an end it cannot see. It also only moves when the
  arm has actually completed a trip to the person's face and back, so an arm
  that does not move feeds nobody and the screen says so by not counting.
- The feeding arm stops 0.33 units short of the face, and that gap is the
  arm's reach rather than a chosen politeness. Sweeping every reachable
  combination of its three joints against the measured head, the closest the
  claw can come is 0.329. The arm cannot touch this person from where it is
  bolted, and the drawing shows exactly that.

---

## 5. Safety

Every command from her UI passes `py/governor.py` before anything
downstream sees it. A refused task never reaches the state machine; the
refusal carries the governor's own words rather than a paraphrase.

Order inside `/api/task` is idempotency, then validation, then the
governor, then the machine. Idempotency first because a repeat must
replay the old decision rather than make a new one: the body moves
between frames, so re-judging could accept once and refuse once for the
same command, and her adapter deliberately does not retry.

`/api/stop` shares nothing with that path, so it works when the rest is
broken.

**We read the arms. We never drive them.** `py/armbridge.py` sends
`state` and `bye` and cannot move anything, which is why it is safe to
run while somebody else is driving. DIMOS.md is explicit that two bridges
on one bus are two stacks answering the same names.

---

## 6. What is still not true

- **No motor has been observed moving by this code.** The arms report,
  hold position, and jitter by 0.0004 rad like a powered servo. Reporting
  is not moving.
- **The wheelchair is placed, not measured.** Seat position and lean come
  from where the person sits; the chair's dimensions do not.
- **Both recordings are a person in an office chair.** No wheelchair and
  no arms in frame, so the body path is proven and the rig geometry is
  not.
- **`arm_sight.py` has never seen a real arm.** It compares a drawn arm
  against measured depth and reports which way a base is off. Proven on
  simulated scenes, never run against the rig.

---

## 7. The shape of the mistakes

Worth reading before changing any of this, because the same shape keeps
recurring: **a second implementation of something that already existed.**

- The coverage bake modelled the wrong arm for weeks, and reported it as
  a hardware problem.
- The page drew the wrong arm's link lengths, and the only label that
  could have caught it said "roarm" in a file baked from the OpenYAM.
- The backend measured three body dimensions from joint positions while
  `live_body.py` measured thirteen, better, in the window beside it.
- The comparison panel started as a canvas redrawing his joints, which
  could have agreed with the numbers and disagreed with his program.
- `py/armlink.py` was shadowed by `scrub3d/armlink.py`, reported "no
  bridge reader", and looked exactly like the bridge being down.

Each was found by putting two things that should agree next to each other
and looking. That is what the comparison page is for.
