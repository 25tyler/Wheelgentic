"""tests/test_runsh.py — run.sh is the DEMO-DAY PATH and has never been run.

Proves: both children start, one signal kills BOTH, no orphans survive.
An orphaned http.server holding :8000 or a zombie scrubbot holding the
websocket port means the next launch silently serves stale code.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import watchdog; watchdog.arm(120)   # SIGALRM: a daemon-thread
# watchdog once let a test run for 3h21m and block a whole suite.
import os, signal, subprocess, sys, time, threading

# A DEDICATED PORT. Running run.sh on 8000 collided with the harness server
# that every browser test starts (tests/serve.py): run.sh's child could not
# bind, exited, and this test reported a bogus 'http.server child is up'
# failure. Its own port makes the harness irrelevant.
PORT = "8123"
os.chdir(os.path.expanduser("~/Wheelgentic"))
threading.Thread(target=lambda: (time.sleep(90), print("*** WATCHDOG ***"),
                 os._exit(3)), daemon=True).start()

fails = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: fails.append(label)

def procs(exclude=()):
    """Processes that run.sh is responsible for.

    EXCLUDES pids we started ourselves. tests/serve.py starts a shared web
    server for the browser tests; it is NOT a run.sh child, so run.sh cannot
    kill it -- but this test counted it as an orphan and failed. My fix for
    cross-test interference created a false failure in a different test.
    """
    out = subprocess.run(["ps", "-eo", "pid,command"], capture_output=True,
                         text=True).stdout
    keep = []
    for l in out.splitlines():
        if "ps -eo" in l or "grep" in l:
            continue
        if not (f"http.server {PORT}" in l or "py/scrubbot.py" in l):
            continue
        try:
            pid = int(l.split()[0])
        except (ValueError, IndexError):
            continue
        if pid in exclude:
            continue
        keep.append(l)
    return keep

# The suite runs its own http.server on 8000; run.sh starts another. Kill the
# suite's own server first or run.sh's child cannot bind and dies silently.
# Anything already serving :8000 before we start belongs to the harness, not
# to run.sh. Record it and exclude it from every orphan check.
_PRE = set()
for _l in procs():
    try: _PRE.add(int(_l.split()[0]))
    except (ValueError, IndexError): pass
if _PRE:
    print(f"  (excluding {len(_PRE)} pre-existing harness process(es): {sorted(_PRE)})")

print("=== PRE: no stale processes ===")
# Retry: a SIGKILL'd process lingers as a zombie until reaped, and an earlier
# manual pkill can race this. Poll rather than sleeping a fixed amount.
for _ in range(10):
    left = procs(_PRE)
    if not left:
        break
    for l in left:
        try: os.kill(int(l.split()[0]), signal.SIGKILL)
        except (ProcessLookupError, ValueError): pass
    time.sleep(0.4)
check("clean slate", not procs(_PRE), f"{len(procs(_PRE))} left")

# :8765 must be FREE or scrubbot cannot bind and exits — which looks exactly
# like run.sh failing to start it. Wait for the previous test's server to go.
import socket
def port_free(p):
    with socket.socket() as s_:
        s_.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: s_.bind(("127.0.0.1", p)); return True
        except OSError: return False
for _ in range(20):
    if port_free(8765): break
    time.sleep(0.4)
check("websocket port 8765 is free", port_free(8765))

print("\n=== START run.sh (replay mode, no camera/arm needed) ===")
env = dict(os.environ, REPLAY="recordings/good_run.jsonl",
           BROWSER_OFF="1", PORT=PORT)
# A FILE, NOT A PIPE. stdout=PIPE with nobody reading fills the OS buffer
# (~64KB) and the child blocks forever on write. Same trap that hung
# test_integration for 120s with no output.
_RUNSH_LOG = "/tmp/_runsh_test.log"
_rlog = open(_RUNSH_LOG, "w")
p = subprocess.Popen(["bash", "run.sh", "--headless"], env=env,
                     stdout=_rlog, stderr=subprocess.STDOUT,
                     text=True, preexec_fn=os.setsid)
time.sleep(5.0)
running = procs(_PRE)
print("   " + "\n   ".join(x[:100] for x in running) if running else "   (none)")
check("http.server child is up", any(f"http.server {PORT}" in l for l in running))
check("scrubbot child is up", any("py/scrubbot.py" in l for l in running))

import urllib.request
try:
    code = urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=3).status
except Exception as e:
    code = str(e)
check("page is actually served", code == 200, str(code))

print("\n=== ONE SIGNAL MUST KILL EVERYTHING ===")
# SIGTERM, not SIGINT. A non-interactive bash script started in the background
# IGNORES SIGINT (POSIX); measured with a minimal script, the trap never fires.
# Interactive ^C still works because the tty signals the whole foreground
# process group directly -- but a test must send what it can actually deliver.
os.killpg(os.getpgid(p.pid), signal.SIGTERM)
time.sleep(3.0)
left = procs(_PRE)
check("no orphaned children after ^C", not left,
      f"{len(left)} orphan(s): " + "; ".join(x[:60] for x in left))

# AND run.sh MUST REPORT ITS STATUS. Nothing here read the exit code, which is
# how two defects lived through 90+ suites. Measured on the
# unpatched script: with the page port FREE, a fatal scrubbot (camera denied)
# left run.sh polling past 14s with the page still answering 200 -- the
# launcher never returned and never said the demo was dead. With the port
# already held its own server died silently (started >/dev/null 2>&1, so the
# bind failure prints nothing), both children were then gone, and the script
# fell off its end reporting SUCCESS: rc=0 on camera denial, on a missing
# --replay file, and on argparse rejecting a flag. A wrapper reads that as a
# working demo. A clean SIGTERM shutdown -- this path -- must still be 0.
_rc = p.wait(timeout=15)
check("run.sh exits 0 on a clean SIGTERM shutdown", _rc == 0,
      f"rc={_rc} -- 0 expected; a non-zero here means cleanup now reports a "
      "fault on the normal demo-day teardown")
# Only meaningful if the HARNESS was not already serving :8000. When
# tests/serve.py has a server up (every browser test starts one), the port
# stays bound by design and this check would fail on a correct run.sh.
try:
    urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=2)
    served = True
except Exception:
    served = False
check(f"port {PORT} released", not served)

# hard cleanup regardless
for l in procs(_PRE):
    try: os.kill(int(l.split()[0]), signal.SIGKILL)
    except Exception: pass

print("\n" + "="*58)
if fails:
    print(f"  *** {len(fails)} FAILED: {fails}"); sys.exit(1)
print("  run.sh LIFECYCLE PASSED")
