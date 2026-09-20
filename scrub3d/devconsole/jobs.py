"""devconsole/jobs.py -- what the console can start, and how it stops it.

Every job is a command that already exists, run through `runner.py`. Each one
says what it needs (the camera, the GPU) and whether it writes anything OUTSIDE
its own run directory, because a button that rewrites config.json should say so
on the button.

Deliberately not a job:
  fetch_models.py   downloading the weights is accepting CC-BY-NC terms, which a
                    person does, not a page
  anything that commands an arm

STOP IS A LADDER, NOT terminate()
---------------------------------
`terminate()` on Windows kills without running `finally` or `atexit` and leaves
children running, and Ctrl-C does nothing to main.py. So Stop writes RUN/stop,
the child ends itself cooperatively, raises into its main thread after three
seconds, and only after ten does this kill the process tree -- sparing any
Rerun viewer, which is not the job's to lose.
"""
import json
import os
import shutil
import threading
import time

from devconsole import inventory as INV
from devconsole import runner as RN
from devconsole.store import STORE

HERE = os.path.dirname(os.path.abspath(__file__))
SCRUB3D = os.path.dirname(HERE)
DATA = os.path.join(SCRUB3D, "data")
KILL_AFTER_S = 10.0
SELFTESTS_JSON = os.path.join(RN.RUNS, "selftests.json")


class Job:
    def __init__(self, jid, label, group, script, build, needs=(), writes="",
                 confirm=None, params=None, doc=""):
        self.id, self.label, self.group = jid, label, group
        self.script = script
        self.build = build            # (params, run_dir) -> [args]
        self.needs = tuple(needs)
        self.writes = writes
        self.confirm = confirm
        self.params = params or {}
        self.doc = doc

    def describe(self):
        return {"id": self.id, "label": self.label, "group": self.group,
                "script": os.path.relpath(self.script, SCRUB3D).replace("\\", "/"),
                "needs": list(self.needs), "writes": self.writes,
                "confirm": self.confirm, "params": self.params, "doc": self.doc}


def _p(params, key, default):
    v = (params or {}).get(key)
    return default if v in (None, "") else v


def _capture(params, key="capture", default="scan01"):
    name = os.path.basename(str(_p(params, key, default)))
    return os.path.join(DATA, name)


def _script(rel):
    return os.path.join(SCRUB3D, rel)


