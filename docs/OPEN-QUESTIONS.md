# OPEN QUESTIONS — someone must answer these

Ordered by what happens if the answer is bad.

---

## 0. The wheelchair reads as a wheelchair now (ANSWERED 2026-09-19)

A cold read of five projector frames, by someone who knew the product going
in: *"it reads as a person sitting cross-legged on the floor, surrounded by
four industrial arms on separate floor pads."*

**It was the camera, not the chair.** Three fixes were tried against this
and all three measured worse or no better, because all three assumed the
chair model was at fault: `wheelchair-power.glb`, `wheelchair-deluxe.glb`,
and lowering the wide shot's aim.

Measured from the live page instead of guessed: the seated body spans
x -0.89..0.80, z -0.62..0.67. The chair spans x -0.57..0.68, z -0.66..0.60.
The body covers the chair almost exactly. The camera sat at x = dist * 0.30,
about a 17 degree orbit, so there was no angle in the wide shot where any
part of that chair was not behind a person. A footplate added at the
measured foot position disappeared behind the body on the next screenshot,
which is what finally pointed at the camera.

**The fix.** Orbit and height became per shot, defaulting to the old values
so every other shot is unchanged. The wide shot swings round to 0.62 and
drops to 0.74, and the dolly pulls in to pay back the screen size the swing
costs. The wheel, the blue frame and the footplate all read, and the face
still carries. Further round than about 0.8 turns it into a profile and the
dignity of the pose goes with it.

A footplate and two posts were also added under the seat, in the chair's
own blue dropped toward the frame slate. A first version placed them at
z 0.78 on the assumption that a seated figure's feet reach forward; the leg
bones are at z 0 and -0.24, and that version read as a blue luggage cart
parked in front of a person.

**The rails DO read from the new angle**, which corrects an earlier claim
here that they were invisible. That claim came from looking at a
screenshot, not from measuring. Hiding the two rails and the spine and
diffing the frame changes about 9,500 pixels, so they are carrying real
weight in the picture now even though they sit behind the chair. They
stay.

## 1. The bench-mount rule (CAN DISQUALIFY THE PROJECT)

The brainstorm says: *"Rules say bench mounted only. This is vague though. How
to circumvent it? Arm uses clamping mechanism, could we just clamp on the
chair?"*

**Nobody has read the actual rule.** Everything else in this repo is
recoverable; this is not. If the rule means "the arm may not be attached to
anything a person is sitting on", then clamping to the chair is exactly the
thing it prohibits and we find out at judging.

**Do this, in this order:**

1. Find the rule verbatim — hackathon site, Devpost page, hardware-lab terms,
   or the Waveshare/organiser hardware agreement. Screenshot it.
2. If it is ambiguous, **ask an organiser in writing** and keep the reply.
   "We asked and they said yes" is a complete answer; "we assumed" is not.
3. Fall back if needed: **bench-mount the arm and put the person's forearm on
   the bench.** That is what the current software already assumes — a forearm
   on a table at a known height. No code changes, no rule risk.

   **Re-verified 2026-09-21** by grep rather than memory: `forearm_z_mm` is
   read in the two places that use it (`py/scrubbot.py` lines 408 and 513,
   both through the hot-reloading config), and the words "chair" and "lap"
   appear nowhere in the arm code at all. Nothing in the software knows or
   cares what the arm is bolted to. The fallback costs a config number and a
   recalibration.

   **Verified, not assumed.** The fallback is exactly two things, both already
   supported: `config.json` -> `forearm_z_mm` (the plane height, hot-reloads,
   no restart) and a `python py/calibrate.py` on whatever surface you end up
   using. There is no code path that assumes a chair, a person's lap, or any
   particular mounting — the homography maps camera pixels to robot mm for
   whatever flat plane you clicked four corners on. Nothing in `py/` reads a
   mount type. So the fallback costs one number and 90 seconds of calibration,
   which is why it is not worth risking the rule.

**Do not "circumvent" it.** Getting disqualified for a mounting bracket after
36 hours of work is the worst possible ending, and the fallback costs nothing.

### Step 2, written out. Paste this.

Step 2 above says "ask an organiser in writing" and then stops, which is why
this has stayed a five-minute task nobody has done. Here is the message:

