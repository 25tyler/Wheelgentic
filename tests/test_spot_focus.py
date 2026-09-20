"""Ask the chair to clean a spot and the arm that owns it must answer.

BRAINSTORM-2 line 92: "User can indicate an area to clean more, and the arm
focuses there." The demo script's closing beat at 1:52 has the volunteer say
"clean my left arm" and the chair naming the owning arm out loud.

Two failures this guards, both of which shipped, and both of which were
invisible to reading the diff:

1. THE WORD BOUNDARIES WERE LITERAL BACKSPACE CHARACTERS. A patch script wrote
   a \\b inside a Python string, so the pattern compiled as \\x08left\\x08 and
   matched nothing. Every phrase fell through to the "which one?" reply. The
   chair still spoke, so it sounded like it was working, and the file on disk
   looked correct. Nothing threw and nothing logged.

2. SPEAKING REQUIRED A MICROPHONE. say() lived inside makeVoice, so the chair
   could only answer after `m` had started the recogniser -- and on any
   browser without SpeechRecognition makeVoice returns null and it could never
   answer at all. Speaking is speechSynthesis and listening is
   SpeechRecognition: two different APIs, and the answer is the one line the
   script promises out loud.

WHY IT DRIVES THE SHIPPED RESOLVER. The path that normally reaches this runs
behind a real microphone and a closure. A test that re-implemented the lookup
would have passed against both bugs above. main.js exposes focusSpokenSpot,
which is the same function onIntent calls.

The numbers here are counted from the bake the page loads rather than written
down, so this stays true when the partition is re-solved.
"""
import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond, detail=""):
    print(f"   {'    PASS' if cond else '*** FAIL'}  {label}"
          f"{'  [' + str(detail) + ']' if detail else ''}")
    if not cond:
        FAILS.append(label)


def expected_from_bake(path):
    """Count per-arm ownership exactly the way the shipped resolver does."""
    cells = json.load(open(path))["cells"]
    out = {}
    for word, want in (("left", {"forearm_L", "upper_arm_L"}),
                       ("right", {"forearm_R", "upper_arm_R"})):
        tally = [0, 0, 0, 0]
        for owner, region in zip(cells["owner"], cells["region"]):
            if 0 <= owner <= 3 and region in want:
                tally[owner] += 1
        best = tally.index(max(tally))
        out[word] = (best, tally[best])
    return out


