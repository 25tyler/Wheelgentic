"""devconsole/check.py -- the console's own self-test.

    python scrub3d/devconsole/check.py            # about a minute
    python scrub3d/devconsole/check.py --full     # + the replay, Stop, overhead

Every check runs a real script through the runner, exactly as a button on the
page does, then reads back what the probes wrote. A check that passes means the
console shows that thing. A check that fails means a page would be quietly
wrong: an empty run looks like a quiet one.

The runs land in devconsole/runs like any other, labelled "check: ...", so a
failure can be opened in the console and looked at.
"""
import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRUB3D = os.path.dirname(HERE)
WORKTREE = os.path.dirname(SCRUB3D)
# Imported as a package, for the same reason as app.py.
sys.path[:] = [p for p in sys.path
               if os.path.normcase(os.path.abspath(p or ".")) != os.path.normcase(HERE)]
if SCRUB3D not in sys.path:
    sys.path.insert(0, SCRUB3D)

from devconsole import live3d as L3                             # noqa: E402
from devconsole import runner as RN                             # noqa: E402
from devconsole import store as ST                              # noqa: E402

DATA = os.path.join(SCRUB3D, "data")
FAILED = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  --  {detail}" if detail else ""),
          flush=True)
    if not ok:
        FAILED.append(name)
    return ok


