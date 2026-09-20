Progress + what's in the repo

Short version: the whole software side runs end to end right now, with
no hardware plugged in. `CAM=fake ./run.sh --no-arm` gives you the full
two-minute demo on screen. 26 automated checks pass. It's on GitHub at
25tyler/Wheelgentic.

What I built against the brainstorm, and where I deviated:

One arm, not four. The software scales fine (each arm is just another
object) but four means four USB ports, four calibrations, four power
budgets, and arm-to-arm collision avoidance over a person, which is
new work nobody has started. One arm working beats four half-working. We can
still say "the architecture is per-arm, this is one of four" and it's
true.

Webcam, not the RealSense. Every depth camera we can get either needs a
source rebuild with sudo, or CUDA, or is discontinued on Mac. So: one
webcam, MediaPipe finds the forearm, and because the arm rests on a
table (a known flat plane) we map camera pixels straight to robot
millimetres. No depth needed. If a RealSense does show up it only
improves things, nothing has to change.

Privacy, solved differently than we discussed. Instead of point clouds,
the projector shows a goofy low-poly cartoon that mirrors the person
live. Camera frames never leave the laptop and nothing is stored. So
the honest claim on stage is: all processing is on-device, and the only
thing anyone in the room ever sees is the cartoon. The avatar isn't a
gimmick, it IS the privacy answer, and it's much more demo-able than a
point cloud.

Touching, not hovering. It presses 5mm into the skin plane, and the
counter only moves on real torque feedback from the joints. Hovering
kills that, the counter becomes a timer, and "it traced her arm" is a
weaker claim than "it scrubbed her arm". This is one config line if we
change our minds, but it changes the pitch, so we should decide before
we rehearse.

Dry sponge. Water gets nowhere near the motors.

What's on screen: cartoon mirrors the person, three dirt spots on its
forearm, they pop into soap bubbles as the real sponge cleans, a
cleanliness counter climbs 0 to 100%, confetti and a fanfare at the
end. There's a live status line showing what the robot is actually
doing (approaching, scrubbing, returning) pulled from the real state
machine, not a fake timer. Sound on every beat.

Safety, since a judge will ask: it never starts on its own, an operator
arms every single cycle. Torque limits so it yields instead of pushing.
Every move rate-limited. Emergency stop on a key plus a physical
switch, and if the stop command can't reach the arm it says so out loud
and retreats instead of pretending it worked. Most demos can't make
that last distinction.

Repo layout: `py/` is the robot and vision, `web/` is the projector
page, `tests/` is the regression suite, `docs/` is everything else.
Start with `docs/DEMO-SCRIPT.md`, that's the two-minute run of show
beat by beat with who says what. `docs/RECOVERY-CARD.md` is meant to be
printed and taped to the laptop, it's the what-to-do-when-it-breaks
page. `docs/TRUTH.md` is what the project is and what's out of scope.

Two things I need from you, both rules questions, both can end the
project and neither is a coding problem:

One, the bench-mount rule. Nobody has read the actual rule text.
Clamping to the chair is exactly what it might prohibit and we'd find
out at judging. I've drafted the message to ask an organiser in writing
and it's in `docs/OPEN-QUESTIONS.md`. Worth noting we already built for
the conservative answer, arm on the table and forearm on the same
table, so a restrictive reply costs us nothing.

Two, no-pre-written-code. This repo has weeks of commits. If that's not
allowed we need to know now, not after. That message is drafted too.
Send them separately, they probably go to different people.

Still owed on my side: nobody has heard the demo out loud, and nobody
has practised the recovery card under pressure. Both need a person, not
another commit.
