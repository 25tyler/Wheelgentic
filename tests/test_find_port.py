"""tests/test_find_port.py — the port discovery the DEMO uses, and no test did.

`run.sh:124` launches `py/scrubbot.py "$@"` with no `--port`. `config.json`
ships `"serial_port": null`, so `scrubbot.py:1146`'s default is None,
`:1154` calls `Arm(port=None)`, and `py/arm.py:118` takes `port or
find_port()`. Nineteen test sites construct `A.Arm(port=fake.port)` with an
explicit pty, so the `or find_port()` half NEVER evaluated. Zero coverage on
the branch the live demo actually takes -- recorded in 8ef60e2.

This opens no serial port and needs no hardware: `glob.glob` is swapped for a
dict lookup, so the three patterns can be made to match or not match at will.

WHAT IS PINNED, and why each matters on demo day:
  - all THREE patterns are searched. The card tells the operator to replug USB;
    a CP210x board, a SiLabs SLAB board and a WCH board enumerate under
    different names, and dropping one silently fails to find a plugged-in arm.
  - the match is SORTED-first. With two boards attached the choice must be
    deterministic, or a rehearsal and the demo pick different arms.
  - nothing found RAISES SystemExit, and the message names the driver, where
    to approve it, and that a reboot is needed. That text is the whole of what
    the operator gets; `find_port` has no other output.

PLANT-VERIFIED, 6 plants, each caught by the check that owns the property:
only-usbserial, drop-wch, sorted-but-last, unsorted-first, unsorted-last, and
return-None-instead-of-raising. The fixture is four ports whose sorted-first,
sorted-last, raw-first and raw-last are FOUR DISTINCT values (A1/Z9/M5/Q7), so
no pair of faults can cancel -- an earlier two-element fixture let "return the
last match" and "skip the sort" produce the same answer, and that plant passed
while testing nothing.
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchdog; watchdog.arm(30)   # pure in-process work; 30s is generous
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py"))

import arm as A

fails = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: fails.append(label)

USB  = "/dev/cu.usbserial-*"
SLAB = "/dev/cu.SLAB_USBtoUART*"
WCH  = "/dev/cu.wchusb*"

RAISED = object()
def with_ports(mapping):
    """Call find_port() with glob answering from `mapping`. Never lets a
    SystemExit escape: EVERY call must be guarded, not just the empty one --
    an unguarded call killed the first version of this harness outright."""
    real = A.glob.glob
    A.glob.glob = lambda pat: mapping.get(pat, [])
    try:
        return A.find_port()
    except SystemExit as e:
        return (RAISED, str(e))
    finally:
        A.glob.glob = real

print("\n=== find_port: the branch run.sh takes with no --port ===")

check("a usbserial port is found",
      with_ports({USB: ["/dev/cu.usbserial-A50285BI"]}) == "/dev/cu.usbserial-A50285BI")
check("the SLAB pattern is searched too",
      with_ports({SLAB: ["/dev/cu.SLAB_USBtoUART"]}) == "/dev/cu.SLAB_USBtoUART")
check("the wch pattern is searched too",
      with_ports({WCH: ["/dev/cu.wchusbserial1420"]}) == "/dev/cu.wchusbserial1420")
check("usbserial is preferred when several kinds are attached",
      with_ports({USB: ["/dev/cu.usbserial-B"],
                  SLAB: ["/dev/cu.SLAB_USBtoUART"]}) == "/dev/cu.usbserial-B")
# Four ports whose sorted-first / sorted-last / raw-first / raw-last all differ.
check("two boards pick the SORTED-first one, deterministically",
      with_ports({USB: ["/dev/cu.usbserial-M5", "/dev/cu.usbserial-Z9",
                        "/dev/cu.usbserial-A1", "/dev/cu.usbserial-Q7"]})
      == "/dev/cu.usbserial-A1")

print("\n=== nothing plugged in: the message IS the whole user experience ===")
_r = with_ports({})
_raised = isinstance(_r, tuple) and _r and _r[0] is RAISED
_msg = _r[1] if _raised else ""
check("no port raises SystemExit rather than returning None", _raised,
      "returning None would hand `serial.Serial(None, ...)` a null port")
check("the message names the driver to install", _raised and "CP210x" in _msg)
check("and where to approve the extension",
      _raised and ("Privacy" in _msg or "Security" in _msg))
check("and that a REBOOT is required", _raised and "REBOOT" in _msg.upper())

print("\n" + "=" * 58)
if fails:
    print(f"  *** {len(fails)} FAILED: {fails}")
    sys.exit(1)
print("  FIND_PORT CHECKS PASSED")
print("  NOTE: this proves DISCOVERY only. Opening the port is arm-protocol's job.")
