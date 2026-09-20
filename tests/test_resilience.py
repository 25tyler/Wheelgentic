"""FALLBACK LADDER: prove the browser survives Python dying and reconnects."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import serve; serve.ensure()   # never depend on a shared server
import watchdog; watchdog.arm(150)   # SIGALRM: a daemon-thread
# watchdog once let a test run for 3h21m and block a whole suite.
import asyncio, subprocess, time, os, signal, json, sys
from playwright.async_api import async_playwright
os.chdir(os.path.expanduser("~/Wheelgentic"))
# Prefer the repo venv: it is what run.sh uses and what demo day uses. The
# throwaway /tmp env is a fallback for a fresh clone.
PY = next((c for c in ("venv/bin/python", "/tmp/sbtest/bin/python")
           if os.path.exists(c)), "python3")

def start():
    return subprocess.Popen([PY,"py/scrubbot.py","--replay",
        "recordings/synthetic.jsonl","--no-arm","--headless"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid)

async def main():
    proc=start(); time.sleep(3.0)
    if proc.poll() is not None:
        print("*** python died at startup — wrong interpreter? ***")
        print(f"    used: {PY}")
        sys.exit(1)
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True,
            args=["--use-angle=metal","--enable-unsafe-swiftshader"])
        pg=await b.new_page(viewport={"width":1280,"height":720})
        errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
        await pg.goto("http://localhost:8000/",wait_until="load")
        await pg.wait_for_timeout(1800); await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(800)
        st=lambda: pg.evaluate("()=>document.getElementById('link').textContent")
        print("1. python up          :", await st())

        # frame count before/after kill proves rendering continues
        # THE PROBE MUST MEASURE THE APP'S LOOP, NOT VSYNC. The old version
        # counted its OWN requestAnimationFrame callbacks, which keep firing at
        # 60-120fps even if the app's renderer.setAnimationLoop is completely
        # dead -- it reported "121 fps" and would have reported 121 fps with
        # zero application frames rendered. Read three.js's own render counter
        # instead: renderer.info.render.frame only advances when the app draws.
        fps_probe = """()=>new Promise(r=>{
          const R = window.__wheelgentic && window.__wheelgentic.renderer;
          if(!R) return r({err:'no renderer handle'});
          const a = R.info.render.frame;
          setTimeout(()=>r({appFrames: R.info.render.frame - a}), 1000);
        })"""

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            print("*** python was already dead — nothing was being tested ***")
            sys.exit(1)
        time.sleep(2.0)
        print("2. python KILLED      :", await st())
        fps = await pg.evaluate(fps_probe)
        n = fps.get("appFrames") if isinstance(fps, dict) else None
        print(f"   cartoon still rendering: {n} APPLICATION frames in 1s")
        assert n is not None, f"probe failed: {fps}"
        assert n > 30, ("*** RENDER LOOP DIED WITH PYTHON *** "
                        f"only {n} app frames drawn")

        # manual keyboard fallback must still work with no server
        await pg.keyboard.press("1"); await pg.wait_for_timeout(900)
        pct = await pg.evaluate("()=>document.getElementById('pct').textContent")
        print(f"   manual key '1' with server dead -> counter {pct}")
        assert pct!="0%", "*** MANUAL FALLBACK BROKEN ***"

        proc=start(); time.sleep(3.0)
        print("3. python RESTARTED   :", await st())
        assert "LINKED" in (await st()), "*** DID NOT RECONNECT ***"
        await pg.screenshot(path="/tmp/shot_resilience.png")
        print("page errors:", [e for e in errs] or "(none)")
        await b.close()
    try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError: pass
    time.sleep(1.2)          # release :8765 before the next test binds it
    print("\nFALLBACK LADDER VERIFIED")
asyncio.run(main())
