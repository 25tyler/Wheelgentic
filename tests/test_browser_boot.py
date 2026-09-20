import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import watchdog; watchdog.arm(120)   # SIGALRM: a daemon-thread
# watchdog once let a test run for 3h21m and block a whole suite.
import sys
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve; serve.ensure()          # tests must not depend on a shared server
import asyncio, json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(channel=None, headless=True, args=["--use-angle=metal","--enable-unsafe-swiftshader","--use-fake-ui-for-media-stream",
                                          "--use-fake-device-for-media-stream"])
        pg = await b.new_page(viewport={"width":1280,"height":720})
        errs, logs = [], []
        pg.on("console", lambda m: (logs if m.type!="error" else errs).append(m.text))
        pg.on("pageerror", lambda e: errs.append(f"PAGEERROR: {e}"))
        failed=[]
        pg.on("requestfailed", lambda r: failed.append(f"{r.url} {r.failure}"))

        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(2500)

        print("=== console errors ===")
        for e in errs: print("  ", e[:160])
        if not errs: print("   (none)")
        print("=== failed requests ===")
        for f in failed: print("  ", f[:160])
        if not failed: print("   (none)")

        # Did three.js actually build a scene with the avatar in it?
        probe = await pg.evaluate("""() => {
          const c = document.querySelector('canvas');
          return {
            canvas: !!c,
            w: c ? c.width : 0, h: c ? c.height : 0,
            gate: !!document.getElementById('gate'),
            pct: document.getElementById('pct')?.textContent,
            link: document.getElementById('link')?.textContent,
          };
        }""")
        print("=== DOM probe ===", json.dumps(probe))

        await pg.screenshot(path="/tmp/shot_gate.png")

        # Press Enter -> unlock audio, remove gate, start pose
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(3000)
        probe2 = await pg.evaluate("""() => ({
          gate: !!document.getElementById('gate'),
          pct: document.getElementById('pct')?.textContent })""")
        print("=== after Enter ===", json.dumps(probe2))
        await pg.screenshot(path="/tmp/shot_running.png")

        # Fire the money shot via keyboard fallback
        await pg.keyboard.press("1"); await pg.wait_for_timeout(900)
        await pg.screenshot(path="/tmp/shot_pop1.png")
        p1 = await pg.evaluate("()=>document.getElementById('pct').textContent")
        await pg.keyboard.press("f"); await pg.wait_for_timeout(1400)
        await pg.screenshot(path="/tmp/shot_finale.png")
        p2 = await pg.evaluate("()=>document.getElementById('pct').textContent")
        conf = await pg.evaluate("()=>document.querySelectorAll('canvas').length")
        print(f"=== after '1': {p1}   after 'f': {p2}   canvases: {conf}")
        print("=== late errors ===")
        for e in errs[-6:]: print("  ", e[:160])
        # ASSERT, don't just print — a test that only prints always passes.
        hard = [e for e in errs if 'WebSocket' not in e]
        assert not hard, f"page errors: {hard}"
        assert not failed, f"failed requests: {failed}"
        assert probe['canvas'] and probe['w'] > 0, "no canvas rendered"
        assert probe2['gate'] is False, "gate did not dismiss on Enter"
        assert p1 == '33%', f"one pop should be 33%, got {p1}"
        assert p2 == '100%', f"finale should be 100%, got {p2}"
        assert conf >= 2, "confetti canvas never created"
        print("ASSERTIONS PASSED")
        await b.close()

asyncio.run(main())
