"""tests/test_estop_cartoon.py — the estop must stop the SCREEN, not just the arm.

TWO CRITICAL BUGS, both on the estop, both found by an adversarial audit of code
that a 24-test green suite had already passed.

(1) 'x' froze the real arm and left the cartoon scrubbing. The stroke interval
    lived inside startScrubChoreography()'s closure as `const iv = setInterval`,
    so nothing outside could reach it -- the on-screen arm kept stroking a
    volunteer's forearm for the rest of a 9.8s window while the machine was
    stopped. The presenter says "that's the emergency stop" and the projector
    behind them disagrees.

(2) 's' while estopped played a FULL fake scrub. The server correctly refuses to
    arm, but the page started the choreography optimistically and never listened
    for the refusal: green ARMED banner, cartoon arm travels in, scrubs. The
    projector is the only surface the operator and the whole room are watching,
    and it was lying about the state of a machine that had been emergency
    stopped.

WHAT TO MEASURE. The stroke lives on the forearm group's rotation.z. Measure
THAT, not the sponge's world position: setPhase('rest') makes the arm TRAVEL
back to its parked pose, which is real, large movement, and three separate
probes for this fix mistook that retreat for a scrub and reported the bug fixed
while it was still there.
"""
import asyncio, os, subprocess, signal, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(os.path.join(HERE, ".."))
import serve; serve.ensure()
import watchdog; watchdog.arm(150)
from playwright.async_api import async_playwright

PY = next((c for c in ("venv/bin/python", "/tmp/sbtest/bin/python")
           if os.path.exists(c)), "python3")
FAILS = []

# Every Group in the arm; the stroke shows up as rotation.z on the forearm.
_Z = """()=>{const zs=[]; window.__wheelgentic.robot.root.traverse(
  o=>{if(o.type==='Group') zs.push(o.rotation.z);}); return zs;}"""


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(label)


def free_8765():
    subprocess.run(["pkill", "-9", "-f", "py/scrubbot.py"], capture_output=True)
    for _ in range(8):
        held = subprocess.run(["lsof", "-ti", "tcp:8765"],
                              capture_output=True, text=True).stdout.split()
        if not held:
            return
        for pid in held:
            subprocess.run(["kill", "-9", pid], capture_output=True)
        time.sleep(0.4)


