#!/usr/bin/env python3
"""tools/hardware-selftest.py — is each piece of hardware actually there?

    python3 tools/hardware-selftest.py
    python3 tools/hardware-selftest.py --json      # for scripts

Every check runs INDEPENDENTLY. A dead CAN bus must not stop the camera from
being tested, because the whole point of a selftest is to tell you WHICH of
four things is broken when the demo will not start. So no check may raise, and
no check may depend on another having passed.

Three results, and the difference between them is the whole value of the file:

    PASS  the thing was exercised and worked
    FAIL  the thing should work here and does not — a problem to fix
    N/A   the thing was not exercised, for a named reason: either this
          platform cannot do it at all, or the hardware it needs is absent

N/A is not a softer FAIL. On a Mac there is no SocketCAN, so "CAN interface
up" is not a failing test, it is a test that does not apply. Equally, on this
Linux box with the CANable unplugged, can0 not existing is the expected and
correct state of a machine missing one USB device — not a fault in the
machine. Reporting either as FAIL would train an operator to ignore red,
which is how a real FAIL gets missed on the day.

AND THE N/A REASON MUST NAME WHICH KIND IT IS. "CAN unavailable" is useless:
on a Mac it means "never, buy a Linux box", and here it means "plug the
adapter in". A row that does not distinguish those two sends the operator
after the wrong problem, which is the failure this file exists to prevent.

WHAT THIS DOES NOT DO: it never commands a motor to move. It enables nothing
and sends no MIT frames. The motor check listens for frames the motors emit
and, at most, sends the same enable frame py/openyam.py sends at construction.
A selftest that moved the arm would be a selftest nobody dares run with a
person nearby, which is exactly when you most need it.
"""
import argparse
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "py"))

IS_LINUX = platform.system() == "Linux"
IS_MAC = platform.system() == "Darwin"


def describe_platform():
    """A one-line name for the machine, specific enough to be actionable.

    NOT cosmetic. Every FAIL in this file carries a remedy, and the correct
    remedy differs per board: `vcgencmd` exists only on a Pi, apt package
    names differ between Debian arm64 and a Jetson/GB10 image, and an operator
    told "run vcgencmd" on a GB10 gets `command not found` and stops trusting
    the table. So the remedies below branch on the board, and the board has to
    be named here rather than guessed from `platform.machine()` alone --
    aarch64 covers a Pi, a Jetson, a GB10 and an Apple Silicon Mac, which are
    four different machines with four different fixes.
    """
    arch = platform.machine()
    if IS_MAC:
        return f"macOS {arch}"
    if not IS_LINUX:
        return f"{platform.system()} {arch}"
    # /proc/device-tree/model is the ARM board's own self-description. It is
    # NUL-terminated because it comes straight out of the flattened device
    # tree, so it has to be stripped or the string carries a trailing \0 into
    # every later comparison.
    model = ""
    try:
        with open("/proc/device-tree/model", "rb") as fh:
            model = fh.read().decode("utf-8", "replace").strip("\0").strip()
    except Exception:
        pass
    if not model:
        # DMI is the x86-and-some-arm fallback when there is no device tree.
        try:
            with open("/sys/class/dmi/id/product_name") as fh:
                model = fh.read().strip()
        except Exception:
            pass
    return f"Linux {arch}" + (f" — {model}" if model else "")


PLATFORM_NAME = describe_platform()
_PLAT_LOWER = PLATFORM_NAME.lower()
# A Pi is the only board here with vcgencmd and the 600mA USB current cap, so
# the USB-power remedy must not be offered anywhere else.
IS_PI = "raspberry" in _PLAT_LOWER

# The CANable 2.0's USB ids, running gs_usb firmware. Same pair as the udev
# rule in tools/provision-linux.sh:  keep them in step.
CANABLE_VID, CANABLE_PID = 0x1D50, 0x606F
CAN_IFACE = os.environ.get("CAN_IFACE", "can0")
CAN_BITRATE = 1_000_000

PASS, FAIL, NA = "PASS", "FAIL", "N/A"

results = []


def record(name, status, detail, cause=None, command=None):
    """One row of the table.

    `cause` and `command` are only meaningful on FAIL, and they are required
    there by convention rather than by code: docs/RECOVERY-CARD.md's entire
    format is symptom -> fix -> time, and a FAIL with no next command is a row
    that sends the operator back to reading source at the worst moment.
    """
    results.append(dict(name=name, status=status, detail=detail,
                        cause=cause, command=command))
    return status


