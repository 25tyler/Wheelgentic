#!/usr/bin/env bash
# tools/compare.sh -- her UI and his live view, side by side in one browser.
#
#   bash tools/compare.sh           # against a recording, no hardware
#
# WHAT COMES UP
#   9090  the Rerun WEB viewer, hosting the same window live_body.py would
#         spawn on the desktop. It ships inside the rerun-sdk wheel.
#   9876  the gRPC port that viewer listens on. live_body.py connects here
#         instead of spawning its own, because a desktop window cannot sit
#         beside a web page.
#   8765  our backend's websocket, feeding the cartoon 3D view.
#   5173  her carechair server.
#   8000  the page that frames the two.
#
# The right panel is HIS PROGRAM, not a drawing of his data. That distinction
# is the whole point of this script existing: anything else is a second
# implementation that can agree with the numbers and disagree with the truth.
set -u
cd "$(dirname "$0")/.."
ROOT=$(pwd)
REC=${REC:-scrub3d/data/live_rec_sample}
VIEWER=venv/lib/python3.12/site-packages/rerun_sdk/rerun_cli/Rerun.app/Contents/MacOS/Rerun

[ -d "$REC" ] || { echo "no recording at $REC"; exit 1; }
[ -x "$VIEWER" ] && [ -s "$VIEWER" ] || {
  echo "the Rerun viewer is missing from the installed wheel."
  echo "  ./venv/bin/pip install --force-reinstall --no-deps rerun-sdk==0.38.1"
  exit 1; }

for p in 9090 9876 8765 8770 8000 5173; do
  lsof -ti tcp:$p 2>/dev/null | xargs kill -9 2>/dev/null
done
pkill -9 -f "scrubbot.py|live_body.py" 2>/dev/null
sleep 1

echo "rerun web viewer      -> :9090"
nohup "$VIEWER" --serve-web --port 9876 --web-viewer-port 9090 >/tmp/rr.log 2>&1 &
sleep 5

echo "his live view ($REC)  -> logs into :9876"
nohup ./venv/bin/python scrub3d/live/live_body.py \
  --replay "$REC" --replay-fps 30 --viewer-port 9876 --no-arms \
  >/tmp/lb.log 2>&1 &

echo "our backend           -> :8765 (+ seam on :8770)"
CAM=replay SCRUB3D_ARM=openyam PYTHONPATH=scrub3d/live \
  nohup ./venv/bin/python -u py/scrubbot.py --no-arm --headless \
  >/tmp/scrubbot.log 2>&1 &

echo "the page              -> :8000"
nohup python3 -m http.server 8000 -d web >/tmp/web.log 2>&1 &

CC=/private/tmp/claude-501/-Users-tyler/8aa7e4a5-1929-40ab-82f8-8ebd61ffaaf0/scratchpad/carechair
if [ -f "$CC/server.js" ]; then
  echo "her carechair UI      -> :5173"
  ( cd "$CC" && nohup node server.js >/tmp/cc.log 2>&1 & )
fi

sleep 20
echo
echo "  open http://localhost:8000/compare.html"
