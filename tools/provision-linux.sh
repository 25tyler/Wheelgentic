#!/usr/bin/env bash
# tools/provision-linux.sh — bring a bare aarch64 Linux box up to
# "Wheelgentic runs here".
#
#   bash tools/provision-linux.sh            # everything
#   bash tools/provision-linux.sh --no-build # skip the librealsense source build
#
# WHICH MACHINES THIS TARGETS. It was written against the NVIDIA GB10
# (ASUSTeK GX10, 20-core Cortex-X925, Ubuntu 24.04 noble, kernel
# 6.17.0-1022-nvidia) — that is the box the demo actually runs on. It is NOT
# Raspberry-Pi-specific and must not become so again: every stage below
# detects what it needs instead of assuming a Pi kernel, a Pi apt repo, or
# Raspberry Pi OS package names. It should also come up on a Pi 5 running a
# 64-bit Debian-family image, because nothing here depends on GB10 hardware.
#
# Re-running is SAFE and is the intended way to use this. Every stage checks
# for its own result first and skips if it is already there, so a run that
# dies in the middle (a dropped wifi during a long build, a full disk) is
# recovered by running it again — not by undoing anything by hand.
#
# WHAT THIS DOES NOT DO: it never touches the RoArm serial path, and it never
# edits config.json. Provisioning installs capability; it does not choose
# which arm or which camera a run uses. That stays with --arm / CAM= / the
# config, so there is exactly one place a run's shape is decided.
#
# ORDER IS LOAD-BEARING. apt before venv (the interpreter must exist before we
# make a venv from it), venv before pip, pip before the stub removal (the stub
# can only be judged against a real binding that is already installed), and
# librealsense last because it is the only stage that can take 40 minutes.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"

# --------------------------------------------------------------- reporting ---
# Matches tools/first_run.sh's vocabulary so an operator reading both sees one
# voice. Stages announce themselves BEFORE doing work: on a long build a
# silent terminal is indistinguishable from a hang.
STAGE=0
stage() { STAGE=$((STAGE + 1)); printf '\n\033[1m[%d/8] %s\033[0m\n' "$STAGE" "$1"; }
ok()    { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
skip()  { printf '  \033[36mSKIP\033[0m  %s\n' "$1"; }
warn()  { printf '  \033[33mWARN\033[0m  %s\n' "$1"; }
die()   { printf '  \033[31mFATAL\033[0m %s\n' "$1" >&2; exit 1; }

BUILD_RS=1
for a in "$@"; do
    case "$a" in
        --no-build) BUILD_RS=0 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) die "unknown argument: $a (try --help)" ;;
    esac
done

# Every root action goes through this one wrapper rather than a bare `sudo`,
# because on the GB10 sudo REQUIRES A PASSWORD and this script is routinely
# driven over a non-interactive ssh. A bare `sudo` there does not fail fast —
# it blocks on a password prompt that nobody can answer, and `set -e` never
# fires. -n turns that hang into an immediate non-zero, which every caller
# below already handles by warning and continuing. The rule for callers: a
# stage that cannot get root must WARN and print the exact command a human
# should run, never die. Provisioning the userspace half is still worth doing
# on a box where you have no root.
ROOT_OK=1
NEEDS_ROOT=()
need_root() {
    # Record a command a human must run later, once we know root is missing.
    NEEDS_ROOT+=("$1")
}
sudo_n() {
    sudo -n "$@" 2>/dev/null
}
if sudo -n true 2>/dev/null; then
    ok "passwordless sudo available"
else
    ROOT_OK=0
fi

# ------------------------------------------------------- stage 0: platform ---
# Refuse EARLY and by name. This script runs apt, loads kernel modules and
# writes systemd units — on macOS every one of those either does not exist or
# means something else, and a half-run would leave a confusing mess. The
# operator's Mac is a development machine, not a target.
if [ "$(uname -s)" != "Linux" ]; then
    cat >&2 <<EOF

REFUSING TO RUN: this is $(uname -s), not Linux.

provision-linux.sh targets the Linux box, and everything it does is
Linux-only and has no macOS equivalent:

  * apt-get              — macOS has no apt; Homebrew is a different tree
  * modprobe gs_usb      — the CANable driver is a Linux kernel module.
                           macOS has no gs_usb driver AND no SocketCAN, so
                           there is nothing for it to attach to.
  * ip link set can0 up  — SocketCAN is a Linux network stack. There is no
                           can0 on macOS to bring up.
  * /etc/udev/rules.d    — udev is Linux. macOS device permissions come from
                           TCC (System Settings > Privacy & Security).
  * systemd              — macOS uses launchd.