> hi, quick rule question before we build further.
>
> we're bringing a small desktop robot arm (Waveshare RoArm-M2-S) for a
> demo where it gently scrubs a volunteer's forearm with a damp sponge.
>
> the rules say hardware must be bench mounted. two things we want to
> confirm in writing:
>
> 1. does "bench mounted" mean bolted or clamped to a table only? we
>    want to be sure a clamp onto anything a person sits on or holds is
>    not allowed, because that's the reading we're planning around.
> 2. is clamping to a standard table edge fine, or does it need to be a
>    specific bench you provide?
>
> we've already built for the conservative answer. the arm sits on the
> table and the volunteer rests their forearm on the same table, so a
> "table only" reply costs us nothing. we'd just rather have it from you
> than guess.
>
> thanks

It asks the narrow question and volunteers the conservative answer, so a
restrictive reply costs nothing and an ambiguous one is hard to send back.
**Keep the reply.** "We asked and they said yes" is an answer; "we assumed"
is not.

---

## 2. Contact vs hover (changes the safety story and the counter)

Brainstorm: *"Not like actually touching the body but just hovering above the
body?"* Built: presses 5mm into the skin plane.

| | Touch (`contact_depth_mm: -5`) | Hover (`contact_depth_mm: +20`) |
|---|---|---|
| Torque contact sensor | works — the counter measures real contact | **gone** — counter is a timer again |
| Safety story | "it yields to contact, torque-capped in firmware" | "it never touches anyone" |
| Judge reaction | more impressive, more nerve | safer, less visceral |
| Honest claim | "it actually scrubbed her arm" | "it traced her arm" |

**Touch is one config edit. Hover is a config edit PLUS a restart**, because
`--no-contact-gate` is an argparse flag with **zero** `CFG.data.get()` reads —
it cannot be set from `config.json`, so it cannot be flipped mid-demo the way
`contact_depth_mm` can. Verified by grep. **The pitch changes**, so decide
before rehearsing, not during.

If hovering: also pass `--no-contact-gate`, and restart to do it.

**What "or the splotches never pop" means, precisely.** With a real arm,
`arm.contact` comes from torque feedback (`torS > 550`), so hovering never
trips it and the contact-gated branch never fires. On this laptop it cannot be
demonstrated either way: `--no-arm` has no torque stream, so `arm.contact` is
False in BOTH modes, and the end-of-scrub finale fires every remaining splotch
unconditionally. Measured, touch and hover, same dry run: 3 pops each, spread
**0.0s**, all at scrub end, `[fsm] splotch` logged 0 times in both. So a
dry-run rehearsal will show hover "working" whether or not the flag is set —
the difference only appears with the arm attached.

---

## 3. One arm or four

Brainstorm: 4 arms, one per body section, each an independent agent.
Built and tested: one arm, one forearm.

The software scales — `Arm` is per-instance, so four is four objects. What
does **not** scale for free:

- 4 serial ports and 4 CP210x devices (each needs the driver + a reboot)
- 4 separate calibrations, each 90 seconds, each redone if anything is bumped
- **inter-arm collision avoidance** — genuinely new work, and the failure mode
  is two arms hitting each other over a person
- 4x the power budget; brownout resets the ESP32 mid-demo

**Recommendation: demo one arm, say the architecture is per-arm.** "This is
one of four; the software is identical for each" is true, costs nothing, and
does not risk two arms colliding over a volunteer on stage. Four half-working
arms read as broken; one working arm reads as a prototype.

---

## 4. Point cloud or RGB

Brainstorm: *"Use point cloud only to map out 3D version of human body instead
of video to address the privacy concerns."*

Their instinct is right and the sensor-level answer is genuinely stronger. But
`DIRECTIVE.md` §3 rules out every depth camera available here: RealSense needs
an arm64 source rebuild plus `sudo` (which breaks one-command startup), ZED
requires CUDA (impossible on Apple Silicon), Azure Kinect is discontinued and
never supported macOS.

**What we can say today, truthfully:** every frame is processed on-device,
nothing is written to disk, nothing leaves the laptop, and the only thing
anyone — including us — ever sees is the cartoon. The goofy avatar IS the
privacy mechanism.

That is a real answer, it is demonstrable on stage (point at the screen), and
it does not depend on hardware we cannot get working in time. If a depth
camera appears, the homography path is unaffected — depth would only improve
the avatar.