def _registry():
    jobs = [
        Job("replay", "Replay bag01", "Pipeline", _script("main.py"),
            lambda p, run: ["--scan", _capture(p), "--replay", os.path.join(DATA, "bag01"),
                            "--frames", str(int(_p(p, "frames", 300))),
                            "--save", os.path.join(run, "view.rrd")]
            + (["--scrub-trunk"] if _p(p, "scrub_trunk", False) else []),
            needs=("gpu",),
            params={"frames": 300, "capture": "scan01", "scrub_trunk": False},
            doc="The whole flow off the recorded bag: scan, reconstruct, partition, "
                "pre-flight, then follow the person frame by frame."),
        Job("simulate", "Simulate the rig", "Pipeline", _script("viz.py"),
            lambda p, run: ["--capture", _capture(p),
                            "--frames", str(int(_p(p, "frames", 2400))),
                            "--realtime", "--save", os.path.join(run, "view.rrd")],
            needs=("gpu",), params={"frames": 2400, "capture": "scan01"},
            doc="No camera and no bag: the scanned person sits still while the "
                "four arms scrub what main.py would give them, at their real "
                "speed. Made for watching on the Overview."),
        Job("live", "Live camera", "Pipeline", _script("main.py"),
            lambda p, run: ["--scan", _capture(p), "--frames", str(int(_p(p, "frames", 600))),
                            "--save", os.path.join(run, "view.rrd")],
            needs=("camera", "gpu"), params={"frames": 600, "capture": "scan01"},
            doc="The same flow reading the D455 instead of the bag."),
        Job("record", "Record a person", "Pipeline", _script("main.py"),
            lambda p, run: ["--record", str(_p(p, "name", "person")),
                            "--seconds", str(float(_p(p, "seconds", 10))),
                            "--frames", "1", "--save", os.path.join(run, "view.rrd")],
            needs=("camera", "gpu"), writes="data/<name>/ (a new capture)",
            params={"name": "person", "seconds": 10},
            doc="Capture, segment, scan and plan in one command, as on camera day."),
        Job("preflight", "Pre-flight", "Pipeline", _script("main.py"),
            lambda p, run: ["--scan", _capture(p), "--preflight"],
            needs=("gpu",), params={"capture": "scan01"},
            doc="The eight checks. Anything that cannot be checked here is UNKNOWN."),
        Job("reconstruct", "Reconstruct", "Pipeline", _script("shell.py"),
            lambda p, run: ["--capture", _capture(p), "--preview",
                            os.path.join(run, "frames", "reconstruction.png")],
            needs=("gpu",), params={"capture": "scan01"},
            doc="The person from real depth and AI normals, with its four gates."),
        Job("cameraday", "Camera-day check", "Pipeline",
            _script(os.path.join("tools", "check_camera_day.py")),
            lambda p, run: [], needs=("gpu",),
            writes="a temporary capture, deleted again",
            doc="Three of the four runbook steps with only the RealSense call stubbed."),
        Job("search_dry", "Placement search (dry run)", "Planning",
            _script(os.path.join("tools", "plan_rig.py")),
            lambda p, run: ["--dry-run", "--samples", str(int(_p(p, "samples", 900))),
                            "--shortlist", str(int(_p(p, "shortlist", 20))),
                            "--keep", str(int(_p(p, "keep", 3))),
                            "--sweeps", str(int(_p(p, "sweeps", 2)))],
            params={"samples": 900, "shortlist": 20, "keep": 3, "sweeps": 2},
            doc="Hours at the defaults. Reports the best rig; writes nothing."),
        Job("search_write", "Placement search (may write the rig)", "Planning",
            _script(os.path.join("tools", "plan_rig.py")),
            lambda p, run: ["--samples", str(int(_p(p, "samples", 900))),
                            "--shortlist", str(int(_p(p, "shortlist", 20))),
                            "--keep", str(int(_p(p, "keep", 3))),
                            "--sweeps", str(int(_p(p, "sweeps", 2)))],
            writes="config.json, only if it beats the rig already there",
            confirm="This can replace the arm placement in config.json.",
            params={"samples": 900, "shortlist": 20, "keep": 3, "sweeps": 2},
            doc="The same search, allowed to write the winner."),
        Job("segment", "Re-segment captures", "Data",
            _script(os.path.join("tools", "segment_captures.py")),
            lambda p, run: [], needs=("gpu",),
            writes="sapiens_seg.npy in captures whose cache is stale",
            doc="Rebuilds only the masks that fail their registration check."),
        Job("probecheck", "Probe table check", "Console",
            os.path.join(HERE, "probes.py"), lambda p, run: ["--check"],
            doc="Every wrapped target still exists; every output pattern still matches."),
    ]
    for m in INV.selftest_modules():
        writes = "bodies/selftest" if m == "bodystore" else ""
        jobs.append(Job(f"selftest:{m}", m, "Self-tests", _script(f"{m}.py"),
                        lambda p, run: [], writes=writes,
                        needs=("gpu",) if m in ("scan", "shell", "track", "bodystore",
                                                "adapt", "rsfeed", "pose", "sapiens")
                        else (), doc=f"{m}.py's own self-test."))
    return jobs


