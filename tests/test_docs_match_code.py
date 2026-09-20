"""tests/test_docs_match_code.py — the recovery card must not lie.

Docs drift. The card told the operator to run
`REPLAY=recordings/good_run.jsonl ./run.sh` when that file did not exist; it
described keys that had moved; it named flags that had been renamed. Every one
of those is a 3am failure on a machine you cannot debug.

This checks the CLAIMS in docs/ against the actual code and filesystem.
"""
import glob, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
os.chdir(ROOT)
sys.path.insert(0, HERE)
import watchdog; watchdog.arm(60)

FAILS = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: FAILS.append(label)

card = open("docs/RECOVERY-CARD.md").read()

def open_all_test_lines():
    import glob
    for f in sorted(glob.glob("tests/*.py")):
        with open(f) as fh:
            yield from fh

directive = open("docs/DIRECTIVE.md").read()

# BOTH SCRIPTS. DEMO-SCRIPT.md is the old one-arm run of show, kept for its
# rules and recovery lines; DEMO-SCRIPT-V2.md is the wheelchair run of show
# and is what anyone actually rehearses from. Every check below that greps
# `demo` was reading the OLD file only, so a claim that existed solely in V2
# was unguarded -- which is how the 0:52 beat came to quote a per-body arm
# schedule with nothing checking it (test_scrub3d_merge.py covers that one).
demo = (open("docs/DEMO-SCRIPT.md").read()
        + "\n" + open("docs/DEMO-SCRIPT-V2.md").read())
readme = open("README.md").read()
py = open("py/scrubbot.py").read()
js = open("web/main.js").read()
runsh = open("run.sh").read()
cfg = open("config.json").read()
openq = open("docs/OPEN-QUESTIONS.md").read()

print("=== 1. EVERY KEY THE CARD PROMISES EXISTS ===")
# THE PYTHON KEYS ARE CHECKED INSIDE THE MAIN-FSM HANDLER BLOCK, not file-wide.
# Plant-proven: deleting the main FSM's SPACE binding -- the one the card
# documents and an operator presses in an emergency -- left the old
# `"ord(' ')" in py and "arm.estop()" in py` check GREEN, because those strings
# survive 3x and 6x in the camera-dropout and scripted loops. Four of the
# fifteen entries here were that shape: SPACE [3,6], r [3,5], q [3], 1/2/3 [2].
#
# Slice, then match. Both markers are 1x unique; all six keys are 1x INSIDE the
# 35-line slice. And assert the slice resolved first -- renaming the end marker
# would otherwise leave it empty and every check below would pass vacuously,
# exactly the way `_fin`'s [-1] slice broke when a later try/finally appeared.
# START MARKER MUST NOT BE ONE OF THE CHECKED LINES. It was the SPACE binding,
# so planting SPACE collapsed the whole slice and reddened all six keys plus the
# precondition -- a plant that cannot isolate its target proves nothing. The
# waitKey line sits one line above the block, is 1x unique, and is not asserted.
_FSM_START = "k = cv2.waitKey(1) & 0xFF"
_FSM_END = "def remote_control_loop(arm):"
check("the main-FSM key block is findable", _FSM_START in py and _FSM_END in py,
      "the slice below is vacuous without both markers -- every key check would pass")
_fsm = py[py.index(_FSM_START):py.index(_FSM_END)] if (_FSM_START in py and _FSM_END in py) else ""
check("and the slice is the handler block, not the whole file",
      0 < len(_fsm) < 4000, f"{len(_fsm)} chars")
for label, ok in [
    ("SPACE -> estop (python)",   "k == ord(' '): arm.estop()" in _fsm),
    ("r -> clear estop (python)", "k == ord('r'): arm.clear_estop()" in _fsm),
    ("h -> home (python)",        "k == ord('h'): arm.go_home()" in _fsm),
    ("s -> arm (python)",         "elif k == ord('s'):" in _fsm
                                  and "ARMED = True" in _fsm),
    ("q -> quit (python)",        "k == ord('q'): RUNNING = False" in _fsm),
    ("1/2/3 -> pop (python)",     "k in (ord('1'), ord('2'), ord('3'))" in _fsm),
    ("Enter -> start (browser)",  "'Enter'" in js),
    ("s -> arm (browser)",        "cmd: 'arm'" in js),
    ("x -> estop (browser)",      "cmd: 'estop'" in js),
    ("shift+C -> clear (browser)","cmd: 'clear'" in js),
    ("f -> finale (browser)",     "setClean(100)" in js),
    # HANDLER, not just the symbol: resetAll() appears 3x (definition, the
    # m.reset socket branch, the key handler). Deleting the KEY HANDLER left
    # the old `"resetAll()" in js` check green -- plant-verified.
    ("r -> reset (browser)",      "e.key === 'r') resetAll()" in js),
    # e/d/v were promised by the recovery card and guarded by nothing, which
    # is how README's key table drifted seven keys behind the code.
    ("e -> emote (browser)",      "e.code === 'KeyE'" in js
                                  and "avatar?.playOnce?.(name)" in js),
    ("d -> death gag (browser)",  "e.code === 'KeyD'" in js
                                  and "playOnce?.('die'" in js),
    ("v -> next character",       "e.code === 'KeyV'" in js
                                  and "avatar?.cast" in js),
]:
    check(label, ok)

print("\n=== 1b. THE README KEY TABLE MATCHES THE RECOVERY CARD ===")
# The README shipped listing 4 browser keys while the code had 11 and the
# recovery card documented all of them. A key table that is only mostly right
# is worse than none: the operator reads it, presses a key that does nothing,
# and concludes the software is broken mid-demo.
#
# SET EQUALITY, BOTH DIRECTIONS. This used to assert that six specific browser
# keys appeared SOMEWHERE in the README -- a whole-file substring, one-directional,
# and silent about the eight python keys. A key vanishing from the CARD passed.
#
# Extract from the KEY COLUMN of each table: README's is three-column
# (| Where | Key | Does |) so the key is index 1; the card's are two-column so it
# is index 0. My first attempt read index 1 on both, found ONE README key
# ('test_dirt.py'), and swept 'NO LINK' / 'ARMED' / 'run.sh' in from the card.
# Count the columns before diffing two tables.
def _table_keys(text, keycol):
    out = set()
    for _line in text.splitlines():
        if not _line.startswith("|") or _line.startswith("|--"):
            continue
        _cells = [c.strip() for c in _line.split("|")[1:-1]]
        if len(_cells) <= keycol:
            continue
        _cell = _cells[keycol]
        _found = re.findall(r"`([^`]{1,10})`", _cell)
        # a KEY cell is only backticked tokens plus bold/space -- nothing else.
        # That rejects prose cells that merely quote a key.
        if _found and not re.sub(r"`[^`]*`|\*|\s", "", _cell):
            out.update(_found)
    return out

_rk = _table_keys(readme, 1)
_ck = _table_keys(card, 0)
# EQUALITY, NOT A FLOOR. `>= 15` against an actual 16 left exactly one key
# deletable from BOTH surfaces with all four of these checks still green: the
# two set-difference checks below compare the README and the card only to each
# other, so a key dropped from both vanishes silently. This is the same fault
# the comment at the test-count guard warns about -- a floor blesses drift
# downward to its own limit. 16 is the union of the two documented tables (the
# projector page and the OpenCV debug window) and it matches the key set the
# code actually binds: web/main.js's keydown handler plus py/scrubbot.py's
# cv2.waitKey ord() literals. Plant-verified: deleting `h` from both surfaces
# turns BOTH counts red.
# 16 -> 18: `k` (skin tone) and `o` (outfit) were added to the
# projector page and documented in BOTH tables. Raising this is deliberate --
# the equality is what stops a key being dropped from both surfaces silently,
# so it must move only when the bound key set genuinely changes.
#
# 18 -> 26 on 2026-09-19, when the scope became the wheelchair. The eight new
# keys are all projector-side and all in the demo: `7` `8` `9` `0` pick the
# capability (shower, feed, vitals, voice), `shift+8` is the pill beat, `b`
# shows the measured body, `n` swaps in a differently shaped person, and `m`
# opens the microphone.
#
# `m` AND NOT `v`, and the reason is worth keeping: voice was first bound to
# `v`, which has cycled the 12 characters since long before voice existed.
# The new handler returned first and the character swap went unreachable --
# a standing requirement broken by a key that was never free.
#
# This guard did its job on the way in: all three of its checks went red the
# moment the keys existed in the code and not in both tables, which is
# exactly the drift it is here to catch.
#
# 26 -> 27 on 2026-09-19: `j` cycles the eight accessibility props (glasses,
# sunglasses, mask, hearing aid, three canes, a crutch) that were vendored with
# the character pack and never loaded. Same argument as `k` and `o` beside it,
# and the same guard caught it again -- both tables went red the moment the key
# existed in code alone.
# 27 -> 28 on 2026-09-20: `shift+7` brings a drink. The mode strip has
# promised "eating, drinking, pills" since it was built and the middle one had
# no key and no prop -- saying "I'm thirsty" routed to the food branch and
# carried a bowl of soup while the chair said "Bringing your food".
# 28 -> 29 on 2026-09-21: `g` is a slow camera drift for the 1:02 handoff,
# the only beat with no keypress, where the presenter and the volunteer
# trade places over a screen that was doing nothing. NOT `h` -- the Python
# window's `h` is home, and one letter meaning two things on one card is a
# trap for an operator who is already having a bad minute.
_N_KEYS = 29
check(f"the README key table has all {_N_KEYS} keys", len(_rk) == _N_KEYS,
      f"{len(_rk)} keys" if len(_rk) == _N_KEYS
      else f"{len(_rk)} keys, expected {_N_KEYS}: {sorted(_rk)}")
