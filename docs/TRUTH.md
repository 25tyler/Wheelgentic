# TRUTH.md — the source of truth for Wheelgentic

> **SUPERSEDED ON SCOPE. Read
> `docs/sources/STANDING-PROMPT.md` first; it outranks this file.**
>
> This file was written, when the project was a one-arm
> forearm-scrubbing demo. The brainstorms in `docs/sources/` reset it to an
> **AI wheelchair with four arms** doing shower, eating and drinking, pill
> feeding, vitals sensing and voice control. Everything below about SCOPE --
> what the demo is, what is out of scope, which beats matter -- is the old
> product. `docs/PLAN.md` and `docs/DEMO-SCRIPT-V2.md` are the current ones.
>
> What is still authoritative here: the hard constraints, the instrument
> lessons in §3, and the drift analysis in §7. Those are about how to work,
> not about what to build, and they did not change.

**Read this before anything else. Every work item must trace to a line in this
file. If it does not, do not start it.**

Written after reading every document in this repo, all 282 commits,
and all 89 of Tyler's messages across both sessions. It exists because the work
drifted badly and nothing in the repo could detect that. See §7.

Companions, all authoritative:
`docs/STATE.md` (what actually works) · `docs/DECISIONS.md` (settled calls) ·
`docs/ROADMAP.md` (what is left, ranked).

---

## 1. WHAT THIS IS

A Waveshare RoArm-M2-S with a sponge scrubs a person's forearm. One webcam and
MediaPipe find the forearm. A projector shows a goofy low-poly cartoon that
mirrors the person, with dirt splotches that pop with soap bubbles as the real
arm scrubs.

**The pitch:** bathing is the #1 daily activity people lose first and the most
undignified to need help with. 74.5% of US residential-care residents need help
bathing. There are not enough caregivers, and the ones we have are injured at
5x the industry rate lifting and turning people. So: a robot.

**The privacy mechanism IS the goofy avatar.** Every frame is processed
on-device, nothing is stored, nothing leaves the laptop, and the only thing
anyone — including us — ever sees is the cartoon. That is not a joke bolted
onto a demo; it is the architectural answer to "you are pointing a camera at a
naked person."

MIT hackathon, ~36h build, demoed on a projector to judges.

---

## 2. WHAT "DONE" MEANS

**Done is a 2-minute demo that runs cleanly five times in a row in front of a
judge.** Not a green test suite. Not a complete work queue.

`docs/DEMO-SCRIPT.md` is the acceptance test. Twelve timed beats. The demo is
done when every beat has been driven end to end on the real key path and the
whole thing survives five consecutive rehearsals.

The demo script says it better than any metric:

> "Rehearse this five times. The version that runs cleanly five times beats
> the version with an extra feature that runs twice."

---

## 3. TYLER'S STANDING INSTRUCTIONS — verbatim, with dates

These are quoted from the transcripts, not paraphrased. They do not expire.

**On never stopping** (, said four times, escalating):
> "never wait on my inputs ever. i told you to do the highest roi thing at any
> point in time and to use your own judgement"
> "you keep stopping pre emptively when i asked you to keep going without
> stopping. you shouldnt rely on the timer though"

**On what to work on** ( 04:19 — THE REDIRECT):
> "stop working on testing and work on vastly improving the application. do a
> full 180 and comprehensive docuemnt explaining what you need to do and then
> do it. its a hackathon project, do not work on tests work on improving it
> immensely."

**On reuse over building** (, said twice):
> "i think it would be more worth it if you use already made unity versions of
> this... i cant emphasize enough to not make things yourself if you can just
> take it from already existing things online"

**On the character's feel**:
> "it needs to look like a character made from a game engine to look
> goofy/funny. think of things like gangbeasts. fall guys, dumb ways to die,
> peak (especially peak), etc. characters like that that are highly responsive
> to game engine like physics but in a fun way."

**On research depth**:
> "do an immensely large research phase where you find the best of the best
> tools, ideas, github repos, etc to directly use in this project"

**On how to explain things**:
> "whenever i ask something you always explain it in an extremely convoluted
> way. make a rule that you have to explain it in the most easy to understand
> and straightforward way possible, without the fluff of the random details
> and more of what's actually important for me (like the high level specifics,
> not the sequence of how you got there with confusing terminology)"

Lead with the answer. No sequence-narration, no internal jargon, no
measurement dumps as proof. A detail earns its place only if it changes what
he would think or do. Decisions he has to make go first, not last. Full rule
in `CLAUDE.md`.