---

## 5. Spinning sponge

**Update 2026-09-20: the projector shows it either way.** The sponge heads
now spin on screen during the scrub, spooling up as an arm lands and coasting
down as it lifts. The beat reads as powered whether or not a servo exists, so
this is now about the physical build rather than about whether judges see a
spinning sponge.

Pure hardware call. Software cost is zero — it is a servo the arm firmware
never needs to know about. Their own note already flags the risk: *"each one
needs a servo + microcontroller + wiring + power. Might not be enough time."*

---

## 6. "Point at an area to clean more" — CUT AS POINTING, SHIPPED AS VOICE

**Update 2026-09-20.** The vision line is served, by a different mechanism.
Say *"clean my left arm"* and the chair answers with the arm that owns that
side and lights its territory: *"Arm 3 has that. 164 patches."* Ask for the
right and arm 0 answers with 273. Those numbers come from the solver's own
per-cell ownership, so they match the counts panel and change when the body
swaps.

That has none of the problem below. The volunteer speaks; nothing reaches
into a moving arm's workspace. The analysis that follows is about POINTING
with a hand, and it still stands on its own terms.

Not built as pointing, and still deliberately **not building it that way**. The research is done and
the answer is that it is cheap to build and impossible to demo.

**It would work.** MediaPipe's pose model already carries the finger landmarks
(index 19/20, pinky 17/18, thumb 21/22) so it needs no second model;
`project_to_limb()` already returns the same `t` the splotches use; and
`EVENT["place"]` is an existing server-to-browser wire that moves splotches.
Two constants and a call into code that exists — an hour, as estimated.

**It cannot be demoed.** The splotches live on `arm-left`, which is the forearm
resting on the taped X being scrubbed. To point at a spot on that arm the
volunteer has to reach across with their other hand, into the workspace of a
moving robot arm, while it is scrubbing them. The one safety rule of this
project is that nothing enters that envelope mid-cycle.

**And the demo script argues against it in its own words:** *"Rehearse this
five times. The version that runs cleanly five times beats the version with an
extra feature that runs twice."* The recovery card is blunter: *"Add features
after the freeze. Rehearse instead."*

If a second limb is ever scrubbed, revisit — pointing with the free hand at the
*other* arm is a real interaction. Today it is not.

---

## 7. "No code written before the event" (CAN END THE PROJECT)

**Read this as item 2.** By the ordering this file claims it belongs there, and
it can end the project the same way the bench-mount rule can. It sits last only
because moving it up would renumber five headings to place one draft, and
nothing sorts this file by number.

HackMIT rules typically forbid pre-written code, and this repo has weeks of
commits. If the answer is bad, everything here becomes a prototype to be
rebuilt during the event, and that changes the whole plan. Nobody has asked.

### The message. Paste this.

> hi, want to get ahead of a rules question rather than find out at
> judging.
>
> we've been prototyping our project before the event and have a public
> repo with a few weeks of commits in it. the hardware is a robot arm
> plus a webcam, and most of that work is calibration and safety code.
>
> what we want to know:
>
> 1. what exactly does the no-pre-written-code rule cover? is it "all
>    code must be written during the event", or "the work you demo must
>    be substantially built during the event"?
> 2. if pre-existing code is allowed with disclosure, what form do you
>    want that disclosure in?
> 3. if it isn't allowed, we'd rather know now and start from an empty
>    repo on the day than argue about it afterwards.
>
> happy to share the repo link if that helps you answer.
>
> thanks

**Send it separately from the bench-mount one.** The two questions may go to
different people, and bundling them invites a single vague reply that answers
neither.

Keep the reply, same as item 1. The value is a written answer, not a remembered
conversation.

---

## The scrub beat fills in 3 seconds, not 8

**Measured, not guessed.** All three splotches pop within 2.2 seconds
of an 8 second scrub. Simulated against `motion.scrub_offset` and
confirmed live: the middle one pops at t=0 because the sponge starts
sitting on it, the other two at 1.8s and 2.2s. The remaining 5.8
seconds rub an already-clean arm.

**Why it is structural, not a bad constant.** The sponge travels only
`u = 0.5 +/- 0.175`, which `py/scrubbot.py:66` already documents, and
the three splotches at 0.34 / 0.50 / 0.66 span nearly that whole band.
The Hann envelope grows the reach until 4s and then plateaus, so after
4s the sponge only retraces ground it has covered. No placement of
three splotches inside that band spreads across 8 seconds.

