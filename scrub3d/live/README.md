# scrub3d/live

New here? [HANDOFF.md](HANDOFF.md) is the tour: the rules on this branch,
how to run the view, the recordings, the rig editor, what changed on
 and what is still wrong.

The person in the chair, live from the RealSense D455, with a body model
measured while it runs, and the arms scrubbing that model. Shown in Rerun.
The arms are simulated unless `--drive` is given; then the real arms follow
them (see [ARMS.md](ARMS.md)).

## Run it

```bash
python scrub3d/live/live_body.py --seat-mm 420     # camera, model, arms, Rerun
python scrub3d/live/live_body.py --no-arms         # the person only
python scrub3d/live/stop_live.py                   # stop one running in the background
python scrub3d/live/rig_editor.py                  # place the arms: http://127.0.0.1:8078
python scrub3d/live/arm_hw.py                      # the real arms plugged in; nothing moves
python scrub3d/live/live_body.py --seat-mm 420 --drive   # the real arms follow the view
python scrub3d/live/arm_hw.py --report LOG         # what the real arms did in that run
```

`--seat-mm` is the chair's seat height if you measured it. Without it the
seat is taken from where the torso ends. Add `--upside-down` when the camera
is mounted upside down. The arms stand where `live_rig.json` puts them (see
below), and a running view moves them when that file is saved again;
`--project-rig` uses `scrub3d/config.json` instead.

Two Anvil OpenYAMs through dimOS instead of the RoArms: `--drive dimos`,
with `SCRUB3D_ARM=openyam` set. The runbook is [DIMOS.md](DIMOS.md).

Work offline from a recording, with no camera:

```bash
python scrub3d/live/live_body.py --dump rec --dump-delay 15   # record ~25 s
python scrub3d/live/live_body.py --replay rec --seat-mm 420   # play it back
python scrub3d/live/live_body.py --replay rec --once --no-viewer --diag out
python scrub3d/live/arms_check.py rec                         # the arms, no viewer
python scrub3d/live/search_rig.py rec                         # search arm mounts
```

`--dump-delay` shows a countdown in the Measurements panel first, so the
person can get ready: seated, facing the camera. The body model is built for
a person facing it; side on, the seat and the body come out wrong. A
recording is lossless PNG frames plus their times, about 130 to 270 MB for
200 frames, kept the right way up. **It is pictures of a person.**
`scrub3d/data/` is ignored by git; keep recordings there or outside the repo.

One recording is in the repository on purpose,
[`scrub3d/data/live_rec_20260916`](../data/live_rec_20260916/). Use it as
`rec` in the commands above, with `--seat-mm 420`. The numbers
below were measured on a newer recording kept only on the machine it was
made on.

`--diag out` prints, every few frames, how far each model part stands from the
live surface in mm, and saves a side and top view (`side_*.png`) and the model
drawn over the camera image (`over_*.jpg`) into `out`.

## The Rerun layout

- **top left:** the camera's live surface of the person, with the model's
  skeleton, the scrub patches and the arms
- **bottom left:** the same from the person's right side
- **top right:** the model, the chair and the arms
- **bottom right:** what the arms are doing, then the measurements, beside
  the camera's own picture. Until the floor is found the panel says what
  the view is waiting for: how often the camera finds the person, and how
  the floor search is going. Nothing else is drawn until then.

## What happens each frame (`live_body.py`)

1. MediaPipe Pose finds the joints and the person's outline. With
   `--upside-down` every frame is turned 180 degrees first. A camera the
   wrong way round shows up as a floor tilted upward and a seat below it.
2. The floor is fitted from the depth and defines the world: +Z up, +X toward
   the camera, origin on the floor under the person. The search finds the
   surface with the most points, takes it out and looks again, four times,
   and the floor is the flat one (fitting to 12 mm or better) furthest below
   the camera: with the camera near level, a bed, a desk or a lap often has
   more points than the floor. The first floor counts once two fits on
   separate frames agree. It is fitted again only when two fits in a row say
   the camera moved by 3 degrees or 45 mm.
3. Lengths and widths are measured where the frame shows them clearly, each a
   running median blended from a typical adult while samples are few. Bone
   lengths count only when the bone is seen side on.
4. The model is posed from the joints themselves. MediaPipe gives each joint
   in the image, the depth gives its distance, and for the elbow and wrist a
   small search picks the depths that best fit both bone lengths and the
   depth along the whole limb. The torso is fitted to its own front surface,
   the head to the face.