Run this ON THE LINUX BOX, over ssh:

    ssh wg
    cd ~/Wheelgentic
    bash tools/provision-linux.sh

To see what the Mac CAN do right now, run the selftest instead — it is
cross-platform and reports per-item why each thing is or is not available:

    python3 tools/hardware-selftest.py

EOF
    exit 1
fi

# ------------------------------------------------- stage 1: OS + python fit ---
stage "checking OS and Python version fit"

. /etc/os-release 2>/dev/null || die "no /etc/os-release — cannot identify this distro"
printf '  distro: %s (%s)\n' "${PRETTY_NAME:-?}" "${VERSION_CODENAME:-?}"
printf '  arch:   %s\n' "$(uname -m)"
printf '  kernel: %s\n' "$(uname -r)"
# Board identity is informational only — nothing below branches on it. It is
# printed because the single biggest past mistake in this file was assuming a
# Raspberry Pi, and an operator who can see "GX10 / ASUSTeK" in the log will
# not re-file a Pi-shaped bug against a machine that is not one.
if [ -r /sys/class/dmi/id/product_name ]; then
    printf '  board:  %s %s\n' \
        "$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || echo '?')" \
        "$(cat /sys/class/dmi/id/product_name 2>/dev/null || echo '?')"
elif [ -r /proc/device-tree/model ]; then
    printf '  board:  %s\n' "$(tr -d '\0' < /proc/device-tree/model)"
fi

# APT_FAMILY gates every apt call below. A distro without dpkg is not a
# failure of this script's purpose — the venv and pip stages still work — so
# we degrade to "tell the human what to install" rather than dying.
APT_FAMILY=0
command -v apt-get >/dev/null 2>&1 && command -v dpkg >/dev/null 2>&1 && APT_FAMILY=1
[ "$APT_FAMILY" = 1 ] || warn \
    "no apt/dpkg on this distro. The apt stage will be skipped and its package
        list printed instead — install the equivalents with your own package
        manager, then re-run."

[ "$(uname -m)" = "aarch64" ] || warn \
    "arch is $(uname -m), not aarch64. Every wheel this project pins
        (mediapipe, opencv-contrib-python, pyrealsense2) is fetched as an
        aarch64 manylinux build. On x86_64 pip will find different wheels and
        they are untested here; on a 32-bit arm image there are no wheels at
        all."

# Python 3.12 is the ONLY version that satisfies both pinned dependencies at
# once, and neither constraint is negotiable:
#   * mediapipe 1.0.0 classifies Python 3.10-3.12 only. 3.13 is untested by
#     upstream and the failure would surface at RUNTIME, not at install, since
#     the wheel is py3-none. requirements.txt pins 1.0.0 because 1.0.1 SIGABRTs.
#   * pyrealsense2 publishes aarch64 wheels for cp39/cp310/cp312 — there has
#     never been a cp311 aarch64 wheel in any release.
# On Ubuntu 24.04 noble `python3` IS 3.12, so the loop below finds it on the
# first candidate and no extra apt package is needed. On distros whose default
# is 3.11 or 3.13 the explicit python3.12 binary is required, which is why we
# never simply use whatever `python3` happens to be.
PY312=""
for c in python3.12 /usr/bin/python3.12; do
    command -v "$c" >/dev/null 2>&1 && { PY312="$(command -v "$c")"; break; }
done
if [ -z "$PY312" ]; then
    warn "python3.12 not found yet — stage 2 will try to apt-install it"
else
    ok "python3.12 at $PY312"
fi

# --------------------------------------------------- stage 2: apt packages ---
stage "installing apt packages"

# Split into groups so the failure message can name WHICH group failed and
# why it matters, instead of one 30-package wall.
#
# libusb-1.0-0 and libudev1 are NOT optional and pip will neither install them
# nor warn: the pyrealsense2 extension module links them directly (DT_NEEDED),
# so a missing one is an ImportError at `import pyrealsense2`, long after pip
# reported success.
APT_CORE="python3.12 python3.12-venv python3-pip git"
APT_RUNTIME="libusb-1.0-0 libudev1"
APT_CAN="can-utils"
# OpenCV and mediapipe pull these in at import time on a headless image.
APT_CV="libgl1"
# Only needed when we build librealsense from source. The -dev headers live
# here rather than in APT_RUNTIME on purpose: the prebuilt pyrealsense2 wheel
# needs only the runtime .so files, so a wheel-only box should not be made to
# carry a compiler toolchain it will never use.
APT_BUILD="cmake build-essential pkg-config libssl-dev libusb-1.0-0-dev libudev-dev python3.12-dev"

