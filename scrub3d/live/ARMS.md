# Connecting the real arms

The live view drives the real RoArm-M2-S arms with `--drive`. Nothing else
in `scrub3d/live` talks to an arm. The driver is `arm_hw.py`. It has only
been run against simulated boards (`fake_esp32.py`). A real arm has never
been connected, so do the first run as described below.

## Keeping clear of the person

The live view checks every move against the body model, and on top of that
against what the depth camera measures, so that a model that is off cannot
walk an arm into you (README, *Keeping clear of the person when the model
is wrong*). What the arms and their driver add:

- **Speed, capped on each board.** When an arm answers, the driver sends it
  a `T:122` to the angles it is already at (it does not move) with a servo
  speed of 90 degrees a second and an acceleration of about 1500 degrees a
  second squared. Every later move uses those, whatever the software asks.
- **Torque, capped on each board** (`T:112`): 5 to 11% of each servo's
  strength, so a pressing arm gives way. Whether that is still enough for an
  arm to hold itself up only a real arm can show (below).
- **The driver stops or backs off** when an arm runs into something, when a
  board restarts, when a servo stops answering, when the live view stalls
  for half a second, and when an arm pressing on the skin is held well short
  of its point (the person is nearer than the model).
- **Nothing is checked before the live view runs.** At power-up each arm
  drives itself straight ahead to its start pose, whatever is there.
  **Switch the arms on before anyone sits in the chair.** The live view then
  turns each one to the start pose set in the rig editor.
- **Each arm runs a WiFi hotspot** with a default password, and anyone who
  joins it can move the arm through its web page. No firmware command turns
  the hotspot or the page off. Before a demo, change the hotspot's password
  (Waveshare's WiFi settings), and do not leave the arms powered where
  people could join it.

What the checks cannot promise: a person who moves into an arm faster than
the camera updates (about 15 times a second) can still meet it, at the
arm's capped speed and torque. The test on recordings is
`safety_check.py`.

## What each arm needs

- **Power:** its own 12 V supply. When an arm is switched on, it moves by
  itself to the firmware's start pose: pointing straight ahead, forearm out,
  about 31 cm in front of its shoulder. **Switch the arms on before anyone
  sits in the chair.** The live view then turns it to the start pose the rig
  editor sets (`start_deg`).
- **Cable:** a USB-C *data* cable from the driver board's port marked
  **USB** to the laptop. That is the port in the middle of the board; the
  one on the edge is for a LiDAR. Three arms need three USB ports, or a
  powered hub.
- **Driver:** the board talks through a SiLabs CP2102 (USB id `10C4:EA60`).
  Windows usually installs its driver by itself. If Device Manager shows no
  new port under *Ports (COM & LPT)*, install the SiLabs "CP210x USB to UART
  Bridge VCP" driver.
- **Firmware:** the driver follows Waveshare's `roarm_m2` firmware, version
  0.84. The arm's small screen shows the version at start-up. Another
  version may behave differently.

## Steps

1. Put the arms where `live_rig.json` says: the rig editor shows each base's
   place and angle. Switch them on and plug them in.
2. See the arms (nothing moves):

   ```bash
   python scrub3d/live/arm_hw.py
   ```

   It lists each board's COM port and MAC address, and where its arm's tool
   is. A board that is still starting can take up to 20 s to answer.
3. Say which arm is which:

   ```bash
   python scrub3d/live/arm_hw.py --assign
   ```

   On each board in turn, the gripper opens a little and closes, twice.
   Type which arm it was (0 red, 1 blue, 2 green). The result is saved in
   `arm_ports.json`, with each board's MAC address, so a board that comes
   back on a different COM number is still found.
4. **First run, with nobody in the chair.** Play the recording and let the
   real arms scrub the recorded person in the air:

   ```bash
   python scrub3d/live/live_body.py --replay scrub3d/data/live_rec_20260917 --seat-mm 420 --drive
   ```

   Watch that each arm goes where Rerun shows it: same side, same height,
   same direction. The white dots in Rerun are where the arms report their
   tools are. If an arm is off by a steady amount, see *What only a real arm
   can confirm*. Press Ctrl+C to end. The terminal then prints the command
   that reports what the real arms did (see *Checking a run*); run it.
5. Then the real thing. Sit down first: the chair is placed from the first
   seconds of camera readings. Then run:

   ```bash
   python scrub3d/live/live_body.py --seat-mm 420 --drive
   ```

## Stopping

- **Ctrl+C:** each arm draws its tool 5 cm back toward its own shoulder,
  away from the person, and holds there with its torque on.
- **Emergency:** switch off the 12 V supply. The arm goes limp and drops.
- **On its own:** the view makes every arm hold where it is, and says why
  in the terminal and in the Rerun panel, when:
  - a board stops taking commands;
  - an arm has not answered for 1 s;
  - a board restarts (it prints its version; it has driven its arm to the
    start pose by itself);
  - an arm has been more than 6 cm from where it was sent for 0.8 s
    (something is holding it back);
  - an arm's readings stop changing for 0.5 s while it is sent 3 cm or
    more away (a servo has stopped answering, or the arm is jammed);
  - a real arm, where its own joint sensors put it, is 15 mm or more
    inside the person as the camera sees them;
  - two real arms, where their sensors put them, come within 5 mm.

  These two hold the others and **back that arm off** the way it came:
  - an arm that is not meant to be on the skin is stuck more than 15 mm
    off its path for 0.3 s, with its base or elbow motor at its torque
    limit (it has run into something);
  - an arm pressing on the skin is held more than 25 mm short of its
    point, at its torque limit, for half a second (the person is nearer
    than the model).

  And without stopping the run: an arm not given a new point for half a
  second (the live view has stalled) stops where it is until it gets one;
  with the person out of view for half a second, arms on the skin lift off
  and all wait; and while the camera disagrees with the body model, no arm
  touches the person.

  Restart the live view to go on after a hold.
- The driver never sends `T:0`. In this firmware `T:0` is not a latched
  stop: it turns every servo off for 10 s (the arm drops) and then back on,
  and the board reads nothing meanwhile. `T:999` does nothing.

## What the arms can sense, and what the view does with it

- **Joint angles.** Each servo has a position sensor (4096 steps a turn,
  about 0.09 degrees). Every position request makes the board read all of
  them there and then; it also works out the tool point from them. The
  view asks 20 times a second.
- **Motor effort.** Each servo also reports how hard it is driving, as a
  share of its maximum, never above its torque limit. This is **not a
  force or pressure sensor**: nothing on these arms can say how hard the
  sponge presses. It can show that a motor is pushing against something.
  The shoulder's number carries a fixed offset from start-up (it is one
  shoulder servo minus the other, and the other is read only once), so the
  contact check uses the base and elbow only.
