# THE 2-MINUTE DEMO

Rehearse this five times. The version that runs cleanly five times beats the
version with an extra feature that runs twice.

**Cast.** *Presenter* — talks, never touches a keyboard. *Operator* — at the
laptop, silent, hand near the projector keyboard. *Volunteer* — a rehearsed
teammate, or better, a judge.

**Pre-set, before the timer starts**

- `./run.sh` running, Chrome kiosk on the projector
- **`Enter` already pressed** so audio is unlocked
- Arm homed, sponge damp-not-dripping (or dry — see §0c of the directive)
- A **taped X** on the table where the forearm goes
- Three brown splotches on the cartoon's forearm, counter reads `0%`
- Physical kill-switch on the 12V supply within the operator's reach
- `docs/RECOVERY-CARD.md` printed and taped to the laptop

---

| Time | Presenter | Operator | On screen |
|---|---|---|---|
| **0:00–0:14** | "Bathing is the number one daily activity people lose first. Seventy-four percent of people in residential care need help with it. And it's the most undignified thing to need help with." | idle | Cartoon idles, mirroring the presenter. Three brown splotches. `CLEANLINESS 0%` |
| **0:14–0:26** | "There aren't enough caregivers — and the ones we have get injured at **five times** the industry rate, lifting and turning people. So: a robot." | idle | Cartoon keeps idling and mirroring — same as the beat above |
| **0:26–0:38** | *(points at the screen)* "That's me. That's the **only** thing anyone sees. Every frame stays on this laptop. Nothing stored, nothing uploaded." | idle | Cartoon mirrors gestures |
| **0:38–0:48** | *waves both arms, turns* "The goofy avatar isn't a joke. It **is** the privacy mechanism." *(let this land)* | idle | Cartoon follows in sync — this is what proves it's live |
| **0:48–0:58** | "Can I borrow an arm?" | **presenter steps OUT of frame as the volunteer steps in — one person in shot at a time.** Then checks the debug window reads ARMED, and that the elbow dot, wrist dot and the line joining them sit on the volunteer’s arm | Cartoon keeps mirroring whoever the tracker is following |
| **0:58–1:06** | "Three dirty spots. Watch the counter." | volunteer rests forearm on the taped X | Splotches visible on the cartoon's forearm |
| **1:06–1:10** | "It won't start on its own — someone has to arm it." | presses **`s`** on the projector; the **top-right link indicator** flashes `ARMED` for ~1.6s | `ARMED`, top-right. Then the **left corner line flips `IDLE` → `APPROACHING`** the moment the arm starts moving. Two different confirmations: the right one says the server heard you, the left one says the machine is doing something |
| **1:10–1:22** | *(quieter)* "Watch." *then say nothing* | hand near `x` | Arm travels, descends, **oscillates**. **Say nothing — the scrub has its own sound now**, a soft squish on every stroke. Let it carry the beat. **The left corner reads `SCRUBBING`, then `RETURNING` when it lifts** — that is Python's actual state machine, so the audience can follow what the robot is doing without you narrating it |
| **1:22–1:32** | *(only AFTER the first pop)* "There." | if a splotch misses: press `1`, silently | Splotch 1 **POPS** — bubbles rise, sparkle, counter bounces `0 → 33%` |
| **1:32–1:44** | "It's tracking her actual forearm, live. It didn't know where her arm would be until she put it there. And the counter only moves on real contact — torque feedback from the joints." | — | Splotches 2 and 3 pop. `67% → 100%` |
| **1:44–1:52** | *(stop talking — but **about 2 seconds, not 3**)* | arm returns HOME | **CONFETTI both sides. FANFARE.** `100%` — **and it clears itself.** Measured over three runs: 100% holds **2.2 / 2.4 / 2.2s**, then the server's RETREAT fires its reset and the counter drops to 0%. The confetti is still falling, so the beat survives; what does not survive is pointing at the number. Say your line while it is up. |
| **1:52–2:00** | "Two-hundred-dollar arm, one webcam, no depth camera, no cloud. The only thing anyone ever sees is the cartoon. Ask us anything." | presses `r` — proves it's live, not a video | Splotches return and the character stands up. **The counter has already reset itself** by now (see the row above), so `r` is for the splotches, not the number. |

---

## The three rules

**Presenter:** never say "it should." Never say "as you can see" — point
instead. Never explain a failure in engineering terms. Hands off the keyboard.

**Operator, memorised:**
- Splotch doesn't pop within 3s → press its number. Silently.
- Want a laugh while resetting? **`e`** cycles the character's reactions and
  **`d`** flops it over. Both are one keypress and both prove the cartoon is a
  live rig rather than a video — which is the doubt a judge actually has. The corner HUD
  will blink `THIS CYCLE WILL RESET IT` for ~1.5s; **that is expected
  during a rescue** and nobody in the audience is reading the corner. It is
  there for the other case: tapping `1` `2` `3` when you did NOT mean to have a
  cycle running, where the counter jumping backwards would otherwise be a
  mystery.
- Arm behaves oddly → **`x`** immediately, then **read the indicator**:
  `ESTOP CONFIRMED` means the arm heard it; `ESTOP FAILED — CUT POWER` means
  it did not, so hit the physical switch. Then the presenter says *"that's the
  emergency stop — it's bound to a key and to a torque limit on every joint."*
  A visible safety cutout on a machine touching a person is a **feature**.
- If the estop prints **ESTOP WRITE FAILED** → the arm did not hear it and is
  retreating to HOME by itself. If it does not lift within a second, **cut
  power at the supply.** Say nothing; the presenter covers it.
- Total freeze → `Ctrl+C`, `REPLAY=recordings/good_run.jsonl ./run.sh`, and the
  presenter says *"we're on the recorded run — same software, same data."*

**Rehearse two things specifically:** the 1:10–1:22 window, which is the whole
demo, and **saying nothing** during it and over the confetti. The silence is
what makes the pop land.

---

## Questions you will get

**"Is the dirt detection real?"**
> "The splotches in this run are scripted for reliability. The detector is
> built — 365nm UV plus a hue and brightness threshold on the same fluorescent
> tracer hospitals use for hand-hygiene training. Want me to run it?"

Then run it if the shroud is set up. **Never claim it's live if it isn't.** A
judge who catches one overclaim discounts everything else you said.

**"How does it know where the arm is?"**
> "MediaPipe gives us 33 body keypoints from one webcam. The forearm rests on a
> table, which is a known plane, so a homography maps camera pixels straight to
> robot millimetres. No depth camera — we deleted that dependency on purpose."

**"What's the safety story?"**
> "Four layers. It never starts on its own — an operator arms each cycle, and
> arming never skips the hover. Torque limits in the firmware so it yields to
> contact instead of pushing through, watched on every joint. A workspace clamp
> and a reachability check before every command, and every move is rate-limited
> to 240mm/s — including the recovery after an emergency stop. And an e-stop on
> two keys plus that physical switch." *(Point at it.)*

If a judge pushes on the e-stop: *"It also refuses to lie. If the stop command
can't reach the arm, it says so and retreats to home instead of pretending it
stopped."* That is a real distinction and most demos cannot make it.

**"Would this work on a real person in a real bathroom?"**
> "Not this arm — it's a 200-dollar desktop unit with a two-kilo payload. What
> we're demonstrating is the perception and privacy architecture. That's the
> part that transfers."
