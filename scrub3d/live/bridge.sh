#!/usr/bin/env bash
# scrub3d/live/bridge.sh -- the dimOS bridge on the arm computer, one at a time.
#
#   bash scrub3d/live/bridge.sh real      # both real arms (can0 = a0/left, can1 = a1/right)
#   bash scrub3d/live/bridge.sh real-swapped   # the same with can1 as the left arm
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
export PATH="$HOME/.local/bin:$PATH"
export SCRUB3D_ARM=openyam

stop_bridge() {
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

start_bridge() {
  stop_bridge
  # The old bridge's ports linger a few seconds after it exits; wait for them.
  for _ in $(seq 1 30); do
    ss -ltn | grep -qE ":7790 |:8095 " || break
    sleep 1
  done
  for p in 7790 8095; do
    if ss -ltn | grep -q ":$p "; then echo "  port $p is still bound after 30 s; something else holds it"; exit 1; fi
  done
  setsid nohup "$PY" "$HERE/dimos_bridge_server.py" "$@" > "$LOG" 2>&1 < /dev/null &
  for _ in $(seq 1 60); do
    grep -aq "bridge listening" "$LOG" && break
    grep -aqE "Traceback|Error:|failed to" "$LOG" && break
    sleep 2
  done
  grep -aE "bridge listening|Traceback|Error:|failed to" "$LOG" | tail -3
}

case "${1:-status}" in
  real)   start_bridge --left-can-port can0 --right-can-port can1 ;;
  real-swapped) start_bridge --left-can-port can1 --right-can-port can0 ;;   # the left arm is on can1
  mock)   start_bridge ;;
  stop)   stop_bridge ;;
  status) pgrep -af "[d]imos_bridge_server" | head -1 || echo "  no bridge running"
          grep -aE "bridge listening|live view" "$LOG" 2>/dev/null | tail -3 ;;
  log)    tail -f "$LOG" ;;
  *)      echo "usage: bridge.sh real|real-swapped|mock|stop|status|log"; exit 2 ;;
esac