**How these interact.** "Never stop" is about *cadence*, not *direction*. It
was never permission to generate arbitrary work. When "never stop" and "do not
work on tests" conflict, the redirect wins: keep working, but on the
application. Running out of demo work means asking what a judge sees that is
still weak — not opening a new verification front.

---

## 3b. THE THREE RULES THAT DECIDE HOW WORK GETS DONE

These are Tyler's, stated repeatedly, and they override instinct. Every one of
them has been violated on this project already, which is why they are here in
full rather than as a line in a list.

---

### 3b.1 TAKE IT FROM ONLINE. DO NOT BUILD IT.

Tyler, said twice and escalating:

> "i think it would be more worth it if you use already made unity versions of
> this. everything youve made isnt what i had in mind and all versions that i
> want are from games, so taking a game engine version that already exists can
> help. **i cant emphasize enough to not make things yourself if you can just
> take it from already existing things online**"

And again:

> "make a huge thing in the documents about taking as much as you can from
> online"

**This is the default, not a fallback.** Before writing any asset, effect,
shader, animation, model, UI component, sound, or physics behaviour, the first
action is to search for it. Building it yourself is the last resort and it
needs a written justification.

**The order of operations, every time:**

1. **Search.** GitHub, npm, Kenney, Poly Pizza, Sketchfab, itch.io, CodePen,
   three.js examples, Quaternius, OpenGameArt, Mixamo, freesound.
2. **Check the licence.** CC0 and MIT are free to take. Anything else, move on
   — a hackathon is not the place for an attribution dispute.
3. **Take it and adapt it.** Rename, recolour, rescale, restyle. Adapting
   someone else's asset is the job; making one from scratch is the failure.
4. **Only if genuinely nothing exists**, build it — and write down what you
   searched for and why nothing fit.

**The evidence this rule exists.** The character went through TWO wrong shapes
before anyone searched: first Kenney's *blocky* pack (reads as Minecraft), then
a blob hand-sculpted from capsules and lathes. Both were thrown away. The
pack actually in use — Kenney Mini Characters, CC0 — was found in minutes and
shipped a skinned rig plus **32 professional animations** that no amount of
hand-building would have produced.

**What is already downloaded and unused.** `vendor.sh` pulls the Kenney Mini
Characters zip, which contains **26 models**. We ship **12**. Sitting unused
in that archive right now:

| Unused asset | Why it matters here |
|---|---|
| `wheelchair`, `wheelchair-deluxe`, `wheelchair-power`, `wheelchair-power-deluxe` | **Stays unused. Tyler: "i want it gone".** One was in the scene; it labelled a volunteer who never agreed to it. No mobility prop as scenery. See `DECISIONS.md` |
| `aid-cane`, `aid-cane-blind`, `aid-cane-low-vision`, `aid-crutch` | **Stays unused, same reversal as the row above.** These are the obvious substitute for the removed wheelchair. Putting one beside the volunteer is the same decision with a smaller model, so it is the same answer: no |
| `aid-glasses`, `aid-sunglasses`, `aid-mask`, `aid_hearing` | character variety, free |
| `character-male-a` | a 13th character we simply never extracted |

Free, CC0, already on disk after one `vendor.sh` run. Nothing needs building.

---

### 3b.2 IT MUST LOOK GREAT. THAT OUTRANKS PROVING IT WORKS.

Tyler:

> "you need to focus much more on features looking great rather than working
> great (ie with tests)"

And earlier, the redirect this repeats:

> "its a hackathon project, do not work on tests work on improving it
> immensely"

**What this means concretely.** A judge stands ten feet from a projector for
120 seconds. They cannot see a test suite. They cannot see an assertion count.
They see the screen and they hear the room.

- A feature that **looks incredible and is slightly fragile** beats a feature
  that is bulletproof and dull. Every time.
- The existing suite is a **regression gate only**. Run it before committing.
  Do not add to it. Do not spend an hour making it more thorough.
- "Is it verified?" is the wrong question. **"Does it look good on the
  projector?"** is the right one, and it is answered by a screenshot, not by an
  assertion.
- Polish that only a code reader would notice is not polish.

**The evidence.** In the 20 hours after the first redirect, 124 commits landed
and **6 touched `web/`**. Meanwhile the projected frame was measured at **94%
empty void** — a small cartoon floating in a black rectangle with a grey stick
passing through its arm. That was found by screenshotting the page, which no
amount of test-writing would ever have surfaced.

