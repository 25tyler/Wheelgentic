"""py/careapi.py -- seam B: the three HTTP endpoints Crystal's UI already calls.

    start(scrubbot_module, port=8770)

WHY THIS FILE EXISTS
--------------------
`docs/LIVE-3D-PLAN.md` §2 calls this "the single biggest gap in the product".
Her `robot-adapter.js` has always POSTed `{command_id, category, action,
target, item, source}` to `/api/task`, POSTed the same shape to `/api/stop`,
and GET `/api/vitals`. Her half is built. Our half did not exist, so
`ROBOT_MODE` stayed `demo` and nothing was ever plugged into it.

THE SHAPE OF THE SEAM IS THE SAFETY ARGUMENT, NOT A PREFERENCE
---------------------------------------------------------------
Plan §3: "the safety reasoning lives on our side. If her UI reached the arms
directly, every command would bypass it. One door into the machine, and the
guard stands in it." So every task that arrives here goes through
`py/governor.py` BEFORE anything downstream can see it. A refused task does
not reach the FSM at all -- it never sets `_ARM_REQUEST`, so no consent is
stamped and no cycle can start from it.

WHY A REFUSAL IS HTTP 200 AND NOT 4xx -- READ BEFORE "FIXING" IT
-----------------------------------------------------------------
Her `requestJson` (voice-providers.js:4-14) throws on any non-2xx and
replaces the body with a fixed string: "Robot backend request failed (HTTP
N). Check its key, access and quota." The governor's own words -- "above the
head ceiling", "structure would come inside D_BODY" -- are DESTROYED by that
path. A refusal is a successful safety decision, not a transport failure, so
it is 200 with `status: "refused"` and the reason verbatim in the body.

The statuses, matching the plan's "accepted, refused, or could not be
attempted":

    accepted     the governor cleared it and the FSM has it
    refused      the governor said no. `reason` is fleet.py's own wording
    unavailable  we could not attempt it (no arm path wired yet)

Non-2xx is reserved for what it means to her code: the request itself was
malformed or the method was wrong.

IDEMPOTENCY -- WHY IT IS FIRST, BEFORE THE GOVERNOR
-----------------------------------------------------
robot-adapter.js:22 states the rule in its own comment: "No automatic
retries: a lost response must not duplicate a physical task." She also sends
the id twice, as `command_id` in the body and as the `Idempotency-Key`
header. If a response is lost in transit and the command is ever re-sent,
replaying the STORED verdict is the only answer that cannot move an arm
twice. So the cache is consulted before validation and before the governor:
a repeat is not a new decision, it is the old decision said again.

The store is bounded (_SEEN_MAX) because this process runs for hours on
stage and an unbounded dict fed by an external client is a slow leak.

/api/stop AND WHY IT SHARES NOTHING
-------------------------------------
Plan §5: "This must work even when everything else is broken." So the stop
handler touches no lock, no governor, no config and no cache. It sets
`_ESTOP_REQUEST`, which `remote_control_loop` already drains in its own
thread at 20Hz and where ESTOP already wins over every other request
(scrubbot.py:1729-1731). That is the SAME path the projector's SPACE key and
the page's estop button take -- not a second one. A stop must never be able
to be blocked by a task holding something.

A stop is also NOT idempotency-cached. Replaying a cached "ok" for a stop
would mean a re-sent stop did not stop anything, which inverts the safety
direction of the whole mechanism. Stopping twice is free; stopping zero
times is not.

/api/vitals
-------------
Returns `{"status": "disconnected", "readings": null}`. Plan §7: "No vitals
sensor exists." Her Vitals page already renders that honestly as an em dash
and "No readings yet" (robot-adapter.js:27 returns the identical shape in
demo mode). A plausible-looking number in a care product is the worst bug
available, so this is not a stub waiting to be filled with one -- it is the
correct output until a sensor is wired.

THE TARGET COORDINATES, AND WHAT THEY HONESTLY ARE
----------------------------------------------------
The governor judges a POINT, and her vocabulary is words: `left_arm`,
`right_arm`, `both_arms`, `body` (voice-commands.js commandSchema). The
translation lives in TASK_POINTS below and every entry is derived from a
number already in this repo -- py/arm.py's HOME and its BOX -- rather than
typed fresh. `above_head` is not in her vocabulary; it exists only so the
refusal path can be exercised without hardware, and it is marked as such.

This is the honest limit of what this file claims: it proves a COMMAND
passes the guard. It does not claim the arm moved. Plan §7: "No motor has
been observed moving."
"""
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Where each of her words points, in the arm's own base frame, mm. The
# governor works in that frame directly (py/governor.py: "the demo's world IS
# the arm's base frame"), so these pass straight through with no transform to
# get wrong.
#
# DERIVED, NOT TYPED. py/arm.py HOME is (235.11, 0, 234.79) and its BOX is
# x 120..420, y -260..260, z 25..380. Left and right are HOME's x and z with
# y taken to either side, well inside the box; `body` and `none` are HOME
# itself. The point of these is to be a real, reachable, judgeable target --
# not a guess at where a person's arm is, which only vision can say.
HOME_X, HOME_Z = 235.0, 235.0
TASK_POINTS = {
    "left_arm": (HOME_X, 150.0, HOME_Z),
    "right_arm": (HOME_X, -150.0, HOME_Z),
    "both_arms": (HOME_X, 0.0, HOME_Z),
    "body": (HOME_X, 0.0, HOME_Z),
    "none": (HOME_X, 0.0, HOME_Z),
    # NOT ONE OF HER TARGETS. The governor's head ceiling sits at BOX zmax
    # (380mm), so this is above it and is refused by geometry. It exists to
    # exercise the refusal path end to end without hardware and without
    # relaxing anything. Keep it: a guard nobody can demonstrate refusing is
    # a guard nobody can trust.
    "above_head": (HOME_X, 0.0, 520.0),
}

