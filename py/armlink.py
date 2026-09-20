"""py/armlink.py -- where the real arms ARE, read from the bridge.

WHAT THIS IS
------------
`scrub3d/live/dimos_bridge_server.py` runs on the arm computer and owns the
two OpenYAMs. Its `state` op returns, per arm, the six joint angles dimOS
read off the encoders and where that puts the tool. This asks for that and
nothing else.

READ ONLY, AND THAT IS THE WHOLE SAFETY ARGUMENT
-------------------------------------------------
The bridge speaks `target`, `home`, `gripper`, `cancel` and `hold`, all of
which MOVE AN ARM. This module sends `state` and `bye`. It cannot move
anything, which is why it is safe to run while somebody else is driving --
and driving is exactly what is happening: DIMOS.md says "One bridge at a
time. Two bridges are two dimOS stacks on one bus answering the same
names." We never start one. We connect to the one that is already there.

WHY NOT arm_dimos.py, WHICH ALREADY SPEAKS THIS
-------------------------------------------------
That module is a full arm DRIVER: it holds a link, follows world points,
sends targets, parks. Importing it to read six numbers would pull the
sending half into a process whose entire claim is that it never sends. The
protocol is four lines of JSON over a socket; a reader that cannot command
is worth more than the code it saves.

MEASURED, NOT COMMANDED, AND THE PAGE SAYS WHICH
--------------------------------------------------
Every angle here came off an encoder. py/scrubbot.py's _sample_joints()
prefers this over the commanded pose and stamps the payload "measured"; the
HUD then reads ARM LINKED - MEAS instead of CMD. When this is unreachable
it returns None and the commanded path continues, so the picture degrades
to "what we asked for" rather than freezing or lying.

NEVER BLOCKS THE LOOP IT RIDES
--------------------------------
The read happens on its own thread at a few hertz and the loop takes the
last answer. A bridge that stops answering costs a stale flag, not a frame:
the 15Hz websocket is what keeps the cartoon alive, and nothing on a
network belongs between it and the screen.
"""
import json
import os
import socket
import threading
import time

# Where the bridge is. Same variable name arm_dimos.py uses, so one export
# configures both rather than two names meaning the same thing.
ENV = "SCRUB3D_DIMOS"
DEFAULT_PORT = 7790

# How often to ask. The arms report at 30Hz inside dimOS; the websocket
# sends at 15. Asking at 10 is under both and leaves the bridge alone --
# it is somebody else's process and we are a guest in it.
POLL_HZ = 10.0

# A reading older than this is not current. An arm's pose at 2 seconds ago
# is not where it is now, and drawing it as live would be the same class of
# claim this whole project exists to avoid.
STALE_S = 2.0


def _endpoint():
    raw = (os.environ.get(ENV) or "").strip()
    if not raw:
        return None
    if ":" in raw:
        host, _, port = raw.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            return None
    return raw, DEFAULT_PORT


class ArmLink:
    """Polls the bridge for measured joint angles. Read only."""

    def __init__(self, endpoint=None):
        self.endpoint = endpoint or _endpoint()
        self._lock = threading.Lock()
        self._state = None          # the last good reply
        self._at = 0.0              # when it arrived
        self._err = None            # the last failure, for the operator
        self._stop = threading.Event()
        self._thread = None

    # --- what scrubbot reads ------------------------------------------------
    def joints(self, side):
        """The six measured angles for one arm, or None.

        None means NOT MEASURED -- unreachable, stale, or an arm the stack
        has no pose for. It is never a last-known value dressed as current.
        """
        with self._lock:
            s, at = self._state, self._at
        if s is None or (time.time() - at) > STALE_S:
            return None
        arm = (s.get("arms") or {}).get(side)
        if not arm:
            return None
        j = arm.get("joints")
        if not j:
            return None
        return [float(v) for v in j]

    def tool_mm(self, side):
        """Where the tool is, in MILLIMETRES, or None. The bridge sends
        metres; scrub3d is millimetres everywhere, so it converts here
        rather than leaving two unit conventions in the same process."""
        with self._lock:
            s, at = self._state, self._at
        if s is None or (time.time() - at) > STALE_S:
            return None
        arm = (s.get("arms") or {}).get(side)
        p = (arm or {}).get("p")
        return [float(v) * 1000.0 for v in p] if p else None

    def alive(self):
        with self._lock:
            return self._state is not None and (time.time() - self._at) <= STALE_S

    def error(self):
        with self._lock:
            return self._err

    # --- the poll -----------------------------------------------------------
    def start(self):
        """Begin polling on a daemon thread. Never raises."""
        if self.endpoint is None or self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        period = 1.0 / POLL_HZ
        sock = f = None
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                if sock is None:
                    sock = socket.create_connection(self.endpoint, timeout=3.0)
                    # The bridge sets TCP_NODELAY on its side and reads
                    # line by line with no buffering; a buffered writer here
                    # can hold a request until a later flush, and the reply
                    # never comes because the request never left.
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    f = sock.makefile("rwb", buffering=0)
                f.write(b'{"op":"state"}\n')
                line = f.readline()
                if not line:
                    raise ConnectionError("bridge closed the connection")
                rep = json.loads(line)
                if not rep.get("ok"):
                    raise ValueError(rep.get("error") or "bridge said not ok")
                with self._lock:
                    self._state, self._at, self._err = rep, time.time(), None
            except Exception as exc:                         # noqa: BLE001
                # RECONNECT NEXT TICK, DO NOT DIE. The bridge is restarted
                # often while somebody works on the arms, and a reader that
                # gives up on the first refusal would need the demo
                # restarted to come back.
                with self._lock:
                    self._err = f"{type(exc).__name__}: {exc}"
                try:
                    if sock is not None:
                        sock.close()
                except Exception:                            # noqa: BLE001
                    pass
                sock = f = None
            time.sleep(max(0.0, period - (time.monotonic() - t0)))
        try:
            if f is not None:
                f.write(b'{"op":"bye"}\n')
            if sock is not None:
                sock.close()
        except Exception:                                    # noqa: BLE001
            pass


def _self_test():
    """Against a real bridge if SCRUB3D_DIMOS points at one, else the shape."""
    print("armlink self-test")
    ep = _endpoint()
    if ep is None:
        print("  SCRUB3D_DIMOS not set -- checking the no-endpoint path only")
        a = ArmLink()
        assert a.start() is a and a.joints("left") is None and not a.alive()
        print("  no endpoint -> joints None, alive False, no exception")
        print("OK")
        return

    print(f"  polling {ep[0]}:{ep[1]}")
    a = ArmLink().start()
    for _ in range(30):
        if a.alive():
            break
        time.sleep(0.2)
    if not a.alive():
        print(f"  no reading: {a.error()}")
        print("  (a bridge that is down is a normal state, not a failure)")
        return
    for side in ("left", "right"):
        j, p = a.joints(side), a.tool_mm(side)
        print(f"  {side:6} joints {[round(v, 4) for v in j] if j else None}")
        print(f"         tool   {[round(v, 1) for v in p] if p else None} mm")
    a.stop()
    print("OK -- MEASURED off the encoders, nothing was commanded")


if __name__ == "__main__":
    _self_test()
