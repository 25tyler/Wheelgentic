"""devconsole/viewer3d.py -- a run's 3D view, in Rerun's own web viewer.

Every job the console starts saves the operator view to RUN/view.rrd. This
serves one of those files with `rerun --serve-web`, and the Body tab shows the
web viewer in a frame. Nothing is rebuilt: it is the same viewer the operator
uses, reading the same recording.

ONE AT A TIME, ON PORTS OF ITS OWN
----------------------------------
A recording can be tens of megabytes and the viewer holds it in memory, so
there is one sidecar, replaced when another run is opened and stopped with the
console. It listens on 127.0.0.1 only, on 9091 (web) and 9877 (gRPC), because
9090 and 9876 are Rerun's defaults and an operator's own viewer may hold them.

The sidecar reads the file once when it starts. A run still being written
shows what was on disk at that moment; opening it again rereads it.
"""
import atexit
import importlib.util
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request

WEB_PORT = 9091
GRPC_PORT = 9877
MEMORY_LIMIT = "1GiB"
START_TIMEOUT_S = 15.0
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _rerun_exe():
    """The real viewer binary, not pip's launcher for it.

    `rerun.exe` in Scripts is a shim that starts the binary inside the
    rerun_sdk package as a child. Stopping the shim orphaned that child, which
    went on serving after "Close it". The binary is found without importing
    anything.
    """
    spec = importlib.util.find_spec("rerun_cli")
    if spec is not None and spec.origin:
        exe = os.path.join(os.path.dirname(spec.origin),
                           "rerun.exe" if os.name == "nt" else "rerun")
        if os.path.exists(exe):
            return exe
    return shutil.which("rerun")


def _answers(port, timeout=0.5):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout):
            return True
    except Exception:                                         # noqa: BLE001
        return False


class Viewer3D:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.run = None
        self.path = None
        self.started = None
        atexit.register(self.close)

    def url(self):
        proxy = f"rerun+http://127.0.0.1:{GRPC_PORT}/proxy"
        return (f"http://127.0.0.1:{WEB_PORT}/?url="
                f"{urllib.parse.quote(proxy, safe='')}")

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def open(self, rid, path, log_path):
        """Serve `path` for run `rid`. -> (url or None, message)."""
        if not os.path.exists(path):
            return None, ("this run saved no view.rrd; only runs that write the "
                          "operator view to a file can be opened here")
        exe = _rerun_exe()
        if exe is None:
            return None, "the rerun executable was not found next to this Python"
        with self.lock:
            self._stop()
            if _answers(WEB_PORT):
                return None, (f"port {WEB_PORT} is already serving something that "
                              f"is not this console's viewer; close it first")
            cmd = [exe, "--serve-web", "--bind", "127.0.0.1",
                   "--web-viewer-port", str(WEB_PORT), "--port", str(GRPC_PORT),
                   "--server-memory-limit", MEMORY_LIMIT, path]
            log = open(log_path, "ab")
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=subprocess.STDOUT,
                                             creationflags=NO_WINDOW)
            finally:
                log.close()
            self.run, self.path, self.started = rid, path, time.time()
            deadline = time.time() + START_TIMEOUT_S
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    self.proc = None
                    return None, f"rerun exited at once; see {log_path}"
                if _answers(WEB_PORT):
                    mb = os.path.getsize(path) / 2**20
                    return self.url(), (f"serving {os.path.basename(os.path.dirname(path))}"
                                        f"/view.rrd ({mb:.0f} MB); it streams in "
                                        f"over a few seconds")
                time.sleep(0.2)
            self._stop()
            return None, f"rerun did not answer within {START_TIMEOUT_S:.0f}s"

    def _stop(self):
        """Stop the sidecar and anything it started."""
        if self._alive():
            import psutil
            try:
                root = psutil.Process(self.proc.pid)
                procs = root.children(recursive=True) + [root]
            except psutil.Error:
                procs = []
            for p in procs:
                try:
                    p.terminate()
                except psutil.Error:
                    pass
            _gone, alive = psutil.wait_procs(procs, timeout=5)
            for p in alive:
                try:
                    p.kill()
                except psutil.Error:
                    pass
        self.proc = None
        self.run = self.path = self.started = None

    def close(self):
        with self.lock:
            self._stop()

    def status(self):
        with self.lock:
            if not self._alive():
                return None
            return {"run": self.run, "pid": self.proc.pid, "url": self.url(),
                    "since": self.started}


def reap_orphans(runs_dir):
    """Stop a sidecar a previous console left running. -> how many.

    atexit does not run when a console is killed outright, and the orphan
    would hold the port and refuse the next "Open". Only a rerun process on
    this module's web port serving a file from this console's runs directory
    is touched; an operator's own viewer never matches both.
    """
    import psutil
    root = os.path.normcase(os.path.abspath(runs_dir))
    n = 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if not (p.info["name"] or "").lower().startswith("rerun"):
                continue
            cmd = p.info["cmdline"] or []
            ours = str(WEB_PORT) in cmd and any(
                os.path.normcase(os.path.abspath(c)).startswith(root)
                for c in cmd if c.endswith(".rrd"))
            if ours:
                p.terminate()
                n += 1
        except psutil.Error:
            continue
    return n


VIEWER = None


def viewer():
    global VIEWER
    if VIEWER is None:
        VIEWER = Viewer3D()
    return VIEWER