# Her enums, copied from voice-commands.js commandSchema. Validated here too
# because this endpoint is reachable by anything that can open a socket, not
# only by her server -- a backend that trusts its caller to have validated is
# a backend with no validation.
CATEGORIES = ("eating", "showering", "take_meds", "talk_to_me")
ACTIONS = ("start", "repeat", "pause", "stop", "none")

_SEEN_MAX = 512                  # bounded: this runs for hours, fed remotely
_seen = {}                       # command_id -> the response we already gave
_seen_order = []                 # insertion order, for the bounded eviction
_seen_lock = threading.Lock()

_SB = None                       # the scrubbot module, injected by start()
_started_at = 0.0


def _remember(command_id, response):
    """Store one verdict under its command_id. Oldest out past _SEEN_MAX."""
    with _seen_lock:
        if command_id not in _seen:
            _seen_order.append(command_id)
        _seen[command_id] = response
        while len(_seen_order) > _SEEN_MAX:
            _seen.pop(_seen_order.pop(0), None)


def _recall(command_id):
    with _seen_lock:
        return _seen.get(command_id)


def handle_task(body, idem_header=None):
    """The whole /api/task decision. Pure of HTTP, so it is testable. -> dict

    ORDER IS THE SAFETY PROPERTY HERE AND IT IS NOT INTERCHANGEABLE:
      1. idempotency  -- a repeat replays, it never re-decides
      2. validation   -- her enums, checked on our side too
      3. the governor -- BEFORE anything downstream exists
      4. the FSM      -- reached only by what step 3 cleared
    """
    if not isinstance(body, dict):
        return 400, {"status": "rejected", "reason": "body must be a JSON object"}

    command_id = body.get("command_id")
    if not isinstance(command_id, str) or not command_id.strip():
        return 400, {"status": "rejected", "reason": "command_id is required"}
    command_id = command_id.strip()

    # Her adapter sends the id twice (body + Idempotency-Key). If both are
    # present and disagree, we cannot know which task this is, and guessing
    # is how one command becomes two physical actions.
    if idem_header and idem_header != command_id:
        return 400, {"status": "rejected",
                     "reason": "Idempotency-Key does not match command_id"}

    # STEP 1. Before validation, before the governor. A replay is the old
    # answer repeated, not a new decision -- see the module docstring.
    seen = _recall(command_id)
    if seen is not None:
        out = dict(seen)
        out["duplicate"] = True
        return 200, out

    category = body.get("category")
    action = body.get("action")
    target = body.get("target")
    if category not in CATEGORIES or action not in ACTIONS:
        return 400, {"status": "rejected",
                     "reason": "category or action is not one we know"}
    if target not in TASK_POINTS:
        return 400, {"status": "rejected",
                     "reason": "target is not one we know"}

    # 'none' and the control verbs never reach the arm path. stop/pause are
    # routed to /api/stop by her adapter (robot-adapter.js:17) and cannot
    # normally arrive here; if one does, it must not be treated as a task.
    if action in ("none", "stop", "pause"):
        out = {"status": "unavailable", "command_id": command_id,
               "reason": f"action '{action}' is not a task",
               "governor": None}
        _remember(command_id, out)
        return 200, out

    x, y, z = TASK_POINTS[target]

    # STEP 3. THE GUARD IN THE DOOR. Nothing below this line runs for a task
    # the governor refused -- that is the entire reason our backend sits
    # between her UI and Justin's arms (plan §3).
    gov = getattr(_SB, "GOV", None) if _SB else None
    if gov is None:
        # No governor object at all means scrubbot has not finished starting.
        # Refusing is the only safe answer: an unjudged task must never reach
        # the FSM just because the judge was late.
        out = {"status": "refused", "command_id": command_id,
               "reason": "the safety governor is not up yet",
               "governor": {"state": "absent", "checked": False}}
        _remember(command_id, out)
        return 200, out

    verdict = gov.check(x, y, z, t=time.time())
    detail = verdict.as_event()
    detail["state"] = gov.state
    detail["target_mm"] = [x, y, z]

    # NOT COSMETIC -- THIS ONE BROKE EVERY ACCEPTED TASK. `clearance_mm` is
    # +inf whenever no limb has been seen yet, which is the normal state
    # before the camera finds anybody. Python's json.dumps writes that as the
    # bare token `Infinity`, which is not valid JSON: Node's JSON.parse throws
    # on it, her requestJson turns the throw into PROVIDER_ERROR, and the
    # accepted verdict is destroyed on the wire. Measured with
    # `node -e 'JSON.parse(...)'` -> "Unexpected token 'I'".
    #
    # null, not a big number: "nothing to be close to" is honestly absent,
    # and a large float here would read as a measured distance to a person
    # nobody has seen. governor.as_event() keeps the float because the
    # websocket has Python at both ends; this is the boundary that cannot.
    clearance = detail.get("clearance_mm")
    if clearance is not None and not math.isfinite(clearance):
        detail["clearance_mm"] = None

    if not verdict.allowed:
        # REFUSED. The FSM is not touched. `reason` is fleet.py's own wording
        # passed through verbatim so the UI shows what the guard said rather
        # than a paraphrase of it.
        out = {"status": "refused", "command_id": command_id,
               "reason": verdict.reason or "the safety governor refused it",
               "governor": detail}
        _remember(command_id, out)
        return 200, out

    # STEP 4. Cleared, so the FSM may have it. `_ARM_REQUEST` is the SAME
    # latch the projector's 's' key sets, drained by remote_control_loop,
    # which stamps consent and refuses while estopped (scrubbot.py:1755+).
    # Setting the existing flag rather than arming directly keeps one arming
    # path: a second one would skip the estop refusal that path enforces.
    accepted = False
    request = getattr(_SB, "_ARM_REQUEST", None) if _SB else None
    if request is not None:
        request.set()
        accepted = True

    if not accepted:
        out = {"status": "unavailable", "command_id": command_id,
               "reason": "the task path is not running",
               "governor": detail}
        _remember(command_id, out)
        return 200, out

    out = {"status": "accepted", "command_id": command_id,
           "category": category, "action": action, "target": target,
           "governor": detail}
    _remember(command_id, out)
    return 200, out