---

### 3b.3 MAKE IT FEEL LIKE A POLISHED VIDEO GAME — OUTFITS, SKIN, FACES

Tyler:

> "make sure wherever you found the models you can change the outfits, skin
> colors, etc to make it feel much more like a polished video game"

And on the feel he wants:

> "it needs to look like a character made from a game engine to look
> goofy/funny. think of things like **gangbeasts, fall guys, dumb ways to die,
> peak (especially peak)** — characters that are highly responsive to game
> engine like physics but in a fun way"

**This is already possible and nobody has used it.** Measured:

All 12 characters share **one texture atlas**: `web/assets/Textures/colormap.png`,
512×512, containing **162 flat colour swatches** in vertical column families:

| Atlas region (px) | Family |
|---|---|
| x32–64, y256–384 | **skin / tan** (`#fde4c7` → `#f4ca98`) |
| x96–128 | greens |
| x160–192 | yellows / golds |
| x224–256 | oranges |
| x288–320 | reds |
| x352–384 | blues |
| x416–448 | pale blues |
| x480–512 | purples |
| y384–512, x320–512 | **a full skin-tone range** — `#f2bf99`, `#f1976c`, `#b06041`, `#845442`, `#cc875f` |
| y384–512, x0–320 | greys, charcoals, whites |

Because every model UV-maps onto flat patches of this one image, **changing a
character's skin tone or clothing colour means repainting a swatch or shifting
a UV island — not building a model and not authoring a texture.**

**Why this is worth real effort, beyond looking good:** the pitch is that
being bathed by another person is undignified and the cartoon is what protects
the volunteer's privacy. A volunteer who sees a character with *their* skin
tone on the projector gets "that's me" instead of "that's a generic guy." The
customization is not decoration; it strengthens the exact claim the demo makes.

**What to build (see `ROADMAP.md` R5):** an operator key that cycles skin
tone, and one that cycles outfit colour. Both shipped. All of it is
`web/`-only, visible from ten feet, and none of it requires touching `py/`.

**NOT the wheelchair or cane props.** An earlier version of this line told the
reader to place them in the scene. One was placed, and Tyler had it removed on
: *"i want it gone."* A mobility prop beside the volunteer labels a
person who never agreed to carry that label. `DECISIONS.md` has the full
reversal. The models stay vendored and unloaded.

---

## 4. THE TWO RISKS THAT CAN END THE PROJECT

Both have sat unresolved in `WORK-QUEUE.md`, under the heading
`NOT MINE — do not idle on these`, inside an append-only log that grows every
session. They are surfaced here because burying an existential risk in that
log is how it gets missed.

**Grep the heading, do not cite a line number.** This paragraph used to say
"line 4284, inside a 4,546-line file". By the time anyone read it the log had
passed 6,400 lines and 4284 pointed at an unrelated note. A line number into
an append-only file is stale the next time anyone appends.

### 4.1 The bench-mount rule — CAN DISQUALIFY

The teammate brainstorm says: *"Rules say bench mounted only. This is vague
though. How to circumvent it? Arm uses clamping mechanism, could we just clamp
on the chair?"*

**Nobody has read the actual rule.** Everything else in this repo is
recoverable; this is not.

The fallback is free and already supported: bench-mount the arm and put the
person's forearm on the bench. That is exactly what the software assumes — a
forearm on a flat plane at a known height. It costs one `config.json` value
(`forearm_z_mm`) and a 90-second calibration. **Do not "circumvent" the rule.**

### 4.2 "No code written before the event"

HackMIT rules typically forbid pre-written code. **This repo has weeks of
commits.** Tyler must resolve this with an organiser. If the answer is bad,
everything here becomes a prototype to be rebuilt during the event, and that
changes the whole plan.

**Neither of these is mine to resolve.** Both are listed first because they
dominate every other consideration.

**But the asking is now written, so the remaining step is smaller than this
section used to describe.** `docs/OPEN-QUESTIONS.md` carries a paste-ready
message for each: section 1, "Step 2, written out" for the bench-mount rule,
and section 7 for the pre-written-code rule. Each asks one narrow question and
volunteers the conservative answer we have already built for, so a restrictive
reply costs nothing. Send them separately; the two questions may go to
different people.

**So these are no longer unasked. They are unsent.** Surface them that way.
Reading this section as "nobody has asked" invites writing the messages again,
which is where a whole session went before they existed.

