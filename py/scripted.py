"""py/scripted.py — canned scrub with NO camera and NO pose.

Cut-list item #3: if live tracking fails at hour 7, the volunteer puts their
forearm on a taped X and this runs. The arm still scrubs a real forearm; only
the tracking is gone. 20 minutes to fall back to, and it still demos.

    python py/scrubbot.py --scripted
"""
import time
import motion


class _Aborted(Exception):
    """Raised inside run_canned when an estop (or a caller-supplied stop)
    fires. Caught at the top level so a stop unwinds the whole routine rather
    than continuing to the next travel segment."""

# Edit these two to match your taped X, then never touch them again.
FOREARM_MID = (280.0, 0.0)      # robot mm, centre of the taped forearm spot
BUCKET      = (200.0, -180.0)   # robot mm, where the water is


def run_canned(arm, fire, fire_reset, splotch_ts, z_mm=45.0, dur=8.0,
               use_water=False, contact_depth=-5.0, settle=2.5,
               should_stop=None):
    """Canned routine. `use_water` gates the bucket dip.

    The team brainstorm says NO WATER -- it damages the motors -- so the dip
    defaults OFF. Keep the flag rather than deleting the code: the dip is the
    single most legible motion in the demo (visible from 20 feet, and it is
    what makes an audience understand what the machine is doing) so if the
    hardware team ever green-lights a damp sponge, it is one config edit.
    """
    hover = z_mm + 70.0
    # WHERE THE ARM ACTUALLY IS, IN MILLIMETRES, ASKED FOR AS SUCH.
    # This was `arm.target`, later sliced [:3] and handed to motion.travel
    # as a cartesian triple. That is only millimetres on the RoArm; on the
    # OpenYAM target is six joint RADIANS, so the routine interpolated from
    # (0.0, 0.437, 0.437) to the forearm as if radians were millimetres --
    # starting ~280mm from the arm's real position, with every intermediate
    # point fabricated. motion.travel's length guard cannot catch it because
    # both are length 3. This is the fallback mode most likely to be running
    # against a real person, so it gets the honest question.
    start_xyz = arm.cartesian_pose()

    # ABORT ON ESTOP. --scripted never runs vision_loop, so it had NO estop
    # gate at all: pressing the emergency stop parked the pump, and clearing
    # it resumed the canned routine mid-scrub on a person's forearm with
    # nobody having re-authorised anything. This is the fallback most likely
    # to be running against a real human, since it is what you switch to when
    # tracking fails.
    def stopped():
        if arm.estopped or getattr(arm, "abort_requested", False):
            return True
        return bool(should_stop and should_stop())

    def send(x, y, z, t=3.14):
        if stopped():
            raise _Aborted()
        arm.set_target(x, y, z, t)

    try:
        _run(send, stopped, fire, fire_reset, splotch_ts, arm, start_xyz,
             hover, z_mm, dur, use_water, contact_depth, settle)
    except _Aborted:
        print("[scripted] ABORTED — estop or stop requested")
        arm.go_home()


def _run(send, stopped, fire, fire_reset, splotch_ts, arm, start_xyz,
         hover, z_mm, dur, use_water, contact_depth, settle):
    # ALREADY (x, y, z) IN MILLIMETRES. It used to arrive as the arm's raw
    # target and get sliced [:3] here, which silently meant radians on a
    # 6-DOF arm. The slice is gone so there is nothing left to slice wrong.
    start = start_xyz
    if use_water:
        print("[scripted] dipping in bucket")
        motion.travel(send, start, (BUCKET[0], BUCKET[1], hover), 1.6)
        motion.travel(send, (BUCKET[0], BUCKET[1], hover),
                      (BUCKET[0], BUCKET[1], z_mm + 5), 0.8)
        time.sleep(0.6)
        motion.travel(send, (BUCKET[0], BUCKET[1], z_mm + 5),
                      (BUCKET[0], BUCKET[1], hover), 0.8)
        start = (BUCKET[0], BUCKET[1], hover)
    else:
        print("[scripted] dry sponge (no water — motors)")
        motion.travel(send, start, (FOREARM_MID[0], FOREARM_MID[1], hover), 1.6)
        start = (FOREARM_MID[0], FOREARM_MID[1], hover)

    print("[scripted] travelling to forearm")
    motion.travel(send, start, (FOREARM_MID[0], FOREARM_MID[1], hover), 1.4)
    motion.travel(send, (FOREARM_MID[0], FOREARM_MID[1], hover),
                  (FOREARM_MID[0], FOREARM_MID[1], z_mm + contact_depth), 1.0)

    print("[scripted] scrubbing")
    t0 = time.time()
    popped = 0
    while time.time() - t0 < dur:
        el = time.time() - t0
        off = motion.scrub_offset(el, dur)
        send(FOREARM_MID[0] + off, FOREARM_MID[1], z_mm + contact_depth)
        # Pop splotches on a schedule across the scrub window.
        want = int(len(splotch_ts) * min(el / (dur * 0.85), 1.0))
        while popped < want and popped < len(splotch_ts):
            fire("forearm_L", splotch_ts[popped],
                 100 * (popped + 1) / len(splotch_ts), True)
            popped += 1
            print(f"[scripted] splotch {popped}/{len(splotch_ts)}")
        time.sleep(0.025)

    while popped < len(splotch_ts):
        fire("forearm_L", splotch_ts[popped], 100, True)
        popped += 1

    print("[scripted] retreating")
    motion.travel(send, (FOREARM_MID[0], FOREARM_MID[1], z_mm + contact_depth),
                  (FOREARM_MID[0], FOREARM_MID[1], hover), 1.0)
    arm.go_home()
    time.sleep(settle)          # a parameter so tests need not wait 2.5s each
    fire_reset()
    print("[scripted] done")
