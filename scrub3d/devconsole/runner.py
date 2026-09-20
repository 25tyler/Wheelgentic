"""devconsole/runner.py -- run any scrub3d script with the console watching.

    python scrub3d/devconsole/runner.py scrub3d/main.py --replay scrub3d/data/bag01
    python scrub3d/devconsole/runner.py scrub3d/fleet.py

The target runs UNCHANGED. What the runner adds is a run directory under
`devconsole/runs/` holding what it did (events.jsonl), what it printed
(stdout.log), how it started (meta.json) and how it ended (exit.json).

TWO PROCESSES, ON PURPOSE
-------------------------
A terminal run starts a child -- `runner.py --child RUN -- SCRIPT ARGS` -- and
tails its log. The console starts the same child directly. So a run looks the
same however it began, and the child's stdout goes straight into a file at the
OS level, which is the only way to also catch what MediaPipe and Open3D print
from C++.

HOW THE CHILD RUNS THE SCRIPT
-----------------------------
1. Its own helpers are loaded by FILE PATH under private names. `armlink` puts
   `py/` at the front of `sys.path`, and `py/` has modules called `config`,
   `vision` and `motion`; nothing of ours may be importable by a name like
   those.
2. `sys.path[0]` becomes the script's directory and `sys.argv` the script's
   arguments -- exactly what `python script.py` would have set.
3. The probe hook is installed, so every scrub3d module is wrapped as it
   finishes importing.
4. The script is split at its `if __name__ == "__main__":`. The part above runs
   into a fresh `__main__` module, probes are installed into that namespace --
   which is how `main()` finding `preflight` in its own globals gets caught --
   and then the guard runs.

STOPPING
--------
The console writes `RUN/stop`. The child notices within 200 ms and the probes
end the run cooperatively (see probes.DevConsoleStop). Three seconds later an
asynchronous exception is raised in the main thread. After ten the console
kills the process tree. `terminate()` is not the Stop button: on Windows it
skips `finally` and `atexit` and orphans children, and Ctrl-C does not stop
`main.py` at all.

IT NEVER COMMANDS HARDWARE
--------------------------
Inside a child, opening a serial port raises unless `--allow-hardware` was
given. The console never gives it.
"""
import ast
import builtins
import datetime
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SCRUB3D = os.path.dirname(HERE)
WORKTREE = os.path.dirname(SCRUB3D)
RUNS = os.path.join(HERE, "runs")
RUNNER = os.path.abspath(__file__)

KEEP_RUNS = 20
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


# ---------------------------------------------------------------------------
# Run directories
# ---------------------------------------------------------------------------

