# WHEELGENTIC — RECOVERY CARD

**Print this. Tape it to the laptop.** Every line has been executed and timed.

---

## KEYS

**Python window (the OpenCV debug view)**

| Key | Does |
|---|---|
| `SPACE` | **EMERGENCY STOP** — sends `T:0`, arm freezes |
| `r` | clear estop, return to IDLE. **The splotches and counter reset** — an estop ends the cycle, it does not pause it. Press `s` to start a fresh one. |
| `h` | home |
| `s` | **ARM one scrub cycle.** Arms only — it never jumps the arm straight to contact. Safe to press twice. |
| `1` `2` `3` | pop a splotch manually |
| `q` | quit |

**On the PROJECTOR** (Chrome, not the Python window) the wheelchair demo adds:

| Key | Does |
|---|---|
| `b` | the measured body beside the person, on and off |
| `g` | **the handoff camera move.** A slow 6s drift for the 1:02 beat, while the presenter and volunteer trade places. Changes nothing else -- no mode, no cycle, no props -- so it is safe to press any time and safe to forget; the next mode key takes the camera back. |
| `n` | swap to a differently shaped person, territories re-solve. **Pressing it mid-cycle ends the cycle** -- counter back to 0%, dirt restored. A new person has not been washed. |
| | **If `b` or `n` do nothing**, the scan file is missing. Everything else works; skip that beat and carry on. Verified: page renders, all modes switch, zero errors. |
| `shift+7` | the drink beat, a glass of water counted in sips |
| `shift+8` | the pill beat, a glass rather than a bowl |
| `7` `8` `9` `0` | shower, feed, vitals, voice |
| `m` | microphone on and off |

**Only `SPACE`, `r` and `q` work in two states.** The Python window reads keys
in three separate places, and two of them are cut down on purpose so an
operator always has an estop:

- **While the camera is dropped** (no frame arriving) the arm retreats home and
  the window reads `SPACE`, `r`, `q` only. `h`, `s` and `1` `2` `3` do nothing.
- **While an estop is held**, the same three, and `r` there clears the estop
  without returning to IDLE.

This matters for the `NO LINK` row below: pressing `s` in the Python window is
the right move when the socket is down, but **not if the camera dropped too**.
Pop splotches from the projector instead, which keeps working either way.

**Browser (the projector)**

| Key | Does |
|---|---|
| `Enter` | start + **UNLOCK AUDIO** — do this before the demo, always |
| **`s`** | **ARM one scrub cycle** — works from the projector, no alt-tab |
| **`x`** | **EMERGENCY STOP** from the projector. **Watch the indicator:** `ESTOP CONFIRMED` = the arm heard it. `ESTOP FAILED — CUT POWER` = it did not. `NO LINK` = hit SPACE in the Python window. |
| **`shift+C`** | **CLEAR** an emergency stop, from the projector. **The whole recovery flow is tested as one flow** (`tests/test_recovery_sequence.py`): stop with `x`, clear with this, reset with `r`, then `1` `2` `3` — the counter reaches 100% with no errors, so this works even with the arm dead.  Says `CLEARED` on success, `CLEAR FAILED — still stopped` if the arm never heard it (press again). The torque watchdog fires on its own, so this is how you recover without hunting for the Python window. `NO LINK — press r in the python window` = the socket is down, so the projector cannot clear it; press `r` in the Python window instead. **Splotches and counter reset** — the cycle is over, not paused. |
| `1` `2` `3` | pop a splotch (works with the server dead) |
| **`e`** | **EMOTE** — cycles the character's reaction clips (yes / no / reach / pick-up / kick / jump). A laugh on demand. |
| **`d`** | **THE DEATH GAG** — the character flops over **and stays down** (a punchline that springs upright in 120ms is not a punchline). Any emote (`e`) or `r` stands them back up. Deliberate comedy, and it proves the cartoon is a live rig, not a video. |
| **`k`** | **NEXT SKIN TONE** — 6 tones. Repaints the texture in place, so it is instant and **safe to press mid-demo** (unlike `v`). Match the volunteer if you can: "that's me" is the privacy pitch landing. | 0s |
| **`o`** | **NEXT OUTFIT** — 6 shirt and shorts palettes. Instant, no reload. | 0s |
| **`j`** | **NEXT ACCESSORY** — glasses, sunglasses, mask, hearing aid, cane, blind cane, low-vision cane, crutch, then back to none. Same argument as the skin tone: a volunteer who sees their own glasses sees themselves. Instant, no reload. | 0s |
| **`v`** | **NEXT CHARACTER** — 12 people, same rig. **SETUP ONLY: it reloads the page.** Pick before the judges arrive, never during. |
| `f` | force the finale |
| `r` | reset splotches + counter — **and stands the character back up** if you left them flopped with `d` |
| `c` | **same as `f`** — calls the finale: confetti, fanfare, counter turns green. There is no confetti-only key. |