def script_file(run_dir, name, body):
    """A throwaway script inside the run, with scrub3d importable by bare name."""
    path = os.path.join(run_dir, name)
    head = ("import sys\n"
            f"sys.path.insert(0, {SCRUB3D!r})\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + body)
    return path


class Done:
    """A finished run, parsed the way the console parses it."""

    def __init__(self, run_dir, code, seconds):
        self.dir = run_dir
        self.code = code
        self.seconds = seconds
        self.run = ST.Run(run_dir)
        self.run.refresh_meta()
        self.run.pump()

    def spans(self, fn):
        """Every span of `fn`, from the event file itself: the console keeps
        only the recent ones."""
        out = []
        with open(os.path.join(self.dir, "events.jsonl"), encoding="utf-8",
                  errors="replace") as fh:
            for line in fh:
                if f'"{fn}"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("k") == "span" and ev.get("fn") == fn:
                    out.append(ev)
        return out

    def calls(self, fn):
        for st in self.run.stages.values():
            a = st.fn_aggs.get(fn)
            if a is not None:
                return a.get("n", 0)
        return 0

    def stdout(self):
        with open(os.path.join(self.dir, "stdout.log"), "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")

    def misc(self, kind):
        return [e for e in self.run.misc if e.get("k") == kind]


def run(script, args=(), label=None, timeout=900, stop_when=None, follow=None,
        during=None):
    """Run `script` through the runner and wait. -> Done.

    `script` and `args` may be functions of the run directory, for a script
    written into the run or a `--save` path inside it. `stop_when(run)` is
    polled while the child runs; the first time it is true the stop file is
    written, exactly as the Stop button writes it. With `follow`, a LiveCheck,
    the live 3D server streams the run's view.rrd while it is written, and
    `during(follower)` is polled once the follower exists.
    """
    base = script if isinstance(script, str) else (label or "script")
    run_dir = RN.new_run_dir("check-" + os.path.splitext(os.path.basename(base))[0])
    script = script(run_dir) if callable(script) else script
    args = args(run_dir) if callable(args) else args
    name = label or os.path.basename(script)
    t0 = time.time()
    proc = RN.spawn_child(run_dir, script, list(args), label=f"check: {name}",
                          job="check")
    rid = os.path.relpath(run_dir, RN.RUNS).replace(os.sep, "/")
    if follow is not None:
        follow.add(rid, f"check: {name}", os.path.join(run_dir, "view.rrd"), proc,
                   os.path.basename(script))
    live = ST.Run(run_dir) if stop_when else None
    stopped_at = None
    while proc.poll() is None:
        if time.time() - t0 > timeout:
            RN.kill_tree(proc.pid)
            proc.wait(10)
            break
        if live is not None and stopped_at is None:
            live.refresh_meta()
            live.pump()
            if stop_when(live):
                open(os.path.join(run_dir, "stop"), "w").close()
                stopped_at = time.time()
        if during is not None and follow is not None:
            f = follow.follower(rid)
            if f is not None:
                during(f)
        time.sleep(0.25)
    ended = time.time()
    done = Done(run_dir, proc.returncode, ended - t0)
    done.ended = ended
    done.stop_took = None if stopped_at is None else ended - stopped_at
    done.follower = follow.wait(rid) if follow is not None else None
    return done


# ---------------------------------------------------------------------------
# The quick set
# ---------------------------------------------------------------------------

def check_probe_table():
    r = subprocess.run([sys.executable, os.path.join(HERE, "probes.py"), "--check"],
                       capture_output=True, text=True, timeout=120, cwd=WORKTREE)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    check("every probe target resolves (probes.py --check)",
          r.returncode == 0 and last == "OK", last)


def check_main_split():
    d = run(os.path.join(SCRUB3D, "session.py"))
    n = len(d.spans("session.Consent.press"))
    check("functions defined in the script itself are captured (session.py)",
          d.code == 0 and n == 3, f"{n} press spans, exit {d.code}")


def check_governor():
    d = run(os.path.join(SCRUB3D, "fleet.py"))
    seen = {}
    for e in d.run.verdicts:
        s = e.get("sum") or {}
        if s.get("action"):
            seen.setdefault(s["action"], s.get("reason") or "")
    want = ("hold", "retreat", "refuse", "estop")
    missing = [a for a in want if a not in seen]
    unexplained = [a for a in want if a in seen and not seen[a]]
    check("every kind of governor verdict is captured, with its reason (fleet.py)",
          d.code == 0 and not missing and not unexplained,
          f"saw {sorted(seen)}; missing {missing}; no reason {unexplained}")
    held = [e for e in d.spans("fleet.FleetGovernor._hold")]
    check("verdicts made inside _hold are captured too", bool(held),
          f"{len(held)} _hold spans")


def check_box_refusal():
    d = run(os.path.join(SCRUB3D, "armlink.py"))
    box = [e for e in d.spans("armlink.GovernedArm.set_target")
           if "BOX" in str((e.get("sum") or {}).get("verdict_reason") or "")
           and (e.get("sum") or {}).get("result") is False]
    check("the arm link's BOX refusal is captured (armlink.py)",
          d.code == 0 and bool(box),
          f"{len(box)} refused set_target calls naming BOX, exit {d.code}")


def check_identity():
    d = run(os.path.join(SCRUB3D, "bodystore.py"))
    verdicts = {(e.get("sum") or {}).get("verdict")
                for e in d.spans("bodystore.identity_ok")}
    want = {"LOAD", "REFUSE", "CANNOT JUDGE"}
    check("all three identity verdicts are captured (bodystore.py)",
          d.code == 0 and want <= verdicts,
          f"saw {sorted(v for v in verdicts if v)}, exit {d.code}")


DUP = """
import importlib.util
import fleet
spec = importlib.util.spec_from_file_location("fleet", fleet.__file__)
copy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copy)
sys.modules["_dc_probes"].install(copy, "fleet", fleet.__file__)
print("second copy wrapped:", hasattr(copy.FleetGovernor.propose, "__wrapped__"))
"""


def check_dup():
    # No current script imports a module twice, so the bookkeeping is driven
    # directly: a second module object from the same file.
    d = run(lambda rd: script_file(rd, "dup_probe.py", DUP), label="dup_probe.py")
    dups = [e for e in d.misc("dup") if e.get("module") == "fleet"]
    installs = [e for e in d.run.installed if e.get("module") == "fleet"]
    check("a module loaded twice is reported, and both copies are wrapped",
          d.code == 0 and dups and dups[-1].get("copies") == 2 and len(installs) == 2
          and "second copy wrapped: True" in d.stdout(),
          f"{len(dups)} dup events, {len(installs)} installs, exit {d.code}")


COPIED = """
import anatomy
import viz
same = viz.anatomical_body is anatomy.anatomical_body
print("copied name is the wrapped one:",
      same and hasattr(viz.anatomical_body, "__wrapped__"))
"""


def check_copied_names():
    d = run(lambda rd: script_file(rd, "copied_names.py", COPIED),
            label="copied_names.py")
    check("a name copied by `from anatomy import ...` is the wrapped object",
          d.code == 0 and "copied name is the wrapped one: True" in d.stdout(),
          f"exit {d.code}")


STOP_SAFE = """
import os
import time
import session
rt = sys.modules["_dc_probes"].RT
consent = session.Consent()
open(os.path.join(rt.run_dir, "stop"), "w").close()
time.sleep(0.8)
consent.revoke("check")
print("revoke ran after Stop")
try:
    consent.press()
    print("press ran after Stop")
except BaseException as exc:
    print("press was stopped:", type(exc).__name__)
"""


def check_stop_safe():
    d = run(lambda rd: script_file(rd, "stop_safe.py", STOP_SAFE), label="stop_safe.py")
    out = d.stdout()
    check("Stop never blocks a call that makes things safer, and blocks the rest",
          "revoke ran after Stop" in out and "press was stopped: DevConsoleStop" in out,
          " / ".join(l.strip() for l in out.splitlines()
                     if "after Stop" in l or "was stopped" in l) or f"exit {d.code}")


GUARD = """
import serial
try:
    serial.Serial("COM250")
except RuntimeError as exc:
    print("GUARD:", exc)
    raise SystemExit(0)
print("the port opened, or failed for some other reason")
raise SystemExit(1)
"""


def check_hardware_guard():
    d = run(lambda rd: script_file(rd, "open_serial.py", GUARD), label="open_serial.py")
    refused = [e for e in d.spans("serial.Serial.__init__") if e.get("refused")]
    check("opening a serial port in a console job is refused",
          d.code == 0 and "GUARD: devconsole" in d.stdout() and bool(refused),
          f"exit {d.code}, {len(refused)} refused spans")


# Spare ports, so a console that is running meanwhile is not disturbed.
LIVE_WEB, LIVE_GRPC = 9192, 9978


class LiveCheck:
    """A live 3D server that follows only the runs this check starts."""

    def __init__(self):
        self.rows, self.procs = [], {}
        self.live = L3.Live(source=self, web_port=LIVE_WEB, grpc_port=LIVE_GRPC,
                            runs_dir=RN.RUNS, seed=False)
        self.message = None

    def start(self):
        busy = [p for p in (LIVE_WEB, LIVE_GRPC) if L3._port_open(p)]
        if busy:
            self.message = f"port {busy[0]} is already taken"
            return False
        self.message = self.live.start()
        return self.live.state == "up"

    # The source live3d reads.
    def runs(self):
        for r in self.rows:
            r["status"] = "running" if self.procs[r["id"]].poll() is None else "ok"
        return [dict(r) for r in self.rows]

    def running(self, rid):
        return self.procs[rid].poll() is None

    def add(self, rid, label, path, proc, script):
        self.procs[rid] = proc
        self.rows.append({"id": rid, "label": label, "path": os.path.normpath(path),
                          "status": "running", "script": script})

    def follower(self, rid):
        with self.live.lock:
            return next((f for f in self.live.followers.values() if f.rid == rid),
                        None)

    def wait(self, rid, timeout=60):
        """The run's follower once it has finished, or None."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            f = self.follower(rid)
            if f is not None and f.finished():
                return f
            time.sleep(0.2)
        return self.follower(rid)

    def close(self):
        self.live.close()


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _rerun_for(port):
    """Rerun processes whose command line names this port."""
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = [str(c) for c in p.info["cmdline"] or []]
            if (p.info["name"] or "").lower().startswith("rerun") and str(port) in cmd:
                out.append(p.info["pid"])
        except psutil.Error:
            continue
    return out


def _streamed(d, what):
    """The follower of a finished run sent the whole file, and exited 0."""
    f = d.follower
    path = os.path.join(d.dir, "view.rrd")
    if f is None:
        return check(what, False, "the run was never followed")
    whole = os.path.exists(path) and f.digest.hexdigest() == _sha(path)
    early = f.first_byte is not None and f.first_byte < d.ended
    lead = (f"{d.ended - f.first_byte:.1f}s before the job ended"
            if f.first_byte else "never")
    return check(what, f.state == "done" and whole and f.code == 0 and early,
                 f"{f.state} ({f.why}); {f.sent / 2**20:.1f} MB, same bytes as the "
                 f"file: {whole}; forwarder exit {f.code}; first bytes sent {lead}")


def check_live3d(lc):
    if not check("the live 3D server starts (spare ports)", lc.live.state == "up",
                 lc.message or lc.live.why):
        return
    viz = os.path.join(SCRUB3D, "viz.py")
    d = run(viz, lambda rd: ["--frames", "80", "--save", os.path.join(rd, "view.rrd")],
            label="viz.py followed live", follow=lc)
    _streamed(d, "a job's recording is streamed while it is written, byte for byte")
    plain_dir = os.path.join(d.dir, "plain")
    os.makedirs(plain_dir, exist_ok=True)
    r = subprocess.run([sys.executable, viz, "--frames", "80",
                        "--save", os.path.join(plain_dir, "view.rrd")],
                       cwd=WORKTREE, capture_output=True, timeout=600,
                       env=RN.child_env())
    a = _masked(d.stdout(), d.dir)
    b = _masked(r.stdout.decode("utf-8", errors="replace"), plain_dir)
    check("a followed job prints exactly what an unfollowed one prints",
          d.code == 0 and r.returncode == 0 and a == b,
          f"exit {d.code} and {r.returncode}; {len(a)} vs {len(b)} lines")
    check("the server is still up afterwards", lc.live.server_up(), lc.live.why)


# ---------------------------------------------------------------------------
# The full set: minutes, and the GPU
# ---------------------------------------------------------------------------

REPLAY = ["--scan", os.path.join(DATA, "scan01"), "--replay", os.path.join(DATA, "bag01")]

# Third-party lines whose presence and order vary between two identical runs.
NOISE = re.compile(r"^(W0000|I0000|E0000|INFO:|WARNING:|\s*warnings\.warn|"
                   r".*UserWarning|.*DeprecationWarning)")


def _masked(text, *paths):
    lines = []
    for line in text.splitlines():
        if not line.strip() or NOISE.match(line):
            continue
        for p in paths:
            line = line.replace(p, "<PATH>")
        lines.append(re.sub(r"\d+(\.\d+)?", "#", line.rstrip()))
    return lines


def _replay_args(frames):
    return lambda rd: REPLAY + ["--frames", str(frames),
                                "--save", os.path.join(rd, "view.rrd")]


def check_replay(lc):
    frames = 150
    d = run(os.path.join(SCRUB3D, "main.py"), _replay_args(frames),
            label="main.py replay", follow=lc)
    run_dir = d.dir
    updates = d.calls("track.Tracker.update")
    check(f"the replay tracks every frame ({frames})", d.code == 0 and updates == frames,
          f"{updates} tracker updates, exit {d.code}")
    keys = set(d.run.series)
    for prefix in ("track/", "reach/", "coverage/"):
        n = sum(1 for k in keys if k.startswith(prefix))
        check(f"the loop's {prefix} series reach the console", n > 0, f"{n} series")
    rows = d.run.preflight or []
    check("all eight pre-flight rows are captured", len(rows) == 8, f"{len(rows)} rows")
    cost = d.run.cost or {}
    spent = (cost.get("probe_ms") or 0) + (cost.get("writer_ms") or 0)
    share = spent / 1000.0 / max(d.seconds, 1e-6)
    check("the probes cost under 5% of the run", share < 0.05,
          f"{share * 100:.2f}% of {d.seconds:.0f}s")

    # The same command without the runner. Its output must not change.
    plain_dir = os.path.join(run_dir, "plain")
    os.makedirs(plain_dir, exist_ok=True)
    r = subprocess.run([sys.executable, os.path.join(SCRUB3D, "main.py"), *REPLAY,
                        "--frames", str(frames),
                        "--save", os.path.join(plain_dir, "view.rrd")],
                       cwd=WORKTREE, capture_output=True, timeout=900,
                       env=RN.child_env())
    plain = r.stdout.decode("utf-8", errors="replace")
    a = _masked(d.stdout(), run_dir)
    b = _masked(plain, plain_dir)
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    check("a run through the console prints what a plain run prints",
          r.returncode == 0 and not diff and len(a) == len(b),
          f"{len(a)} vs {len(b)} lines, first difference: {diff[:1]}")
    if lc is not None:
        _streamed(d, "the replay streamed live, byte for byte")


def check_stop(lc):
    def far_enough(live):
        st = live.stages.get("tracking")
        a = st.fn_aggs.get("track.Tracker.update") if st else None
        return bool(a and a.get("n", 0) >= 30)
    d = run(os.path.join(SCRUB3D, "main.py"), _replay_args(300),
            label="Stop mid-replay", stop_when=far_enough, follow=lc)
    ex = d.run.exit or {}
    check("Stop mid-replay ends the run cleanly, with the loop's own summary",
          d.stop_took is not None and d.code == 0 and bool(ex.get("stopped"))
          and "frames tracked" in d.stdout(),
          (f"exit {d.code}, stopped={ex.get('stopped')}, ended {d.stop_took:.1f}s "
           f"after Stop") if d.stop_took is not None else "never reached 30 frames")
    if lc is not None:
        _streamed(d, "a stopped run's stream ends with it")
        forwarders = [pid for pid in _rerun_for(LIVE_GRPC) if pid != lc.live.proc.pid]
        check("no forwarder is left behind after Stop", not forwarders,
              f"left: {forwarders}")


def check_stall(lc):
    """A forwarder that stops reading is stopped; the job never notices."""
    import psutil
    frozen = {}

    def freeze(f):
        if not frozen and f.state == "streaming" and f.sent > 0 and f.proc:
            psutil.Process(f.proc.pid).suspend()
            frozen.update(pid=f.proc.pid, at=time.time())
    frames = 150
    d = run(os.path.join(SCRUB3D, "main.py"), _replay_args(frames),
            label="replay, forwarder frozen", follow=lc, during=freeze)
    f = d.follower
    updates = d.calls("track.Tracker.update")
    check("a frozen forwarder does not hold up the job",
          bool(frozen) and d.code == 0 and updates == frames,
          f"froze pid {frozen.get('pid')}; the job tracked {updates}/{frames} "
          f"frames, exit {d.code}")
    took = (f.ended - frozen["at"]) if (f and f.ended and frozen) else None
    check("the follower gives up on it after the stall limit",
          f is not None and f.state == "stalled" and took is not None
          and took < L3.STALL_S + 5,
          f"{f.state if f else None} ({f.why if f else ''})"
          + (f", {took:.1f}s after the freeze" if took is not None else ""))
    left = frozen.get("pid") in _rerun_for(LIVE_GRPC)
    check("the frozen forwarder is gone", bool(frozen) and not left,
          f"pid {frozen.get('pid')}")


FPS = re.compile(r"frames tracked at ([\d.]+) fps")


def check_overhead(repeats=3):
    track = os.path.join(SCRUB3D, "track.py")
    plain, probed = [], []
    for _ in range(repeats):
        r = subprocess.run([sys.executable, track], cwd=WORKTREE, capture_output=True,
                           timeout=600, env=RN.child_env())
        m = FPS.search(r.stdout.decode("utf-8", errors="replace"))
        if m:
            plain.append(float(m.group(1)))
        d = run(track, label="track.py (overhead)")
        m = FPS.search(d.stdout())
        if m:
            probed.append(float(m.group(1)))
    ok = len(plain) == repeats and len(probed) == repeats
    if ok:
        a, b = statistics.median(plain), statistics.median(probed)
        ok = b >= 0.95 * a
        detail = f"median {a:.1f} fps plain, {b:.1f} fps probed ({(b / a - 1) * 100:+.1f}%)"
    else:
        detail = f"could not read fps: plain {plain}, probed {probed}"
    check("probes cost the tracker under 5% of its frame rate", ok, detail)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--full", action="store_true",
                    help="also the 150-frame replay against a plain run, Stop, and "
                         "the frame-rate overhead (several minutes, GPU)")
    a = ap.parse_args()
    RN.ensure_runs_dir()
    t0 = time.time()
    print("devconsole self-test")
    check_probe_table()
    check_main_split()
    check_governor()
    check_box_refusal()
    check_identity()
    check_dup()
    check_copied_names()
    check_hardware_guard()
    check_stop_safe()
    lc = LiveCheck()
    up = lc.start()
    try:
        check_live3d(lc)
        if a.full:
            check_replay(lc if up else None)
            check_stop(lc if up else None)
            if up:
                check_stall(lc)
    finally:
        lc.close()
    left = _rerun_for(LIVE_GRPC) + _rerun_for(LIVE_WEB)
    check("closing the live 3D server leaves no Rerun process behind", not left,
          f"left: {left}")
    if a.full:
        check_overhead()
    print(f"\n  {time.time() - t0:.0f}s")
    if FAILED:
        print(f"  {len(FAILED)} FAILED: {FAILED}")
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
