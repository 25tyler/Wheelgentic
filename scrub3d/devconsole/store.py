"""devconsole/store.py -- run directories in, state the pages can read out.

ONE WRITER
----------
A single tailer thread reads the run directories every 200 ms and is the only
thing that changes this state. Page callbacks take a copy under a lock and never
touch the files, so a slow page cannot slow the tailing and two pages cannot race
each other.

FILES ARE OPENED, READ AND CLOSED EVERY TIME
-------------------------------------------
Windows refuses to delete a file that another process holds open, so holding
`events.jsonl` open for tailing would make run retention fail for exactly the
runs worth deleting. A partial last line is kept back until its newline arrives.

LAZY
----
The run list is built from `meta.json` and `exit.json` alone. A run's events are
parsed only once somebody looks at it, or while it is still running.
"""
import collections
import glob
import json
import os
import re
import threading
import time

from devconsole import probes as PR

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")

SERIES_MAX = 20000
LOG_MAX = 20000
VERDICT_MAX = 5000
SPANS_PER_STAGE = 60
SPANS_PER_FN = 30        # so a busy helper cannot push its caller's spans out
TAIL_S = 0.2

VERDICT_FNS = {
    "fleet.FleetGovernor.propose", "fleet.FleetGovernor._hold",
    "fleet.FleetGovernor.estop", "fleet.FleetGovernor.clear_estop",
    "fleet.FleetGovernor.link_lost", "fleet.FleetGovernor.link_restored",
    "fleet.FleetGovernor.home_order", "fleet.FleetGovernor.rotate_priority",
    "armlink.GovernedArm.set_target", "armlink.GovernedArm.health_tick",
    "armlink.GovernedArm.go_home", "serial.Serial.__init__",
    "session.Consent.__init__", "session.Consent.press",
    "session.Consent.start_motion", "session.Consent.finish",
    "session.Consent.revoke", "session.Consent.state",
    "session.Consent.may_move", "adapt.may_hand_over", "adapt.Adapter.commit",
}

# Case matters. The pipeline shouts its verdicts (REFUSED, FAIL, CANNOT JUDGE)
# and uses the same words in lower case inside explanations of a PASS ("the
# governor refused every proposal above the ceiling"), which is not a warning.
# A refusal is a correct outcome, so it is a warning, never an error.
_ERR = re.compile(r"Traceback|[A-Za-z]*Error\b|\bFAILED\b|\[ *FAIL *\]|Exception\b")
_WARN = re.compile(r"WARNING|Warning\b|\bwarn(ing)?\b|\bREFUSED?\b|NOT ALONE|"
                   r"CANNOT JUDGE|could not|unavailable|STALE|\[UNKNOWN\]")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _alive(meta):
    """Is the process that wrote this run still the one running under its pid?"""
    if not meta or not meta.get("pid"):
        return False
    try:
        import psutil
        p = psutil.Process(int(meta["pid"]))
        ct = meta.get("create_time")
        return ct is None or abs(p.create_time() - float(ct)) < 1.0
    except Exception:                                         # noqa: BLE001
        return False


class _Tail:
    """Read what was appended to a file since last time, whole lines only."""

    def __init__(self, path, binary=False):
        self.path = path
        self.pos = 0
        self.rest = b""

    def read_lines(self):
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.pos)
                data = fh.read()
        except OSError:
            return []
        if not data:
            return []
        self.pos += len(data)
        data = self.rest + data
        parts = re.split(rb"\r\n|\n|\r", data)
        self.rest = parts.pop()
        return [p.decode("utf-8", errors="replace") for p in parts]


class Stage:
    __slots__ = ("id", "calls", "spans", "errors", "refusals", "dropped",
                 "sum_ms", "max_ms", "p50", "p95", "running_since", "last",
                 "last_t", "missing", "recent", "fn_aggs", "last_by_fn", "by_fn")

    def __init__(self, sid):
        self.id = sid
        self.calls = self.spans = self.errors = self.refusals = self.dropped = 0
        self.sum_ms = self.max_ms = 0.0
        self.p50 = self.p95 = None
        self.running_since = None
        self.last = None
        self.last_t = None
        self.missing = set()
        self.recent = collections.deque(maxlen=SPANS_PER_STAGE)
        self.fn_aggs = {}
        self.last_by_fn = {}
        self.by_fn = {}


