"""tests/fake_roarm.py — a protocol-level RoArm-M2-S simulator on a pty.

WHY THIS EXISTS: no physical arm is available, and `arm.py` had never talked
to anything at all. This speaks the REAL ESP32 JSON dialect over a pseudo-
terminal, so `serial.Serial(port)` connects to it exactly as it would to
/dev/cu.usbserial-*.

WHAT IT PROVES: wire format, command rate, rate-limiting, reachability
rejection, estop semantics, feedback parsing.

WHAT IT CANNOT PROVE: servo dynamics, brownout under load, the true reach
envelope, CP210x driver behaviour, firmware quirks. First contact with real
hardware is still a human running `python py/arm.py`. Do not let a green run
here read as "the arm works."

Protocol modelled from waveshareteam/roarm_m2 json_cmd.h (AGPL — read, never
pasted) and the Waveshare motion-control wiki.
"""
import json, math, os, pty, threading, time

ARM_L1 = 126.06
ARM_L2 = math.hypot(236.82, 30.00)
ARM_L3 = math.hypot(280.15, 1.73)
HOME = (235.11, 0.0, 234.79, 3.14)


class FakeRoArm:
    """Serves one pty. `port` is the slave path to hand to serial.Serial()."""

    def __init__(self, verbose=False, echo_debug=False):
        self.master, self.slave = pty.openpty()
        self.port = os.ttyname(self.slave)
        self.verbose = verbose
        self.debug_on = echo_debug        # T:605 toggles firmware log spam
        self.running = True

        self.x, self.y, self.z, self.t = HOME
        self.torque_caps = {"b": 1000, "s": 1000, "e": 1000, "h": 1000}
        self.estopped = False
        # torque we will REPORT; a test can drive this to fake contact
        self.report_torque = {"torB": 12, "torS": 40, "torE": 25, "torH": 8}

        self.commands = []                # every parsed command, in order
        self.raw_lines = []               # every raw line, for wire-format checks
        self.bad_lines = []               # anything that failed to parse
        self.cmd_times = []               # arrival timestamps, for rate checks
        self.stalled = False       # see stall(): makes the ARM's writes fail
        self.lock = threading.Lock()
        threading.Thread(target=self._serve, daemon=True).start()

    # ------------------------------------------------------------------ io --
    def _write(self, obj):
        os.write(self.master, (json.dumps(obj) + "\r\n").encode())

    def _serve(self):
        buf = b""
        while self.running:
            try:
                data = os.read(self.master, 4096)
            except OSError:
                return
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if line:
                    self._handle(line.decode("utf-8", "replace"))

    def _handle(self, line):
        now = time.perf_counter()
        with self.lock:
            self.raw_lines.append(line)
            self.cmd_times.append(now)
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            with self.lock:
                self.bad_lines.append(line)
            return
        with self.lock:
            self.commands.append(d)
        T = d.get("T")

        if T == 1041:                       # CMD_XYZT_DIRECT_CTRL, non-blocking
            if self.estopped:
                return                      # estopped hardware ignores motion
            self.x, self.y = d.get("x", self.x), d.get("y", self.y)
            self.z, self.t = d.get("z", self.z), d.get("t", self.t)
            if self.verbose:
                print(f"  [fake] -> ({self.x:.1f},{self.y:.1f},{self.z:.1f})")

        elif T == 105:                      # CMD_FEEDBACK_GET
            self._write({"T": 1051, "x": round(self.x, 2), "y": round(self.y, 2),
                         "z": round(self.z, 2), "b": 0.0, "s": 0.0, "e": 1.57,
                         "t": self.t, **self.report_torque, "v": 11.9})

        elif T == 112:                      # CMD_DYNAMIC_ADAPTATION
            for k in ("b", "s", "e", "h"):
                if k in d:
                    self.torque_caps[k] = d[k]

        elif T == 0:                        # CMD_EMERGENCY_STOP
            self.estopped = True

        elif T == 999:                      # CMD_RESET_EMERGENCY
            self.estopped = False

        elif T == 605:                      # debug log toggle
            self.debug_on = bool(d.get("cmd", 0))

    # -------------------------------------------------------------- helpers --
    def snapshot(self):
        with self.lock:
            return {"commands": list(self.commands),
                    "raw": list(self.raw_lines),
                    "bad": list(self.bad_lines),
                    "times": list(self.cmd_times)}

    def of_type(self, T):
        with self.lock:
            return [c for c in self.commands if c.get("T") == T]

    def rate_hz(self, T=1041):
        """Observed arrival rate of one command type."""
        with self.lock:
            idx = [i for i, c in enumerate(self.commands) if c.get("T") == T]
            ts = [self.cmd_times[i] for i in idx]
        if len(ts) < 3:
            return 0.0
        return (len(ts) - 1) / (ts[-1] - ts[0])

    def max_step_mm(self):
        """Largest Cartesian jump between consecutive 1041 commands."""
        pts = [(c.get("x"), c.get("y"), c.get("z")) for c in self.of_type(1041)]
        pts = [p for p in pts if None not in p]
        return max((math.dist(a, b) for a, b in zip(pts, pts[1:])), default=0.0)

    def stall(self, on=True):
        """Make the ARM'S WRITES FAIL, the way a wedged ESP32 does.

        WHY NOT DROP BYTES IN _handle(): Arm._send() catches
        SerialTimeoutException / SerialException and counts them into
        _consec_write_fail, which is what link_ok and the estop recovery path
        both read. Silently discarding a command after the pty delivered it
        leaves _send() returning True, so none of that machinery engages and
        the scenario under test never happens. The failure has to be visible
        to the WRITER.

        This existed nowhere, which is why py/arm.py:422 -- "0 commands
        delivered after recovery with estop pressed, versus 41 ending at
        HOME" -- was the one MEASURED claim in the codebase that could not be
        re-verified. test_arm_protocol's link_ok section sets
        _consec_write_fail by hand and never drives a real failing write.
        """
        self.stalled = bool(on)

    def attach_stall(self, arm):
        """Route `arm`'s serial writes through this fake's stall flag.

        Wraps the port object rather than patching Arm: the code under test
        stays untouched, and every retry/counter path in _send() runs for
        real.
        """
        import serial as _serial
        fake = self
        real_write = arm.ser.write

        def guarded(data):
            if fake.stalled:
                raise _serial.SerialTimeoutException("stalled (test)")
            return real_write(data)

        arm.ser.write = guarded
        return arm

    def set_contact(self, on=True):
        """Fake the sponge pressing a forearm."""
        self.report_torque = ({"torB": 90, "torS": 700, "torE": 520, "torH": 40}
                              if on else
                              {"torB": 12, "torS": 40, "torE": 25, "torH": 8})

    def close(self):
        """Shut down without hanging.

        The reader thread blocks in os.read(master). Closing `master` from
        another thread does NOT reliably wake it -- the process hangs at exit
        with a non-daemon-ish blocked read. Writing a sentinel byte to the
        SLAVE end unblocks the read first, then the fds close cleanly.
        """
        self.running = False
        try:
            os.write(self.slave, b"\n")     # wake the blocked reader
        except OSError:
            pass
        time.sleep(0.05)
        for fd in (self.master, self.slave):
            try: os.close(fd)
            except OSError: pass
