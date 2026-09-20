# Handing over the live arms

For whoever picks this up next. What is here, how to run it, what was
changed and why, and what is still wrong. The detail of how
each part works is in [README.md](README.md) (the view, the scrub, the
depth guard, the rig) and [ARMS.md](ARMS.md) (the real arms and their
driver). This file is the tour and the standing rules.

## The rules on this branch

- Work only inside `scrub3d/`. Never change py/, web/, tests/, run.sh or
  the root config.json. The repo-root CLAUDE.md is for the web demo; its
  "never stop, never ask" rules do not apply here.
- **Nothing may command arm hardware.** Fake boards (`fake_esp32.py`) and
  `--drive fake` only, unless the person says otherwise themselves.
- **Before starting or restarting the live view on the camera, and before
  any recording, say so and wait for a go-ahead.** The chair is placed
  from the first seconds of readings, so a person who is not seated yet
  spoils the run. Replaying a recording needs no go-ahead.
- **Never commit `scrub3d/data/`, `weights/`, `bodies/`,
  `devconsole/runs/`, `drive_logs/`, `arm_ports.json` or any recording.**
  They are pictures of a person. `data/` is git ignored; keep it that way.
  One recording is in the repository on purpose (`data/live_rec_20260916`).
- Test changes against the recordings, not by restarting the camera. The
  person asked for this in as many words.
- Commit subjects and body lines at most 72 characters, no em dashes, and
  a `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` trailer. Push
  only when asked.

## Running it

```bash
# the live view on the camera (ask first)
python scrub3d/live/live_body.py --seat-mm 420 --keep-floor
# a recording instead of the camera, looping
python scrub3d/live/live_body.py --replay scrub3d/data/live_rec_20260917d --seat-mm 420
# once through, no viewer, for measuring
python scrub3d/live/live_body.py --replay scrub3d/data/live_rec_20260917d --once --no-viewer --seat-mm 420
# record a new dataset: a countdown, then 1200 frames (about 2 minutes)
python scrub3d/live/live_body.py --seat-mm 420 --dump scrub3d/data/live_rec_NEW --dump-delay 10 --dump-max 1200
# stop a view running in the background
python scrub3d/live/stop_live.py
```

`--seat-mm 420` is the chair's measured seat height. `--keep-floor` holds
the floor once a sensible seat has been found on it: without it a
wandering floor fit re-tilts the world mid demo (see *What bit us*).

The console prints each finished pass with its per arm share, every change
in what the depth guard holds the arms for, and where the arms are every
10 s. That is enough to follow a run without watching Rerun.

### The 3D position adjustor (the rig editor)

```bash
python scrub3d/live/rig_editor.py                 # http://127.0.0.1:8078
python scrub3d/live/rig_editor.py --rig other.json --recording scrub3d/data/live_rec_20260917d --port 8079
```

A page with the seated person from a recording, the chair, the camera and
the arms in 3D. Drag an arm's base to move it, use the turn and tilt
gizmos, or type numbers. **Start pose** (key G, or the "start °" field,
or Z and C) turns where an arm waits, up to 88 degrees either way,
positive to the arm's left. **Check** reports what each arm could reach
and warns when an arm has nowhere safe to wait. **Simulate** runs the
arms' real motion through the recording. Saving writes `live_rig.json`
(the previous one is kept as `live_rig.previous.json`), and a running live
view picks the new rig up within a second.

Rigs in the repository:

| file | where the arms are | reaches |
|---|---|---|
| `live_rig.json` | as the person placed them: red low in front at the crotch, blue and green at the sides | 1,020 to 1,800 cm2 |
| `live_rig_clear.json` | searched so that every arm is clear of the person at rest | 1,680 to 1,820 cm2 |
| `live_rig_table.json` | searched earlier: all three at table height in front | 1,630 to 1,750 cm2 |
| `live_rig_tilted.json` | the older hand rig with its bases tipped | |

### Testing

```bash
python scrub3d/live/safety_check.py scrub3d/data/live_rec_20260917d   # 19 wrong model runs
python scrub3d/live/rig_sim.py scrub3d/data/live_rec_20260917d        # the arms, no viewer
python scrub3d/live/arms_anywhere.py scrub3d/data/live_rec_20260917d --rigs 60
python scrub3d/live/depth_guard.py --selftest
python scrub3d/live/arm_hw.py --selftest
```

`safety_check` makes the body model wrong on purpose (moved 3 or 6 cm each
way, 2 cm thinner, a forearm 8 cm off, 0.4 s late, stuck, the person out
of view) while the camera's depth stays true, and follows each arm with a
model of the real one. It fails a run on an impact over 25 cm/s, a sponge
more than 20 mm in, or structure more than 15 mm in. A run the driver
stops is safe and is reported as such.

The recordings are large (1 to 2 GB each) and are not in git:

| recording | what it is |
|---|---|
| `live_rec_20260916` | in the repository on purpose, 200 frames |
| `live_rec_20260917` | morning, 600 frames, sitting at rest |
| `live_rec_20260917b` | afternoon, 1200 frames over 94 s, some arm movement |
| `live_rec_20260917c` | 1200 frames over 98 s, arms moving a lot |
| `live_rec_20260917d` | 1200 frames over 126 s, the current test set |

Following a recording takes about a minute, so cache it if you iterate:
pickle a `rig_sim.Track(folder, 420.0, keep_depth=True)` once and load it
for every later run.

