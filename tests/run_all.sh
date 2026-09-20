#!/usr/bin/env bash
# tests/run_all.sh — every check that caught a real bug. Run after ANY change
# to camera framing, avatar.root.position, the A-pose angle, addSplotch(),
# the websocket event shape, or the arm pump rate.
#
# These are not unit tests for their own sake. Each one caught a defect that
# syntax checks, console-error checks and DOM probes all passed.
#
# NOTE ON `| tail`: piping a test into tail HIDES its exit code ($? is tail's).
# An earlier version of this script printed "ALL CHECKS PASSED" while four of
# five tests were dying on ModuleNotFoundError. Output goes to a temp file and
# the exit status is read from the command itself.
set -u
cd "$(dirname "$0")/.."

# Playwright tests need the interpreter that HAS playwright; the calibration
# test needs the one that has cv2. They are often not the same interpreter.
# PREFER THE PROJECT VENV. The old default was /tmp/sbtest/bin/python, which
# has cv2 but NOT mediapipe -- so test_vision_real and test_mirror failed on
# every full run while quick.sh (which prefers ./venv) passed them. The
# preflight below only checked cv2, so the suite reported 19/21 and blamed the
# tests. /tmp is also wiped on reboot, which would have taken the whole
# PY_CV path with it.
PY_CV="${PY_CV:-./venv/bin/python}"          # needs cv2 + numpy + mediapipe
[ -x "$PY_CV" ] || PY_CV=/tmp/sbtest/bin/python
PY_PW="${PY_PW:-python3}"                    # needs playwright

command -v "$PY_PW" >/dev/null || { echo "no $PY_PW"; exit 2; }
"$PY_PW" -c "import playwright" 2>/dev/null || {
  echo "FATAL: $PY_PW lacks playwright. Install: $PY_PW -m pip install playwright && $PY_PW -m playwright install chromium"
  exit 2; }
"$PY_CV" -c "import cv2" 2>/dev/null || {
  echo "FATAL: $PY_CV lacks cv2. Set PY_CV=<interpreter with opencv>"; exit 2; }
# CHECK EVERY IMPORT THE SUITE NEEDS, not just the first one. Checking cv2
# alone let a mediapipe-less interpreter through and turned a setup problem
# into two mysterious test failures.
"$PY_CV" -c "import mediapipe" 2>/dev/null || {
  echo "FATAL: $PY_CV lacks mediapipe (test_vision_real + test_mirror need it)."
  echo "       Set PY_CV=<interpreter with mediapipe 1.0.0>, or:"
  echo "       python3.12 -m venv venv && ./venv/bin/pip install -r requirements.txt"
  exit 2; }

# Generate test-only artifacts. homography.pkl is NOT committed (it is
# specific to one camera on one table); tests make a synthetic one.
# ONE SUITE AT A TIME. Two concurrent run_all.sh instances fight over :8000
# and :8765 and produce failures that belong to neither run.
#
# A LOCKFILE, not a pgrep: `pgrep -f "bash tests/run_all.sh"` also matches the
# shell evaluating the guard, and `$$` inside $(...) is the subshell's parent,
# so the obvious self-exclusion silently never matches. mkdir is atomic and
# has no such ambiguity.
LOCK=/tmp/.wheelgentic-suite.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  owner=$(cat "$LOCK/pid" 2>/dev/null || echo "?")
  if kill -0 "$owner" 2>/dev/null; then
    echo "REFUSING: another tests/run_all.sh is running (pid $owner)."
    echo "  kill it first:  pkill -f run_all.sh; rm -rf $LOCK"
    exit 2
  fi
  rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null   # stale lock from a killed run
fi
echo $$ > "$LOCK/pid"
# NO EXIT TRAP HERE. Bash REPLACES an EXIT trap instead of appending,
# so a trap installed here would be silently discarded by the one at the
# server-start line below, and the lock would outlive every run (measured:
# same inode present after a clean exit). The lock removal lives in THAT
# trap. If you need more cleanup, add it to that body -- never a new trap.

# Kill leftover headless-Chrome and scrubbot processes from previous runs.
# Orphaned chrome-headless holds :8765 CONNECTIONS open, which is enough to
# make the next scrubbot fail to bind and every socket test fail for a reason
# unrelated to the code.
pkill -9 -f "chrome-headless" 2>/dev/null
pkill -9 -f "py/scrubbot.py"  2>/dev/null
# STALE TEST PROCESSES FROM EARLIER RUNS. A `tests/test_*.py` orphan matches
# none of the patterns above, so nothing ever reaped one: I found
# test_arm_protocol.py alive for 2h45m, PPID 1, still holding a pty, and it is
# the best explanation for a SIGINT retreat that failed in-suite while passing
# 3/3 in isolation on the same code.
#
# AGE, NOT PPID. The obvious filter -- kill anything with PPID 1 -- is wrong
# here: this suite's own `py/scrubbot.py --replay` children reparent to init
# within seconds, so a PPID test would kill the process under test and report a
# bogus failure (the exact landmine DIRECTIVE.md warns about for bare
# `pkill -f tests/test_`). Age is safe because this block runs BEFORE any child
# of this run exists, so nothing legitimate can be older than a few minutes.
ps -eo pid,etime,command | awk '
  /tests\/test_[a-z_]*\.py/ && !/awk/ {
    # etime is [[dd-]hh:]mm:ss -- anything with a day, an hour, or >=10 minutes
    split($2, t, /[-:]/)
    stale = (index($2, "-") > 0) || (length(t) >= 3) || (t[1] + 0 >= 10)
    if (stale) print $1
  }' | while read -r _pid; do
  echo "  killing stale test process $_pid (older than this run)"
  kill -9 "$_pid" 2>/dev/null