# --------------------------------------------------------------- camera ---
def check_camera_rgb():
    """Open the camera the way py/vision.py opens it, and read a real frame.

    isOpened() alone is NOT sufficient and this is measured, not theoretical:
    py/vision.py's TRAP 3 documents that a permission-denied camera on macOS
    returns isOpened()==False with no exception, and a camera claimed by
    another process can return isOpened()==True and then hand back black
    frames forever. So we read a frame and check its shape.
    """
    try:
        import cv2
    except ImportError:
        return record("camera RGB", FAIL, "cv2 not installed",
                      cause="the venv is missing opencv-contrib-python",
                      command="pip install -r requirements.txt")

    # Probing an index that does not exist makes OpenCV print its own
    # complaint ("out device of bound") from C, NOT from Python, so
    # contextlib.redirect_stderr cannot catch it. Scanning past the last
    # camera is normal and expected here, so that noise is pure confusion in
    # a table whose job is to be read at a glance. Swap the OS-level fd 2 for
    # the duration of the scan and put it straight back.
    saved_fd = None
    devnull = None
    try:
        saved_fd = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
    except Exception:
        # If the swap fails, the noise prints. That is strictly better than
        # losing the check, so carry on either way.
        pass

    opened = []
    for idx in range(3):
        cap = None
        try:
            cap = cv2.VideoCapture(idx)
            if not cap.isOpened():
                continue
            ok, frame = cap.read()
            if ok and frame is not None and frame.size:
                h, w = frame.shape[:2]
                opened.append((idx, w, h))
        except Exception:
            # A camera index that throws is a camera index that does not
            # work, which is all this loop needs to know. Never let one bad
            # index end the scan — the good one may be index 1.
            pass
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    # Restore stderr BEFORE any record() call, or every FAIL message below
    # would be written into /dev/null and the operator would see an empty row.
    if saved_fd is not None:
        try:
            os.dup2(saved_fd, 2)
            os.close(saved_fd)
        except Exception:
            pass
    if devnull is not None:
        try:
            os.close(devnull)
        except Exception:
            pass

    if not opened:
        # On Linux, "no /dev/video* at all" and "devices exist but none gave a
        # frame" are different faults with different fixes, and the generic
        # message below covers only the second. A D455 that has dropped off
        # USB takes its video nodes with it, and telling that operator to
        # check group membership sends them after a permissions problem they
        # do not have.
        if IS_LINUX and not glob.glob("/dev/video*"):
            return record(
                "camera RGB", FAIL, "no /dev/video* devices exist at all",
                cause="no camera is enumerated on USB. The D455 in particular "
                      "can reset itself off the bus and stay gone until it is "
                      "physically replugged — its video nodes vanish with it",
                command="lsusb | grep 8086   # nothing here means unplug and replug it")
        if IS_MAC:
            return record(
                "camera RGB", FAIL, "no camera index returned a frame",
                cause="macOS denies camera access SILENTLY — no exception, "
                      "just isOpened()==False. The grant belongs to the "
                      "TERMINAL APP (Terminal/iTerm/VS Code), not to the "
                      "python binary.",
                command="open 'x-apple.systempreferences:com.apple.preference."
                        "security?Privacy_Camera'")
        return record(
            "camera RGB", FAIL, "no camera index returned a frame",
            cause="on Linux this is a missing udev rule, absent plugdev "
                  "membership (it is read at LOGIN — a fresh usermod does not "
                  "apply to this shell), or the camera on a port that did not "
                  "enumerate",
            command="ls -l /dev/video* && id -nG && dmesg | tail -20")

    detail = ", ".join(f"index {i}: {w}x{h}" for i, w, h in opened)
    return record("camera RGB", PASS, detail)


