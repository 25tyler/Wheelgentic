"""The chair answers questions from its own live state.

BRAINSTORM-2 asks for "voice support / personal agent", and until now only
the first half existed: every voice intent made the machine DO something. A
machine that only takes orders is a remote control. These four intents make
it answer, and what it answers with is read out of the running page.

WHY THAT IS THE INTERESTING CLAIM. A language model bolted on would answer
fluently, from its training, about a machine it cannot see -- and nobody in
the room could check a word of it. Every answer here is checkable against the
projector while it is being spoken. The test that matters is therefore not
"did it say something" but "did the sentence change when the state changed",
which is what sections 3 and 4 below actually assert.

HOW SPEECH IS CAPTURED. `speechSynthesis` is stubbed in an init script before
the page runs, so `say()` pushes its text into an array instead of making
noise. The alternative -- asserting on some flag the code sets -- would pass
with the speaking broken, which has already happened once on this page: the
whole voice path was mute for weeks because `say()` lived inside a function
that returns null without a microphone.

NOT DRIVEN THROUGH SpeechRecognition. Headless Chromium has one, but it
cannot be fed audio, so the intent matching is exercised directly against the
real INTENTS table imported from voice.js (section 1) and the answers through
the debug handle (sections 2 to 4). Both halves are the shipped code.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog; watchdog.arm(150)
import serve; serve.ensure()

from playwright.async_api import async_playwright

FAILS = []


def check(label, cond, detail=""):
    print(f"   {'    PASS' if cond else '*** FAIL'}  {label}"
          f"{'  [' + str(detail) + ']' if detail else ''}")
    if not cond:
        FAILS.append(label)


# Capture speech instead of making it. Installed before any page script runs.
STUB = """window.__said = [];
Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
  cancel() {}, speak(u) { window.__said.push(String(u.text)); } } });"""

# The REAL matcher, against the REAL table. A copy of the loop here would
# drift from voice.js and start passing while the page misroutes.
MATCH = """async (phrases) => {
  const mod = await import('/voice.js');
  const out = {};
  for (const p of phrases) {
    const t = p.toLowerCase();
    out[p] = null;
    for (const i of mod.INTENTS) {
      if (i.phrases.some(x => t.includes(x))) {
        out[p] = i.mode || '(fixed-answer)'; break; }
    }
  }
  return out; }"""


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:140]))
        await pg.add_init_script(STUB)
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(5000)
        await pg.keyboard.press(" ")
        await pg.wait_for_timeout(1500)

        async def ask(kind):
            await pg.evaluate("window.__said = []")
            await pg.evaluate("(k) => window.__wheelgentic.answerQuestion(k)", kind)
            await pg.wait_for_timeout(300)
            said = await pg.evaluate("window.__said")
            return said[0] if said else ""

        print("\n=== 1. A QUESTION IS NOT AN ORDER ===")
        # THE TRAP THIS SECTION EXISTS FOR, measured before it was fixed: a
        # question about a thing contains the word for that thing. With the
        # question intents listed after the action ones, "how clean am i"
        # matched 'clean' and STARTED A WASH, "what is my heart rate" opened
        # vitals, and "which arm is washing my leg" started a wash too. The
        # chair answered a question by doing a chore. Questions are matched
        # first now, and each phrase is long enough not to swallow an order.
        routed = await pg.evaluate(MATCH, [
            "how clean am i", "are you done", "how much longer",
            "which arm is washing my leg", "who does what",
            "how long have you been doing this",
            "what is my heart rate", "am i okay",
            "give me a wash", "clean me", "i am hungry", "i am thirsty",
            "my pills please", "stop", "do my left arm again",
        ])
        for said, want in [("how clean am i", "ask-clean"),
                           ("are you done", "ask-clean"),
                           ("how much longer", "ask-clean"),
                           ("which arm is washing my leg", "ask-arms"),
                           ("who does what", "ask-arms"),
                           ("how long have you been doing this", "ask-care"),
                           ("what is my heart rate", "ask-vitals"),
                           ("am i okay", "ask-vitals")]:
            check(f'"{said}" is answered, not obeyed',
                  routed[said] == want, routed[said])
        # And the orders must still reach their actions. A question intent
        # that swallowed "clean me" would be worse than the bug it replaced.
        for said, want in [("give me a wash", "shower"), ("clean me", "shower"),
                           ("i am hungry", "feed"), ("i am thirsty", "drink"),
                           ("my pills please", "pills"), ("stop", "stop"),
                           ("do my left arm again", "spot")]:
            check(f'"{said}" still does the thing', routed[said] == want,
                  routed[said])

        print("\n=== 2. IT SPEAKS, AND SAYS SOMETHING REAL ===")
        # Not "it set a flag". The whole voice path was mute for weeks with
        # every flag correct, because say() was unreachable without a mic.
        await pg.keyboard.press("b")
        await pg.wait_for_timeout(2500)
        arms = await ask("ask-arms")
        check("it answers which arm is doing the most", "Arm" in arms, arms)
        # The anatomical region names come from the bake's per-cell labels, so
        # this is the solver talking, not a lookup table.
        check("and names a body part, not just a number",
              any(w in arms for w in ("arm", "leg", "thigh", "torso", "forearm")),
              arms)

        print("\n=== 3. THE ANSWER TRACKS THE STATE ===")
        # The point of the whole feature. A fixed sentence is indistinguishable
        # from a caption; these have to move when the machine moves.
        # BOTH SAMPLES MUST BE MID-WASH. The first version compared the cold
        # "I have not started yet" against a running answer, which differ even
        # if the running answer is a hardcoded sentence -- and a plant that
        # replaced it with `say('You are 50 percent clean.')` passed. Pop one
        # patch first so `before` is already a real number, then pop another.
        await pg.keyboard.press("1")
        await pg.wait_for_timeout(800)
        before = await ask("ask-clean")
        await pg.keyboard.press("2")
        await pg.wait_for_timeout(800)
        after = await ask("ask-clean")
        check("the cleanliness answer changes as more is cleaned",
              before != after, f"{before!r} -> {after!r}")
        check("and it quotes the number on the wall", "%" in after
              or "percent" in after, after)
        # THE COUNT, NOT JUST THE PERCENT. "two patches left" is a thing the
        # person can look down and verify; a percentage is not.
        check("and the count of what is left, which a person can check",
              "patch" in after, after)

        # DELIVER SOME CARE FIRST, because the counter is deliberately honest:
        # it only ticks while the machine is WORKING, not merely switched on.
        # Popping splotches by hand does not move it and neither does sitting
        # in shower mode. Vitals does -- the chair is reading someone for as
        # long as it is up -- so that is the cheapest real care to bank here.
        # Getting this wrong is what the first version of this test did, and
        # it failed against correct code.
        await pg.keyboard.press("9")
        await pg.wait_for_timeout(2600)
        care = await ask("ask-care")
        check("the care answer reports real elapsed seconds",
              "second" in care or "minute" in care, care)
        # The counter's reason for existing is the second half of the line.
        check("and attaches the claim the counter is there to make",
              "lift" in care.lower(), care)
        # Back to shower so section 4's body swap runs from the same state
        # every other section did.
        await pg.keyboard.press("7")
        await pg.wait_for_timeout(900)

        print("\n=== 4. A DIFFERENT BODY GETS A DIFFERENT ANSWER ===")
        # The pitch's central claim, spoken aloud: the plan comes from the
        # person's own measured body. `n` swaps in a differently proportioned
        # body and the partition genuinely re-solves, so the sentence has to
        # change. Measured: 273 patches becomes 317.
        a = await ask("ask-arms")
        await pg.keyboard.press("n")
        await pg.wait_for_timeout(5500)
        c = await ask("ask-arms")
        check("swapping the body changes what the chair says about it",
              a != c, f"{a!r} -> {c!r}")

        print("\n=== 4b. A QUESTION IS NOT AN ORDER, AT RUNTIME TOO ===")
        # Section 1 proves the ROUTING sends questions to the agent. This
        # proves the agent then leaves the demo where it found it. The
        # recovery card tells the operator that asking a question cannot
        # move the beat they are in, and a card that promises something the
        # code does not do is worse than no card.
        await pg.keyboard.press("7")
        await pg.wait_for_timeout(900)
        for kind in ("ask-clean", "ask-arms", "ask-care"):
            was = await pg.evaluate(
                "document.getElementById('label')?.textContent || ''")
            await ask(kind)
            now = await pg.evaluate(
                "document.getElementById('label')?.textContent || ''")
            check(f"{kind} leaves the beat alone", was == now, f"{was!r} -> {now!r}")
        # THE ONE EXCEPTION, AND IT IS DELIBERATE. Asked for a heart rate
        # while vitals is closed, the honest move is to go and read the
        # sensor rather than invent a number, so this one DOES open vitals.
        # The card says so in as many words.
        await ask("ask-vitals")
        await pg.wait_for_timeout(2500)
        check("asking for a heart rate opens vitals rather than inventing one",
              "BPM" in await pg.evaluate(
                  "document.getElementById('pct')?.textContent || ''"),
              await pg.evaluate("document.getElementById('pct')?.textContent"))
        # And once it is open, the spoken number is the number on the wall.
        spoken = await ask("ask-vitals")
        shown = await pg.evaluate(
            "document.getElementById('pct')?.textContent || ''")
        import re as _re
        _s = _re.search(r"(\d+)", spoken or "")
        _w = _re.search(r"(\d+)", shown or "")
        check("the spoken heart rate is the one on screen",
              bool(_s and _w and _s.group(1) == _w.group(1)),
              f"said {spoken!r}, screen {shown!r}")

        print("\n=== 5. IT ANSWERS BEFORE ANYTHING HAS HAPPENED ===")
        # An agent that throws, or says "undefined", on the first question of
        # the demo is worse than one that is not there. Every branch has a
        # cold path and this drives all four.
        pg2 = await b.new_page(viewport={"width": 1280, "height": 720})
        await pg2.add_init_script(STUB)
        errs2 = []
        pg2.on("pageerror", lambda e: errs2.append(str(e)[:140]))
        await pg2.goto("http://localhost:8000/", wait_until="load")
        await pg2.wait_for_timeout(5000)
        for kind in ("ask-clean", "ask-arms", "ask-care", "ask-vitals"):
            await pg2.evaluate("window.__said = []")
            await pg2.evaluate("(k) => window.__wheelgentic.answerQuestion(k)", kind)
            await pg2.wait_for_timeout(250)
            said = await pg2.evaluate("window.__said")
            txt = said[0] if said else ""
            check(f"{kind} says something from a cold page", bool(txt), txt)
            check(f"{kind} does not leak a placeholder",
                  "undefined" not in txt and "NaN" not in txt
                  and "null" not in txt, txt)
        check("no page error from a cold question", not errs2, str(errs2[:2]))

        check("no page error through any of it", not errs, str(errs[:2]))
        await b.close()


asyncio.run(main())
print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  THE CHAIR ANSWERS QUESTIONS FROM ITS OWN LIVE STATE")