def handle_stop():
    """The estop path. Shares NOTHING -- no lock, no cache, no governor.

    Sets the flag `remote_control_loop` already drains, where ESTOP already
    wins over a clear in the same tick. Deliberately not idempotency-cached:
    a replayed "ok" for a stop would mean a re-sent stop stopped nothing.
    """
    request = getattr(_SB, "_ESTOP_REQUEST", None) if _SB else None
    if request is None:
        return 503, {"status": "unavailable",
                     "reason": "the stop path is not running"}
    request.set()
    return 200, {"status": "stopping", "mode": "estop"}


def handle_vitals():
    """No sensor is wired. Saying so is the correct output -- plan §7.

    The same shape her adapter returns in demo mode (robot-adapter.js:27), so
    her Vitals page renders it the way it already renders no readings. Do not
    put a number here until a sensor exists to have measured it.
    """
    return 200, {"status": "disconnected", "readings": None,
                 "source": "no vitals sensor is wired"}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # her fetch reuses the connection
    server_version = "careapi"

    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler logs every request to stderr, which on stage
        # buries the FSM's own lines. Keep the one line that matters.
        return

    def _send(self, status, payload):
        # allow_nan=False MAKES A NON-JSON NUMBER A LOUD ERROR HERE rather
        # than a silent parse failure in her Node server. Python happily
        # writes `Infinity` and `NaN`; JSON.parse rejects both, and the
        # failure would surface to the person as a generic PROVIDER_ERROR
        # with the real verdict gone. handle_task already converts the one
        # known source (clearance_mm); this catches any future one.
        try:
            blob = json.dumps(payload, allow_nan=False).encode()
        except ValueError as e:
            print(f"[care] REFUSING TO SEND non-JSON numbers: {e}")
            blob = json.dumps({"status": "unavailable",
                               "reason": "response was not encodable"}).encode()
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(blob)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > 32768:      # her server's own cap
            return None
        try:
            return json.loads(self.rfile.read(length).decode())
        except (ValueError, UnicodeDecodeError):
            return None

    def do_GET(self):
        if self.path == "/api/vitals":
            return self._send(*handle_vitals())
        if self.path == "/api/health":
            return self._send(200, {"status": "ok",
                                    "uptime_s": round(time.time() - _started_at, 1)})
        self._send(404, {"status": "rejected", "reason": "not found"})

    def do_POST(self):
        if self.path == "/api/stop":
            # READ NOTHING FIRST. Her adapter sends a body, but a stop that
            # depends on parsing one is a stop that a malformed body can
            # block. The body is drained after the flag is set, purely so the
            # connection can be reused.
            status, payload = handle_stop()
            try:
                self._read_json()
            except Exception:
                pass
            return self._send(status, payload)
        if self.path == "/api/task":
            body = self._read_json()
            idem = self.headers.get("Idempotency-Key")
            return self._send(*handle_task(body, idem))
        self._send(404, {"status": "rejected", "reason": "not found"})


def start(scrubbot_module=None, port=8770):
    """Start the seam on its own daemon thread. Never raises.

    LOUDLY ON FAILURE, and never fatal -- same contract as start_ws(). A
    leftover process holding the port must cost a log line, not the demo.
    """
    global _SB, _started_at
    _SB = scrubbot_module
    _started_at = time.time()

    def _run():
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
            httpd.daemon_threads = True
            print(f"[care] seam B listening on http://127.0.0.1:{port}"
                  f"  (/api/task /api/stop /api/vitals)")
            httpd.serve_forever()
        except OSError as e:
            print("\n" + "=" * 62)
            print(f"[care] CANNOT BIND http://127.0.0.1:{port} — {e}")
            print("     Crystal's UI will get ROBOT_NOT_CONFIGURED.")
            print("     Almost certainly a leftover process:")
            print(f"       lsof -ti tcp:{port} | xargs kill -9")
            print("=" * 62 + "\n")
        except Exception as e:                  # never take the demo down
            print(f"[care] seam B died: {e!r} — the projector is unaffected")

    threading.Thread(target=_run, daemon=True).start()