check(f"the recovery card key tables have all {_N_KEYS} keys", len(_ck) == _N_KEYS,
      f"{len(_ck)} keys" if len(_ck) == _N_KEYS
      else f"{len(_ck)} keys, expected {_N_KEYS}: {sorted(_ck)}")
check("every card key is in the README table", not (_ck - _rk),
      f"missing from README: {sorted(_ck - _rk)}")
check("every README key is on the card", not (_rk - _ck),
      f"missing from the card: {sorted(_rk - _ck)}")

print("\n=== 1b2. THE CARD'S WRONG-CWD ROW MATCHES WHAT run.sh ACTUALLY DOES ===")
# The card said the symptom was "Nothing happens, no output at all". That is a
# FOSSIL of run.sh's own documented bug #2 -- `[ -d venv ] && source ...` as a bare
# statement returned 1, the EXIT trap killed the process group, and you got exit
# code 144 with no output. Lines 18-24 fixed that with a real if/elif/else. Today
# run.sh self-locates (`cd "$(dirname "$0")"`), so from a foreign directory:
#   ~/Wheelgentic/run.sh        -> works, lands in the repo
#   bash ~/Wheelgentic/run.sh   -> works, lands in the repo
#   ./run.sh               -> "no such file or directory", loud and immediate
# A card row whose SYMPTOM never occurs gets discarded by the operator reading it
# under pressure, even when its FIX is right. Pin the row to the behaviour.
# MATCH AN UNCOMMENTED LINE, not the bare string. My first version was
# `'cd "$(dirname "$0")"' in runsh`, and the plant that commented the line out
# left it GREEN -- the substring survives inside `# cd "$(dirname "$0")"`. A guard
# that matches its own commented-out target is the tautology this file has now
# been bitten by ten times. Require the statement to be live code.
_selfloc = [l for l in runsh.splitlines()
            if l.strip().startswith('cd "$(dirname "$0")"')]
check("run.sh still self-locates (uncommented)", len(_selfloc) == 1,
      f"{len(_selfloc)} live `cd $(dirname $0)` lines -- if it stops self-locating, "
      "the card's wrong-cwd row needs rewriting again")
check("the card does not claim a SILENT failure for the wrong cwd",
      "Nothing happens, no output at all" not in card,
      "run.sh's bare-venv bug produced that symptom and was fixed; "
      "the real failure is a loud 'no such file or directory'")
_cwd_row = [l for l in card.splitlines() if "not in `~/Wheelgentic`" in l]
check("the wrong-cwd row exists to check", len(_cwd_row) == 1,
      f"{len(_cwd_row)} rows matched")
if _cwd_row:
    check("the wrong-cwd row names the real symptom",
          "no such file or directory" in _cwd_row[0],
          "the operator greps the card for what they SAW on screen")

print("\n=== 1c. THE DOCS AGREE ON WHICH REPLAY FILE TO USE ===")
# THE CARD POINTED AT THE WEAKER ARTIFACT. Two replay files exist and both
# load, so nothing 404s and no guard noticed -- but they are not equivalent.
# Measured elbow-x standard deviation: synthetic.jsonl 5.06 over a 16px range
# (hand-generated, effectively a static dot), good_run.jsonl 34.94 over 106px
# (generated through the real pipeline). RECOVERY-CARD sent the operator to
# the fixture while README and DEMO-SCRIPT named the real one.
#
# This is the same drift class that left the README key table seven keys
# behind the code: a fact stated in several places, guarded in none.
_recovery = re.findall(r"REPLAY=(recordings/[\w.-]+)", card)
_other = re.findall(r"REPLAY=(recordings/[\w.-]+)", readme + demo)
check("the recovery card names a replay file", len(_recovery) > 0)
for _r in set(_recovery):
    check(f"the card's replay artifact is the real-pipeline one ({_r})",
          _r == "recordings/good_run.jsonl",
          "synthetic.jsonl is hand-generated; the card is the artifact an "
          "operator reads under pressure and must name the best one")
for _r in set(_recovery + _other):
    check(f"replay file exists: {_r}", os.path.exists(_r))

print("\n=== 1d. THE CARD'S CLAMP THRESHOLD MATCHES THE CODE'S ===")
# THE CARD TOLD THE OPERATOR TO RECALIBRATE OVER NORMAL DRIFT. arm.py warns
# whenever a target is relocated more than 50mm, and its own comment says
# "Small clamps are normal (the forearm drifts past a box edge). A large one
# means the transform is wrong." The card quoted only `clamped 400mm` -> "Your
# homography is wrong. Recalibrate. Do not ignore this line."
#
# Measured on the card's OWN camera-dead fallback
# (REPLAY=recordings/good_run.jsonl under the test-fixture homography): 48% of
# targets fall below the BOX xmin=120 floor and clamp at 51-54mm, 0.63/sec.
# So the documented fallback continuously trips a do-not-ignore line, and the
# operator either burns 90s recalibrating for nothing or learns to ignore a
# warning that also fires for genuinely broken transforms.
_arm = open("py/arm.py").read()
_thr = re.search(r"if d > (\d+(?:\.\d+)?):", _arm)
check("arm.py states a clamp-warning threshold", _thr is not None)
if _thr:
    check(f"the card explains the {_thr.group(1)}mm-scale case, not only 400mm",
          "50-60mm" in card or "50mm" in card,
          "the card sends the operator to a 90s recalibration for normal drift")
check("the card says a small repeating clamp is survivable",
      "Keep going" in card,
      "an operator on the replay fallback sees this warning continuously")

print("\n=== 1e. THE CARD SAYS REPLAY POPS ARE MANUAL-ONLY ===")
# THE FALLBACK LOOKS LIKE IT WORKS AND THEN DOES NOTHING. replay_loop() only
# calls arm.set_target(); the APPROACH/SCRUB state machine and all three
# fire() calls live inside vision_loop(), which replay never runs. Measured:
# press 's' on replay and the FSM prints "ARMED (from the projector)" and then
# emits ZERO pops in 36 seconds, even with --no-contact-gate. replay_loop also
# has no cv2 window, so the Python-side 1/2/3 is dead there as well.
#
# The browser's 1/2/3 DOES work on replay -- verified end to end, 0% -> 100%,
# 3/3 splotches gone -- so the fallback is usable, but only that way. An
# operator who presses 's', sees ARMED and waits is stuck at 0% for the rest
# of the demo. test_remote_arm asserts "the FSM actually armed" and stops
# exactly where this gap begins, which is why it shipped unnoticed.
_sb_src = open("py/scrubbot.py").read()
_replay_fn = _sb_src[_sb_src.index("def replay_loop"):]
_replay_fn = _replay_fn[:_replay_fn.index("\ndef ")] if "\ndef " in _replay_fn else _replay_fn
# BOUND THE SLICE. The next two assertions are NEGATIVES over this region, and a
# negative passes both when the region is right and when it silently SHRINKS.
# That is the `_fin` failure: its `split("    finally:")[-1]` collapsed from 1157
# chars to 14 when a later try/finally appeared, and every check inside it would
# have passed vacuously. Measured here at 2,820 chars.
check("the replay_loop slice is a function body, not a fragment",
      1500 < len(_replay_fn) < 6000, f"{len(_replay_fn)} chars")
check("replay_loop still does NOT drive the scrub cycle",
      "fire(" not in _replay_fn and "APPROACH" not in _replay_fn,
      "if replay grew a real cycle, this card row is now WRONG -- rewrite it")
check("the card warns that 's' does nothing on replay",
      "ARMED` and nothing else happens" in card or "nothing else happens" in card,
      "an operator presses s, sees ARMED, and waits at 0% forever")
# ROW-SCOPED, not file-wide. The phrase appears in TWO card rows (43 "Camera
# dead / black" and 44 "on replay, s says ARMED"), so deleting it from row 43 --
# the row that exists to tell an operator with a dead camera where the working
# pop keys are -- left the old file-wide check GREEN. Row 43's label is 1x.
_cam_row = [l for l in card.splitlines() if "Camera dead / black" in l]
check("the camera-dead row exists to check", len(_cam_row) == 1,
      f"{len(_cam_row)} rows matched -- a row-scoped check needs exactly one")
if _cam_row:
    check("the camera-dead row points at the projector keys for replay pops",
          "`1` `2` `3` on the projector" in _cam_row[0],
          "the only pop path that works in replay mode, and this is the row an "
          "operator reads when the camera is the thing that died")

print("\n=== 1f. HOVER NEEDS A RESTART, AND THE DOCS SAY SO ===")
# OPEN-QUESTIONS #2 said "Both are one config edit and both are tested". Only
# half true: contact_depth_mm hot-reloads, but --no-contact-gate is an argparse
# flag with ZERO CFG.data.get() reads, so hover cannot be enabled from
# config.json at all. An operator flipping to hover mid-setup, per the doc,
# gets the depth change and not the gate change.
_sb2 = open("py/scrubbot.py").read()
_cfg_reads = _sb2.count('CFG.data.get("no_contact_gate"') \
    + _sb2.count("CFG.data.get('no_contact_gate'")
check("--no-contact-gate is still CLI-only (0 config reads)", _cfg_reads == 0,
      "if it became config-readable, OPEN-QUESTIONS #2 can drop the restart")
check("OPEN-QUESTIONS says hover needs a restart",
      "PLUS a restart" in openq or "and restart to do it" in openq,
      "the doc claimed hover was one config edit")

