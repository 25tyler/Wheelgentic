"""devconsole/live3d.py -- the Overview's 3D: whatever is running, as it runs.

viewer3d.py serves one finished recording, read once. This keeps a second
Rerun server up for as long as the console runs and streams every running
job's recording into it while the job is still writing it: the arms, the
person, the territories and the paint, live.

A RUNNING JOB IS NEVER TOUCHED
------------------------------
Every job the console starts saves the operator view to a file
(`--save RUN/view.rrd`). A follower thread here reads that file as it grows,
every 100 ms, and pipes the new bytes into a forwarder the console owns:
`rerun --headless --bind 127.0.0.1 --port <server> -`. Finding a server on that
port, it sends what it reads on stdin there and exits at the end of input. The
job gains no code, no thread, no process and no environment variable.

Why the job does not stream to the server itself: in Rerun 0.37 a job that
streams to a server blocks when the server stops reading -- first its file
sink, then rr.log itself -- and a server stops reading whenever a browser tab
falls behind. A forwarder that blocks for STALL_S here is killed instead, the
follower is marked stalled, and the job never notices.

Measured before this was built, on spare ports:
  - a recording being written grows on disk about every 200 ms, Windows too;
  - the bytes a follower forwards hash to the finished file's hash;
  - a hidden browser tab, and a server suspended for 20 s, cost the job
    nothing;
  - a frozen forwarder is killed after 5 s and the job runs on;
  - with nothing on the port, the same command becomes a viewer itself: a
    window, or with --headless an invisible one serving that port. So the
    server is checked first, and a server that dies takes its forwarders
    with it. (`--connect` is not a forwarder: it opens a viewer app that
    never exits.)

WHAT THE PAGE SHOWS
-------------------
One run at a time. A server hands a page that opens everything it holds, so
when a run's recording appears and nothing else is streaming, the server is
started again empty and the page gets that run rather than a backlog of every
run before it. Runs that overlap share the server, and the viewer shows the
newest. When the console starts, the newest run that ended cleanly is sent
once, so the view is never empty. The layout (overview.rbl) is written once
per Rerun version: the scene large, progress and tracking beside it.

Ports 9092 (web) and 9878 (gRPC), next to viewer3d's 9091/9877 and clear of
Rerun's defaults, which an operator's own viewer may hold.
"""
import atexit
import hashlib
import importlib.metadata
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

from devconsole import viewer3d as V3D

HERE = os.path.dirname(os.path.abspath(__file__))
SCRUB3D = os.path.dirname(HERE)
WORKTREE = os.path.dirname(SCRUB3D)
RUNS = os.path.join(HERE, "runs")

WEB_PORT = 9092
GRPC_PORT = 9878
MEMORY_LIMIT = "512MiB"
START_TIMEOUT_S = 15.0
SUPERVISE_S = 1.0
READ_EVERY_S = 0.1
CHUNK_BYTES = 8 << 20          # at most this much per write, so a stall shows
STALL_S = 5.0                  # a write blocked this long kills the forwarder
QUIET_S = 1.0                  # after the run's process is gone
CLOSE_WAIT_S = 10.0
RESTART_AFTER_S = 10.0
MAX_RESTARTS = 5
NO_WINDOW = V3D.NO_WINDOW

# Runs worth showing when nothing is running: the live loop and the rig
# simulation. A self-test's recording is not what the page is for.
SEED_SCRIPTS = ("main.py", "viz.py")

BLUEPRINT = r'''
import sys
import rerun.blueprint as rrb
rrb.Blueprint(
    rrb.Horizontal(
        rrb.Spatial3DView(
            origin="world", name="The rig and the person",
            # The capsules are what the governor checks. Drawn, they hide the
            # arms they stand for. The room's point cloud buries the person
            # and pulls the camera out to the walls. The Body tab's viewer
            # still shows both.
            contents=["+ $origin/**", "- /world/obstacles"]
                     + [f"- /world/arm_{a}/capsules" for a in range(4)],
            # From in front of the person and to their left, above the arms.
            eye_controls=rrb.EyeControls3D(position=[1900, -1500, 1650],
                                           look_target=[80, 0, 800],
                                           eye_up=[0, 0, 1]),
        ),
        rrb.Vertical(
            rrb.TimeSeriesView(origin="coverage", name="Scrubbed, per arm (%)"),
            rrb.TimeSeriesView(origin="reach", name="Remaining work reachable (%)"),
            # The flags only: the per-frame motion reaches a metre while
            # the operator walks to the chair, and flattens them.
            rrb.TimeSeriesView(origin="track", name="Tracked, frozen, not seated",
                               contents=["+ /track/ok", "+ /track/freeze",
                                         "+ /track/away", "+ /track/stop"]),
        ),
        column_shares=[3, 1],
    ),
    rrb.BlueprintPanel(state="hidden"),
    rrb.SelectionPanel(state="hidden"),
    rrb.TimePanel(state="collapsed"),
).save("wheelgentic3d", sys.argv[1])
'''


