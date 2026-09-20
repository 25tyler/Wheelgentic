# THE 2-MINUTE DEMO, the wheelchair

Replaces `DEMO-SCRIPT.md`, which describes the one-arm scrubbing demo the
project was before the brainstorm reset the scope. That file is kept because
its three rules and its recovery lines are still correct.

Rehearse this five times. The version that runs cleanly five times beats the
version with an extra feature that runs twice.

**Cast.** *Presenter*, talks, never touches a keyboard. *Operator*, at the
laptop, silent. *Volunteer*, a rehearsed teammate, or better, a judge.

**Pre-set, before the timer starts**

- `CAM=fake ./run.sh --no-arm` running, Chrome kiosk on the projector
- **`Enter` already pressed** so audio is unlocked
- Mode strip reads SHOWER, counter `0%`
- Microphone permission already granted (press `m` once, then `m` again)
- Physical kill-switch within the operator's reach
- `docs/RECOVERY-CARD.md` printed and taped to the laptop
- **Matched to the volunteer if you have a minute**: `v` cycles 12
  characters, `k` 6 skin tones, `o` 6 outfits, `j` 8 accessories
  (glasses, mask, hearing aid, cane, crutch). Worth doing: the
  cartoon is the privacy mechanism, and a volunteer who recognises
  themselves in it is the dignity half of the pitch landing without
  anyone saying it. `v` reloads the page, so do it FIRST; the other
  three are instant and safe any time.

---

| Time | Presenter | Operator | On screen |
|---|---|---|---|
| **0:00–0:15** | "Bathing is the number one daily activity people lose first. Seventy-four percent of people in residential care need help with it. And it is the most undignified thing to need help with." | idle | A tiled bathroom under a ceiling light: **a person in a wheelchair with four arms around her, reading as one machine**. The wheel, the blue frame and the footplate are all visible from this angle, which is what makes it a wheelchair rather than someone sitting on the floor. Mode strip on the right. **You do not have to say where this is** -- the room says it while you deliver the line. |
| **0:15–0:28** | "There aren't enough caregivers, and the ones we have get hurt at five times the industry rate lifting people. So: a wheelchair that does the lifting." | idle | Same. The cartoon idles and mirrors. |
| **0:28–0:40** | *(points at the screen)* "That's the volunteer. That is the **only** thing anyone in this room sees." *(then at the top-left line)* "Every camera frame stays on that laptop. **That number counts the frames. The zero next to it is how many were kept.**" | idle | Cartoon mirrors the volunteer's movements. **The top-left line reads `N SEEN - 0 STORED` and the first number climbs while you talk** -- the count is taken where the page actually consumes a camera frame, so it moves only if frames are really arriving. Point at it; a caption saying the same words would not move. |
| **0:40–0:52** | "And it isn't guessing where she is." *(then, to the volunteer)* **"Lean."** | presses **`b`** | **The camera pushes in and slides left** as **the measured body appears beside her**, coloured by which of the four arms owns which part. This is the moment the pitch stops being a claim. The frame does the pointing, so you do not have to. **Ask her to lean and the scan leans with her** -- that tilt is computed from her real shoulder line every frame, which is the whole "no preprogrammed paths" argument happening in front of them. Standing still it is a sway of about a degree and reads as nothing, so the ask is what makes it visible. |
| **0:52–1:02** | "Four arms, one body, split into four territories. No preprogrammed paths, because every body is a different shape." *(point at the list on the left)* "Watch the numbers." | presses **`n`** | **A different person appears and the territories re-solve in front of them.** This is the proof, not the claim: the arms that own each part change because the body changed. **The list on the left changes with it** -- arm 3 goes from 164 cells owning the left upper arm to 282 owning the upper arm and the forearm, and the contested count goes 433 to 482. **The bottom line changes too**: body A schedules `ARM 0 + ARM 2 + ARM 3, then ARM 1`, body B schedules `ARM 0 + ARM 2, then ARM 1 + ARM 3`. That is the four of them working out who can move at the same time without reaching into each other — a different answer for a different body, which no caption could produce. Those are the solver's own numbers and its own body-part names, not a caption. Contested means reachable by more than one arm, so that bottom line is the four of them renegotiating who takes what. (Pressed here, before `s`, nothing is running -- mid-cycle it would reset the counter and the dirt.) |
| **1:02–1:12** | "Can I borrow an arm?" | **press `g`** as you say it — a slow six-second camera drift that keeps the frame alive through the swap. It changes no state, so it is safe to press and safe to forget. **Start the swap DURING the 0:52 beat, not after it** — the volunteer walks in while the territories are still re-solving, so the screen is changing through the shuffle. Presenter steps OUT as they step in. One person in shot. | Cartoon follows whoever the tracker has. **This is the only beat with no keypress**, so it is the one window where a judge's eye leaves the projector — and it lands on two people trading places, right after you told them to watch the cartoon mirror the volunteer. While the tracker has nobody the top-left line reads **`LOOKING FOR A SUBJECT`** in amber, then **`SUBJECT LOCKED`** in green when it picks the volunteer up. If the cartoon stalls for a second, point at that line: it is the system saying what it is doing, not a freeze. |
| **1:12–1:18** | "It won't start on its own. Someone has to arm it." | presses **`s`** | `ARMED` top-right, then the left corner flips `IDLE` → `APPROACHING` |
| **1:18–1:34** | *(quieter)* "Watch." *then say nothing* | hand near `x` | **The camera pushes in over two seconds** and **all four arms move**, staggered, **the sponge heads spinning up as they land**, **water spraying off them and foam building on every one**, and **steam rising through the light beam** — the shaft has been there all along and only reads as a beam once something drifts through it. The steam thickens as the counter climbs. Left corner reads `SCRUBBING`, and **the safety verdicts stream underneath it**: cleared, held, refused. Dirt pops into bubbles, counter climbs, and **the measured body fills in beside her** as each arm works through its own territory. Let the sound carry it. |
| **1:34–1:42** | *(point at the body filling in)* "Four arms, four territories, one person, done together." *(then at the verdicts)* "And every one of those moves was checked first." *then stop and let the confetti land* | if a splotch misses: press `1`, silently | `67% → 100%`, confetti, fanfare |
| **1:42–1:52** | "But it isn't only a shower." *(as the arm swaps tools)* "It puts the sponge down and picks up a spoon. Food, medication, vitals." | **`8`**, wait two spoonfuls, **`shift+8`**, wait one pill, **`9`**. **Do NOT wait for each to finish** — all three run 11.6s end to end and the beat is 10s. Two beats of each is enough to read. | FEED lights and an arm carries a bowl to her mouth. Then a glass for the pills, readout reads MEDICATION. Then VITALS: arms withdraw, a live **heart rate** and a heartbeat trace -- and **the sensor on the chair's armrest pulses with it**, so the number is visibly coming from the chair rather than from a caption. |
| **1:52–2:00** | "And you can just ask." *(then, to the volunteer)* **"Ask it something."** | presses **`m`**, volunteer says *"clean my left arm"*, then **asks a question** -- *"how clean am I?"* or *"which arm is washing my leg?"* | `LISTENING`, the words appear on screen, **the chair answers out loud with the arm that owns that side** -- "arm 3 has that, 164 patches" -- and lights its territory on the scan. **Then it answers the question from what is on the screen behind it**: "you are 67 percent clean, one patch left", or "arm 0 is doing the most, it has your upper arm right and your forearm right". Every number it says is a number the judge can see while it is saying it. |