def ensure_runs_dir():
    """Create runs/ with its own .gitignore, before anything is written in it.

    Runs can hold camera frames, which are pictures of a person. The repo's
    .gitignore lists runs/ as well, but a directory that ignores itself stays
    ignored even if it is moved or the repo file is edited.
    """
    os.makedirs(RUNS, exist_ok=True)
    gi = os.path.join(RUNS, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as fh:
            fh.write("# Camera frames and body data: never committed.\n*\n")
    return RUNS


def _slug(s):
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip("-")
    return s[:48] or "run"


def new_run_dir(label, parent=None):
    base = os.path.join(parent, "children") if parent else ensure_runs_dir()
    os.makedirs(base, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{_slug(label)}"
    path = os.path.join(base, name)
    i = 1
    while os.path.exists(path):
        i += 1
        path = os.path.join(base, f"{name}-{i}")
    os.makedirs(path)
    os.makedirs(os.path.join(path, "frames"), exist_ok=True)
    return path


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, default=str)
    os.replace(tmp, path)


def child_env():
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    # stdio only. PYTHONUTF8 would also change open()'s default encoding inside
    # frames, scan and rigconfig -- a pipeline change by the back door.
    env["PYTHONIOENCODING"] = "utf-8:backslashreplace"
    env.pop("PYTHONUTF8", None)
    return env


def spawn_child(run_dir, script, args, label=None, job=None,
                allow_hardware=False, extra=None):
    """Start `runner.py --child` for this run. -> Popen.

    stdout goes to a file the child owns from its first byte, stdin is closed,
    and the child gets its own process group and no console window.
    """
    cmd = [sys.executable, RUNNER, "--child", run_dir]
    if allow_hardware:
        cmd.append("--allow-hardware")
    if label:
        cmd += ["--label", label]
    if job:
        cmd += ["--job", job]
    if extra:
        cmd += list(extra)
    cmd += ["--", script, *args]
    _write_json(os.path.join(run_dir, "launch.json"),
                {"cmd": cmd, "label": label, "job": job,
                 "launched": time.time(), "script": script, "args": list(args)})
    out = open(os.path.join(run_dir, "stdout.log"), "wb")
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(cmd, cwd=WORKTREE, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT,
                                env=child_env(), creationflags=flags)
    finally:
        out.close()
    return proc


def kill_tree(pid, keep=("rerun",)):
    """Kill a process and everything it started, except a Rerun viewer."""
    try:
        import psutil
    except Exception:                                         # noqa: BLE001
        return 0
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return 0
    victims = []
    try:
        victims = root.children(recursive=True)
    except psutil.Error:
        pass
    victims = [p for p in victims
               if not any(k in (p.name() or "").lower() for k in keep)]
    victims.append(root)
    for p in victims:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(victims, timeout=5)
    return len(victims)


# ---------------------------------------------------------------------------
# The child
# ---------------------------------------------------------------------------

def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _find_main_guard(tree):
    for i, node in enumerate(tree.body):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                and t.left.id == "__name__" and len(t.comparators) == 1
                and isinstance(t.comparators[0], ast.Constant)
                and t.comparators[0].value == "__main__"):
            return i
    return None


def run_script(script, probes):
    """Execute the script as __main__, installing probes into its namespace."""
    with open(script, "rb") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=script)
    split = _find_main_guard(tree)

    mod = types.ModuleType("__main__")
    mod.__file__ = script
    mod.__builtins__ = builtins
    mod.__spec__ = None
    mod.__package__ = None
    mod.__loader__ = importlib.machinery.SourceFileLoader("__main__", script)
    mod.__cached__ = None
    sys.modules["__main__"] = mod
    name = os.path.splitext(os.path.basename(script))[0]

    if split is None:
        exec(compile(tree, script, "exec"), mod.__dict__)
        return
    pre = ast.Module(body=tree.body[:split], type_ignores=[])
    guard = ast.Module(body=tree.body[split:], type_ignores=[])
    exec(compile(pre, script, "exec"), mod.__dict__)
    probes.install(mod.__dict__, name, script)
    exec(compile(guard, script, "exec"), mod.__dict__)