def _port_open(port, timeout=0.3):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def save_path(run):
    """The recording a run writes, from its own command line, or None."""
    args = [str(a) for a in run.get("args") or []]
    path = None
    for i, a in enumerate(args):
        if a == "--save" and i + 1 < len(args):
            path = args[i + 1]
        elif a.startswith("--save="):
            path = a.split("=", 1)[1]
    if not path or not path.lower().endswith(".rrd"):
        return None
    if not os.path.isabs(path):
        path = os.path.join(run.get("cwd") or WORKTREE, path)
    return os.path.normpath(path)


class StoreSource:
    """The runs the console's store knows about."""

    def runs(self):
        from devconsole.store import STORE
        out = []
        for r in STORE.list_runs():
            path = save_path(r)
            if path:
                out.append(dict(r, path=path))
        return out

    def running(self, rid):
        from devconsole.store import STORE
        return STORE.process_alive(rid)


class Follower(threading.Thread):
    """One run's recording, piped into the live server while it grows."""

    def __init__(self, live, rid, label, path, running, seed=False):
        super().__init__(name=f"dc-live3d {rid[-32:]}", daemon=True)
        self.live = live
        self.rid, self.label, self.path = rid, label, path
        self.running = running          # () -> is the run's process still there
        self.seed = seed
        self.state = "waiting"          # waiting streaming done stalled failed
        self.why = "waiting for the run to open its recording"
        self.sent = 0
        self.digest = hashlib.sha256()  # of every byte forwarded
        self.opened = time.time()
        self.first_byte = self.last_byte = self.ended = None
        self.proc = None
        self.code = None
        self._cancel = threading.Event()
        self._writing_since = None
        self._killed = False

    # --- life -------------------------------------------------------------

    def cancel(self):
        self._cancel.set()

    def finished(self):
        return self.state not in ("waiting", "streaming")

    def run(self):
        try:
            self._follow()
        except Exception as exc:                              # noqa: BLE001
            self._end("failed", f"the follower crashed: {exc!r}")

    def _end(self, state, why):
        p = self.proc
        if p is not None:
            # End of input is how a forwarder learns the recording is over.
            try:
                p.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                self.code = p.wait(CLOSE_WAIT_S if state == "done" else 1.0)
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    self.code = p.wait(5)
                except subprocess.TimeoutExpired:
                    self.code = None
                if state == "done":
                    state, why = "failed", (f"the forwarder was still running "
                                            f"{CLOSE_WAIT_S:.0f}s after the end")
            if state == "done" and self.code != 0:
                state, why = "failed", (f"the forwarder exited with {self.code}; "
                                        f"see live3d.log")
        self.state, self.why, self.ended = state, why, time.time()
        self.live._followed(self)

    def _follow(self):
        while True:
            if self._cancel.is_set():
                return self._end("stopped", "the console stopped following")
            if os.path.exists(self.path):
                break
            if not self.running():
                return self._end("failed", "the run ended without a recording")
            time.sleep(0.5)
        self.live.make_room(self)
        # Checked now, not at start: with nothing listening, a forwarder would
        # have nothing to send to.
        if not self.live.server_up():
            return self._end("failed", "the live 3D server is not up")
        self.proc = self.live.spawn_forwarder(self.rid)
        threading.Thread(target=self._watchdog, name="dc-live3d-watchdog",
                         daemon=True).start()
        self.state, self.why = "streaming", "reading the recording as it grows"
        pos, quiet = 0, None
        while True:
            if self._cancel.is_set():
                return self._end("stopped", "the console stopped following")
            try:
                size = os.path.getsize(self.path)
            except OSError:
                size = -1
            if size < pos:
                return self._end("failed", "the recording shrank or vanished "
                                           "while it was being read")
            if size > pos:
                with open(self.path, "rb") as fh:
                    fh.seek(pos)
                    data = fh.read(min(size - pos, CHUNK_BYTES))
                if data:
                    if not self._send(data):
                        return None
                    pos += len(data)
                    self.sent = pos
                    now = time.time()
                    self.first_byte = self.first_byte or now
                    self.last_byte = now
                    quiet = None
                    continue
            if not self.running():
                quiet = quiet or time.time()
                if time.time() - quiet >= QUIET_S:
                    return self._end("done", f"sent all {pos / 2**20:.1f} MB")
            time.sleep(READ_EVERY_S)

    def _send(self, data):
        self._writing_since = time.time()
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (OSError, ValueError):
            self._writing_since = None
            if self._killed:
                self._end("stalled", f"a write blocked for more than {STALL_S:.0f}s, "
                                     f"so the forwarder was stopped; the run was "
                                     f"not affected")
            else:
                self._end("failed", f"the forwarder exited ({self.proc.poll()}); "
                                    f"see live3d.log")
            return False
        self._writing_since = None
        self.digest.update(data)
        return True

    def _watchdog(self):
        proc = self.proc
        while proc.poll() is None and not self.finished():
            since = self._writing_since
            if since is not None and time.time() - since > STALL_S:
                self._killed = True
                proc.kill()
                return
            time.sleep(0.2)

    def describe(self):
        now = time.time()
        return {"run": self.rid, "label": self.label, "state": self.state,
                "why": self.why, "seed": self.seed,
                "sent_mb": round(self.sent / 2**20, 1),
                "since_last_byte_s": (round(now - self.last_byte, 1)
                                      if self.last_byte and not self.finished()
                                      else None),
                "opened": self.opened, "first_byte": self.first_byte,
                "ended": self.ended,
                "pid": self.proc.pid if self.proc is not None else None}


