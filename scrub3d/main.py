"""scrub3d/main.py -- the entry point. Nothing here runs py/scrubbot.py.

    python scrub3d/main.py --preflight
    python scrub3d/main.py --scan scrub3d/data/scan01 --replay scrub3d/data/bag01

WHY ITS OWN ENTRY POINT
------------------------
`py/scrubbot.py` references `signal.SIGHUP` unconditionally at two places, and
SIGHUP does not exist on Windows: importing and running it raises AttributeError
at startup. We never run it, so that never fires. Recorded here so nobody
"fixes" it by editing a file this project is not allowed to touch.

The consequence is that its shutdown handling has to be re-earned. Windows
does not deliver SIGTERM except through an explicit os.kill, so a console
handler is registered instead, and Ctrl-C, Ctrl-Break and closing the window
all stop the run. What shutdown reports is only what it did: this entry point
connects no arm, so there is nothing to retreat, and it says so.

PYTHON 3.11 OR BETTER, AND IT IS NOT A STYLE PREFERENCE. Below 3.11 the Windows
timer resolution is about 15.6ms, which cannot sustain a 40Hz control pump: the
loop would tick at whatever the scheduler felt like and the timing model would
become fiction while continuing to print numbers.

PRE-FLIGHT: ALL OF THEM, OR NOTHING MOVES
------------------------------------------
Eight checks. Several need hardware and several need a human to look at
something, and this reports those as UNKNOWN rather than passing them by
default. A check that silently passes because it could not run is worse than
no check, because it is indistinguishable from one that ran.
"""
import argparse
import os
import sys

import numpy as np

try:
    from . import collide as C
    from . import fleet as FLEET
    from . import frames as FRAME
    from . import kinematics as K
    from . import partition as P
    from . import rsfeed
    from . import session as SESSION
except ImportError:
    import collide as C
    import fleet as FLEET
    import frames as FRAME
    import kinematics as K
    import partition as P
    import rsfeed
    import session as SESSION

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


CONSOLE_EVENTS = {0: "Ctrl-C", 1: "Ctrl-Break", 2: "console closed",
                  5: "logoff", 6: "system shutdown"}


def _install_shutdown(on_shutdown):
    """Make Ctrl-C, Ctrl-Break, closing the console and logoff STOP the run.

    Windows never sends SIGTERM of its own accord, so the console control
    handler is the only route by which a window close reaches us.

    Calling on_shutdown is not stopping. Both handlers used to do only that:
    the SIGINT one replaced Python's own, and the console one returned True,
    which tells Windows the event was dealt with. Ctrl-C printed a message and
    the loop ran on to its last frame.

    Now a stop is a request first. While the live loop is reading frames, the
    feed ends at the next one, so the loop returns and still prints what it
    did. At any other moment -- scanning, pre-flight -- nothing would see the
    request, so the main thread gets a KeyboardInterrupt, which main() turns
    into an orderly exit. Asking twice always interrupts.

    The console handler runs on a thread Windows creates, so it cannot raise
    into the main thread itself; _thread.interrupt_main() does that for it.
    For a closed console, logoff or shutdown, Windows ends the process once the
    handler returns, so it waits a little for the main thread to finish first.
    """
    import _thread
    import signal
    import time

    def _request(why):
        """-> True if the running loop will see the request by itself."""
        on_shutdown(why)
        again = rsfeed.STOP.is_set()
        rsfeed.STOP.set()
        return rsfeed.feeding() and not again

    def _sig(signum, _frame):
        if not _request(signal.Signals(signum).name):
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sig)
    if os.name != "nt":
        signal.signal(signal.SIGTERM, _sig)
        return "posix signals"
    try:
        import ctypes
        HANDLER = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

        def _h(evt):
            if not _request(CONSOLE_EVENTS.get(evt, f"console event {evt}")):
                _thread.interrupt_main()
            if evt not in (0, 1):
                time.sleep(3.0)
            return True

        _install_shutdown._ref = HANDLER(_h)      # must outlive the call
        ctypes.windll.kernel32.SetConsoleCtrlHandler(
            _install_shutdown._ref, True)
        return "SetConsoleCtrlHandler"
    except Exception as exc:                                    # noqa: BLE001
        return f"console handler unavailable ({exc})"