def check_camera_depth():
    """Depth needs the real librealsense binding AND exclusive USB access.

    Both halves fail differently and the operator needs to know which, so this
    check reports three distinct outcomes rather than one boolean.
    """
    try:
        import pyrealsense2 as rs
    except ImportError:
        # The remedy differs by platform and the old text asserted "this Mac"
        # unconditionally, which is simply false on the GB10 -- where a
        # prebuilt aarch64 wheel exists, installs without compiling, and is
        # the entire reason depth works on this box at all.
        if IS_MAC:
            return record(
                "camera depth", FAIL, "pyrealsense2 not installed",
                cause="Intel publishes no Apple Silicon wheel, so depth is "
                      "not available on this machine at all. The replay stub "
                      "normally stands in for it and even that is missing",
                command="run depth on the Linux box instead")
        return record(
            "camera depth", FAIL, "pyrealsense2 not installed",
            cause="the Intel SDK's Python binding is missing from this venv. "
                  "A prebuilt aarch64 wheel exists, so this needs no compiling",
            command="pip install pyrealsense2   # or: bash tools/provision-linux.sh")

    # THE STUB CHECK. venv/lib/*/site-packages/pyrealsense2.py is a hand-
    # written placeholder that satisfies the import and raises only when a
    # live-camera call is reached. It resolves EVERY attribute, so any
    # hasattr() probe returns True and a naive check reports a working SDK.
    #
    # THE DISCRIMINATOR IS A COMPILED EXTENSION ANYWHERE IN THE MODULE, NOT
    # THE FILENAME. An earlier version tested `src.endswith(".py")`, which is
    # correct for the flat stub (site-packages/pyrealsense2.py) but WRONG for
    # the real aarch64 wheel: that ships a PACKAGE whose __init__.py re-exports
    # from pyrealsense2.cpython-312-aarch64-linux-gnu.so beside it. So
    # __file__ ends in ".py" for both, and the filename test reported the
    # genuine, streaming SDK on the GB10 as "the replay STUB" -- a confident
    # FAIL, with a remedy that would have had an operator RENAME THE WORKING
    # SDK's entry point and destroy depth on the one machine that has it.
    # Measured on the GB10, 2026-09-19.
    #
    # `rs.__spec__.origin` is the .so directly when the wheel is a flat
    # extension module; when it is a package, the .so sits next to __init__.py.
    # Check both, and only conclude "stub" when NO compiled object exists.
    src = getattr(rs, "__file__", "") or ""
    origin = getattr(getattr(rs, "__spec__", None), "origin", "") or ""
    compiled = origin.endswith((".so", ".pyd", ".dylib"))
    if not compiled and src:
        pkg_dir = os.path.dirname(src)
        try:
            compiled = any(f.startswith("pyrealsense2")
                           and f.endswith((".so", ".pyd", ".dylib"))
                           for f in os.listdir(pkg_dir))
        except OSError:
            compiled = False
    if not compiled:
        if not IS_MAC:
            return record(
                "camera depth", FAIL,
                f"pyrealsense2 is the replay STUB, not the SDK "
                f"({os.path.basename(src)})",
                cause="the stub satisfies the import and resolves every "
                      "attribute, so it SHADOWS the real SDK silently. On Linux "
                      "the real binding must win or depth never works",
                command=f"mv {src} {src}.stub-disabled")
        # On macOS the stub is expected, but stopping here would hide the more
        # useful fact: whether librealsense itself can reach the camera. The
        # Homebrew C++ tools answer that without the Python binding, and the
        # answer separates "no SDK" from "SDK present, camera unreachable".
        detail = "no Python binding on Apple Silicon (replay stub in place)"
        cause = ("Intel publishes no Apple Silicon pyrealsense2 wheel, and the "
                 "stub keeps scrub3d's replay path importable")
        if shutil.which("rs-enumerate-devices"):
            try:
                r = subprocess.run(["rs-enumerate-devices"], capture_output=True,
                                   text=True, timeout=60)
                if "RS2_USB_STATUS_ACCESS" in (r.stdout + r.stderr):
                    detail = "librealsense installed, but it cannot claim the camera"
                    cause = ("macOS UVCAssistant claims all five of the D455's "
                             "video interfaces before librealsense can, so "
                             "libusb fails with RS2_USB_STATUS_ACCESS. This is "
                             "a macOS architecture constraint, not a "
                             "permissions problem, and there is no fix on this "
                             "machine. RGB is unaffected because it goes "
                             "through UVC/AVFoundation, which is the SAME path "
                             "py/vision.py uses. Depth needs the Pi.")
            except Exception:
                pass
        return record("camera depth", NA, detail, cause=cause)

    try:
        ctx = rs.context()
        devs = list(ctx.query_devices())
    except Exception as e:
        return record(
            "camera depth", FAIL, f"librealsense could not enumerate: {e}",
            cause="usually a permissions problem reaching the USB device",
            command="sudo udevadm control --reload-rules && sudo udevadm trigger")

    if not devs:
        if IS_MAC:
            return record(
                "camera depth", NA,
                "librealsense sees 0 devices (RS2_USB_STATUS_ACCESS)",
                cause="macOS UVCAssistant claims all five of the D455's video "
                      "interfaces before librealsense can, so libusb cannot "
                      "claim interface 0. This is a macOS architecture "
                      "constraint, not a fault and not fixable by permissions. "
                      "RGB still works because it goes through UVC/AVFoundation, "
                      "which is the SAME path py/vision.py uses.")
        return record(
            "camera depth", FAIL, "librealsense sees 0 devices",
            cause="D455 unplugged, on a USB2 port, or the udev rule is "
                  "missing. Note the D455 can also drop OFF the bus after a "
                  "reset and not come back without a physical replug — if "
                  "lsusb shows no 8086 device, the camera is gone, not merely "
                  "unreadable",
            command="lsusb | grep 8086   # nothing here means unplug and replug it")

    # A device that enumerates may still fail to stream — that is the failure
    # that matters, so actually start a pipeline and pull one frame.
    try:
        pipe = rs.pipeline()
        cfg = rs.config()
        cfg.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, 30)
        pipe.start(cfg)
        try:
            frames = pipe.wait_for_frames(5000)
            d = frames.get_depth_frame()
            got = bool(d)
            wh = f"{d.get_width()}x{d.get_height()}" if got else "?"
        finally:
            pipe.stop()
    except Exception as e:
        # The remedy is board-specific and a wrong one destroys trust faster
        # than no remedy: `vcgencmd` exists ONLY on a Pi, so offering it on the
        # GB10 hands the operator a `command not found` at the exact moment
        # they needed a real answer.
        if IS_PI:
            cause = ("on a Pi this is most often USB power — the Pi 5 caps "
                     "total USB current at 600mA unless it detects a 5A "
                     "supply, and the D455 browns out mid-stream rather than "
                     "failing to enumerate")
            command = "vcgencmd get_throttled   # non-zero means power trouble"
        else:
            cause = ("the D455 enumerated but would not deliver frames. The "
                     "usual causes are a USB2 link where the requested mode "
                     "needs USB3 bandwidth, another process already streaming "
                     "from it, or the camera drawing more current than the "
                     "port allows and resetting itself off the bus mid-stream")
            command = ("lsusb -t | grep -A2 8086   # confirm it is on a 5000M "
                       "port, not 480M")
        return record(
            "camera depth", FAIL, f"device present but streaming failed: {e}",
            cause=cause, command=command)

    if not got:
        return record("camera depth", FAIL, "pipeline started but no depth frame",
                      cause="camera streaming but delivering nothing",
                      command="rs-enumerate-devices -c")
    return record("camera depth", PASS, f"depth frame {wh} @ 30fps")


# ------------------------------------------------------------------ CAN ---
def _socketcan_available():
    """Can this kernel open a CAN socket at all? Returns (ok, reason_if_not).

    This is the ONE question that separates "macOS, hopeless" from "Linux,
    just waiting for hardware", and it is answered by ASKING THE KERNEL rather
    than by reading platform.system(). Opening a PF_CAN/SOCK_RAW socket needs
    no interface, no adapter and no root: it succeeds as soon as the CAN core
    exists, and the attempt AUTOLOADS the `can` module if it is built as one
    (measured on the GB10 -- `lsmod | grep can` was empty before this call and
    the socket still opened). A modprobe check alone would have reported the
    stack missing on a box where it works.

    The socket is closed immediately. Nothing is transmitted and no interface
    is bound, so this cannot disturb a live bus.
    """
    import socket
    if not hasattr(socket, "AF_CAN"):
        return False, f"Python on {platform.system()} has no socket.AF_CAN"
    sk = None
    try:
        sk = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        return True, ""
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        if sk is not None:
            try:
                sk.close()
            except Exception:
                pass


