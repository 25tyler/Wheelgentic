"""The handoff camera move keeps the frame alive and touches nothing else.

1:02 is the only beat in the run of show with no keypress. The presenter steps
out, the volunteer steps in, and the camera used to sit parked in the scan
framing while two people traded places in front of it. A still screen is where
a judge's eye leaves the projector -- and it leaves for the one thing you least
want watched, which is the tracker changing subject.

`g` is a six-second drift between that framing and wide. What makes it safe to
put in a rehearsed two-minute demo is that it changes NOTHING else: no mode, no
cycle, no props, no counter. An operator can press it at the wrong moment, or
press it and then forget, and the next mode key sets its own shot.

So this guards two things of equal weight:

1. THAT IT ACTUALLY MOVES. A shot that resolves to the framing already in
   effect is a no-op, and the beat is silent again with nobody the wiser.
2. THAT IT MOVES NOTHING ELSE. The failure that would matter on stage is a
   camera key that quietly resets the cleanliness counter or drops the cycle
   line, found at 1:02 in front of judges.

NOT `h`, which is what it was bound to first. The Python window's `h` is home,
and one letter meaning two things on one recovery card is a trap for an
operator who is already having a bad minute. There is a check for that below,
because the collision was found by reading the card rather than by anything
failing.
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


CAM = ("(() => { const c = window.__wheelgentic.camera;"
       "  return [+c.position.x.toFixed(3), +c.position.z.toFixed(3)]; })()")
STATE = """(() => ({
  label: document.getElementById('label')?.textContent || '',
  cycle: document.getElementById('cycle')?.textContent || '',
  pct:   document.getElementById('pct')?.textContent   || '',
}))()"""


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

        print("\n=== 1. `h` DOES NOTHING HERE ===")
        # The Python window's home key. If this ever starts moving the
        # projector camera, the card has two meanings for one letter again.
        before_h = await pg.evaluate(CAM)
        await pg.keyboard.press("h")
        await pg.wait_for_timeout(1800)
        check("pressing h leaves the projector camera alone",
              await pg.evaluate(CAM) == before_h, before_h)

        print("\n=== 2. `g` MOVES THE FRAME ===")
        start = await pg.evaluate(CAM)
        state_before = await pg.evaluate(STATE)
        await pg.keyboard.press("g")
        await pg.wait_for_timeout(1500)
        mid = await pg.evaluate(CAM)
        check("the camera is moving a second and a half in",
              mid != start, f"{start} -> {mid}")
        # It is a LONG move on purpose: a short one finishes while the shuffle
        # is still happening and the screen goes still again.
        await pg.wait_for_timeout(2500)
        later = await pg.evaluate(CAM)
        check("and still moving four seconds in", later != mid,
              f"{mid} -> {later}")

        print("\n=== 3. IT CHANGES NOTHING ELSE ===")
        # Plant-verified with `cleaned = 1; setClean(33)` on the key, which
        # fails this exactly. Note what it does NOT catch: a plant of
        # setCycle(true) stayed green, because the render loop rewrites that
        # line from cycleLive every frame and had corrected it before the
        # sample. That is the line defending itself rather than a hole here,
        # but do not read this check as covering it.
        await pg.wait_for_timeout(2500)          # let the whole move land
        check("the mode, the cycle line and the counter are untouched",
              await pg.evaluate(STATE) == state_before, state_before)

        print("\n=== 4. A MODE KEY TAKES THE CAMERA BACK ===")
        # The operator presses it and forgets. The next beat has to win.
        await pg.keyboard.press("g")
        await pg.wait_for_timeout(600)           # interrupt mid-move
        await pg.keyboard.press("8")             # feed sets its own shot
        await pg.wait_for_timeout(2500)
        # "MEAL RUN", NOT "FEEDING". The label named an outcome -- feeding,
        # drinking, medication -- and each claims something reached a person,
        # when there is no food, cup or pill anywhere in the program and the
        # arm only travels. It names the errand the operator asked for now.
        check("feed still opens over an unfinished handoff move",
              "MEAL RUN" in await pg.evaluate(
                  "document.getElementById('label').textContent"),
              await pg.evaluate("document.getElementById('label').textContent"))

        print("\n=== 5. PRESSING IT REPEATEDLY IS SAFE ===")
        # An operator who has lost the thread taps the key. Each press kills
        # the previous tween before starting its own, so re-entry cannot leave
        # the camera mid-blend or at a non-finite position -- which on this
        # page would be a black frame, at 1:02, in front of judges.
        for _ in range(5):
            await pg.keyboard.press("g")
            await pg.wait_for_timeout(250)
        await pg.wait_for_timeout(2500)
        finite = await pg.evaluate(
            "['x','y','z'].every((k) =>"
            "  Number.isFinite(window.__wheelgentic.camera.position[k]))")
        check("the camera survives five rapid presses", finite, finite)

        check("no page error through any of it", not errs, str(errs[:2]))
        await b.close()


asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  THE HANDOFF MOVE KEEPS THE FRAME ALIVE AND NOTHING ELSE")