def preflight(scan_dir=None, layout=None, calibrations=None,
              have_hardware=False, neck_z_mm=None, governor=None,
              terr=None, body=None, phases=None):
    """The eight checks. -> list of (name, status, detail).

    Returns rather than raises, so an operator sees every failure at once
    instead of the first one.
    """
    out = []

    def add(name, status, detail):
        out.append((name, status, detail))

    # 1. Arms alive.
    add("arms report link_ok and a fresh T:105",
        UNKNOWN if not have_hardware else FAIL,
        "needs hardware; link_ok counts consecutive WRITE failures only, so "
        "add feedback staleness or a wedged ESP32 reads as healthy")

    # 2. Torque caps.
    add("torque caps re-asserted within 10s",
        UNKNOWN if not have_hardware else FAIL,
        "needs hardware; a brownout-reset arm returns at the firmware default "
        "of 1000, about 16x the intended shoulder torque")

    # 3. Calibrations agree about one point in the room.
    if calibrations:
        try:
            from . import handeye
        except ImportError:
            import handeye
        spread, ok = handeye.cross_validate(
            [c["cal"] for c in calibrations],
            [c["fiducial_arm_xyz"] for c in calibrations])
        add("all arm calibrations agree on one fiducial",
            PASS if ok else FAIL, f"{spread:.1f}mm spread, limit {handeye.GO_MM}mm")
    else:
        add("all arm calibrations agree on one fiducial", UNKNOWN,
            "no calibrations supplied; without this the collision model "
            "between arms is fiction")

    # 4. The body model fits the person in front of us.
    if scan_dir and os.path.isdir(scan_dir):
        try:
            rig = FRAME.solve(scan_dir)
            add("floor fit and world frame", PASS,
                f"{rig['floor']['rms_mm']:.1f}mm residual, camera "
                f"{rig['floor']['camera_height_mm']:.0f}mm up, "
                f"{rig['floor']['pitch_down_deg']:.1f} deg down")
        except Exception as exc:                                # noqa: BLE001
            add("floor fit and world frame", FAIL, str(exc))
    else:
        add("floor fit and world frame", UNKNOWN, "no scan supplied")

    # 5. The person is where the scan says.
    add("person seated as scanned", UNKNOWN if not have_hardware else FAIL,
        "needs a live frame to compare against the scan; mark the floor")

    # 6. Offline collision self-test, perturbed. THIS ONE WE CAN ACTUALLY RUN.
    if layout is not None and terr is not None and body is not None:
        worst, bad, n = _perturbed_collision_test(layout, terr, body,
                                                  phases)
        together = [g for g in (phases or [list(range(len(layout)))])
                    if len(g) > 1]
        name = "collision self-test, 25mm perturbation on THE PLAN"
        # "PASS, 0 tested" used to cover two different things. When no two
        # arms ever move at once there is nothing to collide, which is a pass
        # worth saying plainly. When pairs do move together but no pose could
        # be drawn, nothing was tested, and that is not a pass at all.
        if not together:
            add(name, PASS,
                "no two arms move at the same time in this plan, so there is "
                "no pair to collide; nothing needed sampling")
        elif n == 0:
            add(name, UNKNOWN,
                f"the arm groups {together} move together, but none of their "
                f"sampled targets was reachable, so nothing was tested")
        else:
            add(name, PASS if bad == 0 else FAIL,
                f"{n} pose sets drawn from each arm's own territory, tested "
                f"within each concurrency phase {together}; {bad} came inside "
                f"D_ESTOP; worst separation {worst:.0f}mm")
    else:
        add("collision self-test, 25mm perturbation", UNKNOWN,
            "needs the layout, the territories and the body")

    # 7. A physical cut-off. Software cannot verify this and must not pretend.
    add("hardwired E-stop cutting 12V, independent of software", UNKNOWN,
        "a human has to look at it. Four arms near a person is where a "
        "hardware kill stops being optional")

    # 8. The head, designed out at the constraint level.
    #
    # Reachability alone always fails this, and that is the point: every one of
    # these arms CAN put its tool point above the neck. The requirement is
    # therefore a constraint, not a hope about the planner, so what gets
    # checked is that the governor actually refuses -- it being the only thing
    # every command passes through.
    if layout is not None and neck_z_mm is not None:
        reach = [a for a, T in enumerate(layout)
                 if _can_reach_above(T, neck_z_mm)]
        if governor is None or governor.z_ceiling is None:
            add("no arm can put the sponge above the neck", FAIL,
                f"arms {reach} reach above z={neck_z_mm:.0f}mm and no ceiling "
                f"is installed in the governor")
        elif governor.z_ceiling > neck_z_mm:
            add("no arm can put the sponge above the neck", FAIL,
                f"the governor ceiling is {governor.z_ceiling:.0f}mm, above "
                f"the neck at {neck_z_mm:.0f}mm")
        else:
            held = _ceiling_holds(governor, neck_z_mm)
            add("no arm can put the sponge above the neck",
                PASS if held else FAIL,
                f"{len(reach)} arms can reach there and the governor refused "
                f"every proposal above {governor.z_ceiling:.0f}mm" if held
                else "a proposal above the ceiling was accepted")
    else:
        add("no arm can put the sponge above the neck", UNKNOWN,
            "needs a layout and the neck height from the scan")

    return out