def _iface_exists(name=None):
    """Does this network interface exist right now?

    /sys/class/net is the cheapest truthful answer and needs no subprocess,
    no iproute2 and no root. Used as a GUARD by the traffic and motor checks
    so an absent adapter produces one honest N/A per row instead of a cascade
    of FAILs whose real cause is three rows higher up the table.
    """
    return os.path.exists(os.path.join("/sys/class/net", name or CAN_IFACE))


def _gs_usb_module_path():
    """Is the CANable's kernel driver present on disk? Path, or "".

    Deliberately checks the FILE rather than running `modprobe`, because
    modprobe needs root and this box has a sudo password. Knowing the driver
    is installed changes the remedy: "plug it in and it will bind" instead of
    "you also need to install a kernel module", and getting that wrong costs
    an operator a package hunt they do not need.
    """
    base = os.path.join("/lib/modules", platform.release(),
                        "kernel/drivers/net/can/usb")
    for ext in (".ko.zst", ".ko.xz", ".ko.gz", ".ko"):
        p = os.path.join(base, "gs_usb" + ext)
        if os.path.exists(p):
            return p
    return ""


# ------------------------------------------------------------ RoArm serial ---
def check_roarm_ports():
    """Which RoArm-M2-S driver boards are plugged in, by USB chip.

    SHARES scrub3d/live/arm_hw.py's candidate_ports() rather than re-globbing
    /dev/cu.*. The two ways of finding a board disagree on purpose-built
    hardware: py/arm.py's find_port() matches on the DEVICE NAME
    (/dev/cu.usbserial-*), which on a Mac also matches an Arduino, a GPS
    puck or any other CP210x/CH340 gadget, and returns the first one
    alphabetically. arm_hw matches on the USB (vendor, product) pair, so it
    names the chip and can list several boards at once -- which is what the
    four-arm rig needs and what an operator actually wants to see here.

    Keeping one implementation means a board that arm_hw will drive is a
    board this selftest reports, with no second list to fall out of date.

    NOTHING IS OPENED. Opening the port is what resets the ESP32 on a board
    whose DTR/RTS are not held low, and a selftest that reboots the arm is
    one nobody runs with a person nearby (this file's own rule, above).
    """
    try:
        sys.path.insert(0, os.path.join(REPO, "scrub3d", "live"))
        sys.path.insert(0, os.path.join(REPO, "scrub3d"))
        import arm_hw
    except Exception as e:
        # pyserial missing is the realistic cause, and it is the same import
        # py/arm.py needs, so this is a genuine FAIL on a RoArm host.
        return record("RoArm boards present", FAIL,
                      f"could not load the arm driver: {e}",
                      cause="scrub3d/live/arm_hw.py did not import, so neither "
                            "the four-arm rig nor this check can find a board",
                      command="./venv/bin/pip install -r requirements.txt")
    try:
        ports = arm_hw.candidate_ports()
    except Exception as e:
        return record("RoArm boards present", FAIL, f"could not list USB serial: {e}",
                      cause="pyserial's list_ports raised",
                      command="./venv/bin/python -c "
                              "'from serial.tools import list_ports; "
                              "print(list(list_ports.comports()))'")
    if not ports:
        # N/A, not FAIL, for the same reason as the CAN adapter row: no board
        # attached is a true fact about the machine, not a fault in it. The
        # demo runs --no-arm every day.
        return record(
            "RoArm boards present", NA, "no driver board on USB",
            cause="no USB device carrying a CP210x/CH340/ESP32 serial chip is "
                  "attached, so there is no arm to command. The demo still "
                  "runs: py/scrubbot.py --no-arm",
            command="plug a board's middle USB-C port into this machine, then "
                    "re-run. On macOS the SiLabs CP210x driver must be "
                    "installed and approved first")
    return record("RoArm boards present", PASS,
                  ", ".join(f"{p['port']} ({p['chip']})" for p in ports))


def check_roarm_assignment():
    """Is each arm's port written down, so the rig knows which is which?

    Four identical boards enumerate in whatever order macOS felt like. The
    rig file says where the RED arm stands; arm_ports.json is the only thing
    that says which /dev/cu.* IS red. Without it a four-arm run drives the
    right poses to the wrong arms, and every collision check is then computed
    for an arm that is somewhere else -- the one failure this whole stack
    cannot detect from the inside.

    Only meaningful once a board is attached, so it reads N/A otherwise
    rather than telling an operator with no hardware to go run --assign.
    """
    try:
        sys.path.insert(0, os.path.join(REPO, "scrub3d", "live"))
        import arm_hw
    except Exception:
        return record("RoArm arms named", NA, "the arm driver did not import")
    try:
        attached = bool(arm_hw.candidate_ports())
    except Exception:
        attached = False
    if not attached:
        return record("RoArm arms named", NA, "no board attached to name")
    try:
        ports = arm_hw.load_ports()
    except arm_hw.ArmError as e:
        return record(
            "RoArm arms named", FAIL, str(e).splitlines()[0],
            cause="scrub3d/live/arm_ports.json does not say which board is "
                  "which arm, so a multi-arm run would drive red's poses to "
                  "whichever board enumerated first",
            command="python scrub3d/live/arm_hw.py --assign")
    named = {arm_hw.NAMES[i % 4]: p for i, p in sorted(ports.items()) if p}
    return record("RoArm arms named", PASS,
                  ", ".join(f"{k}={v}" for k, v in named.items()) or "none")