- **Not reported:** current, voltage and temperature. The board reads them
  but no command sends them.
- **If a servo stops answering,** the board keeps repeating its last
  reading without saying so. The view catches readings that stop changing
  while the arm is sent elsewhere.
- **What the joint sensors cannot say** is where an arm's base really
  stands. They measure the arm against its own base, so an arm whose base
  is a few cm from where the rig file puts it still looks right to them.
  Only the camera can see that (below).

With these, the view:

- refuses an arm at start-up whose joint angles and tool point disagree
  under scrub3d's model of the arm (a wrong frame or joint direction);
- checks every frame where the real arms are, from their joint angles,
  against the person and against each other (the stops listed above);
- counts skin as scrubbed only where a real sponge reached it, and shows
  that next to what the plan counted, in the panel and in the colours on
  the body. Skin a real sponge missed is done again on the next pass;
- in a live run, every 2 s, draws each real arm where it should be, as the
  camera would see it, and compares that with the depth the camera
  measures (`arm_sight.py`). The panel says, for each arm, whether it is
  where the rig file puts it or which way it looks off. On drawn test
  scenes it catches a base 2 to 3 cm off; 1 cm still looks right. This
  only warns, and it has not seen a real arm yet: dark arm parts can give
  the camera no depth, and then it says it cannot tell. It is off in
  replays and with simulated boards;