def _exit_code(exc):
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def child_main(argv):
    opts = {"allow_hardware": False, "label": None, "job": None, "nested": False}
    if "--" not in argv:
        print("runner --child needs `-- SCRIPT ARGS`", file=sys.stderr)
        return 2
    cut = argv.index("--")
    head, tail = argv[:cut], argv[cut + 1:]
    run_dir = os.path.abspath(head[0])
    i = 1
    while i < len(head):
        a = head[i]
        if a == "--allow-hardware":
            opts["allow_hardware"] = True
        elif a == "--nested":
            opts["nested"] = True
        elif a in ("--label", "--job") and i + 1 < len(head):
            opts[a[2:]] = head[i + 1]
            i += 1
        i += 1
    if not tail:
        print("runner --child: no script given", file=sys.stderr)
        return 2
    script = os.path.abspath(tail[0])
    args = tail[1:]

    events = _load("_dc_events", "events.py")
    probes = _load("_dc_probes", "probes.py")
    os.makedirs(run_dir, exist_ok=True)
    writer = events.Writer(run_dir)

    if opts["nested"]:
        _tee_stdout(os.path.join(run_dir, "stdout.log"))

    stop = threading.Event()
    rt = probes.Runtime(writer, events, stop, opts["allow_hardware"], script, run_dir)

    import psutil
    me = psutil.Process()
    meta = {"run": os.path.basename(run_dir), "dir": run_dir,
            "label": opts["label"] or os.path.basename(script),
            "job": opts["job"], "script": os.path.relpath(script, WORKTREE),
            "args": args, "pid": os.getpid(), "create_time": me.create_time(),
            "python": sys.version.split()[0], "started": time.time(),
            "cwd": os.getcwd(), "allow_hardware": opts["allow_hardware"],
            "nested": opts["nested"],
            "parent": (os.path.dirname(os.path.dirname(run_dir))
                       if opts["nested"] else None)}
    _write_json(os.path.join(run_dir, "meta.json"), meta)
    writer.emit("run", **{k: meta[k] for k in ("label", "job", "script", "args",
                                                "pid", "python", "nested")})

    main_ident = threading.get_ident()
    finished = threading.Event()
    threading.Thread(target=_stop_watcher,
                     args=(run_dir, stop, finished, main_ident, probes, writer),
                     name="dc-stop", daemon=True).start()

    # The script sees what `python script.py` would have shown it.
    here_norm = os.path.normcase(HERE)
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or "."))
                   != here_norm]
    sys.path.insert(0, os.path.dirname(script))
    sys.argv = [script, *args]

    probes.activate(rt)
    _watch_subprocess(rt, run_dir, writer)

    code, why = 0, "returned"
    t0 = time.time()
    try:
        run_script(script, probes)
    except SystemExit as exc:
        code, why = _exit_code(exc), "sys.exit"
    except (KeyboardInterrupt, probes.DevConsoleStop) as exc:
        code, why = 130, f"stopped ({type(exc).__name__})"
        print(f"\n[devconsole] stopped: {exc}", flush=True)
    except BaseException:                                     # noqa: BLE001
        traceback.print_exc()
        code, why = 1, "exception"
    finally:
        finished.set()

    if stop.is_set() and code == 0:
        why = "stopped cleanly (the feed ended the loop)"
    peak = None
    torch = sys.modules.get("torch")
    try:
        if torch is not None and torch.cuda.is_initialized():
            peak = round(torch.cuda.max_memory_allocated() / 2**20, 1)
    except Exception:                                         # noqa: BLE001
        pass
    try:
        rt.flush_aggs()
    except Exception:                                         # noqa: BLE001
        pass
    exit_rec = {"code": code, "why": why, "stopped": stop.is_set(),
                "ended": time.time(), "seconds": round(time.time() - t0, 2),
                "torch_peak_mb": peak,
                "probe_ms": round(rt.probe_s * 1000, 2),
                "writer_ms": round(writer.cost_s * 1000, 2)}
    writer.emit("exit", **exit_rec)
    writer.close()
    _write_json(os.path.join(run_dir, "exit.json"), exit_rec)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:                                         # noqa: BLE001
        pass
    return code


def _stop_watcher(run_dir, stop, finished, main_ident, probes, writer):
    flag = os.path.join(run_dir, "stop")
    while not finished.is_set():
        if os.path.exists(flag):
            stop.set()
            writer.emit("stop", phase="cooperative")
            break
        time.sleep(0.2)
    if finished.is_set():
        return
    deadline = time.time() + probes.STOP_GRACE_S
    while time.time() < deadline:
        if finished.wait(0.1):
            return
    # Still running: interrupt the main thread. This waits for any C call that
    # is in progress to return, which is the most that can be asked of it.
    import ctypes
    writer.emit("stop", phase="async exception")
    ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(main_ident), ctypes.py_object(probes.DevConsoleStop))


