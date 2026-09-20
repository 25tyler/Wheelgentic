"""py/contact.py -- scrub3d/torque.py in the live feedback loop.

WHAT THIS REPLACES
------------------
py/arm.py:443 decides contact with a fixed threshold on raw servo torque:

    self.contact = (abs(fb.get("torS", 0)) > 550
                    or abs(fb.get("torE", 0)) > 450)

Raw torque is dominated by GRAVITY, and gravity on a servo depends on where
the arm is, not on what is under it. MEASURED with scrub3d/torque.py's model
of this arm, free air, nothing under the sponge:

    extended, arm out    raw torS 625   -> the 550 test FIRES
    half folded          raw torS 331   -> quiet
    folded in close      raw torS   3   -> quiet

So the shipped test claims torque-confirmed contact with nothing under the arm
at one end of the workspace and cannot reach the threshold at the other. On
the projector that is a splotch popping because the arm is extended.

scrub3d/torque.py subtracts what gravity explains:

    tau_shoulder = A*cos(j1) + B*cos(j1 + j2) + C
    tau_elbow    =             D*cos(j1 + j2) + E

and contact becomes the residual, which is pose-independent by construction.
The same three poses read delta 0.4 / -0.1 / -0.6 -- all quiet, correctly.

WHY IT FITS ITSELF INSTEAD OF SHIPPING COEFFICIENTS
---------------------------------------------------
Those coefficients are this arm's mass distribution. They are not a constant
anyone can look up, and THE ARM HAS NEVER MOVED -- there is no free-air sweep
to fit against and no kitchen-scale press to turn the residual into newtons.
Shipping invented numbers would be worse than the threshold it replaces,
because it would look calibrated.

So the model fits from the arm's OWN feedback, live, while the FSM is IDLE and
the sponge is provably not on anybody. T:105 already returns "s" and "e" --
shoulder and elbow angles in radians -- next to torS/torE, so every sample the
watchdog already polls is a free-air training row whenever the arm is idle.
That needs no new hardware, no new command and no operator step.

Until it has enough spread to fit, `ready` is False and NOTHING here touches
arm.contact: the shipped threshold stays in charge. This is a strict addition
that switches on when it has earned the right to, and says which one is
driving in its log line.

NO NEWTONS, AND SAYING SO
-------------------------
ForceScale maps residual torque to newtons from presses onto a kitchen scale.
That measurement does not exist, so `force_n` is None here and the projector
is never told a force. The residual is still pose-independent, which is the
part that fixes the bug; newtons are a second, separate calibration that needs
the arm to actually press on something known.
"""
import os
import sys
import threading

# scrub3d is the backend package; py/ is the live demo. The demo must start on
# a machine with neither numpy nor scrub3d -- --replay exists for exactly the
# case where the heavy stack has failed -- so this import is guarded and every
# failure degrades to "the shipped threshold stays in charge".
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_torque():
    """-> (module, why). None with a reason rather than raising."""
    path = os.environ.get("SCRUB3D") or os.path.join(_ROOT, "scrub3d")
    if not os.path.isdir(path):
        return None, "scrub3d/ not found"
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        import torque
        return torque, ""
    except Exception as e:                        # numpy missing, etc.
        return None, f"{type(e).__name__}: {e}"


# FIT WINDOW. The gravity model has three shoulder coefficients and two elbow
# ones, so five rows is the algebraic minimum and would fit noise exactly.
# These are least squares over a real arm's feedback, which carries the
# quantisation of a 4096-count encoder plus serial-read jitter; 120 samples at
# the watchdog's ~10Hz is about twelve seconds of idle, which the demo spends
# before the first cycle anyway.
MIN_SAMPLES = 120

# SPREAD GATE. 120 samples taken while the arm sits perfectly still are 120
# copies of one row: the fit would be singular in everything but name and the
# residual would read zero everywhere. cos(j1) has to actually vary. 0.15 is
# about 8.6 degrees of shoulder travel, which the approach move alone exceeds.
MIN_SPREAD = 0.15

# CONTACT THRESHOLD, in residual torque units. scrub3d/torque.py's own
# demonstration uses abs(delta) > 40 to separate free air (0.4 / -0.1 / -0.6)
# from a press, and its synthetic arm puts a 2N press at delta 89. There is no
# measured press on this hardware to tune against, so this stays at the value
# the backend module demonstrates and is named here rather than buried.
DELTA_CONTACT = 40.0