print("\n=== 1g. PORT= DOES NOT ISOLATE AN INSTANCE, AND run.sh SAYS SO ===")
# run.sh's comment said PORT was overridable "so tests can run run.sh without
# colliding" -- which reads as whole-instance isolation. It is not: the
# websocket is hardcoded at :8765 in BOTH py/scrubbot.py and web/main.js, so a
# second run.sh on a different PORT serves the page and then dies with
# "[ws] CANNOT BIND". Measured on a fresh clone: PORT=8100 gave http 200 and a
# dead socket.
#
# The tell was already in the tree: test_runsh.py WAITS for :8765 to be free
# before starting, rather than trusting PORT to separate instances.
_rs = open("run.sh").read()
_sb3 = open("py/scrubbot.py").read()
_mj3 = open("web/main.js").read()
check("the websocket is still hardcoded in scrubbot", '"127.0.0.1", 8765' in _sb3,
      "if it became configurable, run.sh's PORT comment can be simplified")
check("and hardcoded in the page", ":8765" in _mj3)
check("run.sh says PORT moves the PAGE ONLY",
      "MOVES THE PAGE ONLY" in _rs,
      "the old wording implied PORT isolates a whole instance")

print("\n=== 1h. THE CARD AND THE DEMO SCRIPT AGREE ON MID-CYCLE RESCUE ===")
# THE TWO DOCS CONTRADICTED EACH OTHER. DEMO-SCRIPT's 1:22 beat says "if a
# splotch misses: press 1, silently" -- during a cycle armed at 1:06. The card
# said "Pick one: arm it and let it run, OR tap 1/2/3. Never both."
#
# The code supports the rescue: main.js flashes "THIS CYCLE WILL RESET IT"
# and pops anyway, and test_cycle_conflict pins that as correct. The
# card's symptom is real but MISTIMED as a prohibition: fire_reset() runs in
# the RETREAT->IDLE transition, 2.5s after the scrub ends, so a pop at 1:22
# survives through the 1:44 finale and the reset lands after it.
check("the demo script still rehearses the mid-cycle rescue",
      "if a splotch misses" in demo,
      "if this beat is gone, the card's rescue row can go too")
check("the card no longer bans it outright", "Never both" not in card,
      "the card contradicted the script the operator is rehearsing")
check("and the card explains the rescue is supported",
      "Supported — do it" in card or "Supported" in card)
check("main.js still WARNS rather than blocking",
      "CYCLE RUNNING" in js and "isCycleLive" in js,
      "1/2/3 must never be gated -- it is the crash fallback")

print("\n=== 1i. avatar.js's HEADER MATCHES THE SHIPPED MODEL ===")
# The header said "WHY THIS MODEL: skins:0. VERIFIED by inspecting the GLB --
# it is NOT a skinned mesh, it is six RIGID mesh parts". True of the BLOCKY
# pack this file first loaded; false from the moment it swapped to the Kenney
# mini pack, which reports skins=2 and 32 animations. It survived the swap for
# weeks while limbLocalBox() -- thirty lines below it -- existed precisely
# BECAUSE the mesh is skinned. A VERIFIED claim in a header is the one a future
# reader is least likely to re-check.
import json as _json_hdr
import struct as _struct
import re as _re_hdr
_av_hdr = open("web/avatar.js").read()
_m_hdr = _re_hdr.search(r"const CAST = \[([^\]]+)\]", _av_hdr)
_first = _re_hdr.findall(r"'([^']+)'", _m_hdr.group(1))[0] if _m_hdr else None
_glb = os.path.join("web/assets", _first + ".glb") if _first else None
if _glb and os.path.exists(_glb):
    with open(_glb, "rb") as _f:
        _f.read(12)
        _jl = _struct.unpack("<I", _f.read(4))[0]
        _f.read(4)
        _gj = _json_hdr.loads(_f.read(_jl))
    _skins = len(_gj.get("skins", []))
    check(f"the shipped model reports skins={_skins}", True)
    check("avatar.js does NOT still claim skins:0",
          "skins:0. VERIFIED" not in _av_hdr,
          "the header describes a model this file no longer loads")
    if _skins > 0:
        check("and avatar.js actually detects the skinned mesh",
              "o.isSkinnedMesh" in _av_hdr,
              "the header claims a skinned rig; the code must find one")

print("\n=== 1i2. BOTH SPLOTCH-PLACEMENT BRANCHES ARE LIVE AND AGREE ===")
# MEASURED, by reading every return in limbLocalBox:
#   448 (rigid path)   -> { len, wid, cx, along: 'y' }        -- NO `from`
#   431 (skinned path) -> { len, wid, cx, along, from, to, offA, offB, offC }
#   393 -> a cached copy of one of the above
# addSplotch gates on `L.along && L.from !== undefined`, so the test splits
# skinned-vs-rigid exactly: the shipped mini pack (skins=2) takes the dynamic
# axis, the rigid Kenney pack falls back to -Y. NEITHER branch is dead, which is
# why the comment above addSplotch still documents the -Y geometry -- it
# describes the FALLBACK, not the shipped path. avatar.js's limbAxis note records
# the same split ("the rigid Kenney pack ran limbs down -Y ...; the mini pack's
# arm bone runs along +X"). The two branches must keep placing dirt over the same
# span of the limb, which the comment asserts in prose and nothing checked.
_av2 = open("web/avatar.js").read()

_rng = len(re.findall(r"0\.20 \+ t \* 0\.65", _av2))
check("both splotch branches use the same 0.20 + t*0.65 range", _rng == 2,
      f"{_rng} occurrences -- the comment says BOTH code paths use it. One means "
      "a branch drifted and dirt lands differently per pack; three means a third "
      "placement site appeared and this guard no longer covers them all")

_from_sites = [_i + 1 for _i, _l in enumerate(_av2.splitlines())
               if re.search(r"\bfrom:", _l)]
check("from/to are supplied at exactly one site", len(_from_sites) == 1,
      f"assigned at lines {_from_sites} -- addSplotch's branch test gates on "
      "L.from, so a second supplier silently changes which branch runs")

check("the branch still gates on L.from, not on along alone",
      "if (L.along && L.from !== undefined)" in _av2,
      "the rigid return supplies along:'y' with no from -- gating on along "
      "alone sends the rigid pack down the skinned path, where L.to is "
      "undefined and every splotch lands at NaN")

check("the rigid return still supplies along without from",
      re.search(r"cx: \([^)]*\) / 2 \|\| 0, along: 'y'", _av2) is not None,
      "if the rigid path gained a `from`, the -Y fallback becomes unreachable "
      "and the comment block above addSplotch stops describing anything live")

print("\n=== 1j. THE COLD-BOOT BLOCK COVERS A MISSING CALIBRATION ===")
# MEASURED ON A FRESH CLONE: ./run.sh serves the page (200) and then scrubbot
# EXITS -- "No homography.pkl. Run: python py/calibrate.py", socket never up,
# [main] clean shutdown. homography.pkl is gitignored, so EVERY fresh clone
# hits this, and the card's COLD BOOT block said only "./run.sh" with a
# "No venv?" footnote covering the other missing prerequisite.
#
# The asymmetry is real and deliberate: replay_loop() has an `except SystemExit`
# fallback (screen-only, and it says so); vision_loop() has NONE, because
# calib.load() refuses to fabricate a transform that would put the sponge ~30cm
# off a person's forearm. Documented rather than changed -- that guard is the
# reason a real arm never runs on a made-up calibration.
_sb5 = open("py/scrubbot.py").read()
_vl = _sb5.split("def vision_loop")[1].split("\ndef ")[0]
_rl2 = _sb5.split("def replay_loop")[1].split("\ndef ")[0]
# BOUND BOTH SLICES -- see the note at the replay_loop slice above. `_vl` carries a
# NEGATIVE ("except SystemExit" not in it) over 23,301 chars, so a shrunken slice
# passes it for free; `_rl2`'s is positive and would red on its own, but bound it
# too so the pair stays independent rather than one covering for the other.
check("the vision_loop slice is a function body, not a fragment",
      10000 < len(_vl) < 40000, f"{len(_vl)} chars")
check("the replay_loop slice (2nd read) is a function body, not a fragment",
      1500 < len(_rl2) < 6000, f"{len(_rl2)} chars")
check("vision_loop still REFUSES a missing calibration",
      "except SystemExit" not in _vl,
      "if it now degrades, the card's cold-boot note must change")
check("replay_loop still degrades instead", "except SystemExit" in _rl2)
check("the card warns a fresh clone needs calibrate.py first",
      "No `homography.pkl`?" in card and "calibrate.py" in card,
      "a fresh clone boots to a served page and a dead socket")

print("\n=== 1k. CALIBRATION.md NAMES THE SENTINEL AND THE CAMERA GATE ===")
# CALIBRATION.md told the operator `rm -f homography.pkl` and said NOTHING
# about .homography-is-synthetic -- while the fixture's own message says
# "Delete both files". calibrate.py retires the sentinel itself on a successful
# solve, which is the right fix, but the doc never said so, and a banner
# screaming "TEST FIXTURE" over a REAL calibration teaches the operator to
# ignore the one thing standing between the sponge and a forearm 30cm off.
#
# It also never said the procedure needs a real camera: calibrate() and
# verify() open cv2.VideoCapture directly with no CAM=fake path, unlike
# tools/tune_dirt.py which has one. Nobody can rehearse the clicking here.
_cal_doc = open("docs/CALIBRATION.md").read()
_cal_src = open("py/calibrate.py").read()
check("calibrate.py still retires the fixture sentinel",
      "A REAL CALIBRATION RETIRES THE FIXTURE SENTINEL" in _cal_src,
      "every later run would call a real calibration a fixture")
check("CALIBRATION.md says so", ".homography-is-synthetic" in _cal_doc,
      "the operator is told to rm one file and left with the other")