5. The chair is placed once, from the first seconds of sitting, under the
   upper back: the torso's axis at shoulder height, taken straight down with
   MediaPipe's lean (at most 15 degrees). Its backrest sits just behind the
   back. The chest's front is no guide here: seated, it slopes back from the
   belly (17 degrees on the recording, where MediaPipe says upright), and
   following it put the chair 15 cm too far forward. The model's torso still
   follows that front, so the sponge meets the real surface; the model can
   look reclined, sitting forward on the seat.

## The arms (`arms_live.py`)

- **How close.** The simulated arms run closer than the project's defaults
  (`collide.py`: 6 cm from a person, 9 cm between arms): their moving
  structure keeps 5 mm from the person, the limb being scrubbed included;
  the sponge is pressed 5 mm into the skin (the tool point works 35 mm out,
  where the 40 mm sponge ball meets the surface); arms hold 4.5 cm apart and
  the fleet stops at 5 mm. The settings are at the top of `arms_live.py`.
- **Planning.** When the chair is placed, `partition.solve` plans who scrubs
  what on the posed model, in a thread. Then every cell is checked for every
  arm at the pose the person is in (`reach_matrix`): a cell stays with its
  planned arm while that arm can reach it, and otherwise goes to one that
  can (`assign_now`), cells the planner left out included.
- **All at once.** Every arm with work scrubs at the same time; the governor
  keeps them apart move by move (`TAKE_TURNS = True` brings back turns).
  Two working arms that hold each other for 0.3 s do not both wait: the one
  lower in priority steps back and does a stroke clear of the other first
  (`GIVE_WAY`). An arm starting a new stroke takes one whose points are all
  16 cm from the other arms, if it has one.