def check_can_adapter():
    """Is the CANable 2.0 physically attached? Separate from can0 existing.

    Worth its own row: "adapter not plugged in" and "adapter plugged in but
    gs_usb did not bind it" are different problems with different fixes, and
    an interface-only check cannot tell them apart.
    """
    vid_pid = f"{CANABLE_VID:04x}:{CANABLE_PID:04x}"
    if IS_MAC:
        try:
            out = subprocess.run(["system_profiler", "SPUSBDataType"],
                                 capture_output=True, text=True, timeout=30).stdout
        except Exception as e:
            return record("CAN adapter present", NA, f"could not list USB: {e}")
        seen = f"0x{CANABLE_PID:04x}" in out.lower()
        return record(
            "CAN adapter present", PASS if seen else NA,
            f"CANable {vid_pid} {'found on USB' if seen else 'not found on USB'}",
            cause=None if seen else
            "macOS has no gs_usb driver, so even when the adapter is plugged "
            "in it does not appear as a CAN device — nothing here can use it")

    if shutil.which("lsusb"):
        try:
            out = subprocess.run(["lsusb"], capture_output=True, text=True,
                                 timeout=10).stdout
            if vid_pid in out.lower():
                return record("CAN adapter present", PASS,
                              f"CANable {vid_pid} on USB")
        except Exception:
            pass
    # NOT A FAIL. "The adapter is unplugged" is a true statement about the
    # world, not a fault in this machine -- nothing is broken and nothing needs
    # repairing. Reporting it red alongside a genuinely broken camera is how an
    # operator learns to skim past red, and then misses the row that mattered.
    # The row still tells them exactly what to do, which is the whole job.
    return record(
        "CAN adapter present", NA, f"no USB device {vid_pid} — adapter not plugged in",
        cause="the CANable 2.0 is not attached to this machine. Until it is, "
              "there is no CAN interface, so the arm cannot be commanded and "
              "every CAN row below is unanswerable rather than failing",
        command="plug the CANable into a USB port, then: lsusb | grep 1d50")


def check_can_interface():
    """Does can0 exist and is it UP at the right bitrate?

    The bitrate is checked, not assumed. A can0 that is up at 500000 against a
    1Mbit bus is the worst kind of failure: the interface looks healthy, and
    every motor simply never answers.
    """
    if not IS_LINUX:
        return record(
            "CAN interface up", NA, f"no SocketCAN on {platform.system()}",
            cause="SocketCAN is a Linux kernel network stack. macOS has "
                  "neither it nor a gs_usb driver, so py/openyam.py's real "
                  "path cannot run here at all — only --no-arm (dry) can")

    try:
        out = subprocess.run(["ip", "-details", "link", "show", CAN_IFACE],
                             capture_output=True, text=True, timeout=10)
    except Exception as e:
        return record("CAN interface up", FAIL, f"could not run ip: {e}",
                      cause="iproute2 missing", command="sudo apt install iproute2")

    if out.returncode != 0:
        # THE WHOLE POINT OF THIS BRANCH: distinguish "this OS cannot do
        # SocketCAN" from "this OS can, and the hardware simply is not here".
        # Those read identically in a one-line table and have opposite
        # meanings. On macOS the answer is permanent, and the arm can never
        # run from that machine. On this Linux box it is a five-second fix
        # once the adapter arrives, and telling the operator that the OS is
        # missing SocketCAN would send them installing kernels they already
        # have. Reported as N/A, not FAIL, for the same reason as the adapter
        # row above -- the interface is ABSENT, which is expected when its
        # hardware is absent, not broken.
        socketcan_ok, why_not = _socketcan_available()
        if not socketcan_ok:
            return record(
                "CAN interface up", FAIL, f"no interface {CAN_IFACE}",
                cause=f"this kernel cannot open a CAN socket at all: {why_not}. "
                      "The CAN core module is missing from this kernel build, "
                      "so no adapter would work here even once plugged in",
                command="modinfo can && grep CONFIG_CAN= /boot/config-$(uname -r)")
        gs_usb = _gs_usb_module_path()
        have_driver = " and the gs_usb driver is installed" if gs_usb else ""
        return record(
            "CAN interface up", NA,
            f"{CAN_IFACE} does not exist yet — no adapter to create it",
            cause=f"this kernel HAS SocketCAN{have_driver}, so the OS is ready. "
                  f"{CAN_IFACE} is a network interface that only appears when "
                  "gs_usb binds a plugged-in CANable. No adapter, no interface. "
                  "This is NOT the macOS case, where SocketCAN does not exist "
                  "at all and no amount of hardware would help",
            command="plug in the CANable, then run the one-shot bring-up (it "
                    "needs root, and this box prompts for a sudo password): "
                    "sudo bash tools/can-bringup.sh")

    txt = out.stdout
    up = "state UP" in txt or ",UP" in txt
    # `ip -details` prints the configured rate as `bitrate 1000000`.
    rate = None
    for tok in txt.replace("\n", " ").split():
        if rate == "":
            rate = tok
            break
        if tok == "bitrate":
            rate = ""
    try:
        rate = int(rate)
    except (TypeError, ValueError):
        rate = None

    if not up:
        return record(
            "CAN interface up", FAIL, f"{CAN_IFACE} exists but is DOWN",
            cause="never brought up, or it went bus-off and did not restart",
            command="sudo bash tools/can-bringup.sh   # re-running it is safe; "
                    "this box prompts for a sudo password")
    if rate is not None and rate != CAN_BITRATE:
        return record(
            "CAN interface up", FAIL,
            f"{CAN_IFACE} is UP at {rate}, expected {CAN_BITRATE}",
            cause="a wrong bitrate does not error — the bus just goes silent "
                  "and every motor looks dead",
            command=f"sudo ip link set {CAN_IFACE} down && "
                    "sudo bash tools/can-bringup.sh   # brings it back at "
                    f"{CAN_BITRATE}")
    return record("CAN interface up", PASS,
                  f"{CAN_IFACE} UP at {rate or CAN_BITRATE}")


