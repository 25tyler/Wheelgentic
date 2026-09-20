# ROADMAP.md — what is left, ranked by what a judge sees

**This is the work source.** Take the top unblocked item. `WORK-QUEUE.md` is an
audit log, not a queue. Companion to `docs/TRUTH.md`.

## The drift check — answer before starting anything

> **What does a judge see differently because of this?**

If the answer is "nothing", it is not the next item. Write it down and pick
something else. This question exists because 20 hours went to work that had no
answer to it (`TRUTH.md` §7).

**Second check:** is this test work? If yes, stop. The suite is a regression
gate and does not grow.

**Third check:** can I take this from online instead of building it? Search
first — GitHub, Kenney, Poly Pizza, Sketchfab, three.js examples, Quaternius,
freesound. CC0 or MIT. Building it yourself is the last resort and needs a
written reason. (`TRUTH.md` §3b.1)

**The bar:** it has to LOOK great from ten feet. That outranks proving it
works. (`TRUTH.md` §3b.2)

---

## BLOCKED ON A HUMAN — surface these, do not idle on them

| # | Item | Who |
|---|---|---|
| B1 | **The bench-mount rule.** Can disqualify. **The message is written:** `OPEN-QUESTIONS.md` section 1, "Step 2, written out". Paste and send. | Tyler / team |
| B2 | **"No code written before the event."** The repo has weeks of commits. **The message is written:** `OPEN-QUESTIONS.md` section 7. Send it separately from B1. | Tyler + organiser |
| B3 | First serial contact with the real RoArm-M2-S (`python py/arm.py`). **The machine half is already done:** it was run, exits 1 in 0s through `find_port()` with the documented driver message, no hang and no partial motion. Only the plugged-in arm is left. | Tyler |
| B4 | Real 365nm UV threshold tuning under the lamp. **The machine half is already done:** `tools/tune_dirt.py` has a `CAM=fake` branch that runs and prints "synthetic tracer, NOT a real lamp". Only the lamp is left. | needs the lamp |
| B5 | Water vs dry sponge; 1 arm vs 4 | team's hardware call |

A blocker on one item is never a blocker on everything.

---

## R1 — DRIVE THE UNPROVEN DEMO BEATS ✅ DONE

**Eleven of twelve beats are now driven on the real key path**, in one
continuous session, 0 page errors. The table is in `STATE.md` §2.

The twelfth is `0:00–0:48`, the mirroring, and it cannot be driven on this
machine: headless Chrome has no camera, and the fake-device stream is a test
pattern with no person in it. The mirroring path itself is proven by injection
(arm swings 0.701 between poses). What is missing is the camera end, and only a
real camera or a recorded video fed to Chrome closes it.

The original entry follows.

---

## R1 (original) — DRIVE THE NINE UNPROVEN DEMO BEATS

`DEMO-SCRIPT.md` has 12 beats. Three have been driven on the real key path
(0:48, 1:32, 1:52). **Nine have not.** Tests call the underlying functions
directly, which is a weaker claim than "the operator pressed the key and the
right thing happened."

Judge sees: every beat the presenter performs, working, in order.

Do it with `CAM=fake` + the projector page, pressing actual keys:
- `0:00–0:48` — the idle/mirroring beats. Does the cartoon actually mirror a
  moving subject for 48 seconds without drifting or freezing?
- `0:58–1:06` — splotches visible on the forearm when the volunteer places it
- `1:06–1:10` — `s` flashes ARMED
- `1:10–1:22` — the arm travels, descends, oscillates. **This is the whole demo.**
- `1:22–1:32` — first pop, and the `1` rescue if it misses
- `1:44–1:52` — arm returns HOME, confetti both sides, fanfare

Acceptance: each beat driven by keypress, screenshotted, timed against the
script's own clock.

---

## R2 — THE FIVE-REHEARSAL RUN ✅ DONE

> "Rehearse this five times. The version that runs cleanly five times beats the
> version with an extra feature that runs twice."

**Five clean rehearsals of `0:48–2:00` on one page load.** ARMED flash every
run, 3/3 splotches every run, reset to 0% every run, 0 page errors, and — the
point of the item — **nothing accumulates**: draw calls, triangles, geometries
and textures are identical across all five, frames and wall-clock flat within
0.1s. Full table in `STATE.md`.

Re-run it after any `web/` change. It is the cheapest regression signal that
exists for this project, and it is the bar the demo script itself sets.

---

## R3 — SUBJECT HANDOFF AT 0:48 ✅ MEASURED

**Tracking does not follow the person who moves.** Measured against the real
detector with two rendered people in one frame, well separated (the 2-pose
control saw both in 25 of 25 frames):

| Condition | Who is tracked |
|---|---|
| Left person waves, right still | left (nose x 0.168) |
| Right person waves, left still | left (nose x 0.167) |
| One person alone, left | 0.168 |
| One person alone, right | 0.834 |

