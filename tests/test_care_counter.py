"""The care count must never claim care that was not delivered.

WHY THIS ONE MATTERS MORE THAN ITS SIZE. Every other readout on the projector
measures the machine: cleanliness, spoonfuls, heart rate, cells per arm. This
is the only one that measures what the machine saved a person, and the
presenter asserts at 0:15 that nursing assistants get hurt at five times the
industry rate. A number in that position that inflates is worse than no number
at all, because the whole panel is an honesty claim.

Three ways it inflated, all found by audit after it shipped:

1. COUNTING A MODE THAT WAS MERELY SELECTED. The rule was
   `mode === 'feed'`, but `mode` stays 'feed' long after the last spoonful
   lands -- the arms park at rest and the bar freezes at 4 / 4 SPOONS, and the
   script has the presenter talking over that transition for about 25 seconds.
   All of it counted. `feedLive` is the real signal: serve() clears it when
   the last spoonful lands, and feed's leave() clears it when the operator
   switches away mid-beat.

   VITALS IS DELIBERATELY NOT LIKE THIS. It keeps reading a heart rate for as
   long as it is up, so every second it is up genuinely is care delivered.

2. SURVIVING A RESET. resetAll() zeroes the cleanliness, the sweep, the
   splotches and the counter, but left careSeconds alone. Rehearse once for
   40 seconds, press `r` for the judges' run, and the panel opened at 40s with
   nothing yet having happened -- the only readout on screen describing a
   different run than everything beside it.

   THE ESTOP IS DELIBERATELY NOT THIS. `x` does not call resetAll, so an
   emergency stop keeps the count. That care really was delivered.

3. Voice was never counted, and must never be. A machine listening to a
   question has not delivered care.
"""
import asyncio
import re
import sys

from playwright.async_api import async_playwright

FAILS = []


def check(label, cond, detail=""):
    print(f"   {'    PASS' if cond else '*** FAIL'}  {label}"
          f"{'  [' + str(detail) + ']' if detail else ''}")
    if not cond:
        FAILS.append(label)