def check_can_traffic(seconds=2.0):
    """Are frames arriving at all? Passive listen, nothing transmitted.

    This is deliberately separate from the per-motor check. Damiao servos are
    silent until addressed, so zero frames here is NOT proof of a dead bus —
    but a wiring or termination fault shows up here first, and finding it
    before six per-motor checks all fail is the faster diagnosis.
    """
    if not IS_LINUX:
        return record("CAN frames arriving", NA,
                      "requires SocketCAN (Linux only)")
    # A missing interface is not a bus fault. Opening the bus below would
    # raise ENODEV and the old code recorded that as FAIL "could not open
    # bus", which reads as "the CAN wiring is broken" when the true state is
    # "no adapter is attached". Ask first and answer honestly.
    if not _iface_exists():
        return record(
            "CAN frames arriving", NA,
            f"no {CAN_IFACE} to listen on",
            cause="there is no interface yet, so there is nothing to hear. "
                  "This says nothing about the bus wiring or the motors -- "
                  "it is untested, not failing")
    try:
        import can
    except ImportError:
        return record("CAN frames arriving", FAIL, "python-can not installed",
                      cause="py/openyam.py imports python-can on the real "
                            "(non-dry) path and raises SystemExit without it. "
                            "It is pinned in requirements.txt, so this means "
                            "the venv was never populated from that file",
                      command="pip install -r requirements.txt")
    bus = None
    try:
        bus = can.interface.Bus(channel=CAN_IFACE, interface="socketcan")
        n, end = 0, time.monotonic() + seconds
        while time.monotonic() < end:
            if bus.recv(timeout=0.2) is not None:
                n += 1
    except Exception as e:
        return record("CAN frames arriving", FAIL, f"could not open bus: {e}",
                      cause=f"{CAN_IFACE} is down or missing",
                      command=f"ip -details link show {CAN_IFACE}")
    finally:
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass

    if n == 0:
        return record(
            "CAN frames arriving", FAIL, f"0 frames in {seconds:.0f}s",
            cause="Damiao servos are SILENT until addressed, so this alone "
                  "does not prove a fault — but check the obvious physical "
                  "causes first: 120ohm termination at BOTH ends, motor power "
                  "actually on (the CANable is USB-powered and looks healthy "
                  "with the motor supply dead), and CANH/CANL not swapped",
            command=f"candump {CAN_IFACE}   # in another terminal, then power-cycle the motors")
    return record("CAN frames arriving", PASS, f"{n} frames in {seconds:.0f}s")


def check_motors():
    """Ask each of the six joints to answer. One row per joint.

    Sends the SAME enable frame py/openyam.py sends at construction and then
    listens for the feedback frame the servo emits in reply. It does not send
    a position, a velocity or a torque — nothing here can make the arm move.

    Per-joint rather than aggregate on purpose: "the arm does not respond" and
    "joint 4 does not respond" point at completely different faults, and the
    second one is usually a single connector.
    """
    if not IS_LINUX:
        for i in range(6):
            record(f"motor J{i}", NA, "requires SocketCAN (Linux only)")
        return
    # Six red rows for one absent adapter is the single worst thing this file
    # can print: it buries whatever else is genuinely wrong under a wall of
    # identical failures that all have the same non-motor cause. The motors
    # are UNTESTED here, and saying so once per joint is honest; calling them
    # failed is not -- nobody has asked them anything yet.
    if not _iface_exists():
        for i in range(6):
            record(f"motor J{i}", NA, f"untested — no {CAN_IFACE} to ask on")
        return
    try:
        import can
        import damiao
        import openyam
    except ImportError as e:
        for i in range(6):
            record(f"motor J{i}", FAIL, f"import failed: {e}",
                   cause="python-can missing, or py/ not importable",
                   command="pip install -r requirements.txt")
        return

    bus = None
    try:
        bus = can.interface.Bus(channel=CAN_IFACE, interface="socketcan")
    except Exception as e:
        for i in range(6):
            record(f"motor J{i}", FAIL, f"bus unavailable: {e}",
                   cause=f"{CAN_IFACE} down",
                   command="sudo bash tools/can-bringup.sh")
        return

    try:
        answered = {}
        for idx, (sid, rid) in enumerate(zip(openyam.SEND_IDS, openyam.RECV_IDS)):
            try:
                bus.send(can.Message(arbitration_id=sid,
                                     data=damiao.pack_command(damiao.CMD_ENABLE),
                                     is_extended_id=False), timeout=0.05)
            except Exception:
                # A send failure is still worth a listen: the motor may have
                # been enabled by a previous run and be chattering already.
                pass
            end = time.monotonic() + 0.30
            while time.monotonic() < end:
                msg = bus.recv(timeout=0.05)
                if msg is None:
                    continue
                if msg.arbitration_id != rid:
                    continue
                fb = damiao.unpack_feedback(bytes(msg.data),
                                            openyam.JOINT_MOTORS[idx])
                if fb is not None:
                    answered[idx] = fb
                    break

        for idx in range(6):
            fb = answered.get(idx)
            if fb is None:
                record(
                    f"motor J{idx}", FAIL,
                    f"no reply on 0x{openyam.RECV_IDS[idx]:02X}",
                    cause="this joint's CAN id is not what SEND_IDS assumes, "
                          "its connector is loose, or its internal baudrate is "
                          "not 1Mbit — Damiao motors ship at varying rates and "
                          "a silent motor is more often mis-rated than dead",
                    command=f"candump {CAN_IFACE} | grep -i "
                            f"{openyam.RECV_IDS[idx]:03x}")
            elif fb["err"]:
                record(
                    f"motor J{idx}", FAIL,
                    f"answered with error: {damiao.describe_error(fb['err'])}",
                    cause="the motor is alive and reporting a fault of its own",
                    command="power-cycle the motor supply, then re-run this test")
            else:
                # q is reported in radians. It is printed because a joint
                # sitting at an unexpected angle is the cheapest possible
                # early warning that HOME_Q or the sign convention is wrong —
                # both are still CALIBRATE placeholders in py/openyam.py.
                record(f"motor J{idx}", PASS,
                       f"q={fb['q']:+.3f} rad  tau={fb['tau']:+.2f} N*m")
    finally:
        try:
            bus.shutdown()
        except Exception:
            pass


