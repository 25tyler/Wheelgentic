"""tests/test_repeat_cycles.py — the demo is run REPEATEDLY, not once.

Judges come in waves. The machine will run its cycle a dozen times before the
day is out, and every previous browser test checked exactly ONE cycle. State
that leaks across cycles -- a counter that does not reset, a sprite that is
rebuilt instead of restored, a geometry that accumulates -- shows up as the
demo degrading for the third judge and nobody able to say why.
"""
import asyncio, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import serve; serve.ensure()
import watchdog; watchdog.arm(150)
from playwright.async_api import async_playwright

BAD = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: BAD.append(label)

CYCLES = 4

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
            args=["--use-angle=metal", "--enable-unsafe-swiftshader"])
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:90]))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(2200)
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(1200)

        snaps = []
        for c in range(1, CYCLES + 1):
            for k in "123":
                await pg.keyboard.press(k)
                await pg.wait_for_timeout(450)
            st = await pg.evaluate("""()=>({
                pct: document.getElementById('pct').textContent,
                gone: window.__wheelgentic.recs.filter(r=>r.gone).length,
                sprites: window.__wheelgentic.avatar.splotches.length,
                objs: window.__wheelgentic.scene.children.length,
                geos: window.__wheelgentic.renderer.info.memory.geometries,
                texs: window.__wheelgentic.renderer.info.memory.textures,
                progs: window.__wheelgentic.renderer.info.programs.length })""")
            snaps.append(st)
            print(f"    cycle {c}: {st['pct']} gone {st['gone']}/3  "
                  f"sprites={st['sprites']} objs={st['objs']} "
                  f"geo={st['geos']} tex={st['texs']} prog={st['progs']}")
            await pg.keyboard.press("r")
            await pg.wait_for_timeout(800)

        print()
        check(f"every one of {CYCLES} cycles reaches 100%",
              all(s["pct"] == "100%" for s in snaps),
              str([s["pct"] for s in snaps]))
        check("every cycle pops all 3 splotches",
              all(s["gone"] == 3 for s in snaps),
              str([s["gone"] for s in snaps]))
        for key, label in (("sprites", "splotch sprites"),
                           ("objs", "scene objects"),
                           ("geos", "geometries"),
                           ("texs", "textures"),
                           ("progs", "shader programs")):
            vals = [s[key] for s in snaps]
            check(f"{label} do not accumulate", len(set(vals)) == 1,
                  f"{vals} — a growing count is a leak across cycles")

        final = await pg.evaluate("""()=>({
            pct: document.getElementById('pct').textContent,
            gone: window.__wheelgentic.recs.filter(r=>r.gone).length })""")
        check("the final reset returns to 0%", final["pct"] == "0%", final["pct"])
        check("and restores every splotch", final["gone"] == 0, str(final["gone"]))

        frames = await pg.evaluate("""()=>new Promise(r=>{
            const R=window.__wheelgentic.renderer; const a=R.info.render.frame;
            setTimeout(()=>r(R.info.render.frame-a),1000);})""")
        check(f"still rendering after {CYCLES} cycles", frames > 30,
              f"{frames} app frames/s")
        check("no page errors across every cycle", not errs, str(errs[:2]))
        await b.close()

asyncio.run(main())
print("\n" + "=" * 58)
if BAD:
    print(f"  *** {len(BAD)} FAILED: {BAD}"); sys.exit(1)
print("  REPEATED CYCLES LEAK NOTHING")
