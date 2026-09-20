"""The sponge turns while it is on the body, and only while it is on the body.

BRAINSTORM-2 line 80 calls a spinning sponge the "big wow factor" and doubts
it only on hardware grounds -- servo, wiring, power. None of those apply on a
projector, and the head was static: four arms held a block against a person
and dragged it. The scrub beat is the longest single stretch of the demo and
the thing actually touching the person was the one part not moving.

THE TWO WAYS THIS GOES WRONG:

1. Spinning on the wrong axis. The sponge's Z is already owned by the stroke
   (robotarm.js drives `sponge.rotation.z` from strokeNow), so a spin added to
   Z cancels the rub that makes the arm read as scrubbing rather than
   pressing. The spin belongs on Y, which is the head's own axis against the
   limb.

2. Spinning when the arm is not touching anyone. A head that turns during the
   approach, or keeps turning after the arms have lifted and parked, reads as
   a machine that cannot switch itself off -- the opposite of the safety
   claim the governor strip is making three lines above it.

DRIVEN THROUGH THE REAL CHOREOGRAPHY. startScrubChoreography() is the spine
the demo runs: it holds the arms in `hover` for the first 1600ms and only then
goes to `scrub`. A test that called setPhase('scrub') directly would pass
while the real path never spun at all -- which is exactly what happened on the
first attempt at this feature.
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


SPIN = "window.__wheelgentic.fleet[0].arm.sponge.rotation.y"
ROLL = "window.__wheelgentic.fleet[0].arm.sponge.rotation.z"


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

        print("\n=== 1. STILL UNTIL THE ARM ARRIVES ===")
        rest = await pg.evaluate(SPIN)
        # The real spine, not setPhase by hand. It holds `hover` for 1600ms.
        await pg.evaluate("window.__wheelgentic.startScrubChoreography()")
        await pg.wait_for_timeout(900)          # still travelling in
        approach = await pg.evaluate(SPIN)
        check("the head does not turn during the approach",
              abs(approach - rest) < 0.5, f"{rest:.2f} -> {approach:.2f}")

        print("\n=== 2. TURNING ONCE IT IS ON THE BODY ===")
        await pg.wait_for_timeout(1600)         # now in scrub
        a = await pg.evaluate(SPIN)
        await pg.wait_for_timeout(1200)
        c = await pg.evaluate(SPIN)
        # A full turn is 6.28 rad. Anything under about one turn a second
        # would not read as powered at projector distance.
        check("the head turns while scrubbing", c - a > 3.0,
              f"{c - a:.2f} rad in 1.2s")

        print("\n=== 3. THE STROKE STILL RUBS ===")
        # Z belongs to the stroke. If the spin were put on Z it would cancel
        # this, and the arm would read as pressing rather than scrubbing.
        rolls = []
        for _ in range(8):
            rolls.append(await pg.evaluate(ROLL))
            await pg.wait_for_timeout(120)
        check("the stroke roll is still alive and changes sign",
              max(rolls) > 0.001 and min(rolls) < -0.001,
              f"roll range {min(rolls):.3f} to {max(rolls):.3f}")

        print("\n=== 4. IT STOPS WHEN THE ARMS LIFT ===")
        await pg.evaluate("window.__wheelgentic.stopScrubChoreography()")
        await pg.evaluate(
            "window.__wheelgentic.fleet.forEach((f) => f.arm.setPhase('rest'))")
        await pg.wait_for_timeout(1200)         # let the ease-out finish
        d = await pg.evaluate(SPIN)
        await pg.wait_for_timeout(1200)
        e = await pg.evaluate(SPIN)
        check("the head stops once the arms are parked",
              abs(e - d) < 0.05, f"{e - d:.3f} rad while at rest")

        print("\n=== 5. THE ESTOP STOPS IT TOO ===")
        # The estop is the beat where the pitch says every arm halts. A head
        # still turning against a person on a stopped machine contradicts that
        # at the worst possible moment, and this motion was added after the
        # estop test was written, so nothing else asserts it.
        #
        # THROUGH THE SHIPPED STOP, not setPhase by hand. stopScrubChoreography
        # is what the estop key calls once the socket is live, which is the
        # only state the choreography can be running in -- `s` refuses to start
        # it without a link.
        await pg.evaluate("window.__wheelgentic.startScrubChoreography()")
        await pg.wait_for_timeout(3200)          # let it reach scrub and spin up
        # Measure the turning BEFORE the stop, in its own window. Comparing a
        # pre-stop sample against a post-ease-out one spans the spin-down and
        # measures a smaller number than the head was ever doing.
        spun = await pg.evaluate(SPIN)
        await pg.wait_for_timeout(900)
        turning = await pg.evaluate(SPIN)
        check("it was turning before the stop", turning - spun > 3.0,
              f"{turning - spun:.2f} rad in 0.9s")

        await pg.evaluate("window.__wheelgentic.stopScrubChoreography()")
        await pg.wait_for_timeout(1400)          # the ease-out finishes
        f = await pg.evaluate(SPIN)
        await pg.wait_for_timeout(1200)
        g = await pg.evaluate(SPIN)
        check("and the estop path stops the head", abs(g - f) < 0.05,
              f"{g - f:.3f} rad after the stop")

        check("no page error through any of it", not errs, str(errs[:2]))
        await b.close()


asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  THE SPONGE TURNS ONLY WHILE IT IS ON THE BODY")
