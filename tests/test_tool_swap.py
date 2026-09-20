"""Changing tools has to be an exchange, not a teleport.

The script has the presenter say, at 1:42, "it puts the sponge down and picks
up a spoon". Both props hang off the same attach point on the feeding arm, so
flipping `.visible` on each swapped them within a single frame: the spoken
line described an exchange while the screen showed one object becoming
another. BRAINSTORM-2 asks for the tool change by name ("Magnet to change
tools?"), and a swap nobody can see is the claim without the evidence.

THE THING THIS TEST REALLY PROTECTS is the measured scale. The spoon is
authored at 0.55 and the sponge at 1.0, and both numbers were set against the
character with a ruler -- the file records that getting the spoon wrong once
already put a sliver on screen and getting the bowl wrong put a soup bowl
bigger than the character's head. A scale tween that ends on a literal instead
of the remembered value would resize a prop permanently, and nothing about
reading the diff would show it. The value is stashed on the object the first
time it is touched and every tween ends on that.

Three failure modes, all plant-verified:
  - swapping instantly again (the regression this replaces)
  - a tool that never comes back, because swapOut leaves it at scale 0.001
    and something later sets only `.visible`
  - a tween that lands on the wrong size
"""
import asyncio
import sys

from playwright.async_api import async_playwright

FAILS = []


def check(label, cond, detail=""):
    print(f"   {'    PASS' if cond else '*** FAIL'}  {label}"
          f"{'  [' + str(detail) + ']' if detail else ''}")
    if not cond:
        FAILS.append(label)


SPONGE = "window.__wheelgentic.fleet[1].arm.sponge"

# The spoon is not exposed by name, so find it by the tag swapOut/swapIn put
# on every tool they touch -- and exclude the sponge, which carries it too.
FIND_SPOON = """(() => { let r = null;
  window.__wheelgentic.scene.traverse((o) => {
    if (o.userData && o.userData.toolScale !== undefined
        && o !== window.__wheelgentic.fleet[1].arm.sponge) r = o; });
  return r; })()"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(4500)
        await pg.keyboard.press(" ")
        await pg.wait_for_timeout(500)

        print("\n=== 1. THE SPONGE STARTS AT ITS MEASURED SIZE ===")
        before = await pg.evaluate(f"+{SPONGE}.scale.x.toFixed(3)")
        check("the sponge is at its authored scale before any swap",
              abs(before - 1.0) < 0.001, before)

        print("\n=== 2. THE EXCHANGE IS ANIMATED, NOT INSTANT ===")
        # Sample INSIDE the page. Stepping between playwright calls skips the
        # whole 0.38s animation and every value reads as the end state --
        # which is how an instant swap would pass a slower test.
        await pg.evaluate(
            "window.__tr = [];"
            "window.__ti = setInterval(() => {"
            "  const sp = " + FIND_SPOON + ";"
            "  window.__tr.push(sp ? +sp.scale.x.toFixed(3) : null); }, 30);")
        await pg.keyboard.press("8")
        await pg.wait_for_timeout(900)
        await pg.evaluate("clearInterval(window.__ti)")
        trace = [x for x in await pg.evaluate("window.__tr") if x is not None]
        mids = [x for x in trace if 0.01 < x < 0.54]
        check("the spoon grows through intermediate sizes",
              len(mids) >= 1 and len(set(trace)) >= 3,
              f"{len(set(trace))} distinct sizes, {len(mids)} mid-growth")
        check("and overshoots before settling, so it reads as picked up",
              max(trace) > 0.56, f"peak {max(trace)}")

        print("\n=== 3. IT LANDS ON THE MEASURED SIZE, NOT A LITERAL ===")
        spoon_end = await pg.evaluate(
            "(() => { const s = " + FIND_SPOON
            + "; return s ? +s.scale.x.toFixed(3) : null; })()")
        check("the spoon settles at its authored 0.55",
              spoon_end is not None and abs(spoon_end - 0.55) < 0.002,
              spoon_end)
        gone = await pg.evaluate(f"{SPONGE}.visible")
        check("and the sponge has been put away", gone is False, gone)

        print("\n=== 4. THE SPONGE COMES BACK AT FULL SIZE ===")
        # swapOut leaves the mesh at 0.001. Anything that restores it with
        # `.visible = true` alone puts an invisible speck on the arm, and the
        # next scrub cycle runs with no sponge on screen.
        await pg.keyboard.press("7")
        await pg.wait_for_timeout(1200)
        back = await pg.evaluate(f"+{SPONGE}.scale.x.toFixed(3)")
        vis = await pg.evaluate(f"{SPONGE}.visible")
        check("the sponge is visible again", vis is True, vis)
        check("and back at its authored scale, not at 0.001",
              abs(back - 1.0) < 0.002, back)

        print("\n=== 5. HAMMERING THE KEYS DOES NOT STRAND A TOOL ===")
        # An operator who has lost the thread taps keys faster than the 0.38s
        # exchange. This asserts the END state: after six interrupted swaps the
        # sponge is back at its authored size and on the arm, not parked at
        # 0.001 where it would be invisible for the rest of the demo with
        # nothing on screen to explain it.
        #
        # HONEST ABOUT WHAT THIS DOES NOT PROVE. I planted the removal of
        # killTweensOf expecting this to go red, and it stayed green: competing
        # tweens still END on the same authored value, so the guard changes
        # only what the size does mid-flight, not where it lands. The guard is
        # still right -- overlapping tweens fighting over one property is a
        # visible stutter -- but this check does not cover it, and a future
        # reader should not think it does.
        for _ in range(6):
            await pg.keyboard.press("8")
            await pg.wait_for_timeout(120)     # well inside the animation
            await pg.keyboard.press("7")
            await pg.wait_for_timeout(120)
        await pg.wait_for_timeout(2000)        # let the last one settle
        hammered = await pg.evaluate(f"+{SPONGE}.scale.x.toFixed(3)")
        check("the sponge survives six interrupted swaps",
              abs(hammered - 1.0) < 0.002, hammered)
        check("and is still on the arm", await pg.evaluate(f"{SPONGE}.visible"),
              await pg.evaluate(f"{SPONGE}.visible"))

        check("no page error through any of it", not errs, str(errs[:2]))
        await b.close()


asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  THE TOOL CHANGE IS AN EXCHANGE YOU CAN SEE")
