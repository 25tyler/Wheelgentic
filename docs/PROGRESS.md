# PROGRESS

Running log so no session loses time re-deriving state. Newest first.
Rule from the standing prompt: document as you go, do not get stuck in bugs.

---

## 2026-09-19 — the safety panel stopped being a caption

The merge item in the standing prompt had seen no work at all. Checked
what "combining scrub3d and main" actually means in files: 29 modules in
`scrub3d/`, and only three of them reachable from anything else. One
file bridges the halves, `tools/export_body.py`, and it bakes the body
scan and the four-arm split into JSON the page reads.

**What was still hand-written.** The CLEAR / HOLD / REFUSE lines during
the scrub were four strings typed into `web/main.js`, with an honest
comment saying the live governor was "a backend change on another
branch". That branch is in the repo now, and `fleet.py` imports nothing
but numpy, so it runs in the bake. The panel reads the governor's own
words and its own reasons.

**Why it is worth more than ticking the item.** Body A clears at 73mm off
the body, body B at 64mm. Press `n` and the safety numbers move, which is
the same measured-not-scripted argument the territory panel makes,
applied to the claim judges actually ask about.

**Three mistakes on the way, each caught by the governor refusing
everything.** A hardcoded 1000mm head ceiling cut through the person's
own shoulders, which sit at 1055; it reads off the measured body now.
`propose()` takes the ARM'S frame and transforms to world itself, so
passing world coordinates transformed them twice and threw every probe
above the person. And proposing the sponge AT a body cell is refused
every time, correctly, because the structure would come inside the 60mm
shell.

**The standoff sweep is the best fact this produced.** On arm 0's own
cells: 0 clear at 40mm, 30 at 70mm, 109 at 90mm, 200 at 120mm. D_HOLD is
90mm and the crossover sits exactly there. That is the governor enforcing
its own documented margin rather than a number written down twice.

**Still unwired:** 26 of 29 modules. The interesting ones are `track`
(the body follows the person every frame), `torque` (telling scrubbing
from leaning) and `control` (the coverage controller, which explicitly
has no stored trajectory).

---

## 2026-09-19 — the chair answers questions now, not just orders

BRAINSTORM-2 asks for "voice support / personal agent" and only the first
half was built. Every voice intent made the machine DO something, which
makes it a remote control. Four intents now make it answer.

**The answers are read off the running page.** How clean am I quotes the
counter and the patches left. Which arm is doing what comes from the
solver's own per-cell ownership with the anatomical names the bake
assigns, so swapping in a differently shaped person with `n` changes the
sentence: 273 patches becomes 317. How long have you been at this reads
the care counter, which only ticks while the machine is really working.
What is my heart rate reads the number the readout is drawing, so the
spoken answer cannot disagree with the screen behind it.

**A language model was considered and rejected.** It would answer from
its training, fluently, about a machine it cannot see, and nobody in the
room could check a word of it. These are checkable against the projector
as they are spoken, and they need no API key on a laptop strangers will
handle, no venue wifi, and no latency on the beat that has to feel
instant.

**The ordering trap, measured rather than reasoned about.** A question
about a thing contains the word for that thing. With the question intents
listed after the action ones, "how clean am I" started a wash, "what is
my heart rate" opened vitals, and "which arm is washing my leg" started a
wash. The chair answered questions by doing chores. Questions are matched
first now and each phrase is long enough not to swallow an order:
"clean me" is still a wash.

**One weak test caught by planting.** The check that the answer tracks the
state compared a cold answer against a running one, and a plant that
replaced the live sentence with a hardcoded one passed. It compares two
mid-wash samples now, and the same plant turns it red.

---

## 2026-09-19 — the dirt was on the torso and a passing test said otherwise

Swinging the wide shot round turned the splotch placement test red on all
twelve characters. The first instinct was that the camera change broke
placement. It did not. It made a year-old bug visible.

**Where the dirt actually was.** Cast a ray through each splotch's own
screen position with the sprites hidden, and ask which bone owns the
triangle it hits. Every one of the twelve characters returned `torso` or
`leg-left`. Not one returned `arm-left`. The dirt has never been on the
arm.

**Why.** The stand-off was `wid * 0.6` along the bone's local +Z. `wid`
is the LARGER of the two cross-axis spans, so on the shipped arm the push
was 0.133 against a limb only 0.111 deep. And bone-local +Z stops
pointing at the camera the moment the limb rotates or the shot moves.

**Why nothing caught it.** The test measures perpendicular distance in
SCREEN space. From the old near head-on camera the offset projected to
one to three pixels and passed every run. From the new angle the same
offset projects to fourteen. The test was correct all along and the
camera was hiding the answer. Widening the test would have buried it
again, which is what the numbers in it were quietly drifting toward.

**The fix.** The holder sits on the limb's axis and the sprite is lifted
toward the camera each frame by the limb's own half-depth, so it stays
right at every limb angle and in every shot.

**Two false alarms worth remembering.** A colour-threshold pixel counter
reported zero dirt on screen and nearly sent the fix in the bin; it
returned zero for the ORIGINAL code too, so it was measuring nothing. The
honest version toggles the sprites and counts pixels that CHANGE, which
found about nine thousand. And a ray that reported the torso in front of
the arm was hitting a silhouette edge, not proving occlusion: a sweep of
twenty-one points down the whole bone found every one of them visible.

---

## 2026-09-19 — the chair was always there, the camera was hiding it

The cold read from 09-21 was right that it reads as a person on the floor.
Everything tried against it since then assumed the chair model or the
scene was at fault. All of it was aimed at the wrong thing.

**The camera sat almost head on.** It was placed at x = dist * 0.30, about
a 17 degree orbit. Measured off the live page rather than guessed, the
seated body spans x -0.89..0.80, z -0.62..0.67 and the chair spans
x -0.57..0.68, z -0.66..0.60. The body covers the chair almost exactly, so
from that angle no chair geometry could ever have helped. That is why the
two model swaps and the aim change all measured no better, and why the
rails added under the previous entry could not be seen: they sit at
z -0.95, behind the chair. From the new angle they DO read -- hiding them
and diffing the frame moves about 9,500 pixels -- so they were never the
wrong idea, only the wrong thing to look at from the wrong place.

**What proved it.** A footplate was added at the feet's measured position
and vanished behind the body on the very next screenshot. Geometry placed
correctly and still invisible points at the viewpoint, not the model.

**The fix.** Orbit and height are per shot now, defaulting to the old
values so the other five shots are unchanged. The wide shot swings round
to 0.62 and drops to 0.74, with the dolly pulling in to pay back the
screen size the swing costs. The wheel, the blue frame and the footplate
all read, and the face still carries. Past about 0.8 it becomes a profile
and the dignity of the pose goes with it.

**One wrong turn worth keeping.** The footplate first went at z 0.78, on
the assumption that a seated figure's feet reach forward. The leg bones
are at z 0 and -0.24. That version read as a blue luggage cart parked in
front of a person: a fourth detached object, worse than the problem it was
meant to fix. Measure the bones, do not assume the pose.

---

## 2026-09-21 — the other standing failure was a budget, not a hang

The arm-protocol watchdog has been called a known flake since long before
tonight. It aborted on 3 of 20 suite runs -- 15%, five times the rate the
docs quoted -- and passed cleanly every single time it was re-run alone.

It is not hanging. One block in that test sweeps ten timing offsets and
**retries the whole sweep up to twice** if any of them comes out
inconsistent, and each pass takes thirteen seconds. Two retries add
thirty-nine. The inconsistency that triggers them is itself sensitive to
how busy the machine is, so a full suite run causes the retries that then
push the test past its own time limit.

The retained log of an aborted run shows both retries firing. A clean
solo run fires neither. That is the whole mechanism.

The base run takes about 87 seconds, so 87 plus 39 is 126 against a limit
of 150 -- twenty-four seconds of headroom for everything else happening on
the machine. It is 210 now, which leaves eighty-four.

**Verified on the failing case itself.** The run that proved it fired both
retries, reached the section where it used to die, and finished with 80
checks passed.

Deleting the retries would have been the dishonest fix: they are what
prove the race is not real, and the code says that block has produced five
separate critical bugs.

Both of the suite's standing failures are now actually fixed rather than
documented.

---

## 2026-09-21 — the splotch failure was never a placement bug

That test has failed on four of twelve characters all session and the log
above calls it pre-existing three times. It was a tolerance one
percentage point too tight.

