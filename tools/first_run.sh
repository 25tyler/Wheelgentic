#!/usr/bin/env bash
# tools/first_run.sh — the three hour-0 go/no-gos, in order, with the exact
# next action for each failure. Run this FIRST on the real hardware.
#
#   bash tools/first_run.sh
#
# Everything the software can prove without hardware is already green
# (bash tests/quick.sh). This covers only what needs a real camera and a real
# arm — and each one, if it fails, changes the plan rather than the hour.
set -u
cd "$(dirname "$0")/.."
PY=./venv/bin/python
[ -x "$PY" ] || PY=python3

hr() { printf '%s\n' "------------------------------------------------------------"; }
ok()   { printf '  \033[32mGO\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mNO-GO\033[0m %s\n' "$1"; }

hr; echo "GO/NO-GO 0 — disk + interpreter"
free=$(df -g . | awk 'NR==2{print $4}')
[ "$free" -ge 5 ] && ok "${free}GB free" || bad "only ${free}GB free — need 5. pip cache purge"
$PY -c "import cv2,numpy,serial,websockets" 2>/dev/null \
  && ok "cv2 numpy pyserial websockets import" \
  || bad "missing deps — python3.12 -m venv venv && pip install -r requirements.txt"
$PY -c "import mediapipe as m;assert m.__version__=='1.0.0'" 2>/dev/null \
  && ok "mediapipe pinned at 1.0.0" \
  || bad "wrong mediapipe — 1.0.1 SIGABRTs on arm64. pip install mediapipe==1.0.0"

hr; echo "GO/NO-GO 1 — CAMERA (macOS denies this SILENTLY)"
if $PY -c "
import cv2,sys
c=cv2.VideoCapture(0)
sys.exit(0 if c.isOpened() else 1)" 2>/dev/null; then
  ok "camera opens"
  echo
  echo "  Now run this and WAVE ONE ARM:"
  echo "      $PY py/vision.py"
  echo "  Watch which label moves. GREEN=elbow ORANGE=wrist."
  echo "  If the LEFT label moves when you wave your RIGHT arm, set"
  echo "  \"mirror\": false in config.json. SETTLE THIS NOW — getting it wrong"
  echo "  makes the robot scrub the WRONG ARM."
  echo "  Need >=20 fps. Torso MUST be in frame or you get no pose at all."
else
  bad "camera blocked or missing"
  echo "      System Settings > Privacy & Security > Camera"
  echo "      Enable it for THIS terminal app (Terminal/iTerm/VS Code),"
  echo "      NOT for the python binary. Then re-run this script."
  echo "      Meanwhile everything works: REPLAY=recordings/good_run.jsonl ./run.sh"
fi

hr; echo "GO/NO-GO 2 — ARM (needs the CP210x driver + a reboot)"
ports=$(ls /dev/cu.usbserial-* /dev/cu.SLAB_USBtoUART* 2>/dev/null | tr '\n' ' ')
if [ -n "$ports" ]; then
  ok "serial port(s): $ports"
  echo "  Now run, with the arm CLAMPED and nothing near it:"
  echo "      $PY py/arm.py"
  echo "  It must MOVE and print a feedback dict. If it moves but feedback is"
  echo "  empty, you are on HTTP not serial — feedback is serial-only."
else
  bad "no serial port"
  echo "      Install the SiLabs CP210x VCP driver, approve the blocked system"
  echo "      extension in Privacy & Security, and REBOOT. The port will not"
  echo "      appear otherwise. Do not debug Python until it does."
fi

hr; echo "GO/NO-GO 3 — CALIBRATION"
if [ -f .homography-is-synthetic ]; then
  bad "homography.pkl is a TEST FIXTURE — the arm will scrub in the wrong place"
  echo "      rm homography.pkl .homography-is-synthetic"
  echo "      $PY py/calibrate.py"
elif [ -f homography.pkl ]; then
  ok "a real calibration exists"
  echo "  Re-run it on the ACTUAL table at the venue. 90 seconds."
else
  bad "no calibration"
  echo "      $PY py/calibrate.py   (A4 sheet ON A BOOK at forearm height)"
fi

hr
echo "When all three are GO:"
echo "  1. $PY py/scrubbot.py --record recordings/good_run.jsonl   # REAL capture"
echo "  2. ./run.sh                                                # the demo"
echo "  3. press 's' to ARM one scrub cycle (it never starts by itself)"
echo
echo "Read docs/RECOVERY-CARD.md before the demo. Read docs/OPEN-QUESTIONS.md"
echo "with the team — the bench-mount rule can disqualify the project and"
echo "nobody has checked it."
hr
