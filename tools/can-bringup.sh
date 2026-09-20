#!/usr/bin/env bash
# tools/can-bringup.sh — bring the CANable 2.0 up as can0 on the GB10.
#
# WHY THIS FILE EXISTS: every step below needs root, and the account that
# runs the demo cannot get a password prompt in the middle of a bring-up.
# So the root-only work is collected here, run ONCE by a human, and the
# demo afterwards runs entirely unprivileged. Nothing in py/ ever calls
# this; it is an operator step, not a code path.
#
#   sudo bash tools/can-bringup.sh
#
# Re-running it is safe. Every step checks for the state it wants before
# changing anything, so a second run on an already-up bus is a no-op that
# just reprints the verification.
#
# WHAT IT DELIBERATELY DOES NOT DO: it does not grant anyone permission to
# USE the bus, because nobody needs granting. Opening an AF_CAN socket to
# send and receive frames requires no privilege at all -- measured on this
# box as uid 1000, where bind() to a missing interface returns ENODEV
# ("no such device"), not EPERM ("not permitted"). Only CONFIGURING the
# link needs CAP_NET_ADMIN, which is what this script spends its root on.
# A udev rule chmod-ing the device would therefore fix nothing: a
# SocketCAN interface is a network device, not a /dev node, so there is no
# file mode for udev to change. The udev rule this script DOES install
# does a different job -- see step 4.
set -euo pipefail