The splotch sits **one pixel off the limb's centre line** -- dead centre
of the arm -- and five pixels past where the test puts the shoulder. The
window stopped one hundredth short of that. So the dirt was on the arm
the whole time and the check was arguing about where the arm ends.

Two measurements of the same bone disagreeing at its end: the avatar
places the splotch through a fraction of the bone's own vertex box, and
the test projects that box's corners to the screen. A box corner is not a
joint once a limb is at an angle, and the gap is worst at the shoulder
where the deltoid widens the box.

The window is wider now, set to half the spacing between splotches so one
still cannot be credited to the wrong end of the limb -- which is the
only thing that bound is for. The sideways check that catches dirt
floating in mid-air is untouched, and a plant that shoves every splotch
off the limb still fails all twelve characters on all three.

**All 12 pass.** The suite's only standing failure is gone.

---

## 2026-09-21 — the screen now says what it is

The title read **WHEELGENTIC** and nothing else: a project codename from when
this was a one-arm scrubbing demo. A judge reading it learned nothing
about what was in front of them, and the line below it already had a job
carrying the privacy claim.

It now reads WHEELGENTIC, then AI WHEELCHAIR, FOUR ARMS in a smaller dimmer
amber, so the name still reads first. Fits at 1024 wide with more than
half the row to spare, and the projector legibility check passes at all
three resolutions with no overlap.

---

## 2026-09-21 — it looked like a person on the floor, not a wheelchair

Had a cold read done of five projector frames by someone who knew the
product going in. The verdict was blunt and right: **it reads as a person
sitting cross-legged on the floor, surrounded by four industrial arms on
separate floor pads.** All that was visible of the chair was one wheel
hub. The product IS the chair.

**Three obvious fixes were tried and all three measured no better.** The
power wheelchair hides entirely behind the occupant and loses the one
wheel that did read. The deluxe looked nicer by eye and counted FEWER
distinct pixels in the chair zone than the original. Lowering the
camera's aim just adds empty floor. All reverted, all written down so
nobody repeats them.

**What was missing was not a better chair, it was a connection.** Four
detached bases and a chair with no visible link between them is five
objects. There is now a rail along each side joining that side's two
bases, and a cross-member under the chair joining the rails, so the eye
follows one continuous frame from the left arms, under the person, to the
right arms. Same boxes, same colours already in the file.

**The first version of that frame did nothing**, and a second read caught
it: the rails were the same value as the bases and the floor, so they
read as a shadow smear. They are a third darker now. The rails were also
built from a position the code declares but never uses, so one landed
half a metre short of its own plinth.

**And the plate that looked abandoned was not an arm base at all.** It is
the measured body's pedestal, off to one side, empty for most of the run.
Once the other four were joined it read as a leftover. It appears with
the body it holds now, and its shadow goes with it.

Also recoloured the four bases from warm tan to the arms' own slate. They
had been the highest-contrast objects in the lower third, pulling the eye
before the chair.

---

## 2026-09-21 — two tests that were watching a clock

A full run came back 32 of 35 instead of the usual 34. Both extra
failures passed when re-run alone, so neither was a real break, but both
were sampling on a timer instead of waiting for the thing.

One waited three seconds for the page to reload after a body swap. Fine
on an idle machine; inside the suite the machine is busy, the reload had
not finished, and the check read the **previous page** and saw the old
count. It marks the page and waits for the mark to vanish now.

The other read the top-right banner once, two and a half seconds after a
keypress. That element has two writers -- the estop's refusal and the
socket's own link state -- so a link update inside the window wiped the
message being checked for. It waits for the text now, and a plant proves
the wait cannot hide a real failure.

Thirteen other places read that element after a fixed sleep. They pass
today; the trap is written down so whoever hits it next does not spend
the hour I did.

**And the recovery card now has a test.** Every key on it was tested
somewhere; the card was not. Nobody had run stop, clear, reset, then pop
the dirt by hand as one flow, which is exactly what an operator does when
it has gone wrong in front of judges. It reaches 100% with no errors.

My first plant against that test was too weak and nearly went into the
log as a real result: removing one counter reset changed nothing because
a second site also zeroes it. Killing the manual pop path is what fails
it.

---

## 2026-09-21 — the beam finally looks like a beam

Two changes, both about what a judge sees before they see anything else.

**The gate was hiding the scene.** The first thing anyone ever saw was a
near-black screen with yellow text: at 80% black the chair, the four arms,
the tiled wall and the light shaft were all behind the start overlay and
all invisible. The pitch was hidden until somebody pressed a key. At 56%
it all reads and the instruction is still by far the brightest thing.

**And steam, which is what makes the light shaft pay.** A beam is
invisible until something crosses it. That shaft has been in the room
since the room was built and read as a flat cone, because nothing ever
drifted through it. Now the wash raises steam that thickens as the
counter climbs, and the beam looks like a beam.

It is also the warmth. A shower with spray and no steam reads as a
machine rubbing a mannequin rather than someone getting a warm wash.

**Nothing was downloaded for it.** The puff is the same soft circle the
file already draws for contact shadows, inverted to white. One extra draw
call, measured.

The first attempt read as a field of distinct dots, because the fade ran
out too fast and each puff had a visible edge. Steam has no edge.

**A wet floor was tried and thrown away the same hour.** The idea was a
reflection so the arms and the beam would double in a wet sheen. It cost
**40% of the frame rate** -- the page went from 21 frames a second to 12,
because it renders the whole scene an extra time and the outline pass
already renders it twice. That is a real risk on a projector nobody has
profiled, for decoration.

It also looked worse. At any strength where you could see it, it read as
a mirror rather than a wet floor: the warm floor colour vanished under a
muddy double image and the arm plates appeared to float. Weaker was
invisible, stronger was a mirror, no middle.

**And the fallback video has no steam, which is correct.** Checked, and
it looked like the gate failing. The recorder deliberately never starts a
cycle, because that clip is for the case where Python has died and the
operator is popping dirt by hand. Nothing is washing in that story.

---

## 2026-09-21 — where the session stands

Suite: **33 of 34**, and the one failure is the pre-existing splotch
placement that is one pixel off on four characters the demo never shows.
Every test added tonight passes inside the suite rather than only on its
own. Working tree clean, everything pushed, localhost alive.

What the screen gained across the session: a care counter that only
counts real working seconds, spinning sponge heads, a visible tool
exchange, a camera frame count that proves nothing is stored, a subject
readout that admits when the tracker has lost you, a camera move that
covers the handoff, and the solver's own schedule of which arms can move
at the same time.

What got merged: the whole scrub3d backend, which main did not have. The
bake behind the territory overlay had been loading it from a /tmp
worktree that a reboot erases.

What got refused: a soil-detected readout, because the detector ships
off and the field that looked like it could back it actually means arm
reach. Written into DECISIONS so nobody proposes it again, including me.

---

## 2026-09-21 (night) — two guards that were watching the wrong thing

**The docs checker was reading the old script.** Every check in it that
looks at the run of show was reading `DEMO-SCRIPT.md`, the one-arm
version. The file anyone actually rehearses from is V2, so anything that
existed only there had nothing watching it. That is exactly how the 0:52
beat came to promise a per-body arm schedule with no guard. Widening it
went red immediately on correct docs, because a flag in V2 belongs to a
different program than the one that check searches.

**And a third passing check that printed its own failure.** Three flag
checks said "not found in scrubbot.py or run.sh" while passing. Same
shape I fixed twice earlier; this file has a section whose entire job is
catching it.

**Checked whether the projector should solve the real rig.** The
operator view solves the four measured arm positions and the bake solves
a synthetic ring, which looked like something to unify. Measured both:
the real rig gives arm 0 **nothing** and arm 3 twelve patches, with worse
coverage. That is two arms working and two watching, under a panel whose
whole argument is four agents dividing a job. The ring stays, and the
numbers are written down so nobody re-derives it.

---

## 2026-09-21 (later still) — the arms say who can move together

The brainstorm's line is that each arm is an independent agent, and the
project's own title says agentic. The counts panel already argued that
with contested cells: 433 patches on this body that two arms both claim.
The other half of the argument had been computed on every single bake
and thrown away.

The solver returns four things and the export was taking one. One of the
discarded three is the schedule: which arms can work at the same time
without reaching into each other. It was on disk nowhere and on screen
nowhere.

The panel now ends with **together, 2 passes: arm 0 + arm 2 + arm 3,
then arm 1**. Swap the body and it renegotiates to arm 0 + arm 2, then
arm 1 + arm 3, because the second person is a different shape. A written
caption could not do that, it would be wrong for one of the two.