# Ubuntu 24.04 (noble) renamed several runtime libraries with a `t64` suffix
# during the 64-bit-time_t transition: libglib2.0-0 became libglib2.0-0t64,
# and the OLD NAME NO LONGER EXISTS as an installable package — `apt-get
# install libglib2.0-0` on noble fails outright. Asking apt which name it
# actually has is the only way to stay correct across Bookworm (old name),
# noble (t64 name) and whatever comes next, so resolve rather than hardcode.
apt_pick() {
    # Echoes the first of its arguments that apt knows how to install.
    # Silence is a deliberate answer: the caller drops the package.
    local cand
    for cand in "$@"; do
        if apt-cache show "$cand" >/dev/null 2>&1; then
            printf '%s' "$cand"
            return 0
        fi
    done
}

if [ "$APT_FAMILY" = 1 ]; then
    GLIB="$(apt_pick libglib2.0-0t64 libglib2.0-0 || true)"
    [ -n "$GLIB" ] && APT_CV="$APT_CV $GLIB"

    want="$APT_CORE $APT_RUNTIME $APT_CAN $APT_CV"
    [ "$BUILD_RS" = 1 ] && want="$want $APT_BUILD"

    needed=""
    for p in $want; do
        dpkg -s "$p" >/dev/null 2>&1 || needed="$needed $p"
    done

    if [ -n "$needed" ]; then
        if [ "$ROOT_OK" = 1 ]; then
            printf '  installing:%s\n' "$needed"
            sudo_n apt-get update || warn "apt-get update failed — continuing with the cached index"
            # Word splitting of $needed is intended: it is a list of package names.
            # shellcheck disable=SC2086
            if sudo_n apt-get install -y $needed; then
                ok "apt packages installed"
            else
                warn "apt install failed for:$needed
        Read apt's own error above. If a name is simply unknown on this
        release, it was probably renamed — see the t64 note in this stage."
                need_root "sudo apt-get install -y$needed"
            fi
        else
            # This is the GB10's normal state: sudo wants a password and this
            # script is being driven over ssh. Missing apt packages are NOT
            # fatal — mediapipe, opencv and pyrealsense2 all ship self-
            # contained aarch64 wheels, so the venv stages below still do
            # their whole job. Only can-utils and the build toolchain are
            # genuinely unavailable without root, and both are optional.
            warn "sudo needs a password, so apt cannot run from here.
        Missing:$needed
        None of these block the Python install below — the pinned wheels are
        self-contained. Run the command printed at the end when you have a
        terminal on the box."
            need_root "sudo apt-get install -y$needed"
        fi
    else
        skip "all apt packages already present"
    fi
else
    printf '  install these with your package manager:\n    %s\n' \
        "$APT_CORE $APT_RUNTIME $APT_CAN $APT_CV"
fi

# Re-resolve rather than trusting stage 1: apt may have just installed it.
if [ -z "$PY312" ]; then
    PY312="$(command -v python3.12 2>/dev/null || true)"
fi
[ -n "$PY312" ] || die "python3.12 is still missing and nothing below can work without it.
        On Debian/Ubuntu:  sudo apt-get install -y python3.12 python3.12-venv"

# ------------------------------------------------------------ stage 3: venv ---
stage "creating the Python 3.12 virtualenv"

# WHERE THE VENV LIVES, and why it is not simply $REPO/venv. On the GB10 the
# venv was built at ~/wg-venv and $REPO/venv is a SYMLINK to it, because the
# repo directory is rsynced from the Mac and a real venv inside it would be
# clobbered by the next sync (and would carry the Mac's absolute paths). Honour
# an existing symlink instead of replacing it: following it is the difference
# between provisioning the venv the box actually uses and quietly building a
# second one that nothing imports from. WG_VENV lets a fresh box pick the same
# out-of-tree layout on the first run.
VENV="${WG_VENV:-$REPO/venv}"
if [ -L "$REPO/venv" ] && [ -z "${WG_VENV:-}" ]; then
    resolved="$(readlink -f "$REPO/venv" 2>/dev/null || true)"
    if [ -n "$resolved" ] && [ -d "$resolved" ]; then
        VENV="$resolved"
        ok "venv/ is a symlink into the box's own tree — using $VENV"
    fi