def _watch_subprocess(rt, run_dir, writer):
    """A child scrub3d script becomes a child run, visible under this one."""
    orig_init = subprocess.Popen.__init__
    scrub = os.path.normcase(SCRUB3D)
    here = os.path.normcase(HERE)

    def __init__(self, args, *a, **kw):
        try:
            if isinstance(args, (list, tuple)) and len(args) >= 2:
                exe = os.path.basename(str(args[0])).lower()
                script = os.path.normcase(os.path.abspath(str(args[1])))
                if (exe.startswith("python") and script.endswith(".py")
                        and script.startswith(scrub + os.sep)
                        and not script.startswith(here + os.sep)):
                    child = new_run_dir(os.path.basename(str(args[1])), parent=run_dir)
                    args = [args[0], RUNNER, "--child", child, "--nested",
                            "--", str(args[1]), *[str(x) for x in args[2:]]]
                    writer.emit("child", dir=child, script=os.path.basename(script))
        except Exception as exc:                              # noqa: BLE001
            writer.emit("note", what="nested run rewrite failed", err=repr(exc))
        return orig_init(self, args, *a, **kw)

    subprocess.Popen.__init__ = __init__


class _Tee:
    def __init__(self, stream, path):
        self._s = stream
        self._f = open(path, "a", encoding="utf-8", errors="replace")

    def write(self, data):
        try:
            self._f.write(data)
            self._f.flush()
        except Exception:                                     # noqa: BLE001
            pass
        return self._s.write(data)

    def flush(self):
        self._s.flush()

    def __getattr__(self, name):
        return getattr(self._s, name)


def _tee_stdout(path):
    sys.stdout = _Tee(sys.stdout, path)
    sys.stderr = _Tee(sys.stderr, path)


# ---------------------------------------------------------------------------
# The terminal front end
# ---------------------------------------------------------------------------

def terminal_main(argv):
    allow = False
    if argv and argv[0] == "--allow-hardware":
        allow = True
        argv = argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    script = os.path.abspath(argv[0])
    if not os.path.exists(script):
        alt = os.path.join(SCRUB3D, argv[0])
        if os.path.exists(alt):
            script = alt
        else:
            print(f"no such script: {argv[0]}", file=sys.stderr)
            return 2
    run_dir = new_run_dir(os.path.basename(script))
    proc = spawn_child(run_dir, script, argv[1:], label=os.path.basename(script),
                       job="terminal", allow_hardware=allow)
    print(f"[devconsole] run {os.path.relpath(run_dir, WORKTREE)}  pid {proc.pid}",
          flush=True)
    log = os.path.join(run_dir, "stdout.log")
    presses = 0
    pos = 0
    while True:
        try:
            pos = _pump(log, pos)
            if proc.poll() is not None:
                time.sleep(0.2)
                _pump(log, pos)
                break
            time.sleep(0.1)
        except KeyboardInterrupt:
            presses += 1
            if presses == 1:
                print("\n[devconsole] stopping (Ctrl-C again to kill)", flush=True)
                open(os.path.join(run_dir, "stop"), "w").close()
            else:
                print("\n[devconsole] killing", flush=True)
                kill_tree(proc.pid)
                break
    code = proc.wait()
    if not os.path.exists(os.path.join(run_dir, "exit.json")):
        _write_json(os.path.join(run_dir, "exit.json"),
                    {"code": code, "why": "killed", "killed": True,
                     "ended": time.time()})
    print(f"[devconsole] exit {code}", flush=True)
    return code


def _pump(path, pos):
    try:
        with open(path, "rb") as fh:
            fh.seek(pos)
            data = fh.read()
    except OSError:
        return pos
    if data:
        sys.stdout.write(data.decode("utf-8", errors="replace"))
        sys.stdout.flush()
    return pos + len(data)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--child":
        sys.exit(child_main(argv[1:]))
    sys.exit(terminal_main(argv))