# THE CARD PROMISES A TAPE CHECK THAT AN ENV VAR CAN DELETE. calibrate.py gates
# verify() behind SKIP_VERIFY, and that branch used to be a SILENT skip -- 0
# print() calls, empty else -- while the card promises the tape check at two
# separate rows. An operator who inherited the variable got an unvalidated
# homography and no warning. Measured before the fix: SKIP_VERIFY in 0 tests, 0
# tools, 0 README; "tape" in 0 tests.
# A SUBSTRING TEST HERE PASSED A PLANT. Replacing the gate with `if True:` left
# 'SKIP_VERIFY' in the file -- it still appears in the banner text and in the
# comment below it -- so the check could not tell a live gate from prose about
# one. That is the same fault as the old '"mirror"' in _cfg substring test two
# records up. Parse it: require an If whose test names SKIP_VERIFY and whose
# else-branch is non-empty, which is what makes the skip loud.
import ast as _ast
_cal_tree = _ast.parse(_cal_src)
_sv_gates = [_n for _n in _ast.walk(_cal_tree)
             if isinstance(_n, _ast.If) and "SKIP_VERIFY" in _ast.unparse(_n.test)]
check("calibrate.py still GATES verify() on SKIP_VERIFY (parsed, not grepped)",
      len(_sv_gates) == 1,
      f"{len(_sv_gates)} gate(s) -- 0 means the tape check is unconditional or "
      "always skipped; 2+ means two places decide and they can disagree")
check("and that gate has a non-empty else, so the skip is LOUD",
      bool(_sv_gates) and bool(_sv_gates[0].orelse),
      "an empty else is a silent skip: the operator gets an unvalidated "
      "homography and no warning"
      if not (_sv_gates and _sv_gates[0].orelse)
      else f"{len(_sv_gates[0].orelse)} statement(s) on the skip path")
# AND THE ESC PATH IS THE OTHER DOOR TO THE SAME HOLE. verify() returns early when
# no point was clicked, and every print() sits after that return, so pressing ESC
# skipped the tape check as silently as the env var used to. Generator 5 found it by
# reading the SKIP_VERIFY commit adversarially.
check("verify()'s empty-click path SAYS SO too",
      "THE TAPE CHECK DID NOT COMPLETE" in _cal_src,
      "ESC with no click is a silent skip: no GO/NO-GO band, no warning, and the "
      "operator has no way to know the solve is unvalidated")

check("and the skip path SAYS SO instead of skipping silently",
      "THE TAPE CHECK DID NOT RUN" in _cal_src,
      "a silent skip leaves an unvalidated homography looking like a good one")
_card_names_it = "SKIP_VERIFY" in card
check("the card names SKIP_VERIFY where it promises the tape check",
      _card_names_it,
      "the operator is told to run a check an inherited variable removes"
      if not _card_names_it else "so the promise and the escape hatch agree")

check("and warns the procedure needs a real camera",
      "needs a real camera" in _cal_doc,
      "calibrate() has no CAM=fake path; this cannot be rehearsed dry")
check("calibrate.py genuinely has no fake-camera path",
      "FakeCapture" not in _cal_src,
      "if it gained one, the doc's warning is now wrong")

print("\n=== 1l. NO DOC CITES THE DISCREDITED fps FIGURE AS EVIDENCE ===")
# BOTH DOCS QUOTED A NUMBER THEY THEMSELVES RECORD AS MEANINGLESS. The old fps
# probe counted its OWN requestAnimationFrame callbacks, which keep firing at
# 60-120fps even when renderer.setAnimationLoop is completely dead -- it
# reported "121 fps" and would have reported 121 with ZERO application frames
# drawn. test_resilience.py now reads three.js's renderer.info.render.frame.
#
# DIRECTIVE.md line 202 and README.md both still cited "121fps" as PROOF the
# cartoon survives a SIGKILL, while the same two files elsewhere explain that
# the figure came from a broken instrument. Measured with the corrected probe:
# 240 app frames/s, stable -- 64 samples across five suite runs read 240, 242
# or 254, never anything near 121.
# MY FIRST VERSION OF THIS GUARD WAS A TAUTOLOGY. It asked
# `(not cites) or explains` -- but BOTH docs keep the explanatory passages
# (DIRECTIVE:403/603, README:121) that say WHY 121 was meaningless, so
# `explains` is permanently True and the whole expression can never be False.
# Plant-verified the wrong way round: putting 121fps back in the SIGKILL row
# still PASSED. Test the CITATION SITES instead -- the specific lines that
# offer a number as proof -- not whether the string appears anywhere.
_sigkill_row = [l for l in directive.splitlines()
                if "Survives Python SIGKILL" in l]
check("the SIGKILL row exists to check", len(_sigkill_row) == 1,
      f"{len(_sigkill_row)} rows matched")
if _sigkill_row:
    check("the SIGKILL row does not cite 121fps",
          "121" not in _sigkill_row[0],
          "it quotes the broken probe's number as proof of success")
_readme_claim = [l for l in readme.splitlines()
                 if "manual keys still worked" in l]
check("the README success line exists to check", len(_readme_claim) == 1,
      f"{len(_readme_claim)} lines matched")
if _readme_claim:
    check("the README success line does not cite 121fps",
          "121" not in _readme_claim[0],
          "it quotes the broken probe's number as proof of success")
# THIRD TIME, SAME WEAKNESS: a file-wide substring cannot tell WHERE a number
# sits. "240 app frames/s" appears 3 times in the directive (the row, the
# phase-log line, and the work-log entry ABOUT this fix), so with 121fps
# planted back into the row this still passed. Check the row itself.
if _sigkill_row:
    check("the SIGKILL row quotes the corrected measurement",
          "240 app frames/s" in _sigkill_row[0],
          "the row should carry a number the current probe can reproduce")
# FOURTH TIME, SAME WEAKNESS -- and this one nobody had guarded at all. FIVE
# surfaces state the arm pump's rate: py/arm.py:305 (39.9), the directive's
# summary table row, the directive's lessons list, the directive's own comment
# -audit entry (40.0), and five fresh runs here (39.9-40.0, naive loop
# 31.3-31.7). The two that disagreed were BOTH in the directive, at 39.5 -- and
# the comment-audit pass that re-measured 40.0 never noticed the table nine
# hundred lines above it still said 39.5. No guard covered any pump-rate figure,
# so it could drift forever. Check the CITATION SITES, not the file.
_pump_row = [l for l in directive.splitlines() if "deadline-pacing fix" in l]
check("the pump-rate table row exists to check", len(_pump_row) == 1,
      f"{len(_pump_row)} rows matched")
if _pump_row:
    check("the pump-rate row does not cite 39.5Hz",
          "39.5" not in _pump_row[0],
          "measured 39.9-40.0 across five runs; arm.py:305 says 39.9")
_pace_lesson = [l for l in directive.splitlines()
                if "absolute-deadline pacing" in l]
check("the pacing lesson exists to check", len(_pace_lesson) == 1,
      f"{len(_pace_lesson)} lines matched")
if _pace_lesson:
    check("the pacing lesson does not cite 39.5 Hz",
          "39.5" not in _pace_lesson[0],
          "same stale figure, second site, same fix")
# ...and the code's own figure must stay consistent with the docs, or the next
# person re-derives from whichever surface they happened to open.
check("arm.py still states the deadline-paced rate",
      "Deadline pacing measures 39.9 Hz" in _arm,
      "if this number moves, both directive sites above must move with it")

check("test_resilience still reads the RENDER counter, not rAF",
      "info.render.frame" in open("tests/test_resilience.py").read(),
      "if it reverts to rAF, every fps figure in the docs is meaningless again")

print("\n=== 2. EVERY FILE THE DOCS TELL YOU TO RUN EXISTS ===")
# Recordings referenced anywhere in the docs must be real files.
#
# POPULATION FLOOR FIRST. `for x in set(re.findall(...)): check(...)` reports
# success when the population is EMPTY -- the body never runs and the section
# prints clean. Measured today: 4 recording refs (card 2, demo 1, readme 1) and 12
# script refs (card 5, readme 7). If a doc rewording or a regex tweak empties
# either, these floors red instead of the section silently asserting nothing.
_rec_pop = sum(len(set(re.findall(r"recordings/[\w.-]+", d)))
               for d in (card, demo, readme))
_scr_pop = sum(len(set(re.findall(r"(?:py|tools|tests)/[\w.]+\.(?:py|sh)", d)))
               for d in (card, readme))
check("the docs reference recordings at all", _rec_pop >= 3,
      f"{_rec_pop} refs -- an empty population asserts nothing and still passes")
check("the docs reference scripts at all", _scr_pop >= 8,
      f"{_scr_pop} refs -- an empty population asserts nothing and still passes")
for doc, name in ((card, "RECOVERY-CARD"), (demo, "DEMO-SCRIPT"), (readme, "README")):
    for rec in set(re.findall(r"recordings/[\w.-]+", doc)):
        # NO EXEMPTIONS. backup.mp4 used to be excused here as "flagged as
        # not-yet-recorded", which is how a file the recovery card calls its
        # LAST RESORT stayed missing indefinitely -- the guard cheerfully
        # passed while the total-failure path pointed at nothing.
        # tools/record_backup.py records it; if it is gone, that is a failure.
        # THE DETAIL MUST AGREE WITH THE VERDICT. This printed
        # "MISSING -- run: ..." unconditionally, so four PASSING checks
        # each explained their own failure and a green suite read as
        # broken. Section 5d of this same file exists to catch that
        # shape; it had it too.
        _have = os.path.exists(rec)
        check(f"{name}: {rec}", _have,
              "present" if _have else "MISSING — run: python3 tools/record_backup.py")

for doc, name in ((card, "RECOVERY-CARD"), (readme, "README")):
    for scr in set(re.findall(r"(?:py|tools|tests)/[\w.]+\.(?:py|sh)", doc)):
        check(f"{name}: {scr} exists", os.path.exists(scr))

