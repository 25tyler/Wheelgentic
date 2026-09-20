#!/usr/bin/env bash
# run.sh — one command, two processes, one ^C kills both.
#
# Rejected just/honcho/overmind/tmuxinator: each is a brew install plus a new
# config dialect to replace `trap 'kill 0'`.
#
# TWO BUGS THIS FILE ALREADY HAD, both fatal on demo day and both invisible
# until it was actually executed:
#   1. It called `python`, which does NOT exist on a stock macOS (only
#      `python3`). With no venv present it died instantly.
#   2. `[ -d venv ] && source ...` as a bare statement returns 1 when venv is
#      absent, which under the EXIT trap killed the whole process group before
#      anything started. Exit code 144, no output, nothing to debug.
set -u
cd "$(dirname "$0")"

# Resolve the interpreter EXPLICITLY. Never assume `python` exists.
if [ -d venv ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "FATAL: no python3 on PATH and no venv/ — run: python3.12 -m venv venv"
  exit 1
fi
"$PY" -c "import sys;assert sys.version_info>=(3,9)" 2>/dev/null || {
  echo "FATAL: $PY is too old"; exit 1; }

# PREFLIGHT. Without this a missing dependency looks EXACTLY like a crash on
# demo day: the child dies, `wait` returns, the trap fires, and you get an
# empty terminal with nothing to debug. Name the missing module instead.
#
# THE MODULE LIST DEPENDS ON THE ARM. pyserial drives the RoArm, python-can
# drives the OpenYAM, and neither host will have both: the Pi that runs the
# OpenYAM has no reason to carry pyserial and this Mac cannot use SocketCAN at
# all. Demanding both here would fail the preflight on every machine, which is
# the one failure mode this block exists to prevent. scrubbot.py imports the
# driver lazily for the same reason.
ARM="${ARM:-roarm}"
case " $* " in *" --arm openyam "*) ARM=openyam ;; esac
MISSING=$("$PY" - "$ARM" <<'PYCHK'
import sys
mods = ["cv2", "numpy", "websockets"]
mods.append("can" if sys.argv[1] == "openyam" else "serial")
missing = []
for m in mods:
    try: __import__(m)
    except ImportError: missing.append(m)
print(" ".join(missing))
PYCHK
)
if [ -n "$MISSING" ]; then
  echo "FATAL: $PY is missing: $MISSING"
  echo "  fix: python3.12 -m venv venv && source venv/bin/activate \\"
  echo "       && pip install -r requirements.txt"
  echo "  (mediapipe is only needed for LIVE mode, not --replay/--scripted)"
  exit 1
fi

# Set AFTER the exits above so a config failure prints its message instead of
# being swallowed by the trap.
#
# `kill 0` alone was NOT enough: python -m http.server does not reliably exit
# on the SIGINT that reaches it via the process group, so it survived ^C
# holding port 8000 and the NEXT launch silently served nothing. Track the
# children explicitly, SIGTERM them, then SIGKILL anything still alive.
cleanup() {
  trap - EXIT INT TERM HUP
  for pid in ${CHILDREN:-}; do kill -TERM "$pid" 2>/dev/null; done
  # Give them a moment, then insist. python -m http.server in particular does
  # not reliably exit on SIGTERM while a connection is open.
  for _ in 1 2 3 4 5 6; do
    alive=""
    for pid in ${CHILDREN:-}; do kill -0 "$pid" 2>/dev/null && alive="y"; done
    [ -z "$alive" ] && break
    sleep 0.2
  done
  for pid in ${CHILDREN:-}; do kill -KILL "$pid" 2>/dev/null; done
}
trap cleanup EXIT INT TERM HUP
CHILDREN=""
CHILD_RC=0
DEMO_PID=""

