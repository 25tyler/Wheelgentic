"""tests/serve.py — make sure http://localhost:8000/ is answering.

Browser tests kept failing with ERR_CONNECTION_REFUSED because an earlier
test's cleanup killed the shared server. Rather than ordering the suite around
that, every browser test just calls ensure() and starts one if needed.
"""
import atexit, os, subprocess, time, urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_proc = None


def up(timeout=1.5):
    try:
        return urllib.request.urlopen("http://localhost:8000/",
                                      timeout=timeout).status == 200
    except Exception:
        return False


def ensure():
    """Start a server on :8000 if nothing is answering. Idempotent."""
    global _proc
    if up():
        return False
    _proc = subprocess.Popen(
        ["python3", "-m", "http.server", "8000", "-d",
         os.path.join(ROOT, "web")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atexit.register(_stop)
    for _ in range(24):
        if up(0.5):
            return True
        time.sleep(0.25)
    raise SystemExit("could not start a web server on :8000")


def _stop():
    if _proc and _proc.poll() is None:
        _proc.terminate()
        try: _proc.wait(2)
        except Exception: _proc.kill()
