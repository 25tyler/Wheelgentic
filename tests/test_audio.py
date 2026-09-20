"""tests/test_audio.py — the sound actually plays.

DIRECTIVE section 3 calls this "THE #1 DEMO-DAY FAILURE MODE": browsers block
all audio until a user gesture, and an autonomous robot demo has no clicks.
Nineteen tests existed and NONE of them had ever unlocked audio or fired a
sound -- web/juice.js was the only source file with no test at all.

The first run found the bug: ZzFXMicro declares `zzfxX` (its AudioContext)
with `let` at SCRIPT scope, so it never lands on `window`. unlockAudio() read
`window.zzfxX`, got undefined, resumed nothing -- and every sound would have
been silent on stage while the visuals looked perfect.
"""
import asyncio, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import serve; serve.ensure()
import watchdog; watchdog.arm(120)
from playwright.async_api import async_playwright

BAD = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: BAD.append(label)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
            args=["--use-angle=metal", "--enable-unsafe-swiftshader"])
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:100]))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(2000)

        print("=== 1. THE AUDIO CONTEXT IS REACHABLE ===")
        pre = await pg.evaluate("""()=>({
            zzfx: typeof window.zzfx,
            ctx: window.zzfxX ? window.zzfxX.state : 'MISSING' })""")
        check("window.zzfx is a function", pre["zzfx"] == "function", pre["zzfx"])
        check("window.zzfxX (the AudioContext) EXISTS",
              pre["ctx"] != "MISSING",
              "ZzFXMicro uses `let` at script scope — it must be published")
        print(f"    state before the gesture: {pre['ctx']}")

        print("\n=== 2. THE GESTURE RESUMES IT ===")
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(900)
        post = await pg.evaluate(
            "()=>window.zzfxX ? window.zzfxX.state : 'MISSING'")
        check("the context is RUNNING after Enter", post == "running", post)

        print("\n=== 3. A SPLOTCH POP ACTUALLY MAKES SOUND ===")
        # Count real AudioBufferSourceNodes rather than trusting that play()
        # was called -- zzfx swallows its own exceptions by design.
        r = await pg.evaluate("""()=>new Promise(res=>{
          const ctx = window.zzfxX;
          if (!ctx) return res({err:'no ctx'});
          let n = 0;
          const orig = ctx.createBufferSource.bind(ctx);
          ctx.createBufferSource = function(){ n++; return orig(); };
          window.dispatchEvent(new KeyboardEvent('keydown',{key:'1'}));
          setTimeout(()=>{ ctx.createBufferSource = orig;
                           res({nodes:n, state:ctx.state}); }, 900);
        })""")
        check("a pop creates real audio nodes", r.get("nodes", 0) >= 1,
              f"{r.get('nodes', 0)} nodes — squish + sparkle")

        print("\n=== 4. THE FINALE FANFARE PLAYS ===")
        r2 = await pg.evaluate("""()=>new Promise(res=>{
          const ctx = window.zzfxX;
          let n = 0;
          const orig = ctx.createBufferSource.bind(ctx);
          ctx.createBufferSource = function(){ n++; return orig(); };
          window.dispatchEvent(new KeyboardEvent('keydown',{key:'f'}));
          setTimeout(()=>{ ctx.createBufferSource = orig; res({nodes:n}); }, 1500);
        })""")
        check("the finale plays a multi-note fanfare", r2.get("nodes", 0) >= 3,
              f"{r2.get('nodes', 0)} nodes — 4 notes plus the pops")

        print("\n=== 5. EVERY VENDORED GLOBAL IS REACHABLE ===")
        # The ZzFX bug was a library keeping its state off `window`. Check the
        # others rather than assuming; a vendor.sh re-download that drops the
        # patch would silently kill the audio again.
        g = await pg.evaluate("""()=>({
            confetti: typeof window.confetti,
            shapeFromText: (window.confetti && typeof window.confetti.shapeFromText) || 'undefined',
            gsap: typeof window.gsap,
            gsapTo: (window.gsap && typeof window.gsap.to) || 'undefined',
            zzfx: typeof window.zzfx })""")
        for _name, _want in (("confetti","function"), ("shapeFromText","function"),
                             ("gsap","object"), ("gsapTo","function"),
                             ("zzfx","function")):
            check(f"window.{_name} is reachable", g[_name] == _want,
                  f"got {g[_name]}")

        print("\n=== 6. AUDIO NEVER BREAKS A FRAME ===")
        check("no page errors from the audio path", not errs, str(errs[:2]))
        frames = await pg.evaluate("""()=>new Promise(r=>{
            const R=window.__wheelgentic.renderer; const a=R.info.render.frame;
            setTimeout(()=>r(R.info.render.frame-a),1000);})""")
        check("still rendering after every sound", frames > 30,
              f"{frames} app frames/s")
        await b.close()

asyncio.run(main())
print("\n" + "=" * 58)
if BAD:
    print(f"  *** {len(BAD)} FAILED: {BAD}"); sys.exit(1)
print("  AUDIO WORKS — context published, resumed, and making sound")