- writes every sample to `scrub3d/live/drive_logs/` (kept out of git: it
  holds places on the person's body).

## Checking a run

```bash
python scrub3d/live/arm_hw.py --report scrub3d/live/drive_logs/drive_YYYYMMDD_HHMMSS.jsonl
```

For each pass, how much skin the real sponges reached against what the
plan counted. For each arm:

- how far its real tool was from where it was sent, and how late it
  followed;
- how far its joint angles were from scrub3d's model of its tool point
  (more than a degree or two means a frame or calibration problem);
- how close its real structure came to the person;
- each motor's effort moving in the air and on the skin, and how often it
  sat at its torque limit. An arm often at its limit in the air cannot
  carry itself at that speed: raise that cap, or the contact check will
  stop the arms;
- how old its readings were;
- what the camera check said, and which way the arm looked off.

## What the driver does

- Opens each port with DTR and RTS held low, so the board's automatic
  download circuit cannot restart the ESP32.
- Sends three settings, and repeats them every 10 s in case a board has
  restarted:
  - echo and debug text off (`T:605 cmd 0`);
  - the ESP-NOW follower mode off (`T:301 mode 0`), otherwise any nearby
    leader device can move the arm;
  - joint torque capped so the arm gives way on contact (`T:112`, base 60,
    shoulder 110, elbow 50, hand 50, out of 1000).
- Asks each arm where it is (`T:105`) until it answers. Nothing moves until
  every arm has answered, and the simulated arms start where the real ones
  are. Each arm then travels, under the governor, to where it waits, before
  any scrubbing starts.
- Then sends each arm the tool point the governor last approved, 40 times a
  second (`T:1041`, which does not block).
  - Each point is at most 12 mm from the one before.
  - The gripper angle is always included; without it the gripper opens.
  - A point the firmware cannot solve is never sent: this command has no
    guard, and a bad point can whip the arm.
- Asks each arm for its position 20 times a second.

The firmware's x, y, z are millimetres from the arm's **shoulder pivot**
(x forward, y left, z up), not from its base. The driver converts to and
from scrub3d's base frame (`FW_ORIGIN_MM`, 10 mm forward and 123 mm up).

## What only a real arm can confirm

- **The shoulder height.** Waveshare's URDF says 123 mm, its firmware
  config says 126. If every arm sits a steady 3 mm high or low, change
  `FW_ORIGIN_MM` in `arm_hw.py`.
- **Whether the torque caps are enough for an arm to hold itself up.** If an
  arm sags and the view holds with "something holds it back", raise the
  shoulder cap (`s` in `TORQUE_CAPS`), say to 300. The report shows how
  often each motor sat at its cap.
- **What motor effort scrubbing takes.** Only a real run shows the numbers,
  in the report; they are what a future pressure estimate would start
  from.
- **How closely the arms follow.** The simulated boards follow within
  about 1 to 3 cm; the report gives the real figure.
- **Whether the boards' USB serial numbers are unique.** The MAC address is
  tried first.
- **Elbow direction.** scrub3d's kinematics bends the elbow up, as the
  firmware source does. Any other bend would show in step 4.

## Older arm code

`py/arm.py` and `scrub3d/armlink.py` assume two things the firmware source
contradicts: that the firmware's x, y, z start at the base, and that `T:0`
is a latched stop. They also clamp to a tabletop box. Do not use them for
these arms. `arm_hw.py` is what the live view uses.

**The torque-scale finding below has now been acted on in `py/arm.py`** (the
projector demo's driver; it is still the wrong driver for *these* arms, for
the frame and `T:0` reasons above).

The finding was: that file's contact test (shoulder effort over 550 or elbow
over 450) can never fire with the torque caps `py/arm.py` sets itself (110
and 50), since a servo's effort reading stays within its cap. The same was
true of its `TORQUE_ESTOP` cutout (600-900 against caps of 50-110), so the
demo's torque watchdog ran at 10 Hz for the whole run having checked
nothing.

Reproduced by execution against `fake_esp32.py`, which clamps its reported
loads to the `T:112` cap as the firmware does: with the tool stalled 60 mm
inside a surface the board reported every joint pinned at its cap
(60/110/50/50) while `over_torque()` returned `None` and `contact` read
`False`.

`py/arm.py` now derives both thresholds from the caps it sends, at
`arm_hw.py`'s `LOAD_NEAR_CAP` (0.9) for the cutout. It checks `torB` and
`torH` only:

- `torS` is excluded for the reason this document already gives -- it is one
  shoulder servo's load minus the other's, zeroed at boot, so it carries an
  unknown offset. `arm_hw.py` excludes it from `BUMP_KEYS` for the same
  reason.
- `torE` is excluded by measurement. Sweeping all 58,968 reachable points in
  `py/arm.py`'s `BOX` through `fake_esp32._loads` with nothing under the
  sponge, `torE` reaches 39.1 of its cap of 50 -- 78% -- on gravity alone.
  Ten units of headroom cannot separate a press from a long reach, so that
  joint is left to `py/contact.py`, which subtracts the gravity term and
  tests the residual. `torB` and `torH` keep over 90% headroom and are
  separable.

Measured after the change: zero false positives across six poses spanning
the box in free air, and a real cutout (`torB 60 > 54`) on the demo's own
watchdog thread when the sponge presses.

## Sources

- Waveshare RoArm-M2-S wiki pages, read through SpotPear's copies because
  the Waveshare wiki blocks automated reads:
  - the driver board's port list:
    https://spotpear.com/index/study/detail/id/1072.html
  - the JSON commands, spd and acc:
    https://spotpear.com/wiki/Robotic-Arm-Control.html
  - serial use from Python:
    https://spotpear.com/wiki/Python-UART-Communication.html
  - ESP-NOW control: https://spotpear.com/wiki/ESP-NOW-Control.html
- The firmware: https://github.com/waveshareteam/roarm_m2, folder
  `RoArm-M2_example/`:
  - `json_cmd.h` and `uart_ctrl.h` for the commands;
  - `RoArm-M2_module.h` for the coordinate frame, the IK, `T:0`, and
    what the position reply reads (`RoArmM2_getPosByServoFeedback`);
  - `RoArm-M2_config.h` for the link lengths and the ESP-NOW default.
- Waveshare's `roarm_ws_em0` URDF and ROS 2 driver, for the joint signs.
- The servo library (`SCServo/SMS_STS.cpp`) in
  https://github.com/waveshareteam/ugv_base_general, for what the effort
  and torque limit registers mean.