done
for _ in 1 2 3 4 5; do
  [ -z "$(lsof -ti tcp:8765 2>/dev/null)" ] && break
  lsof -ti tcp:8765 2>/dev/null | xargs kill -9 2>/dev/null
  sleep 0.5
done

echo "=== test fixtures ==="
"$PY_CV" tests/fixture.py

echo "=== serving web/ on :8000 ==="
pkill -f "http.server 8000" 2>/dev/null; sleep 0.5
python3 -m http.server 8000 -d web >/dev/null 2>&1 &
SRV=$!
# ONE trap, three jobs. Bash REPLACES an EXIT trap, so a second one
# anywhere in this file silently drops these. Add to this string.
CLEANUP="kill $SRV 2>/dev/null; pkill -f 'py/scrubbot.py' 2>/dev/null"
CLEANUP="$CLEANUP; rm -rf '$LOCK'"
trap "$CLEANUP" EXIT
sleep 1.2

serve_web() {              # (re)start the static server if it is not answering
  curl -s -o /dev/null --max-time 2 http://localhost:8000/ && return 0
  python3 -m http.server 8000 -d web >/dev/null 2>&1 &
  SRV=$!
  for _ in $(seq 20); do
    curl -s -o /dev/null --max-time 1 http://localhost:8000/ && return 0
    sleep 0.3
  done
  echo "  !! could not start the web server on :8000"
  return 1
}

fail=0; passed=0; ran=0
# Where retained per-test logs go. Cleared at the start of each suite so
# one run's evidence is never confused with the next run's.
KEEPDIR="${KEEPDIR:-/tmp/wheelgentic-kept-logs}"
rm -rf "$KEEPDIR"
run() {                    # run <label> <interpreter> <script> <tail-lines>
  ran=$((ran+1))
  echo ""; echo "=== $1 ==="
  # Browser tests need :8000. A previous test may have killed it.
  case "$3" in *browser*|*splotch*|*projector*|*degraded*|*integration*|*resilience*|*voice*|*spot*|*care*|*sponge*|*tool*|*privacy*|*handoff*|*steam*|*recovery*)
    serve_web || { echo "  -> *** FAIL (no web server) ***"; fail=1; return; } ;;
  esac
  local log; log=$(mktemp)
  # TIME EVERY TEST. A wedged run that never printed a WATCHDOG line was
  # indistinguishable from a fast one in every suite log -- see the hang item
  # in docs/WORK-QUEUE.md. Two runs there wedged for 4+ minutes and printed no
  # line at all, so grepping for the line undercounts by an unknown amount.
  # WHY IT PRINTED NOTHING IS OPEN. The first explanation on record -- SIGALRM
  # starved by a main thread in a C call -- did not survive measurement: 8 of 8
  # cases fire the handler at 2.0s, including pyserial 3.5's read and readline
  # on a timeout=None pty, which is the shape test_arm_protocol drives. PEP 475
  # runs the handler and then retries the syscall. Do not re-derive that.
  # The duration stands on its own regardless of cause: an arm-protocol block
  # that measures 87.0s +/- 0.1s idle (four runs) showing 148s is the same
  # failure, line or no line. The ~30s this line used to claim was stale by a
  # factor of three; a 7f retry adds a measured ~35s on top of the 87s.
  # SECONDS is a bash builtin, so this adds no fork to a 25-test suite.
  local t0=$SECONDS
  if "$2" "$3" >"$log" 2>&1; then
    local secs=$((SECONDS-t0))
    tail -"${4:-12}" "$log"; echo "  -> PASS (${secs}s)"; passed=$((passed+1))
    local _keep=0
  else
    # Capture the code BEFORE running tail -- $? after a command is tail's
    # status, so the old version always printed "(exit 0)" on a failure.
    local code=$?
    local secs=$((SECONDS-t0))
    tail -25 "$log"; echo "  -> *** FAIL (exit $code, ${secs}s) ***"; fail=1
    local _keep=1
  fi
  # KEEP THE LOG WHEN IT MIGHT ANSWER SOMETHING. Deleting every per-test log
  # is why three separate questions this session were unanswerable from a suite
  # run: the 122s arm block's cause, the 7f retry's own "(retried ...)" line,
  # and the flake denominator over 47 runs. A PASS prints 12 tailed lines and a
  # FAIL 25, so anything above that scrolls off and then the evidence is gone.
  # Retain on failure, or when a block runs long enough to be worth explaining.
  # SLOW_KEEP_S is deliberately above every measured block except arm-protocol
  # at 87s, so a normal suite keeps nothing and costs nothing.
  if [ "$_keep" = "1" ] || [ "$secs" -ge "${SLOW_KEEP_S:-100}" ]; then
    mkdir -p "$KEEPDIR"
    cp "$log" "$KEEPDIR/$(printf '%s' "$1" | tr -cs 'A-Za-z0-9' '_')-${secs}s.log"
  fi
  rm -f "$log"
}