# ------------------------------------------------------------ dependencies ---
def check_imports():
    """The Python packages a live run needs, each named separately.

    Cheap, and it removes an entire class of confusion: a missing mediapipe
    presents at runtime as a camera that produces no pose, which reads exactly
    like a camera fault.
    """
    for mod, why, fix in (
        ("cv2", "camera capture and the debug window", "pip install -r requirements.txt"),
        ("numpy", "every array in the pipeline", "pip install -r requirements.txt"),
        ("mediapipe", "pose landmarks", "pip install mediapipe==1.0.0"),
        ("websockets", "the projector link", "pip install -r requirements.txt"),
        ("serial", "the RoArm serial path (--arm roarm)", "pip install -r requirements.txt"),
        ("can", "the OpenYAM CAN path (--arm openyam)", "pip install -r requirements.txt"),
    ):
        try:
            m = __import__(mod)
            v = getattr(m, "__version__", "")
            record(f"import {mod}", PASS, f"{v or 'ok'} — {why}")
        except ImportError:
            # python-can INSTALLS fine on macOS and is pinned in
            # requirements.txt; only the socketcan interface it opens is
            # Linux-only. So its absence is a real gap in this venv, not a
            # platform limit — but it blocks nothing on a Mac, where the arm
            # can only run dry anyway. Report it honestly as N/A with the
            # true reason rather than implying macOS cannot have it.
            if mod == "can" and not IS_LINUX:
                record(f"import {mod}", NA,
                       "not installed in this venv (pinned in requirements.txt)",
                       cause="python-can installs fine here; only the "
                             "socketcan interface it opens is Linux-only. "
                             "Nothing on a Mac needs it, since the arm can "
                             "only run dry (--no-arm) without SocketCAN",
                       command="pip install -r requirements.txt")
            else:
                record(f"import {mod}", FAIL, f"missing — needed for {why}",
                       cause="not installed in this interpreter", command=fix)


def check_mediapipe_delegate():
    """Which MediaPipe delegate will actually initialise here?

    Worth a row of its own because the GPU delegate does NOT fall back to CPU:
    it fails at init, and on a Pi (no OpenGL ES 3.1) that is the single most
    likely first-boot crash. Finding it here costs two seconds; finding it on
    stage costs the demo.
    """
    try:
        import mediapipe  # noqa: F401
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision as mpv
        from mediapipe.tasks.python import BaseOptions
    except Exception as e:
        return record("mediapipe delegate", FAIL, f"mediapipe unusable: {e}",
                      cause="not installed or wrong version",
                      command="pip install mediapipe==1.0.0")

    model = os.path.join(REPO, "models", "pose_landmarker_full.task")
    if not os.path.isfile(model):
        return record("mediapipe delegate", FAIL, "pose model missing",
                      cause="models/*.task are fetched, not committed",
                      command="./vendor.sh")

    working = []
    # WHY the GPU delegate failed, in the delegate's own words. It matters
    # more than the fact: on this box the reason is "GPU processing is
    # disabled in build flags", meaning the mediapipe WHEEL was built without
    # GPU calculators. No driver, no EGL library and no amount of graphics
    # troubleshooting can change that -- it is a property of the package. The
    # row used to assert "needs OpenGL ES 3.1+", which is the Pi's reason and
    # is simply wrong on a machine with an NVIDIA GPU sitting idle. An
    # operator handed that sentence goes hunting drivers for an hour and
    # finds nothing, which is exactly the confident-wrong-answer failure this
    # file exists to avoid. So capture the real text and print it.
    gpu_reason = ""
    for name in ("GPU", "CPU"):
        try:
            d = (BaseOptions.Delegate.GPU if name == "GPU"
                 else BaseOptions.Delegate.CPU)
            lm = mpv.PoseLandmarker.create_from_options(
                mpv.PoseLandmarkerOptions(
                    base_options=mpp.BaseOptions(model_asset_path=model,
                                                 delegate=d),
                    running_mode=mpv.RunningMode.VIDEO, num_poses=1))
            lm.close()
            working.append(name)
        except Exception as e:
            # Only a catchable init failure lands here. py/vision.py's header
            # documents that some MediaPipe failures are SIGABRT and kill the
            # process outright — if this file dies mid-check, that is itself
            # the answer, and the row simply never prints.
            if name == "GPU":
                # MediaPipe puts the real reason in the exception STRING, not
                # the type: every init failure is NotImplementedError, and only
                # the text distinguishes "this wheel has no GPU calculators"
                # from "this machine has no usable GL context". Keep the last
                # line, which is the specific complaint; the first is always
                # the generic "ValidatedGraphConfig Initialization failed."
                lines = [ln.strip() for ln in str(e).splitlines() if ln.strip()]
                gpu_reason = lines[-1] if lines else type(e).__name__

    if not working:
        return record("mediapipe delegate", FAIL, "neither GPU nor CPU initialised",
                      cause="wrong mediapipe version for this platform",
                      command="pip install mediapipe==1.0.0")
    if "GPU" not in working:
        # "disabled in build flags" means the WHEEL was compiled without GPU
        # calculators. That is not fixable by drivers, and saying so stops an
        # operator burning an hour on graphics troubleshooting for a package
        # property. Any other text is a real runtime/GL problem, so do not
        # claim a build-flag cause we have not seen.
        if "build flags" in gpu_reason.lower():
            cause = ("this mediapipe wheel was built WITHOUT GPU support "
                     f"({gpu_reason}). No driver, GL library or graphics "
                     "setting can change that — it is a property of the "
                     "installed package, so CPU is the only delegate here and "
                     "there is nothing to fix. Run with --delegate CPU; the "
                     "GPU delegate does NOT fall back and crashes at startup")
        else:
            why = gpu_reason or "no reason reported"
            cause = (f"the GPU delegate would not initialise ({why}). It does "
                     "NOT fall back — run with --delegate CPU or it crashes "
                     "at startup")
        return record(
            "mediapipe delegate", PASS, "CPU only (GPU delegate unavailable)",
            cause=cause)
    return record("mediapipe delegate", PASS, "+".join(working))