Waving changes nothing. The pick is identical either way.

**Consequence for the demo, which is the part that matters:** at 0:48 the
presenter and volunteer are both in frame by design. Whoever the tracker has
does not change when the volunteer raises their arm, so the handoff cannot be
relied on. `DEMO-SCRIPT.md` now carries the stage direction: **one person in
frame at a time** — the presenter steps out of shot as the volunteer steps in.

Not worth building a selection rule for. A stage direction costs nothing and
`py/vision.py` is the file where a change means re-proving the mirror
convention, the visibility gate and the freshness watchdog.

**Two of my own rigs were wrong before this came out clean**, both from
changing more than one thing: figures rendered at different widths are
different *sizes*, not different positions; and two 480-wide figures pasted
240px apart on a 960 canvas *overlap*, merge into one body, and return a nose
at the seam (0.501) while the control drops to seeing 1 person.

---

## R4 — P3: THE DIRT READS AS ONE MASS. CLOSED, NOT FIXABLE.

**Stop working on this.** Both levers are now measured and both are dead.

*Geometry* was exhausted by the exhaustive matrix in `VISUAL-OVERHAUL.md`:
three splotches do not fit inside the `[0.20, 0.85]` joint band at any scale
that stays readable from ten feet.

*Colour* is exhausted as of today. If the lobes cannot be separated by a gap,
the remaining hope was separating them by value, so the eye reads a seam where
it cannot read a gap. Measured at the three centres: `#281c12`, `#271c12`,
`#1e150e`. The first two differ by one unit per channel, which is invisible,
and the third is the *end* lobe, so darkening it separates nothing in the
middle. `makeDirtTexture` already varies the silhouette per seed, so the
variation that exists has already failed to read.

What remains true: three lobes ARE distinguishable up close, and connected
components find one mass. Nobody standing ten feet from a projector is running
connected-component analysis. This is a measurement that offends more than the
picture does.

The original entry follows, kept for the reasoning.

---

## R4 (original) — THE DIRT STILL READS AS TOUCHING BLOBS

`VISUAL-OVERHAUL.md` P3 is the one visual item not finished. Three lobes are
distinguishable but connected-component analysis finds **2 components, not 3**.
The measured reason: sprite width (26.9 / 18.0 / 14.8 px) exceeds neighbour
spacing (29 / 19 / 16 px) at every projector shape, and lobed textures paint
past their nominal box.

Options not yet tried:
- Move the splotches further apart along the limb — but `[0.20, 0.85]` is the
  joint-free band and `main.js:358` records that 0.22/0.78 were unreachable by
  the sponge. The reachable window is roughly 0.325–0.675.
- Give the middle splotch a different hue/value so the eye separates them by
  colour rather than by gap.
- Accept it. The demo script says "three dirty spots" — if the eye reads three,
  that may be enough.

Judge sees: whether "three dirty spots" matches the screen.

---

## R5 — CUSTOMIZATION + PROPS ✅ ALL THREE DONE

All three sub-items shipped. What landed:

- **R5a** — `k` cycles 6 skin tones, `o` cycles 6 outfits. The atlas ramp is
  repainted band by band on a cloned material AND a cloned texture, so one
  character's recolour cannot leak into another. Instant, no reload, safe to
  press mid-demo.
- **R5b: REVERSED.** A wheelchair went into the scene and
  Tyler had it removed: *"why is there a wheelchair? i didnt ask for that"*.
  It was my initiative, not a request, and it labelled a volunteer who never
  agreed to be labelled. See `DECISIONS.md`. Props stay extracted and unused.
  **No mobility prop as scenery, in any form.**
- **R5c** — the floppy overreaction. Spring damping 0.72 to 0.93, which also
  improved tracking rather than costing it (0.591 to 0.701), whole-torso wobble
  on a pop, and a bigger slower flinch on the estop.

The body below is the original plan, kept for the atlas coordinates and the
rationale.

---

## R5 (original) — CUSTOMIZATION + PROPS: make it feel like a polished game

Tyler: *"make sure wherever you found the models you can change the
outfits, skin colors, etc to make it feel much more like a polished video
game."* Full rationale in `TRUTH.md` §3b.3.

**All of this is already downloaded. None of it needs building.**

### R5a — skin tone and outfit cycling
All 12 characters share one 512×512 atlas, `web/assets/Textures/colormap.png`,
with 162 flat swatches. Skin tones live at x32–64 y256–384 and across
y384–512 x320–512 (`#f2bf99` → `#f1976c` → `#b06041` → `#845442`); clothing
colours are vertical column families (greens x96–128, yellows x160–192,
oranges x224–256, reds x288–320, blues x352–384, purples x480–512).