---

## 5. HARD CONSTRAINTS

- **Never break the demo to improve it.** Every change ends with the page
  loading and the 2-minute script still runnable.
- **Never edit a shared file while the suite is running.**
- **Plant-verify every new guard** — reintroduce the bug, watch the guard fail,
  remove the plant. A guard that has never been seen to fail is decoration.
- **Commit and push each landing.**
- **Keep localhost alive on :8000.** Tyler checks it.
- **RUN IT.** Every bug that mattered on this project was invisible to code
  review. Five real defects passed syntax checks, console checks and DOM
  probes. They were caught by executing the thing and looking at the output.
- **Do not touch `py/` safety code for polish.** The estop has produced five
  separate critical bugs. It is not where hackathon polish belongs.

---

## 6. WHAT IS EXPLICITLY OUT OF SCOPE

Listed so they stop being rediscovered. Full rationale in `DECISIONS.md`.

- **New tests.** The suite is a regression gate at 26/26. It does not grow.
- **Test-infrastructure work** — log retention, watchdog plumbing, suite
  timing, assertion counting, doc-vs-code guards. All of it is off-limits by
  Tyler's redirect, not by the queue.
- **Config-parsing robustness.** Done to the point of diminishing returns.
- **The four-arm build.** One arm done well beats four half-working.
- **Depth cameras / point clouds.** Ruled out on Apple Silicon.
- **"Point at an area to clean more."** Researched, then cut: it requires the
  volunteer to reach into a moving robot's envelope.
- **Anticipation on the arm descent.** Built, measured at 7.6px against a 41px
  stroke, reverted as invisible.

---

## 7. THE DRIFT THIS DOCUMENT EXISTS TO PREVENT

Measured across all 282 commits, classified by the surface each one
primarily touched.

| Period | DOCS | TEST-INFRA | ROBOT/SAFETY | DEMO-VISIBLE |
|---|---|---|---|---|
| All 282 commits | 114 | 63 | 47 | 53 |
| **Since the redirect (124)** | **89** | **23** | **6** | **6** |

Tyler said "do not work on tests work on improving it immensely" at 04:19 on
. In the 20 hours after that, **6 of 124 commits touched `web/`** —
the only directory a judge ever sees. Hours 17 through 22 of that day produced
13, 10, 9 and 12 queue records against 0, 0, 0 and 1 code changes.

**The mechanism.** `WORK-QUEUE.md` is append-only, self-feeding (17 generators
that manufacture new items when it empties), and calibrated on a line from
`DIRECTIVE.md` §00 that says *"verification of things believed-working has
consistently outscored new features."* That line was true when it was written.
It stopped being true the moment Tyler redirected, and nothing in the loop
could notice, because the loop's own success metric is records appended.

**The correction.** The queue is demoted to an append-only audit log. It is no
longer the answer to "what next." `ROADMAP.md` is. Before starting any item,
answer in one sentence: *what does a judge see differently because of this?*
If the answer is "nothing," it is not the next item.

---

## 8. THE HARDWARE GAP — state it honestly

**No RoArm-M2-S has ever been connected.** Not once, in 282 commits. Everything
about the arm is proven against `tests/fake_roarm.py`, a pty-based simulator
speaking the real ESP32 JSON dialect.

That catches wire format, rate limiting, estop logic and protocol ordering. It
does **not** catch real servo dynamics, brownout under load, the actual reach
envelope, CP210x driver behaviour, or firmware quirks.

First contact with real hardware is a go/no-go Tyler runs (`python py/arm.py`).
Until then, `CAM=fake ./run.sh --no-arm` runs the entire demo end to end with
no hardware at all, and that is the only claim to make.

---

## 9. HOW TO USE THESE DOCUMENTS

1. Read `TRUTH.md` (this file) — what we are building and why.
2. Read `ROADMAP.md` — take the top item. It is ranked by what a judge sees.
3. Check `DECISIONS.md` before proposing anything that sounds new. It has
   probably been settled.
4. Check `STATE.md` before claiming something works. It separates measured from
   assumed.
5. Do the work. Run it. Look at it. Commit and push.
6. Append one line to `WORK-QUEUE.md` as an audit record — **after** the work,
   never as the work.

`DIRECTIVE.md` remains valuable as the hard-won-facts archive (§3 especially:
every trap that cost hours is written there). It is no longer the entry point.