What each option measured:

- Tightening the match tolerance from 0.10 to 0.03 buys 3.05s, not 8.
- Six splotches instead of three gives even 0.4s gaps but still ends
  at 3.07s.
- Dropping `scrub_hz` from 1.2 to 0.35 reaches 4.7s, at the cost of
  halving the visible rubbing speed. A sponge that slow reads as a
  stall.

**Why it is not being fixed.** A judge watches the arms, and the arms
animate for the full 8 seconds either way. The counter reaching 100%
early is not visible as a fault unless someone is timing it. Fixing it
properly means reworking how the sponge covers the forearm, which is a
change to the motion model on the demo's most load-bearing beat.

**If someone picks it up:** the best ratio is six splotches at a 0.03
tolerance, which is even and well paced, combined with a slower
envelope rather than a slower oscillation. The reach then keeps growing
through the whole 8 seconds while the stroke speed stays where it is.

---

## The demo command is missing a flag that the beat depends on

Seven files document the no-hardware demo as
`CAM=fake ./run.sh --no-arm`. Measured against that exact command:
**zero splotches pop while the arm is scrubbing.** All three are dumped
by the end-of-cycle finale in a single socket frame, so the counter's
whole journey from 0 to 100 happens in one tween with no per-splotch
beats. With `--no-contact-gate` added, 12 pops land during the scrub
and each splotch goes as the sponge reaches it.

The reason is already written down at `py/scrubbot.py:721`: with
`--no-arm` there is no torque sensor, `arm.contact` is false for the
whole scrub, and the pop check is gated on it. The recovery card lists
the flag under "splotches never pop" as a fix to reach for when
something looks wrong, rather than as part of the normal command.

**One thing I got wrong while measuring this, recorded so nobody
repeats it.** I first read the counter as ending at 0% and thought the
documented command produced a scrub that cleaned nothing. That was a
sampling mistake: the readings were taken after the end-of-cycle reset,
which is correct behaviour. Sampled per frame the counter really does
go 31, 48, 80, 94, 100. The defect is the missing progression, not a
missing result.

**FIXED, same session.** `--no-arm` now implies `--no-contact-gate`,
set on the parsed args in `py/scrubbot.py`. That was the better of the
two options: adding the flag to seven documented commands leaves an
operator to remember it on demo day, and `--no-arm` is precisely the
case where there is no contact to sense.

Verified on the unchanged documented command: 3 in-scrub pops where
there were 0, splotches going one at a time rather than together, and
the counter climbing 37, 55, 70, 86, 100 instead of jumping in a single
frame. The flag stays CLI-only with zero config reads, so the guard
that requires that still passes, along with the three tests that pass
`no_contact_gate=True` themselves.

---

## The measured body reads as a silhouette, not as data

5125 of the scan's 5824 cells are unreachable, which is the torso and
head, and they render at 1.95:1 against the back wall. On a projector
that whole mass reads as a dark shadow with four coloured limb bands
floating on it, rather than as a measured body.

**Brightening the cells does not work.** Measured: by the time the
unreachable colour clears 3:1 against the wall it is as bright as the
dimmest arm colour, so which arm owns which patch stops reading, and
that is the one thing the overlay exists to show.

**A dark backing panel is the right idea and I could not land it.** A
panel at 0x141829 puts the same cells at 3.44:1 without touching any
cell colour or the wall, which is deliberately dark so the character
wins the frame. Four attempts all rendered the panel to one side of the
body. Measured by projecting both centres to screen space, the last one
sat 62px left and 18px up of the cloud it was meant to back, even when
placed at the cloud's own measured centre and made to face the camera.
Something about where that plane actually lands is not what I think;
the next person should read the panel's world matrix rather than trust
its position.

Reverted rather than shipped half-right. The scan is legible today, it
just does not read as well as it could, so this is polish and not a
hole.

**What is already measured, if someone picks it up:** the cloud is
1.397 wide by 2.608 tall spanning y 0.63 to 3.238, centre (-2.45,
1.934, 0.58). A panel 2.10 x 3.26 with a falloff on semi-axes 0.80 and
1.45 covers it with margin.
