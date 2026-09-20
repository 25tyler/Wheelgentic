"""tests/test_remote_arm.py — the PROJECTOR must be able to arm a cycle.

The ARMED latch is read by cv2.waitKey in the OpenCV DEBUG window. On stage
Chrome is fullscreen in kiosk mode ON the projector, so 's' was only reachable
by alt-tabbing to a window hidden behind it. The demo now REQUIRES a keypress,
so it must be reachable from the window the operator is actually looking at.

The socket already existed; it just never carried anything upstream.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import watchdog; watchdog.arm(150)   # SIGALRM: a daemon-thread
# watchdog once let a test run for 3h21m and block a whole suite.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve; serve.ensure()          # tests must not depend on a shared server
import asyncio, json, os, signal, subprocess, sys, time
from playwright.async_api import async_playwright
os.chdir(os.path.expanduser("~/Wheelgentic"))

BAD = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: BAD.append(label)

PY = next((c for c in ("venv/bin/python", "/tmp/sbtest/bin/python")
           if os.path.exists(c)), "python3")

# :8765 must be free. Orphaned headless-Chrome from a previous test keeps
# CONNECTIONS to it open, which is enough to stop scrubbot binding -- and then
# every check here fails for a reason that has nothing to do with the code.
subprocess.run(["pkill", "-9", "-f", "py/scrubbot.py"], capture_output=True)
for _ in range(6):
    held = subprocess.run(["lsof", "-ti", "tcp:8765"],
                          capture_output=True, text=True).stdout.split()
    if not held:
        break
    for pid in held:
        subprocess.run(["kill", "-9", pid], capture_output=True)
    time.sleep(0.5)
LOG = "/tmp/_remote_arm.log"
proc = subprocess.Popen(
    [PY, "-u", "py/scrubbot.py", "--replay", "recordings/good_run.jsonl",
     "--no-arm", "--headless"],
    stdout=open(LOG, "w"), stderr=subprocess.STDOUT, preexec_fn=os.setsid)
time.sleep(3.5)
if proc.poll() is not None:
    print("*** python died at startup ***"); print(open(LOG).read()[:800]); sys.exit(1)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
            args=["--use-angle=metal", "--enable-unsafe-swiftshader"])
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:100]))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(2200)
        await pg.keyboard.press("Enter")           # dismiss the gate
        await pg.wait_for_timeout(1500)

        linked = await pg.evaluate("()=>document.getElementById('link').textContent")
        check("projector is linked to python", "LINKED" in linked, linked)

        r = await pg.evaluate("""()=>new Promise(res=>{
          const before = document.getElementById('link').textContent;
          window.dispatchEvent(new KeyboardEvent('keydown', {key:'s'}));
          setTimeout(()=>res({before,
            after: document.getElementById('link').textContent}), 500);
        })""")
        check("pressing 's' on the projector confirms on screen",
              r["after"] == "ARMED", f"{r['before']!r} -> {r['after']!r}")

        # ...AND THE CORNER MUST NOT CLAIM A CYCLE IS RUNNING. The browser
        # turns that line on at the `s` keypress, and replay sends no phase,
        # no pop and no reset -- so nothing ever turned it off. Measured
        # before the fix: CYCLE RUNNING, green, counter at 0%, for the rest
        # of the demo, on the fallback the recovery card sends you to when
        # the camera is dead.
        #
        # Waits past the point the real FSM would have reached SCRUB, so a
        # replay that ever grows a cycle fails the assertion above rather
        # than this one.
        await pg.wait_for_timeout(4000)
        line = await pg.evaluate(
            "()=>({txt:(document.getElementById('cycle')?.textContent||'').trim(),"
            " live: !!window.__wheelgentic.isCycleLive()})")
        check("replay does NOT leave the corner claiming a live cycle",
              not line["live"],
              "idle" if not line["live"]
              else f"{line['txt']!r} with no cycle behind it")

        await pg.wait_for_timeout(500)
        await pg.evaluate("""()=>window.dispatchEvent(
            new KeyboardEvent('keydown',{key:'x'}))""")
        await pg.wait_for_timeout(900)
        # ...and CLEAR it from the same window with a REAL shift+C.
        # A physical shift+C delivers key='C' (uppercase), so a handler
        # matching `e.key === 'c' && e.shiftKey` is DEAD on a real keyboard --
        # while Playwright's synthetic Shift+c delivers key='c' and passes.
        # Press it for real, and check the uppercase form explicitly.
        await pg.keyboard.press("Shift+C")
        await pg.wait_for_timeout(900)
        cleared = await pg.evaluate(
            "()=>document.getElementById('link').textContent")
        check("REAL shift+C (key='C') clears the estop",
              "CLEARED" in cleared, cleared)

        # plain c is the finale, not a clear -- they must not collide
        await pg.evaluate(
            "()=>{document.getElementById('link').textContent='SENTINEL';}")
        await pg.keyboard.press("c")
        await pg.wait_for_timeout(500)
        plain = await pg.evaluate(
            "()=>document.getElementById('link').textContent")
        check("plain 'c' does NOT clear the estop", "CLEARED" not in plain,
              plain)
        check("no page errors", not errs, str(errs[:2]))
        await b.close()

asyncio.run(main())
try: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
except ProcessLookupError: pass
time.sleep(1.4)
out = open(LOG).read()
check("python received the ARM request", "ARM requested from the projector" in out)
check("the FSM actually armed", "ARMED (from the projector)" in out)

# ...AND THEN NOTHING FOLLOWS, WHICH IS THE POINT. This test armed the FSM on
# REPLAY data and stopped there, so nobody noticed that replay has no scrub
# cycle at all: replay_loop() only calls arm.set_target(), while APPROACH,
# SCRUB and all three fire() calls live in vision_loop(), which replay never
# runs. Measured: zero pops in 36s, even with --no-contact-gate.
#
# Pin the real behaviour rather than leaving it unspecified. If replay ever
# grows a cycle this assertion fails and the recovery card's row -- which now
# tells the operator to use 1/2/3 because 's' does nothing -- must be rewritten
# in the same commit.
_no_cycle = "-> APPROACH" not in out and "-> SCRUB" not in out
check("replay ARMS but runs NO scrub cycle (pops are manual there)",
      _no_cycle,
      "no cycle, as documented" if _no_cycle
      else "replay grew a cycle: update the recovery card's replay row too")
check("python received the ESTOP request", "ESTOP requested from the projector" in out)
_clear = "CLEAR-ESTOP requested from the projector" in out
check("python received the CLEAR request", _clear,
      "received" if _clear else
      "the torque watchdog fires on its own — recovery must live where the "
      "trigger lives, or a cutout deadlocks the demo")
check("the estop was actually cleared", "estop cleared" in out)

print("\n" + "="*58)
if BAD:
    print(f"  *** {len(BAD)} FAILED: {BAD}"); sys.exit(1)
print("  REMOTE ARM/ESTOP FROM THE PROJECTOR WORKS")
