#!/usr/bin/env bash
# scrub3d/live/bridge.sh -- the dimOS bridge on the arm computer, one at a time.
#
#   bash scrub3d/live/bridge.sh real      # both real arms, AS THIS RIG IS WIRED:
#                                         #   can1 = the person's left (a0), can0 = their right (a1)
#                                         #   (already running? then it is left alone)
#   bash scrub3d/live/bridge.sh restart   # stop it and start it again: drops the arms' torque
#   bash scrub3d/live/bridge.sh real-swapped   # the same thing as real, kept for old habits
#   bash scrub3d/live/bridge.sh real-can0-left # the other wiring: only for a rig cabled the other way
#   bash scrub3d/live/bridge.sh mock      # dimOS's simulated arms, nothing moves
#   bash scrub3d/live/bridge.sh stop      # stop it and wait until it has really gone
#   bash scrub3d/live/bridge.sh status    # is one running, and what does it say
#   bash scrub3d/live/bridge.sh log       # follow its log
#
# Two bridges are two dimOS stacks on one bus answering the same names, which
# looks like random failures. This script never starts one until the old one
# has exited. Runs from the repo root; the log is ~/bridge.log.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${DIMOS_PY:-$HOME/dimos/.venv/bin/python}"
LOG="$HOME/bridge.log"
KEEP="$HOME/.bridge_keep"        # there while the bridge is meant to be up
export PATH="$HOME/.local/bin:$PATH"
export SCRUB3D_ARM=openyam

stop_bridge() {
  # Who asked: the bridge kept being stopped by somebody who was not driving it.
  echo "$(date '+%F %T') stop via '${0##*/} ${MODE:-?}' from: $(ps -o cmd= -p $PPID 2>/dev/null | cut -c1-100) | ssh ${SSH_CONNECTION:-none} | tty $(tty 2>/dev/null)" >> "$HOME/bridge_stops.log"
  rm -f "$KEEP"                  # first, or the keeper starts it again
  if pgrep -f "[d]imos_bridge_server" >/dev/null; then
    echo "  stopping the bridge (support the arms: torque drops when it exits)"
    pkill -f "[d]imos_bridge_server"
    for _ in $(seq 1 30); do pgrep -f "[d]imos_bridge_server" >/dev/null || break; sleep 1; done
    if pgrep -f "[d]imos_bridge_server" >/dev/null; then
      echo "  it would not exit; killing it"; pkill -9 -f "[d]imos_bridge_server"; sleep 2
    fi
  fi
  echo "  no bridge running"
}

# One bridge.sh at a time. Run twice at once, the second killed what the first was
# still starting, and the first then killed the second's: seven starts in a
# hundred seconds one evening, and not one of them lived.
case "${1:-status}" in
  keep|status|log) ;;                       # these change nothing, or ARE the bridge
  *) exec 9> "$HOME/.bridge.lock"
     if ! flock -n 9; then
       echo "  another bridge.sh is already working on it: wait for that one (it takes about 25 s)"
       exit 0
     fi ;;
esac

# Is a bridge with these exact ports already up, or on its way up?
same_bridge() {
  pgrep -af "[d]imos_bridge_server" | grep -q -- "$*"
}

start_bridge() {
  # Asked to start what is already running: leave it alone. It used to be
  # stopped and started again, which drops the arms' torque, takes 25 s, and
  # prints nothing while it does, so whoever typed it typed it again.
  if [ -e "$KEEP" ] && same_bridge "$@"; then
    if grep -aq "bridge listening" "$LOG"; then
      echo "  already running with this wiring, left alone (bridge.sh restart forces it)"
    else
      echo "  already starting with this wiring: give it about 25 s (bridge.sh status)"
    fi
    grep -aE "bridge listening" "$LOG" | tail -1
    return 0
  fi
  stop_bridge
  # The old bridge's ports linger a few seconds after it exits; wait for them.
  for _ in $(seq 1 30); do
    ss -ltn | grep -qE ":7790 |:8095 " || break
    sleep 1
  done
  for p in 7790 8095; do
    if ss -ltn | grep -q ":$p "; then echo "  port $p is still bound after 30 s; something else holds it"; exit 1; fi
  done
  # Kept up, not just started. Twice in one evening the bridge shut itself down
  # cleanly a few seconds after a live view was killed mid-request, nobody ever
  # found what told it to, and each time the next run died on "not reachable".
  : > "$LOG"
  touch "$KEEP"
  # 9>&-: the keeper must not inherit the lock, or it holds it for as long as it lives
  setsid nohup bash "$HERE/bridge.sh" keep "$@" > /dev/null 2>&1 < /dev/null 9>&- &
  printf "  starting dimOS and the arms, about 25 s "
  for _ in $(seq 1 60); do
    grep -aq "bridge listening" "$LOG" && break
    grep -aqE "Traceback|Error:|failed to" "$LOG" && break
    printf "."; sleep 2
  done
  echo
  grep -aE "bridge listening|Traceback|Error:|failed to" "$LOG" | tail -3
}

# bridge.sh real aim        the claw is AIMED at the skin (orientation cost 0.4)
# bridge.sh real aim 0.8    ... harder.   bridge.sh real  = not aimed, as it always was
AIM=""
if [ "${2:-}" = "aim" ]; then AIM="--orientation-cost ${3:-0.4}"; fi
MODE="${1:-status}"
case "$MODE" in
  # On this rig the person's LEFT arm is on can1 (found by lifting one arm and
  # photographing which moved). "real" used to mean can0 = left, and started
  # that way each arm gets the OTHER arm's commands; the bases face opposite
  # ways, so every move comes out mirrored: "turn to the front" turns the claw
  # into the person in the chair. It did, into a leg. "real" is now the wiring
  # this rig has, so the obvious word is the safe one.
  real|real-swapped) start_bridge --left-can-port can1 --right-can-port can0 $AIM ;;
  restart)           rm -f "$KEEP"; start_bridge --left-can-port can1 --right-can-port can0 $AIM ;;
  real-can0-left)    start_bridge --left-can-port can0 --right-can-port can1 $AIM ;;
  mock)   start_bridge ;;
  stop)   stop_bridge ;;
  keep)   shift                  # the keeper: not for calling by hand
          while [ -e "$KEEP" ]; do
            "$PY" "$HERE/dimos_bridge_server.py" "$@" >> "$LOG" 2>&1 < /dev/null
            code=$?
            [ -e "$KEEP" ] || break
            echo "  bridge exited ($code) at $(date +%T): torque was off; starting it again in 3 s" >> "$LOG"
            sleep 3
          done ;;
  status) pgrep -af "[d]imos_bridge_server" | head -1 || echo "  no bridge running"
          grep -aE "bridge listening|live view" "$LOG" 2>/dev/null | tail -3 ;;
  log)    tail -f "$LOG" ;;
  *)      echo "usage: bridge.sh real|restart|mock|stop|status|log   (real = can1 is the person's left)"; exit 2 ;;
esac