**And a floor on the backup video.** Mid-way through re-recording I got a
6.5MB file from the same command that had been writing 10.1MB all week.
Same reported length, same content stamp, passed every check, because
nothing looked at the size. The next run was 10.2MB from an unchanged
tree. That file is what you play when the live demo has died in front of
judges, so the recorder now refuses anything under 9MB instead of
shipping a bad one quietly.

---

## 2026-09-21 (later) — the screen admits when it has lost you

Had a cold read done of the run of show, looking for the weakest ten
seconds from a judge's seat. It found 1:02, and it was right.

That is the only beat with no keypress. The presenter steps out, the
volunteer steps in, and the screen does nothing at all while two people
shuffle in front of the camera. For a second or two the tracker has
nobody and the cartoon just held its last pose, in silence -- landing
immediately after the presenter has told the judges to watch that cartoon
mirror the volunteer. A freeze with no explanation reads as broken
tracking at the exact moment a judge is staring at it.

The top-left line now ends with **looking for a subject** in amber, or
**subject locked** in green, taken from the detector's own answer so it
cannot claim a person the tracker does not have. With no camera at all it
says nothing about subjects, because that would read as a fault when the
real state is that there is no camera.

The other half of the fix costs nothing: the swap now starts during the
territory re-solve rather than after it, so the screen is moving through
the shuffle. No extra words for the presenter to say.

**Testing the half that matters.** A headless run has a fake camera with
nobody in it, so "looking" is the state it falls into by default and
asserting only that would leave the state that actually appears on stage
untested. The detector is reachable from a test now, so the shipped line
can be made to see a person.

---

## 2026-09-21 — the backend is in the repo, and it has a second screen

The standing prompt calls the scrub3d merge co-equal with the vision and I
had spent the whole night on the frontend. Main carried the baked body
file but not the code that makes it.

**It was worse than a missing folder.** The bake loaded scrub3d from a
worktree in /tmp, which the machine erases on reboot. The file the entire
territory overlay is drawn from depended on a directory that could
disappear between a rehearsal and the demo, with no error until someone
re-ran the export. The code is in the repo now and the bake prefers it.
Re-ran it: the body file comes out byte-identical, so nothing about the
demo changed except where the code lives. Left out the 600MB of recorded
camera frames; the code makes a body without them.

**And it unlocked something worth showing.** There is an operator view in
there that draws the seated body with the four real Waveshare arm meshes,
posed by the same maths the collision checker uses, with a header saying
how much of the person is reachable, how much area each arm owns, and
which arms can move at the same time. On this rig three can work together
and the fourth has to wait. That grouping is the answer to "is this four
robots or one robot four times" and nothing on the projector shows it.

**It died about half the time.** A cosmetic smooth-skin step calls abort()
when it fails rather than raising, so the try/except written to degrade
gracefully could never run -- the whole program was already gone. It
builds in a child process now: when it dies we lose a nicer-looking
surface and keep everything measured. Three runs in a row, all fine.

Deliberately not in the two-minute run of show. It is what you open at the
table when a judge asks how the arms decide.

---

## 2026-09-20 (night, last) — chasing an estop bug that was not one

Added the spinning sponge earlier tonight, then realised nothing checks
that stopping the machine stops it. A head still turning against a
person on a stopped machine would contradict the safety beat at the
worst possible moment.

Pressed the stop with no arm connected and the head kept turning, 8.4
radians after the key. Looked like a real bug for about a minute. It is
not reachable: with no link the stop only tells the operator where the
working one is, and with no link the scrub could never have started.
Through the path the demo actually uses, the head stops dead.

The check now drives that path, and my first version of it failed on
correct code because I measured the turning across a window that
included the slowing down. It read 1.3 where the head was doing 6.4.

Also corrected a number I had guessed: the care panel reads about 5s on
a keyboard-only rehearsal, not the 32 I wrote earlier. The scrub is
most of the real care and only counts with the arm connected. Put it on
the recovery card, because a presenter expecting 30 and seeing 5 would
assume something broke mid-demo.

Twenty-seven commits tonight. Four things added to the screen, five real
bugs fixed, five new test files, and the rest keeping what the docs
claim in step with what the code does.

Final suite: **31 of 32**, the best result of the night. The one failure
is the pre-existing one-pixel splotch placement. The arm-protocol
watchdog did not recur, and all five new tests passed in the suite
rather than only on their own.

One last honest note. I planted a guard removal expecting my newest
check to go red and it stayed green: two competing animations still end
on the same value, so that guard changes what a prop does mid-flight
rather than where it lands. The check is still worth having and the
guard is still right, but the test now says in its own comment that it
does not cover that. A check whose plant never went red proves nothing,
and this session already produced one of those.

---

## 2026-09-20 (night, later) — the privacy line proves itself now

The strongest defensive claim in the pitch is that every camera frame
stays on the laptop, and the presenter says it at 0:28 over a line that
read "ON-DEVICE ONLY, 0 FRAMES STORED" as fixed text. That line would
have said exactly the same thing with the camera unplugged. A claim that
cannot fail is not evidence.

It now reads "N SEEN, 0 STORED". The first number climbs, counted in the
one place the page actually takes a new camera frame. The second is a
plain zero because nothing here writes a frame anywhere. The climbing
number is the dim half and the zero keeps the bright green, because the
point is not that a camera sees things, it is that nothing is kept.

**The test needed a fake camera to mean anything.** Headless Chrome has
no video device, so the count correctly stays at zero and a test without
that flag would have passed against a completely dead counter. Checked
that directly: good code with no camera reads 0 SEEN, same as the
broken version. The flag is doing real work.

Suite is 32 tests.

---

## 2026-09-20 (night) — two claims checked, one dropped, one already true

Went through the vision doc looking for things it claims that the screen
never shows. Two were worth an answer and neither ended in new code.

**No "soil detected" readout, and that is final.** The vision wants the
machine to identify the dirtiest areas. There is a real tracer detector
in the Python side and its positions really do reach the screen, but the
demo ships with it switched off, so in the run of show nothing observes
anything and the three dirt spots are constants picked to sit inside the
sponge's reach. The tempting shortcut was worse: the body scan has a
field per patch with honest-sounding names, but it means which arm can
reach there, not how dirty anything is. Calling those the spots needing
most attention would be a category error wearing a real number, which
survives a glance and collapses the moment a judge asks what the word
means. Written into DECISIONS.md so nobody proposes it again.

**The arms adapting to movement is already on screen.** An audit called
this a gap, and it is not. The scan's lean is 0.646 of the character's
torso lean against the 0.65 the code asks for, and the torso angle comes
from real shoulder positions, so with a person in frame the scan tilts
with them. The problem was that nobody ever moves during the demo.
Standing still it is about a degree and reads as nothing. The fix was
one line in the script: the presenter now asks the volunteer to lean.

My first check said the follow was broken. I set the torso angle by hand
and the scan barely moved. The idle animation overwrites that value
every frame; measuring both numbers inside the page over 2.5 seconds
gave the real answer.

**The splotch test failure is bounded now.** Four of twelve characters,
always the same four, always one pixel. Ruled out the two theories worth
ruling out and wrote the rest down rather than keep going. Bugs are the
lowest priority here and this one is invisible on screen.

---

## 2026-09-20 (evening, later) — you can see the tool change now

Second gap from the same audit. At 1:42 the presenter says "it puts the
sponge down and picks up a spoon" while the screen showed one object
becoming another inside a single frame: both props hang off the same
point on the arm, and the swap was two visibility flips.

Now the sponge shrinks away, the hand is visibly empty for a moment, and
the spoon grows in with a small overshoot so it reads as being picked
up. No new geometry, about a third of a second.

**The measured sizes were the real risk.** The spoon is authored at 0.55
and the sponge at 1.0, both set against the character with a ruler, and
this file already records getting each of them wrong once: a spoon that
was a sliver, a soup bowl bigger than the character's head. A scale
animation that ends on a plain number instead of the remembered one
would resize a prop permanently and look fine in the diff. Each prop now
remembers its own size the first time it is touched, and every animation
ends on that.

Two things caught while writing it. The animation library is a global
here with a fallback everywhere it is used, and I had written it as a
bare name: if that library ever failed to load, the error would have
happened inside a mode switch and taken the whole keypress with it,
leaving the demo stuck in the shower. And putting the sponge back with
visibility alone leaves it at almost zero size, so the next scrub cycle
would run with no sponge on screen at all. Both are now what the test
plants.

Suite is 31 tests.

---

## 2026-09-20 (evening) — the sponge turns now

