#!/usr/bin/env bash
# tools/connect-linux.sh — point this Mac at the Linux host that runs the arm.
#
# Usage:  bash tools/connect-linux.sh <user>@<ip>
# e.g.    bash tools/connect-linux.sh pi@10.189.58.42
#
# Installs the wheelgentic key, adds an ssh alias, and reports what the
# Linux box can actually see. Safe to re-run.
set -euo pipefail

TARGET="${1:-}"
[ -n "$TARGET" ] || { echo "usage: $0 <user>@<ip>"; exit 2; }
KEY="$HOME/.ssh/wheelgentic"

echo "==> installing key on $TARGET (asks for its password ONCE)"
ssh-copy-id -i "$KEY.pub" -o StrictHostKeyChecking=accept-new "$TARGET"

echo "==> adding 'wg' alias to ~/.ssh/config"
touch ~/.ssh/config; chmod 600 ~/.ssh/config
if ! grep -q "^Host wg$" ~/.ssh/config 2>/dev/null; then
  { echo ""
    echo "Host wg"
    echo "    HostName ${TARGET#*@}"
    echo "    User ${TARGET%@*}"
    echo "    IdentityFile $KEY"
    echo "    StrictHostKeyChecking accept-new"
  } >> ~/.ssh/config
  echo "    added"
else
  echo "    already present (edit ~/.ssh/config to change the IP)"
fi

echo "==> what the Linux box sees"
ssh wg 'echo "  host   : $(hostname) — $(uname -srm)"
        echo "  distro : $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
        echo "  python : $(python3 --version 2>&1)"
        echo "  camera : $(lsusb 2>/dev/null | grep -i intel || echo "no Intel device on USB")"
        echo "  canable: $(lsusb 2>/dev/null | grep -i 1d50:606f || echo "no CANable on USB")"
        echo "  socketcan: $(ip -br link show type can 2>/dev/null || echo "no can interfaces yet")"'

echo ""
echo "Done. From now on: ssh wg"
