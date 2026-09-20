"""The privacy line has to be evidence, not a caption.

At 0:28 the presenter says the strongest defensive claim in the pitch: every
camera frame stays on this laptop. The line backing it read
"ON-DEVICE ONLY - 0 FRAMES STORED" as fixed text, which would say exactly the
same thing with the camera unplugged, the detector dead, or the whole vision
path removed. A claim that cannot fail is not evidence.

It now reads "N SEEN - 0 STORED". The seen count is incremented in the one
branch that consumes a genuinely new camera frame (`video.currentTime !==
lastVideoTime`, the same gate that feeds the pose detector), so it moves only
when frames are really arriving. The stored count is a literal zero because
nothing on this page writes a frame anywhere; if that ever changes, the line
has to start lying on purpose rather than by omission.

THIS TEST NEEDS A REAL CAMERA. Plain headless Chromium has no video device, so
the pose branch never runs and the count correctly stays at zero -- a test
without `--use-fake-device-for-media-stream` would pass against a completely
dead counter. That flag gives Chromium a synthetic camera whose frames go
through the same getUserMedia and the same detector as a real one.

The line also says whether the tracker currently has a person. That half
exists for 1:02, the only beat in the run of show with no keypress, where the
presenter steps out and the volunteer steps in: for a second or two nobody is
in frame, the avatar holds its last pose, and a silent freeze on a static
screen reads as broken tracking to a judge who was just told to watch the
cartoon mirror the volunteer.

The failure modes it guards:
  - the count goes back to being a caption that never moves
  - the stored half stops being zero, which is the claim itself
  - the subject state disappears, so a handoff looks like a crash again
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


def seen(text):
    m = re.search(r"(\d+)\s*SEEN", text or "")
    return int(m.group(1)) if m else None


def stored(text):
    m = re.search(r"(\d+)\s*STORED", text or "")
    return int(m.group(1)) if m else None


async def main():
    async with async_playwright() as p:
        # A SYNTHETIC CAMERA, not no camera. See the module docstring.
        b = await p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
        ])
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto("http://localhost:8000/", wait_until="load")
        # The pose model is fetched and compiled before any frame is consumed.
        await pg.wait_for_timeout(7000)
        await pg.keyboard.press(" ")
        await pg.wait_for_timeout(1500)

        async def line():
            return await pg.evaluate(
                "document.getElementById('privacy')?.innerText || ''")

        print("\n=== 1. FRAMES ARE REALLY ARRIVING ===")
        first = await line()
        check("the line reports a frame count at all", seen(first) is not None,
              repr(first))
        await pg.wait_for_timeout(2500)
        later = await line()
        # The whole point: a caption cannot do this.
        check("and the count climbs as frames come in",
              seen(later) is not None and seen(first) is not None
              and seen(later) > seen(first),
              f"{seen(first)} then {seen(later)}")

        print("\n=== 2. NOTHING IS KEPT ===")
        check("the stored count is zero", stored(later) == 0, stored(later))
        check("and it is still zero after more frames",
              stored(await line()) == 0, stored(await line()))

        print("\n=== 3. THE ZERO IS THE BRIGHT HALF ===")
        # The count that climbs is the ordinary part -- cameras see frames,
        # that is their job. The zero is the claim, so it carries the line's
        # own green. Reversed, the emphasis lands on surveillance happening
        # rather than on nothing being kept.
        colors = await pg.evaluate("""(() => {
          const e = document.getElementById('privacy');
          const s = e && e.querySelector('.seen');
          const k = e && e.querySelector('.kept');
          return { seen: s ? getComputedStyle(s).color : null,
                   kept: k ? getComputedStyle(k).color : null }; })()""")
        check("the seen count and the stored zero are styled apart",
              colors["seen"] and colors["kept"]
              and colors["seen"] != colors["kept"], colors)

        print("\n=== 4. IT SAYS WHETHER IT HAS A PERSON ===")
        # The one beat in the run of show with no keypress is 1:02, where the
        # presenter steps out and the volunteer steps in. For a second or two
        # the tracker has nobody, and the avatar used to just hold its last
        # pose in silence -- on a static screen, right after the presenter has
        # told the judges to watch the cartoon mirror the volunteer. A freeze
        # with no explanation reads as broken tracking.
        hunting = await line()
        check("with nobody in frame it says it is looking",
              "LOOKING FOR A SUBJECT" in hunting, repr(hunting[-40:]))

        # THE LOCKED HALF THROUGH THE SHIPPED BRANCH. A headless run has a
        # synthetic camera and no person in it, so "looking" is the state it
        # falls into by default -- asserting only that would leave the half
        # that actually appears on stage untested. Replacing the detector
        # makes the same line in the render loop see a person.
        await pg.evaluate("""(() => {
          const one = Array.from({length: 33}, (_, i) =>
            ({x: (i % 5) * 0.05, y: 1.4 - i * 0.03, z: 0, visibility: 1}));
          window.__wheelgentic.landmarker =
            { detectForVideo: () => ({ worldLandmarks: [one] }) };
        })()""")
        await pg.wait_for_timeout(1200)
        locked = await line()
        check("and with someone in frame it says it is locked on",
              "SUBJECT LOCKED" in locked, repr(locked[-40:]))
        check("the two states are styled apart",
              await pg.evaluate("""(() => {
                const e = document.getElementById('privacy');
                return !!e.querySelector('.lock'); })()"""),
              "lock span present")

        check("no page error through any of it", not errs, str(errs[:2]))
        await b.close()


asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  THE PRIVACY LINE IS EVIDENCE, NOT A CAPTION")
