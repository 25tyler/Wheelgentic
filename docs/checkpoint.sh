#!/usr/bin/env bash
# docs/checkpoint.sh — the 20-minute heartbeat.
#
# Prints OBJECTIVE repo state, not my impression of it. Run every ~20 min and
# paste the result into DIRECTIVE.md §6. The point is to catch drift: going
# deep on the wrong thing without stepping back. That failure mode already
# cost three wrong guesses at splotch placement in this project.
cd "$(dirname "$0")/.."
echo "==================== CHECKPOINT $(date '+%H:%M') ===================="
echo ""
echo "PHASES (from DIRECTIVE.md §5):"
python3 - <<'PY'
import re
ph=None
for ln in open('docs/DIRECTIVE.md'):
    m=re.match(r'^### Phase (\S+)', ln)
    if m: ph=m.group(1)
    m2=re.match(r'^- ([✅⬜🔨⚠️]+)\s*(.*)', ln.rstrip())
    if m2 and ph:
        mark, txt = m2.group(1), m2.group(2)
        txt = re.sub(r'[`*]','',txt)[:66]
        print(f"  {mark:2} P{ph:2} {txt}")
PY
echo ""
d=$(grep -c '^- ✅' docs/DIRECTIVE.md); t=$(grep -cE '^- [✅⬜🔨⚠]' docs/DIRECTIVE.md)
echo "  PROGRESS: $d/$t items complete"
echo ""
echo "REPO:"
echo "  commits:     $(git rev-list --count HEAD 2>/dev/null)"
echo "  uncommitted: $(git status --porcelain | wc -l | tr -d ' ') files"
echo "  py lines:    $(cat py/*.py 2>/dev/null | wc -l | tr -d ' ')"
echo "  js lines:    $(cat web/*.js 2>/dev/null | wc -l | tr -d ' ')"
echo "  tests:       $(ls tests/test_*.py 2>/dev/null | wc -l | tr -d ' ')"
echo ""
# THE WORK SOURCE MOVED. §5 above reports 37/37 and has zero un-done items --
# it is a finished record, not a live plan. Everything since the
# redirect comes from ROADMAP.md, which this script did not know existed, so a
# heartbeat aimed only at §5 could report "all complete" forever while real
# work went unmentioned.
echo "ROADMAP (docs/ROADMAP.md — the live work source):"
python3 - <<'PY'
import re
for ln in open('docs/ROADMAP.md'):
    m = re.match(r'^## ((?:R|B)\d+[^\n]*)', ln.rstrip())
    if not m: continue
    t = re.sub(r'[`*]', '', m.group(1))
    if '(original)' in t: continue          # kept-for-reasoning duplicates
    done = '✅' in t or 'CLOSED' in t
    print(f"  {'✅' if done else '⬜'} {t[:68]}")
PY
echo ""
echo "BLOCKED ON A HUMAN (these cannot be worked around):"
sed -n '/^## BLOCKED ON A HUMAN/,/^---/p' docs/ROADMAP.md \
  | grep -E '^\| B[0-9]' | sed 's/|/ /g' | cut -c1-104 | sed 's/^/  /'
echo ""
echo "LAST 3 LOG ENTRIES (DIRECTIVE §6):"
grep -E '^\- \*\*[0-9]{2}:[0-9]{2}\*\*' docs/DIRECTIVE.md | tail -3 | sed 's/^/  /'
echo "  NOTE: §6 stopped being appended. The running record is WORK-QUEUE.md."
echo ""
echo "ASK YOURSELF:"
echo "  1. Is what I'm doing the top un-done item in ROADMAP.md?"
echo "  2. What does a JUDGE see differently because of it?"
echo "  3. Have I RUN it, or only read it?"
echo "  4. If I found a guard, have I planted a bug and watched it fail?"
echo "  5. Is it recorded in WORK-QUEUE.md, and committed and pushed?"
echo "======================================================================"