async def main():
    free_8765()
    log = open("/tmp/_estop_cartoon.log", "w")   # a file, never a pipe
    proc = subprocess.Popen(
        [PY, "py/scrubbot.py", "--no-arm", "--headless"],
        stdout=log, stderr=subprocess.STDOUT, text=True,
        preexec_fn=os.setsid, env=dict(os.environ, CAM="fake"))
    time.sleep(4.0)
    if proc.poll() is not None:
        log.close(); sys.exit("scrubbot died:\n" + open("/tmp/_estop_cartoon.log").read()[:1500])

    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True, args=["--use-angle=metal", "--enable-unsafe-swiftshader"])
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(2500)

        async def stroke_span(samples=10, gap=200):
            rows = []
            for _ in range(samples):
                rows.append(await pg.evaluate(_Z))
                await pg.wait_for_timeout(gap)
            return max(max(r[i] for r in rows) - min(r[i] for r in rows)
                       for i in range(len(rows[0])))

        async def hud():
            return await pg.evaluate("()=>document.getElementById('link').textContent")

        # THE LINK IS A PRECONDITION, NOT DECORATION. Without it 's' does
        # nothing, every span reads 0.0000, and BOTH estop assertions go green
        # while proving nothing. That exact false pass happened once already.
        link = await hud()
        check("the browser linked to Python", "LINKED" in link, repr(link))
        if "LINKED" not in link:
            print("\n  ABORTING: with no link, 's' is a no-op and every span "
                  "below would read 0 and PASS vacuously.")
            await b.close(); log.close()
            try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception: pass
            sys.exit(1)
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(600)

        print("\n=== 1. 'x' STOPS THE ON-SCREEN SCRUB ===")
        # DO NOT GATE THIS ON isCycleLive(). Measured, 0.4s windows from the
        # keypress: the BROWSER's choreography strokes from t+0.4 to t+7.8s
        # with live=False the entire time, and the FSM's own cycle only sets
        # live=True at t+9.8s, by which point that second scrub is 2s from
        # ending. Waiting for the flag put every sample in the tail of the
        # wrong scrub, so the section-1 probe read ~0 whether the estop worked
        # or not -- it passed with the bug planted straight back in.
        #
        # The choreography IS the thing 'x' has to cancel, so sample it from
        # the keypress with no wait.
        await pg.keyboard.press("s")
        during = await stroke_span()
        # The positive control. If this is 0 the scrub never started and the
        # 'after' reading below is meaningless.
        check("the stroke IS running during a scrub", during > 0.05,
              f"span {during:.4f}"
              + ("" if during > 0.05 else " — no scrub to stop; test is blind"))

        # stroke_span() above consumed ~2s of the ~7.8s window, so the
        # choreography is still mid-stroke here -- which is the only moment at
        # which 'x' cancelling it means anything.
        await pg.keyboard.press("x")
        await pg.wait_for_timeout(1200)      # let the retreat travel settle
        after = await stroke_span()
        check("and 'x' STOPS it", after < 0.01,
              f"span {after:.4f}"
              + ("" if after < 0.01 else " — CARTOON STILL SCRUBBING AFTER ESTOP"))

        print("\n=== 2. 's' WHILE ESTOPPED DOES NOT FAKE A SCRUB ===")
        await pg.keyboard.press("s")
        await pg.wait_for_timeout(2500)
        faked = await stroke_span()
        check("arming while estopped plays NO scrub", faked < 0.01,
              f"span {faked:.4f}"
              + ("" if faked < 0.01 else " — THE PROJECTOR IS LYING ABOUT THE ARM"))
        # WAIT FOR THE BANNER, DO NOT SAMPLE ONCE. #link carries both the
        # estop refusal and the socket's own link state, and a link update
        # inside the 2.5s window above can overwrite the refusal before this
        # reads it. Seen in a full suite run: this went red with
        # 'ARM ● LINKED' while passing alone, which is the shape of a race
        # rather than a regression.
        try:
            await pg.wait_for_function(
                "() => (document.getElementById('link')?.textContent || '')"
                "        .includes('STILL STOPPED')", timeout=6000)
        except Exception:
            pass                      # fall through to the check's own message
        h = await hud()
        check("and the HUD says what to press instead",
              "STILL STOPPED" in h, repr(h))

        # Both halves of the refusal must exist: the server has to SEND it and
        # the page has to ACT on it. A page handler for an ack nothing emits is
        # dead code that passes a behavioural test by luck of ordering.
        _sb = open("py/scrubbot.py").read()
        check("the FSM refuses to arm while estopped",
              '"cmd": "arm", "ok": False' in _sb,
              "no refusal is sent, so the page has nothing to act on")
        _mj = open("web/main.js").read()
        check("the page acts on the refusal",
              "stopScrubChoreography" in _mj and "'arm'" in _mj,
              "the refusal would arrive and be ignored")
        # And the interval must be reachable from outside its closure -- that
        # was the whole of bug (1).
        check("the stroke interval is cancellable from outside",
              "let choreoInterval" in _mj,
              "a const inside the closure cannot be cleared by the estop")

        print("\n=== 3. CLEARING RECOVERS ===")
        # An estop the operator cannot walk back from is its own demo-ender.
        # Shift+C, not "C". pg.keyboard.press("C") sends KeyC with NO shift
        # modifier, the page's `e.shiftKey` check correctly rejects it, and the
        # server log shows no CLEAR ever arrived -- a harness bug that reads
        # exactly like a broken recovery path.
        await pg.keyboard.press("Shift+KeyC")
        await pg.wait_for_timeout(2000)
        await pg.keyboard.press("s")
        live2 = False
        for _ in range(30):
            await pg.wait_for_timeout(400)
            if await pg.evaluate("()=>window.__wheelgentic.isCycleLive()"):
                live2 = True
                break
        check("a cycle arms again after shift+C", live2,
              "estop is a one-way door — the demo is over")
        recov = await stroke_span()
        check("and the cartoon scrubs again", recov > 0.05, f"span {recov:.4f}")

        print("\n=== 4. THE FEEDING ARM STOPS TOO ===")
        # The feed beat runs on `modeTimers`, a list added to main.js after
        # this test was written, and stopScrubChoreography cleared only the
        # scrub's own timers. So `x` during feeding parked the arms and then
        # the pending serve() fired ~1.6s later and sent the arm back to the
        # person's mouth. After an emergency stop.
        #
        # WHAT IS WATCHED CHANGED, WHAT IS BEING TESTED DID NOT. This used to
        # read the soup bowl's visibility. The bowl, the glass and the spoon
        # were all removed on 2026-09-20 -- the arms carry nothing now and the
        # sponge is the only prop on screen -- so the instrument had to become
        # something that still exists.
        #
        # The counter is the better one anyway: it is incremented only when
        # the arm completes a round trip to the person's face, so a count that
        # moves after the stop IS the arm moving after the stop, with no mesh
        # in between to be visible for some other reason.
        #
        # The waits are deliberately long. The whole failure is something that
        # happens AFTER everything looks stopped, and one round trip of this
        # arm takes about twelve seconds, so a short wait passes on broken
        # code.
        # Runs AFTER section 3, which already cleared the estop and proved a
        # cycle arms again -- so the machine is live here, which is the only
        # state in which this check means anything.
        await pg.keyboard.press("x")          # stop the cycle section 3 started
        await pg.wait_for_timeout(600)
        await pg.keyboard.press("Shift+KeyC")
        await pg.wait_for_timeout(1200)
        await pg.keyboard.press("8")          # feed: the arm reaches
        # WATCH THE ARM, NOT THE WORDS. The readout was the instrument here
        # for a while, but with the counter gone it settles on one idle string
        # -- so comparing it before and after would pass even if the estop did
        # nothing at all. The claw's own position cannot be idle-identical
        # through a trip, so that is what gets measured.
        claw = ("()=>{const W=window.__wheelgentic;const a=W.fleet[1].arm;"
                "const V=W.camera.position.constructor;const t=a.toolWorld(new V());"
                "return [t.x,t.y,t.z];}")

        async def spread(ms, step=400):
            """How far the claw travels over `ms`. An arm on a trip sweeps
            about a unit; a stopped one sits inside a few thousandths."""
            pts = []
            for _ in range(max(2, ms // step)):
                pts.append(await pg.evaluate(claw))
                await pg.wait_for_timeout(step)
            far = 0.0
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    d = sum((pts[i][k] - pts[j][k]) ** 2 for k in range(3)) ** 0.5
                    far = max(far, d)
            return far

        # Ask for a trip and prove the arm is genuinely travelling first,
        # or the check below is asserting against something already still.
        await pg.keyboard.press("8")
        moving = await spread(5000)
        check("the feeding arm is travelling before the estop",
              moving > 0.20, f"claw moved {moving:.3f} over five seconds")
        await pg.keyboard.press("x")
        await pg.wait_for_timeout(2500)     # let the springs settle
        stopped = await spread(6000)
        check("and 'x' stops it and it stays stopped", stopped < 0.05,
              f"claw moved {stopped:.3f} over six seconds after the stop")

        check("no page errors", not errs, str(errs))
        await b.close()

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGINT); proc.wait(timeout=5)
    except Exception:
        try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception: pass
    log.close()

asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
print("  THE ESTOP STOPS THE SCREEN, NOT JUST THE ARM")