class LiveContact:
    """Fits the gravity model from idle feedback, then calls contact by residual.

    Thread-confined to feedback_loop(): update() and note_idle() are called
    from that one thread. `ready`, `driving` and `last` are read by other
    threads for the log line and are plain attribute reads of immutable values.
    """

    def __init__(self):
        self.mod, self.why = _load_torque()
        self.g = None                 # the fitted GravityModel
        self.ready = False            # has a model that passed the spread gate
        self.sense = None             # torque.ContactSense, once fitted
        self.last = None              # last update() dict, for the log line
        self._rows = []               # (j1, j2, tau_s, tau_e) free-air samples
        self._idle = False            # is the arm provably not on anybody
        self._said = False            # log the handover exactly once
        self._lock = threading.Lock()
        if self.mod is None:
            print(f"[contact] gravity model unavailable ({self.why}) -- "
                  f"the fixed torque threshold stays in charge")

    def _scrub_hz(self):
        """config.json's scrub_hz, as a number. -> float.

        Read through the SAME Config object py/scrubbot.py uses, so there is
        one hot-reloading source for the scrub rate rather than two that can
        disagree. Falls back to ContactSense's own default on anything
        unreadable -- config.py is a bare json.load precisely so a malformed
        edit never crashes the process on stage, and a bad value here must not
        either.
        """
        try:
            import scrubbot
            v = float(scrubbot.CFG.data.get("scrub_hz", 1.2))
            return v if v > 0.0 else 1.2
        except Exception:
            return 1.2

    # -- the FSM tells us when nothing is under the sponge --------------------

    def note_idle(self, idle):
        """IDLE means the sponge is provably not on a person, so feedback is
        free air and a legitimate training row. Any other phase is not:
        fitting gravity from samples taken while pressing on a forearm would
        teach the model that the press IS gravity and subtract the very signal
        it exists to detect.
        """
        self._idle = bool(idle)

    # -- one feedback sample -------------------------------------------------

    def update(self, fb):
        """One T:105 reply. -> dict or None.

        None means this sample taught us nothing and decided nothing, which is
        every sample until the model fits.
        """
        if self.mod is None or not fb:
            return None
        # "s"/"e" are the shoulder and elbow angles in RADIANS, which is what
        # GravityModel's cosines want; torS/torE are the raw torques at those
        # same joints. Both come from the one T:105 reply the watchdog already
        # polls, so there is no extra serial traffic for any of this.
        for k in ("s", "e", "torS", "torE"):
            if k not in fb:
                return None            # not a RoArm reply; nothing to do
        try:
            j1 = float(fb["s"])
            j2 = float(fb["e"])
            ts = float(fb["torS"])
            te = float(fb["torE"])
        except (TypeError, ValueError):
            return None

        if not self.ready:
            if self._idle:
                self._rows.append((j1, j2, ts, te))
                self._try_fit()
            return None

        out = self.sense.update(j1, j2, ts, te)
        self.last = out
        return out

    def _try_fit(self):
        if len(self._rows) < MIN_SAMPLES:
            return
        import numpy as np
        a = np.asarray(self._rows, float)
        # THE SPREAD GATE, on the regressor that actually carries the shoulder
        # term. Checking the raw angle instead would pass a sweep that happened
        # to straddle a cosine peak, where 20 degrees of travel moves cos(j1)
        # barely at all and the fit is still degenerate.
        if float(np.ptp(np.cos(a[:, 0]))) < MIN_SPREAD:
            # Keep collecting, but do not grow without bound: an arm parked in
            # one pose for the whole demo would otherwise accumulate rows
            # forever. Keep the most recent window.
            if len(self._rows) > MIN_SAMPLES * 4:
                self._rows = self._rows[-MIN_SAMPLES * 2:]
            return
        g = self.mod.GravityModel().fit(a[:, 0], a[:, 1], a[:, 2], a[:, 3])
        self.g = g
        # scale=None: no kitchen-scale calibration exists on this hardware, so
        # ContactSense reports force_n None and `contact` False. We therefore
        # do NOT use its `contact` field -- see contact_from() -- and read the
        # residual directly. The class is still what runs the Goertzel bin.
        #
        # f_scrub MUST BE THE RATE THE ARM ACTUALLY OSCILLATES AT, which is
        # config.json's scrub_hz -- the same key py/scrubbot.py hands
        # motion.scrub_offset(). ContactSense defaults to 1.2 and config.json
        # ships 1.2, so they agree today and the bug is invisible; but that key
        # HOT-RELOADS, so an operator retuning the scrub at the venue would
        # leave this bin listening at 1.2Hz while the sponge moved at another
        # rate, and every real scrub would read as leaning. Ask for the live
        # value instead of copying the default.
        self.sense = self.mod.ContactSense(g, None,
                                           f_scrub_hz=self._scrub_hz())
        self.ready = True
        print(f"[contact] gravity model fitted from {len(a)} free-air samples "
              f"(residual shoulder {g.rms[0]:.1f}, elbow {g.rms[1]:.1f}) -- "
              f"pose-independent contact is now driving")

    # -- what the FSM asks ---------------------------------------------------

    def contact_from(self, out):
        """-> bool, from an update() dict. Residual, not newtons.

        ContactSense.contact is force_n >= force_floor_n, and force_n is None
        with no ForceScale, so its own boolean is always False here. The
        residual is the calibrated-in-shape part; the newton scale is the part
        that is missing. Reading the residual directly is what makes this
        usable with the calibration that actually exists.
        """
        if not out:
            return False
        return (abs(out["delta_shoulder"]) > DELTA_CONTACT
                or abs(out["delta_elbow"]) > DELTA_CONTACT)