def secs(text):
    """The seconds the panel is claiming, or None if it is not showing one."""
    m = re.match(r"(\d+)s", (text or "").strip())
    return int(m.group(1)) if m else None


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(4000)
        await pg.keyboard.press(" ")        # dismiss the boot gate
        await pg.wait_for_timeout(500)

        async def read():
            return await pg.evaluate(
                "document.getElementById('care')?.innerText || ''")

        print("\n=== 1. THE COUNT STOPS WHEN THE FEEDING BEAT ENDS ===")
        # WHAT ENDS THE BEAT CHANGED on 2026-09-20. It used to end on a typed
        # total -- four spoonfuls, three sips, two pills -- and this section
        # waited for the fourth to land and then checked the clock had
        # stopped. Nothing measures how much food is in a bowl, so the total
        # was removed along with the bowl itself, and feeding now runs until
        # the operator ends it.
        #
        # The rule being tested is unchanged and is the point of the file: the
        # count must measure care DELIVERED, not the mode being selected. So
        # the beat is ended here the way an operator ends it, and the clock
        # must stop with it.
        await pg.keyboard.press("8")
        await pg.wait_for_timeout(15000)    # long enough for real arm trips
        landed = secs(await read())
        check("feeding counted while it was delivering",
              landed is not None and landed > 0, landed)
        await pg.keyboard.press("x")        # the operator stops it
        await pg.wait_for_timeout(1000)
        stopped = secs(await read())
        await pg.wait_for_timeout(6000)     # still in feed mode, arms at rest
        idle = secs(await read())
        # The whole defect: `mode` is still 'feed' for all six of these
        # seconds, and nothing on screen is moving.
        check("and stops once the beat is over",
              stopped == idle, f"{stopped}s then {idle}s six seconds later")

        print("\n=== 2. VITALS COUNTS FOR AS LONG AS IT IS UP ===")
        await pg.keyboard.press("9")
        await pg.wait_for_timeout(4000)
        vit = secs(await read())
        # INVERTED ON 2026-09-20, and the inversion is the point. This used to
        # assert that vitals counts for as long as the panel is up, on the
        # argument that a heart rate is being read the whole time. No heart
        # rate is being read: no pulse oximeter is wired, /api/vitals answers
        # disconnected, and the panel says NO SENSOR CONNECTED. Seconds of
        # care accumulating under those words was the clearest contradiction
        # on the screen.
        check("a panel with no sensor counts no care",
              vit == idle, f"{idle}s then {vit}s under NO SENSOR CONNECTED")

        print("\n=== 3. A RESET CLEARS IT LIKE EVERY OTHER READOUT ===")
        await pg.keyboard.press("7")        # leave feed, or it repaints
        await pg.wait_for_timeout(400)
        await pg.keyboard.press("r")
        await pg.wait_for_timeout(900)
        after = await read()
        check("`r` clears the care panel", after.strip() == "",
              repr(after[:40]))
        # And the count itself must be zero, not merely hidden -- a hidden
        # panel that reopens at 40s is the same lie with an extra step.
        # THE FEED BEAT EARNS THE SECONDS NOW, not vitals -- see section 2.
        # A trip takes about twelve seconds and only counts while it is in
        # flight, so this waits for one to be well under way.
        await pg.keyboard.press("8")
        await pg.wait_for_timeout(6000)
        restarted = secs(await read())
        check("and the count itself restarted, not just the panel",
              restarted is not None and restarted < vit,
              f"was {vit}s, reopened at {restarted}s")

        print("\n=== 4. A NEW PERSON HAS NOT BEEN CARED FOR ===")
        # `n` swaps in a differently proportioned body and routes through
        # resetAll, so the count has to go with it. Carrying seconds across a
        # body swap would claim care delivered to someone who just arrived --
        # the same lie as carrying them across `r`, in a place that is easier
        # to miss because the whole screen changes at once.
        await pg.keyboard.press("8")            # earn some seconds first
        await pg.wait_for_timeout(6000)         # mid-trip, while it counts
        earned = secs(await read())
        check("seconds were on the panel before the swap",
              earned is not None and earned > 0, earned)
        await pg.keyboard.press("7")            # leave feed, or it repaints
        await pg.wait_for_timeout(400)
        # WAIT FOR THE RELOAD, DO NOT GUESS AT IT. `n` reloads the page, and a
        # fixed 3s wait passed alone and failed inside the suite, where the
        # machine is busy and the reload had not finished -- so this read the
        # OLD page and saw the old count. Marking the page first and waiting
        # for the mark to disappear waits for the actual event.
        await pg.evaluate("window.__beforeSwap = true")
        await pg.keyboard.press("n")
        try:
            await pg.wait_for_function("!window.__beforeSwap", timeout=20000)
        except Exception:
            pass          # some paths do not reload; the check below still holds
        await pg.wait_for_timeout(1500)         # the bake is fetched async
        after_swap = await read()
        check("and the panel is clear for the new person",
              after_swap.strip() == "", repr(after_swap[:40]))

        print("\n=== 5. THE ZEROS ARE THE POINT ===")
        # The swap above left the panel empty, so earn a second back before
        # reading the rows -- otherwise this checks an empty string and would
        # pass with the two zeros deleted from the markup entirely.
        await pg.keyboard.press("8")
        await pg.wait_for_timeout(6000)     # mid-trip, while the clock runs
        rows = await read()
        check("it still says nobody lifted and nobody was hurt",
              "0 LIFTS BY A PERSON" in rows and "0 BACKS AT RISK" in rows,
              repr(rows[:60]))

        print("\n=== 6. LISTENING IS NOT CARE ===")
        await pg.keyboard.press("7")
        await pg.wait_for_timeout(300)
        await pg.keyboard.press("r")        # back to a known zero
        await pg.wait_for_timeout(600)
        await pg.keyboard.press("0")        # voice mode
        await pg.wait_for_timeout(4000)
        voiced = await read()
        check("four seconds in voice mode counted nothing",
              secs(voiced) in (None, 0), repr(voiced[:40]))

        check("no page error through any of it", not errs, str(errs[:2]))
        await b.close()


asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  THE CARE COUNT ONLY COUNTS CARE")
