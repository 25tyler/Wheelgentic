"""tests/test_degraded_boot.py — the page must never hang silently.

main.js uses TOP-LEVEL await. If makeAvatar rejects, module evaluation stops
and the keyboard listener is never registered: the page sits on "PRESS ANY KEY
TO START" forever while keys do nothing, with the only clue in a console
nobody has open in kiosk mode. Verified by blocking the GLB.

Every asset failure must (a) say what is wrong ON SCREEN and (b) leave the
manual keyboard fallbacks working.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import watchdog; watchdog.arm(150)   # SIGALRM: a daemon-thread
# watchdog once let a test run for 3h21m and block a whole suite.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve; serve.ensure()          # tests must not depend on a shared server
import asyncio, sys
from playwright.async_api import async_playwright

BAD = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: BAD.append(label)

async def boot(b, block=None):
    pg = await b.new_page(viewport={"width": 1280, "height": 720})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:100]))
    if block:
        await pg.route(block, lambda route: route.abort())
    await pg.goto("http://localhost:8000/", wait_until="domcontentloaded")
    await pg.wait_for_timeout(3200)
    st = await pg.evaluate("""()=>({
      gateText: document.getElementById('gate')?.textContent || '',
      canvas: !!document.querySelector('canvas'),
      handle: typeof window.__wheelgentic })""")
    await pg.keyboard.press("Enter"); await pg.wait_for_timeout(700)
    await pg.keyboard.press("1"); await pg.wait_for_timeout(900)
    after = await pg.evaluate("""()=>({
      gate: !!document.getElementById('gate'),
      pct: document.getElementById('pct')?.textContent })""")
    await pg.close()
    return st, after, errs

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
            args=["--use-angle=metal", "--enable-unsafe-swiftshader"])

        print("=== NORMAL BOOT ===")
        st, after, errs = await boot(b)
        check("no page errors", not errs, str(errs[:2]))
        check("canvas rendered", st["canvas"])
        check("debug handle exposed", st["handle"] == "object")
        check("Enter dismisses the gate", after["gate"] is False)
        check("key '1' pops a splotch", after["pct"] == "33%", after["pct"])

        # THE CANCEL MUST BE PROVABLY LOAD-BEARING. index.html arms a 6000ms
        # classic-script watchdog that reveals #bootfail and removes the gate;
        # main.js cancels it right after its imports resolve. Every assertion
        # above reads the page at 3200ms, so deleting that cancel left the whole
        # suite GREEN -- measured: a plant against it was SILENT until something
        # looked at a HEALTHY page PAST the deadline. This is that something.
        # Its own page, because boot() closes the one it made.
        late_pg = await b.new_page(viewport={"width": 1280, "height": 720})
        await late_pg.goto("http://localhost:8000/", wait_until="domcontentloaded")
        await late_pg.wait_for_timeout(8000)       # well past the 6000ms deadline
        # ASK THE COMPUTED STYLE, NOT THE ATTRIBUTE. el.hidden reports the
        # ATTRIBUTE; an ID rule (#bootfail{display:flex}) outranks the UA
        # stylesheet's [hidden]{display:none}, so the overlay was painted
        # full-viewport while every probe read hidden===true. The recording is
        # what exposed it: 49s of the failure notice over a healthy page.
        _late = await late_pg.evaluate("""() => {
          const bf = document.getElementById('bootfail');
          const cs = bf ? getComputedStyle(bf) : null;
          return {
            hidden: bf ? bf.hidden : 'no element',
            painted: !!(bf && cs.display !== 'none'
                        && bf.getClientRects().length > 0),
            gate: !!document.getElementById('gate'),
            canvas: !!document.querySelector('canvas') };
        }""")
        check("a HEALTHY page never PAINTS the boot-failure notice",
              _late["hidden"] is True and not _late["painted"],
              f"hidden={_late['hidden']} painted={_late['painted']}")
        check("and its gate survives the watchdog deadline", _late["gate"],
              f"gate={_late['gate']} canvas={_late['canvas']}")
        await late_pg.close()

        print("\n=== GLB BLOCKED (vendor.sh not run / corrupt asset) ===")
        # THE FILENAME MOVED. This blocked character-a.glb, which the app
        # stopped loading when the model was swapped to Kenney's mini pack --
        # so it blocked NOTHING, the page booted fine, and the degraded-boot
        # assertion failed on a path that was never exercised. A route-blocking
        # test is only as good as its pattern.
        st, after, errs = await boot(b, "**/mini-character.glb")
        check("says what is wrong ON SCREEN",
              "FAILED" in st["gateText"] and "vendor.sh" in st["gateText"],
              st["gateText"][:60])
        check("no unhandled page error", not errs, str(errs[:2]))
        check("Enter STILL works (listener registered)", after["gate"] is False)
        check("keyboard fallback STILL works", after["pct"] == "33%", after["pct"])

        print("\n=== GSAP BLOCKED (vendor 404 / partial vendor.sh) ===")
        # NO TEST BLOCKED ANY VENDOR FILE until this one. gsap and confetti load as
        # classic <script src> tags, so a 404 leaves the GLOBAL undefined rather
        # than throwing a module error -- and setClean's gsap.to onUpdate was the
        # ONLY writer of #pct/#fill in all of web/. Measured before the fix: the
        # page booted, the gate read normally, '1' marked the splotch gone, and the
        # counter sat at 0% for the rest of the demo with one console line.
        #
        # The card promises `1` `2` `3` in SIX rows, including "works with the
        # server dead" and "Verified under SIGKILL". That promise is what this
        # asserts -- not the bounce, which is decoration.
        # OWN PAGE: boot() closes its page before returning, and the checks
        # below need a live one to read splotch records and to press 'f'. Timings
        # and evaluate strings mirror boot() rather than changing its contract.
        gsap_errs = []
        gsap_pg = await b.new_page(viewport={"width": 1280, "height": 720})
        gsap_pg.on("pageerror", lambda e: gsap_errs.append(str(e)[:100]))
        await gsap_pg.route("**/gsap.min.js", lambda r: r.abort())
        await gsap_pg.goto("http://localhost:8000/", wait_until="domcontentloaded")
        await gsap_pg.wait_for_timeout(3200)
        st = await gsap_pg.evaluate(
            "()=>({ gateText: document.getElementById('gate')?.textContent"
            " || '', canvas: !!document.querySelector('canvas'),"
            " handle: typeof window.__wheelgentic })")
        await gsap_pg.keyboard.press("Enter")
        await gsap_pg.wait_for_timeout(700)
        await gsap_pg.keyboard.press("1")
        await gsap_pg.wait_for_timeout(900)
        after = await gsap_pg.evaluate(
            "()=>({ gate: !!document.getElementById('gate'),"
            " pct: document.getElementById('pct')?.textContent })")
        errs = gsap_errs
        check("page still boots without gsap", st["canvas"])
        check("Enter still dismisses the gate", after["gate"] is False)
        check("AND THE COUNTER STILL MOVES", after["pct"] == "33%", after["pct"])
        # LABEL NAMES THE CASE. The first draft reused "no unhandled page error",
        # which already exists at the GLB case above -- and section 6b of
        # test_docs_match_code.py caught it: "1 duplicated:
        # ['test_degraded_boot.py:[68, 87] no unhandled page error']". That guard
        # was widened earlier today to see f-string labels; it bit the person who
        # widened it, on his own paste, which is the point of it.
        check("no page error from the missing gsap global", not errs, str(errs[:2]))

        # THE CAMERA STILL REACHES ITS SHOTS. setShot tweens through gsap and
        # has a no-gsap branch that lands the framing instantly. That branch
        # was wrong when it was written -- it applied fit() without landing the
        # interpolation, so it worked only on the FIRST call, by the luck of
        # shotMix.t starting at 1.
        #
        # TWO shots, not one, because one call cannot tell a correct landing
        # from that luck. The second is the one that catches it: after a first
        # move the mixer has to have been reset, or the second shot arrives
        # blended toward the first.
        _cam = "()=>{const c=window.__wheelgentic.camera;return +c.position.z.toFixed(2);}"
        _z0 = await gsap_pg.evaluate(_cam)
        await gsap_pg.keyboard.press("b")          # -> the scan shot, pushes in
        await gsap_pg.wait_for_timeout(500)
        _z1 = await gsap_pg.evaluate(_cam)
        await gsap_pg.keyboard.press("9")          # -> the room shot, pulls back
        await gsap_pg.wait_for_timeout(500)
        _z2 = await gsap_pg.evaluate(_cam)
        check("the camera reaches its first shot without gsap", _z1 < _z0 - 0.3,
              f"z {_z0} -> {_z1} (expected a push IN)")
        check("and its second, rather than blending toward the first",
              _z2 > _z1 + 0.5, f"z {_z1} -> {_z2} (expected a pull BACK)")
        # PUT THE PAGE BACK. `9` left it in vitals mode, which BORROWS the #pct
        # element to show a heart rate -- so the 'f' check further down read
        # "69 BPM" instead of "100%" and failed. A guard that changes the state
        # its neighbours measure is worse than no guard: it reports a fault
        # that is its own.
        await gsap_pg.keyboard.press("7")          # back to shower
        await gsap_pg.wait_for_timeout(400)

        # PLANT 2 WAS SILENT WITHOUT THIS. Removing popSplotch's
        # hide-without-tween left every assertion above green: the counter moved,
        # no error fired, and the dirt just stayed on screen. The tween's
        # onComplete is the only thing that hides the sprite when gsap EXISTS, so
        # the no-gsap branch must hide it directly -- and nothing asserted that.
        _hid = await gsap_pg.evaluate(
            "() => window.__wheelgentic.avatar.splotches.map("
            "r => ({ gone: r.gone, vis: !!r.sprite.visible }))")
        check("and the popped splotch is HIDDEN, not just marked gone",
              _hid[0]["gone"] and not _hid[0]["vis"],
              f"rec0 gone={_hid[0]['gone']} visible={_hid[0]['vis']} -- without "
              "gsap the scale-away never runs, so the hide must be direct")

        # THE FINALE CHECKS MOVED TO THE CONFETTI CASE. They lived here first and
        # plant 3 (neutering `typeof confetti === 'function'`) stayed SILENT: the
        # 'f' key does reach 100% and finale() does run, but confetti is PRESENT in
        # this page -- only gsap is blocked -- so the guard is trivially true and
        # removing it changes nothing. A guard can only be planted where the thing
        # it guards is actually absent.
        #
        # What DOES belong here: the no-gsap fallback must carry the counter all
        # the way to 100, not just to 33.
        await gsap_pg.keyboard.press("f")
        await gsap_pg.wait_for_timeout(1600)
        _fin = await gsap_pg.evaluate(
            "() => document.getElementById('pct')?.textContent")
        check("and 'f' reaches 100% with no gsap at all", _fin == "100%", _fin)
        check("still no page error at the payoff", not gsap_errs,
              str(gsap_errs[:2]))
        await gsap_pg.close()

        print("\n=== CONFETTI BLOCKED (the other classic-script global) ===")
        # OWN PAGE so the finale can be driven here. This is where the finale's
        # `typeof confetti === 'function'` guard is load-bearing: without it the
        # loop throws on its first frame and takes the rest of finale() with it,
        # including the 'complete' class. Plant-verified by neutering the guard.
        conf_errs = []
        conf_pg = await b.new_page(viewport={"width": 1280, "height": 720})
        conf_pg.on("pageerror", lambda e: conf_errs.append(str(e)[:100]))
        await conf_pg.route("**/confetti.browser.js", lambda r: r.abort())
        await conf_pg.goto("http://localhost:8000/", wait_until="domcontentloaded")
        await conf_pg.wait_for_timeout(3200)
        st = await conf_pg.evaluate(
            "()=>({ canvas: !!document.querySelector('canvas') })")
        await conf_pg.keyboard.press("Enter")
        await conf_pg.wait_for_timeout(700)
        await conf_pg.keyboard.press("1")
        await conf_pg.wait_for_timeout(900)
        _p1 = await conf_pg.evaluate(
            "() => document.getElementById('pct')?.textContent")
        check("page still boots without confetti", st["canvas"])
        check("the counter still moves", _p1 == "33%", _p1)

        await conf_pg.keyboard.press("f")
        await conf_pg.wait_for_timeout(1800)
        _cf = await conf_pg.evaluate(
            "() => ({ pct: document.getElementById('pct')?.textContent,"
            " complete: document.body.classList.contains('complete') })")
        check("the FINALE survives a missing confetti library",
              _cf["pct"] == "100%", _cf["pct"])
        _done = _cf["complete"]
        check("and finale() gets far enough to set the complete class", _done,
              "reached it" if _done
              else "finale() threw at the confetti loop before the class")
        check("no unhandled page error either", not conf_errs, str(conf_errs[:2]))
        await conf_pg.close()

        print("\n=== THREE.JS BLOCKED (the importmap's bare specifier) ===")
        # FOUND BY SWEEPING index.html's assets against every asset any test
        # blocks: four of six were unblocked, and this is the one that matters.
        # index.html maps "three" -> ./vendor/three.module.min.js and four modules
        # import it BARE (main.js:2, avatar.js, juice.js, robotarm.js). A 404 on
        # that file fails the import, so NO module evaluates -- unlike gsap and
        # confetti, whose classic <script> 404s only leave a global undefined.
        #
        # MEASURED before the fix: no canvas, no window.__wheelgentic, the gate stuck
        # on "PRESS ANY KEY TO START", Enter and 1/2/3 dead, counter 0%, and NO
        # pageerror at all. The code that writes "run ./vendor.sh" lives inside
        # the module that died, so the page could not report its own failure.
        #
        # THE KEYBOARD CANNOT SURVIVE THIS and this case must not ask it to. The
        # file docstring's "manual keyboard fallbacks keep working" holds where the
        # module DID evaluate (a missing GLB, a missing vendor global). With no
        # module there is no keydown listener and none can exist. What index.html's
        # classic-script watchdog promises instead: remove the gate that lies about
        # keys working, and paint a notice naming the fix.
        #
        # OWN PAGE and a LONGER WAIT: the watchdog fires at 6000ms, so boot()'s
        # 3200ms would read the DOM 2.8s before the notice is due -- which is
        # exactly how the first measurement misread the fix as broken.
        three_errs = []
        three_pg = await b.new_page(viewport={"width": 1280, "height": 720})
        three_pg.on("pageerror", lambda e: three_errs.append(str(e)[:100]))
        await three_pg.route("**/three.module.min.js", lambda r: r.abort())
        await three_pg.goto("http://localhost:8000/", wait_until="domcontentloaded")
        await three_pg.wait_for_timeout(7600)      # 6000 + margin to paint
        _t = await three_pg.evaluate("""() => {
          const bf = document.getElementById('bootfail');
          const cs = bf ? getComputedStyle(bf) : null;
          const r  = bf ? bf.getBoundingClientRect() : null;
          return {
            canvas: !!document.querySelector('canvas'),
            gate: !!document.getElementById('gate'),
            hidden: bf ? bf.hidden : 'no element',
            names: bf ? /vendor\\.sh/.test(bf.textContent || '') : false,
            // NOT offsetParent: it is null for position:fixed, and #bootfail IS
            // fixed -- that test failed while the element filled the viewport.
            shown: !!(bf && !bf.hidden && cs.display !== 'none'
                      && cs.visibility !== 'hidden' && cs.opacity !== '0'
                      && bf.getClientRects().length > 0
                      && r.width > 0 && r.height > 0),
          };
        }""")
        check("the block landed: no canvas without three.js", not _t["canvas"],
              f"canvas={_t['canvas']}")
        check("the boot-failure notice is VISIBLE, not just present",
              _t["shown"], f"hidden={_t['hidden']} shown={_t['shown']}")
        check("and it names ./vendor.sh so the operator knows the fix",
              _t["names"])
        check("the lying 'PRESS ANY KEY' gate was removed",
              _t["gate"] is False, f"gate still present={_t['gate']}")
        await three_pg.close()


        print("\n=== HUD.CSS BLOCKED (the privacy leg nobody tested) ===")
        # The other degraded-boot cases ask whether the page still WORKS.
        # This one asks whether it still keeps its promise. web/hud.css:8 was
        # `#cam { display:none }` and, until the `hidden` attribute landed in
        # index.html, it was the ONLY thing concealing the live webcam feed:
        # no inline style, no attribute, and zero sites in main.js touching the
        # element's display/visibility/hidden state.
        #
        # MEASURED with the stylesheet blocked and no `hidden` attribute:
        # display=inline, 300x150, one client rect. The raw camera on the
        # projector, while the page booted fine and printed no error -- one
        # line under a HUD that reads "ON-DEVICE ONLY - 0 FRAMES STORED".
        # It failed OPEN and SILENT, which is the combination worth a guard.
        css_errs = []
        css_pg = await b.new_page(viewport={"width": 1280, "height": 720})
        css_pg.on("pageerror", lambda e: css_errs.append(str(e)[:100]))
        await css_pg.route("**/hud.css", lambda r: r.abort())
        await css_pg.goto("http://localhost:8000/", wait_until="domcontentloaded")
        await css_pg.wait_for_timeout(3200)
        _css = await css_pg.evaluate("""() => {
          const v = document.getElementById('cam');
          const cs = v ? getComputedStyle(v) : null;
          const r  = v ? v.getBoundingClientRect() : null;
          return {
            // NOT v.hidden: that reports the ATTRIBUTE, and an ID rule can
            // override it while the attribute still reads true. Ask what the
            // element actually computes to and whether it has a box.
            painted: !!(v && cs.display !== 'none' && cs.visibility !== 'hidden'
                        && v.getClientRects().length > 0
                        && r.width > 0 && r.height > 0),
            display: cs ? cs.display : 'no #cam',
            w: r ? Math.round(r.width) : -1,
            h: r ? Math.round(r.height) : -1,
            // body background is set only by hud.css, so this proves the block
            // landed. A styleSheets scan does NOT: an aborted request still
            // leaves the <link> and can list a zero-rule sheet.
            cssApplied: getComputedStyle(document.body).backgroundColor
                        === 'rgb(14, 18, 32)',
            canvas: !!document.querySelector('canvas'),
          };
        }""")
        check("the hud.css block landed (its rules are not in force)",
              _css["cssApplied"] is False, f"cssApplied={_css['cssApplied']}")
        check("the page still boots without its stylesheet", _css["canvas"])
        check("AND THE CAMERA FEED IS STILL HIDDEN",
              _css["painted"] is False,
              f"display={_css['display']} {_css['w']}x{_css['h']} -- the "
              "`hidden` attribute on the <video>, not hud.css, is what "
              "guarantees this")
        check("no page error from the missing stylesheet",
              not css_errs, str(css_errs[:2]))
        await css_pg.close()

        print("\n=== POSE MODEL BLOCKED (cartoon cannot mirror) ===")
        st, after, errs = await boot(b, "**/pose_landmarker_lite.task")
        check("page still boots", st["canvas"])
        check("Enter still works", after["gate"] is False)
        check("counter still driveable by hand", after["pct"] == "33%", after["pct"])

        await b.close()

asyncio.run(main())
print("\n" + "="*58)
if BAD:
    print(f"  *** {len(BAD)} FAILED: {BAD}"); sys.exit(1)
print("  DEGRADED-BOOT CHECKS PASSED")