print("\n=== 3. EVERY CLI FLAG THE DOCS USE IS ACCEPTED ===")
# POPULATION FLOOR -- see the note in section 2. Measured 3 of ours today
# (--no-arm, --no-contact-gate, --scripted) after the chrome/tsc skip list:
# card names 2, README names 3, DEMO-SCRIPT names none. The floor equals the
# union, i.e. ZERO headroom, and that is deliberate -- a documented flag
# disappearing from every doc IS the drift this section exists to catch, so
# reddening on it is correct rather than a false positive.
# Flags that belong to OTHER programs. This section checks that every flag the
# docs use is accepted by scrubbot.py or run.sh, so anything aimed at a
# different binary has to be listed or it reddens on a true statement.
#
# --preview is scrub3d/viz.py's, the operator view. It arrived when the docs
# guard was widened to read DEMO-SCRIPT-V2.md as well as the old script, and
# it went red immediately -- on correct docs, because the flag is real and the
# program it belongs to is not the one being searched.
# --assign, --report and --selftest are scrub3d/live/arm_hw.py's, the operator
# checks for the real arms (README, "Checking the arms before a run"). Same
# shape as --preview above: real flags, on a program this section does not
# search. arm_hw.py --selftest is the one that exercises the whole driver
# against simulated boards, so it is the flag most worth having documented.
_NOT_OURS = ("--args", "--incognito", "--new-window", "--kiosk", "--noEmit",
             "--preview", "--port", "--no-3d", "--save", "--frames",
             "--assign", "--report", "--selftest")
_flag_pop = len([f for f in set(re.findall(r"--[a-z][a-z-]+", card + demo + readme))
                 if f not in _NOT_OURS])
check("the docs use our CLI flags at all", _flag_pop >= 3,
      f"{_flag_pop} flags -- an empty population asserts nothing and still passes")
for flag in sorted(set(re.findall(r"--[a-z][a-z-]+", card + demo + readme))):
    if flag in _NOT_OURS:
        continue
    known = flag in py or flag in runsh
    # The detail has to agree with the verdict. This printed "not found in
    # scrubbot.py or run.sh" on every PASS, so three green checks each
    # explained their own failure. Third instance of that shape found
    # tonight; section 5d of this file exists for exactly it.
    check(f"flag {flag} is accepted", known,
          ("scrubbot.py" if flag in py else "run.sh") if known
          else "not found in scrubbot.py or run.sh")

print("\n=== 4. CONFIG KEYS THE DOCS NAME ARE REAL ===")
# POPULATION FLOOR -- and this is the sharpest of the four: only ONE key is
# currently named in the card or demo script ("dirt_mode"), so a single rewording
# takes the population to zero and this whole section asserts nothing while
# printing clean.
_key_pop = len(set(re.findall(r'"(\w+_\w+)"', card + demo)))
check("the docs name at least one config key", _key_pop >= 1,
      f"{_key_pop} keys -- at 0 this section asserts nothing and still passes")
for key in sorted(set(re.findall(r'"(\w+_\w+)"', card + demo))):
    if key in cfg or key in py:
        check(f'config key "{key}"', True)
    else:
        check(f'config key "{key}"', False, "not in config.json or scrubbot.py")

print("\n=== 4b. THE SOURCE-OF-TRUTH POINTERS STILL POINT ===")
# The correction (TRUTH.md §7) exists because 20 hours of work
# drifted from Tyler's redirect and NOTHING in the repo could detect it. These
# four documents are the correction; CLAUDE.md and README.md are how a fresh
# session finds them. A dangling pointer here means the next session starts at
# the wrong file and the drift repeats -- which is the specific failure this
# whole section is insurance against.
_TRUTH_DOCS = ("docs/TRUTH.md", "docs/ROADMAP.md",
               "docs/STATE.md", "docs/DECISIONS.md")
for _d in _TRUTH_DOCS:
    check(f"{_d} exists", os.path.exists(_d),
          "the source-of-truth set is incomplete; see TRUTH.md section 9")

if os.path.exists("CLAUDE.md"):
    _claude = open("CLAUDE.md").read()
    check("CLAUDE.md names docs/TRUTH.md as the source of truth",
          "docs/TRUTH.md" in _claude and "SOURCE OF TRUTH" in _claude.upper(),
          "a session that reads CLAUDE.md must be sent to TRUTH.md")
    check("CLAUDE.md names docs/ROADMAP.md as the work source",
          "docs/ROADMAP.md" in _claude,
          "without this the next session picks work from the audit log again")
    # The redirect itself, quoted. If this sentence goes missing the strongest
    # standing instruction in the project is left to memory.
    #
    # STRIP THE BLOCKQUOTE CHROME, THEN THE WHITESPACE. The quote is a wrapped
    # markdown blockquote, so the raw text carries a ">" BETWEEN the words:
    # "...improving it > immensely." A plain whitespace collapse leaves that
    # marker in place and the search still fails -- which it did, on this
    # guard's first two runs, and I theorised about the cause twice before
    # printing both strings. The question is "is the redirect present", not
    # "is it free of markdown".
    _claude_flat = " ".join(re.sub(r"(?m)^\s*>\s?", "", _claude).split())
    check("CLAUDE.md carries Tyler's redirect verbatim",
          "do not work on tests work on improving it immensely" in _claude_flat,
          "the instruction that the drift violated")
else:
    check("CLAUDE.md exists", False, "the repo-level entry point is gone")

check("README.md points at docs/TRUTH.md",
      "docs/TRUTH.md" in readme,
      "the README docs table is a session's other entry point")

# DIRECTIVE.md used to claim the title itself. Two files claiming to be THE
# source of truth is worse than either claim alone.
_dir_head = directive[:1200]
check("DIRECTIVE.md no longer claims to BE the source of truth",
      "SUPERSEDED AS THE ENTRY POINT" in _dir_head,
      "two files cannot both be the entry point")
check("DIRECTIVE.md redirects to docs/TRUTH.md",
      "docs/TRUTH.md" in _dir_head)

_queue_head = open("docs/WORK-QUEUE.md").read()[:1400]
check("WORK-QUEUE.md is marked as an audit log, not a queue",
      "AUDIT LOG" in _queue_head.upper() and "docs/ROADMAP.md" in _queue_head,
      "this file self-feeds; without the demotion header it becomes the queue again")

print("\n=== 5. THE CARD'S HEADLINE FALLBACK ACTUALLY WORKS ===")
m = re.search(r"REPLAY=(\S+)", card)
check("the card names a REPLAY file", m is not None)
if m:
    check(f"and {m.group(1)} exists", os.path.exists(m.group(1)),
          "this is what an operator types when the camera dies")

print("\n=== 5c. THE TOOLS THE DOCS TELL YOU TO RUN STILL PARSE ===")
# Four places tell someone to run tools/record_backup.py, and "the file exists"
# is not the same claim as "it runs". A syntax error or a bad import would sail
# through the existence check above and only surface at hour 30, when the whole
# point of the tool is that hour 30 is too late.
import ast as _ast
for _t in ("tools/record_backup.py", "tools/tune_dirt.py", "py/calibrate.py"):
    if not os.path.exists(_t):
        check(f"{_t} exists", False)
        continue
    try:
        _ast.parse(open(_t).read())
        check(f"{_t} parses", True)
    except SyntaxError as _e:
        check(f"{_t} parses", False, f"line {_e.lineno}: {_e.msg}")

print("\n=== 5d. THE CARD'S VENDOR-404 ROW MATCHES WHAT THE SCREEN NOW SAYS ===")
# MEASURED: web/index.html's import map names ONLY
# "./vendor/three.module.min.js". It never mentions three.core.min.js. But
# three.module.min.js carries `from"./three.core.min.js"` 2x, so the browser
# fetches a file no HTML references -- which is why a missing core file used to
# read as "blank page, no obvious cause". vendor.sh calls it "THE ONE PEOPLE
# MISS" and its sanity loop verifies it.
#
# THE SYMPTOM CHANGED IN 42b296b AND THE CARD DID NOT. That commit added a
# classic-script watchdog to index.html: when no module evaluates -- which is
# exactly what a missing core file causes -- it paints "WHEELGENTIC FAILED TO START
# / A vendor file is missing or corrupt / Run ./vendor.sh, then reload".
# Re-measured by blocking **/three.core.min.js in Playwright: canvas False,
# __wheelgentic undefined, and the notice PAINTED with that text on screen. The card
# still said "Browser blank | Console will say ... 404", sending a panicking
# operator to a console they no longer need to open. A recovery card that
# describes the previous symptom is worse than one that says nothing, because it
# makes the operator doubt the screen in front of them.
#
# This guard now pins BOTH legs: the row must describe the notice the operator
# actually sees, AND still name the file plus the fix.
_vsh = open("vendor.sh").read()
_idx = open("web/index.html").read()
_mod = open("web/vendor/three.module.min.js").read()

_blank_row = [_l for _l in card.splitlines()
              if _l.startswith("| Screen says") and "FAILED TO START" in _l]
check("the card has a row for the vendor-404 notice", len(_blank_row) == 1,
      f"{len(_blank_row)} rows -- this guard reads one row, not the whole card")
_row = _blank_row[0] if _blank_row else ""

# The card's wording must match the string index.html actually paints. A card
# quoting text the page does not show is the same drift in the other direction.
_notice = "WHEELGENTIC FAILED TO START"
check("the row quotes the notice index.html paints",
      _notice in _row and _notice in _idx,
      f"row={_notice in _row} index.html={_notice in _idx} -- "
      "the card must quote what is on the screen, verbatim")

check("the row names three.core.min.js as the likely culprit",
      "three.core.min.js" in _row,
      "the card must name the file nothing else hints at")
check("the row's fix is re-running vendor.sh",
      "vendor.sh" in _row,
      "the documented fix is `./vendor.sh` -- the only thing that refetches it")

