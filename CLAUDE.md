# CLAUDE.md — Wheelgentic (`~/Wheelgentic`)

## THE HIGHEST SOURCE OF TRUTH IS `docs/sources/STANDING-PROMPT.md`

**Read it first, verbatim, every session and every 20 minutes.** It is Tyler's
standing prompt word for word and it outranks every other file here, including
`docs/TRUTH.md`. A 20-minute timer re-reads it to prevent drift.

### The three sources it names, and how to use them

| Source | Where | How to use it |
|---|---|---|
| **Brainstorm 2 — the vision** | `docs/sources/BRAINSTORM-2-vision.md` | The product is an **AI wheelchair with 4 robotic arms**: shower, eating/drinking, pill feeding, vitals sensing, voice control. Re-explain it to yourself every 20 minutes. Any work that does not serve this vision is the wrong work. |
| **Brainstorm 1 — hardware** | `docs/sources/BRAINSTORM-1-hardware-list.md` | What the team has vs what they are requesting. Read before assuming a sensor, board or screen exists. Names the wheelchair, the 4 arms, the AGX Spark, ESP32, vitals sensors (MAX30102), LCD and touchscreen. |
| **ECC** | `.claude/` in this repo (292 skills, 68 agents, 94 commands, 23 rules). Upstream: `github.com/affaan-m/ECC` | **Use it to its full potential — no exceptions.** Before hand-writing anything, check for a skill. **Checked 2026-09-19, so nobody re-checks:** the three motion skills (`motion-foundations`, `motion-patterns`, `motion-advanced`) are all React/Next.js built on Framer Motion and DO NOT APPLY here — this page is vanilla JS with three.js and GSAP and no React anywhere. `liquid-glass-design` is SwiftUI. What does apply: **`ui-demo`** (Playwright demo recording; its injected-cursor technique became the keypress overlay in `tools/record_backup.py`, and its discover-before-you-script phase is what caught the backup video covering only one of the five capabilities). Worth a look but unused so far: `frontend-design-direction`, `design-system`, `remotion-video-creation`, `orch-build-mvp`. Delegate to the 68 agents rather than doing everything inline. |

### The standing rules from that prompt

0. **NEVER STOP. Writing prose to the user IS stopping.** A status summary, a
   recap of what landed, ending a turn because a milestone looked tidy — all
   of those are the failure this project's timer exists to catch. The only
   acceptable end to a turn is running out of context or Tyler interrupting.
   If something on the demo screen is visibly broken, fix it before anything
   else; leaving it to write a summary is the worst available trade.
1. **Never stall.** There is no such thing as a blocker — there is always a
   high-ROI task. If one path closes, name the next and start it.
2. **Take, do not build.** Search GitHub, npm, asset libraries and ECC skills
   FIRST for every asset, animation, effect and component. Write down what was
   searched. Hand-building is the failure case.
3. **The merge is co-equal with the vision.** `scrub3d` is the backend (body
   scan, 4-arm partition, live tracking, safety governor). `main` is the
   frontend (cartoon, HUD, juice). They become one application.
4. **Impressive beats correct.** This is a demonstration, not a product. If it
   works in a presentation that is good enough. **Bugs are the lowest
   priority.** Never spend a session fixing bugs.
5. **Commit often, document as you go.** `docs/PROGRESS.md` is the running log
   so no time is lost re-deriving state.

---

## THE SOURCE OF TRUTH FOR THE OLD SCOPE IS `docs/TRUTH.md`

**Read it first, in every session, before any other file.** Then:

| File | What it is | When |
|---|---|---|
| **`docs/TRUTH.md`** | **THE SOURCE OF TRUTH** — what this is, what "done" means, Tyler's standing instructions verbatim, the two risks that can end the project, hard constraints, what is out of scope | always, first |
| **`docs/DEMO-SCRIPT-V2.md`** | **THE RUN OF SHOW** for the wheelchair demo. `DEMO-SCRIPT.md` is the old one-arm version, kept for its rules and recovery lines | before rehearsing |
| **`docs/ROADMAP.md`** | **THE WORK SOURCE** — what is left, ranked by what a judge sees | to pick the next item |
| **`docs/STATE.md`** | what actually works, measured vs assumed | before claiming anything works |
| **`docs/DECISIONS.md`** | settled calls with their rationale | before proposing anything that sounds new |
| `docs/DIRECTIVE.md` | the hard-won-facts archive (§3 especially) — every trap that cost hours | when you hit something strange |
| `docs/WORK-QUEUE.md` | **an append-only audit log, NOT a work source** | after finishing work, to record it |

---

## The two rules that override everything else

**1. Never stop.** Tyler, said four times:

> "never wait on my inputs ever. i told you to do the highest roi thing at any
> point in time and to use your own judgement"

Do not ask. Do not stop to report. The turn ends when the work runs out, not
when a milestone looks tidy. If the last action was prose, the turn ended early.

**2. Work on the application, not on tests.** Tyler:

> "stop working on testing and work on vastly improving the application. do a
> full 180 and comprehensive docuemnt explaining what you need to do and then
> do it. its a hackathon project, do not work on tests work on improving it
> immensely."

These interact: "never stop" is about *cadence*, not *direction*. Running out
of demo work means asking what a judge still sees as weak — not opening a new
verification front. **The drift check before starting anything: what does a
judge see differently because of this?**

---

## HOW TO TALK TO TYLER — plain and short, always

Tyler:

> "whenever i ask something you always explain it in an extremely convoluted
> way. make a rule that you have to explain it in the most easy to understand
> and straightforward way possible, without the fluff of the random details
> and more of what's actually important for me (like the high level specifics,
> not the sequence of how you got there with confusing terminology)"

