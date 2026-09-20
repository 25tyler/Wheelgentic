# P5 — phase indicator. PREMISE CHECKED FIRST, AND IT CHANGES THE DESIGN.

## What I was going to build

A HUD line reading APPROACH / SCRUBBING / RETREAT so the audience can read the
state machine without the presenter narrating it.

## Why the naive version would be a lie

Python HAS a real FSM with exactly those names --
py/scrubbot.py:402 ("camera lost -> RETREAT"), :491 ("armed + forearm detected
-> APPROACH"), :494 (elif state == "APPROACH"), :503 (-> "SCRUB").

**But the state never leaves Python.** EVENT's full key set is
seq, mode, limb, t, scrub, contact, clean, reset, pops, place, ack -- no phase
field, and no EVENT write carries `state`.

The browser's only phase knowledge is its OWN choreography:
  main.js:436  robot.setPhase('hover')
  main.js:437  setTimeout(... setPhase('scrub'), 1600)    <- a TIMER
  main.js:450  robot.setPhase('rest')

So a HUD reading "SCRUBBING" would be reporting a 1600ms browser timer, not the
arm's state. If the arm stalled, estopped, or never left APPROACH, the projector
would confidently print SCRUBBING at a judge.

**This is worse than the P4 foam question.** Foam is a drawing of movement and
asserts nothing. A phase label IS a status assertion about the machine, on the
same screen that claims "0 FRAMES STORED" and "the counter only moves on real
contact". Do not build the timer version.

## Two honest options

(a) **Send the phase.** One line in py/scrubbot.py's FSM: set EVENT["phase"] =
    state wherever `state, t_state = ...` is assigned, and clear nothing (it is
    a level, not a one-shot -- unlike `scrub`). Then the HUD prints what the
    ARM says. Truthful and small. BUT it is py/scrubbot.py, the file with five
    historical estop bugs, which I have deliberately not touched this session.
    It is a display-only field that no control path reads, so the blast radius
    is genuinely small -- but it needs the suite green and its own plant-verify.

(b) **Label only what the browser genuinely knows.** `#link` already shows
    ARM ● LINKED / ARM ○ MANUAL from socket liveness, and `cycleLive` tracks
    whether a real Python cycle is running (set from m.scrub/m.pops, cleared on
    ws.onclose). A second line reading CYCLE RUNNING / IDLE would be true
    without any protocol change.

RECOMMENDATION: (b) now, (a) later if there is time. (b) costs one HUD element
and reuses a flag that already exists and is already correct on disconnect --
that correctness was a documented past bug fix, so it is trustworthy.

## What (b) looks like

- index.html: a `<div id="phase">` inside #hud, under #privacy.
- hud.css: same clamp() sizing family as #link; dim when idle, bright when live.
- main.js: set its text where `cycleLive` is already assigned (the ws handler
  and ws.onclose), so there is exactly ONE source of truth and no new state.

Acceptance: unplug Python mid-cycle and the line must go to IDLE, because
ws.onclose already clears cycleLive. That is the test that proves it is not a
timer.

---

# OPTION (b) AS A CONCRETE PATCH — anchors read from disk

## Why (b) is honest, named precisely

`cycleLive` is the browser's own knowledge of whether a REAL Python cycle is
running. Its population is small and closed:

    main.js:425  let cycleLive = false;          <- sole declaration
    main.js:510  cycleLive = false;              <- ws.onclose
    main.js:552  cycleLive = false;              <- link lost / arm-lost
    main.js:558  if (m.reset) { cycleLive = false; resetAll(); }
    main.js:559  if (m.scrub || m.pops.length) cycleLive = true;
    main.js:799  reader (the 1/2/3 conflict warning)
    main.js:887  reader (__wheelgentic.isCycleLive)

    LINE NUMBERS DRIFT -- these were re-read from disk AFTER the
    suds landing shifted main.js by ~13 lines. The first draft of this doc said
    424/496/538/544/545 and was already wrong by the time it was read back.
    ANCHOR ON THE TEXT, never on these numbers: find the `cycleLive = false;`
    inside ws.onclose, etc. Re-grep before patching.

**496 and 538 are what make this truthful.** If Python dies, the socket closes
and the flag clears -- so the line drops to IDLE on its own. That is the exact
property a timer-driven phase label would NOT have, and it is why option (a)
(labelling the browser's 1600ms setPhase timer) was rejected: a HUD phase label
is a status assertion about the machine, and this one degrades honestly.

## PATCH 1 — web/index.html, inside #hud (currently lines 18-22)

> **HISTORICAL. This block is how the HUD looked when this phase shipped.**
> The privacy line is now `ON-DEVICE ONLY · N SEEN · 0 STORED`, where the
> first number is counted at a real frame-consumption site. Do not paste the
> markup below back into `index.html`; it would restore a caption that says
> the same thing with the camera unplugged. See `VISUAL-OVERHAUL.md` item 2.

    <div id="hud">
      <div id="title">WHEELGENTIC</div>
      <div id="privacy">ON-DEVICE ONLY &middot; 0 FRAMES STORED</div>
      <div id="cycle">IDLE</div>
      <div id="link" class="warn">CONNECTING</div>
    </div>

## PATCH 2 — web/hud.css, matching the #privacy sizing family (hud.css:18)

    /* Same clamp() family as #privacy so it scales with the projector.
       Dim when idle, bright when a real cycle is live -- see setCycle(). */
    #cycle      { font-size:clamp(13px, 1.5vh, 28px); color:#55607a;
                  margin-top:0.9vh; letter-spacing:1px }
    #cycle.live { color:#4ec9f5 }

## PATCH 3 — web/main.js, ONE writer, called from the existing assignment sites

Add beside the other small HUD helpers:

    /** The cycle line. Driven ONLY from cycleLive's assignment sites, so there
     *  is no second source of truth and no timer. It reads IDLE whenever Python
     *  is not actually running a cycle -- including when the socket dies, which
     *  is what makes it a status rather than a guess. */
    function setCycle(live) {
      const el = document.getElementById('cycle');
      if (!el) return;
      el.textContent = live ? 'CYCLE RUNNING' : 'IDLE';
      el.className = live ? 'live' : '';
    }

Then call `setCycle(false)` immediately after each of the three
`cycleLive = false` sites and `setCycle(true)` after the `= true` site.
Four one-line additions, no new state. Locate them by grepping
`cycleLive`, not by the line numbers above.

## Acceptance (the test that proves it is not a timer)

Start a cycle, then kill Python mid-cycle. The line MUST go to IDLE, because
ws.onclose clears cycleLive at 496. If it stays on CYCLE RUNNING, it is lying.

## Verified before writing this

- No test pins the HUD's element set (grep for getElementById('privacy'),
  querySelectorAll('#hud...), hud children: zero hits), so adding a div is safe.
- #privacy's clamp() sizing at hud.css:18 is the family to match; it was tuned
  for projector legibility and re-using it avoids a fresh legibility question.