# The three facts that make the symptom what the card says it is:
check("index.html does NOT name three.core.min.js",
      "three.core.min.js" not in _idx,
      "if the HTML named it, a 404 would be obvious and the row would be wrong")
_core_imports = _mod.count('from"./three.core.min.js"')
check("but three.module.min.js DOES import it",
      _core_imports >= 1,
      f"{_core_imports} imports of from\"./three.core.min.js\" -- "
      "0 means nothing fetches core and the card's symptom is impossible")

# SCOPED TO THE curl LINE. A whole-file needle was BLIND here: deleting the
# fetch left the sanity loop's mention, which satisfied `in _vsh` on its own.
# MEASURED -- the fetch and the verification are separate claims and
# each has to be answered by its own site.
_fetch_lines = [_l for _l in _vsh.splitlines()
                if "curl" in _l and "three.core.min.js" in _l]
check("vendor.sh actually fetches three.core.min.js",
      len(_fetch_lines) == 1,
      f"{len(_fetch_lines)} curl lines name it -- the card sends you to "
      "vendor.sh, so vendor.sh must be the thing that pulls the file")

# SCOPED TO THE SANITY LOOP, not a whole-file occurrence count.
# MEASURED: a `>= 2` file-wide floor was BLIND here. The curl line
# names the file twice (the -o target and the URL), so the occurrence count is 3,
# and deleting the entire verification site still left 2. `grep -c` says "2"
# because it counts LINES, which is what misled the first draft. Slice the loop.
_sanity = _vsh.split("for f in", 1)[-1].split("; do", 1)[0] if "for f in" in _vsh else ""
check("vendor.sh has a sanity-check file list", 0 < len(_sanity) < 2000,
      f"{len(_sanity)} chars -- an empty or runaway slice asserts nothing")
check("and the sanity list verifies three.core.min.js landed",
      "three.core.min.js" in _sanity,
      "fetch-without-verify means vendor.sh can 'succeed' having written nothing, "
      "which is the exact failure the card's 'Browser blank' row describes")


print("\n=== 5d2. THE `c` ROW MUST NOT PROMISE A KEY THAT DOES NOT EXIST ===")
# MEASURED in a browser: before `c`, body.complete is False and #pct
# renders rgb(78,201,245) (blue); after `c`, complete is True and #pct is
# rgb(107,207,127) (green), with the fanfare played. The card and README both
# said "confetti only", one row under "`f` | force the finale", so the docs drew
# a distinction the code does not make. An operator reaching for a quiet burst
# mid-rescue got the whole victory beat while the counter still read 0%.
#
# Fixed in the DOCS, not the binding: a confetti-only path would be new code,
# and the card's own DO NOT forbids features after the freeze. This guard pins
# that decision -- if someone later adds a real confetti-only path, this fails
# and the docs get updated deliberately rather than drifting back.
_main_c = open("web/main.js").read()
_ckey = re.search(r"if \(e\.code === 'KeyC' && !e\.shiftKey[^)]*\) (\w+)\(\);", _main_c)
check("main.js still binds unmodified KeyC to a single call",
      _ckey is not None,
      "the binding moved -- re-measure what `c` does before trusting the docs")
if _ckey:
    check("and that call is finale(), the same one `f` reaches",
          _ckey.group(1) == "finale",
          f"`c` calls {_ckey.group(1)}() -- if this is no longer finale, the "
          "docs saying 'same as f' are now the drifted side")
for _doc, _nm in ((card, "RECOVERY-CARD"), (readme, "README")):
    _crow = [_l for _l in _doc.splitlines()
             if re.match(r"\|\s*\|?\s*`c`\s*\|", _l)]
    check(f"{_nm}: has exactly one `c` row", len(_crow) == 1, f"{len(_crow)} rows")
    if _crow:
        _r = _crow[0]
        check(f"{_nm}: the `c` row no longer claims confetti-only",
              "confetti only" not in _r,
              "measured: `c` calls finale() -- confetti, fanfare, green counter")
        check(f"{_nm}: and it points at `f` instead",
              "`f`" in _r,
              "the row must say what the key actually does, not a distinct effect")

print("\n=== 5e. 'DEMO SURVIVES MUTED' IS A TRY/CATCH INVARIANT, NOT A HOPE ===")
# MEASURED: the card tells the operator a skipped `Enter` is
# survivable. That is true by construction, not by luck: play() early-returns on
# !audioReady, so with Enter never pressed no zzfx call is reached at all; and
# every zzfx/resume call site is individually wrapped (resume, silent primer,
# fanfare, SFX[name]) so even a thrown AudioContext error cannot take a frame
# down. If someone unwraps a call site or deletes the early return, the card's
# advice becomes a lie -- this catches it.
_juice = open("web/juice.js").read()

_muted_row = [_l for _l in card.splitlines() if _l.startswith("| No sound")]
check("the card still has a 'No sound' row", len(_muted_row) == 1,
      f"{len(_muted_row)} rows -- this guard reads one row")
check("the row promises the demo survives muted",
      "Demo survives muted" in (_muted_row[0] if _muted_row else ""),
      "the operator is told to narrate over it -- the code must back that up")

check("play() early-returns when audio was never unlocked",
      "if (!audioReady) return;" in _juice,
      "without this, a skipped Enter reaches zzfx with no live context")

# Every audio call site wrapped. Counting, not naming: a new unwrapped site
# should fail this rather than sail past a fixed list of line numbers.
#
# Judged by BRACE DEPTH, not by "is 'try' on this line". juice.js already uses
# the multi-line `try {` / newline / `catch` shape elsewhere, so a single-line
# substring test would red-flag a correctly-wrapped audio call written that way.
# MEASURED: this walker scores the real file 0 unwrapped, CATCHES an
# unwrapped SFX[name] call, and ACCEPTS the same call re-wrapped across 4 lines.
def _unwrapped_audio(_src):
    _depth, _try_depths, _bad = 0, [], []
    for _i, _line in enumerate(_src.splitlines()):
        _code = _line.split("//")[0]                   # ignore trailing comments
        if re.search(r"\btry\s*\{", _code):
            _try_depths.append(_depth)
        if ("zzfx(" in _code or ".resume()" in _code) and not _try_depths:
            _bad.append((_i + 1, _line.strip()))
        for _ch in _code:                              # depth AFTER classifying
            if _ch == "{":
                _depth += 1
            elif _ch == "}":
                _depth -= 1
                if _try_depths and _depth <= _try_depths[-1]:
                    _try_depths.pop()
    return _bad

_sites = [_l for _l in _juice.splitlines()
          if ("zzfx(" in _l.split("//")[0] or ".resume()" in _l.split("//")[0])]
check("juice.js has audio call sites to check at all", len(_sites) >= 3,
      f"{len(_sites)} sites -- an empty population asserts nothing and passes")
_unwrapped = _unwrapped_audio(_juice)
check("every audio call site is inside a try/catch", not _unwrapped,
      f"{len(_unwrapped)} unwrapped at lines {[_n for _n, _ in _unwrapped]} -- "
      "one throw kills the frame and 'Demo survives muted' stops being true")


print("\n=== 6. THE VENDORED ZZFX PATCH IS INTACT ===")
# ZzFXMicro keeps its AudioContext in a script-scope `let`, so vendor.sh
# appends a line publishing it on window. A re-download that drops that line
# silently kills every sound while the visuals still look perfect -- and the
# committed file must match what vendor.sh WOULD produce, or the next
# ./vendor.sh quietly changes behaviour.
_zz = open("web/vendor/zzfx.js").read()
check("the vendored zzfx publishes its globals",
      "window.zzfx = zzfx; window.zzfxX = zzfxX; window.zzfxV = zzfxV;" in _zz,
      "without this every sound is silent")
_vs = open("vendor.sh").read()
check("vendor.sh re-applies the patch on download",
      "window.zzfx = zzfx; window.zzfxX = zzfxX; window.zzfxV = zzfxV;" in _vs,
      "otherwise ./vendor.sh silently reverts it")
check("the committed file ends with exactly that patch",
      _zz.rstrip().endswith(
          "window.zzfx = zzfx; window.zzfxX = zzfxX; window.zzfxV = zzfxV;"),
      "the patch must be the LAST thing in the file")

print("\n=== 4b2. EVERY NO-LINK MESSAGE NAMES A KEY THE CARD EXPLAINS ===")
# Three NO-LINK messages, three DIFFERENT python keys: 's' to start, SPACE to
# stop, 'r' to clear. Each fires when the socket is down -- i.e. exactly when the
# operator is already scrambling and reaching for the printed card. Only the
# SPACE one was pinned (test_cycle_conflict.py), and the shift+C -> "press r"
# case was not on the card AT ALL: an operator would read a message the card
# never mentions while an arm sat estopped on a person's forearm.
for _msg, _remedy, _why in (
    ("NO LINK \u2014 press s in the python window", "press `s` in the Python window",
     "the projector could not start a cycle and the card must say where to"),
    ("NO LINK \u2014 HIT SPACE IN THE PYTHON WINDOW / CUT POWER", "hit SPACE in the Python window",
     "this is the estop path; an unexplained message here is the worst case"),
    ("NO LINK \u2014 press r in the python window", "press `r` in the python window",
     "clearing an estop from the projector with the socket down"),
):
    check(f"main.js still shows: {_msg[:34]}...", js.count(_msg) == 1,
          f"{js.count(_msg)} occurrences -- the card quotes this wording")
    check(f"the card explains the remedy ({_remedy[:28]}...)",
          _remedy.lower() in card.lower(), _why)