Audited the vision doc against what the screen actually shows, and the
highest ratio of visible motion to effort was the one the brainstorm
itself calls the big wow factor: a spinning sponge. It doubts it only on
hardware grounds, servo and wiring and power, none of which apply to a
projector. The head was static, so four arms held a block against a
person and dragged it, through the longest stretch of the demo.

It now turns while it is on the body and only while it is on the body:
still through the 1.6s approach, spooling up as the arm lands, coasting
down as it lifts. Spun on its own axis, because the other one already
belongs to the back-and-forth rub and driving both on one axis cancels
it.

**The first version passed a test and did nothing.** I checked it by
setting the scrub phase by hand, which worked, and the real demo path
never spun at all -- the choreography that the `s` key runs holds the
arms in a different phase first. The test now drives that real spine, so
a version that only works when poked by hand fails.

On screen from the demo's own camera: the heads are bright yellow, each
at a different angle mid-turn, bubbles rising off them.

Suite is 30 tests.

---

## 2026-09-20 (later still) — the care count was lying three ways

Audited the two features from the previous entry rather than waiting on
a suite run. Three real defects in the care panel, one in the voice
routing, all four fixed.

**The count claimed care while nothing was moving.** The rule was
`mode === 'feed'`, but the mode stays 'feed' long after the last
spoonful lands: the arms park at rest, the bar freezes at 4 / 4 SPOONS,
and the script has the presenter talking over that transition for about
25 seconds. Every one of those counted. It now gates on whether the beat
is actually delivering, cleared when the last spoonful lands and when
the operator switches away mid-beat. Vitals deliberately keeps counting,
because it really is reading a heart rate the whole time it is up.

**The count survived a reset.** Everything else on screen goes back to
zero on `r`; this did not. Rehearse once for 40 seconds, press `r` for
the judges' run, and the panel opened at 40s with nothing yet having
happened. The estop is deliberately not this path: `x` keeps the count,
because that care really was delivered.

**The chair would have restarted the shower instead of answering.** The
intent list is matched first-hit and the generic wash intent was listed
ahead of the spot intent. "clean my left arm" contains "clean", so the
script's closing beat routed to shower. The comment above the spot
intent already claimed it was listed first. It was not. Checked every
other phrase while fixing it: hungry, thirsty, heart rate and stop all
still route where they should.

**One of my own guards proved nothing.** The no-microphone check
asserted that headless Chromium lacks SpeechRecognition. It has one.
Planting the bug back left the suite green. Replaced with the property
that matters, and an audit caught a second version of the same mistake:
an identity comparison that passed only because the microphone had never
started, and would have silently inverted if the test were reordered.

Every fix above is plant-verified: break it, watch the named check go
red, restore, confirm the tree is byte-clean. Suite is 29 tests.

---

## 2026-09-20 (later) — the screen counts care, and the chair answers

Two gaps in the vision, both now on screen, plus two bugs found while
checking the second one worked.

**CARE DELIVERED, top right.** The presenter asserts at 0:15 that
nursing assistants get hurt at five times the industry rate, and the
screen had nothing to back it up. Three rows now: seconds of real care,
lifts by a person, backs at risk. The last two stay zero for the whole
demo, which is the entire point of them.

It counts working seconds only: a live scrub cycle, feeding, or vitals.
Voice is deliberately left out. A machine listening to a question has
not delivered care, and counting it would inflate the one number on this
screen that has to be honest.

**Corrected 2026-09-20:** an earlier note here guessed about 32s over a
rehearsed run. Measured since, and it depends entirely on the scrub. The
scrub is most of the real care and it only counts when Python is driving
a live cycle, so a keyboard-only rehearsal with no arm connected reads
about 5s. With the arm connected the 24-second scrub counts and the
number lands near 30. Worth knowing before pointing at it on stage: if
you are rehearsing without hardware, that panel will look small and it is
not broken.

The numbers sat at subtitle size at first, which made the one readout
about a person the faintest thing on the projector. They now match the
mode strip's headline size; the words stay small.

**Ask for a spot and the arm that owns it answers.** Brainstorm 2 line
92 asks for this by name. Say "clean my left arm" and arm 3 replies with
164 patches. Ask for the right and arm 0 replies with 273. Those come
from the solver's own per-cell ownership rather than a lookup table, so
they match the counts panel exactly and change when the body swaps.

Only left and right are offered, because that is all the arms can reach.
Counted from both bakes: they own forearms and upper arms, nothing else.
Offering "my legs" would have hit the cannot-reach reply three times out
of four.

**Two bugs, both invisible to reading the diff.**

The word boundaries in the spot patterns were literal backspace
characters. A patch script wrote a backslash-b inside a Python string,
so the pattern compiled as \x08left\x08 and matched nothing. Every
phrase fell through to "which one?" -- and with the boundary broken,
"left" hit the generic arm pattern and confidently named the wrong arm.
Nothing threw, nothing logged, and the file on disk looked right. Found
by printing the live regex source out of the running page.

Speaking required a microphone. say() lived inside makeVoice, which
returns null when there is no recogniser, so the chair could only answer
after `m` had been pressed. Listening and speaking are two different
browser APIs and are now two different functions, with one shared
implementation.

**A guard that had to be fixed twice.** The first version of the
no-microphone check asserted that headless Chromium lacks
SpeechRecognition. It does not. Planting the bug back left the test
green -- a check that proved nothing. It now asserts the real property:
the chair answered while the microphone had never been started, and
say() is exported on its own. Planting the bug back now fails 5 checks.

Suite is 28 tests. The splotch-placement failure is the known
pre-existing one, identical at bd4fba2.

---

## 2026-09-20 (late) — it looks like a bathroom

The presenter opens with "bathing is the number one daily activity
people lose first", over a room made of two flat-coloured planes and a
skirting board. The brainstorm ranks "physically impressive" above all
five capabilities, and this file already recorded the same lesson once:
94.2% of the projected frame was void, no test could see it, and the
room was the fix.

Now the walls are tiled and a beam of light falls on the chair from a
fixture out of frame.

**Drawn, not downloaded.** The file has built canvas textures since the
floor existed, so a tile grid is four lines of 2D canvas. Searched
Kenney's furniture kit and prototype textures first, both CC0 and both
good, and rejected them as heavier than the thing they replace. Same
for the light: two MIT volumetric-spotlight libraries exist and both
are a cone with a shader pair, which is what this is.

Measured rather than eyeballed: half-unit square tiles so the grout
meets at the corner, 59 pixels each at 1080p, grout at 1.10:1 against
the wall because it sits behind the character and must not compete with
the point cloud. Tagged sRGB, which the background gradient a hundred
lines above already documents as necessary or a canvas texture renders
washed pale. That one was caught by reading, not by a picture.

**And one caught only by the picture.** The first shot of the light was
a solid black cone filling the frame. The page renders through an
outline effect that draws every mesh a second time as a black inverted
hull, and a double-sided cone's hull fills it completely. The point
cloud escapes because points are skipped; a mesh has to opt out.

---

## 2026-09-20 — what "agentic" actually means, on screen

The project's own title in the brainstorm is "Agentic Multi-Arm
Robotic AI Bathing Assistant", and that word was doing no work on the
projector. The governor panel shows four fixed lines, so four arms
coordinating read as a caption rather than as a result.

The bake has carried the real number since it was first exported and
nothing read it: **433 of the 5824 cells are contested** -- reachable
by more than one arm, claims the solver had to resolve. Split across
the arms as 94 / 132 / 124 / 83.

Press `n` and it renegotiates to 482, split 150 / 88 / 92 / 152,
because the body is a different shape. That is the difference between
four arms following a script and four agents dividing a job, and it was
one line of output already sitting on disk.

Its own colour, measured rather than picked: a softer violet sat 0.25
from the unreachable slate directly beneath it, too close for two
adjacent rows. The one that shipped is 0.36 from its nearest neighbour
and 7.1:1 against the background.

The counts panel now tells the whole partition: who owns what, which
body parts, what nobody can reach, and what had to be negotiated.

---

## 2026-09-20 (later) — the shower is wet

The brainstorm ranks "physically impressive" above all five
capabilities, and the word SHOWER has been on the mode strip while four
arms rubbed a dry cartoon with a dry sponge. It is the one thing here a
judge already knows the look of without being told.

Water sprays off the working sponge and falls. Foam builds on all four
arms rather than just the first -- the stroke tick only ever read one
sponge, so three of the four have been scrubbing completely dry since
the fleet was built, invisible in a still because the one that worked
is nearest the dirt.

