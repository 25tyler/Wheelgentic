"""tests/watchdog.py — a watchdog that actually fires.

A daemon thread doing `time.sleep(N); os._exit(3)` is the obvious approach and
it is NOT reliable: I found a tests/test_arm_protocol.py process that had been
running for THREE HOURS AND TWENTY-ONE MINUTES with a 90-second daemon
watchdog installed. A blocked main thread, a stopped process, or an
interpreter wedged in a C call can all starve it.

SIGALRM is delivered by the OS, so no other Python thread can starve it.

THE STARVATION THIS DOCSTRING USED TO CLAIM DID NOT SURVIVE MEASUREMENT.
The old text said CPython runs the Python handler only at a bytecode boundary,
so a main thread parked in a C call never reaches one and the alarm never
fires. Tested on 3.14.6 with a 2s alarm, against a pure-Python control that
dies at 2.0s exit 3: threading.Lock.acquire() on a held lock, os.read on an
empty pty, select.select([],[],[]), time.sleep(60), os.waitpid on a live
child, and pyserial 3.5's read(1) and readline() on a timeout=None pty -- the
exact shape test_arm_protocol drives -- ALL fire at 2.0s. Eight of eight.
PEP 475 is why: since 3.5 an interrupted syscall runs the handler and THEN
retries, so these calls are interruptible by construction.

What remains true is the observation, not the explanation: two runs of
test_arm_protocol.py wedged for 4+ minutes printing no WATCHDOG line. Why is
OPEN. Do not re-derive the starvation story -- it is measured false here. A
faulthandler.dump_traceback_later backstop was written for it and deliberately
NOT landed, because a guard whose triggering condition cannot be reproduced
cannot be plant-verified.
"""
import os, signal, sys

# Set by disarm(); read by the fallback thread. See arm()'s except branch.
_cancelled = False


def arm(seconds=90, label=None):
    """Hard-kill this process after `seconds`. Idempotent; call once at import.

    Uses SIGALRM (OS-delivered) rather than a daemon thread, and os._exit so
    no atexit handler can block on the same fd that hung us.
    """
    name = label or os.path.basename(sys.argv[0] or "test")

    def _boom(signum, frame):
        sys.stderr.write(
            f"\n*** WATCHDOG: {name} exceeded {seconds}s — aborting ***\n")
        sys.stderr.flush()
        os._exit(3)

    try:
        signal.signal(signal.SIGALRM, _boom)
        signal.alarm(int(seconds))
    except (ValueError, AttributeError):
        # Not the main thread, or a platform without SIGALRM. Fall back rather
        # than leaving the caller with no watchdog at all.
        #
        # THE FALLBACK MUST HONOUR disarm(). `disarm()` is signal.alarm(0),
        # which cannot reach a thread, so before this flag the two paths
        # disagreed: armed on the main thread, disarm() held (measured, rc=0
        # after 4s); armed from a thread, disarm() was ignored and the process
        # died anyway (measured, rc=3). No caller reaches that combination
        # today -- all 24 arm() sites are module-level imports on the main
        # thread and disarm() has no callers -- but a helper or fixture that
        # arms inside a thread would get a process killed mid-suite with no
        # explanation, which is the shape of the unexplained wedge in the
        # docstring above.
        global _cancelled
        _cancelled = False
        import threading, time as _t

        def _wait_then_boom():
            _t.sleep(seconds)
            if not _cancelled:
                _boom(None, None)

        threading.Thread(target=_wait_then_boom, daemon=True).start()


def disarm():
    # BOTH PATHS. signal.alarm(0) cancels the SIGALRM arm; the flag cancels the
    # daemon-thread fallback, which alarm(0) cannot reach.
    global _cancelled
    _cancelled = True
    try:
        signal.alarm(0)
    except (ValueError, AttributeError):
        pass