print("\n=== 4c. FOUR SURFACES MUST AGREE ON WHICH SIGNALS ARE HANDLED ===")
# Same gap the pump rate had: several surfaces state the same fact and NOTHING
# pinned them together. run.sh traps INT TERM HUP; main() registers
# _term_handler three times; disarm_signals() ignores the same three; and
# test_signals.py drives and checks three. Today SIGINT was missing from the
# registrations for weeks while the other three surfaces already named it --
# the disarm loop covered SIGINT (second-signal protection) while the
# first-signal path did not, and the test's own loop checked exactly the two
# that were registered. A guard shaped like the code cannot see what the code
# omits, so assert the AGREEMENT, not any single list.
_sig_names = ("SIGTERM", "SIGHUP", "SIGINT")
_regs = [s for s in _sig_names
         if f"signal.signal(signal.{s}, _term_handler)" in _sb_src]
check("main() registers all three signals on _term_handler",
      len(_regs) == 3, f"registered: {_regs or 'none'}")
_disarm = re.search(r"for sig in \(([^)]*)\):", _sb_src)
check("disarm_signals() iterates a real tuple", _disarm is not None)
if _disarm:
    _d = [s for s in _sig_names if s in _disarm.group(1)]
    check("disarm_signals() covers the same three signals", len(_d) == 3,
          f"disarms: {_d or 'none'} -- a second signal aborts the retreat for "
          "any signal missing here")
    check("registrations and the disarm loop name the SAME set",
          set(_regs) == set(_d),
          f"registered {sorted(_regs)} vs disarmed {sorted(_d)}")
# run.sh is the other half: it forwards to children, so its trap list must not
# shrink below what scrubbot handles or a demo-day ^C leaves an arm on a person.
for _s in ("INT", "TERM", "HUP"):
    check(f"run.sh's trap still covers {_s}",
          re.search(r"trap cleanup EXIT[^\n]*\b" + _s + r"\b", runsh) is not None,
          "run.sh must forward every signal scrubbot retreats on")
# ...and the test that proves the retreat must drive all three, or two of the
# registrations above are asserted but never exercised.
_ts = open("tests/test_signals.py").read()
check("test_signals.py drives all three signals",
      all(f'signal.{s}, "{s}"' in _ts for s in _sig_names),
      "a registered signal nobody sends is an untested retreat path")

print("\n=== 5a. EVERY SAFETY FIX IS STILL IN THE FILE ===")
# A hand-rolled plant's restore silently reverted a fix to a pre-patch
# snapshot, and I then debugged the FEATURE for a full cycle before noticing
# the CODE was gone. Each line below is a safety fix that cost real work to
# find; if one disappears, say so loudly rather than waiting for the one test
# that happens to cover it.
for _needle, _where, _why in (
    ("def disarm_signals", "py/scrubbot.py",
     "a second ^C aborts the retreat with the sponge on the forearm"),
    ("disarm_signals()", "py/scrubbot.py",
     "the disarm exists but nothing calls it"),
    ("def _reset_cycle_state", "py/scrubbot.py",
     "cleaned-splotch state bleeds into the next volunteer's scrub"),
    ("targets = _uv_targets if _uv_targets else SPLOTCH_TS", "py/scrubbot.py",
     "manual 1/2/3 pops nothing in UV mode while the counter hits 100"),
    ('EVENT["link"] = "arm-lost"', "py/scrubbot.py",
     "a dead arm link never reaches the projector"),
    ("def link_ok", "py/arm.py",
     "the demo claims a scrub the arm never received"),
    ("def apply_torque_caps", "py/arm.py",
     "a brownout recovery runs with firmware-default torque"),
    ("if (held) return;", "web/main.js",
     "the CUT POWER banner is erased in under 150ms"),
    ("const r = recs[+e.key - 1]", "web/main.js",
     "manual keys miss the moved splotches in UV mode"),
    ("m.link === 'arm-lost'", "web/main.js",
     "the projector shows nothing when the arm dies"),
):
    _txt = open(_where).read()
    check(f"{_where}: {_needle[:44]}", _needle in _txt, _why)

print("\n=== 5a2. THE TESTS BLOCK THE MODEL THE APP ACTUALLY LOADS ===")
# test_degraded_boot blocks the character model's URL to prove the page still
# boots without it. When the model was swapped to Kenney's mini pack the test
# kept blocking the OLD filename -- so it blocked nothing, the page loaded
# fine, and the degraded-boot assertion failed on a path never exercised. A
# route-blocking test is only as good as its pattern, and nothing tied the two
# together.
import json
import re as _re2
_av = open("web/avatar.js").read()
# The path is built from a template literal now (the character is selectable),
# so the old single-quote regex found nothing and the guard failed on correct
# code. Match the CAST list instead -- that is the real source of truth for
# which models must exist.
_m2 = _re2.search(r"const CAST = \[([^\]]+)\]", _av)
check("avatar.js names a model file", _m2 is not None)
if _m2:
    _cast = _re2.findall(r"'([^']+)'", _m2.group(1))
    check("the cast is not empty", len(_cast) > 0)
    for _c in _cast:
        check(f"cast model exists: assets/{_c}.glb",
              os.path.exists(os.path.join("web/assets", _c + ".glb")))
    # test_degraded_boot must block the DEFAULT, which is what actually loads.
    _deg = open("tests/test_degraded_boot.py").read()
    check(f"test_degraded_boot blocks the default ({_cast[0]})",
          _cast[0] in _deg,
          "it is blocking a filename the app no longer loads")

print("\n=== 5b. THE BACKUP VIDEO IS NOT STALE ===")
# It went stale within an hour of first being recorded -- a UI change landed and
# the last-resort artifact still showed the old build. Nobody will remember to
# re-record at hour 30, so make it detectable.
#
# NOT BY MTIME. A fresh `git clone` stamps every file at checkout time, and the
# sub-second spread made web/*.js land AFTER the video -- I measured a clone
# false-firing on four files. Record the hashes the video was made from
# instead; content is the thing that actually matters and it survives a clone.
import glob as _glob
import hashlib as _hl
# web/style.css NEVER EXISTED. index.html loads ./hud.css, and the digest loop
# skips missing files silently -- so the real stylesheet, and robotarm.js (which
# main.js imports and which holds the arm spring), sat OUTSIDE this digest. A
# demo-visible regression in either would land while the guard said the backup
# video still matched. MEASURED: `find. -name style.css` -> nothing.
# tools/record_backup.py:199 carries the same tuple and must stay in step with
# this one -- a drifting pair stamps a digest the test cannot reproduce.
# EVERY MODULE THE PAGE LOADS, DISCOVERED rather than listed. The hardcoded
# tuple missed coverage.js, territories.js, vitals.js and voice.js -- so a
# change to the coverage sweep, the measured-body overlay, the ECG or the voice
# control left this green while the video showed the old behaviour. The comment
# below already records the same gap happening to hud.css and robotarm.js.
# A glob cannot drift from the directory the way a list can.
_WEB = tuple(sorted(_glob.glob("web/*.js"))) + ("web/index.html", "web/hud.css")
_STAMP = "recordings/backup.sources"


def _web_digest():
    h = _hl.sha256()
    for f in _WEB:
        if os.path.exists(f):
            h.update(f.encode())
            h.update(open(f, "rb").read())
    return h.hexdigest()


if not os.path.exists("recordings/backup.mp4"):
    check("the backup video exists", False,
          "run: python3 tools/record_backup.py")
elif not os.path.exists(_STAMP):
    check("the backup video records what it was built from", False,
          f"{_STAMP} missing — run: python3 tools/record_backup.py")
else:
    _want = open(_STAMP).read().strip()
    _have = _web_digest()
    # The detail used to print "web/ changed -- re-record" on EVERY run, so a
    # passing line told the operator to redo work the same line says is done.
    _fresh = _want == _have
    check("the backup video matches the current web/ sources",
          _fresh,
          f"digest {_have[:12]} matches the stamp" if _fresh
          else "web/ changed since it was recorded — "
               "re-run: python3 tools/record_backup.py")

print("\n=== 6b. NO ASSERTION IS WRITTEN TWICE ===")
# THE COUNT GUARD BELOW STRUCTURALLY CANNOT SEE A DUPLICATE. It counts check()
# lines live and compares them to the directive's figure, so a block pasted in
# twice inflates BOTH sides identically and the comparison stays happy.
# Measured: the ZZFX section existed at lines 311 AND 427 of this very file --
# byte-identical, same sha256, 19 lines, 3 duplicated labels -- while the
# directive said 429 assertions and the honest number was 426. The file that
# guards every other document had a section pasted into it twice, and its own
# arithmetic agreed with itself the whole time.
import collections as _coll
_dupe_hits = []
# THE LITERAL PREFIX MISSED 59 OF 500 LABELS. 'check("' is not a substring of
# check(f" or check(f', so every f-string label was invisible -- 11.8% of the
# suite, and 22 of them in THIS file, the one whose pasted-twice ZZFX section is
# why this section exists. Match the call shape instead of a fixed prefix.
#
# AND EXEMPT SIBLING-BRANCH PAIRS. Three labels are written twice deliberately,
# as two arms of ONE logical assertion: `config key "{key}"` at :567/:569 and
# `{_t} parses` at :590/:592 here, plus `scrub_offset survives {label}` at
# :237/:239 of test_scripted_and_config.py -- that last one a try/except whose
# try arm passes a VARIABLE, which is why an exemption keyed on two literals
# missed it. Exactly one arm runs per iteration. Measured by dry-run: a bare
# widening flags 3, this shape flags 0, and f-string labels stay visible.
_CHK_LABEL = re.compile(r"""check\(\s*f?("|')(.*?)\1""")
_CHK_FALSE = re.compile(r"""check\(\s*f?("|')(?:.*?)\1\s*,\s*False\b""")
for _tf in sorted(glob.glob("tests/test_*.py")):
    _seen = _coll.defaultdict(list)
    _false_arm = _coll.defaultdict(bool)
    for _i, _ln in enumerate(open(_tf), 1):
        _m = _CHK_LABEL.search(_ln)
        if not _m:
            continue
        _lab = _m.group(2)
        _seen[_lab].append(_i)
        if _CHK_FALSE.search(_ln):
            _false_arm[_lab] = True
    for _lab in list(_seen):
        _at = _seen[_lab]
        _tight = (max(_at) - min(_at)) <= 4
        if len(_at) == 2 and _tight and _false_arm[_lab]:
            del _seen[_lab]
    for _lab, _at in _seen.items():
        if len(_at) > 1:
            _dupe_hits.append(f"{os.path.basename(_tf)}:{_at} {_lab[:40]}")