---

## The four keys that matter

| Key | What it does | Why it is in the script |
|---|---|---|
| `b` | measured body on/off | the strongest technical claim, and it is invisible otherwise |
| `n` | swap to a different person | turns that claim from an assertion into something they watch happen. Press it BEFORE `s`, as the script does: mid-cycle it resets the counter and the dirt |
| `s` | arm the cycle | proves it never starts itself |
| `7 8 9 0` | shower, feed, vitals, voice | proves it is a wheelchair, not a scrubber |
| `shift+7` | the drink beat | the strip promises eating, drinking and pills; this is the middle one |
| `shift+8` | the pill beat | a glass instead of a bowl, so medication is visibly its own job |
| `m` | microphone on/off | the capability a judge will remember |

## If something fails

Everything in `DEMO-SCRIPT.md`'s "three rules" section still applies. The two
additions for this version:

- **The measured body does not appear on `b`.** Say nothing and carry on. The
  rest of the demo is unaffected; it is an overlay.
- **The camera ends up somewhere wrong.** Press `r`. It returns to the wide
  shot in under a second, and the wide shot is the framing every other beat
  was composed against, so nothing else is disturbed. The camera cannot get
  stuck: each beat's key sets its own framing, so the next keypress fixes it
  regardless.
- **Voice does not hear the volunteer.** Press `7` and say "or the operator
  can just press a key". Do not repeat the phrase into the microphone twice;
  it reads as a failure both times. **The readout tells you which failure it
  is**: `SAY SOMETHING` means the microphone is open and the room is just
  loud, so one closer attempt is worth it. `NO NETWORK` or `NO MIC` means it
  cannot hear at all -- do not try again, go straight to `7`.