def _perturbed_collision_test(layout, terr, body, phases=None, n=200,
                              jitter_mm=25.0, seed=0):
    """Test THE PLAN, perturbed. -> (worst_mm, n_bad, n_tested).

    Sampling the whole workspace tests a machine nobody intends to build. Of
    course four unconstrained arms collide: measured that way, 123 of 240 poses
    came inside D_ESTOP and the check was answering a question no one asked.
    What has to be clear is the plan -- each arm somewhere in ITS OWN
    territory, which is where it will actually be.

    AND ONLY THE ARMS THAT RUN TOGETHER. The phase schedule is not decoration;
    it IS the mechanism that keeps interlocking territories apart, and a check
    that ignores it tests a machine the scheduler will never produce. Measured
    on the searched rig, which runs as [[0,1],[2,3]]: all four at once put 171
    of 196 poses inside D_ESTOP with a worst separation of -126mm, while the
    pairs that actually run together are clear. Failing on that would have
    rejected a good rig for a collision the schedule forbids -- and, worse, a
    layout that passed this way would have been trusted for the wrong reason.

    The 25mm perturbation is the point. A plan clear only at the exact poses it
    was checked at is not clear, and 25mm is the plan's own model-error budget:
    kinematics 10, skeleton and mesh 15, base calibration 10.
    """
    rng = np.random.default_rng(seed)
    L = [np.asarray(T, float) for T in layout]
    fl = C.Fleet(L)
    Pw, Nw, _, _ = body.world_cells()
    own = {a: np.where(terr.owner == a)[0] for a in range(len(L))}
    home = K.ik(235.11, 0.0, 234.79)
    groups = [list(g) for g in (phases or [list(range(len(L)))]) if len(g) > 1]

    worst, bad, tested = float("inf"), 0, 0
    for _ in range(n):
        states, ok = [], True
        for a in range(len(L)):
            idx = own.get(a)
            if idx is None or len(idx) == 0:
                states.append(home)
                continue
            i = int(rng.choice(idx))
            tgt = Pw[i] + Nw[i] * 110.0 + rng.normal(0, jitter_mm / 1.732, 3)
            local = (np.linalg.inv(L[a]) @ np.r_[tgt, 1.0])[:3]
            j = K.ik(*local)
            if j is None:
                ok = False
                break
            states.append(j)
        if not ok:
            continue
        d = fl.pair_distances(states)
        for g in groups:
            inside = [v for (i, j), v in d.items() if i in g and j in g]
            if not inside:
                continue
            m = min(inside)
            worst = min(worst, m)
            bad += int(m < C.D_ESTOP)
            tested += 1
    return (worst if tested else float("nan")), bad, tested