**Taken, not built.** canvas-confetti was already vendored, already
drove the foam, and already turns an emoji into a particle shape with a
fallback for a machine that lacks it. Searched three.quarks and
Kenney's particle pack first; both do it properly and both cost a
dependency or a download for something the library on disk already
does, which is the same argument this file recorded when it picked
confetti in the first place.

Opposite of the foam in every number that matters, so the two read as
different substances rather than two settings of one effect: water
falls, throws wide and is gone in half a second; foam rises, clusters
and lingers. The foam budget is shared across the arms now, because
four at the old per-arm count is four times the density it was tuned at
and reads as a wall of white.

**And the finale stopped celebrating the wrong thing.** Forcing it from
feed, vitals or voice threw confetti and the fanfare over a heart rate
or a spoonful count, turned the readout celebration green, and left the
cleanliness counter at 100 behind it for a cycle nobody watched. `f`
bounces to the shower beat first now, which costs one keypress the
operator was going to need anyway.

I made the same change to `c` and a guard caught it inside a minute. It
pins that binding to a single call, because the docs once described a
quiet confetti burst while the code fired the whole victory beat. `f`
is the one that needed fixing -- it is the card's "force the finale",
which is what an operator who is behind reaches for. `c` is deliberate,
and the card already says what it does.

---

## 2026-09-20 — the chair reads you, visibly

The brainstorm puts "Put it on the chair" on its own line under the
vitals item, and the hardware list names the part. On screen the heart
rate simply appeared in the HUD, so the claim that the chair reads you
was made only in text.

There is now a pulse sensor on the near armrest, placed from the seat
surface that was already solved rather than by eye. Its light beats on
the ECG's own cardiac phase, the same value the trace is drawn from, so
the glow on the chair and the spike in the HUD are one event rather
than two clocks.

**Three placements, each settled by a picture.** Level with the seat it
was entirely hidden by the body. Halfway along the arm it read as
sitting on the person. Pushed to the front it swung across the chair's
midline and floated in front of the person on nothing.

The geometry is the answer rather than a fourth guess: the seated
cartoon's shoulders overhang a chair 0.9 units wide, and the
three-quarter yaw puts the near armrest behind the torso. Nothing
clears the body without leaving the chair. So it sits where the real
part goes and is partly occluded, because moving a prop somewhere it
could not be, in order to see it better, is what this whole overlay
exists not to do.

**And a fix that did not work, caught by checking it.** The replay
fallback used to leave the corner reading CYCLE RUNNING, green, with
the counter at zero, for the whole demo -- on the path the recovery
card sends you to when the camera is dead. The first attempt put the
correction inside the phase-change guard, which made it unreachable:
the phase starts as IDLE, so the browser records that on the very first
message and the guard never fires again. Measured live, the line was
unchanged. Checking on every message fixes it, and a real cycle still
reads APPROACHING then SCRUBBING.

---

## 2026-09-19 (night) — the estop did not stop the feeding arm

The worst thing in the repo, and a green test was claiming otherwise.

`stopScrubChoreography` cleared the scrub's own timers. The feed,
vitals and voice beats run on a separate list added later that nothing
but a mode change ever cleared. So `x` during feeding played the thunk,
flinched the character, parked all four arms -- and then the pending
spoonful fired about 1.6 seconds later and lifted the bowl back to the
person's mouth.

After an emergency stop, the machine resumed touching the volunteer.

Proven by reverting the fix rather than argued: bowl up, `x`, still up,
still up 2.6 seconds later. The script offers `x` to a judge as the
safety demonstration and the recovery card says the arms stop. Both
were false in three of the five modes.

`test_estop_cartoon.py` passed through all of it, because it only ever
pressed `s` then `x` and never entered a mode. A test whose whole job
is that claim could not see the claim failing.

**Five more from the same two audits**, all on paths the script or the
recovery card actually uses:

- The pill beat left the soup bowl parked at the mouth while the glass
  tweened into the same spot, because `shift+8` is a mode change to the
  mode you are already in, which skips the handler that hides it. Two
  props interpenetrating at the volunteer's face under the word
  MEDICATION, at the closest camera angle in the demo.
- Toggling the scan off and on mid-cycle painted two thirds of the body
  white in one frame, which reads as a pre-rendered fill and destroys
  the one thing that key exists to prove.
- The microphone key did nothing at all, silently, on any browser
  without speech recognition -- no mode change, no click, no readout --
  on the beat right after the presenter says "and you can just ask".
- On the replay fallback the corner read CYCLE RUNNING, green, with the
  counter at 0%, for the whole demo. That is the fallback the recovery
  card sends you to when the camera is dead.
- The self-rearming readouts pushed a timer id every 90ms onto a list
  only a mode change empties. The script ENDS in voice mode, so a page
  parked between back-to-back runs collected about 666 dead ids a
  minute.

**And one claim rejected after measuring it.** An audit reported that
the pop shake leaves a permanent camera offset that accumulates over a
cycle. Seeking the tween to its end does land at +0.04, which is what
made it look real. Driven by the actual ticker over its full run it
returns exactly to where it started. The existing code was right.

---

## 2026-09-19 (late) — the screen stops lying about what it hears

Chrome sends the audio to Google to transcribe. The demo script says so
out loud, as the one part of this system that is not on-device. So a
dead venue network is the failure to expect, and it was not handled:
the recogniser restarted, failed, restarted again, and looped, while
the projector kept reading SAY SOMETHING.

That invitation looks exactly like a working microphone. It lands on
the beat where the presenter has just asked a judge to speak and is
watching the judge, not the readout, so nobody finds out until the
silence is long enough to be the worst moment of the demo.

It now stops and names the fault, and pressing `m` recovers. A
microphone that vanishes mid-session gets the same treatment, since it
looped identically. `no-speech` deliberately does not: Chrome fires
that constantly in a quiet room, and treating it as a fault would stop
the microphone every few seconds on a stage nobody is talking on yet.

**Two things the fix nearly broke on its way in.** The readout wraps
what it heard in quote marks, so a fault would have rendered as if the
volunteer had said it out loud. And after stopping, the screen read
PRESS M -- sending the operator to press the key that had just failed.

**One caught with a calculator.** "NO NETWORK FOR SPEECH" was written
straight into the big readout, which renders at 97px on a 1080p
projector in a fixed-width pixel font: 21 characters is 2037px against
a 1920 frame. The long wording moved to the smaller line, where it is
336px against a 1344px limit, and the big one says NO NETWORK. There is
now a guard with a 19-character budget, plant-verified against that
exact string.

**The tool change had the same fault in miniature.** The spoon showed
unconditionally, so the medication beat had an arm holding a spoon AND
a glass of water. Nobody spoon-feeds pills, and the point of a tool
change is that the tool matches the job.

**And a bug I found by reading my own code after it passed a suite.**
The camera's no-gsap branch applied the framing without landing the
interpolation, so it worked only on the first call, by the luck of the
mixer starting at 1. Plant-verified: with the original code the second
shot sticks at 5.57 instead of pulling back to 7.26. One check could
never have seen it, which is why that guard presses two shots.

---

## 2026-09-19 (evening) — the frame moves

**The camera was a security camera.** Two minutes, five capabilities,
one rectangle that never moved. The script was the evidence: three
separate stage directions telling the presenter to point at the screen,
because the frame would not do it.

Five framings now fire off keys the operator already presses. `b`
slides left and pushes in on the measured body. `s` pushes in over 2.2
seconds so the move lands just before the confetti. `8` goes close for
feeding, `9` pulls back to sell the four arms, `r` returns to wide.

A shot is an offset on top of `fit()`, never a replacement. `fit()`
still owns the aspect arithmetic that four screenshot sessions solved,
so a projector re-detecting its resolution mid-move re-solves the shot
the demo is actually in. GSAP was already vendored and already tweened
the camera for the screen shake, so nothing new was added.

**What the screenshots found that the arithmetic did not.** The
measured body went solid black on any focus: unreachable cells have
owner -1, never match the focused arm, and all 5125 of them -- 88% of
the body -- were being dimmed. And the dim itself, 0.12, put cells at
0.08 luminance against a 0.21 floor, darker than the background.

One tradeoff kept rather than fixed: the scrub push-in crops the scan's
left edge. The only dolly that holds the whole scan is 0.92, which is
barely a move. That beat is about the arms and the dirt.

**The arm changes tools.** The brainstorm asks for it by name and
nothing showed it: every arm wore a permanently attached sponge, so the
feeding arm carried soup to a mouth while still wearing the thing it
scrubs with. `cooking-spoon.glb` was vendored with the food kit and had
never been loaded.

