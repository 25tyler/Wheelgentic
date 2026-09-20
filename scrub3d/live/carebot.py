"""scrub3d/live/carebot.py -- what the website's voice commands reach.

    python scrub3d/live/carebot.py --real --upside-down   # the camera, the real arms
    python scrub3d/live/carebot.py --dry                  # no arms: a recording, simulated joints
    python scrub3d/live/carebot.py --selftest

In carechair/.env:   ROBOT_MODE=live   ROBOT_BACKEND_URL=http://127.0.0.1:8770

WHAT IT IS
----------
The carechair website turns speech into one small command (showering, eating,
take_meds; start, repeat, stop, pause) and POSTs it to a robot backend. This is
that backend, for the two OpenYAM arms. It waits with the arms at their initial
position, folded at the person's front, and does one thing at a time:

  showering   the scrub (live_body.py --drive dimos, with run_openyam.ps1's
              settings) until "stop"; then the arms are drawn back, folded to
              the front, and it waits again
  eating, take_meds
              the bottle to the mouth and back (drink.py --pose, hands free);
              the arm ends where it rested, and it waits again
  stop, pause whatever runs is ended: the scrub draws back and folds away; a
              sip is broken off, the bottle levelled and put back

Each runs as its own process, as it does by hand, because each owns the one
camera and its own safety, and neither was written to share them. A request
while one runs is refused as busy, except stop, which always goes through.

It also puts the two views on the website rather than the desktop: it serves
the cartoon page (web/) and starts Rerun as a web viewer, which live_body.py
finds already listening and sends to (its serve-web probe).

WHAT IT ANSWERS (carechair's robot-adapter.js, docs/SEAM-B-ENDPOINTS.md)
  POST /api/task    {command_id, category, action, target, item, source}
  POST /api/stop    the same, action stop or pause
  GET  /api/status  what it is doing, and the last lines its task said
  GET  /api/vitals  no vitals sensor on the arms: the website has its own
  GET  /api/health

NOT YET RUN ON THE ARMS. Written the night of 2026-09-19 with no hardware to
hand. Proven: the wire against carechair's own adapter, the states, and both
tasks end to end with --dry. Unproven: the fold to the front after a scrub
(park(), which is arm_dimos's planned start pose), and a stop from here
reaching a real drink.py in time.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
S3D = os.path.dirname(HERE)
REPO = os.path.dirname(S3D)
LOGS = os.path.join(HERE, "drive_logs")
STOP_FILE = os.path.join(LOGS, "scrub.stop")
WATCH_STOP = os.path.join(LOGS, "watch.stop")
DIMS_FILE = os.path.join(LOGS, "dims.json")            # live_body.py's _share_dims writes it
KEYS_FILE = os.path.join(LOGS, "drink.keys")          # drink.py's Keys._tail reads it
SETTINGS = os.path.join(HERE, "run_openyam.ps1")      # the scrub's numbers, kept in one place
RIG = os.path.join(HERE, "live_rig_openyam.json")
RECORDING = os.path.join(S3D, "data", "live_rec_20260917f300")
if not os.path.isdir(RECORDING):     # recordings are pictures of a person, never in git:
    RECORDING = os.path.join(os.path.dirname(REPO), "thingy-scrub3d", "scrub3d", "data",
                             "live_rec_20260917f300")      # ... so --dry borrows the old repo's

PORT, PAGE_PORT, RERUN_PORT, RERUN_WEB_PORT = 8770, 8000, 9876, 9090
WS_PORT = 8765                        # web/main.js connects here: it is written into that file
PHASES = {"idle": "IDLE", "showering": "SCRUB", "drinking": "APPROACH", "stopping": "RETREAT",
          "parking": "RETREAT"}
SCRUB = ("showering",)
DRINK = ("eating", "take_meds")       # one motion for all of them: the bottle to the mouth


def scrub_settings():
    """The $env: lines of run_openyam.ps1, as a dict: whoever tunes the scrub
    there tunes it here."""
    out = {}
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            for line in f:
                m = re.match(r'\s*\$env:(\w+)\s*=\s*"([^"]*)"', line)
                if m:
                    out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


class Bot:
    """One task at a time. `state`: idle, showering, drinking, stopping, parking."""

    def __init__(self, a):
        self.a = a
        self.lock = threading.Lock()
        self.state, self.task, self.proc = "idle", None, None
        self.said = collections.deque(maxlen=12)       # the task's last lines
        self.seen = collections.OrderedDict()          # command_id -> reply (idempotency)
        self.since = time.time()
        self.watch = None                              # the live 3D render, between tasks
        os.makedirs(LOGS, exist_ok=True)

    # --- the live 3D render, always on -----------------------------------------
    def start_watch(self):
        """live_body.py with no arms: the sitter as the depth camera sees them,
        in the 3D view, whenever no scrub is running (a scrub is the same view
        WITH the arms, and there is one camera, so they take turns). The drink
        uses no camera and runs alongside it."""
        if self.a.no_watch or (self.watch is not None and self.watch.poll() is None):
            return
        if os.path.exists(WATCH_STOP):
            os.remove(WATCH_STOP)
        cmd, env = self._command("showering")
        cmd = [c for c in cmd if c not in ("--drive", "dimos")] + ["--no-arms"]
        env["SCRUB3D_STOP_FILE"] = WATCH_STOP
        self.watch = subprocess.Popen(cmd, cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop_watch(self):
        w, self.watch = self.watch, None
        if w is None or w.poll() is not None:
            return
        open(WATCH_STOP, "w").close()
        try:
            w.wait(timeout=12.0)
        except subprocess.TimeoutExpired:
            w.terminate()
            w.wait(timeout=5.0)

    # --- the wire -------------------------------------------------------------
    def handle_task(self, body, key=None):
        cid = str(body.get("command_id") or key or "")
        with self.lock:
            if cid and cid in self.seen:
                return 200, self.seen[cid]
        cat, act = body.get("category"), body.get("action")
        if act in ("stop", "pause"):
            return self.handle_stop(body)
        if cat not in SCRUB + DRINK or act not in ("start", "repeat"):
            return 400, {"status": "rejected", "reason": f"nothing here does {cat!r} {act!r}"}
        with self.lock:
            if self.state != "idle":
                rep = {"status": "refused", "reason": f"busy: {self.state}", "state": self.state}
                return 200, rep              # an answer, not a fault: the website says it
            self.state, self.task, self.since = (
                "showering" if cat in SCRUB else "drinking"), cat, time.time()
            self.said.clear()
            rep = {"status": "accepted", "accepted": True, "command_id": cid, "state": self.state}
            if cid:
                self.seen[cid] = rep
                while len(self.seen) > 64:
                    self.seen.popitem(last=False)
        threading.Thread(target=self._run, args=(cat,), daemon=True).start()
        return 200, rep

    def handle_stop(self, body=None):
        with self.lock:
            was, proc = self.state, self.proc
            if was in ("showering", "drinking"):
                self.state = "stopping"
        if was == "showering":
            open(STOP_FILE, "w").close()               # live_body.py's loop ends on it
        elif was == "drinking":
            with open(KEYS_FILE, "a", encoding="utf-8") as f:
                f.write("space\n")                     # drink.py: stop, level, put it back
        self.note(f"stop asked for while {was}")
        return 200, {"status": "stopping" if proc else "idle", "accepted": True, "was": was}

    def status(self):
        with self.lock:
            return {"status": "ok", "state": self.state, "task": self.task,
                    "for_s": round(time.time() - self.since, 1), "said": list(self.said),
                    "dry": bool(self.a.dry), "views": {
                        "cartoon": f"http://127.0.0.1:{PAGE_PORT}/",
                        "rerun": (f"http://127.0.0.1:{RERUN_WEB_PORT}/?url=rerun%2Bhttp%3A%2F%2F"
                                  f"127.0.0.1%3A{RERUN_PORT}%2Fproxy")}}

    # --- the tasks ------------------------------------------------------------
    def note(self, line):
        line = line.rstrip()
        if line and not re.match(r"^(W0000|I0000|INFO:|WARNING: All log)", line):
            self.said.append(line.strip())
            print(f"  [{self.state}] {line.strip()}", flush=True)

    def _command(self, cat):
        a, py = self.a, [sys.executable, "-u"]
        env = dict(os.environ, SCRUB3D_ARM="openyam", PYTHONUNBUFFERED="1")
        if cat in SCRUB:
            env.update(scrub_settings())
            env["SCRUB3D_STOP_FILE"] = STOP_FILE
            env["SCRUB3D_DIMS"] = DIMS_FILE
            cmd = py + [os.path.join(HERE, "live_body.py"), "--rig", RIG,
                        "--viewer-port", str(RERUN_PORT)]
            if a.dry:
                cmd += ["--replay", a.recording, "--seat-mm", "420"]
            else:
                cmd += ["--drive", "dimos"] + (["--upside-down"] if a.upside_down else [])
        else:
            cmd = py + [os.path.join(HERE, "drink.py"), "--pose"]
            cmd += ["--dry", "--auto"] if a.dry else ["--real"]
        if a.endpoint:
            env["SCRUB3D_DIMOS"] = a.endpoint
        return cmd, env

    def _run(self, cat):
        for f in (STOP_FILE,):
            if os.path.exists(f):
                os.remove(f)
        if cat in SCRUB:
            self.stop_watch()                          # one camera: the scrub takes it
        cmd, env = self._command(cat)
        self.note("starting: " + " ".join(os.path.basename(c) if c.endswith(".py") else c
                                          for c in cmd[2:]))
        try:
            proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    text=True, errors="replace")
        except OSError as exc:
            self.note(f"could not start: {exc}")
            with self.lock:
                self.state, self.task = "idle", None
            return
        with self.lock:
            self.proc = proc
        asked = None
        for line in proc.stdout:
            self.note(line)
            if self.state == "stopping" and asked is None:
                asked = time.monotonic()
        code = proc.wait()
        self.note(f"ended ({code})")
        with self.lock:
            self.proc = None
        if cat in SCRUB and not self.a.dry:
            # The scrub leaves the arms drawn back where they were. The initial
            # position is folded at the front, so put them there.
            with self.lock:
                self.state = "parking"
            self.note(park(self.a.endpoint))
        with self.lock:
            self.state, self.task, self.since = "idle", None, time.time()
        self.start_watch()
        self.note("waiting for the next request")

    def shutdown(self):
        self.handle_stop()
        self.stop_watch()
        with self.lock:
            proc = self.proc
        if proc is not None:
            try:
                proc.wait(timeout=45.0)
            except subprocess.TimeoutExpired:
                proc.terminate()


# --- what the cartoon is told ----------------------------------------------------------

class Feed:
    """The websocket the cartoon page (web/main.js) listens on, which
    py/scrubbot.py used to serve and nothing did once the OpenYAMs took over.

    Ten times a second: what the arms are doing (`phase`), the REAL arms'
    six joints each, read from the bridge, so the drawn arms move as the real
    ones do (`joints`, src "measured"), and the sitter's thirteen measured
    dimensions from the running scrub, so the cartoon is their size (`body`).
    The cartoon's own pose stays with the browser's camera: the scrub shares
    its joints twice a second, which would make the mirror jerk. --measured-pose
    sends them anyway.

    Read only, like py/armbridge.py: it asks the bridge for state and nothing
    else. An "estop" from the page is a stop."""

    def __init__(self, bot):
        self.bot, self.joints, self.clients = bot, None, set()
        threading.Thread(target=self._serve, daemon=True).start()
        if not bot.a.dry and bot.a.endpoint:
            threading.Thread(target=self._poll_bridge, daemon=True).start()

    def _poll_bridge(self):
        os.environ["SCRUB3D_ARM"] = "openyam"
        for p in (S3D, HERE):
            if p not in sys.path:
                sys.path.insert(0, p)
        link = None
        while True:
            try:
                if link is None:
                    from arm_dimos import Link
                    link = Link(self.bot.a.endpoint)
                rep = link.call({"op": "state"}, timeout=1.0)
                arms = rep.get("arms") or {}
                q = {f"a{i}": (arms.get(side) or {}).get("joints")
                     for i, side in enumerate(("left", "right"))}
                self.joints = q if rep.get("ok") and any(q.values()) else None
            except Exception:                                   # noqa: BLE001
                self.joints, link = None, None
                time.sleep(2.0)
            time.sleep(0.1)

    def message(self):
        m = {"phase": PHASES.get(self.bot.state, "IDLE")}
        if self.joints:
            m["joints"] = {"src": "measured", "arms": self.joints}
        try:
            if time.time() - os.stat(DIMS_FILE).st_mtime < 5.0:
                with open(DIMS_FILE, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("body"):
                    m["body"] = d["body"]
                if d.get("limbs") and self.bot.a.measured_pose:
                    m["limbs"] = d["limbs"]
        except (OSError, ValueError):
            pass
        return m

    def _serve(self):
        try:
            import asyncio
            import websockets
        except ImportError:
            print("  no 'websockets' package: the cartoon gets no live data", flush=True)
            return

        async def handler(ws):
            self.clients.add(ws)
            try:
                async for raw in ws:
                    try:
                        cmd = json.loads(raw).get("cmd")
                    except (ValueError, AttributeError):
                        continue
                    if cmd == "estop":
                        self.bot.handle_stop()
                        await ws.send(json.dumps({"ack": {"cmd": "estop", "ok": True}}))
            finally:
                self.clients.discard(ws)

        async def main():
            try:
                server = await websockets.serve(handler, "127.0.0.1", WS_PORT)
            except OSError as exc:
                print(f"  port {WS_PORT} is taken ({exc}): the cartoon gets no live data",
                      flush=True)
                return
            async with server:
                while True:
                    if self.clients:
                        raw = json.dumps(self.message(), allow_nan=False)
                        for ws in list(self.clients):
                            try:
                                await ws.send(raw)
                            except Exception:                   # noqa: BLE001
                                self.clients.discard(ws)
                    await asyncio.sleep(0.1)
        asyncio.run(main())


def park(endpoint=None):
    """Both arms to the initial position: folded at the person's front, claws
    shut, by arm_dimos's own planned start pose (one arm at a time, drawn back
    before it turns). -> what happened, in words. UNPROVEN on the arms from
    here: by hand the same call runs inside the live view."""
    os.environ["SCRUB3D_ARM"] = "openyam"
    for p in (S3D, HERE):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        import arm_dimos
        from arms_live import base_pose
        with open(RIG, encoding="utf-8") as f:
            rel = json.load(f)["arms"]
        hw = arm_dimos.Hardware.connect(endpoint, log=lambda *_: None)
        try:
            hw.place([base_pose(r) for r in rel[:2]])  # which way is the front: no seat needed
            hw._poll_once()
            hw.close_claws()
            rep = hw.home(speed=0.3)
        finally:
            hw.link.close()
        return ("the arms are folded at the front, claws shut" if rep.get("ok")
                else f"could not fold the arms to the front: {rep.get('error')}")
    except Exception as exc:                                    # noqa: BLE001
        return f"could not fold the arms to the front: {exc}"


# --- the views, on the website -------------------------------------------------------

def start_views(a):
    """The cartoon page over HTTP, and Rerun as a web viewer. -> the processes"""
    procs = []
    web = os.path.join(REPO, "web")
    if os.path.isdir(web):
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "http.server", str(PAGE_PORT), "--bind", "127.0.0.1",
             "-d", web], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    try:
        # The real binary, not pip's launcher, which orphans it when stopped.
        import importlib.util
        spec = importlib.util.find_spec("rerun_cli")
        exe = os.path.join(list(spec.submodule_search_locations)[0],
                           "rerun.exe" if os.name == "nt" else "rerun")
        procs.append(subprocess.Popen(
            [exe, "--serve-web", "--bind", "127.0.0.1", "--port", str(RERUN_PORT),
             "--web-viewer-port", str(RERUN_WEB_PORT), "--memory-limit", "2GiB"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    except Exception as exc:                                    # noqa: BLE001
        print(f"  Rerun's web viewer did not start ({exc}): the 3D view will open on the "
              f"desktop instead", flush=True)
    return procs


def make_handler(bot, key=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, code, obj):
            raw = json.dumps(obj, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _allowed(self):
            return not key or self.headers.get("Authorization") == f"Bearer {key}"

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/status":
                return self._send(200, bot.status())
            if path == "/api/health":
                return self._send(200, {"status": "ok"})
            if path == "/api/vitals":
                return self._send(200, {"status": "disconnected", "readings": None,
                                        "source": "no vitals sensor on the arms"})
            self._send(404, {"status": "not found"})

        def do_POST(self):
            if not self._allowed():
                return self._send(401, {"status": "unauthorized"})
            path = self.path.split("?")[0]
            if path == "/api/stop":                   # first, before anything can fail
                return self._send(*bot.handle_stop())
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 32768)) or b"{}")
                assert isinstance(body, dict)
            except (ValueError, AssertionError):
                return self._send(400, {"status": "rejected", "reason": "not a JSON object"})
            if path == "/api/task":
                return self._send(*bot.handle_task(body, self.headers.get("Idempotency-Key")))
            self._send(404, {"status": "not found"})
    return Handler


def self_test():
    a = argparse.Namespace(dry=True, upside_down=False, endpoint=None, recording=RECORDING,
                           measured_pose=False, no_watch=True)
    bot = Bot(a)
    s = scrub_settings()
    assert s.get("SCRUB3D_ARM") == "openyam" and "SCRUB3D_SPONGE_R_MM" in s, s
    print(f"  the scrub's {len(s)} settings are read from run_openyam.ps1")
    assert bot.handle_task({"category": "talk_to_me", "action": "none"})[0] == 400
    cmd, env = bot._command("showering")
    assert "live_body.py" in cmd[2] and "--replay" in cmd and env["SCRUB3D_STOP_FILE"]
    bot.a.dry = False
    cmd, _ = bot._command("showering")
    assert cmd[-2:] == ["--drive", "dimos"] and "--replay" not in cmd, cmd
    cmd, _ = bot._command("eating")
    assert cmd[-2:] == ["--pose", "--real"], cmd
    bot.a.dry = True
    # a drink, for real processes but simulated joints: accepted, busy, idle again
    code, rep = bot.handle_task({"command_id": "t1", "category": "take_meds", "action": "start"})
    assert code == 200 and rep["accepted"], rep
    assert bot.handle_task({"command_id": "t1", "category": "take_meds", "action": "start"})[1] is rep
    assert bot.handle_task({"command_id": "t2", "category": "showering", "action": "start"})[1]["status"] == "refused"
    end = time.monotonic() + 240
    while bot.status()["state"] != "idle" and time.monotonic() < end:
        time.sleep(0.5)
    st = bot.status()
    assert st["state"] == "idle" and any("back where it rested" in x for x in st["said"]), st
    print("  take_meds: accepted once, a second request refused as busy, the bottle went to "
          "the mouth and back (simulated joints), idle again")
    feed = Feed.__new__(Feed)
    feed.bot, feed.joints = bot, {"a0": [0.0] * 6, "a1": [0.1] * 6}
    with open(DIMS_FILE, "w", encoding="utf-8") as f:
        json.dump({"body": {"src": "depth", "mm": {"shoulders": 350}, "confidence": {}},
                   "limbs": {"mm": {}}}, f)
    m = feed.message()
    assert m["phase"] == "IDLE" and m["joints"]["src"] == "measured" and "body" in m, m
    assert "limbs" not in m
    os.remove(DIMS_FILE)
    print("  the cartoon's feed: phase, the real arms' joints and the measured body; the pose "
          "stays with the browser")
    print("  carebot: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description="the robot backend behind the carechair website")
    ap.add_argument("--real", action="store_true", help="yes, drive the REAL arms")
    ap.add_argument("--dry", action="store_true", help="no arms: a recording, simulated joints")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--upside-down", action="store_true", help="the camera is mounted upside down")
    ap.add_argument("--endpoint", default=os.environ.get("SCRUB3D_DIMOS"),
                    help="the dimOS bridge, host:port (default $SCRUB3D_DIMOS, else the one in "
                         "run_openyam.ps1)")
    ap.add_argument("--recording", default=RECORDING, help="--dry: what the scrub replays")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--rerun-port", type=int, default=RERUN_PORT)
    ap.add_argument("--rerun-web-port", type=int, default=RERUN_WEB_PORT)
    ap.add_argument("--page-port", type=int, default=PAGE_PORT)
    ap.add_argument("--measured-pose", action="store_true",
                    help="also send the cartoon the sitter's arm joints as the depth camera "
                         "places them (2 a second: jerky next to the browser's own camera)")
    ap.add_argument("--no-watch", action="store_true",
                    help="no live 3D render between tasks (it uses the camera)")
    ap.add_argument("--no-views", action="store_true", help="do not serve the cartoon or Rerun")
    a = ap.parse_args()
    if a.selftest:
        return self_test()
    if a.real == a.dry:
        raise SystemExit("say which: --real drives the arms, --dry drives nothing")
    g = globals()
    g["RERUN_PORT"], g["RERUN_WEB_PORT"], g["PAGE_PORT"] = a.rerun_port, a.rerun_web_port, a.page_port
    a.endpoint = a.endpoint or scrub_settings().get("SCRUB3D_DIMOS")
    bot = Bot(a)
    Feed(bot)
    views = [] if a.no_views else start_views(a)
    time.sleep(2.0)                                  # Rerun first, so the render finds it
    bot.start_watch()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(bot, os.environ.get("ROBOT_API_KEY")))
    print(f"  carebot on http://127.0.0.1:{a.port} ("
          + ("REAL ARMS at " + str(a.endpoint) if a.real else "dry: nothing real moves") + ")\n"
          f"  the cartoon: http://127.0.0.1:{PAGE_PORT}/   Rerun: {bot.status()['views']['rerun']}\n"
          f"  waiting for the website: shower, eat, drink, meds, stop", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("  Ctrl+C: ending what runs, then leaving", flush=True)
    finally:
        bot.shutdown()
        for p in views:
            p.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