IFACE="${CAN_IFACE:-can0}"
BITRATE="${CAN_BITRATE:-1000000}"   # 1 Mbit/s, the rate openarm_can configures
                                    # for Damiao servos (can_configure -d 1000000
                                    # --no-fd). The servos do not autodetect;
                                    # a mismatch here is a silent dead bus.

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32mOK\033[0m   %s\n' "$*"; }
warn() { printf '    \033[33mWARN\033[0m %s\n' "$*"; }
die()  { printf '    \033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run me as root:  sudo bash tools/can-bringup.sh"

# ---------------------------------------------------------------------------
say "1. Load the gs_usb driver"
# The CANable 2.0 runs candleLight (gs_usb) firmware: a native USB CAN
# protocol, NOT a serial port. That is why no /dev/ttyACM* or /dev/cu.*
# appears for it, on Linux or on macOS -- the absence is correct.
#
# This modprobe is belt-and-braces. gs_usb ships a USB alias for the
# CANable's VID:PID (usb:v1D50p606F, confirmed in modinfo on this kernel),
# so udev autoloads the module the moment the adapter is plugged in. We
# load it anyway so that step 2's error message can distinguish "driver
# missing" from "adapter not plugged in".
if modprobe gs_usb; then
    ok "gs_usb loaded"
else
    die "modprobe gs_usb failed -- is this the right kernel? $(uname -r)"
fi

# Make it survive a reboot. Harmless duplicate-proof append.
if ! grep -qxs 'gs_usb' /etc/modules-load.d/can.conf 2>/dev/null; then
    echo 'gs_usb' >> /etc/modules-load.d/can.conf
    ok "gs_usb added to /etc/modules-load.d/can.conf (loads at boot)"
else
    ok "gs_usb already set to load at boot"
fi

# ---------------------------------------------------------------------------
say "2. Check the adapter is actually present"
# An interface only exists once the USB device enumerates. If this fails
# the problem is the cable or the adapter, and no amount of ip-link fixes
# it -- so stop here rather than emit a confusing error from step 3.
if ! ip link show "$IFACE" >/dev/null 2>&1; then
    printf '\n'
    warn "no interface named '$IFACE'"
    printf '    The adapter is not plugged in, or it enumerated under a\n'
    printf '    different name. Check both:\n\n'
    printf '      lsusb | grep -i 1d50          # expect 1d50:606f CANable\n'
    printf '      ip -br link show type can     # expect can0\n\n'
    die "plug the CANable into the GB10 and re-run"
fi
ok "$IFACE exists"

# ---------------------------------------------------------------------------
say "3. Configure and raise the link at ${BITRATE} bit/s"
# ORDER MATTERS. The bitrate can only be set while the link is DOWN; the
# kernel rejects a bitrate change on a running interface. So we always
# down it first, even if it is already down -- `ip link set down` on a
# down interface succeeds silently, which is what makes this re-runnable.
ip link set "$IFACE" down
ip link set "$IFACE" up type can bitrate "$BITRATE"

# Verify by reading the state back rather than trusting the exit code.
# `ip link set ... up` returns 0 for a link that then fails to come up.
STATE="$(ip -details -brief link show "$IFACE" | awk '{print $2}')"
READBACK="$(ip -details link show "$IFACE" | grep -o 'bitrate [0-9]*' | awk '{print $2}')"
[ "$STATE" = "UP" ] || [ "$STATE" = "UNKNOWN" ] \
    || die "$IFACE is '$STATE', expected UP -- is the adapter wedged? unplug/replug"
[ "$READBACK" = "$BITRATE" ] \
    || die "$IFACE came up at ${READBACK:-unknown} bit/s, expected $BITRATE"
ok "$IFACE is $STATE at $READBACK bit/s"

# ---------------------------------------------------------------------------
say "4. Install the udev rule that re-raises the link on replug"
# THIS IS NOT A PERMISSIONS RULE (see the header). Its job is persistence.
# Everything step 3 did lives in the kernel's netdev state and is destroyed
# the moment the adapter is unplugged -- which, on a demo table, happens by
# accident. Without this, the arm goes dead mid-demo and the fix needs a
# root password nobody has on hand.
#
# ENV{ID_NET_DRIVER}=="gs_usb" matches the driver rather than the VID:PID
# so a replacement adapter with different ids still works. RUN+= is
# deliberately `ip link set up` only: it does NOT re-run this script,
# because a udev RUN is a short-lived, serialised context and a full script
# would block the udev worker.
cat > /etc/udev/rules.d/90-cloak-can.rules <<EOF
# Wheelgentic: raise a candleLight/gs_usb CAN adapter automatically on plug-in.
# Installed by tools/can-bringup.sh. Bitrate must match the Damiao servos.
ACTION=="add", SUBSYSTEM=="net", ENV{ID_NET_DRIVER}=="gs_usb", \\
  RUN+="/usr/sbin/ip link set %k up type can bitrate ${BITRATE}"
EOF
udevadm control --reload-rules
ok "/etc/udev/rules.d/90-cloak-can.rules installed (re-raises $IFACE on replug)"

# ---------------------------------------------------------------------------
say "5. Install can-utils if it is missing"
# candump is how you prove the SERVOS are talking before blaming Python.
# It is the single most useful diagnostic on this bus and it is not
# installed by default on this box. Skipped without failing if the machine
# is offline: the bus works without it, you just debug blind.
if command -v candump >/dev/null 2>&1; then
    ok "can-utils already installed"
elif apt-get install -y can-utils >/dev/null 2>&1; then
    ok "can-utils installed (candump, cansend, cangen)"
else
    warn "could not install can-utils (offline?). The bus is still up."
    warn "Install later with:  sudo apt-get install can-utils"
fi

# ---------------------------------------------------------------------------
say "Done. $IFACE is up at $BITRATE bit/s."
cat <<EOF

Next, in order:

  1. Power the arm, then watch the bus. You should see frames.
         candump $IFACE
     Nothing at all means the problem is physical, not software. Check
     termination (120 ohm at each end), 24V at the servos, CAN-H/CAN-L
     not swapped, and that every motor has a distinct id.

  2. Read the arm without moving it. This needs NO root.
         ~/wg-venv/bin/python tools/hardware-selftest.py

  3. Measure the numbers the driver refuses to guess. Also no root.
         ~/wg-venv/bin/python tools/calibrate-openyam.py

py/openyam.py will not drive real hardware until step 3 has written
config-openyam.json. That refusal is deliberate: link lengths, joint
limits, motor models, home pose and joint signs are all placeholders
until measured, and a wrong sign on J1 or J2 puts the elbow below the
person with nothing in software able to detect it.
EOF
