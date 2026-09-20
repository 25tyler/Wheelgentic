#!/usr/bin/env bash
# tests/quick.sh — the no-browser subset (~250s, measured twice on this Mac).
#
# tests/run_all.sh takes ~9 minutes because the browser and lifecycle tests
# launch real Chromium and real subprocesses. A suite that is slow enough to
# skip is a suite that gets skipped, so this is the one you actually run — the
# pure-logic checks that catch most regressions.
#
# ALWAYS run the full suite before committing. quick.sh does NOT cover the
# browser, the projector, run.sh, or the fallback ladder.
set -u
cd "$(dirname "$0")/.."
PY_CV="${PY_CV:-./venv/bin/python}"
[ -x "$PY_CV" ] || PY_CV=/tmp/sbtest/bin/python

# SAY SO rather than failing two tests with a bare ModuleNotFoundError.
# /tmp/sbtest has cv2 but not mediapipe; falling back to it silently is how
# run_all.sh spent a while reporting 19/21 and blaming the tests.
if ! "$PY_CV" -c "import mediapipe" 2>/dev/null; then
  echo "  NOTE: $PY_CV lacks mediapipe -- test_vision_real and test_mirror will fail."
  echo "        Fix: python3.12 -m venv venv; ./venv/bin/pip install -r requirements.txt"
fi

"$PY_CV" tests/fixture.py >/dev/null 2>&1
fail=0; passed=0; ran=0
# KEEP A FAILING TEST'S LOG. This loop tails only 12 lines on a failure -- the
# full suite tails 25 -- and then deleted the log, so the subset an operator
# actually runs was the one where failure evidence was least recoverable.
# Same directory run_all.sh uses, cleared once here so one run's evidence is
# never confused with the next run's. No duration trigger: quick.sh prints no
# per-test time, and a failure is the case that matters.
KEEPDIR="${KEEPDIR:-/tmp/wheelgentic-kept-logs}"
rm -rf "$KEEPDIR"
for t in test_docs_match_code test_calibration test_signals test_arm_protocol test_scripted_and_config test_dirt test_uv_fsm test_mode_switch test_record_replay test_consent_latch test_vision_real test_mirror; do
  ran=$((ran+1))
  printf "  %-28s " "$t"
  log=$(mktemp)
  if "$PY_CV" "tests/$t.py" >"$log" 2>&1; then
    echo "PASS"; passed=$((passed+1))
  else
    code=$?; echo "*** FAIL (exit $code)"; tail -12 "$log" | sed 's/^/      /'; fail=1
    mkdir -p "$KEEPDIR"; cp "$log" "$KEEPDIR/$t-exit$code.log"
  fi
  rm -f "$log"
done
echo ""
echo "  $passed/$ran fast checks passed"
[ $fail -eq 0 ] || echo "  *** run tests/run_all.sh for the full picture ***"
exit $fail