class Jobs:
    def __init__(self):
        self.lock = threading.RLock()
        self.procs = {}
        self.batch = None
        self._reaper = threading.Thread(target=self._reap, name="dc-reaper",
                                        daemon=True)
        self._reaper.start()
        self._registry = None
        self._registry_key = None

    # --- the registry -----------------------------------------------------

    def registry(self):
        key = INV._mtime_key()
        if self._registry is None or key != self._registry_key:
            self._registry = {j.id: j for j in _registry()}
            self._registry_key = key
        return self._registry

    def describe(self):
        return [j.describe() for j in self.registry().values()]

    # --- starting ---------------------------------------------------------

    def running(self):
        return [r for r in STORE.list_runs() if r["status"] in ("running", "starting")]

    def needs(self, run):
        """What a run uses. A job from the registry says so; a run started from
        a terminal, or by another script, is judged by its command line."""
        job = self.registry().get(run.get("job"))
        if job is not None:
            return set(job.needs)
        script = run.get("script") or ""
        args = [str(a) for a in run.get("args") or []]
        if script == "record.py" or (script == "main.py" and "--replay" not in args
                                     and "--preflight" not in args):
            return {"camera", "gpu"}
        if script in ("main.py", "shell.py", "scan.py", "track.py", "bodystore.py",
                      "check_camera_day.py", "segment_captures.py"):
            return {"gpu"}
        if script == "viz.py" and "--live" in args:
            i = args.index("--live") + 1
            bag = i < len(args) and not args[i].startswith("--")
            return {"gpu"} if bag else {"camera", "gpu"}
        if script == "viz.py" and "--capture" in args:
            return {"gpu"}
        return set()

    def camera_busy(self):
        return any("camera" in self.needs(r) for r in self.running())

    def start(self, jid, params=None, confirmed=False, camera_present=None):
        """-> (run_id or None, message)."""
        job = self.registry().get(jid)
        if job is None:
            return None, f"no such job {jid!r}"
        if job.confirm and not confirmed:
            return None, f"needs confirmation: {job.confirm}"
        running = self.running()
        if "camera" in job.needs:
            if camera_present is False:
                return None, "no RealSense device is connected"
            busy = [r for r in running if "camera" in self.needs(r)]
            if busy:
                return None, (f"the camera is in use by {busy[0]['label']} "
                              f"({busy[0]['id']}); one camera job at a time")
        warn = ""
        if "gpu" in job.needs:
            gpu_busy = [r for r in running if "gpu" in self.needs(r)]
            if gpu_busy:
                warn = f" (another GPU job is running: {gpu_busy[0]['label']})"
        label = job.label if not jid.startswith("selftest:") else f"self-test {job.label}"
        run_dir = RN.new_run_dir(jid.replace(":", "-"))
        try:
            args = job.build(params or {}, run_dir)
            proc = RN.spawn_child(run_dir, job.script, args, label=label, job=jid)
        except Exception as exc:                              # noqa: BLE001
            shutil.rmtree(run_dir, ignore_errors=True)
            return None, f"could not start: {exc!r}"
        with self.lock:
            self.procs[run_dir] = proc
        self.prune()
        rid = os.path.relpath(run_dir, RN.RUNS).replace("\\", "/")
        return rid, f"started {label} (pid {proc.pid}){warn}"

    # --- stopping ---------------------------------------------------------

    def stop(self, rid):
        run = STORE.get(rid)
        if run is None:
            return f"no such run {rid}"
        if run.exit is not None:
            return "already finished"
        try:
            open(os.path.join(run.path, "stop"), "w").close()
        except OSError as exc:
            return f"could not signal stop: {exc}"
        threading.Thread(target=self._escalate, args=(run,), name="dc-escalate",
                         daemon=True).start()
        return "stopping (cooperative first; the process tree is killed after 10 s)"

    def _escalate(self, run):
        deadline = time.time() + KILL_AFTER_S
        while time.time() < deadline:
            time.sleep(0.25)
            run.refresh_meta()
            if run.exit is not None:
                return
        pid = (run.meta or {}).get("pid")
        if pid:
            RN.kill_tree(int(pid))
        if not os.path.exists(os.path.join(run.path, "exit.json")):
            RN._write_json(os.path.join(run.path, "exit.json"),
                           {"code": -9, "killed": True, "stopped": True,
                            "why": "killed: Stop did not end it within 10 s",
                            "ended": time.time()})

    def _reap(self):
        while True:
            time.sleep(1.0)
            with self.lock:
                items = list(self.procs.items())
            for run_dir, proc in items:
                code = proc.poll()
                if code is None:
                    continue
                time.sleep(1.0)
                path = os.path.join(run_dir, "exit.json")
                if os.path.isdir(run_dir) and not os.path.exists(path):
                    RN._write_json(path, {"code": code, "ended": time.time(),
                                          "why": "ended without writing its own "
                                                 "exit record (crashed or killed)"})
                with self.lock:
                    self.procs.pop(run_dir, None)

    # --- housekeeping -----------------------------------------------------

    def prune(self, keep=RN.KEEP_RUNS):
        """Keep the newest `keep` runs, plus the newest run of each self-test
        and each console check. A sweep of all 25 self-tests would otherwise
        push out every run somebody was looking at, and its own first results."""
        runs = STORE.list_runs(include_nested=False)
        done = [r for r in runs if r["status"] not in ("running", "starting")]
        seen, drop, other = set(), [], []
        for r in done:
            job = str(r.get("job") or "")
            if job.startswith("selftest:") or job == "check":
                key = (job, r["label"])
                (drop if key in seen else []).append(r)
                seen.add(key)
            else:
                other.append(r)
        for r in drop + other[keep:]:
            self._delete(r["id"])

    def delete_finished(self):
        n = 0
        for r in STORE.list_runs(include_nested=False):
            if r["status"] not in ("running", "starting"):
                self._delete(r["id"])
                n += 1
        return n

    def _delete(self, rid):
        path = os.path.join(RN.RUNS, rid)
        for _ in range(5):
            try:
                shutil.rmtree(path)
                return True
            except FileNotFoundError:
                return True
            except OSError:
                time.sleep(0.3)
        return False

    # --- all the self-tests, one after another ---------------------------

    def run_all_selftests(self):
        with self.lock:
            if self.batch and self.batch.get("running"):
                return "a self-test sweep is already running"
            mods = INV.selftest_modules()
            self.batch = {"running": True, "started": time.time(), "total": len(mods),
                          "results": {}, "current": None, "cancel": False}
        threading.Thread(target=self._batch, args=(mods,), name="dc-selftests",
                         daemon=True).start()
        return f"running {len(mods)} self-tests, one at a time"

    def cancel_batch(self):
        with self.lock:
            if self.batch and self.batch.get("running"):
                self.batch["cancel"] = True
                cur = self.batch.get("current")
        if cur:
            self.stop(cur)
        return "cancelling after the current test"

    def _batch(self, mods):
        for m in mods:
            with self.lock:
                if self.batch["cancel"]:
                    break
            rid, msg = self.start(f"selftest:{m}")
            if rid is None:
                self.batch["results"][m] = {"status": "not started", "why": msg}
                continue
            with self.lock:
                self.batch["current"] = rid
            lost_since = None
            while True:
                time.sleep(0.5)
                run = STORE.get(rid)
                if run is None:
                    continue
                if run.exit is not None:
                    break
                # A child that died without an exit record and was not ours to
                # reap would otherwise hold the sweep forever.
                if run.status() == "lost":
                    lost_since = lost_since or time.time()
                    if time.time() - lost_since > 30:
                        break
                else:
                    lost_since = None
            # The exit record can be read before the last lines of output are,
            # so the last line comes from the file, not from the parsed log.
            s = run.summary()
            self.batch["results"][m] = {"status": s["status"], "code": s["code"],
                                        "seconds": s["seconds"], "run": rid,
                                        "last": s.get("last") or ""}
        with self.lock:
            self.batch["running"] = False
            self.batch["current"] = None
            self.batch["ended"] = time.time()
            try:
                RN._write_json(SELFTESTS_JSON, self.batch)
            except OSError:
                pass

    def last_sweep(self):
        with self.lock:
            if self.batch:
                return dict(self.batch)
        try:
            with open(SELFTESTS_JSON, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None


JOBS = None


def jobs():
    global JOBS
    if JOBS is None:
        JOBS = Jobs()
    return JOBS