# ------------------------------------------------------------------ report ---
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--skip-motors", action="store_true",
                    help="skip the per-joint CAN probe")
    # Set by the re-exec below so the second run cannot bounce again. A loop
    # here would be invisible: execv replaces the process, so a bad condition
    # spins forever with no traceback and no output.
    ap.add_argument("--no-reexec", action="store_true",
                    help=argparse.SUPPRESS)
    args = ap.parse_args()

    # RE-EXEC INTO THE VENV IF THIS INTERPRETER LACKS cv2.
    #
    # THIS IS NOT A CONVENIENCE. This project has two interpreters on purpose
    # (./venv has cv2 + mediapipe + pyserial, system python3 has playwright),
    # and a selftest is the one tool that MUST NOT report which libraries the
    # wrong one is missing. Run under system python3 it printed
    # "camera RGB FAIL -- the venv is missing opencv-contrib-python" on a
    # machine where the camera was delivering 30fps with pose detection. Six
    # false failures, each with a confident remedy that would have had an
    # operator reinstalling working packages mid-demo.
    #
    # A selftest that lies about hardware is worse than no selftest, so this
    # corrects itself rather than warning and continuing.
    if "--no-reexec" not in sys.argv:
        try:
            import cv2  # noqa: F401
        except ImportError:
            venv_py = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "venv", "bin", "python")
            venv_py = os.path.normpath(venv_py)
            if os.path.exists(venv_py) and venv_py != sys.executable:
                if not args.json:
                    print(f"[selftest] no cv2 here; re-running under {venv_py}")
                os.execv(venv_py, [venv_py, os.path.abspath(__file__),
                                   "--no-reexec"] + sys.argv[1:])
            # No venv to fall back to. Say so plainly instead of reporting
            # every cv2-dependent check as a hardware failure.
            if not args.json:
                print("[selftest] WARNING: this interpreter has no cv2 and no "
                      "./venv exists.\n"
                      "           Camera results below will be WRONG. Fix the "
                      "venv first:\n"
                      "           python3.12 -m venv venv && "
                      "./venv/bin/pip install -r requirements.txt\n")

    if not args.json:
        print(f"\nWHEELGENTIC HARDWARE SELFTEST — {PLATFORM_NAME}, Python "
              f"{sys.version_info.major}.{sys.version_info.minor} "
              f"({sys.executable})")
        print("Nothing here commands the arm to move.\n")

    check_imports()
    check_mediapipe_delegate()
    check_camera_rgb()
    check_camera_depth()
    # Before the CAN rows: on this Mac the RoArm is the arm that can actually
    # be driven, and CAN is the one that cannot (no gs_usb driver). Putting
    # the answerable rows first keeps the top of the table useful.
    check_roarm_ports()
    check_roarm_assignment()
    check_can_adapter()
    check_can_interface()
    check_can_traffic()
    if not args.skip_motors:
        check_motors()

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        w = max(len(r["name"]) for r in results)
        colour = {PASS: "\033[32m", FAIL: "\033[31m", NA: "\033[36m"}
        for r in results:
            print(f"  {colour[r['status']]}{r['status']:<4}\033[0m  "
                  f"{r['name']:<{w}}  {r['detail']}")
        # Causes and commands print BELOW the table, not inside it. A table
        # with paragraphs in it is unreadable at a glance, and the glance is
        # what the table is for.
        notes = [r for r in results if r["cause"] or r["command"]]
        if notes:
            print("\n" + "-" * 60)
            for r in notes:
                print(f"\n  {r['status']}  {r['name']}")
                if r["cause"]:
                    print(f"    why:  {r['cause']}")
                if r["command"]:
                    print(f"    try:  {r['command']}")

        n_pass = sum(r["status"] == PASS for r in results)
        n_fail = sum(r["status"] == FAIL for r in results)
        n_na = sum(r["status"] == NA for r in results)
        print(f"\n  {n_pass} PASS  {n_fail} FAIL  {n_na} N/A\n")

    # Exit code counts FAIL only. N/A must never fail the run or the script is
    # useless on the Mac, where half of it legitimately does not apply.
    return 1 if any(r["status"] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