Recolouring = repaint a swatch on a cloned canvas texture, or shift a UV
island. `avatar.js` currently traverses only for `isSkinnedMesh`, so this needs
one `traverse(o => o.isMesh)` reading `material.map`.

Two operator keys, in the same family as `v` (next character): one cycles skin
tone, one cycles outfit palette.

**Judge sees:** a volunteer who looks like *them* on screen. That is the
privacy pitch landing instead of being asserted.

### R5b — the wheelchair and the mobility aids
`vendor.sh` already downloads a zip with **26 models** and extracts **12**.
Unused and free (CC0): four wheelchairs, three canes (including blind and
low-vision variants), a crutch, glasses, sunglasses, a mask, a hearing aid, and
a 13th character.

~~This is a project about bathing assistance for elderly and disabled people. A
wheelchair beside the character IS the pitch, visible from ten feet, costing
one `unzip` line and one `scene.add`.~~

~~**Judge sees:** who this is actually for, without the presenter saying it.~~

**WRONG, and reversed on Tyler's instruction: "i want it gone."**
The cost was never the `scene.add`. A mobility prop standing beside the
volunteer says who this is for *about them*, and they never agreed to that.
The presenter says who it is for, in words.

Struck through rather than deleted because the argument above reads well and
somebody will make it again. `DECISIONS.md` carries the full reversal, and it
covers canes and crutches too, not just the chair.

### R5c — floppy overreaction (Gang Beasts / Peak feel)
Tyler: *"highly responsive to game engine like physics but in a fun
way."* The rig has springs, breath and squash but not the comic overshoot those
games are built on.

- Stronger overshoot on the scrub recoil (lower damping on the impulse)
- A whole-torso wobble when a splotch pops, not just a limb impulse
- A bigger, slower reaction to the estop — the character notices it stopped

**Judge sees:** whether the cartoon is funny or merely present.

---

## R6 — SOUND ✅ DONE

Before: two sounds in the whole demo. A pop, and the finale fanfare. Arming,
the estop, the recolour keys and the scrub itself were all silent.

Now every beat is audible. `play()` is exported from `juice.js` and six sites
in `main.js` use it:

| Beat | Sound |
|---|---|
| `s` arms a cycle | `ding` (was authored and never called) |
| the scrub stroke | `squish`, on the 260ms choreography tick |
| `x` estop | `thunk`, a low falling tone |
| `shift+C` clear | `ding` |
| `k` skin, `o` outfit | `click` |

Two calls worth knowing. The estop sounds when the key is **sent**, not on the
server's confirmation: the confirmation may never arrive (the NO LINK case) and
an operator who pressed stop has to hear it land. And the scrub rides the same
260ms interval the foam does, so it inherits that tick's rate cap and its stop
path for free; the contact-path stroke stays silent because those events can
arrive batched and one batch would fire a burst at once.

Measured in the browser: 17 strokes over 4.2s against ~16 predicted, and both
canvases still alive, so the sound is not throwing inside the tick and taking
the foam with it.

Judge sees: nothing — but they *hear* the demo land.

---

## R7 — THE RECOVERY PATH. MECHANICS PROVEN, REHEARSAL STILL OWED

**The machine half is done.** All three paths were driven
back to back on ONE page load, which is what this item asked for and what had
never been done: the rows had only ever been executed one at a time from a
clean page.

What that run established:

- **State does not leak between recoveries.** Rescue, then estop, then clear,
  then re-arm: the counter opened at `0%` with 3 of 3 splotches restored. That
  is the bug `scrubbot.py` fixed once, and it stayed fixed across the chain.
- **Manual `1` `2` `3` still work with the socket gone**, which is the total
  freeze fallback.
- **One defect, now fixed.** The estop log line named only the Python window's
  `r`, and on the projector `r` clears splotches instead. It now names both
  windows, matching the ARM REFUSED message that was already correct.

**What is still owed, and no tool call can produce it: an operator using the
card under pressure.** Splotch does not pop, press its number without pausing.
Arm behaves oddly, press `x` and READ the indicator before doing anything
else. Total freeze, `Ctrl+C` then `REPLAY=recordings/good_run.jsonl ./run.sh`.

The difference between the proven half and the owed half is that the keys
work; nobody has practised reaching for them while a judge watches. Do not
close this item by re-running the sequence. It is already run.

Judge sees: a failure handled invisibly instead of a demo dying.

---

## NOT NEXT — and why

These will look tempting. They are not the work.

- Any new test, any suite plumbing, any assertion counting — Tyler's redirect.
- More config-parsing robustness — `DECISIONS.md`, finished.
- Rebuilding anticipation on the arm descent — measured invisible, reverted.
- Resolving the team's open questions — not mine (B1–B5).
- Appending more generator-produced items to `WORK-QUEUE.md` — that mechanism
  is what produced the drift.