def _ceiling_holds(gov, neck_z_mm, samples=600, seed=0):
    """Does the governor refuse everything above its ceiling? -> bool."""
    rng = np.random.default_rng(seed)
    for a in range(gov.n):
        T = gov.layout[a]
        for _ in range(max(samples // gov.n, 1)):
            j0 = rng.uniform(K.BASE_MIN_RAD, K.BASE_MAX_RAD)
            j1 = rng.uniform(K.SHOULDER_MIN_RAD, K.SHOULDER_MAX_RAD)
            j2 = rng.uniform(K.ELBOW_MIN_RAD, K.ELBOW_MAX_RAD)
            x, y, z = (float(v) for v in K.fk(j0, j1, j2))
            w = T[:3, :3] @ np.array([x, y, z]) + T[:3, 3]
            if w[2] <= neck_z_mm:
                continue
            if gov.propose(a, x, y, z, t=0.0).ok:
                return False
    return True


def _can_reach_above(T_base, z_mm, samples=4000, seed=0):
    """Could this arm put its tool point above z? -> bool."""
    rng = np.random.default_rng(seed)
    T = np.asarray(T_base, float)
    j0 = rng.uniform(K.BASE_MIN_RAD, K.BASE_MAX_RAD, samples)
    j1 = rng.uniform(K.SHOULDER_MIN_RAD, K.SHOULDER_MAX_RAD, samples)
    j2 = rng.uniform(K.ELBOW_MIN_RAD, K.ELBOW_MAX_RAD, samples)
    x, y, z = K.fk(j0, j1, j2)
    pts = np.c_[x, y, z] @ T[:3, :3].T + T[:3, 3]
    return bool((pts[:, 2] > z_mm).any())


def report(checks):
    """Print them, and say plainly whether anything may move."""
    print("\n  PRE-FLIGHT")
    width = max(len(n) for n, _, _ in checks)
    for name, status, detail in checks:
        print(f"    [{status:^7s}] {name:{width}s}")
        if detail:
            print(f"              {detail}")
    fails = [n for n, s, _ in checks if s == FAIL]
    unknowns = [n for n, s, _ in checks if s == UNKNOWN]
    print()
    if fails:
        print(f"  {len(fails)} FAILED. Nothing moves.")
    elif unknowns:
        print(f"  {len(unknowns)} could not be checked here. Nothing moves "
              f"until they are.")
    else:
        print("  all eight pass. Motion is authorised.")
    return not fails and not unknowns


def _record_and_segment(name, seconds):
    """Capture from the camera and segment it. -> the capture dir, or None.

    Every one of the eleven captures on disk had to pass `--rotate 180`,
    because the D455 on this rig is mounted upside down. record.py's default
    is now 180 for that reason, and this path does not expose the flag at all:
    a sideways capture produces sideways intrinsics and still looks like a
    plausible point cloud, which is the worst kind of wrong.
    """
    try:
        from . import record as REC
        from . import sapiens
    except ImportError:
        import record as REC
        import sapiens
    import numpy as _np

    out = os.path.join(FRAME.DATA, name)
    print(f"\n  recording {seconds:.0f}s into {out}")
    print("  sit facing the camera, forearms resting on your thighs, "
          "and hold still")
    try:
        REC.record(out, seconds=seconds, label=name, rotate="180",
                   preset="high_density")
    except Exception as exc:                       # noqa: BLE001
        print(f"  recording failed ({exc.__class__.__name__}: {exc})")
        return None

    print("  segmenting ...")
    try:
        import cv2
        bgr = cv2.imread(os.path.join(out, "color_median.png"))
        # The network's MEAN/STD are in RGB order and cv2 hands back BGR.
        seg = sapiens.shared(sapiens.SapiensSeg)(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        # numpy APPENDS .npy to a path that lacks it, so the temp name has to
        # end in .npy or os.replace chases a file never written under the name
        # it was given.
        tmp = os.path.join(out, "sapiens_seg.tmp.npy")
        _np.save(tmp, seg.astype(_np.int16))
        os.replace(tmp, os.path.join(out, "sapiens_seg.npy"))
        print(f"    wrote a {seg.shape} mask, "
              f"{len([c for c in _np.unique(seg) if c])} classes")
    except Exception as exc:                       # noqa: BLE001
        print(f"  segmentation failed ({exc.__class__.__name__}: {exc}); "
              f"scan.py needs it, so stopping rather than guessing")
        return None
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", default=os.path.join(FRAME.DATA, "scan01"))
    ap.add_argument("--replay", default="", help="a bag to follow, or blank "
                                                 "for the live camera")
    ap.add_argument("--preflight", action="store_true",
                    help="run the checks and stop")
    ap.add_argument("--frames", type=int, default=400)
    ap.add_argument("--record", metavar="NAME", default="",
                    help="capture a fresh scan from the camera under this "
                         "name, segment it, and use that instead of --scan")
    ap.add_argument("--seconds", type=float, default=10.0,
                    help="how long the person holds still for --record")
    ap.add_argument("--scrub-trunk", action="store_true",
                    help="also scrub the chest and back. OFF by default: the "
                         "capability works, but what a machine may press "
                         "against somebody's chest is a decision for whoever "
                         "is running it")
    ap.add_argument("--save", metavar="RRD", default="",
                    help="write the operator view to a file instead of "
                         "opening a window")
    a = ap.parse_args()

    if sys.version_info < (3, 11):
        print(f"  python {sys.version_info.major}.{sys.version_info.minor}: "
              f"below 3.11 the Windows timer resolution is ~15.6ms and cannot "
              f"sustain a 40Hz pump. Refusing.")
        return 2

    # Shutdown says what it did, and it can only do something about what this
    # run holds. No arm is connected to this entry point, so there is nothing
    # to retreat -- the message used to announce a retreat and a disarm that
    # never happened. Whoever connects an arm adds its retreat here, and
    # changes the sentence with it.
    held = {"consent": None, "done": False}

    def on_shutdown(why):
        if held["done"]:
            return
        held["done"] = True
        said = "no arm is connected to this run, so none needs retreating"
        if held["consent"] is not None:
            held["consent"].revoke(why)
            said += "; consent revoked"
        print(f"\n  shutdown ({why}): {said}", flush=True)

    how = _install_shutdown(on_shutdown)
    print(f"scrub3d   python {sys.version.split()[0]}   shutdown via {how}")
    try:
        return _session(a, held, on_shutdown)
    except KeyboardInterrupt:
        on_shutdown("interrupted")
        print("  stopped where it was; nothing after that point ran", flush=True)
        return 130


def _session(a, held, on_shutdown):
    """Everything main() does once Ctrl-C and console close can stop it."""
    try:
        from . import scan as SCAN
        from . import viz as VIZ
    except ImportError:
        import scan as SCAN
        import viz as VIZ

    # --- camera day: one command, not three ---------------------------------
    #
    # Without this, a fresh scan means running record.py, then
    # tools/segment_captures.py, then this -- three commands with a person
    # sitting still in front of the camera between them, and two chances to
    # forget the rotation flag. The steps are the same; what changes is that
    # they cannot be done out of order or half done.
    if a.record:
        a.scan = _record_and_segment(a.record, a.seconds)
        if a.scan is None:
            return 2

    print(f"\n  scanning {os.path.basename(a.scan)} ...")
    body, meshes, obstacles, rep = SCAN.scan(a.scan,
                                            scrub_trunk=a.scrub_trunk)
    layout = VIZ.default_layout()
    terr, planes, phases, prep = P.solve(body, layout, envelope_k=P.ENVELOPE_K,
                                         obstacle_points=obstacles,
                                         obstacle_region=rep["obstacle_region"])
    print(f"    {len(body.regions)} regions, "
          f"{rep['scrubbable_cm2']:.0f}cm2 scrubbable, measuring "
          f"{rep['clothing']['measuring']}")
    print(f"    coverage {100 * prep['covered_frac']:.1f}%, phases {phases}")

    neck_z = rep.get("shoulder_z_mm")
    consent = SESSION.Consent()
    held["consent"] = consent
    # The ceiling sits at the acromion, not the crown. The neck is the part
    # that must never be touched, and it begins at the shoulders.
    gov = FLEET.FleetGovernor(layout, body_points=obstacles,
                              z_ceiling_mm=neck_z)

    checks = preflight(scan_dir=a.scan, layout=layout, neck_z_mm=neck_z,
                       governor=gov, terr=terr, body=body,
                       phases=phases)
    may_move = report(checks)
    print(f"\n  consent: {consent.state()[0]}   governor: {gov.n} arms, "
          f"{len(obstacles)} body points")

    if a.preflight:
        return 0 if may_move else 1

    if not may_move:
        print("\n  Pre-flight is not satisfied, so NOTHING WILL MOVE. The "
              "reconstruction\n  and the operator view still run: that is the "
              "degradation ladder working,\n  not a failure.")

    print(f"\n  opening the operator view "
          f"({'replaying ' + os.path.basename(a.replay) if a.replay else 'live camera'})")
    # WITHOUT THIS THE VIEWER NEVER OPENS. viz.live only logs; creating the
    # recording stream is the caller's job, and viz.main() did it while this
    # entry point did not -- so the whole run worked, printed every number,
    # tracked every frame, and showed nobody anything.
    import rerun as rr
    if a.save:
        rr.init('wheelgentic3d', spawn=False)
        rr.save(a.save)
    else:
        rr.init('wheelgentic3d', spawn=True)

    # The body, obstacles and territories printed and pre-flighted above are
    # the ones the loop follows. Left to itself, viz.live scanned the capture a
    # second time with the defaults, so --scrub-trunk never reached the loop
    # and a replay paid for the scan and the partition twice.
    s = VIZ.live(a.scan, a.replay or None, layout=layout, max_frames=a.frames,
                 scanned=(body, meshes, obstacles, rep),
                 plan=(terr, planes, phases, prep))
    if a.save:
        print(f'    wrote {a.save}')
    print(f"    {s['tracked']}/{s['frames']} frames tracked, "
          f"{s['frozen']} frozen by the motion guard")
    if rsfeed.STOP.is_set():
        # Asked to stop mid-loop: the loop ended at a frame boundary and said
        # what it did, and the exit code still says it did not finish.
        print(f"    stopped as asked, after {s['frames']} of {a.frames} frames")
        return 130
    on_shutdown("normal exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