class Live:
    """The live server, and one follower per recording being written."""

    def __init__(self, source=None, web_port=WEB_PORT, grpc_port=GRPC_PORT,
                 runs_dir=RUNS, seed=True, fresh=True):
        self.source = source or StoreSource()
        self.web_port, self.grpc_port = web_port, grpc_port
        self.runs_dir = runs_dir
        self.log_path = os.path.join(runs_dir, "live3d.log")
        self.seed = seed
        self.fresh = fresh           # an empty server for each new run
        self.lock = threading.RLock()
        self.proc = None
        self.state, self.why = "off", "not started"
        self.since = None
        self.generation = 0          # servers started so far; a page reloads on change
        self.followers = {}          # path -> Follower, for this server's life
        self.history = []            # finished followers, newest last
        self.restarts = 0
        self._last_start = 0.0
        self._restarting = False
        self._seeded = False
        self._stop = threading.Event()
        self._thread = None
        self._atexit = False

    # --- the server ---------------------------------------------------------

    def url(self):
        proxy = f"rerun+http://127.0.0.1:{self.grpc_port}/proxy"
        return (f"http://127.0.0.1:{self.web_port}/?url="
                f"{urllib.parse.quote(proxy, safe='')}")

    def server_up(self):
        return (self.proc is not None and self.proc.poll() is None
                and _port_open(self.grpc_port))

    def _blueprint(self):
        """The layout, written once per Rerun version and per BLUEPRINT.

        -> path or None. Written by a short subprocess: the console itself
        never imports rerun.
        """
        try:
            version = importlib.metadata.version("rerun-sdk")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        tag = hashlib.sha1(BLUEPRINT.encode()).hexdigest()[:8]
        path = os.path.join(self.runs_dir, f"overview-{version}-{tag}.rbl")
        if os.path.exists(path):
            return path
        try:
            r = subprocess.run([sys.executable, "-c", BLUEPRINT, path],
                               capture_output=True, text=True, timeout=90,
                               creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return None
        return path if r.returncode == 0 and os.path.exists(path) else None

    def start(self):
        """Start the server and the thread that follows runs. -> message.

        The server is started without holding the lock: it can take seconds,
        and the page reads status() meanwhile.
        """
        if not self._atexit:
            atexit.register(self.close)
            self._atexit = True
        msg = self._start_server()
        if self._thread is None and self.state == "up":
            self._thread = threading.Thread(target=self._supervise,
                                            name="dc-live3d", daemon=True)
            self._thread.start()
        return msg

    def _start_server(self):
        self._last_start = time.time()
        exe = V3D._rerun_exe()
        if exe is None:
            self.state, self.why = "failed", "no rerun executable next to this Python"
            return self.why
        busy = [p for p in (self.web_port, self.grpc_port) if _port_open(p)]
        if busy:
            self.state, self.why = "failed", (
                f"port {busy[0]} is already taken by something else; "
                f"start the console with --no-3d, or free it")
            return self.why
        os.makedirs(self.runs_dir, exist_ok=True)
        rbl = self._blueprint()
        cmd = [exe, "--serve-web", "--bind", "127.0.0.1",
               "--web-viewer-port", str(self.web_port), "--port", str(self.grpc_port),
               "--newest-first", "--server-memory-limit", MEMORY_LIMIT]
        if rbl:
            cmd.append(rbl)
        with open(self.log_path, "ab") as log:
            log.write(f"\n=== live 3D server, {time.ctime()} ===\n".encode())
            log.flush()
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log,
                                         stderr=subprocess.STDOUT,
                                         creationflags=NO_WINDOW)
        self.state, self.why = "starting", "waiting for the server to answer"
        deadline = time.time() + START_TIMEOUT_S
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.state, self.why = "failed", (
                    f"rerun exited at once ({self.proc.returncode}); see "
                    f"{os.path.relpath(self.log_path, WORKTREE)}")
                self.proc = None
                return self.why
            if _port_open(self.web_port) and _port_open(self.grpc_port):
                self.state, self.since = "up", time.time()
                self.generation += 1
                self.why = "serving" + ("" if rbl else ", without its layout")
                return f"live 3D on {self.url()}"
            time.sleep(0.2)
        self._stop_server()
        self.state, self.why = "failed", (
            f"rerun did not answer within {START_TIMEOUT_S:.0f}s")
        return self.why

    def _stop_server(self):
        if self.proc is not None and self.proc.poll() is None:
            _stop_tree(self.proc.pid)
        self.proc = None

    def close(self):
        """Stop following, then stop the server."""
        self._stop.set()
        with self.lock:
            followers = list(self.followers.values())
        for f in followers:
            f.cancel()
        for f in followers:
            f.join(CLOSE_WAIT_S + 2)
        with self.lock:
            self._stop_server()
            self.state, self.why = "off", "stopped with the console"

    # --- following ----------------------------------------------------------

    def spawn_forwarder(self, rid):
        exe = V3D._rerun_exe()
        with open(self.log_path, "ab") as log:
            log.write(f"\n=== forwarder for {rid}, {time.ctime()} ===\n".encode())
            log.flush()
            return subprocess.Popen(
                [exe, "--headless", "--bind", "127.0.0.1",
                 "--port", str(self.grpc_port), "-"],
                stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                creationflags=NO_WINDOW)

    def follow(self, rid, label, path, running, seed=False):
        """Start following one recording, unless this server already has it."""
        with self.lock:
            if path in self.followers or self.state != "up":
                return None
            f = Follower(self, rid, label, path, running, seed=seed)
            self.followers[path] = f
        f.start()
        return f

    def make_room(self, follower):
        """`follower`'s recording just appeared: give it an empty server.

        Only when every run this server holds has finished. A run still
        streaming keeps the server, and the new one joins it.
        """
        if not self.fresh:
            return
        with self.lock:
            others = [f for f in self.followers.values() if f is not follower]
            if not others or any(not f.finished() for f in others):
                return
            self._restarting = True
            self.followers = {follower.path: follower}
            self._stop_server()
        try:
            self._start_server()
        finally:
            self._restarting = False

    def _followed(self, follower):
        with self.lock:
            self.history.append(follower)
            del self.history[:-30]

    def _supervise(self):
        while not self._stop.wait(SUPERVISE_S):
            try:
                self._tick()
            except Exception as exc:                          # noqa: BLE001
                print(f"[devconsole] live 3D: {exc!r}", flush=True)

    def _tick(self):
        if self._restarting:
            return
        if self.proc is None or self.proc.poll() is not None:
            self._server_down()
            return
        runs = self.source.runs()
        for r in runs:
            if r.get("status") in ("running", "starting"):
                rid = r["id"]
                self.follow(rid, r.get("label") or rid, r["path"],
                            lambda rid=rid: self.source.running(rid))
        if self.seed and not self._seeded:
            self._seeded = True
            with self.lock:
                busy = any(not f.finished() for f in self.followers.values())
            done = [r for r in runs
                    if r.get("status") in ("ok", "stopped") and not r.get("nested")
                    and r.get("script") in SEED_SCRIPTS and _nonempty(r["path"])]
            if done and not busy:
                # A run somebody started beats one the self-test started.
                r = max(done, key=lambda s: (s.get("job") != "check",
                                             s.get("started") or 0))
                self.follow(r["id"], r.get("label") or r["id"], r["path"],
                            lambda: False, seed=True)

    def _server_down(self):
        """The server died. Say so, and start another now and then.

        A new server holds nothing, so everything is followed again from the
        start of its file, and the view is seeded again.
        """
        with self.lock:
            was_up = self.state == "up"
            code = self.proc.poll() if self.proc is not None else None
            followers = list(self.followers.values())
            self.followers = {}
            self._seeded = False
            if was_up:
                self.state, self.why = "down", f"the server exited ({code})"
        for f in followers:
            f.cancel()
        if self.restarts >= MAX_RESTARTS:
            self.state, self.why = "failed", (
                f"the server stopped {self.restarts + 1} times; restart the console")
            return
        if time.time() - self._last_start >= RESTART_AFTER_S:
            self.restarts += 1
            self._start_server()

    # --- reading ------------------------------------------------------------

    def status(self):
        with self.lock:
            current = sorted(self.followers.values(), key=lambda f: f.opened,
                             reverse=True)
            shown = [f for f in current if f.first_byte]
            return {"state": self.state, "why": self.why, "since": self.since,
                    "url": self.url() if self.state == "up" else None,
                    "pid": self.proc.pid if self.proc is not None else None,
                    "ports": [self.web_port, self.grpc_port],
                    "restarts": self.restarts, "generation": self.generation,
                    "showing": (max(shown, key=lambda f: f.first_byte).describe()
                                if shown else None),
                    "followers": [f.describe() for f in current]}

    def label(self, pid):
        """What a process is, if it is one of ours. -> text or None."""
        with self.lock:
            if self.proc is not None and pid == self.proc.pid:
                return "the Overview's live 3D server"
            for f in self.followers.values():
                if f.proc is not None and f.proc.pid == pid:
                    return f"live 3D: streaming {f.label}"
        return None


