"""The backend is in the repo, and the bake does not need /tmp.

WHY THIS EXISTS. `web/assets/body.json` is the file the whole territory
overlay is drawn from -- the per-arm cell counts, the region names, the
out-of-reach and contested totals that the counts panel prints and the voice
spot answer reads. It is produced by `tools/export_body.py`, which imports the
`scrub3d` package.

That package used to live only on its own branch, and the export loaded it
from `/tmp/s3d`, a worktree. `/tmp` is erased on reboot. So the bake behind
the most technically impressive thing on the projector depended on a directory
that could vanish between a rehearsal and demo day, and nothing would say so
until someone re-ran the export and got `scrub3d not found`.

The standing prompt calls merging that branch co-equal with the vision. This
guards the merge: the package is present, the export prefers it over any
worktree, and the pieces the bake actually calls still import and run.

WHAT THIS DELIBERATELY DOES NOT DO. It does not re-run the full bake. That
takes about six minutes -- it solves the four-arm partition twice, once per
body -- and a suite that slow is a suite that gets skipped. It checks the
inputs to that bake instead, which is where the /tmp regression would show.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAILS = []


def check(label, cond, detail=""):
    print(f"   {'    PASS' if cond else '*** FAIL'}  {label}"
          f"{'  [' + str(detail) + ']' if detail else ''}")
    if not cond:
        FAILS.append(label)


print("\n=== 1. THE PACKAGE IS IN THE REPO ===")
pkg = os.path.join(ROOT, "scrub3d")
check("scrub3d/ exists in the working tree", os.path.isdir(pkg), pkg)
# The four modules tools/export_body.py imports by name.
for mod in ("anatomy", "partition", "place_arms", "bodymodel"):
    check(f"scrub3d/{mod}.py is present",
          os.path.exists(os.path.join(pkg, f"{mod}.py")))

print("\n=== 2. THE BAKE DOES NOT REACH INTO /tmp ===")
src = open(os.path.join(ROOT, "tools", "export_body.py")).read()
# READ THE CANDIDATE LIST, NOT THE WHOLE FILE. The first version of this
# check compared the positions of two strings anywhere in the source and went
# red on correct code: the comment ABOVE the list explains the /tmp problem,
# so "/tmp/s3d" appeared 559 characters before the repo path while the actual
# lookup order was already right.
#
# The worktree is still a documented fallback, so its presence is fine. What
# must not come back is it being tried before the repo.
_cands = src[src.find("_CANDIDATES = ["):]
_cands = _cands[:_cands.find("]") + 1]
i_repo = _cands.find('os.path.join(ROOT, "scrub3d")')
i_tmp = _cands.find("/tmp/s3d")
check("the export resolves the in-repo copy", i_repo != -1,
      _cands.replace("\n", " ")[:90] if i_repo == -1 else "in _CANDIDATES")
check("and prefers it over the /tmp worktree",
      i_repo != -1 and (i_tmp == -1 or i_repo < i_tmp),
      "repo first" if i_tmp == -1 or i_repo < i_tmp
      else f"/tmp is tried first ({i_tmp} before {i_repo})")

print("\n=== 3. THE PIECES THE BAKE CALLS ACTUALLY RUN ===")
# Import through a subprocess with ONLY the repo on the path, so a stale
# /tmp/s3d on this machine cannot make a broken merge look fine.
probe = (
    "import sys, numpy as np;"
    f"sys.path.insert(0, {pkg!r});"
    "import anatomy, partition, place_arms;"
    "body, meshes = anatomy.anatomical_body();"
    "P, N, region, area = body.world_cells(only_scrubbable=False);"
    "bases = place_arms.ring_layout(2, body=body);"
    "print('CELLS', len(P), 'REGIONS', len({r.name for r in body.regions}),"
    "      'BASES', np.asarray(bases).shape, 'FINITE',"
    "      bool(np.all(np.isfinite(np.asarray(bases)))))"
)
r = subprocess.run([sys.executable, "-W", "ignore", "-c", probe],
                   capture_output=True, text=True, timeout=300)
out = (r.stdout or "").strip().splitlines()
line = out[-1] if out else ""
check("anatomy + place_arms run from the repo copy alone",
      r.returncode == 0 and line.startswith("CELLS"),
      line or (r.stderr or "").strip().splitlines()[-1:] or "no output")

if line.startswith("CELLS"):
    f = line.split()
    cells, regions = int(f[1]), int(f[3])
    # The numbers the counts panel and the docs both quote. If the body model
    # changes shape, the bake changes and every document naming 5824 is stale.
    check("the body still has the 5824 cells the bake exports",
          cells == 5824, cells)
    check("across the 13 regions the counts panel names",
          regions == 13, regions)
    # TWO, NOT FOUR, AND THAT IS A STALE EXPECTATION BEING CORRECTED RATHER
    # THAN A TEST BEING WEAKENED. This probe asked place_arms for a FOUR-arm
    # ring and checked the result was finite. The ring is synthetic -- it is
    # the fallback the bake uses when no rig file is present -- so the number
    # four in it came from the brainstorm's ask, not from any hardware.
    #
    # The rig the arms are bolted to is scrub3d/live/live_rig_openyam.json:
    # two arms, off a tape measure. The bake, the page and index.html now all
    # say two, and a test still demanding four would be the last file in the
    # repo holding the old count -- which is exactly the disagreement this
    # change exists to end.
    #
    # What is CHECKED here is unchanged: that place_arms runs from the repo
    # copy alone and returns finite placements. Only the count it is asked for
    # follows the measurement now.
    check("and the arm placements are finite",
          "FINITE True" in line, line[line.find("BASES"):])

print("\n=== 4. THE BAKED FILE MATCHES WHAT IS ON DISK ===")
# Not a re-bake: just that the artifact the page loads is present, parses, and
# carries the shape the SDK expects.
import json  # noqa: E402
for name in ("body.json", "body-b.json"):
    p = os.path.join(ROOT, "web", "assets", name)
    if not os.path.exists(p):
        check(f"{name} exists", False, p)
        continue
    d = json.load(open(p))
    c = d.get("cells", {})
    ok = all(k in c for k in ("pos", "normal", "area", "owner", "klass",
                              "region"))
    check(f"{name} carries every per-cell field", ok, list(c)[:6])
    check(f"{name} has 5824 owners", len(c.get("owner", [])) == 5824,
          len(c.get("owner", [])))
    # THE SCHEDULE, which the export threw away for a long time. solve()
    # returns (territories, planes, phases, report) and only [0] was taken, so
    # the conflict-graph colouring -- which arms can move at the same time --
    # was computed on every bake and written nowhere. The counts panel prints
    # it now, so it has to survive a re-bake.
    # EVERY ARM IN THE FILE, RATHER THAN THE LITERAL [0, 1, 2, 3].
    #
    # The old assertion demanded exactly four arms in the schedule, which was
    # true while the bake solved a four-arm ring and became false the moment it
    # solved the MEASURED two-arm rig. Hardcoding the count meant the test
    # could only ever agree with one rig -- so it would have to be edited again
    # on the next re-measure, and an assertion that needs editing whenever the
    # hardware is measured is not checking the hardware.
    #
    # THE PROPERTY IT WAS ACTUALLY DEFENDING IS UNCHANGED, and it is worth
    # keeping: solve() returns (territories, planes, phases, report) and the
    # export took only [0] for a long time, so the conflict-graph colouring --
    # which arms may move at the same time -- was computed on every bake and
    # written nowhere. What matters is that the schedule is present, non-empty,
    # and accounts for EVERY arm the same file describes. That is now read off
    # `arms` rather than typed, so it follows the rig instead of predating it.
    ph = d.get("phases")
    n_arms = len(d.get("arms") or [])
    ok = (isinstance(ph, list) and ph and n_arms > 0
          and all(isinstance(g, list) and g for g in ph)
          and sorted(a for g in ph for a in g) == list(range(n_arms)))
    check(f"{name} carries a schedule covering all {n_arms} arms", ok, ph)

print("\n=== 5. THE SCRIPT QUOTES THE SCHEDULE THE BAKE PRODUCES ===")
# DEMO-SCRIPT-V2.md tells the presenter, at 0:52, exactly what the bottom line
# of the counts panel will say for each body -- and pointing at a number is
# the whole reason that beat works. If a re-bake ever changes the schedule,
# the presenter reads a promise the screen does not keep, in front of judges.
#
# Note test_docs_match_code.py cannot catch this: it reads the OLD
# DEMO-SCRIPT.md, not the V2 file that is the actual run of show.
_script = os.path.join(ROOT, "docs", "DEMO-SCRIPT-V2.md")
if not os.path.exists(_script):
    check("DEMO-SCRIPT-V2.md exists", False, _script)
else:
    txt = open(_script).read()
    for name, label in (("body.json", "body A"), ("body-b.json", "body B")):
        d = json.load(open(os.path.join(ROOT, "web", "assets", name)))
        ph = d.get("phases") or []
        # The same string main.js builds for the panel.
        line = ", then ".join(" + ".join(f"ARM {a}" for a in g) for g in ph)
        check(f"the script quotes {label}'s schedule as the bake has it",
              line in txt, line)

print("\n" + "=" * 58)
if FAILS:
    print(f"  *** {len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("  THE BACKEND IS IN THE REPO AND THE BAKE IS SELF-CONTAINED")