## What changed, and why

The demo that morning showed red idle, arms dabbing rather than scrubbing,
and passes counting up with nothing done. In order:

1. **Red could not move.** Its base sits between the person's thighs, so
   the camera sees them where red's own structure is. The depth guard read
   that as "inside the person" for the whole arm and refused every move.
   The camera's points are now judged part by part: a link held against
   them does not stop the rest of the arm, and no part may go deeper.
2. **Distance to measured points cannot tell inside from clear** (deep
   inside a thigh the nearest measured point is far away again), so how
   far a link is *behind* the surface the camera measured along its own
   ray now decides. A link may be 20 mm behind it, and only while the
   model also has it within 25 mm of the person: reaching round a limb
   with room to spare is the arm's work, not a collision.
3. **The sponge slides at the depth it may press** instead of refusing the
   point, which matters because clothes stand 15 to 25 mm in front of the
   model and every stroke was being given up.
4. **Scrubbing looks like scrubbing.** The rub only moved while the sponge
   travelled between two cells, which lasted 0.15 s, and did nothing where
   it stood. It now rubs wherever the sponge is, 25 mm across at 1.3 Hz on
   the torso and 12 mm on a limb, and each point is scrubbed for DWELL_S
   before the stroke moves on (twice that on a second round).
5. **Rows run across the person, top row first** (TOP_DOWN), each carrying
   on from where the one above ended, like a printer, and an arm's highest
   patch before the ones below it.
6. **No parking between passes** (ALWAYS_SCRUBBING): a pass ends where the
   arms are and the next starts there. An arm whose patches are done goes
   round them again while the others work.
7. **One part the camera disputes no longer stops everything.** Only the
   torso being out, or three parts at once, holds all the arms; a forearm
   the person is moving is left alone while the rest is scrubbed.
8. **Cells the person's own body hides from the camera are left out of
   that comparison.** A forearm across the chest made the camera measure
   the forearm where the model has the chest, and the torso was doubted
   almost constantly. Now 0.5% of frames.
9. **The model stopped twitching.** Elbow and wrist readings jumped up to
   15.6 m/s between frames when a limb crossed MediaPipe's visibility
   threshold; a joint is now carried toward a new reading at MAX_JOINT_V
   (1.5 m/s), so the worst jump is 1.2 m/s.
10. **The arms follow a moving person**: the plan is cells of the model, a
    limb is only waited for above CHASE_V, the territories are solved
    again when the arms can reach much less than when they were solved
    (keeping what has been scrubbed), a point given up is retried after
    RETRY_S, and a parked arm looks for work every WAKE_S.
11. **A pass is measured against what an arm could reach during it**, not
    what it can reach this instant, which used to jump to 100% the moment
    the person moved out of reach.

## Where it stands

On `live_rec_20260917d` with the rig as placed by hand: the arms are on
the skin about half the time, parked 1 to 5%, and a pass finishes with red
74 to 78%, blue 93 to 96%, green 88 to 100%. Live passes on the camera
that evening: red 74 to 92%, blue 95 to 100%, green 97 to 100%.

`safety_check` over four recordings, 76 runs: 50 pass. **No contact was
ever fast**: the worst over every run was 189 mm/s, under the 250 mm/s
bar, and nothing but the sponge reached the person at speed. The failures
are slow: in 59 runs the driver stopped because the person met an arm, and
in 25 a structure ended up more than 15 mm inside them.

## What is still wrong

- **Every arm's rest pose is inside the person**, by 36 to 68 mm on the
  last three recordings, because the arms are bolted where the person now
  sits. That is why arms report "nowhere to wait", why each takes 2 to 3 s
  to reach its first stroke (the whole flight is inside the near band, so
  it is capped at 20 cm/s), and why the safety runs find structures inside
  them. `live_rig_clear.json` fixes the geometry (rest clearances of 64,
  91 and 75 mm, and 1,680 to 1,820 cm2 in reach) but is **not simply
  safer**: with that much more of the person in reach the fault runs end
  with deeper and faster contact, the worst 348 mm/s. It wants the near
  person speeds retuned before it is the rig to use.
- **The rig is anchored to a seat point estimated from the person**, which
  moved 12 cm with respect to their hips between two recordings on the
  same day. A rig placed on one recording can land somewhere else on the
  next. Re-place in the editor when the camera or chair moves.
- **The floor fit wanders**: successive fits in this room ranged from 7 to
  16 degrees down. Without `--keep-floor` the view takes that for a moved
  camera, restarts the body, and the arms can end up inside the person.
- **The camera drops to USB 2.1** after repeated restarts, and then cannot
  give the stream the view asks for ("couldn't resolve requests"). A
  `pyrealsense2` `hardware_reset()` (sometimes twice) brings it back to
  3.2; replugging into a USB 3 port is the sure fix.
- The real arms have never been driven by any of this. Everything is fake
  boards and recordings. See ARMS.md before that changes.

## The machine this ran on

Python 3.12 at `C:\Users\justi\AppData\Local\Programs\Python\Python312`.
`rr.spawn` needs that Python's `Scripts` on PATH or the viewer will not
start. The camera is a RealSense D455 about 1 m up, looking about 10 to 14
degrees down. Three arms: red low in front of the person, blue to their
left, green to their right; a fourth (orange) was removed.