# `wait` blocks the trap: bash defers a signal handler until the foreground
# builtin returns, so a plain `wait` delayed cleanup. Waiting in a loop with a
# short sleep lets the handler actually run.
#
# ALSO, and this is the part that cost an hour: a NON-INTERACTIVE bash script
# started in the BACKGROUND ignores SIGINT entirely -- POSIX requires it.
# Measured with a minimal script: `kill -INT` -> trap never fires;
# `kill -TERM` -> fires immediately. Interactive ^C is unaffected (the tty
# signals the whole foreground process group directly), so the demo-day path
# is fine, but any wrapper or test MUST use SIGTERM.
# WATCH THE DEMO CHILD, NOT "ALL CHILDREN". This loop used to break only when
# EVERY tracked pid was gone, and scrubbot.py is not the last to go: the page
# server outlives it. Measured both ways. With the port free, a
# fatal scrubbot (camera denied) left run.sh polling for over 13s with the page
# still answering 200 -- the launcher never returns and never says the demo is
# dead. With the port already held, run.sh's own server died silently (it is
# started with >/dev/null 2>&1, so the bind failure prints nothing), BOTH
# children were then gone, and the script fell off its end reporting SUCCESS:
# rc=0 on camera denial, on a missing --replay file, and on argparse rejecting
# an unknown flag. Any wrapper reads that as a working demo.
#
# So: wait on the demo child specifically. The page server is a helper and its
# EXIT trap already tears it down. Keep POLLING rather than a bare `wait` --
# the comment below is why.
wait_for_children() {
  while :; do
    if [ -n "${DEMO_PID:-}" ] && ! kill -0 "$DEMO_PID" 2>/dev/null; then
      # Reap it for the real status. `wait` still reports it after the poll has
      # seen it die, because bash holds the status until reaped (measured:
      # a child exiting 7 -> wait says 7).
      # PLAIN wait, STATUS ON THE NEXT LINE. `if ! wait "$pid"; then rc=$?`
      # captures the status of the NEGATION, which bash sets to 0 whenever the
      # negation succeeds -- so a child that died 7 recorded 0 and the whole
      # fix silently did nothing. Measured: the `if !` form yields 0, a plain
      # `wait; rc=$?` yields 7.
      wait "$DEMO_PID"
      CHILD_RC=$?
      # 143 = SIGTERM, 130 = SIGINT: that is cleanup doing its job on a normal
      # shutdown, not a fault. Only a real failure should colour the exit.
      case "$CHILD_RC" in 130|143) CHILD_RC=0 ;; esac
      return
    fi
    alive=""
    for pid in ${CHILDREN:-}; do kill -0 "$pid" 2>/dev/null && alive="y"; done
    [ -z "$alive" ] && return
    sleep 0.3
  done
}

# PORT MOVES THE PAGE ONLY, NOT THE WEBSOCKET. It exists so tests can run
# run.sh without colliding with the harness's own server on 8000. Demo day
# uses the default.
#
# IT DOES NOT ISOLATE A WHOLE INSTANCE, and the old wording here ("without
# colliding") implied it did. The socket is hardcoded at :8765 in BOTH
# py/scrubbot.py and web/main.js, so a second run.sh -- even on a different
# PORT -- prints "[ws] CANNOT BIND ws://127.0.0.1:8765" and shuts down.
# Measured on a fresh clone: PORT=8100 served the page at http 200 and then
# died on the socket because another instance held 8765.
#
# That is WHY tests/test_runsh.py waits for :8765 to be free before starting
# rather than relying on PORT to keep two instances apart -- the workaround
# was already there, undocumented. Making the socket configurable means
# touching scrubbot.py, main.js and every test's free_8765(); not worth it
# before a demo. Run one instance at a time.
PORT="${PORT:-8000}"
"$PY" -m http.server "$PORT" -d web >/dev/null 2>&1 &
CHILDREN="$CHILDREN $!"
sleep 1

# BROWSER_OFF=1 skips the launch so automated tests do not open windows.
if [ "${BROWSER_OFF:-}" != "1" ]; then
  open -a "Google Chrome" --args --kiosk \
       --autoplay-policy=no-user-gesture-required "http://localhost:$PORT/" 2>/dev/null || \
    echo "open http://localhost:$PORT/ manually"
fi

# DELETING --no-arm HERE MAKES A PRINTED DOCUMENT WRONG.
# RECOVERY-CARD.md's camera-dead and serial-port rows both tell the operator
# to type `REPLAY=recordings/good_run.jsonl ./run.sh` with NO flags, because
# this line supplies it. Delete it and the card is wrong on paper, taped to
# the laptop, at the moment someone is already having a bad time. No test
# pins the pairing: test_runsh.py checks the child is up, not its argv.
# Proof it takes effect: the replay run logs `[arm] DRY RUN`.
if [ "${REPLAY:-}" != "" ]; then
  "$PY" -u py/scrubbot.py --replay "$REPLAY" --no-arm "$@" &
else
  "$PY" -u py/scrubbot.py "$@" &
fi
DEMO_PID=$!
CHILDREN="$CHILDREN $DEMO_PID"
wait_for_children
exit "$CHILD_RC"