async def main():
    want = expected_from_bake(os.path.join(ROOT, "web/assets/body.json"))

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.keyboard.press(" ")
        # Wait for the REAL async bake instead of sleeping and hoping.
        await pg.wait_for_function(
            "window.__wheelgentic && window.__wheelgentic.territories"
            " && window.__wheelgentic.territories.data", timeout=30000)

        # Capture what the chair says by replacing speechSynthesis.speak. This
        # proves the shipped say() ran, not that some string exists somewhere.
        await pg.evaluate(
            "window.__said=[];"
            "window.speechSynthesis.speak=(u)=>{window.__said.push(u&&u.text);};")

        print("\n=== 1. THE ARM THAT OWNS THAT SIDE ANSWERS ===")
        for word, (arm, patches) in want.items():
            await pg.evaluate("(t) => window.__wheelgentic.focusSpokenSpot(t)",
                              f"clean my {word} arm")
            await pg.wait_for_timeout(400)
            said = await pg.evaluate("(window.__said || []).slice(-1)[0] || ''")
            check(f"'{word}' is answered by the arm that owns it",
                  f"Arm {arm} has that" in said, said)
            # The spoken count must equal the solver's own, or the chair is
            # narrating numbers the counts panel on the same screen disagrees
            # with.
            check(f"'{word}' names the solver's own patch count",
                  f"{patches} patches" in said, said)

        print("\n=== 2. NO MICROPHONE IS NEEDED TO ANSWER ===")
        # The checks above ran with window.__wheelgentic.voice still null -- the
        # microphone was never started, because nothing pressed `m`. That is
        # the property: answering out loud must not depend on makeVoice having
        # been called, since makeVoice returns null on any browser without a
        # recogniser and the answer is the one line the script promises aloud.
        #
        # DO NOT rewrite this as "headless has no SpeechRecognition". It has
        # one. An earlier version of this check asserted that and was vacuous:
        # planting the bug back left it green.
        no_voice = await pg.evaluate("!window.__wheelgentic.voice")
        spoke = await pg.evaluate("(window.__said || []).length > 0")
        check("the chair answered without the microphone ever starting",
              no_voice and spoke, f"voice={not no_voice} spoke={spoke}")

        # And say() must be importable on its own, not reachable only through
        # the object makeVoice builds. This is what the plant test exercises.
        #
        # DELIBERATELY NOT `m.say !== voice.speak`. Since the extraction,
        # makeVoice's `speak` IS `say` -- one implementation, shared. An
        # identity comparison here passes only while `voice` is still null,
        # so it would silently invert the moment anything in this test
        # pressed `m` first. The export plus the null-voice check above are
        # the property; identity is not.
        standalone = await pg.evaluate(
            "(async () => { const m = await import('./voice.js');"
            " return typeof m.say === 'function'; })()")
        check("say() is exported independently of the recogniser",
              standalone, standalone)

        print("\n=== 3. THE SPOT INTENT BEATS THE GENERIC WASH INTENT ===")
        # match() returns the FIRST intent whose phrase is a substring, and
        # "clean my left arm" contains "clean". With the wash intent listed
        # first, the script's closing beat restarts the shower instead of
        # answering -- the chair says "Starting your wash" and the spot
        # resolver never runs. The order in voice.js is load-bearing.
        routed = await pg.evaluate(
            "(async () => {"
            " const m = await import('./voice.js');"
            " const list = m.INTENTS;"
            " if (!list) return 'INTENTS is not exported';"
            " const hit = (t) => { t = t.toLowerCase();"
            "   for (const i of list)"
            "     if (i.phrases.some((p) => t.includes(p))) return i.mode;"
            "   return null; };"
            " return { spoken: hit('clean my left arm'),"
            "          plain:  hit('start my shower') }; })()")
        check("the script's phrase routes to the spot answer",
              isinstance(routed, dict) and routed.get("spoken") == "spot",
              routed)
        check("a plain wash request still starts the shower",
              isinstance(routed, dict) and routed.get("plain") == "shower",
              routed)

        print("\n=== 4. A SPOT IT CANNOT REACH ASKS INSTEAD OF GUESSING ===")
        await pg.evaluate("(t) => window.__wheelgentic.focusSpokenSpot(t)",
                          "clean my elbow")
        await pg.wait_for_timeout(400)
        said = await pg.evaluate("(window.__said || []).slice(-1)[0] || ''")
        check("an unmapped spot asks which side",
              "left arm or your right arm" in said, said)

        print("\n=== 5. THE PANEL PRINTS THE SOLVER'S SCHEDULE ===")
        # `phases` is the conflict graph coloured: which arms can move at the
        # same time without reaching into each other. partition.solve has
        # returned it on every bake since the beginning and the export took
        # only the territories, so it was computed and thrown away -- it
        # existed on screen nowhere while the project's own title says
        # "agentic".
        #
        # It has to come from the BAKE, not a caption: body A schedules
        # [0,2,3] then [1] and body B schedules [0,2] then [1,3], so a fixed
        # string would be wrong for one of them.
        counts = await pg.evaluate(
            "document.getElementById('counts')?.innerText || ''")
        check("the counts panel names who moves together",
              "TOGETHER" in counts, repr(counts.splitlines()[-1:]))
        ph = await pg.evaluate(
            "window.__wheelgentic.territories.data.phases")
        flat = sorted(a for g in (ph or []) for a in g)
        check("and the schedule it printed covers all four arms",
              flat == [0, 1, 2, 3], ph)
        # Every arm in the bake's schedule must appear in the line, or the
        # panel is summarising rather than reporting.
        named = all(f"ARM {a}" in counts for a in flat)
        check("naming each arm the solver scheduled", named,
              repr(counts.splitlines()[-1:]))

        print("\n=== 6. THE PANEL FITS THE PROJECTOR ===")
        # The schedule line is the longest row on this panel -- "TOGETHER 2
        # passes  ARM 0 + ARM 2 + ARM 3, then ARM 1" -- and it was added to a
        # panel whose other rows are short. test_projector.py measures HUD
        # overlap across three resolutions but never presses `b`, so #counts is
        # empty there and invisible to it. This is the only place the panel is
        # open and measurable.
        fit = await pg.evaluate("""(() => {
          const e = document.getElementById('counts');
          const r = e.getBoundingClientRect();
          return {right: Math.round(r.right), bottom: Math.round(r.bottom),
                  vw: innerWidth, vh: innerHeight}; })()""")
        check("the counts panel stays inside the frame",
              fit["right"] <= fit["vw"] and fit["bottom"] <= fit["vh"], fit)

        print("\n=== 7. THE SCAN COMES UP SO THE ANSWER IS VISIBLE ===")
        shown = await pg.evaluate(
            "!!(window.__wheelgentic.territories.points"
            " && window.__wheelgentic.territories.points.visible)")
        check("asking for a spot opens the body scan", shown, shown)

        check("no page error through any of it", not errs, str(errs[:2]))
        await b.close()


asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  THE CHAIR ANSWERS WITH THE ARM THAT OWNS THE SPOT")