**Lead with the answer.** What happened, what it means, what is next. Not the
path you took to find out.

**Never do these:**
- Narrate the sequence ("first I read X, then I ran Y, then I found Z")
- Use internal jargon: *plant-verified, gate score, red-then-green, the 4b
  section, quaternion delta, population floor*. That is working vocabulary,
  not shared language.
- Dump measurements as proof. "46 samples, minimum 0.0272" is not an answer.
  "The sponge actually touches the dirt" is.
- Bury a decision he has to make under detail. It goes first.

**Always do these:**
- Say it in words a smart person outside the project would follow.
- Include a detail ONLY if it changes what he would think or do.
- Separate **what he needs to decide** from **what I did**.
- Stop when the point is made.

**Why this is a correctness rule, not a style preference:** the bench-mount
rule — the one thing that can disqualify the project — sat unread for days
because it was buried in a 4,500-line file surrounded by measurement detail.
Writing that does not reach him is the same as not writing it.

## THE THREE RULES THAT DECIDE WHAT TO BUILD — full text in `docs/TRUTH.md` §3b

### 1. Take it from online. Building it yourself is the failure case.

> "i cant emphasize enough to not make things yourself if you can just take it
> from already existing things online" — Tyler, said twice

Search FIRST, every time, before writing any asset, effect, model, animation,
sound or component: GitHub, Kenney, Poly Pizza, Sketchfab, itch.io, three.js
examples, Quaternius, Mixamo, freesound. CC0 or MIT only. Adapt what you find.
Build only when a real search found nothing, and write down what you searched.

*Cost of ignoring it:* two characters were built and thrown away before anyone
searched. The one that shipped was found in minutes with 32 free animations.
*Sitting unused right now:* 4 wheelchairs, 10 mobility aids, a 13th character
— 26 models in the downloaded zip, 12 extracted.

### 2. Looking great outranks proving it works.

> "you need to focus much more on features looking great rather than working
> great (ie with tests)" — Tyler

A judge sees a projector for 120 seconds. They cannot see a test suite. A
feature that looks incredible and is slightly fragile beats one that is
bulletproof and dull. The suite is a regression gate and does not grow. Ask
"does it look good on the projector" and answer it with a screenshot — not "is
it verified", answered with an assertion.

### 3. The character must be customizable — outfits, skin tones, props.

> "make sure wherever you found the models you can change the outfits, skin
> colors, etc to make it feel much more like a polished video game" — Tyler

All 12 characters share one 512×512 atlas (`web/assets/Textures/colormap.png`)
of 162 flat swatches: skin tones, clothing colour families, greys. Recolouring
is repainting a swatch or moving a UV island — no new model, no new texture.
This is not decoration: the pitch is that the cartoon protects the volunteer's
dignity, and a volunteer who sees their own skin tone gets "that's me".

## Hard constraints

- Never break the demo to improve it. Every change ends with the page loading
  and the 2-minute script runnable.
- Never edit a shared file while the suite is running.
- Plant-verify every new guard — red first, then green, then restore and
  confirm the tree is byte-clean.
- Commit and push each landing.
- Keep localhost alive on `:8000`.
- **RUN IT.** Every bug that mattered here was invisible to code review.

## Two interpreters — getting this wrong gives an empty log

- `./venv/bin/python` — cv2, mediapipe, pyserial
- `python3` — playwright

`tests/run_all.sh` picks per test. A cv2 test under `python3` fails at import.

## Commands

```bash
CAM=fake ./run.sh --no-arm              # the whole demo, no hardware
bash tests/run_all.sh                   # 37 tests, ~770s — regression gate only
# Reading its result: a failed ASSERTION prints `*** N FAILED: [...]`, but a
# TIMEOUT prints `*** WATCHDOG: <test> exceeded Ns — aborting ***` and
# `-> *** FAIL (exit 3, Ns)`. Grepping `FAILED:` alone is blind to the second.
# Gate on `FAIL` and `WATCHDOG`, or on the absence of `ALL CHECKS PASSED`.
# Then, if that WATCHDOG names test_arm_protocol.py: it is the known flake,
# MEASURED 2026-09-21: 3 aborts in 20 suite runs that reached it -- about 15%,
# five times the 4-in-126 figure this line used to quote. Do not treat 3% as
# the rate. Always that test, always section 7g, always the
# block being slow rather than wedged. Re-run it alone before calling it a
# break; it has passed at exit 0 every single time it has been re-run.
# DIAGNOSED and the budget raised 150 -> 210 on 2026-09-21: the cause is a
# load-sensitive retry sweep, not a wedge. See DIRECTIVE's traps section:
#   ./venv/bin/python tests/test_arm_protocol.py
# Passes at exit 0 there means the suite result was the flake. STATE.md 5.
# WAITING FOR THE SUITE: watch /tmp/.wheelgentic-suite.lock, NOT pgrep. A
# `pgrep -f run_all.sh` in a waiter matches the waiter's OWN command string,
# so it never goes quiet and the wait never ends -- I measured 52 matches for
# 3 real suites. run_all.sh's own comment at line 48 says the same thing about
# its self-exclusion problem; the lockfile exists because of it.
#   until [ ! -d /tmp/.wheelgentic-suite.lock ]; do sleep 30; done
bash tests/quick.sh                     # ~250s, no browser tests
python3 -m http.server 8000 -d web      # the projector page alone
```

## Writing

Commit messages and docs go through the `avoid-ai-writing` standard: subject
≤72 chars, no line >72, no em dashes, detector score 0. Gate before committing.
