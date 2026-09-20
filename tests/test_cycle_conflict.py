"""tests/test_cycle_conflict.py — arming a cycle AND tapping 1/2/3.

FOUND WHILE RECORDING THE BACKUP VIDEO. Pressing 's' arms a real FSM cycle that
finishes on its own in scrub_seconds and whose RETREAT calls fire_reset(),
sending reset=true. Tapping 1/2/3 during that window makes the counter
oscillate (measured: 1 -> 0 -> 1 gone) as the cycle resets underneath the
operator.

Both paths are correct in isolation. Doing both is operator error -- and an
operator under demo pressure, watching splotches not pop, will absolutely reach
for 1/2/3 while a cycle is running.

THE FIX IS A WARNING, NOT A BLOCK. 1/2/3 is the recovery card's answer to
"Python crashed mid-demo" and must never be gated on socket state; the whole
point is that it works when everything else is dead. So the page tracks whether
a cycle is live and says so.
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

# The sponge's world position, via the debug handle rather than a colour hunt.
# READ THE EXPOSED HANDLE, not a colour match. This used to hunt the scene for a
# mesh whose material colour matched the sponge accent BY VALUE -- the only such
# hunt in the suite, and the exact fragility __wheelgentic's own comment says the
# `robot` handle exists to prevent. It also duplicated the colour constant from
# robotarm.js with NO guard pinning the two, the same unpinned cross-surface
# shape as the pump rate. makeRobotArm now exposes `sponge`, so neither the
# literal nor the lookup survives here and a retint cannot silently move what
# this measures. (Deliberately NOT restating the hex value or the accessor: the
# point is to delete the duplicate, not to describe it in a comment a grep would
# still find.)
_SPONGE = """()=>{const S=window.__wheelgentic;
  const sp = S.robot.sponge;
  if (!sp) throw new Error("robot.sponge is not exposed -- see robotarm.js");
  const v=new (S.camera.position.constructor)(); sp.getWorldPosition(v);
  return [v.x,v.y,v.z];}"""


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
    log = open("/tmp/_cycle_conflict.log", "w")   # a file, never a pipe
    proc = subprocess.Popen(
        [PY, "py/scrubbot.py", "--no-arm", "--headless"],
        stdout=log, stderr=subprocess.STDOUT, text=True,
        preexec_fn=os.setsid, env=dict(os.environ, CAM="fake"))
    time.sleep(4.0)
    if proc.poll() is not None:
        log.close(); sys.exit("scrubbot died:\n" + open("/tmp/_cycle_conflict.log").read()[:1500])

    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True, args=["--use-angle=metal", "--enable-unsafe-swiftshader"])
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(2500)
        link = await pg.evaluate("()=>document.getElementById('link').textContent")
        check("the browser linked to Python", "LINKED" in link, repr(link))
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(600)

        print("\n=== 1. NO CYCLE RUNNING: 1/2/3 pops, no warning ===")
        await pg.keyboard.press("1")
        await pg.wait_for_timeout(700)
        gone = await pg.evaluate("()=>window.__wheelgentic.recs.filter(r=>r.gone).length")
        hud = await pg.evaluate("()=>document.getElementById('link').textContent")
        check("it pops with no cycle running", gone == 1, f"{gone} gone")
        # Same token as the positive at case 2. Left as "CYCLE RUNNING" this
        # would assert the absence of a string the HUD can no longer emit --
        # a guard that cannot fail, passing beside an honest failure.
        check("and shows NO cycle warning", "RESET" not in hud.upper(), repr(hud))
        await pg.keyboard.press("r")
        await pg.wait_for_timeout(600)

        print("\n=== 2. CYCLE RUNNING: 1/2/3 still pops, and WARNS ===")
        await pg.keyboard.press("s")
        # Wait for a REAL cycle rather than guessing at timing -- an earlier
        # version of this test polled a probe that did not exist, so the wait
        # was a no-op and the keypress landed AFTER the cycle had finished.
        live = False
        for _ in range(30):
            await pg.wait_for_timeout(400)
            if await pg.evaluate("()=>window.__wheelgentic.isCycleLive()"):
                live = True
                break
        # The detail has to agree with the verdict. Printed unconditionally,
        # this said "the rest proves nothing" on a PASSING run, which is the
        # opposite of what happened.
        check("a live cycle was actually observed", live,
              "observed" if live
              else "isCycleLive() never went true — the rest proves nothing")
        await pg.keyboard.press("2")
        await pg.wait_for_timeout(400)
        gone = await pg.evaluate("()=>window.__wheelgentic.recs.filter(r=>r.gone).length")
        hud = await pg.evaluate("()=>document.getElementById('link').textContent")
        # NOT BLOCKED is the load-bearing half: gating 1/2/3 on socket state
        # would break the crash fallback, which is the only thing that works
        # when Python is dead.
        check("the manual pop was NOT blocked", gone >= 1, f"{gone} gone")
        # MATCH THE LOAD-BEARING WORD, NOT THE DECORATION. This asserted
        # "CYCLE RUNNING", which the flash no longer says: the phase line
        # top-left says that from the 's' keypress, so repeating it in the
        # top-right warning put the same two words in both corners during the
        # rescue beat. RESET is the only word unique to this message across all
        # 19 flash strings, so it survives cosmetic rewording and still proves
        # the warning fired rather than some other banner.
        check("the HUD warns the cycle will reset it",
              "RESET" in hud.upper(), repr(hud))

        print("\n=== 3. PYTHON DIES MID-CYCLE: the flag must not latch ===")
        # The crash fallback is the one path that must never be noisy. The flag
        # only ever cleared on m.reset, which a killed Python never sends, so
        # it latched true and every manual 1/2/3 would warn about a dead cycle.
        # It LOOKED fine only because flag(false) writes ARM ○ MANUAL into the
        # same element and overwrote the warning 1.6s later -- a coin flip.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass          # already gone; the socket close is what matters
        await pg.wait_for_timeout(3000)
        still = await pg.evaluate("()=>window.__wheelgentic.isCycleLive()")
        check("the cycle flag cleared when the socket died", not still,
              "isCycleLive() latched true after SIGKILL")
        await pg.keyboard.press("3")
        await pg.wait_for_timeout(700)
        hud = await pg.evaluate("()=>document.getElementById('link').textContent")
        check("a manual pop after the crash does NOT warn",
              "RESET" not in hud.upper(), repr(hud))
        check("and the HUD says the socket is down",
              "MANUAL" in hud, repr(hud))

        print("\n=== 4. UV MODE: 1/2/3 must follow the MOVED splotches ===")
        # UV latches wherever the tracer actually is and the page moves its
        # splotches there. The manual keys indexed a hardcoded SPLOTCH_TS
        # array, and popNearest() rejects anything past 0.07 -- so in UV mode
        # 1/2/3 popped 0 of 3 with the counter stuck at 0%. The rehearsed
        # fallback, dead in exactly the mode a judge asks you to switch to.
        await pg.evaluate("()=>window.__wheelgentic.resetAll()")
        await pg.wait_for_timeout(400)
        await pg.evaluate("()=>window.__wheelgentic.placeSplotches([0.11,0.25,0.75])")
        await pg.wait_for_timeout(400)
        moved = await pg.evaluate("()=>window.__wheelgentic.recs.map(r=>+r.t.toFixed(2))")
        check("the splotches moved to the detected positions",
              moved == [0.11, 0.25, 0.75], str(moved))
        for k in ("1", "2", "3"):
            await pg.keyboard.press(k)
            await pg.wait_for_timeout(350)
        gone = await pg.evaluate("()=>window.__wheelgentic.recs.filter(r=>r.gone).length")
        pct = await pg.evaluate("()=>document.getElementById('pct').textContent")
        check("all three manual keys pop in UV mode", gone == 3, f"{gone}/3 gone")
        check("and the counter reaches 100%", pct == "100%", pct)

        print("\n=== 5. THE CUT-POWER BANNER MUST NOT BE ERASED ===")
        # MEASURED: "ESTOP FAILED — CUT POWER AT THE SUPPLY" was gone in under
        # 150ms, replaced by ARM ○ MANUAL. flashHold() cleared its own timer
        # but nothing stopped flag() -- called from ws.onopen, ws.onclose and
        # the 500ms reconnect retry -- from writing the same element. The most
        # safety-critical string in the UI, erased at the exact moment the arm
        # did NOT stop.
        await pg.evaluate("()=>window.__wheelgentic.flashHold("
                          "'ESTOP FAILED — CUT POWER AT THE SUPPLY')")
        for _ in range(6):
            await pg.wait_for_timeout(500)     # spans several reconnect ticks
        banner = await pg.evaluate("()=>document.getElementById('link').textContent")
        check("the cut-power banner survives 3s of reconnect churn",
              "CUT POWER" in banner, repr(banner))
        # A deliberate confirmed action supersedes it; nothing else may.
        await pg.evaluate("()=>window.__wheelgentic.releaseHold()")
        await pg.wait_for_timeout(900)

        print("\n=== 6. A DEAD ARM LINK REACHES THE PROJECTOR ===")
        # The FSM drops to IDLE and stops advancing the counter when the arm
        # stops acknowledging writes. EVENT["link"] carried that news and the
        # page never read it -- so the projector showed NOTHING, and the
        # operator (watching the projector on stage, not the OpenCV window
        # behind it) had no idea why the demo stopped or that 1/2/3 was now
        # the only way forward. Drive the real message shape.
        await pg.evaluate("()=>window.__wheelgentic.releaseHold()")
        await pg.wait_for_timeout(300)
        await pg.evaluate("""()=>{
            window.__wheelgentic.flashHold('ARM LINK LOST — check USB, use 1 2 3');
        }""")
        await pg.wait_for_timeout(2500)
        hud = await pg.evaluate("()=>document.getElementById('link').textContent")
        check("the ARM LINK LOST banner holds on the projector",
              "ARM LINK LOST" in hud, repr(hud))
        # AND the server actually emits it -- a banner nothing triggers is
        # decoration. Checked against the real source, both halves.
        _sb = open("py/scrubbot.py").read()
        check("the FSM sets EVENT['link'] when the arm goes quiet",
              'EVENT["link"] = "arm-lost"' in _sb)
        check("and drains it like the other one-shots",
              'EVENT["link"] = None' in _sb,
              "left sticky, a recovered arm reports itself lost forever")
        _mj = open("web/main.js").read()
        check("the page handles m.link", "m.link === 'arm-lost'" in _mj,
              "the server would be shouting into a socket nobody reads")
        await pg.evaluate("()=>window.__wheelgentic.releaseHold()")

        print("\n=== 7. A LINK WARNING MUST NOT FREEZE THE HUD ===")
        # The held latch that stopped the CUT POWER banner being erased also
        # froze the HUD: press 'x' with Python down, get "NO LINK — HIT SPACE
        # IN THE PYTHON WINDOW", and only flash() releases it -- but flash()
        # fires on a server ack, which cannot arrive while the socket is down.
        # Measured: Python restarted, socket reconnected, HUD still read NO
        # LINK. The operator's only link indicator, dead for the rest of the
        # demo, because of the fix that protected a different banner.
        #
        # The split: a banner ABOUT THE LINK is answered by the link coming
        # back; a banner about the ARM is not.
        await pg.evaluate("()=>window.__wheelgentic.flashHold("
                          "'NO LINK — HIT SPACE IN THE PYTHON WINDOW', true)")
        await pg.wait_for_timeout(300)
        check("a link warning shows", "NO LINK" in await pg.evaluate(
            "()=>document.getElementById('link').textContent"))
        # Simulate the reconnect the same way ws.onopen does.
        await pg.evaluate("()=>{ if (window.__wheelgentic.isHeld()) "
                          "window.__wheelgentic.releaseHold(); }")
        await pg.wait_for_timeout(300)
        check("and a restored link clears it",
              not await pg.evaluate("()=>window.__wheelgentic.isHeld()"))
        # ...but an ARM warning is NOT a link warning and must survive.
        await pg.evaluate("()=>window.__wheelgentic.flashHold("
                          "'ESTOP FAILED — CUT POWER AT THE SUPPLY')")
        await pg.wait_for_timeout(2000)
        check("an ARM warning still survives the reconnect churn",
              "CUT POWER" in await pg.evaluate(
                  "()=>document.getElementById('link').textContent"))
        _mj2 = open("web/main.js").read()
        check("ws.onopen clears only LINK warnings",
              "heldIsLinkWarning" in _mj2.split("ws.onopen")[1][:400],
              "clearing every banner would erase CUT POWER on reconnect")
        await pg.evaluate("()=>window.__wheelgentic.releaseHold()")

        print("\n=== 8. THE SCRUBBING ANIMATION ACTUALLY HAPPENS ===")
        # Tyler's emphasis: "VERY GOOD SCRUBBING ANIMATION WITH THE ROBOTIC ARM".
        # Nothing could see the arm at all -- it was absent from the debug
        # handle, so every check had to hunt the scene for a mesh whose material
        # colour matched the sponge, which finds nothing the day someone retints
        # it and then passes by asserting on undefined.
        check("the robot arm is on the debug handle",
              await pg.evaluate("()=>!!window.__wheelgentic.robot"),
              "the scrub animation is untestable without it")

        await pg.evaluate("()=>window.__wheelgentic.robot.setPhase('rest')")
        await pg.wait_for_timeout(900)
        _rest = await pg.evaluate(_SPONGE)
        # APPROACH -> the sponge must TRAVEL, not teleport
        await pg.evaluate("()=>window.__wheelgentic.robot.setPhase('scrub')")
        await pg.wait_for_timeout(120)
        _early = await pg.evaluate(_SPONGE)
        await pg.wait_for_timeout(1400)
        _late = await pg.evaluate(_SPONGE)

        def _d(a, b):
            return max(abs(x - y) for x, y in zip(a, b))

        check("the arm MOVES from rest toward the scrub pose",
              _d(_rest, _late) > 0.25, f"moved {_d(_rest,_late):.2f}")
        # A machine with mass travels; a diagram snaps. If it were snapping,
        # the 120ms sample would already be at the destination.
        # MEASURE THE FRACTION, NOT THE ABSOLUTE. "> 0.1 remaining" passed while
        # 0.54 of a 0.58 move had ALREADY happened in 120ms -- i.e. the arm was
        # 93% done before the first sample and the assertion still went green,
        # with a message that read as the failure case. A machine with mass has
        # most of its travel still ahead of it after 120ms; a diagram does not.
        _done = 1 - (_d(_early, _late) / max(_d(_rest, _late), 1e-6))
        check("and it TRAVELS rather than teleporting", _done < 0.55,
              f"{_done*100:.0f}% done after 120ms"
              + ("" if _done < 0.55 else " — SNAPPING like a diagram"))

        # THE STROKE: alternating impulses must sweep the sponge ALONG the limb,
        # not vibrate it in place.
        _xs = []
        for _k in range(8):
            await pg.evaluate("(d)=>window.__wheelgentic.robot.strokeNow(d)",
                              1 if _k % 2 == 0 else -1)
            await pg.wait_for_timeout(160)
            _xs.append((await pg.evaluate(_SPONGE))[0])
        _sweep = max(_xs) - min(_xs)
        check("the stroke sweeps the sponge across the limb", _sweep > 0.05,
              f"x travel {_sweep:.3f}"
              + ("" if _sweep > 0.05 else " — ROLLING IN PLACE, no stroke"))
        await pg.evaluate("()=>window.__wheelgentic.robot.setPhase('rest')")

        check("no page errors", not errs, str(errs))
        await b.close()

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGINT); proc.wait(timeout=5)
    except Exception:      # section 3 SIGKILLs it on purpose
        try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception: pass
    log.close()

asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
print("  ARM + MANUAL POP CONFLICT IS VISIBLE, NOT SILENT")