fi

# A venv built from the WRONG interpreter is the failure this guards. If it
# exists but is 3.11 or 3.13, every later stage installs into a Python that
# cannot run mediapipe or import pyrealsense2 — and pip would report success
# the whole way. Check the version rather than mere existence.
if [ -x "$VENV/bin/python" ]; then
    have="$("$VENV/bin/python" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
    if [ "$have" = "3.12" ]; then
        skip "venv already exists on Python $have at $VENV"
    else
        die "$VENV exists but is Python $have, and only 3.12 works here.
        Move it aside and re-run:  mv '$VENV' '$VENV.$have.bak' && bash tools/provision-linux.sh"
    fi
else
    # A dangling symlink is true for -L and false for -e, so without this check
    # we would try to create a venv on top of it and get a confusing error.
    [ -L "$VENV" ] && [ ! -d "$VENV" ] && \
        die "$VENV is a symlink pointing nowhere. rm '$VENV' && re-run."
    "$PY312" -m venv "$VENV" || die "venv creation failed. On Debian/Ubuntu the
        usual cause is the missing stdlib venv package:
        sudo apt-get install -y python3.12-venv"
    ok "venv created from $PY312 at $VENV"
fi
PY="$VENV/bin/python"

# ---------------------------------------------------- stage 4: pip packages ---
stage "installing Python packages"

"$PY" -m pip install --upgrade pip >/dev/null
# requirements.txt is the single source of truth for the pins, and every pin in
# it is load-bearing (see its own header). Never install these by name here —
# a second list is a second pipeline and they drift.
"$PY" -m pip install -r "$REPO/requirements.txt" || die \
    "pip install -r requirements.txt failed. Read the error: if it is
        mediapipe, confirm this venv is 3.12 (stage 3 checks that) and that
        the arch is aarch64."
ok "requirements.txt installed"

# python-can is REQUIRED by py/openyam.py's real path and is declared in
# requirements.txt, so the install above already covers it. Verify rather than
# re-install: a second `pip install python-can` here would be a second
# dependency list that can drift from the pinned one. Checking the import
# instead catches the case where requirements.txt loses the line again — which
# is the failure that put this check here, an arm that raised SystemExit the
# moment it was constructed.
if "$PY" -c 'import can' >/dev/null 2>&1; then
    ok "python-can importable (py/openyam.py needs it on the real path)"
else
    die "python-can is missing and py/openyam.py cannot open the bus without it.
        It should be pinned in requirements.txt — check that the line is still there."
fi

# pyrealsense2: THE WHEEL IS THE NORMAL PATH, not the fallback. Intel publishes
# an aarch64 manylinux wheel (2.58.4.10922 is what the GB10 runs) and it needs
# glibc >= 2.35, which Ubuntu 24.04 (2.39) satisfies comfortably. Installing it
# takes seconds where the source build takes 30-60 minutes, so trying the wheel
# FIRST is not an optimisation — it is the difference between a provisioning run
# an operator will actually sit through and one they will kill.
#
# The source build in stage 8 is still kept, because the wheel genuinely does
# not exist everywhere: there is no cp311 aarch64 wheel in any release, and an
# older glibc (Debian Bookworm is 2.36 and some images are older still) rejects
# it. On those machines the build is the only route, and deleting it would make
# this script a GB10-only tool.
RS_OK=0
if "$PY" -c 'import pyrealsense2' >/dev/null 2>&1; then
    skip "pyrealsense2 already importable"
    RS_OK=1
elif "$PY" -m pip install pyrealsense2 >/dev/null 2>&1 \
     && "$PY" -c 'import pyrealsense2' >/dev/null 2>&1; then
    ok "pyrealsense2 installed from the prebuilt aarch64 wheel"
    RS_OK=1
else
    warn "no usable pyrealsense2 wheel — stage 8 will build librealsense from source"
fi

# ------------------------------------------------------ stage 5: udev rules ---
stage "installing udev rules (camera and CAN adapter without root)"

# WHY THE TWO DEVICES NEED DIFFERENT TREATMENT, and why that is not an
# inconsistency: the D455 is a USB character device, so its permissions come
# from udev and a plugdev group. The CANable is NOT — gs_usb binds it to the
# kernel CAN stack and it appears as a NETWORK INTERFACE (can0), governed by
# CAP_NET_ADMIN, not by file permissions. So the CANable rule below does not
# grant access; it only gives the interface a stable name so a replug cannot
# rename can0 to can1 underneath a running demo.
RULES_D455=/etc/udev/rules.d/99-realsense-libusb.rules
RULES_CAN=/etc/udev/rules.d/99-canable.rules
rules_changed=0

