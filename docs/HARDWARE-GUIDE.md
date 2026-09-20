# What the software does, and how it works with your hardware

Written for the three of you building the physical side. No code knowledge
needed. Repo is `25tyler/Wheelgentic`.

---

## 1. What the thing actually does

A person sits down and rests one forearm on a flat surface.

A webcam on a tripod watches that person. The laptop uses the camera to work
out exactly where their forearm is, then tells the robot arm to go there. The
arm moves over the forearm, comes down, scrubs back and forth for about eight
seconds, then lifts off and returns to its starting position.

Next to all that is a projector screen. It does NOT show the camera. It shows
a cartoon character that copies the person's movements in real time. The
cartoon has three brown dirt spots on its forearm. As the real sponge scrubs,
those spots pop into soap bubbles one by one, and a big counter climbs from 0%
to 100%. At 100% confetti fires and a fanfare plays.

That is the whole demo. About two minutes.

---

## 2. What the cartoon is for

This is the part judges care about most, so it is worth being clear.

The problem with a bathing robot is obvious: nobody wants a camera pointed at
them while they are undressed. So the answer is that the camera feed never
goes anywhere. Every frame is processed on the laptop, nothing is saved to
disk, nothing is uploaded, and no human ever sees it. The only thing displayed
in the room is the cartoon.

So the cartoon is not decoration and not a joke. It IS the privacy mechanism.
The line on stage is "that's me, and that's the only thing anyone sees."

---

## 3. Who or what is being tracked

One webcam, pointed at the person being cleaned. It tracks THAT person.

The camera sees their whole body, and that single feed does two jobs at once:

- Their resting forearm tells the robot arm where to scrub.
- The rest of their body drives the cartoon, so it mirrors them.

So while the robot is scrubbing their right forearm, if they lean or wave
their free hand, the cartoon leans and waves too. Same person, same camera,
two uses.

**Only one person should be in the camera's view.** If two people are in
frame it may lock onto the wrong one, and it does not switch to whoever is
moving. So the presenter needs to step out of shot as the volunteer steps in.

It is looking for standard body points, elbow and wrist among them. It does
not need a face, a marker, or a special sleeve.

---

## 4. What you build, and the three things that matter to me

You build the frame, mount the arm to it, and set up a tripod for the webcam.
The laptop does everything else. There are only three things about your build
that the software actually depends on.

### (a) The forearm rests on a flat surface, and I need its height

This is the single most important one.

A normal webcam cannot tell how far away something is. But if the forearm is
lying on a flat surface, and I know how high that surface is, then knowing
where the arm is in the picture is enough to work out where it is in space.
Flat surface plus known height equals no depth camera needed.

Right now the software assumes the resting surface is 45mm above the table. If
your frame puts it somewhere else, that is one number in a settings file and
takes ten seconds to change. Just measure it and tell me.

### (b) Put the resting spot inside the arm's reach

The arm can reach roughly:

- 12cm to 42cm out from its base
- 26cm either side of centre
- 2.5cm to 38cm high

The software refuses to send the arm outside that box, so a bad build will not
break the arm. But it does mean the arm cannot reach the person, which means
no demo. Build with those numbers in mind.

### (c) Once we set up, the camera and the arm cannot move

After the frame is built, there is a one-time setup step. Someone puts a sheet
of A4 paper on the resting surface and clicks its four corners on screen. That
takes about 90 seconds and it teaches the software how what the camera sees
maps onto where the robot needs to go.

**If the tripod or the arm gets bumped after that, the software's understanding
is wrong and we have to redo it.** So build it so neither can shift, and do not
move them between setup and judging. If something does get knocked, tell me,
it is 90 seconds to fix and a disaster if we do not notice.

---

## 5. Things that do not matter to the software

How the arm is mounted. Nothing anywhere in the code knows or asks how the
arm is attached to anything. Bench, frame, clamp, bolts, whatever you decide.
That is purely a rules question, not a software one.

What the frame is made of. Wood, PVC, metal, no difference to me.

How it looks. Paint it, dress it, whatever.

---

## 6. Hard constraints

No water. It kills the motors. The software already assumes a dry sponge
and skips any water step, so there is nothing to change, just do not add water.

**Install the SiLabs CP210x driver on the Mac before day one.** The arm
connects by USB. macOS has no built-in driver for the chip on that board, and
the driver needs to be approved in System Settings after installing. Without
it the laptop cannot talk to the arm at all, and it will look like the arm is
broken when it is not. This is the single most likely day-one blocker.

---

## 7. How it runs, step by step

1. Someone starts it with one command. The cartoon comes up on the projector
   and the robot arm sits at its home position.
2. The volunteer sits and rests their forearm on the surface.
3. The operator presses one key to arm the cycle.
4. The arm moves over the forearm, descends, and scrubs for about eight
   seconds, pressing about 5mm into the skin.
5. As it scrubs, dirt spots on the cartoon pop into bubbles and the counter
   climbs.
6. At 100% the confetti fires. The arm lifts and returns home.
7. One key resets it for the next run.

---

## 8. Safety

Everything here is already built except the last item.

- It never starts on its own. The operator presses a key to arm every
  single cycle. There is no autostart, no timer.
- It yields instead of pushing. There are torque limits on every joint, so
  if it meets resistance it gives way.
- Everything is speed limited, including its recovery after an emergency
  stop.
- It checks every move before making it. If a target is outside the safe
  box or unreachable, it is refused rather than attempted.
- Emergency stop on a key. And if the stop command somehow does not reach
  the arm, it says so out loud and retreats to home rather than pretending it
  worked. That is a real distinction most demos cannot make, and worth saying
  to a judge.
- A physical power cutoff within reach of the operator. This one is yours
  to provide. Software cannot do it.

---

## 9. What I need from you, and when

Now: decide the frame, and tell me how high the resting surface will be.

Before day one: SiLabs CP210x driver installed and approved on the Mac.

Once the frame exists: 90 seconds of my time to click four corners. After
that, nothing moves.

Then: rehearse. Nobody has heard this demo out loud yet, and nobody has
practised what to do if something fails mid-run. Both need people, not code.

---

## 10. Two rules questions that could end the project

Neither is a software problem and neither is mine to answer.

1. Bench-mount rule. Nobody has read the actual rule text. If it means the
   arm cannot be attached to anything a person sits on, then clamping to a
   chair is exactly what it prohibits, and we would find out at judging. Good
   news: we already built for the strict answer, arm on the table and forearm
   on the same table, so a restrictive reply costs us nothing.

2. No pre-written code. This repo has weeks of commits in it. If that is
   not allowed we need to know now rather than after.

Draft messages for both are written and ready to send in
`docs/OPEN-QUESTIONS.md`. Send them separately, they probably go to different
people.