- **The scrub.** Each arm scrubs its patch in rows 65 mm apart, laid out in
  the body part's own frame: lengthwise on a limb, back and forth across the
  chest. A pass goes over every patch once (`SWEEPS`, and with more of them
  a pass is along the rows, then across them, then along again halfway
  between); a pass counts as done when every reachable patch has had its
  sweeps, and the next pass starts three seconds later. An arm whose own
  patches are done while another arm is still working goes over its patches
  again meanwhile (`AGAIN_WHILE_OTHERS_WORK`) instead of waiting.
  Along each row the sponge's place moves on at
  30 cm/s and the sponge rubs side to side around it, 12 mm each way one
  and a half times a second (what the real arms' servos can follow); the
  rub does not slow the stroke, and the rubbed band is 104 mm wide. The turn to the next row stays on the skin; the sponge lifts
  (40 mm) only over a hole (two or more points it may not touch now) or to
  go to another patch or sweep. Patches under 10 cm² are left alone, and
  the later sweeps leave out one-point strokes. A plan is a list of cells,
  so it moves with the person.
- **Every move is proposed first** to `fleet.FleetGovernor`. It holds an arm
  that would come within the hold distance of another, refuses a pose whose
  structure would come inside the clearance or put the sponge above the
  head, and stops everything inside the stop distance. Moving away from the
  person or another arm is always allowed. A refused point is skipped and
  counted.
- **Structure means what a move can shift** (`collide.MOVING`): the upper arm
  from 6 cm past the shoulder, the forearm and the jaw. The base column and
  the shoulder stay where the arm is mounted, so no move changes how close
  they are; the rig editor warns about a base close to the person instead.
- **Cells unsafe right now** (the arm could not hover above them, or reach
  them, without its structure passing through the person or too near the limb
  it scrubs) are re-checked every 3 s and left alone.
- **Skin no arm can reach yet** still gets an owner, an arm whose joints
  reach it (`give_rest`), drawn in that arm's colour, faded. When the person
  moves so that it can (an arm lifted off the side of the torso, say), the
  re-check lets that arm scrub it. An arm only waits at the end of a round
  for skin it could reach earlier in the pass. Counting the sponge's whole
  footprint, the arms scrub 29% more skin a pass this way over the
  `arms_anywhere` placements.
- **A moving person.** Near a limb moving faster than 90 mm/s (over half a
  second), an arm waits off the skin instead of chasing it. A parked or
  waiting arm the person comes too close to backs away. A parked arm starts
  65 mm from the person, facing the person if it safely can (drawn in or
  lowered first, turned aside only when nothing else is safe).
- **Moving.** Long legs through the air turn the joints (1.4 rad/s); short
  ones fly straight (35 cm/s). A joint may turn at most 0.06 rad per checked
  step, and a frame longer than 45 ms is moved in up to four checked steps,
  so the arms keep their speed when the view runs slowly: a pass takes the
  same time at 5 frames a second as at 16.
- **A way round.** A flight the governor refuses both ways (joints turning,
  and a straight line) is flown along a path searched in joint space
  (`joint_path`): poses 0.1 rad apart, each 15 mm from the person (5 mm
  near either end), checked in batches, a few hundred milliseconds at most.
  A fold home that is refused gets the same, once per return.
- **Going home.** Straight off the skin, then the joints fold to the parking
  pose, by whichever joint step still closes on it without going over the
  head. An arm held on the way for 1.5 s parks somewhere nearer instead.
- **Arms that cannot wait apart.** When two parked arms would be inside the
  hold distance of each other, one stays still for the run: the one whose
  stillness costs less, counting what the other could then no longer reach.
  The other arm leaves alone whatever would bring it within 5.5 cm of the
  still one, and an arm held by one that is not moving backs off it and
  does a stroke clear of it.
- An arm that has scrubbed nothing new for 15 s goes home. When every arm is
  done the arms pause and start another pass.
- **The panel** says which arms are scrubbing together, how much of what the
  arms can reach this pass has been scrubbed (grey skin is out of their
  reach), and what each arm is doing. Holds and skipped points are part of
  working safely and are not shown as errors.
- **The start pose.** Each arm starts, waits and finishes at the same reach
  and height, its base joint turned `start_deg` from straight ahead (see the
  rig below). When that pose is not clear of the person and the other arms,
  it waits at the nearest turn that is, and the rig editor says so.

### Keeping clear of the person when the model is wrong

Everything above is checked against the body model, which is fitted to the
camera and can be off. So, on top (`arms_live.py`, THE DEPTH GUARD):

- **No arm closes on the person fast.** Within 25 mm of the person the
  sponge closes on them at no more than 8 cm/s and the rest of an arm at no
  more than 12 cm/s; further out, no faster than an arm could brake from in
  time (a braking curve). The distance is to the model's surface, or to what
  the depth camera measured, whichever is nearer. So a model up to 25 mm
  off still meets the person at those speeds, and one up to about 5 cm off
  at under 25 cm/s, the speed industrial standards call reduced. Off the
  skin but within 6 cm of the person, the sponge also moves no faster than
  20 cm/s however it moves, and the fastest point of the rest of the arm no
  faster than 25 cm/s, so a model that is off cannot turn a flight past the
  skin into a fast graze.
- **The sponge presses 8 mm in at most**, measured to the model or to the
  camera's own surface, whichever is nearer. Where clothes stand in front of
  the model, the point the sponge was sent to is inside them: the sponge
  then goes along the surface as deep as it may (`MAX_LIFT_OUT_MM`) and
  counts as arrived, rather than stopping short and giving the patch up.
- **The camera's own surfaces are an obstacle** (`depth_guard.py`). Each
  frame the measured depth becomes points; each arm, with its sponge, is
  drawn where it is and whatever the camera saw at or behind it is taken
  out, as are the floor and the stands. Points inside the person mask (the
  segmentation, a second detector) or within 6 cm of the model count as the
  person, and no move may take a part of an arm deeper into them. Part by
  part, not by whichever part of the arm is nearest: an arm mounted where
  the camera sees the person, as one between the thighs is, has a link
  against them wherever it goes, and judged by the nearest part it could
  never move at all.
- **Nor may a link go behind what the camera measured.** For each link, how
  far it is behind the surface the camera measured along its own ray says
  whether it is inside that surface, which the distance to the measured
  points cannot: deep inside a thigh, the nearest measured point is far
  away again. A link may be 10 mm behind it, no more, and the sponge is left
  out of this (the skin it scrubs can face away from the camera). Where an
  arm waits is held to the same rule with the arms left in the depth, so an
  arm never waits in space that only the model says is free.
- **A part the camera disagrees with is not touched.** Each scrubbed part's
  cells that face the camera are compared with the depth measured there. A
  part whose median is more than 35 mm off (30 mm to be trusted again), or
  with more than 15% of its cells where the camera sees nothing near, stops
  all scrubbing (a fit that is off for one part is seldom right for the
  rest), and an arm on the skin lifts off and goes back to where it waits.
  The pass is not over: it goes on where it stopped once they agree again,
  and no new pass starts meanwhile. The limit is 35 mm because clothes put
  the measured surface 15 to 28 mm in front of the model's torso, and up to
  35 in front of an upper arm, while the person sits as they are modelled.
- **Where an arm waits is clear of the person the camera sees**, not only of
  the model, so a model that is off cannot park an arm against them.
- **The person out of view** for half a second: an arm on the skin lifts off
  slowly, and all hold.
- **Real arms** are checked the same way where their encoders put them; a
  real arm more than 15 mm behind its plan is waited for, so it never cuts a
  corner the checks did not see. See ARMS.md for what the driver adds on the
  boards themselves.

`safety_check.py` is the test (below).

## These five tools need a recording, and this checkout has none

`rig_editor.py`, `rig_sim.py`, `search_rig.py`, `arms_anywhere.py` and
`arms_check.py` all replay an RGB-D recording: a folder of `<name>_c.png` /
`<name>_d.png` frames plus `intr.json`, written by
`python scrub3d/live/live_body.py --dump`, which needs a depth camera.

`HANDOFF.md` says one recording is kept in the repository on purpose. It is
not: `.gitignore` line 28 excludes `scrub3d/data/live_rec_*/`, and the
directory does not exist on either machine. So all five run, parse their
arguments and then stop with an explanation rather than a stack trace. To use
them, record first:

```
python scrub3d/live/live_body.py --dump rec --dump-delay 15   # ~25 s, depth camera
python scrub3d/live/rig_editor.py                             # http://127.0.0.1:8078
python scrub3d/live/rig_sim.py scrub3d/data/live_rec_<date>
python scrub3d/live/arms_check.py scrub3d/data/live_rec_<date>
```

Depth works on the Linux box (`ssh wg`), not on the Mac, where UVCAssistant
blocks it and the camera is colour only.

## The rig (`live_rig.json`)

Mounts are stored relative to the seat point (x toward the camera, y the
person's left, z above the floor, and the way each base faces), so the rig
lands around the chair wherever it is. A rig has one to eight arms.
`start_deg` turns an arm's start pose from straight ahead, up to 88 degrees
either way (positive to the arm's left, 0 when left out). The seat point is
where the measured torso meets the seat height, so how far the person leans
moves it: between the two recordings of it moved 12 cm with
respect to the hips, which is why a rig wants room to spare around the
person (the search below reports the worst clearance of both recordings). A base need not be
level: `tilt_deg` tips it about its own y after it is turned
(positive leans it toward where it faces), then `roll_deg` about its own x;
both are 0 when left out. `live_rig_tilted.json` is the hand-placed rig with
only its bases tipped (searched): 47% of the upper body instead of 43%. Try
it with `--rig scrub3d/live/live_rig_tilted.json`.

Rigs in here, all with the arms facing as they were placed by hand:

| file | where the three bases are | reachable |
|---|---|---|
| `live_rig.json` | as placed by hand: red low in front at the crotch (43 cm in front of the seat point, 40 cm up), blue and green 19 and 25 cm in front, 44 cm to either side, 55 cm up | 1,060 to 1,290 cm2 |
| `live_rig_table.json` | searched: all three at table height (red 52 cm in front at 72 cm, blue 34 cm in front and 44 cm left at 72 cm, green 40 cm in front and 44 cm right at 64 cm) | 1,630 to 1,750 cm2 |

Red low in front, at the crotch, is where the person wants it. Its base is
then between the thighs, so a link of it is against them wherever it goes
and the camera sees the person where its own structure is; the rules above
are what let it work there at all (before them it scrubbed 0 to 33% of its
patches, now 88 to 92%). Two things stay worse there than at table height:
it reaches about a third less skin, and where it waits is chosen from the
model, which the camera cannot check behind the arm itself, so a model 6 cm
out can leave it waiting against the person (two of the 38 safety runs,
with nothing moving into them).

### Placing the arms (`rig_editor.py`)

```bash
python scrub3d/live/rig_editor.py                      # http://127.0.0.1:8078
python scrub3d/live/rig_editor.py --rig other.json --recording rec --port 8079
```

A page with the seated person from a recording (the newest in
`scrub3d/data` unless given), the chair, the camera and the arms, in 3D.
Drag to look around (right-drag pans, the wheel zooms; Camera view, Top and
Side jump to those views). Click an arm to pick it, then:

- **Move** (T): drag its arrows or planes to move the base.
- **Turn** (R): drag the ring to turn it about the vertical.
- **Tilt** (Y): drag its rings to tip the base any way, as on a wall or a
  slope; the free ring turns it about any axis.
- **Start pose** (G): drag the ring to turn which way the arm points when it
  starts, waits and finishes (the white arrow), or type it in the "start °"
  field, or press Z and C to turn it 15 degrees (Shift: 45). The arm is
  drawn where it will really wait; if that is not the turn asked for, the
  check says why.

Hold Shift to snap to 50 mm or 15 degrees (5 mm and 1 degree otherwise). The
numbers can be typed too, and the keys on the page move the picked arm. The
skin is painted by the arm that scrubs it and grey where no arm can reach;
the picked arm shows its reach. **Flat views** adds the old top and side
views, which also work without the internet (three.js comes from a CDN).
Every change is checked within a second: how much of the upper body the
arms can scrub, what each arm gets, how much they hide from the camera,
bases too close together or to the person, and arms with nowhere safe to
wait. A base inside the chair or the person, or two bases on top of each
other, cannot be saved.

**Simulate** runs the arms' real motion three times through the recording
with that placement (about 15 s) and says whether anything went wrong.
**Save** writes `live_rig.json` and keeps the one it replaced as
`live_rig.previous.json`; a running live view moves the arms and plans again
within a second. The page is served on 127.0.0.1 only and never talks to an
arm.

### Searching for mounts (`search_rig.py`)

`scrub3d/config.json` was searched for a scan with the arms held out. On a
person sitting at rest it reaches about 21% of the upper body and leaves two
arms nearly idle. The rig committed as `live_rig.json` was searched with the
project's own `place_arms.search` on a recording of a person sitting at
rest, with mounts kept 400 mm apart and at 650 to 1050 mm, and a penalty
unless every arm can park at least 110 mm from where the others work.

### Testing a rig (`rig_sim.py`, `arms_anywhere.py`, `safety_check.py`)

```bash
python scrub3d/live/rig_sim.py rec [RIG.json] [--loops 2]
python scrub3d/live/arms_anywhere.py rec --rigs 60 --seed 11
python scrub3d/live/safety_check.py rec [RIG.json] [--faults all] [--loops 2]
```

`safety_check` runs the arms with the body model made wrong on purpose
(moved 3 or 6 cm each way, 2 cm thinner, 0.4 s late, stuck, one forearm
8 cm off, the person dropping out of view) while the camera's depth stays
true, and follows each arm with a model of the real one (its servos at the
speed and acceleration the driver sets). Against the true model it counts
impacts (a part reaching the person from 1 cm or more away, and how fast it
was closing), slides (a sponge or arm already at the skin carried across
it), and how deep anything went, and fails a fault with an impact over
25 cm/s, a sponge more than 20 mm in, or the structure more than 15 mm in.

`rig_sim` follows the person once and then runs the arms over the kept
frames, a few seconds a rig. `arms_anywhere` runs it on named awkward rigs
(all on one side, all behind, facing away, far off, on the floor, overhead,
inside the chair, bases stacked, tipped toward the person or away, rolled,
on the walls, hanging from the ceiling) and on random ones all around the
chair, half of them tipped, and fails a rig if the motion crashed, stopped
the fleet, stalled, jumped (a joint turning more than 0.06 rad in a checked
step), or moved an arm into the body or toward another arm. A bad rig
scrubbing little is not a failure; each report says what every arm could
reach when it planned (`reach_cm2`), which its passes are measured against.

## Measured on both recordings

Two recordings of the same person in the same chair: `live_rec_20260916`
(600 frames, 37 s) and a second one that afternoon (1,200 frames, 94 s,
kept on the machine only), where they sit about 7 cm further forward with
respect to the seat point and move their arms about.

`safety_check.py` on both, twice through each, the real arms modelled
(servo speed and acceleration as the driver sets them, readings 40 ms late,
blocked by the true person):

- The searched rig (`live_rig_table.json`): all 19 faults pass on both
  recordings, 38 runs. Nothing but the sponge ever touched the person; the
  fastest the real sponge reached the skin was 19 cm/s and the deepest it
  went 14 mm. With the model right it pressed 9 to 12 mm in, a pass took 44
  to 60 s, and the arms covered 98 to 99% of what they reach.
- The hand-placed rig (`live_rig.json`, red at the crotch): 36 of the 38
  pass. The two that do not are the model 6 cm away from the camera and
  6 cm to the person's right, on the second recording, where red waits
  against the person; nothing moved into them in either (no impact, no
  slide, the sponge 25 and 40 mm in at rest).
- Model 3 cm out any way, 2 cm thinner, one forearm 8 cm off, 0.4 s late,
  stuck, or the person out of view 1.5 s in every 8: both rigs pass, the
  arms kept off or the driver held them.

The same rigs with the arms simulated (the depth guard on, three times
through each recording):

| rig | reach | pass | each arm a pass | moving |
|---|---|---|---|---|
| `live_rig.json` (red at the crotch) | 1,060 to 1,290 cm2 | 29 to 54 s | red 88 to 92%, blue 86 to 100%, green 99 to 100% | 71 to 81% |
| `live_rig_table.json` | 1,630 to 1,750 cm2 | 29 to 54 s | 98 to 100% | 85 to 90% |

- The scrubbable skin is 3,600 cm2, so those are 29 to 36% and 43 to 49% of
  it. What is left out is mostly the sides of the person's torso and the
  undersides of their arms, which their own arms cover: of the torso's
  2,280 cm2 the searched rig reaches 34%, and of each limb 56 to 69%.
- No jump, no stop and no move into the person in any run.

## Measured on the recording

The recording is 600 frames over 37 s (16 a second), the person sitting at
rest, facing the camera.

- The hand-placed three-arm rig in `live_rig.json` reaches 43% of the upper
  body (1,550 of 3,620 cm²: red 916, blue 346, green 291). Over three times
  through the recording (`rig_sim.py`) all three arms scrubbed 100% of what
  they reach in each of three passes and 92 to 100% of the fourth, which the
  run ended inside; a pass took 23 to 32 s (the first 23 s; 43 s before
  tonight's changes), with the sponge moving at about 30 cm/s. The closest
  two arms came was 4.5 cm (the hold line); 75 moves were held for a moment
  and 24 points skipped, with no stop, jump, or move into the person. At 8
  and 5 frames a second a pass takes 28 to 37 s (60 s at 8 before the steps
  were split; it did not finish at 5).
- Counting everything under the sponges, each pass covers about 2,000 cm².
  Tipped as in `live_rig_tilted.json`, the same three bases reach 47% and
  cover about 2,150 cm² a pass, in 25 to 29 s.
- `arms_anywhere.py`, 58 placements (seed 11): all ran cleanly, and in their
  best pass the arms scrubbed 98.4% of what they could reach when they
  planned. On the recording (seed 23), where the person moves
  their arms often, also all clean, at 87% (45% before the faster,
  sub-stepped motion and the way round). Under the sponges, the best passes
  added up to 17,950 and 15,610 cm² (13,910 and 12,060 without owners for
  the skin out of reach).
- Arm motion costs about 8 ms a frame (18 ms before the arm geometry was
  cached and the reach margins computed in numpy).

## Measured on the recording

- Model against the live surface, median gap: torso 10 mm, upper arms 7 to
  9 mm, forearms 6 mm, head 10 mm, thighs 3 to 9 mm, hands 10 to 19 mm.
- 7 to 9 frames per second with the viewer, 17 without, at the time; the
  live view has run at 11 to 19 with the arms since.

## Known limits

- In the rest pose (hands on the thighs) under half of the scrub area can be
  reached at all: 72 arms placed all round the chair reach 45% on the
   recording, and its three hand-placed arms 43%. The rest is the
  torso's sides behind the hanging arms (gaps narrower than the sponge), the
  belly behind the lap and hands (the jaw meets the thighs), and the insides
  of the arms.
- The torso is one rigid shape, posed to the measured front. A seated front
  (a T-shirt draped over the belly) slopes forward by 30 degrees or more,
  so the model leans back as far as it may (25 degrees) and still sits 3 to
  5 cm off the surface on the recording, where the back is
  upright. A torso that is deeper at the belly would fit both.
- An arm waits while its limb moves, and gives up after 15 s with nothing new
  scrubbed. On the recording the person moves their right arm
  every few seconds, and the arm scrubbing it covers less of it per pass.
- Arms hung from the ceiling, or mounted well above the head, reach nothing:
  the sponge may not go above the base of the head anywhere, and every pose
  near such a base is above it.
- Red, in front of the person, hides about a fifth of them from the camera.
- Which arm waits for which, and so holds and coverage, change a lot with
  small differences in when the person moves. Judge a rig on several runs.
- The placements were made on one recording of one person in one chair.
  Check them again for anyone else.
