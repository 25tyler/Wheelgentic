"""The recovery card's own sequence, run as one sequence.

Every key on that card is tested somewhere. The card is not: nobody had ever
run estop -> clear -> reset -> pop by hand as one flow, which is exactly what
an operator does when the demo has gone wrong in front of judges. Individually
green keys can still compose into a dead end -- an estop that leaves the page
in a state `r` cannot clear, a reset that leaves `1` `2` `3` inert.

The card's total-failure row says the answer to "Python crashed mid-demo" is
"ignore it, the cartoon keeps mirroring, use 1/2/3". This asserts that answer
is true after the machine has been stopped and cleared, not just from a clean
boot.

WHY IT ASSERTS THE COUNTER AND NOT THE KEYS. Pressing a key proves a handler
ran. What the operator needs is the number on the wall reaching 100%, because
that is the thing the closing line points at. A run where every key "worked"
and the counter stuck at 67% is a failed demo.
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


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(5000)
        await pg.keyboard.press(" ")
        await pg.wait_for_timeout(600)

        async def pct():
            return await pg.evaluate(
                "document.getElementById('pct')?.textContent || ''")

        print("\n=== 1. SOMETHING GOES WRONG MID-SCRUB ===")
        await pg.evaluate("window.__wheelgentic.startScrubChoreography()")
        await pg.wait_for_timeout(3000)
        await pg.keyboard.press("x")            # the operator stops it
        await pg.wait_for_timeout(1500)
        check("the estop leaves the page alive, not frozen",
              await pg.evaluate("!!window.__wheelgentic"), "handle present")

        print("\n=== 2. THE OPERATOR CLEARS AND RESETS ===")
        # shift+C by the PHYSICAL key, the way the keydown handler matches it.
        await pg.keyboard.down("Shift")
        await pg.keyboard.press("KeyC")
        await pg.keyboard.up("Shift")
        await pg.wait_for_timeout(1500)
        await pg.keyboard.press("r")
        await pg.wait_for_timeout(1500)
        back = await pct()
        check("the counter is back to zero for a fresh run",
              back.strip().startswith("0"), repr(back))

        print("\n=== 3. AND FINISHES BY HAND ===")
        # The card's answer to a dead backend. Each key pops one splotch.
        for k in ("1", "2", "3"):
            await pg.keyboard.press(k)
            await pg.wait_for_timeout(900)
        end = await pct()
        # The number on the wall, not the keys. A run where every key
        # "worked" and this stuck at 67% is a failed demo.
        check("the counter reaches 100% after a stop and a reset",
              "100" in end, repr(end))

        check("no page error through the whole sequence", not errs,
              str(errs[:2]))
        await b.close()


asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  THE RECOVERY CARD'S SEQUENCE WORKS AS A SEQUENCE")