write_root_file() {
    # $1 = destination path. Content on stdin. Returns non-zero (without
    # dying) when root is unavailable, so every caller can degrade to a
    # printed instruction instead of aborting the run.
    local dest="$1"
    sudo_n tee "$dest" >/dev/null
}

if [ -f "$RULES_D455" ]; then
    skip "D455 udev rule already present"
elif write_root_file "$RULES_D455" <<'EOF'
# Intel RealSense D455. 8086 is Intel; 0b5c is the D455's product id.
# MODE 0666 + GROUP plugdev is what Intel's own 99-realsense-libusb.rules
# does for this device. Without it librealsense opens the camera only as
# root, and the failure looks identical to "camera not plugged in".
SUBSYSTEMS=="usb", ATTRS{idVendor}=="8086", ATTRS{idProduct}=="0b5c", MODE:="0666", GROUP:="plugdev"
# The D455's IMU arrives through the industrial-IO subsystem, not USB, and
# needs its own rule or the depth+motion pipeline opens without motion.
KERNEL=="iio*", ATTRS{idVendor}=="8086", ATTRS{idProduct}=="0b5c", MODE:="0777", GROUP:="plugdev"
EOF
then
    # Intel's stock rules file covers product id 0b5c on four separate lines.
    # We write the minimal subset we can justify rather than fetching 108
    # lines over the network mid-provision — a provisioning step that needs
    # github to be up is a provisioning step that fails offline.
    ok "wrote $RULES_D455"
    rules_changed=1
else
    warn "cannot write $RULES_D455 without root."
    need_root "sudo tee $RULES_D455 <<'EOF'
SUBSYSTEMS==\"usb\", ATTRS{idVendor}==\"8086\", ATTRS{idProduct}==\"0b5c\", MODE:=\"0666\", GROUP:=\"plugdev\"
KERNEL==\"iio*\", ATTRS{idVendor}==\"8086\", ATTRS{idProduct}==\"0b5c\", MODE:=\"0777\", GROUP:=\"plugdev\"
EOF"
fi

if [ -f "$RULES_CAN" ]; then
    skip "CANable udev rule already present"
elif write_root_file "$RULES_CAN" <<'EOF'
# CANable 2.0 running gs_usb firmware: VID 0x1D50, PID 0x606F.
# This rule does NOT grant permission — a SocketCAN device is a netdev and
# is governed by CAP_NET_ADMIN, not by file modes. It pins the NAME, so a
# replug mid-setup cannot bring the adapter back as can1 and leave
# py/openyam.py talking to an interface that no longer exists.
SUBSYSTEM=="net", ACTION=="add", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="606f", NAME="can0"
EOF
then
    ok "wrote $RULES_CAN"
    rules_changed=1
else
    warn "cannot write $RULES_CAN without root."
    need_root "sudo tee $RULES_CAN <<'EOF'
SUBSYSTEM==\"net\", ACTION==\"add\", ATTRS{idVendor}==\"1d50\", ATTRS{idProduct}==\"606f\", NAME=\"can0\"
EOF"
fi

if [ "$rules_changed" = 1 ]; then
    sudo_n udevadm control --reload-rules || warn "udevadm reload failed"
    sudo_n udevadm trigger || true
    ok "udev rules reloaded"
fi

# plugdev membership is what makes the D455 rule actually reach this user.
# It takes effect on the NEXT LOGIN, which is the single most common reason
# "I ran the provisioner and the camera still needs sudo" — say so here.
if id -nG "$USER" | tr ' ' '\n' | grep -qx plugdev; then
    skip "$USER already in plugdev"
elif sudo_n usermod -aG plugdev "$USER"; then
    warn "added $USER to plugdev — LOG OUT AND BACK IN before the camera works
        as a normal user. Group membership is read at login, so this shell
        still does not have it."
else
    warn "$USER is not in plugdev and root is unavailable to fix it."
    need_root "sudo usermod -aG plugdev $USER   # then log out and back in"
fi

# -------------------------------------------------- stage 6: SocketCAN can0 ---
stage "setting up SocketCAN can0 at 1000000 bitrate"

