"""The voice readout must never invite speech it cannot hear.

WHY THIS EXISTS. Chrome's recogniser sends the audio to Google to transcribe,
which the demo script says out loud as the one part of this system that is not
on-device. So `network` is the error to expect at a venue, and for a long time
it was not handled: onend fired, wantRestart was still true, it restarted,
failed again, and looped -- while the projector kept reading SAY SOMETHING.

That invitation is indistinguishable from a working microphone. It lands on
the beat where the presenter has just asked a judge to speak and is watching
the judge rather than the readout, so nobody finds out until the silence is
long enough to be the demo's worst moment.

WHY IT DRIVES THE SHIPPED HANDLER. The faults that matter only happen on a
real venue connection, which is exactly the place nobody can run a test.
`rec` is a closure local inside voice.js's start(), so its onerror cannot be
reached from outside -- and a test that re-implemented the handling would
prove nothing about the code that ships. voice.js exposes simulateError(),
which calls the shipped handler with the shipped event shape. Delete the
handler and this test stops working too, which is the property that makes it
worth having.
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


READ = """() => ({
  pct:   (document.getElementById('pct')?.textContent   || '').trim(),
  heard: (document.getElementById('heard')?.textContent || '').trim(),
})"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-fake-ui-for-media-stream"])
        pg = await b.new_page(viewport={"width": 1600, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(7000)
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(500)

        print("\n=== the readout admits it cannot hear ===")
        await pg.keyboard.press("0")          # voice mode
        await pg.wait_for_timeout(800)
        await pg.keyboard.press("m")          # microphone on
        await pg.wait_for_timeout(1500)
        on = await pg.evaluate(READ)
        check("the microphone opens and the screen invites speech",
              on["pct"] == "SAY SOMETHING", on["pct"])

        have = await pg.evaluate(
            "() => !!(window.__wheelgentic.voice && window.__wheelgentic.voice.simulateError)")
        # Verdict and explanation from the SAME condition. A passing check
        # that prints its own failure reason is how a green suite reads as
        # broken -- section 6c of test_docs_match_code.py exists for this.
        check("the shipped error handler is reachable to drive", have,
              "reachable" if have
              else "voice.js must expose simulateError or this proves nothing")

        for err, short, long in (
                ("network", "NO NETWORK", "NO NETWORK FOR SPEECH"),
                ("audio-capture", "NO MIC", "MICROPHONE BLOCKED")):
            await pg.keyboard.press("m")      # off
            await pg.wait_for_timeout(400)
            await pg.keyboard.press("m")      # and on again, clearing the fault
            await pg.wait_for_timeout(1200)
            await pg.evaluate("(e) => window.__wheelgentic.voice.simulateError(e)", err)
            await pg.wait_for_timeout(1000)
            st = await pg.evaluate(READ)
            check(f"`{err}` stops it saying SAY SOMETHING",
                  st["pct"] != "SAY SOMETHING", st["pct"])
            check(f"`{err}` names the fault in the big readout",
                  st["pct"] == short, st["pct"])
            # THE LONG WORDING GOES IN THE SMALL LINE, where 21 characters is
            # 336px against a 1344px limit. In #pct at 97px it would be 2037px
            # against a 1920 frame.
            check(f"`{err}` explains itself in the smaller line",
                  st["heard"] == long, st["heard"])
            # A FAULT IS NOT A QUOTATION. That line wraps what the volunteer
            # said in quote marks; a fault rendered there would read as if a
            # person had said it out loud.
            check(f"`{err}` is not quoted as if someone said it",
                  "“" not in st["heard"], st["heard"])

        print("\n=== and a retry recovers ===")
        await pg.keyboard.press("m")
        await pg.wait_for_timeout(1400)
        back = await pg.evaluate(READ)
        check("pressing `m` clears the fault and listens again",
              back["pct"] == "SAY SOMETHING", back["pct"])

        # `no-speech` is a silence timeout Chrome fires constantly in a quiet
        # room. Treating it as a fault would stop the microphone every few
        # seconds on a stage nobody is talking on yet.
        await pg.evaluate("() => window.__wheelgentic.voice.simulateError('no-speech')")
        await pg.wait_for_timeout(800)
        quiet = await pg.evaluate(READ)
        check("`no-speech` is NOT treated as a fault",
              quiet["pct"] == "SAY SOMETHING", quiet["pct"])

        check("no page error through any of it", not errs, str(errs[:2]))
        await b.close()


asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  THE VOICE READOUT NEVER LIES ABOUT HEARING")
