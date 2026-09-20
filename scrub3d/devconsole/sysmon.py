"""devconsole/sysmon.py -- the processes, the ports, the GPU and the camera.

WHAT COUNTS AS OURS
-------------------
A python process whose script lives in scrub3d/, and Rerun's viewer. Matching
on "scrub3d appears in the command line" is not enough: every shell that ever
cd'd into the worktree matches that, and the list fills with wrapper shells.

THE CONSOLE NEVER TOUCHES THE HARDWARE ITSELF
---------------------------------------------
The camera and torch checks run in short subprocesses with timeouts. Importing
torch here would initialise CUDA in the console's own process, and enumerating
the RealSense while a job is streaming from it risks disturbing the job -- so
that check is skipped entirely while a camera job runs.

Windows' driver model reports per-process GPU memory as [N/A]; the page says
so, and each job records its own torch peak instead.
"""
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRUB3D = os.path.dirname(HERE)
WORKTREE = os.path.dirname(SCRUB3D)
# The live demo is py/scrubbot.py, which is NOT under scrub3d/. Without this
# root the Processes tab showed the console itself and nothing else while the
# demo was running two feet away -- measured on the GB10, 5643 bytes of table
# with "scrubbot" absent from all of it. Watching the demo is the reason an
# operator opens the console during a run, so py/ is a root here, same as
# probes.py already does for py/arm.py.
PY_DIR = os.path.join(WORKTREE, "py")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

PROC_EVERY_S = 3.0
PORTS_EVERY_S = 6.0
GPU_EVERY_S = 5.0
CAMERA_EVERY_S = 5.0
TORCH_EVERY_S = 600.0

_CAMERA_SNIPPET = (
    "import json\n"
    "try:\n"
    "    import pyrealsense2 as rs\n"
    "    ctx = rs.context()\n"
    "    out = [{'name': d.get_info(rs.camera_info.name),\n"
    "            'serial': d.get_info(rs.camera_info.serial_number),\n"
    "            'firmware': d.get_info(rs.camera_info.firmware_version)}\n"
    "           for d in ctx.devices]\n"
    "    print(json.dumps({'ok': True, 'devices': out}))\n"
    "except Exception as e:\n"
    "    print(json.dumps({'ok': False, 'error': repr(e)}))\n")

_TORCH_SNIPPET = (
    "import json\n"
    "try:\n"
    "    import torch\n"
    "    c = torch.cuda.is_available()\n"
    "    print(json.dumps({'ok': True, 'torch': torch.__version__, 'cuda': c,\n"
    "        'device': torch.cuda.get_device_name(0) if c else None,\n"
    "        'cuda_version': torch.version.cuda}))\n"
    "except Exception as e:\n"
    "    print(json.dumps({'ok': False, 'error': repr(e)}))\n")