---

## IF SOMETHING BREAKS

| Symptom | Fix | Time |
|---|---|---|
| **Arm doing anything alarming** | `SPACE`. Then the physical switch on the 12V supply. | instant |
| Camera dead / black | `REPLAY=recordings/good_run.jsonl ./run.sh` — then **pop splotches with `1` `2` `3` on the projector.** | 10s |
| On replay, `s` says `ARMED` and nothing else happens | **Expected. Not a bug, and not recoverable by waiting.** Replay never runs the vision loop, so there is no APPROACH/SCRUB cycle and no automatic pop — measured: 0 pops in 36s, even with `--no-contact-gate`. The cartoon still mirrors the recording and the arm still tracks the forearm. **Use `1` `2` `3` on the projector** (verified end to end on replay: 0% → 100%, 3/3). The Python window's `1` `2` `3` is dead here too — replay opens no OpenCV window. | 0s |
| `run.sh` prints "FATAL: … is missing: cv2 …" | `source venv/bin/activate` first, or rebuild the venv | 60s |
| `./run.sh: no such file or directory` | You are not in `~/Wheelgentic`. `cd ~/Wheelgentic && ./run.sh` — or just run it by full path, `~/Wheelgentic/run.sh`, which works from anywhere (`run.sh` cd's to its own directory first). | 5s |
| Python crashed mid-demo | **Ignore it.** The cartoon keeps mirroring at full framerate. Use `1` `2` `3`. Verified under SIGKILL. | 0s |
| Big readout says `NO NETWORK` | Chrome sends the audio to Google to transcribe, so voice needs the venue wifi. It has stopped itself rather than looping. Press `7` and say "or the operator can just press a key". Press `m` to retry if the wifi comes back -- that clears the message. | 0s |
| Big readout says `NO MIC` | Permission was denied or the OS took the device. Same move: `7`, and carry on. Granting it needs a reload, which is not worth 20 seconds on stage. | 0s |
| The chair answers **"I can focus on your left arm or your right arm. Which one?"** | **Working as intended, not a failure.** It heard the words but the spot asked for is not one the four arms can reach -- they own the forearms and upper arms and nothing else. Say **"clean my left arm"** and it names the arm that owns that side. Ask for the right instead and a different arm answers, which is a better beat anyway. | 0s |
| Big readout says `SAY SOMETHING` and nothing happens | The microphone IS open and hearing nothing -- a noisy room, or the volunteer is too far from the laptop. Ask them once more, closer. If it fails twice, `7` and move on; do not let a third attempt eat the beat. | 0s |
| Top-right care panel reads about **5s** instead of ~30s | **Not broken — the arm is not connected.** The scrub is most of the real care and it only counts while Python is driving a live cycle, so a keyboard-only run counts the feeding and vitals beats and nothing else. If the HUD also says `ARM ○ MANUAL`, that is the same fact twice. Nothing to fix mid-demo. | 0s |
| Top-left says **`LOOKING FOR A SUBJECT`** (amber) | **Normal during the 1:02 handoff, and normal any time nobody is in front of the camera.** It is the tracker telling you it has nobody, not a fault — that is why it is amber and not red. It flips to **`SUBJECT LOCKED`** in green the moment it picks someone up. If it stays amber with someone clearly in shot, they are out of the camera's view or too dark; move them toward the laptop. The cartoon holds its last pose meanwhile, which is the honest thing for it to do. | 0s |
| Top-left reads **`0 SEEN`** and stays there | The camera or the pose model is not running — permission denied, no device, or a missing vendor file. **The count is telling the truth, which is the point of it.** Everything that does not need the camera still works: all four modes, the keys, the counter, the voice. The cartoon will not mirror anyone, so skip the "that's the volunteer" line at 0:28 and say the frames stay on the laptop without pointing at the number. Verified: page renders, feed and vitals run, zero errors. | 0s |
| HUD says `ARM ○ MANUAL` | Socket is down. Cartoon is fine. Pop splotches by hand. | 0s |
| Top-left says `CYCLE RUNNING` but nothing is moving | **On the replay fallback this used to be permanent and is now fixed** -- replay reports IDLE and the line clears. If you still see it with a frozen counter, the line is telling the truth about a cycle that armed and then stalled: press `x`, then `shift+C`, then `s`. | 5s |
| `ARM ○ MANUAL` and the run has been up **more than five minutes** | **This is the expected `CAM=fake` death, not a new fault.** MediaPipe's allocator falls behind a camera that never blocks; measured at 376, 378 and 405 seconds. `Ctrl+C` in the Python window and re-run `CAM=fake ./run.sh --no-arm`. The page does not need reloading, it reconnects by itself. **Relaunch right before you present rather than leaving it running through the queue.** | 15s |
| Splotches never pop | Press `1` `2` `3` silently. (Do NOT reach for `--no-contact-gate`: `--no-arm` already implies it, so passing it changes nothing and you will think you have a second fault.) | 0s |
| Counter jumps back to 0% while you tap `1` `2` `3` | You pressed `s` too. An armed cycle's RETREAT calls `fire_reset()` ~2.5s after the scrub ends, and that resets the counter underneath you. | 0s |
| **Counter drops to 0% right after the finale — on a CLEAN run** | **Expected, not a fault.** The same `fire_reset()` fires on every armed cycle, so 100% holds for about **2.2s** (measured 2.2 / 2.4 / 2.2 over three runs) and then clears on its own. The confetti keeps falling. Say your closing line while the number is up rather than pointing at it afterwards. | 0s |
| **Rescuing a missed splotch MID-CYCLE** (the demo script's 1:22 beat) | **Supported — do it.** Press `1`/`2`/`3`; the HUD flashes `THIS CYCLE WILL RESET IT`, which is expected during a rescue and nobody in the audience reads the corner. The pop lands, the counter climbs, the finale shows 100%, and the reset arrives AFTER it — which is what you want by 1:52 anyway. The old wording here said "never both" and read as a ban on the rescue the script rehearses. | 0s |
| **Cartoon is mirroring the WRONG person** | **Only one person in frame.** The tracker does NOT switch to whoever waves — measured: it picks the same person whether the left or the right one moves. The presenter steps out of shot as the volunteer steps in. | 5s |
| Arm does nothing when a forearm is placed | **You did not press `s`.** The arm never starts by itself — that is deliberate. Press `s` on the projector; the indicator flashes `ARMED`. | 2s |
| Pressed `s`, indicator says `NO LINK` | The socket is down. Press `s` in the Python window instead. | 5s |
| Screen says **WHEELGENTIC FAILED TO START** | A vendor file is missing. Re-run `./vendor.sh`, then reload. The usual culprit is `three.core.min.js` — no HTML names it, so nothing else hints at it. | 30s |
| Screen says **AVATAR FAILED TO LOAD** (red, centred) | **A different failure from the row above, and a milder one.** The page is fine; only the character model is missing. Re-run `./vendor.sh`, then reload. **You can demo without it:** press `Enter` and you get a lit scene with the robot arm, and `1` `2` `3` still climb the counter 33/67/100 with suds and confetti. **But there is no dirt on screen** — the stand-in splotches exist only so the keys keep working, so the percentage rises against an empty arm. Say "the model didn't load, watch the counter" and carry on. | 30s |
| No sound | You skipped `Enter`. Press it. Demo survives muted — narrate over it — but you lose the cues in the next row. | 5s |
| **What you should HEAR** (so silence tells you something) | `s` → a **ding**. The scrub → a soft rubbing sound about three times a second, the whole time the sponge is moving. It is deliberately **quieter than everything else**: it is a bed, not an event. Each pop → a louder squish and a bubble sparkle **over the top of it** — that contrast is the cue. `x` → a low **thunk**. `k`/`o` → a click. The finale → the fanfare. **If the rubbing stops before the arm does, the cycle was cancelled** — check the top-right indicator. | 0s |
| Tripod bumped | `rm homography.pkl && python py/calibrate.py` — click TL,TR,BR,BL **on a book at forearm height**. It clears the test-fixture banner for you. | 90s |
| Arm scrubs 3cm off target | Same as above. Then `calibrate.verify()` with a tape. **If `SKIP_VERIFY=1` is set in your shell the tape check is skipped** — calibrate.py prints a banner saying so; `unset SKIP_VERIFY` and redo. | 90s |
| `[arm] WARNING: target … clamped 400mm` | Your homography is wrong. Recalibrate. Do not ignore a clamp this big. | 90s |
| Same warning but only **~50-60mm**, repeating | **Not a calibration failure — expected on the replay fallback.** The code warns above 50mm (`py/arm.py`); a real forearm drifting past a box edge clamps a few mm over that. Measured on `REPLAY=recordings/good_run.jsonl` with the test-fixture homography: 48% of targets land below the `xmin=120` workspace floor, clamping 51-54mm at 0.6/sec. **Keep going.** Recalibrating mid-demo costs 90s and fixes nothing. | 0s |
| UV detector finds nothing | Edit `config.json` → `"dirt_mode": "scripted"`. **Hot-reloads, no restart.** | 10s |
| Serial port gone | Replug USB. Else `REPLAY=recordings/good_run.jsonl ./run.sh` — same command as the camera-dead row above, because `run.sh` adds `--no-arm` itself on the REPLAY branch. **Do NOT reach for `./run.sh --no-arm` on its own**: that dry-runs the arm but still opens the camera, so on a machine whose camera is blocked the run dies on `CAMERA BLOCKED OR MISSING` and `run.sh` takes the page server down with it. Measured both ways. | 60s |
| Arm limp / ESP32 reset | Brownout. Use the 12V 5A brick, never USB. Reconnect, press `r`. | 15s |
| Total failure | `open recordings/backup.mp4` and narrate. It is a real recording of the real stack — arm, scrub, splotches, finale. | 30s |

---

## COLD BOOT

```bash
cd ~/Wheelgentic
./run.sh                    # everything
```

Then press **`Enter`** on the projector window to unlock audio. Do this before
judges arrive, not during.

**No venv?** `python3.12 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`

**TWO different things stop a cold boot, and the arm one comes FIRST.**

**No arm plugged in?** `./run.sh` dies at `No serial port found.` with SiLabs
driver instructions, and takes the page server down with it. This happens
BEFORE any calibration check, so on a laptop with no RoArm attached — a fresh
clone, a teammate's machine, the bench before the hardware arrives — this is
the message you get, not the homography one. Plug the arm in, or use
`./run.sh --no-arm` to get past it.

**No `homography.pkl`?** (a fresh clone, a wiped checkout, or right after
`rm homography.pkl`) — scrubbot EXITS with
`No homography.pkl. Run: python py/calibrate.py`. That is deliberate:
`calib.load()` refuses to fabricate a transform, because a made-up one puts the
sponge ~30cm off a person's forearm. **Run `python py/calibrate.py` first.**
Measured with `--no-arm` (so serial is not in the way): `[ws] listening on
ws://127.0.0.1:8765`, `[main] clean shutdown`, then the homography line — and
`run.sh` kills its own page server on the way out, so the page is only up
inside that short window. Note the asymmetry — `REPLAY=` DOES degrade
(screen-only, it says so); the live path does not.

---

## BEFORE THE DEMO — 6 checks

0. Nothing moves until you press **`s`**. One press = one cycle. `s` works
   **on the projector window** — you do not need to find the Python window.
   The link indicator flashes `ARMED` to confirm the keypress landed.
1. `df -h .` — need ≥5GB free
2. `./run.sh` starts, page loads, cartoon mirrors you
3. Press `Enter` — audio unlocked (you hear a click)
4. `python py/calibrate.py` on the **actual table**, then the tape check
5. Sponge damp, not dripping. Bucket in reach. Taped X on the table.
6. Backup video open in another window — `recordings/backup.mp4` exists and is
   committed. Re-record after any visual change: `python3 tools/record_backup.py`.
   **It should print about 10 MB.** A short file is a bad encode, not a shorter
   demo — it happened once from an unchanged tree, with the same reported length
   and the same content stamp. The recorder refuses anything under 9 MB now, so
   if it exits non-zero saying so, just run it again.
   (~45s, needs nothing but the repo). A test fails if the file goes missing.

---

## THE NUMBERS (from the team brainstorm — these are the good ones)

- Bathing is the **#1 daily activity people lose first**.
- **74.5%** of US residential care residents need help bathing.
- Bathing disability is the **main reason older people need a home aide**, and
  strongly predicts nursing home admission.
- **63 million** US family caregivers.
- **Nursing assistants are injured at 5x the industry rate** — mostly back and
  shoulder, from lifting and handling people.

That last one is the sleeper. It reframes the whole thing: this is not only
about the person being bathed, it is about the job physically destroying the
people who do it. Lead with dignity, land on the injury rate.

---

## THE THREE QUESTIONS JUDGES ASK

**"Is the dirt detection real?"**
> "The splotches in this run are scripted for reliability. UV mode is built and
> runs end to end — 365nm plus a hue and brightness threshold on the same
> fluorescent tracer hospitals use for hand-hygiene training. It finds where
> the tracer actually is, moves the cartoon's splotches there, and the
> percentage counts the spots it has actually cleaned, not a timer. Want me
> to switch it on?"

Then switch `config.json` -> `"dirt_mode": "uv"` (hot-reloads) if the shroud is
set up. **Never claim it's live if it isn't.**

The one thing still unproven on hardware: the thresholds. They were tuned on
synthetic tracer and the measured table in `py/dirt.py`; real 365nm needs
`tools/tune_dirt.py` under the actual lamp. If the detector finds nothing on
the day, that is the reason — fall back to `"scripted"` and say so.

**"How does it know where the arm is?"**
> "MediaPipe gives us 33 body keypoints from one webcam. The forearm rests on a
> table, which is a known plane, so a homography maps camera pixels straight to
> robot millimetres. No depth camera — we deleted that dependency on purpose."

**"What's the safety story?"**
> "Four layers. It never starts on its own — an operator has to arm each cycle,
> so it can't begin scrubbing someone who just reaches onto the table. Torque
> limits in the arm's firmware, so it yields to contact instead of pushing
> through. A workspace clamp plus a reachability check before every command,
> and every move is rate-limited to 240mm/s — including the recovery after an
> emergency stop. And an e-stop on two keys plus that physical switch."
> *(Point at it.)*

That last clause is worth saying: the *recovery* path is where this kind of
machine hurts someone, and ours is rate-limited like every other move.

---

## DO NOT

- Point the 365nm lamp at anyone's face. UV-A, photokeratitis. Tape it down.
- Explain a failure in engineering terms on stage. Press `SPACE`, say "that's
  the emergency stop — it's bound to a key and a torque limit on every joint."
  A visible safety cutout is a **feature**.
- Say "it should." Say what it does.
- Add features after the freeze. Rehearse instead.