- **The question is heard but answered wrong.** Say "ask it how clean you
  are" and let the volunteer try once more. The question phrases are
  deliberately long ("how clean am I", not "clean") so an order cannot be
  mistaken for a question, and the cost of that is the volunteer has to ask
  a whole question rather than a word. If it still misses, the answer is on
  the screen anyway -- point at the counter and say the sentence yourself.
  Asking a question cannot break the rest of the demo: it starts no cycle and
  changes no mode, so whatever beat you were in is the beat you are still in.
  (The one exception is on purpose: asking for a heart rate while vitals is
  closed opens vitals, because the honest answer is to go and read the sensor
  rather than invent a number.)

## If a judge asks what else it does

Press `shift+7`. A glass of water travels to her mouth and the readout counts
sips rather than spoonfuls. It is the third thing the mode strip promises --
eating, drinking, pills -- and it takes five seconds to show.

Do NOT put it in the run of show. The 1:42 beat already spends its ten
seconds on three keypresses and the script tells you not to wait for any of
them to finish; a fourth overruns it.

## If a judge asks about safety

The full version of the line, which does NOT fit in the 1:34 beat and should
not be rushed into it: *"It refuses anything outside the workspace, and it
holds when two arms want the same space."*

**And you can point at the number.** The counts panel's bottom row says
CONTESTED 433 -- that is how many patches of this person more than one arm
can reach, so it is exactly how often "two arms want the same space" can
happen. It is the solver's own count, and pressing `n` changes it.

Point at the top-left while the arms are moving. Those are the governor's own
verdict names, not a caption: every command is cleared, held, retreated,
refused or stopped before it reaches an arm. Then the two you can demonstrate
on the spot: press `x` and the arms stop, and say "stop" with the microphone
on and they stop the same way.

## If a judge asks how the arms decide who does what

There is a second screen for that, and it is not in the run of show on
purpose -- two minutes is rehearsed and a second window is a way to lose it.
Open it afterwards, at the table.

```bash
PATH="$PWD/venv/bin:$PATH" ./venv/bin/python -W ignore \
  scrub3d/viz.py --preview /tmp/viz.png && open /tmp/viz.png
```

It draws the seated body with the four **real Waveshare arm meshes**, posed by
the same forward kinematics the collision checker uses -- so if a safety
margin were wrong, you would see the arm poke out of its own margin. The
header carries what the solver concluded: how much of the front is reachable,
the area each arm owns in cm2, and **which arms can move at the same time**.
On the current rig that is `[[0, 1, 2], [3]]` -- three can work together and
the fourth has to wait, because it would otherwise collide.

That grouping is the answer to "is this four robots or one robot four times".
Nothing on the projector shows it.

Expect a line saying the fused skin is unavailable and the body drawing in
grey. That is Open3D failing at a cosmetic surface; everything measured is
unaffected. See `scrub3d/README.md`.

## The one thing not to overclaim

The camera never leaves the laptop and that is true. **The voice does leave
it.** Chrome sends the audio to Google to transcribe. If a judge asks whether
everything is on-device, the honest answer is that the vision is and the
speech is not yet. A judge who catches one overclaim discounts everything
else.

## Is it actually speakable in two minutes?

Measured 2026-09-20, counting only the words the presenter says and
excluding stage directions:

| Beat | Length | Words | Pace |
|---|---|---|---|
| 0:00–0:15 | 15s | 32 | 128 wpm |
| 0:15–0:28 | 13s | 26 | 120 wpm |
| **0:28–0:40** | 12s | 35 | **175 wpm** |
| 0:40–0:52 | 12s | 8 | 40 wpm |
| 0:52–1:02 | 10s | 21 | 126 wpm |
| 1:02–1:12 | 10s | 5 | 30 wpm |
| 1:12–1:18 | 6s | 11 | 110 wpm |
| 1:18–1:34 | 16s | 4 | 15 wpm |
| **1:34–1:42** | 8s | 24 | **180 wpm** |
| 1:42–1:52 | 10s | 19 | 114 wpm |
| 1:52–2:00 | 8s | 5 | 38 wpm |

Comfortable presenting is 130 to 150 wpm. The two bolded beats are above
that and are the ones to watch in rehearsal: **0:28** (the privacy line,
where you now have a number to point at) and **1:34** (the payoff, where
the script already tells you to stop and let the confetti land). If either
runs long, cut words from 0:28 first -- the screen is doing the work there
and the count on it makes the point without narration.

The quiet beats are deliberate. 1:18 is four words over sixteen seconds
because the script says to stop talking and let the scrub play.