# gs_usb ships in the Ubuntu and Debian kernels and in the NVIDIA GB10 kernel
# (verified present at
# /lib/modules/6.17.0-1022-nvidia/kernel/drivers/net/can/usb/gs_usb.ko.zst),
# so this is a load, not an install. modinfo is the honest test: it asks
# whether the module EXISTS, which is a question we can answer without root,
# where modprobe cannot even be attempted without it.
if lsmod | grep -q '^gs_usb'; then
    skip "gs_usb already loaded"
elif ! modinfo gs_usb >/dev/null 2>&1; then
    warn "this kernel has no gs_usb module at all ($(uname -r)).
        Without it the CANable cannot become a SocketCAN interface and the
        arm cannot be commanded. Check: modinfo gs_usb"
elif sudo_n modprobe gs_usb; then
    ok "gs_usb loaded"
else
    warn "gs_usb exists in this kernel but loading it needs root."
    need_root "sudo modprobe gs_usb"
fi

# Persist the module across reboots. grep -qx before appending is what makes
# re-running this script safe — without it every run adds another line.
if [ -f /etc/modules-load.d/gs_usb.conf ] && grep -qx gs_usb /etc/modules-load.d/gs_usb.conf; then
    skip "gs_usb persisted in /etc/modules-load.d"
elif echo gs_usb | write_root_file /etc/modules-load.d/gs_usb.conf; then
    ok "gs_usb will load at boot"
else
    warn "cannot persist gs_usb without root."
    need_root "echo gs_usb | sudo tee /etc/modules-load.d/gs_usb.conf"
fi

# The bitrate is 1000000 because that is what the Damiao bus runs at, and it
# is stated in exactly one other place — py/openyam.py's bitrate default
# (py/openyam.py:333). If one moves the other must move with it; a mismatch
# does not error, the bus just goes silent and every motor looks dead.
#
# WHY systemd-networkd AND NOT NetworkManager. NetworkManager owns wifi and
# ethernet on this box, but nmcli 1.46 has NO CAN device type — it cannot
# configure a bitrate or bring a CAN link up, and it leaves can0 unmanaged.
# systemd-networkd can, it is socket-activated and already running here, and
# `networkctl list` shows it managing nothing, so claiming can0 starts no
# fight with NM over an interface NM does not want. The catch that this stage
# must handle: the systemd-networkd UNIT is `disabled` on Ubuntu 24.04, so the
# .network file alone would work this boot and silently stop working after a
# reboot. Enabling the unit is the load-bearing half, not the file.
CAN_NET=/etc/systemd/network/80-can.network
if [ -f "$CAN_NET" ] && grep -q 'BitRate=1000000' "$CAN_NET"; then
    skip "can0 systemd-networkd unit already configured at 1000000"
elif write_root_file "$CAN_NET" <<'EOF'
# Brings can0 up at boot at the Damiao bus rate. This is the reason CAN
# needs no sudo at demo time: systemd-networkd owns the interface, so the
# demo user never has to run `ip link set can0 up`.
#
# RestartSec bounds a bus-off recovery. Without it a single bus fault (a
# loose terminator, a brownout on the motor supply) takes can0 down and it
# never comes back until someone notices and reboots.
[Match]
Name=can0

[CAN]
BitRate=1000000
RestartSec=100ms
EOF
then
    ok "wrote $CAN_NET"
else
    warn "cannot write $CAN_NET without root."
    need_root "sudo tee $CAN_NET <<'EOF'
[Match]
Name=can0

[CAN]
BitRate=1000000
RestartSec=100ms
EOF"
fi

# `enable` is checked separately from the file because they fail separately:
# a box can have the .network file from a previous run and still lose can0 on
# the next reboot because the unit was never enabled.
if systemctl is-enabled systemd-networkd >/dev/null 2>&1; then
    skip "systemd-networkd enabled at boot"
elif sudo_n systemctl enable systemd-networkd; then
    ok "systemd-networkd enabled — can0 will come back after a reboot"
    sudo_n systemctl restart systemd-networkd || warn "systemd-networkd restart failed"
else
    warn "systemd-networkd is NOT enabled at boot on this box. can0 will come up
        now but NOT after a reboot until this is fixed."
    need_root "sudo systemctl enable --now systemd-networkd"
fi

# Bring it up NOW too, so the selftest at the end of this run has something to
# look at. It is allowed to fail: the adapter may simply not be plugged in yet,
# which is not a provisioning error.
if ip link show can0 >/dev/null 2>&1; then
    if ip -details link show can0 | grep -q 'state UP'; then
        ok "can0 is up"
    elif sudo_n ip link set can0 up type can bitrate 1000000; then
        ok "can0 brought up at 1000000"
    else
        warn "can0 exists but would not come up — check: ip -details link show can0"
        need_root "sudo ip link set can0 up type can bitrate 1000000"
    fi