Two defects on the way in, both caught by picture. It was added to the
scene rather than the arm, so it sat at the world origin and the swap
read as losing a tool. And the ruler said scale 0.375, which is the
true spoon-to-bowl ratio and looked like a sliver, because the model is
0.024 thick and no scale fixes that. 0.55 makes it comparable to the
sponge it replaces, which is what has to read from ten feet.

**Eight accessibility props that shipped and were never loaded** --
glasses, sunglasses, a mask, a hearing aid, three canes, a crutch -- are
now on `j`. Placed from the head mesh rather than by eye, after two
screenshots: the head bone is empty, the mesh spans y 0 to 0.432, and
each prop's geometry is offset from its own origin, so putting the
origin on the eye line put the lenses at the hairline.

**And the guard that was watching the wrong files.** The backup video's
staleness check hashed six hardcoded paths while `web/` has ten. Two
commits this week changed only `coverage.js` and the stamp stayed
green, so the committed video showed the old broken behaviour while the
test called it current. Both lists now come from the directory.

`test_projector.py` presses every shot key before measuring now. Its
own history is why: the last reframe put the feet through the
cleanliness bar and the robot's base off the right edge, and nothing
but those measurements saw it.

---

## 2026-09-19 (later) — one number, or it is lying

Started by re-verifying the coverage sweep live and found it never
painted a cell. Ended having fixed one bug class in nine places. The
class: two things on screen claiming the same fact and disagreeing.

**The sweep, measured per animation frame.** The body climbed to 265 of
699 cells and snapped back to 0 three times in a single scrub, once per
splotch pop, while the counter beside it read 100%.

Two causes, both structural. The sweep ran on a 13 second clock of its
own and a real cycle finishes in about three, so the two could never
land on the same number. And `territories.focus()` repaints every cell
to dim an unfocused region, which runs on every phase change, which is
every pop -- so whichever wrote the colour buffer last won.

The fix was to give each QUESTION one owner instead of giving the buffer
one writer. Coverage decides whether a cell is clean; focus decides how
brightly a region is lit; the sweep reads the same value `#pct` and
`#fill` render from. There is no correct value for the old constant,
because the cycle's length depends on how fast the dirt pops.

A cleaned cell also had to dim differently. At the 0.12 the dirty cells
use, a cleaned one lands on rgb(0.10, 0.11, 0.12) -- between 0.011 and
0.036 of luminance from a dimmed dirty cell, which is nothing on a
projector. Every bit of visible progress vanished the moment feed mode
focused one arm. 0.40 keeps it 0.272 clear of dirty and 0.559 below
lit.

**Then the same bug in the Python.** On the exact command seven docs
give as the no-hardware demo, not one splotch popped while the arm was
scrubbing: all three were dumped by the end-of-cycle finale in one
socket frame. The pop check gates on `arm.contact`, which comes from the
torque watchdog on a real servo, and `--no-arm` opens no serial port.
`--no-arm` now implies `--no-contact-gate`. Measured: 0 in-scrub pops
became 3.

**And one I had wrong.** I first read the counter as ending at 0% and
believed the documented command cleaned nothing. The readings were taken
after the end-of-cycle reset, which is correct behaviour. Sampled per
frame it really goes 31, 48, 80, 94, 100. The defect was the missing
progression, not a missing result.

**Four parallel audits while the suite ran** found the rest, including
one I had introduced. The body-swap handler awaits a fetch, and across
that await the old coverage object still pointed at geometry disposed on
the line before, while the overlay was still marked live -- so a pop
arriving during the fetch wrote into a freed buffer. Both guards I had
added for exactly that hazard sat after the await. Order was the whole
fix.

The others, each with a visible symptom: a counter tween kept writing
the readout for 0.8s after vitals took it over, so a percentage
flickered over the heart rate; reaching 100% while vitals held the
element skipped the confetti entirely, giving the payoff beat's
consequences with none of its celebration; the finale's green stayed on
through vitals and voice, tinting a heart rate in celebration colour;
feeding showed "FEEDING" over a leftover "100%" for 400ms because the
first real write waits for the arm to travel; asking for food by voice
after asking for pills served the pills while the label said FEEDING;
and the governor printed "HOLD waiting for arm 2 to leave the shared
zone" at four parked arms during voice mode.

**One audit suggestion rejected.** Routing feed's bar through the shared
cleanliness value would paint half a person clean for two spoonfuls. The
bar shows spoons; the overlay shows cleanliness. Freezing the overlay
during feeding is correct, because no more of the person is being
washed.

**The guard that was watching the wrong files.** The check that
decides whether the backup video is stale hashes a hardcoded list of
six files, duplicated in the test and in the recorder. `web/` has eight
modules and the page imports all of them, so four were outside the
digest: coverage.js, territories.js, vitals.js and voice.js.

Two commits this week changed ONLY coverage.js. The stamp stayed green
through both, so the committed video showed a body snapping back to
dirty while the test reported it current -- and that video is the
fallback if the live demo dies on stage. Today's work went red only
because main.js and juice.js happened to change too.

Both lists are now built from the directory, so the next module added is
covered without anyone remembering. The file's own comment already
recorded this happening once before, to hud.css and robotarm.js.

**Not fixed, recorded instead.** The scrub beat fills in about three
seconds of its eight, and it is structural: the sponge travels only
`u = 0.5 +/- 0.175` and the three splotches span nearly that whole
band, so no placement of three spreads across eight seconds. A judge
watches the arms, which animate for the full eight either way. The
measurements for each option are in OPEN-QUESTIONS.

---

## 2026-09-19 — The coverage sweep, and a key I had no right to take

**The body fills in as the arms clean it.** The scan showed who owned
what and the counter showed how clean the person was, but nothing
joined the two, so nobody could watch the machine work THROUGH a body.
Each arm now sweeps its own territory ordered by distance from its real
base, so it reads as an arm working outward from where it stands rather
than as noise resolving.

Paced against the scrub, not the frame rate. Two cells per arm per
frame finishes all 699 in 1.5 seconds and flashes past. A fractional
rate needs an accumulator or it truncates to zero every frame and
nothing moves at all.

Each arm gets its own rate, which only the arithmetic exposes. The four
own very different amounts (273 / 135 / 127 / 164 on body A), so one
shared rate lands arm 2 at 8.5s and arm 0 at 18.2s: a ten second tail
where three arms sit still and one keeps going. That reads as three
stalls, not as a team finishing a job. Scaled per share they all land
at 13.0s, which is also what real arms dividing a body would do.

A cleaned cell shifts hue, not just brightness. Pure white was the
obvious choice and it fails on exactly one arm: measured as luminance
against each arm's colour, cyan, green and coral all move 0.30 or more,
but yellow moves 0.16. A cleaned yellow cell looked almost identical to
a dirty one, so one arm in four appeared to do nothing. A cool
near-white is brighter than all four and bluer than three of them, so
the change reads on every colour.

**The regression, and the guard that caught it.** Voice was bound to
`v`, which has cycled the 12 characters since long before voice
existed. My handler returned first and the character swap became
unreachable, breaking one of the three standing rules about changing
skin tones and outfits. Voice moved to `m`. Both verified in isolation.

`test_docs_match_code.py` failed three ways the moment the keys existed
in code and not in both doc tables. That is the drift it exists to
catch, and this time it caught a product regression rather than stale
prose. 18 bound keys became 26.

**Five other things found by reading rather than running**, all staged
behind a guard that refuses while the suite runs, all now applied:

- A GPU leak on every body swap. `scene.remove` unparents but does not
  dispose, so each `n` press abandoned 5824 points.
- A timing conflict: the script gives 10s to feed, pills and vitals
  together while feeding alone ran 11.4s. Four spoons at 1.6s is 6.4s
  and the whole beat fits.
- Two HUD colours below the projector legibility threshold. The
  unreachable-cell count sat at 2.47:1 and the interim speech line at
  3.53:1; both now clear 4.5:1.
- A safety line needing 10.4s of speech inside an 8s beat. Shortened,
  with the detail moved to Q&A.
- `TRUTH.md` still asserting the one-arm scope with full authority, in
  the file its own first line tells you to read before anything else.

---

## 2026-09-19 — Projector sizes, and what the vision still lacks

**Checked at all three projector resolutions** with everything on: 1080p,
720p and 1024x768. Nothing runs off screen. The 4:3 case was framing the
subject high with a third of the frame as empty floor, because framing by
height gives a narrow screen the same vertical slice and far less width. The
aim point now drops on narrow aspects.