check("no assertion label appears twice in any test file",
      not _dupe_hits,
      f"{len(_dupe_hits)} duplicated: {_dupe_hits[:3]}")

print("\n=== 6c. run_all.sh HAS EXACTLY ONE EXIT TRAP, CARRYING ALL THREE JOBS ===")
# Bash REPLACES an EXIT trap rather than appending, so a second `trap ... EXIT`
# anywhere in run_all.sh silently discards the first one's body. That is not a
# hypothetical: the lock removal used to sit in an earlier trap, the
# server-start trap replaced it, and /tmp/.wheelgentic-suite.lock outlived every
# run (same inode present after a clean exit) until it was merged into one
# body. run_all.sh:63-67 warns about this in prose -- a comment is not a guard,
# so assert the shape.
#
# COUNTING NOTE, because every naive instrument is wrong on this file. The word
# "trap" appears three times: twice inside that warning comment and once as the
# real command. `grep -c 'trap '` returns 3 and would fail a correct file;
# `grep -cE '\btrap\b[^;]*\bEXIT\b'` stops at the semicolon inside the body.
# Strip comments FIRST, then match `trap` only where a command can start.
def _code_only(_s):
    _out = []
    for _l in _s.split("\n"):
        _out.append("" if _l.lstrip().startswith("#") else _l.split("#", 1)[0])
    return "\n".join(_out)

_ra_src = open("tests/run_all.sh").read()
_ra_code = _code_only(_ra_src)
_ra_traps = re.findall(r"(?:^|;|&&|\|\||\bthen\b|\bdo\b)\s*(trap\b[^\n;]*)",
                       _ra_code, re.M)
_ra_exit = [_t for _t in _ra_traps if re.search(r"\bEXIT\b", _t)]
# Each detail string below is CONDITIONAL on its own verdict. Printing the
# failure consequence next to a PASS tells the 3am reader the opposite of the
# truth, and this test is the first thing the suite runs (tail is 8 lines).
check("run_all.sh installs exactly one EXIT trap",
      len(_ra_exit) == 1,
      "1, and bash would silently drop any second one" if len(_ra_exit) == 1
      else f"{len(_ra_exit)} found: {_ra_exit} -- a second trap DISCARDS the first")
_body_ok = bool(_ra_exit) and '"$CLEANUP"' in _ra_exit[0]
check("that trap's body is the accumulated $CLEANUP string",
      _body_ok,
      "accumulates, so new cleanup can be appended" if _body_ok
      else f"body is {_ra_exit[0] if _ra_exit else '(none)'} -- an inline body "
           "cannot accumulate; that is how the lock removal was dropped before")
_ra_cleanup = "\n".join(_l for _l in _ra_code.split("\n") if "CLEANUP=" in _l)
for _job, _label, _cost in (
    (r"kill \$SRV", "kill the :8000 server",
     "the static server outlives the suite and the next run serves stale web/"),
    (r"pkill -f 'py/scrubbot\.py'", "pkill a stray scrubbot",
     "a leftover scrubbot holds the serial port and the next arm test hangs"),
    (r"rm -rf '\$LOCK'", "remove the lock dir",
     "the lock leaks and every later run refuses to start"),
):
    _has = re.search(_job, _ra_cleanup) is not None
    check(f"$CLEANUP still does: {_label}",
          _has, "present" if _has else f"MISSING -- {_cost}")

print("\n=== 6. THE DIRECTIVE'S TEST COUNT IS THE REAL COUNT ===")
# A hardcoded tally in a doc drifts the moment anyone adds a test, and it is
# the number a tired person reads at 3am to decide whether the suite is
# complete. Assert EQUALITY, not a floor -- a floor blesses drift upward.
import re as _re
_runall = open("tests/run_all.sh").read()
_n_real = len(_re.findall(r'^run "', _runall, _re.M))
_m = _re.search(r"Full suite (\d+) tests\. ~(\d+) assertions", directive)
check("the directive states a test count", _m is not None)
if _m:
    _counts_agree = int(_m.group(1)) == _n_real
    # THE REMEDY MUST NAME THE REAL CAUSE. _n_real anchors on ^run ", so a run
    # line that gets INDENTED (wrapped in an if) or COMMENTED OUT while
    # debugging goes invisible and the count drops -- with the doc still
    # correct. The old detail said "update docs/DIRECTIVE.md when you add a
    # test" for that case too, and following it (26 -> 25) turns the suite
    # green with the disabled test invisible forever. Plant-verified both
    # ways: indent and comment each drop 26 -> 25.
    _hidden = _re.findall(r'^[ \t]*#?[ \t]*run "([^"]*)"', _runall, _re.M)
    _hidden = [h for h in _hidden
               if not _re.search(r'^run "' + _re.escape(h) + '"', _runall, _re.M)]
    if _counts_agree:
        _why = "counts agree"
    elif _hidden:
        _why = (f"{len(_hidden)} run line(s) INDENTED or COMMENTED OUT, not "
                f"counted: {_hidden[:3]} -- re-enable the test, do NOT edit the doc")
    else:
        _why = "update docs/DIRECTIVE.md when you add a test"
    check(f"directive says {_m.group(1)} tests, run_all.sh has {_n_real}",
          _counts_agree, _why)
    _n_asrt = sum(1 for ln in open_all_test_lines()
                  if "check(" in ln and "def check" not in ln
                  and not ln.strip().startswith("#"))
    # assertions drift constantly; allow a band but catch a big divergence
    # Detail was "more than 15 off" unconditionally, so a PASSING assertion
    # printed its own failure explanation. Verdict and explanation must come
    # from the same condition.
    _drift = abs(int(_m.group(2)) - _n_asrt)
    check(f"directive says ~{_m.group(2)} assertions, actual {_n_asrt}",
          _drift <= 15,
          f"drift {_drift} > 15 -- update docs/DIRECTIVE.md" if _drift > 15
          else f"drift {_drift}, within the band")

# ---- 7. A STRING THAT TELLS THE OPERATOR TO PRESS A KEY MUST NAME A KEY
# THAT IS ACTUALLY BOUND -------------------------------------------------
#
# Voice mode's readout said "PRESS V" for a week after the binding moved to
# `m`, because `v` had cycled the 12 characters since long before voice
# existed and the collision was fixed everywhere EXCEPT the string on screen.
# The projector was instructing the operator to press the key that swaps the
# person. Nothing caught it: every other guard here compares docs to code, and
# this is code telling a human something about itself.
#
# Only single-letter instructions are checked. "press shift+C first" and
# "press 1 2 3" name real bindings in forms this cannot parse, and inventing a
# parser for them would be more fragile than the thing it guards.
_web_js = ""
for _f in sorted(_glob.glob("web/*.js")):
    _web_js += open(_f).read()
_told = set(re.findall(r"['\"`]PRESS ([A-Z])['\"`]", _web_js))
for _k in sorted(_told):
    # Bound either as the physical code (e.code === 'KeyM', which is how every
    # operator key in main.js is written) or as the character.
    _bound = (f"Key{_k}" in _web_js
              or f"e.key === '{_k.lower()}'" in _web_js
              or f"e.key === '{_k}'" in _web_js)
    # Verdict and explanation from the SAME condition, which §6c of this file
    # exists to enforce -- a passing check printing its own failure reason is
    # how a green suite reads as broken.
    check(f"the screen says PRESS {_k}, and {_k.lower()} is bound", _bound,
          f"bound" if _bound else
          f"nothing binds {_k.lower()} -- the projector is telling the "
          f"operator to press a key that does nothing, or worse, one bound "
          f"to something else")

# ---- 8. THE BIG READOUT CANNOT OVERFLOW THE FRAME ----------------------
#
# #pct is clamp(56px, 9vh, 150px), so 97px on a 1080p projector, in Press
# Start 2P -- fixed width, roughly one em per glyph. 21 characters is 2037px
# against a 1920 frame and runs off both sides.
#
# Nearly shipped: "NO NETWORK FOR SPEECH" was written straight into it and
# caught with a calculator rather than a test. The element is shared by the
# percentage, the spoon and pill counts, the heart rate and the voice prompts,
# so every new string is another chance to do it again.
#
# 19 characters is 1843px, which clears the frame with a margin. Literals
# only: anything built at runtime from a number is short by construction.
_pct_writes = re.findall(
    r"""pct\.textContent\s*=\s*(?:on\s*\?\s*)?['"]([^'"]+)['"]""", _web_js)
_pct_writes += re.findall(
    r"""voiceFault\s*=\s*[^;]*?['"]([A-Z][A-Z ]{2,})['"]""", _web_js)
_PCT_MAX = 19
_too_long = sorted({t for t in _pct_writes if len(t) > _PCT_MAX})
check(f"every literal in the big readout is <= {_PCT_MAX} chars",
      not _too_long,
      f"{_too_long} -- at 97px on a 1080p projector that is "
      f"{max((len(t) for t in _too_long), default=0) * 97}px "
      f"against a 1920 frame" if _too_long
      else f"{len(set(_pct_writes))} literals, longest "
           f"{max((len(t) for t in _pct_writes), default=0)}")

print("\n" + "=" * 60)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
print("  DOCS MATCH THE CODE")