else
    warn "no can0 interface yet. Normal if the CANable is not plugged in.
        Plug it in and check: dmesg | tail, then ip link show can0"
fi

# --------------------------------------- stage 7: remove the pyrealsense2 stub ---
stage "checking for the pyrealsense2 replay stub"

# THE STUB SHADOWS THE REAL SDK. <venv>/lib/python3.12/site-packages/
# pyrealsense2.py is a hand-written module that exists on the Mac because no
# Apple Silicon wheel exists; it satisfies the import and raises a clear error
# if a live-camera path is actually reached. A plain .py file and a real
# package both sit in site-packages, and which one wins is not something to
# leave to chance — on Linux the real SDK MUST win, because here the live
# camera path is the point.
#
# Order matters: this runs AFTER the pip stage so "is there a real binding"
# is a question with an answer. Deleting the stub before knowing that would
# leave a box with neither.
STUB="$("$PY" - <<'PYSTUB'
import sysconfig, os
p = os.path.join(sysconfig.get_paths()["purelib"], "pyrealsense2.py")
print(p if os.path.isfile(p) else "")
PYSTUB
)"

if [ -z "$STUB" ]; then
    skip "no stub present"
elif [ "$RS_OK" = 1 ]; then
    # Move rather than delete. If the real binding turns out to be broken, the
    # stub is the thing that gets scrub3d's replay path running again, and a
    # deleted file cannot be put back on a box with no network.
    mv "$STUB" "$STUB.stub-disabled"
    ok "stub moved aside to $(basename "$STUB").stub-disabled — the real SDK now wins"
    # Prove it, rather than assuming the move was enough. A leftover
    # __pycache__/pyrealsense2.*.pyc would keep shadowing after the .py is gone.
    find "$(dirname "$STUB")/__pycache__" -name 'pyrealsense2.*' -delete 2>/dev/null || true
    if "$PY" -c 'import pyrealsense2 as rs; rs.context' >/dev/null 2>&1; then
        ok "verified: import pyrealsense2 now resolves to the real SDK"
    else
        warn "stub is gone but pyrealsense2 still does not import cleanly —
        run: $PY tools/hardware-selftest.py  for the specific reason"
    fi
else
    warn "stub is present and there is NO real binding yet. Leaving it in place:
        removing it now would break scrub3d's replay path and fix nothing.
        Re-run this script after stage 8 finishes the source build."
fi

# ------------------------------------------- stage 8: librealsense from source ---
stage "librealsense with Python bindings"

if [ "$RS_OK" = 1 ]; then
    skip "pyrealsense2 already works — no source build needed"
elif [ "$BUILD_RS" = 0 ]; then
    skip "--no-build given"