class Run:
    def __init__(self, path):
        self.path = path
        self.id = os.path.relpath(path, RUNS).replace("\\", "/")
        self.meta = None
        self.launch = None
        self.exit = None
        self.alive = False
        self.parsed = False
        self.ev_tail = _Tail(os.path.join(path, "events.jsonl"))
        self.out_tail = _Tail(os.path.join(path, "stdout.log"))
        self.stages = {s.id: Stage(s.id) for s in PR.STAGES}
        self.series = collections.defaultdict(lambda: collections.deque(maxlen=SERIES_MAX))
        self.logs = collections.deque(maxlen=LOG_MAX)
        self.verdicts = collections.deque(maxlen=VERDICT_MAX)
        self.frames = {}
        self.preflight = None
        self.cost = None
        self.entities = {}
        self.children = []
        self.events_seen = 0
        self.first_t = None
        self.last_t = None
        self.log_seq = 0
        self.tick_seq = 0
        self.last_tick_t = None
        self.fps_ema = None
        self.stops = []
        self.misc = collections.deque(maxlen=500)   # warn, gpu, dup, notes...
        self.installed = []
        self.search = _Search()
        self.versions = collections.Counter()
        self.touched = time.time()
        self._tail_size = -1
        self._tail_line = ""
        self._tail_final = False

    # --- identity -----------------------------------------------------

    def refresh_meta(self):
        if self.meta is None:
            self.meta = _read_json(os.path.join(self.path, "meta.json"))
        if self.launch is None:
            self.launch = _read_json(os.path.join(self.path, "launch.json"))
        if self.exit is None:
            self.exit = _read_json(os.path.join(self.path, "exit.json"))
        self.alive = self.exit is None and _alive(self.meta)

    @property
    def started(self):
        for src in (self.meta, self.launch):
            if src:
                return src.get("started") or src.get("launched")
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return 0.0

    @property
    def label(self):
        for src in (self.meta, self.launch):
            if src and src.get("label"):
                return src["label"]
        return os.path.basename(self.path)

    @property
    def script(self):
        for src in (self.meta, self.launch):
            if src and src.get("script"):
                return os.path.basename(str(src["script"]))
        return ""

    def status(self):
        if self.exit is None:
            if self.alive:
                return "running"
            if self.meta is None and self.launch is not None and \
                    time.time() - (self.launch.get("launched") or 0) < 20:
                return "starting"
            return "lost"
        if self.exit.get("killed"):
            return "killed"
        if self.exit.get("stopped"):
            return "stopped"
        return "ok" if self.exit.get("code") == 0 else "failed"

    def last_line(self):
        """The last non-blank line the run printed, read from the file's end."""
        if self._tail_final:
            return self._tail_line
        finished = self.exit is not None
        path = os.path.join(self.path, "stdout.log")
        try:
            size = os.path.getsize(path)
            if size != self._tail_size:
                with open(path, "rb") as fh:
                    fh.seek(max(0, size - 800))
                    chunk = fh.read()
                lines = [p.strip() for p in re.split(rb"\r\n|\n|\r", chunk)]
                last = next((p for p in reversed(lines) if p), b"")
                self._tail_line = last.decode("utf-8", errors="replace")[:240]
                self._tail_size = size
            # Read once more after the exit record exists, then never again.
            self._tail_final = finished
        except OSError:
            pass
        return self._tail_line

    def summary(self):
        ended = (self.exit or {}).get("ended")
        return {"id": self.id, "label": self.label, "script": self.script,
                "job": (self.meta or self.launch or {}).get("job"),
                "args": (self.meta or self.launch or {}).get("args"),
                "cwd": (self.meta or {}).get("cwd"),
                "status": self.status(), "started": self.started,
                "ended": ended, "code": (self.exit or {}).get("code"),
                "why": (self.exit or {}).get("why"),
                "seconds": (self.exit or {}).get("seconds"),
                "pid": (self.meta or {}).get("pid"),
                "torch_peak_mb": (self.exit or {}).get("torch_peak_mb"),
                "last": self.last_line(),
                "nested": "/children/" in self.id,
                "children": len(self.children)}

    # --- parsing --------------------------------------------------------

    def pump(self):
        changed = False
        for line in self.ev_tail.read_lines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            self._event(ev)
            changed = True
        for line in self.out_tail.read_lines():
            self._stdout(line)
            changed = True
        return changed

    def _log(self, text, level="info", source="stdout", t=None):
        self.log_seq += 1
        self.logs.append((self.log_seq, t or time.time(), level, source, text))
        self.versions["logs"] += 1

    def _stdout(self, line):
        level = "error" if _ERR.search(line) else ("warn" if _WARN.search(line) else "info")
        self._log(line, level)
        if self.search.feed(line):
            self.versions["search"] += 1

    def _event(self, ev):
        k = ev.get("k")
        t = ev.get("t")
        self.events_seen += 1
        if t:
            self.first_t = t if self.first_t is None else min(self.first_t, t)
            self.last_t = t if self.last_t is None else max(self.last_t, t)
        st = self.stages.get(ev.get("stage")) if ev.get("stage") else None

        if k == "span":
            fn = ev.get("fn")
            if st is None:
                return
            st.spans += 1
            st.last = ev
            st.last_t = t
            st.recent.append(ev)
            st.last_by_fn[fn] = ev
            hist = st.by_fn.get(fn)
            if hist is None:
                hist = st.by_fn[fn] = collections.deque(maxlen=SPANS_PER_FN)
            hist.append(ev)
            if st.running_since is not None and (t or 0) >= st.running_since:
                st.running_since = None
            if not ev.get("ok"):
                # A declared refusal raised on purpose: worth seeing, not red.
                self._log(f"{fn}: {ev.get('err')}",
                          "warn" if ev.get("refused") else "error", "probe", t)
            if fn in VERDICT_FNS:
                self.verdicts.append(ev)
                self.versions["verdicts"] += 1
            if fn == "main.preflight" and ev.get("sum"):
                self.preflight = ev["sum"].get("checks")
                self.versions["preflight"] += 1
            self.versions["stages"] += 1
            self.versions[f"fn:{fn}"] += 1
        elif k == "start":
            if st is not None:
                st.running_since = t
                self.versions["stages"] += 1
        elif k == "agg":
            if st is None:
                return
            st.fn_aggs[ev["fn"]] = ev
            aggs = list(st.fn_aggs.values())
            st.calls = sum(a.get("n", 0) for a in aggs)
            st.errors = sum(a.get("err", 0) for a in aggs)
            st.refusals = sum(a.get("ref", 0) for a in aggs)
            st.dropped = sum(a.get("drop", 0) for a in aggs)
            st.sum_ms = sum(a.get("sum", 0.0) for a in aggs)
            st.max_ms = max((a.get("max", 0.0) for a in aggs), default=0.0)
            main = max(aggs, key=lambda a: a.get("n", 0))
            st.p50, st.p95 = main.get("p50"), main.get("p95")
            self.versions["stages"] += 1
        elif k == "tick":
            self._tick(ev)
        elif k == "frame":
            self.frames[ev.get("name")] = ev.get("n")
            self.versions["frames"] += 1
        elif k == "miss":
            if st is not None:
                st.missing.add(ev.get("fn"))
            self._log(f"probe missing: {ev.get('fn')}", "error", "probe", t)
            self.versions["stages"] += 1
        elif k == "cost":
            self.cost = ev
            self.versions["cost"] += 1
        elif k == "entities":
            self.entities = ev.get("counts") or {}
            self.versions["entities"] += 1
        elif k == "child":
            self.children.append(ev.get("dir"))
            self.versions["children"] += 1
        elif k == "exit":
            self.exit = self.exit or {kk: vv for kk, vv in ev.items()
                                      if kk not in ("k", "seq")}
            self.versions["exit"] += 1
        elif k == "stop":
            self.stops.append(ev)
            self._log(f"stop: {ev.get('phase')}", "warn", "console", t)
        elif k == "installed":
            self.installed.append(ev)
        elif k in ("warn", "thread_exc", "note", "writer_error", "dup",
                   "mismatch", "gpu", "run"):
            self.misc.append(ev)
            self.versions["misc"] += 1
            if k == "warn":
                self._log(f"warning ({ev.get('category')}) at {ev.get('where')}: "
                          f"{ev.get('message')}", "warn", "python", t)
            elif k in ("thread_exc", "writer_error", "mismatch"):
                self._log(f"{k}: {ev.get('err') or ev.get('what')}", "error", "probe", t)
            elif k == "dup":
                self._log(f"module imported twice: {ev.get('module')} "
                          f"({ev.get('copies')} copies)", "warn", "probe", t)
            elif k == "note":
                self._log(f"note: {ev.get('what')} {ev.get('err') or ''}", "info",
                          "probe", t)

    def _tick(self, ev):
        self.tick_seq += 1
        n = ev.get("n") or self.tick_seq
        t = ev.get("t")
        s = self.series
        if self.last_tick_t is not None and t and t > self.last_tick_t:
            inst = 1.0 / (t - self.last_tick_t)
            self.fps_ema = inst if self.fps_ema is None else 0.8 * self.fps_ema + 0.2 * inst
            s["feed/fps"].append((n, t, round(self.fps_ema, 2)))
        self.last_tick_t = t
        for key, val in (ev.get("v") or {}).items():
            if isinstance(val, (int, float)):
                s[key].append((n, t, val))
        for key, val in (ev.get("ms") or {}).items():
            s[f"ms/{key}"].append((n, t, val))
        if ev.get("pm") is not None:
            s["feed/produce_ms"].append((n, t, ev["pm"]))
        if ev.get("cm") is not None:
            s["feed/consume_ms"].append((n, t, ev["cm"]))
        self.versions["series"] += 1

    # --- reading ------------------------------------------------------------

    def stage_status(self, sid):
        st = self.stages[sid]
        spec = PR.stage_index()[sid]
        finished = self.exit is not None or not self.alive
        if sid == "search":
            return self.search.status(finished) if self.search.seen else (
                "not called" if finished else "idle")
        if st.missing and not (st.spans or st.calls):
            return "probe missing"
        if st.running_since is not None and not finished:
            return "running"
        last = st.last
        if last is None:
            if not spec.targets:
                return "n/a"
            return "not called" if finished else "idle"
        if not finished and last.get("t") and time.time() - last["t"] < 3.0 \
                and st.calls > 5:
            return "running"
        if last.get("refused"):
            return "refused"
        if not last.get("ok"):
            return "failed"
        return "done"


