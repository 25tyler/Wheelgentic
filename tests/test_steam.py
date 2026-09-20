"""Steam rises during the wash, and only during the wash.

A light shaft is invisible until something moves through it. The beam has been
in the scene since the room was built and read as a flat cone, because nothing
crossed it. The steam is what makes it pay -- and it is the warmth: a shower
with water spray and no steam reads as a machine rubbing a mannequin.

THREE THINGS THAT MATTER, in order of how badly they would show on a
projector:

1. IT MUST NOT RUN IN THE OTHER BEATS. At 1:42 the arms withdraw for feeding
   and vitals and the script wants those beats calm. Steam still drifting
   through a finished scene reads as a machine that cannot switch off -- the
   same failure the spinning sponge had, in a different medium.

2. IT MUST ACTUALLY DRIFT. A particle field that renders and does not move is
   worse than nothing: it reads as dirt on the lens.

3. IT MUST NOT BLOW OUT. The beam above it is additive, and two additive
   layers stacked go to a white blob on a bright projector. The steam is
   NormalBlending and its opacity is bounded, deliberately, and this pins
   both.

Costs one draw call. Measured headless: 24 fps without, 20 with, 91 draw calls
without, 92 with -- the frame rate there is software rendering with no GPU,
and the extra call is the whole cost.
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


# The steam is the one THREE.Points in the scene carrying a texture map. The
# body scan is also Points but has no map, so this cannot pick it up.
STEAM = """(() => {
  const s = window.__wheelgentic.scene.children.find(
    (o) => o.isPoints && o.material && o.material.map);
  if (!s) return null;
  const p = s.geometry.attributes.position.array;
  let sum = 0;
  for (let i = 1; i < p.length; i += 3) sum += p[i];
  return { op: +s.material.opacity.toFixed(4), vis: s.visible,
           meanY: +(sum / (p.length / 3)).toFixed(4),
           blending: s.material.blending,
           order: s.renderOrder }; })()"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(5000)
        await pg.keyboard.press(" ")
        await pg.wait_for_timeout(700)

        print("\n=== 1. NOTHING BEFORE THE WASH ===")
        idle = await pg.evaluate(STEAM)
        check("the steam exists in the scene", idle is not None, idle)
        check("and is invisible while nothing is happening",
              idle and idle["op"] < 0.01, idle and idle["op"])

        print("\n=== 2. IT RISES DURING THE WASH ===")
        # The real spine, not a flag poke.
        await pg.evaluate("window.__wheelgentic.startScrubChoreography()")
        await pg.wait_for_timeout(2500)
        a = await pg.evaluate(STEAM)
        check("it fades in once the wash starts", a["op"] > 0.05, a["op"])
        check("and is on screen", a["vis"] is True, a["vis"])
        await pg.wait_for_timeout(1200)
        c = await pg.evaluate(STEAM)
        # A field that renders and does not move reads as dirt on the lens.
        check("the puffs are actually drifting", c["meanY"] != a["meanY"],
              f"mean height {a['meanY']} -> {c['meanY']}")

        print("\n=== 3. IT DOES NOT BLOW OUT ===")
        # NormalBlending is 1 in three.js. The beam it drifts through is
        # additive; a second additive layer goes white on a bright projector.
        check("it is not stacking a second additive layer",
              c["blending"] == 1, c["blending"])
        check("and stays under the beam's render order", c["order"] < 900,
              c["order"])
        check("with a bounded opacity, not a climbing one", c["op"] <= 0.80,
              c["op"])

        print("\n=== 4. THE OTHER BEATS STAY CALM ===")
        await pg.keyboard.press("9")          # vitals: arms withdraw
        await pg.wait_for_timeout(3000)
        v = await pg.evaluate(STEAM)
        check("it clears when the wash is over", v["op"] < 0.05, v["op"])

        print("\n=== 5. THE STOP CLEARS IT TOO ===")
        # The estop is the beat where the pitch says everything halts. Steam
        # still rising off a stopped machine is the same contradiction the
        # spinning sponge head would have been, in a different medium. This
        # drives the shipped stop path, which is what the estop key calls
        # once the socket is live.
        #
        # NOT the `x` key, deliberately. With no socket, `x` takes the no-link
        # branch: it tells the operator where the working stop is and does not
        # touch the cartoon, so the steam keeps rising. That is unreachable in
        # practice -- the only caller of startScrubChoreography sits inside
        # the same socket guard, so a wash cannot be running when that branch
        # fires. Checked, because it looked like a real hole for a minute.
        # BACK TO SHOWER FIRST. Section 4 above left the page in vitals, and
        # the steam correctly refuses to rise there -- so without this the
        # check below measures the mode gate working and calls it a failure.
        # Caught by the check going red on correct code.
        await pg.keyboard.press("7")
        await pg.wait_for_timeout(600)
        await pg.evaluate("window.__wheelgentic.startScrubChoreography()")
        await pg.wait_for_timeout(2500)
        on = await pg.evaluate(STEAM)
        check("it is rising again before the stop", on["op"] > 0.05, on["op"])
        await pg.evaluate("window.__wheelgentic.stopScrubChoreography()")
        await pg.wait_for_timeout(3000)
        off = await pg.evaluate(STEAM)
        check("and the stop path clears it", off["op"] < 0.05, off["op"])

        check("no page error through any of it", not errs, str(errs[:2]))
        await b.close()


asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  STEAM RISES DURING THE WASH AND ONLY DURING THE WASH")