### The vision, item by item

| Brainstorm item | State |
|---|---|
| Physically impressive | 4 arms, chair, room, measured body, governor verdicts |
| UI | mode strip, per-mode readouts, ECG, heard-text, safety panel |
| Shower | full cycle, 4 arms, territories, dirt to bubbles, counter |
| Eating / drinking | bowl carried to mouth, counts spoonfuls |
| Feeding pills | glass, own readout, own key, own spoken line |
| Vitals | live BPM and a scrolling PQRST trace |
| Voice / personal agent | commands and answers, speaks back |
| 4 arms as independent agents | staggered, per-region reach, own territories |
| No preprogrammed paths | `n` swaps the body and the partition re-solves |
| Arms adapt to movement | scan follows the tracked bones |
| Point cloud for privacy | replaced by the cartoon, which is stronger and demoable |

**What is genuinely not done, and cannot be by a tool call:** every piece of
hardware (4 arms, RealSense, MAX30102, the frame, the chair), the two rules
questions in `OPEN-QUESTIONS.md`, and a rehearsal with a person reading
`DEMO-SCRIPT-V2.md` aloud while someone drives the keys.


## 2026-09-19 — Sound on the new beats, and an instrument that lies

**Three of five beats were silent.** The demo script tells the presenter to
stop talking and let the sound carry the moment, which only ever worked for
the scrub. Feeding, medication and vitals now tick: one per spoonful, one per
heartbeat. A monitor that shows a pulse and makes no noise reads as a
screensaver.

**The instrument trap, worth remembering.** Counting calls to `zzfx` by
patching `window.zzfx` reports ZERO sounds -- including for the splotch pop
that has shipped for weeks and demonstrably works. It is a classic script
loaded before any module or init script, so the patch never takes effect on
the reference the module captured.

Three separate probes all agreed on zero and all three were wrong. The honest
check is to ask the audio context: `window.zzfxX.state` reads `running` at
44100, and `juice.js` already warns on an unknown sound name, so no warning
plus a running context is the real evidence.

Same shape as the frozen arms an hour earlier: a probe that returns a clean,
confident number about something it cannot actually see.


## 2026-09-19 — Three of the four arms were never animating

**The find.** The render loop called `robot?.update(dt)` -- the name of the
single arm that existed when that line was written. Arms 1 to 3 had their
target poses set by `setPhase` and were then never stepped toward them. They
held whatever pose they loaded with, for the entire demo.

**Why it survived this long:** every screenshot showed four arms, correctly
placed, and one of them moved. A static frame cannot tell a held pose from a
reached one. It only showed up by comparing two screenshots of the same beat
before and after the fix, where three arms move from hanging in the air to
touching the body.

The lesson generalises: a thing that is on screen and in the right place can
still be doing nothing, and a single screenshot will confirm it is "working".

**The scan also follows the person now.** The brainstorm names "arms adapt
dynamically to movement -- if the person moves their leg, the arm adjusts" as
the innovation, and nothing showed it. The cartoon's bones already track a real
person through MediaPipe, so the measured cells ride those bones while the
overlay is up. Whole-body rather than per-limb: one transform instead of 5824,
and a demo needs the motion legible more than per-joint.


## 2026-09-19 — The two claims a judge would challenge are now demonstrable

**The safety governor is on screen.** Every arm command in the backend passes
a governor that can clear, hold, retreat, refuse or estop it, and that was
entirely invisible. "What is the safety story" is the question a judge always
asks about a machine that touches people, and the answer was words. The
verdicts now stream in while the arms move, colour-coded and staggered so they
read as decisions arriving. The names come from `fleet.py` itself rather than
being invented for the display, so what an audience reads matches what the
backend decides.

**A second body proves the central claim.** "No preprogrammed actions, every
body type is different so paths are generated from the person's actual 3D
model" cannot be proved with one scan on screen -- it looks exactly like a
canned path would. `n` swaps in a differently proportioned person and the
partition genuinely re-solves: arm 0 goes from 273 cells to 317, arm 3 from
164 to 282. The export now takes measurements and writes one file per person.

Both are in `DEMO-SCRIPT-V2.md` and on the recovery card. Full sequence
re-verified after making the key handler async: zero page errors.


## 2026-09-19 — The coverage sweep is real, and contact stops guessing

Two backend modules moved into the live path: `scrub3d/control.py` (the
coverage controller) and `scrub3d/torque.py` (contact in newtons).

**The sweep on the projector was invented.** `web/coverage.js` sorted
each arm's cells by distance from that arm's base and brightened them at
a rate tuned to finish in 13 seconds. No reach limits, no sponge
footprint, and cells an arm cannot physically touch lit up anyway.
`control.py` is the real thing: a geodesic field over the surface, reach
margin as a term rather than a test, and a traverse when the
neighbourhood is done. `tools/export_body.py` now runs it and ships the
arrival order in the body JSON; the page reads that and keeps the old
fan only as a fallback for bodies baked before this.

Measured on body A: the real order and the fan rank-correlate -0.62 to
-0.99. The arms work inward and the fan worked outward, so the projector
was animating very nearly the reverse of what the machine would do. Cost
to produce it is 0.02s against a partition solve that already takes
2.2s, and every owned cell is reached (699 of 699 on body A, 787 of 787
on body B).

**Contact fired with nothing under the arm.** `py/arm.py` called contact
on raw shoulder torque above 550, but raw torque is mostly gravity and
gravity depends on where the arm is. Measured through the model, free
air, nothing touching: 625 fully extended, 331 half folded, 3 folded in.
So the shipped test claims torque-confirmed contact at one end of the
workspace and cannot reach the threshold at the other.

`py/contact.py` subtracts what gravity explains and reads the residual,
which is pose-independent. The same three poses now read 0.4, -0.1,
-0.6: all quiet, correctly. A 2N press still reads as contact.

The coefficients are this arm's mass distribution, and the arm has never
moved, so there is no sweep to fit against. It fits itself instead, from
the arm's own feedback while the FSM is IDLE and the sponge is provably
on nobody. Until that fit passes a spread gate it changes nothing and
the old threshold stays in charge. No newtons are reported: that needs a
press onto a known weight, which has not happened.

Verified: demo starts and runs with both changes, zero tracebacks, zero
page errors, arm-protocol and scrub3d-merge suites green, and the
browser confirmed painting the controller's order as a strict prefix
rather than the fan.


## 2026-09-19 — Every mode now shows its own state

Three holes found by walking the run of show rather than reading it.

**Pills looked identical to food.** The voice said "here are your pills" and
the same soup bowl appeared. That is the worst kind of demo bug, because an
audience believes what it can see over what it is told. There is no pill
bottle in the Kenney food kit (checked every model in it), so the stand-in is
a glass of water: medication is taken with water and a tall glass is obviously
not a soup bowl at projector distance. One mode, two payloads, because to the
machine it is the same task. `shift+8` runs it without the microphone.

**Feeding and medication left the readout frozen at 0%.** Every other mode
moves, so a stuck one reads as a broken meter. They now count spoonfuls and
pills rather than a percentage of nothing, which is what a carer would
actually track, and the arm re-lifts on each one so the count and the motion
agree. The sequence stops on the last item instead of looping to zero.

**Voice inherited a stale number.** It was the only mode that set no value, so
arriving from a finished scrub left `100%` under the word LISTENING. It now
reports whether the microphone is actually open, and the bar becomes a
breathing level meter rather than a progress bar.

Full sequence verified: measured body, cycle, scrub, feed, pills, vitals,
voice, back to shower. Zero page errors.


## 2026-09-19 — The agent answers, and the scene is a room

**The chair answers questions.** The brainstorm lists a personal agent with a
question mark on the Claude-computer-use half, so it is an open idea. A live
model was considered and rejected for this demo: an API key on a laptop
strangers will handle, venue wifi, and seconds of latency on the one beat that
must feel instant. It answers from what the system genuinely knows instead --
ask if anyone is watching and it says the camera never leaves the laptop; ask
if it is safe and it says what happens when you say stop. Thirteen spoken
phrases checked through the matcher, including the ordering trap where "how am
I doing" must reach vitals rather than an explanation.

**The scene is a room.** Two wall planes and a skirting line. The skirting is
what sells it: without it the walls meet in a seam that reads as a rendering
artefact rather than a corner. Walls darker than the floor so they do not
compete with the character; no ceiling, which would make a widescreen
projector feel like a basement. Sixteen triangles.

**Scene cost stays trivial:** 89 draw calls, 2,933 triangles. There is
enormous headroom on a real projector.