def _run_snippet(code, timeout):
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=timeout, creationflags=NO_WINDOW)
        line = (r.stdout or "").strip().splitlines()
        return json.loads(line[-1]) if line else {"ok": False, "error": r.stderr[-300:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout}s"}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "error": repr(exc)}


def _script_in_scrub3d(cmdline, cwd):
    """-> "scrub3d/main.py" / "py/scrubbot.py" for a watched script, else None.

    Both roots return a path relative to the WORKTREE, so the caller and the
    Processes tab cannot tell which root matched -- they just get a label.
    """
    roots = [os.path.normcase(SCRUB3D), os.path.normcase(PY_DIR)]
    for arg in cmdline[1:]:
        if not arg.lower().endswith(".py"):
            continue
        path = arg if os.path.isabs(arg) else os.path.join(cwd or "", arg)
        path = os.path.normcase(os.path.abspath(path))
        if any(path.startswith(r + os.sep) for r in roots):
            return os.path.relpath(path, os.path.normcase(WORKTREE))
    return None


class SysMon:
    def __init__(self, camera_busy=lambda: False):
        self.lock = threading.Lock()
        self.camera_busy = camera_busy
        self.procs = []
        self.ports = {}
        self.gpu = None
        self.gpu_apps = []
        self.camera = None
        self.camera_t = None
        self.torch = None
        self._pcache = {}
        self._next = {}
        self._thread = threading.Thread(target=self._loop, name="dc-sysmon",
                                        daemon=True)
        self._torch_req = threading.Event()

    def start(self):
        self._thread.start()

    def request_torch(self):
        self._torch_req.set()

    def _due(self, key, every):
        now = time.time()
        if now >= self._next.get(key, 0):
            self._next[key] = now + every
            return True
        return False

    def _loop(self):
        while True:
            try:
                if self._due("procs", PROC_EVERY_S):
                    self._poll_procs()
                if self._due("ports", PORTS_EVERY_S):
                    self._poll_ports()
                if self._due("gpu", GPU_EVERY_S):
                    self._poll_gpu()
                if self._due("camera", CAMERA_EVERY_S):
                    self._poll_camera()
                if self._torch_req.is_set() or self._due("torch", TORCH_EVERY_S):
                    self._torch_req.clear()
                    t = _run_snippet(_TORCH_SNIPPET, 120)
                    t["checked"] = time.time()
                    with self.lock:
                        self.torch = t
            except Exception as exc:                          # noqa: BLE001
                print(f"[devconsole] sysmon: {exc!r}", flush=True)
            time.sleep(0.5)

    def _poll_procs(self):
        import psutil
        out, seen = [], set()
        me = os.getpid()
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                name = (p.info["name"] or "").lower()
                cmd = p.info["cmdline"] or []
                kind = None
                script = None
                if name.startswith("rerun"):
                    kind = "rerun viewer"
                elif name.startswith("python"):
                    try:
                        cwd = p.cwd()
                    except psutil.Error:
                        cwd = None
                    script = _script_in_scrub3d(cmd, cwd)
                    if script:
                        # "demo" is its own label, not "scrub3d". An operator
                        # scanning this table during a run is looking for the
                        # demo specifically, and calling py/scrubbot.py a
                        # scrub3d process would hide it among the backend jobs.
                        kind = ("this console" if p.pid == me else
                                "demo" if script.startswith("py" + os.sep)
                                else "scrub3d")
                if kind is None:
                    continue
                proc = self._pcache.get(p.pid)
                if proc is None or proc.create_time() != p.info["create_time"]:
                    proc = psutil.Process(p.pid)
                    proc.cpu_percent(None)
                    self._pcache[p.pid] = proc
                seen.add(p.pid)
                with proc.oneshot():
                    rss = proc.memory_info().rss / 2**20
                    cpu = proc.cpu_percent(None)
                    threads = proc.num_threads()
                args = " ".join(cmd[1:])
                out.append({"pid": p.pid, "kind": kind, "name": p.info["name"],
                            "script": script, "args": args[:300],
                            "cpu": round(cpu, 1), "rss_mb": round(rss),
                            "threads": threads,
                            "uptime_s": round(time.time() - p.info["create_time"]),
                            "create_time": p.info["create_time"]})
            except psutil.Error:
                continue
        for pid in list(self._pcache):
            if pid not in seen:
                self._pcache.pop(pid, None)
        with self.lock:
            self.procs = sorted(out, key=lambda r: (r["kind"] != "this console",
                                                    r["kind"], r["pid"]))

    def _poll_ports(self):
        import psutil
        ports = {}
        try:
            for c in psutil.net_connections(kind="tcp"):
                if c.status == psutil.CONN_LISTEN and c.pid:
                    ports.setdefault(c.pid, set()).add(c.laddr.port)
        except psutil.Error:
            return
        with self.lock:
            self.ports = {k: sorted(v) for k, v in ports.items()}

    def _poll_gpu(self):
        q = ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,"
             "memory.total,temperature.gpu", "--format=csv,noheader,nounits"]
        try:
            r = subprocess.run(q, capture_output=True, text=True, timeout=8,
                               creationflags=NO_WINDOW)
            parts = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
            gpu = {"name": parts[0], "util": float(parts[1]),
                   "mem_used": float(parts[2]), "mem_total": float(parts[3]),
                   "temp": float(parts[4]), "t": time.time()}
        except Exception as exc:                              # noqa: BLE001
            gpu = {"error": repr(exc), "t": time.time()}
        apps = []
        try:
            r = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,"
                                "used_memory", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=8,
                               creationflags=NO_WINDOW)
            for line in r.stdout.strip().splitlines():
                bits = [b.strip() for b in line.split(",")]
                if len(bits) >= 3:
                    apps.append({"pid": bits[0], "name": os.path.basename(bits[1]),
                                 "memory": bits[2]})
        except Exception:                                     # noqa: BLE001
            pass
        with self.lock:
            self.gpu = gpu
            self.gpu_apps = apps

    def _poll_camera(self):
        if self.camera_busy():
            with self.lock:
                if self.camera is not None:
                    self.camera = dict(self.camera, skipped="a camera job is running")
            return
        cam = _run_snippet(_CAMERA_SNIPPET, 20)
        cam["t"] = time.time()
        with self.lock:
            self.camera = cam
            self.camera_t = time.time()

    def camera_present(self):
        with self.lock:
            if not self.camera or not self.camera.get("ok"):
                return None if not self.camera else False
            return bool(self.camera.get("devices"))

    def snapshot(self):
        with self.lock:
            procs = [dict(p, ports=self.ports.get(p["pid"], [])) for p in self.procs]
            return {"procs": procs, "gpu": self.gpu, "gpu_apps": list(self.gpu_apps),
                    "camera": self.camera, "torch": self.torch}


SYSMON = None


def sysmon(camera_busy=None):
    global SYSMON
    if SYSMON is None:
        SYSMON = SysMon(camera_busy or (lambda: False))
    return SYSMON
