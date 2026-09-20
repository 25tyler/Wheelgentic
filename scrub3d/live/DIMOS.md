# Driving the OpenYAM arms through dimOS

Two Anvil OpenYAMs on one plate, beside the chair, controlled by
[dimOS](https://github.com/dimensionalOS/dimos). Nothing about how the live
view plans, checks or paces a scrub changes; only the last hop -- the thing
that turns an approved tool point into motion -- is different.

## The two halves

| | runs on | needs | file |
|---|---|---|---|
| **bridge server** | the arm computer (the Spark, where the CAN adapters are) | the dimos venv | `dimos_bridge_server.py` |
| **live view** | wherever the camera and open3d are (today: the laptop) | scrub3d's usual deps, no dimOS | `live_body.py --drive dimos`, via `arm_dimos.py` |

They talk over one TCP connection (port 7790, JSON lines). The bridge is the
only code that imports dimOS. `arm_dimos.py` looks exactly like
`arm_hw.Hardware` to the live view.

## Run it

On the Spark, mock arms first (dimOS simulates them; nothing moves). `$WG`
is wherever this checkout lives on that machine -- the commands below are
run from its root:

```bash
cd $WG && ~/dimos/.venv/bin/python scrub3d/live/dimos_bridge_server.py
```

Real arms, once `can0`/`can1` are up (`sudo ip link set can0 up type can bitrate 1000000`, both):

```bash
cd ~/thingy && bash scrub3d/live/bridge.sh real
```

**Use the script, and leave it running.** It starts the bridge with this rig's
wiring (can1 is the person's LEFT arm, can0 their right), keeps it up if it
exits, and refuses to run two at once. Started by hand the other way round
(`--left-can-port can0`), each arm gets the other arm's commands, and because
the two bases face opposite ways every move comes out mirrored: the start pose
turned a claw into the leg of the person in the chair. Do not restart it
between live-view runs: every restart drops the arms' torque, and the live
view reconnects to a running bridge by itself. `bridge.sh status` says whether
it is up; `~/bridge_stops.log` says who last stopped it.

The CAN interfaces reset every time an adapter is unplugged. Make them come
up on their own, once, instead of typing the two `ip link` lines each time:

```bash
echo 'ACTION=="add", SUBSYSTEM=="net", KERNEL=="can*", RUN+="/sbin/ip link set %k up type can bitrate 1000000"' | sudo tee /etc/udev/rules.d/90-can.rules && sudo udevadm control --reload
```

`--attach` joins a stack somebody else started instead of starting one, but
the streamed targets need the cartesian tasks this file's blueprint adds, so
it only works against a stack built from `build_blueprint()` here. dimOS's
own `dimos run dual-openyam-planner-coordinator` has no such tasks: targets
sent to it are ignored.

Viser (the dimOS 3D view) is at `http://127.0.0.1:8095` on the Spark; tunnel it with
`ssh -L 8095:127.0.0.1:8095 asus@10.189.59.208`.

**One bridge at a time.** Two bridges are two dimOS stacks on one bus
answering the same names, and that looks like random failures (targets
ignored, `Planning failed`, missing `_execution_manager`). The bridge takes
port 7790 before it starts dimOS, so a second one refuses to start; but
after stopping one, wait for it to exit (dimOS takes up to 5 s) before
starting the next:

```bash
pkill -f dimos_bridge_server; while pgrep -f dimos_bridge_server >/dev/null; do sleep 1; done
```

Check the bridge from anywhere, without the live view (reads both arms; with
`--nudge-mm 30` it lifts each tool 30 mm and puts it back -- **that moves the arms**):

```bash
SCRUB3D_ARM=openyam SCRUB3D_DIMOS=10.189.59.208:7790 python scrub3d/live/arm_dimos.py
```

`--home` parks both arms afterwards through dimOS's planner (a checked,
paced trajectory), which is also how to get real arms to a known pose.

The live view, on the laptop:

```bash
SCRUB3D_ARM=openyam SCRUB3D_DIMOS=10.189.59.208:7790 \
python scrub3d/live/live_body.py --drive dimos --rig scrub3d/live/live_rig_openyam.json
```

`SCRUB3D_ARM=openyam` swaps `kinematics.py` to the OpenYAM's geometry
(`kinematics_openyam.py`) for everything that reasons about reach and links.
Leave it unset and the RoArm model is back, untouched.

## The rig file

`live_rig_openyam.json` is two rig entries, one per arm base, seat-relative.
The arms need not sit on Anvil's plate: a plank across the armrests at any
spacing works, facing forward or turned inward, because each arm is driven
in its own base frame. Three tape-measure numbers make the file:

```bash
python scrub3d/live/openyam_rig.py --span 800 --height 700 --ahead 100 --facing 0
```

`--span` is base to base across the plank, `--height` floor to plank top,
`--ahead` from the seat point (the hips) forward to the line between the
bases, `--facing` which way the left-port arm points (0 = the way the person
faces; an arm points the way it reaches at its home pose). **a0 is the arm on
`--left-can-port` and must be on the person's left; if the wiring came out
the other way, swap the CAN ports, not the file.**

## The camera

No camera numbers go anywhere. The live view fits the floor plane in the
depth image and finds the person, and builds its world from those: origin on
the floor under the seat, +x the way the person faces. What the camera needs
is a clear view of the person from roughly in front (up to about 30 degrees
off to a side is fine), the arms in frame, and a good patch of bare floor.
Around 1.5 m away is right for the D455. Bags and cases on the floor around
the chair are the usual reason the floor search fails and the arms wait.

## Before bolting the plate: check the rig on a recording

The first placeholder (a plate behind the chair) reached 0% of the person:
every path went through the torso. Reach is not just arm length; the
governor also wants 60 mm of clearance for the arm's structure, and a
458 mm forearm reaching from far away sweeps low over the lap. So test a
plate pose on a recording before drilling anything:

```bash
SCRUB3D_ARM=openyam python scrub3d/live/arms_check.py scrub3d/data/live_rec_20260917f300 scrub3d/live/live_rig_openyam.json
```

It prints what fraction of the scrub area each arm can reach safely and
every refusal with its reason. Edit the plate numbers, re-derive the two
entries, run again. The pair is rigid: 620 mm apart, turned the same way.

Six plate poses on the reference clip (`live_rec_20260917f300`), all facing
the person, seat-relative:

| plate (x from seat, z) | reachable | per arm cm2 | closest arms | refusals |
|---|---|---|---|---|
| **550 mm ahead, 650 high** (the file) | **62%** | 1177 / 1103 | 400 mm | 15 |
| 450 ahead, 850 high | 58% | 1154 / 1005 | 316 mm | 53 |
| 600 ahead, 1000 high | 57% | 1036 / 1063 | 308 mm | 13 |
| 350 ahead, 1000 high | 42% | 857 / 694 | 243 mm | 14 |
| 250 ahead, 800 high | 36% | 619 / 707 | 397 mm | 17 |
| 150 ahead, 950 high | 20% | 383 / 363 | 424 mm | 173 |

And the plank across the armrests, as first built (bases 800 mm apart,
700 mm up), the arms pointing the way the person faces:

| plank line, from the seat point | reachable | per arm cm2 | closest arms | note |
|---|---|---|---|---|
| 100 mm ahead (at the hips) | 8% | 105 / 197 | 197 mm | the forearms sit under the bases, inside minimum reach |
| **250 mm ahead (slid over the knees)** | **42%** | 925 / 617 | 158 mm | no safe place to wait clear of the person |
| 100 mm ahead, arms turned inward | 8% | 105 / 197 | 171 mm | |
| 100 mm ahead, bases 660 mm apart | 4% | 57 / 78 | 45 mm | the arms fight each other |

Lower and further forward wins: the long forearm comes in level with the
person's forearms instead of down over the lap, and an arm cannot scrub
what sits under its own base. A plank across the armrests should be slid
as far forward as it goes; a board in front of the knees facing the person
does better still and leaves the arms somewhere safe to wait. With the
front plate driven on the mock arms, both arms worked a full pass and the
sponge was credited on skin.

## What is different from the RoArms, and what to watch

- **The view draws the OpenYAM itself**, not a ball where its sponge is.
  `armmesh_openyam.py` places i2rt's own meshes (`assets/openyam/`, MIT,
  copied unchanged from dimOS's dual_openyam package). While driving, each
  arm is drawn at the six joints dimOS reports, wrist and all, so it should
  lie on the arm the camera sees; if it does not, the rig file is off. A
  small dot in the arm's colour marks the point it is being sent to. With no
  arms connected it is the planned pose, wrist at zero, gripper on the
  sponge point. For the view only: the governor and the depth guard still
  reason on the three-joint model, and `armmesh.py` stays empty.
- **A target is a pose.** dimOS wants where the grasp frame is AND how it is
  turned. `arm_dimos` points the tool along the surface normal when the live
  view gives one, otherwise away from the shoulder. Position is weighted 4x
  over orientation in the IK, so a poor orientation slows, never blocks.
- **The arms move when the live view starts.** As with the RoArms, the
  driver first sends both arms to their start pose (dimOS's home, through
  its planner). Start the live view before anyone sits in the chair.
- **The arm runs behind the stream.** At the driver's 240 mm/s the mock
  arms trailed the commanded point by 40 to 90 mm while scrubbing, up to
  290 mm on a flight, under dimOS's 1 rad/s joint cap. Real arms will be
  similar. If the sponge visibly lags the model, either raise the cap
  (`--max-joint-velocity 2`, dimOS's own teleop value) or lower
  `MAX_STEP_MM` in arm_dimos.py. Tune it with the arms in front of you.
- **A joint past its limit ends the session.** dimOS's Damiao adapter latches
  a fault, drops torque and tears the stack down (the bridge exits) when a
  joint reports more than its clamp margin outside its range. Upstream that
  margin is 0.05 rad and the right wrist tripped it three times by sagging a
  few degrees past its stop. The latch is left as shipped (it is a safety
  check); instead the bridge's IK keeps 0.5 rad off every limit and the
  start pose keeps the wrist at zero. If the bridge is gone and `bridge.sh
  real` fails to activate, a wrist is past its limit: with torque off, move
  it back by hand and start again.
- **The start pose is turned away from the person.** dimOS's own home
  points the tool 34 cm forward at chest height; with the bases beside the
  hips that is right in front of the person. The driver now parks each arm
  folded up and outward (33 cm outboard of its base, plank height) with the
  claw closed, and the rig's `start_deg` turns the simulated start the same
  way, so "returning" between passes goes outward too.
- **The watchdog is dimOS's.** A cartesian target older than 0.5 s makes the
  arm hold. `arm_dimos` streams only while the live view feeds it fresh
  points, so a stalled live view is a stopped arm, exactly as with the RoArms.
- **Deactivation drops torque.** When the bridge stops, the arms go limp.
  Support them before stopping the server (Anvil's own instruction).
- **The joint angles scrub3d shows are the planar equivalent** of the first
  three real joints. dimOS may place the wrist differently, which can move the
  real tool a hand-length (up to 210 mm) from the model's for the same three
  angles; the collision checks run on the point dimOS REPORTS, so that slack
  only affects the reach estimate, where it is inside the margin anyway.
- **Speed cap:** every joint is capped at 1 rad/s in the bridge's blueprint
  (`--max-joint-velocity`); dimOS's own teleop uses 2. Raise it when the
  scrub looks right at 1.