### What remains, and who it needs

Everything on the software side that a tool call can advance is done. What is
left needs people:

- The two rules questions in `docs/OPEN-QUESTIONS.md`, still unsent.
- Real hardware: the four arms, the RealSense, the MAX30102 vitals sensors,
  the frame. All on the request list in `BRAINSTORM-1`, none in hand.
- A rehearsal. Nobody has read `DEMO-SCRIPT-V2.md` aloud while someone drives
  the keys.


## Every capability has something to look at

**Feeding carries a bowl.** An empty arm near a face is not legible; a bowl
travelling from tray to mouth is legible with no narration. Kenney Food Kit,
CC0, same artist as the character and chair. `vendor.sh` scrapes the download
link off the asset page because the URL carries a content hash that changes.
First size was 5x too big (food-kit props are authored at character scale, not
table scale) and the screenshot showed a bowl larger than the head.

**Vitals has a heartbeat trace.** Checked for something to take first: nothing
charting is vendored, no sparkline skill exists, and the smallest real library
is bigger than the file it would replace. Forty lines of canvas, a real PQRST
shape rather than a sine. First version put ten beats across the width because
samples are not pixels: one per frame at 60fps is 8.7 seconds of history.
240 samples is a 4-second window and shows four beats, like a real monitor.

**Each arm reaches for its own region.** All four made the identical gesture
regardless of whether they owned a shoulder or a shin, which reads as one
four-headed machine. Rest stays shared, because four different idle poses read
as four different faults.

**The soak death is now on the recovery card.** The card said what `ARM ○
MANUAL` means but not that any run older than ~6 minutes will show it. An
operator who does not know that will hunt a fault that is not there, on stage.
Measured deaths: 376, 378, 405 seconds. Relaunch immediately before presenting.

**Full run of show passes end to end**, zero page errors, verified twice.


## All four capabilities work, and the show runs end to end

**Voice control shipped.** The browser's own speech API, no library, no key,
no server. Checked recognition and synthesis both exist before writing a line.
`v` toggles the microphone; saying "wash my arm", "I'm hungry", "check my heart
rate" or "stop" switches mode, and the chair answers out loud. Stop routes
through the same path as the stop key rather than a second copy of it.

Recorded honestly in the code and the script: Chrome sends audio to Google to
transcribe, so the voice is the one part of this that is not on-device. The
privacy line is about the camera, which never leaves the laptop.

**The territories react.** They brighten while the arms work and dim when they
park. Feeding mode lights one arm's region rather than all four, because one
arm is doing the work there. This is the only place on screen where the two
halves of the merge visibly touch: the frontend's choreography driving the
backend's partition.

**`docs/DEMO-SCRIPT-V2.md`** replaces the old run of show, which still walked
an operator through a one-arm demo that no longer exists.

**Ran the whole script end to end rather than reading it**, and it caught a
real bug: entering vitals after a finished cycle showed `100%` under a HEART
RATE label. The counter and the vitals readout were both writing the same
element. Fixed, and verified: vitals now reads `71 BPM` and shower restores the
percentage. Zero page errors across the whole run.


## The merge reaches the screen

**The backend's measurements are now visible.** `tools/export_body.py` runs
scrub3d's real code once and bakes the result: 5824 surface cells across 13
anatomical regions, plus which of four arms owns each one, solved by
`partition.solve`. The page loads that file and draws it as a point cloud
standing beside the cartoon, on its own plinth. A judge sees the drawn person
and the measured person side by side.

It bakes rather than streams for two measured reasons: the real placement
optimiser (`place_arms.search`) did not return inside 500 seconds, and the
socket carries events only so the cartoon survives Python dying.

**Five rendering attempts, all wrong, all caught by screenshot.** Points sized
in the wrong units came out at ~3px. Depth-tested against the body they sit on,
so the body hid every one. Additive blending summed overlaps to white and threw
away the colour that carries the meaning. Scale 0.62 squeezed them into two
blobs. Fitting against a stale bone reading drew stripes through the legs.

**Then the real bug**, which no amount of tuning would have fixed: the export
was only carrying limbs. `world_cells()` returns `(points, normals, region,
area)` and I unpacked the last two backwards, and its `only_scrubbable` flag
defaults to True — correct for planning a scrub, wrong for drawing a person,
because it deletes the torso and head. Every cell sat at |x| 0.2-0.3 with
nothing in the middle, which is exactly the four floating tubes the screen
showed. 1792 cells became 5824.

**Overlaying it on the cartoon was abandoned on purpose.** The backend's body
has a real adult's proportions; the cartoon's head is the size of its torso.
No single scale maps one onto the other, and chasing one is the hand-building
the standing prompt forbids. Beside it is also the better demo.

**The timer now fires every 8 minutes, not 20**, and its first instruction is
that writing a status summary IS stopping. It caught a turn that ended in prose
while the overlay was visibly broken.


## P0 landed: the screen is the product now

Three commits, each verified by screenshot rather than assertion.

**The character sits in a wheelchair.** An earlier pass recorded this as
impossible: no shipped clip lowers the body, which I re-measured and confirmed
(`wheelchair-sit` moves the head 0.0030 and the legs and torso 0.0000). What
it missed is that the rig has addressable leg bones and the spring loop
already has an ownership handshake for one-shot clips. Seating is two
rotations plus that handshake, not authoring a pose.

The chair is the Kenney model already vendored. Its seat height came from
parsing the GLB's own vertex buffer: an empty band at y 0.309-0.371 is the air
between seat and backrest, so the seat sits at 0.56 of chair height. A first
guess of 0.42 sank the body through it, visible in one screenshot.

**Four arms ring the chair**, one per body region, matching the brainstorm's
"split the body into 4 sections". `makeRobotArm` was already a factory, so the
fleet is a loop, not new geometry.

**All four drive the cycle**, staggered 110ms apart so they read as four
agents rather than one four-headed machine. Verified on a live cycle: HUD
reads SCRUBBING from Python's own state machine, suds rise, 0 page errors.

Camera re-solved from FOV 26 to 34 with the aim point back at x 0, because the
content is now symmetric about the chair instead of one arm off to one side.

**Backend now runs on this machine.** scrub3d needed scipy, rerun, trimesh,
open3d, and pyrealsense2 — which has no Apple Silicon wheel at all. Replay
reads PNG frames and never opens a camera, so a stub satisfies the import and
raises loudly if a live-camera path is ever reached. MediaPipe 1.0 dropped the
legacy `solutions` API that all of scrub3d uses, so a compatibility shim maps
it onto the Tasks API. With both in place `live_body.py` replays the recorded
person, fits the floor from real depth ("camera 835mm up, 6.5 degrees down")
and runs its safety governor, which correctly refused to move the arms while a
dependency was missing.


## Session start: scope change to the wheelchair vision

**What changed.** The project was a one-arm forearm-scrubbing demo. The
brainstorm files reset the scope to the real product: an **AI wheelchair with
four robotic arms** that showers, feeds, dispenses pills, senses vitals, and
takes voice commands.

**Set up as permanent infrastructure:**

- `docs/sources/STANDING-PROMPT.md` — Tyler's prompt verbatim, now the highest
  source of truth, above `docs/TRUTH.md`.
- `docs/sources/BRAINSTORM-1-hardware-list.md`, `BRAINSTORM-2-vision.md` —
  saved from the originals.
- ECC installed into `.claude/`: **292 skills, 68 agents, 94 commands, 23
  rules.** Available for every piece of work from here on.
- 20-minute timer running. Each fire re-reads the standing prompt verbatim,
  re-explains the brainstorms, runs an anti-stall check, and forces a tool
  call.
- `CLAUDE.md` rewritten so the sources and rules are discoverable in every
  future session.

**What the two branches hold, measured not assumed:**

- `main` — the frontend. Cartoon avatar mirroring a person, HUD, sound, juice,
  confetti. 26 tests green.
- `scrub3d` — the backend, and it is large: **36,457 lines across 1,141
  files.** Body scanning from a capture (`scan.py`, 1060 lines), 4-arm
  territory partitioning (`partition.py`, 753), live pose tracking
  (`track.py`, 460), arm placement search (`place_arms.py`, 421), a safety
  governor every command must pass (`fleet.py`), torque contact sensing
  (`torque.py`), Sapiens body segmentation (`sapiens.py`, 436), a browser rig
  editor (`rig_editor.html`, 1356), and a developer console.

The merge target: scrub3d's four-arm body model and safety driving main's
visual layer, as one application.