run "docs match the code"                 "$PY_CV" tests/test_docs_match_code.py 8
run "the backend is in the repo, not in /tmp" "$PY_CV" tests/test_scrub3d_merge.py 12
run "live-scan reads the camera's aim correctly" "$PY_CV" tests/test_live_scan.py 12
run "calibration guards"                  "$PY_CV" tests/test_calibration.py 14
run "SIGTERM retreats the arm off the person" "$PY_CV" tests/test_signals.py 16
run "arm protocol (fake RoArm on a pty)"  "$PY_CV" tests/test_arm_protocol.py 12
run "OpenYAM Damiao wire protocol"        "$PY_CV" tests/test_damiao.py 12
run "OpenYAM calibration load + gate"     "$PY_CV" tests/test_openyam_calib.py 12
# Needs python-can, and skips with a visible reason when it is absent. The
# budget is 40s rather than 12 because this one drives a real 100Hz pump
# thread through several estop/clear cycles and waits on wall-clock drains.
run "OpenYAM send path (virtual CAN bus)" "$PY_CV" tests/test_openyam_bus.py 40
run "find_port (the demo path with no --port)" "$PY_CV" tests/test_find_port.py 12
run "scripted mode + config hot-reload"   "$PY_CV" tests/test_scripted_and_config.py 12
run "UV dirt detector"                    "$PY_CV" tests/test_dirt.py 12
run "UV mode through the real FSM"        "$PY_CV" tests/test_uv_fsm.py 12
run "dirt_mode flipped MID-SCRUB"         "$PY_CV" tests/test_mode_switch.py 12
run "record/replay round trip"            "$PY_CV" tests/test_record_replay.py 10
run "consent latch (arm never self-starts)" "$PY_CV" tests/test_consent_latch.py 10
run "REAL mediapipe (framing, delegate, fps)" "$PY_CV" tests/test_vision_real.py 14
run "mirror convention (which arm gets scrubbed)" "$PY_CV" tests/test_mirror.py 12
run "browser boots, no console errors"    "$PY_PW" tests/test_browser_boot.py 8
run "splotches land ON the arm"           "$PY_PW" tests/test_splotch_placement.py 6
run "python -> browser integration"       "$PY_PW" tests/test_integration.py 14
run "fallback ladder"                     "$PY_PW" tests/test_resilience.py 10
run "projector legibility 1080p/720p/4:3" "$PY_PW" tests/test_projector.py 8
run "degraded boot (missing assets)"      "$PY_PW" tests/test_degraded_boot.py 10
run "arm cycle vs manual 1/2/3 conflict"  "$PY_PW" tests/test_cycle_conflict.py 12
run "browser pose: cartoon mirrors"       "$PY_PW" tests/test_browser_pose.py 10
run "repeated cycles leak nothing"        "$PY_PW" tests/test_repeat_cycles.py 12
run "audio actually plays"                "$PY_PW" tests/test_audio.py 10
run "remote arm/estop from the projector" "$PY_PW" tests/test_remote_arm.py 10
run "estop stops the CARTOON, not just the arm" "$PY_PW" tests/test_estop_cartoon.py 12
run "the voice readout never lies about hearing" "$PY_PW" tests/test_voice_faults.py 12
run "the chair answers with the arm that owns the spot" "$PY_PW" tests/test_spot_focus.py 12
run "the chair answers questions from live state" "$PY_PW" tests/test_voice_agent.py 12
run "the care count only counts care" "$PY_PW" tests/test_care_counter.py 12
run "the sponge turns only while it is on the body" "$PY_PW" tests/test_sponge_spin.py 12
run "the tool change is an exchange you can see" "$PY_PW" tests/test_tool_swap.py 12
run "the privacy line is evidence, not a caption" "$PY_PW" tests/test_privacy_count.py 12
run "the handoff move keeps the frame alive" "$PY_PW" tests/test_handoff_shot.py 12
run "steam rises during the wash" "$PY_PW" tests/test_steam.py 12
run "the recovery card sequence works" "$PY_PW" tests/test_recovery_sequence.py 12

# LAST, deliberately. test_runsh.py kills every process matching
# "http.server 8000" -- INCLUDING this script's own server -- so every browser
# test after it died with ERR_CONNECTION_REFUSED. Three tests failed for a
# reason that had nothing to do with the code they were testing.
run "run.sh lifecycle (demo-day path)"    "$PY_PW" tests/test_runsh.py 10

echo ""
echo "======================================"
echo "  $passed/$ran passed"
if [ $fail -eq 0 ] && [ $passed -eq $ran ]; then
  echo "  ALL CHECKS PASSED"
else
  echo "  *** FAILURES ABOVE — do not ship ***"
fi
echo "======================================"
exit $fail