else
    SRC="$HOME/src/librealsense"
    echo "  Only reached when no wheel fits this machine. 10 minutes on a"
    echo "  20-core GB10, 30-60 on a Pi 5. It is the last stage on purpose."

    # A small-memory board OOMs partway through the C++ compile and the failure
    # is a bare "c++: fatal error: Killed signal terminated program cc1plus",
    # which reads like a compiler bug rather than an out-of-memory. Raise swap
    # first WHERE THAT MECHANISM EXISTS: dphys-swapfile is a Raspberry Pi OS
    # package and is absent on Ubuntu (the GB10 has 121GB of RAM and 15GB of
    # swap, so it needs none of this). Guard on the file, and scale the build
    # parallelism to memory rather than to core count — 20 cores × ~1GB per
    # cc1plus is what actually kills a small box.
    if [ -f /etc/dphys-swapfile ] && ! grep -q '^CONF_SWAPSIZE=2048' /etc/dphys-swapfile; then
        if sudo_n sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile; then
            sudo_n dphys-swapfile setup && sudo_n dphys-swapfile swapon
            warn "swap raised to 2048MB for the build. Lower it again afterwards if
        you care about SD card wear: edit /etc/dphys-swapfile, then
        sudo dphys-swapfile setup && sudo dphys-swapfile swapon"
        fi
    fi

    if [ -d "$SRC/.git" ]; then
        skip "source tree already at $SRC"
    else
        mkdir -p "$(dirname "$SRC")"
        git clone --depth 1 https://github.com/IntelRealSense/librealsense "$SRC"
    fi

    mkdir -p "$SRC/build"
    # -DFORCE_RSUSB_BACKEND=true is the load-bearing flag. It makes
    # librealsense talk to the camera through libusb in userspace instead of
    # through V4L2, so NO KERNEL PATCHING is required — and kernel patches do
    # not survive a kernel update, which would silently break the camera weeks
    # later. That matters more here than on a Pi: the GB10 runs a vendor
    # kernel (6.17.0-*-nvidia) that has no librealsense patch set at all.
    # Intel's own installation docs still mention the older -DFORCE_LIBUVC
    # flag; that one is obsolete, do not copy it back.
    #
    # -DBUILD_PYTHON_BINDINGS with an explicit PYTHON_EXECUTABLE is what
    # produces pyrealsense2 at all, and the explicit path is what keeps it
    # from building against the system interpreter instead of our 3.12 venv.
    ( cd "$SRC/build" && cmake .. \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_EXAMPLES=false \
        -DBUILD_GRAPHICAL_EXAMPLES=false \
        -DFORCE_RSUSB_BACKEND=true \
        -DBUILD_PYTHON_BINDINGS=true \
        -DPYTHON_EXECUTABLE="$PY" )

    # One cc1plus peaks near 1GB on this codebase, so jobs are capped by
    # available memory as well as cores. On the GB10 that leaves 20; on a 4GB
    # Pi it lands on 3 and the build finishes instead of being OOM-killed.
    mem_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 4194304)"
    jobs_mem=$(( mem_kb / 1048576 ))
    [ "$jobs_mem" -lt 1 ] && jobs_mem=1
    jobs="$(nproc)"
    [ "$jobs_mem" -lt "$jobs" ] && jobs="$jobs_mem"
    echo "  building with -j$jobs (cores: $(nproc), memory-capped at $jobs_mem)"
    # No backticks in this message: it is inside double quotes, where a
    # backtick is command substitution and would RUN whatever it wrapped at
    # the moment the build failed.
    ( cd "$SRC/build" && make -j"$jobs" && sudo_n make install ) || die \
        "librealsense build or install failed. If it was the install step that
        failed, it needs root: sudo make install && sudo ldconfig  from $SRC/build"
    sudo_n ldconfig || warn "ldconfig needs root — run: sudo ldconfig"

    # cmake installs the .so somewhere on the system, not into the venv. Copy
    # it in rather than exporting PYTHONPATH: a PYTHONPATH only helps shells
    # that happen to have sourced the right rc file, and the demo is launched
    # from run.sh, from systemd and from ssh — three different environments.
    # A file inside the venv works in all of them.
    SITE="$("$PY" -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
    found=0
    for so in "$SRC/build/Release"/pyrealsense2*.so "$SRC/build/wrappers/python"/pyrealsense2*.so; do
        [ -f "$so" ] || continue
        cp "$so" "$SITE/" && found=1
    done
    [ "$found" = 1 ] || die "build finished but no pyrealsense2*.so was found under $SRC/build"

    if "$PY" -c 'import pyrealsense2' >/dev/null 2>&1; then
        ok "pyrealsense2 built and importable"
        RS_OK=1
    else
        die "built, copied, still not importable. Check the DT_NEEDED libs:
        ldd $SITE/pyrealsense2*.so | grep 'not found'"
    fi

    # The stub check ran before the build, so if it deferred, resolve it now.
    if [ -n "$STUB" ] && [ -f "$STUB" ]; then
        mv "$STUB" "$STUB.stub-disabled"
        ok "stub moved aside now that the real SDK exists"
    fi
fi

# ------------------------------------------------------------------- done ---
cat <<EOF

------------------------------------------------------------
PROVISIONING COMPLETE. Nothing above proves the HARDWARE works —
it proves the software is installed. Check the hardware now:

    $PY tools/hardware-selftest.py

If you were added to plugdev in this run, LOG OUT AND BACK IN first,
or the camera will still report a permission failure.
EOF

# The whole point of the -n sudo policy is that this list exists and is
# complete. A run that could not get root is still a useful run, but only if
# it ends by naming every command a human still owes the machine — otherwise
# the operator is left to re-derive them from scrollback.
if [ "${#NEEDS_ROOT[@]}" -gt 0 ]; then
    printf '\nSTILL NEEDS ROOT — run these on the box, in a terminal that can\n'
    printf 'answer a sudo password prompt:\n\n'
    for c in "${NEEDS_ROOT[@]}"; do
        printf '%s\n\n' "$c"
    done
fi
printf -- '------------------------------------------------------------\n'