def _nonempty(path):
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _stop_tree(pid):
    import psutil
    try:
        root = psutil.Process(pid)
        procs = root.children(recursive=True) + [root]
    except psutil.Error:
        return
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


def reap_orphans(runs_dir=RUNS, web_port=WEB_PORT, grpc_port=GRPC_PORT):
    """Stop a live server or forwarder a previous console left running. -> n.

    Matched on this module's exact command lines: a server serving the web
    viewer on `web_port` with gRPC on `grpc_port`, and a headless forwarder
    reading stdin for that gRPC port. An operator's own viewer matches
    neither.
    """
    import psutil
    n = 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if not (p.info["name"] or "").lower().startswith("rerun"):
                continue
            cmd = [str(c) for c in p.info["cmdline"] or []]
            server = ("--serve-web" in cmd and _after(cmd, "--web-viewer-port")
                      == str(web_port) and _after(cmd, "--port") == str(grpc_port))
            forwarder = (cmd[-1:] == ["-"] and "--headless" in cmd
                         and _after(cmd, "--port") == str(grpc_port))
            if server or forwarder:
                p.terminate()
                n += 1
        except psutil.Error:
            continue
    return n


def _after(cmd, flag):
    try:
        return cmd[cmd.index(flag) + 1]
    except (ValueError, IndexError):
        return None


LIVE = None


def live(**kw):
    global LIVE
    if LIVE is None:
        LIVE = Live(**kw)
    return LIVE