class _Search:
    """plan_rig, followed through its own output lines."""

    def __init__(self):
        self.seen = False
        self.phase = None
        self.samples_total = None
        self.sampled = None
        self.best = None
        self.candidate = None
        self.points = []          # (t, candidate, sweep, arm, score, coverage, imbalance)
        self.reports = {}
        self.worth = None
        self.versus = None
        self.wrote = None
        self.mounts = {}
        self.started = None
        self.done = False
        self._re = {k: re.compile(p) for k, (p, _) in PR.PLAN_RIG_PATTERNS.items()}

    def feed(self, line):
        r = self._re
        now = time.time()
        m = r["header"].search(line)
        if m:
            self.seen = True
            self.started = now
            self.samples_total = int(m.group(1))
            self.phase = "sampling"
            return True
        if not self.seen and not r["report"].search(line):
            return False
        m = r["sampled"].search(line)
        if m:
            self.sampled = (int(m.group(1)), int(m.group(2)))
            if m.group(4):
                self.best = {"score": float(m.group(4)), "coverage": float(m.group(5))}
            self.points.append((now, None, None, None,
                                float(m.group(4)) if m.group(4) else None,
                                float(m.group(5)) if m.group(5) else None, None))
            return True
        if r["rerank"].search(line):
            self.phase = "re-ranking the shortlist across the envelope"
            return True
        m = r["candidate"].search(line)
        if m:
            self.candidate = int(m.group(1))
            self.phase = f"refining candidate {self.candidate}"
            return True
        m = r["start"].search(line)
        if m:
            self.points.append((now, self.candidate, -1, None, float(m.group(1)),
                                float(m.group(2)), float(m.group(3))))
            return True
        m = r["sweep"].search(line)
        if m:
            self.points.append((now, self.candidate, int(m.group(1)), int(m.group(2)),
                                float(m.group(3)), float(m.group(4)), float(m.group(5))))
            return True
        m = r["mount"].search(line)
        if m:
            self.phase = "verifying at full fidelity"
            self.mounts[int(m.group(1))] = [int(m.group(2)), int(m.group(3)),
                                            int(m.group(4)), float(m.group(5))]
            return True
        m = r["report"].search(line)
        if m:
            self.seen = True
            self.reports[m.group(1).strip()] = {
                "coverage": float(m.group(2)), "imbalance": float(m.group(3)),
                "phases": int(m.group(4)), "camera_blocked": float(m.group(5)),
                "per_arm": m.group(6)}
            return True
        m = r["worth"].search(line)
        if m:
            self.worth = float(m.group(1))
            return True
        m = r["versus"].search(line)
        if m:
            self.versus = {"new": float(m.group(1)), "old": float(m.group(2)),
                           "decision": m.group(3)}
            return True
        m = r["wrote"].search(line)
        if m:
            self.wrote = m.group(1)
            return True
        if line.strip() == "OK" and self.seen:
            self.done = True
            self.phase = "done"
            return True
        return False

    def status(self, finished):
        if self.done:
            return "done"
        return "running" if not finished else "failed"

    def snapshot(self):
        return {k: getattr(self, k) for k in (
            "seen", "phase", "samples_total", "sampled", "best", "candidate",
            "points", "reports", "worth", "versus", "wrote", "mounts",
            "started", "done")}


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.runs = {}
        self._last_scan = 0.0
        self._watch = set()
        self._stop = threading.Event()
        self._thread = None

    # --- lifecycle --------------------------------------------------------

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="dc-tailer",
                                            daemon=True)
            self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:                          # noqa: BLE001
                print(f"[devconsole] tailer: {exc!r}", flush=True)
            time.sleep(TAIL_S)

    def tick(self):
        now = time.time()
        if now - self._last_scan > 2.0:
            self._scan()
            self._last_scan = now
        with self.lock:
            runs = list(self.runs.values())
        for run in runs:
            if run.exit is None or run.meta is None or run.launch is None:
                run.refresh_meta()
            want = run.alive or run.id in self._watch or (
                run.parsed and run.exit is None)
            if not want:
                continue
            with self.lock:
                if run.pump():
                    run.touched = now
                run.parsed = True

    def _scan(self):
        if not os.path.isdir(RUNS):
            return
        found = set()
        for d in glob.glob(os.path.join(RUNS, "*")):
            if os.path.isdir(d):
                found.add(d)
                for c in glob.glob(os.path.join(d, "children", "*")):
                    if os.path.isdir(c):
                        found.add(c)
        with self.lock:
            for d in found:
                rid = os.path.relpath(d, RUNS).replace("\\", "/")
                if rid not in self.runs:
                    run = Run(d)
                    run.refresh_meta()
                    self.runs[rid] = run
            for rid in list(self.runs):
                if not os.path.isdir(self.runs[rid].path):
                    del self.runs[rid]
                    self._watch.discard(rid)

    # --- reading ----------------------------------------------------------

    def list_runs(self, include_nested=True):
        """Newest first, each run followed by the runs it started, oldest first."""
        self._scan_if_stale()
        with self.lock:
            every = [r.summary() for r in self.runs.values()
                     if include_nested or "/children/" not in r.id]
        kids = {}
        for s in every:
            if s["nested"]:
                kids.setdefault(s["id"].rsplit("/children/", 1)[0], []).append(s)
        for group in kids.values():
            group.sort(key=lambda s: s["started"] or 0)
        out = []

        def place(s):
            out.append(s)
            for k in kids.get(s["id"], ()):
                place(k)
        top = [s for s in every if not s["nested"]]
        top.sort(key=lambda s: s["started"] or 0, reverse=True)
        for s in top:
            place(s)
        if len(out) < len(every):              # a child whose parent is gone
            seen = {s["id"] for s in out}
            out += [s for s in every if s["id"] not in seen]
        return out

    def _scan_if_stale(self):
        if time.time() - self._last_scan > 2.0:
            self._scan()
            self._last_scan = time.time()

    def has(self, rid):
        self._scan_if_stale()
        with self.lock:
            return rid in self.runs

    def process_alive(self, rid):
        """Is the process writing this run still there, exit record or not?

        A run's exit record is written before the interpreter shuts down, and
        Rerun flushes a recording's last bytes at shutdown. Whoever reads the
        recording waits for the process, not the record.
        """
        with self.lock:
            run = self.runs.get(rid)
            if run is None:
                return False
            meta, launch = run.meta, run.launch
        if meta is None:
            return bool(launch) and time.time() - (launch.get("launched") or 0) < 20
        return _alive(meta)

    def latest(self):
        runs = self.list_runs(include_nested=False)
        return runs[0]["id"] if runs else None

    def get(self, rid):
        """The run, parsed up to now. Watching it keeps it parsed."""
        if not rid:
            return None
        self._scan_if_stale()
        with self.lock:
            run = self.runs.get(rid)
            if run is None:
                return None
            if rid not in self._watch:
                self._watch.add(rid)
                if len(self._watch) > 6:
                    oldest = sorted(self._watch, key=lambda r: self.runs[r].touched
                                    if r in self.runs else 0)[0]
                    if oldest != rid:
                        self._watch.discard(oldest)
            if not run.parsed:
                run.refresh_meta()
                run.pump()
                run.parsed = True
            return run

    def version(self, rid, section):
        with self.lock:
            run = self.runs.get(rid)
            # A list, because the page hands it back as JSON and a tuple would
            # never compare equal to what came back.
            return None if run is None else [run.versions[section], run.status()]

    def stage_table(self, rid):
        run = self.get(rid)
        if run is None:
            return []
        with self.lock:
            rows = []
            for spec in PR.STAGES:
                st = run.stages[spec.id]
                rows.append({
                    "id": spec.id, "group": spec.group, "label": spec.label,
                    "what": spec.what, "status": run.stage_status(spec.id),
                    "calls": max(st.calls, st.spans), "spans": st.spans,
                    "errors": st.errors, "refusals": st.refusals,
                    "dropped": st.dropped, "sum_ms": round(st.sum_ms, 1),
                    "max_ms": round(st.max_ms, 2), "p50": st.p50, "p95": st.p95,
                    "last": st.last, "missing": sorted(st.missing),
                    "targets": [t.key for t in spec.targets],
                    "fns": {fn: {"n": a.get("n"), "p50": a.get("p50"),
                                 "p95": a.get("p95"), "max": a.get("max"),
                                 "ref": a.get("ref"), "err": a.get("err"),
                                 "drop": a.get("drop")}
                            for fn, a in st.fn_aggs.items()},
                })
            return rows

    def stage_detail(self, rid, sid):
        run = self.get(rid)
        if run is None or sid not in run.stages:
            return None
        with self.lock:
            st = run.stages[sid]
            return {"recent": list(st.recent), "last_by_fn": dict(st.last_by_fn),
                    "fn_aggs": dict(st.fn_aggs), "missing": sorted(st.missing)}

    def last_span(self, rid, fn):
        run = self.get(rid)
        if run is None:
            return None
        t = PR.BY_KEY.get(fn)
        if t is None:
            return None
        with self.lock:
            return run.stages[t.stage].last_by_fn.get(fn)

    def spans(self, rid, fn, limit=500):
        run = self.get(rid)
        if run is None:
            return []
        t = PR.BY_KEY.get(fn)
        if t is None:
            return []
        with self.lock:
            return list(run.stages[t.stage].by_fn.get(fn, ()))[-limit:]

    def series(self, rid, names, after_n=None):
        run = self.get(rid)
        if run is None:
            return {}
        with self.lock:
            out = {}
            for name in names:
                data = run.series.get(name)
                if not data:
                    continue
                pts = list(data) if after_n is None else [p for p in data if p[0] > after_n]
                out[name] = pts
            return out

    def series_stats(self, rid, names):
        """-> {name: {last, mean, min, max, n}} for each series that has data.

        For pages that want a number, not a chart: series() copies whole
        series, and these can hold 20k points each.
        """
        run = self.get(rid)
        if run is None:
            return {}
        out = {}
        with self.lock:
            for name in names:
                data = run.series.get(name)
                if not data:
                    continue
                vals = [p[2] for p in data]
                out[name] = {"last": vals[-1], "mean": sum(vals) / len(vals),
                             "min": min(vals), "max": max(vals), "n": len(vals),
                             "t": data[-1][1]}
        return out

    def tail_logs(self, rid, n=12):
        """The run's last `n` log lines, oldest first."""
        run = self.get(rid)
        if run is None:
            return []
        with self.lock:
            logs = run.logs
            return [logs[i] for i in range(max(0, len(logs) - n), len(logs))]

    def series_names(self, rid):
        run = self.get(rid)
        if run is None:
            return []
        with self.lock:
            return sorted(run.series)

    def verdicts(self, rid, limit=2000):
        run = self.get(rid)
        if run is None:
            return []
        with self.lock:
            return list(run.verdicts)[-limit:]

    def logs(self, rid, after=0, limit=3000):
        run = self.get(rid)
        if run is None:
            return []
        with self.lock:
            return [l for l in run.logs if l[0] > after][-limit:]

    def snapshot(self, rid):
        """Everything small about a run, for the tabs that need a bit of each."""
        run = self.get(rid)
        if run is None:
            return None
        with self.lock:
            return {"summary": run.summary(), "meta": run.meta, "exit": run.exit,
                    "launch": run.launch, "preflight": run.preflight,
                    "cost": run.cost, "entities": dict(run.entities),
                    "frames": dict(run.frames), "children": list(run.children),
                    "misc": list(run.misc), "stops": list(run.stops),
                    "installed": list(run.installed),
                    "search": run.search.snapshot(),
                    "events": run.events_seen, "first_t": run.first_t,
                    "last_t": run.last_t, "path": run.path}


STORE = Store()
