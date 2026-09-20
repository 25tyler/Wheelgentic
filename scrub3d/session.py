"""scrub3d/session.py -- consent, and the two clocks that bound it.

ONE LATCH FOR THE FLEET, NOT ONE PER ARM
-----------------------------------------
Consent is about the PERSON, not the actuator. Four latches would allow a
partial arming -- two arms consented, two not -- which is a state nobody has
reasoned about and which the safety layer has no opinion on. There is one
latch. Everything moves or nothing does.

TWO CLOCKS, AND THEY ARE NOT INTERCHANGEABLE
----------------------------------------------
    ARM_TIMEOUT_S     after the press, how long until motion must have begun
    SESSION_BUDGET_S  after the press, how long the whole session may run

The existing project shipped one timeout and documented the bug it caused at
scrubbot.py:208-213: press the button, walk away, and the next person to sit
down gets scrubbed on the first person's consent. The tempting fix is to raise
the single timeout until the session fits inside it, which re-creates exactly
that bug with a longer fuse. Two clocks is the fix: a short one that expires if
nothing starts, and a longer one that ends the session regardless.

STAMP FIRST, THEN LATCH
------------------------
The write ordering is deliberate and is kept from the original. Record the
timestamp BEFORE setting the flag, so a crash between the two leaves a session
that has expired rather than one that never expires. The failure mode of the
other order is a latch with no clock on it.
"""
import time

ARM_TIMEOUT_S = 30.0        # press -> motion must start within this
SESSION_BUDGET_S = 180.0    # press -> everything is over by this

IDLE, ARMED, RUNNING, DONE, EXPIRED = "idle", "armed", "running", "done", "expired"


class Consent:
    """The fleet's single consent latch."""

    def __init__(self, arm_timeout_s=ARM_TIMEOUT_S,
                 budget_s=SESSION_BUDGET_S, clock=time.monotonic):
        self.arm_timeout = arm_timeout_s
        self.budget = budget_s
        self.clock = clock
        self._pressed_at = None
        self._started_at = None
        self._state = IDLE
        self.log = []

    # --- the press ---------------------------------------------------------
    def press(self, given_by="operator"):
        """Somebody consented. -> True.

        Stamp first, latch second. A crash between the two leaves an expired
        session rather than an immortal one.
        """
        self._pressed_at = self.clock()          # stamp
        self._started_at = None
        self._state = ARMED                      # then latch
        self.log.append((self._pressed_at, "press", given_by))
        return True

    def adopt(self, pressed_at, given_by="adopted"):
        """Latch a consent that was recorded ELSEWHERE, at its own timestamp.

        -> True.

        WHY THIS IS NOT press(). press() stamps NOW, which is right when this
        object is the thing recording the consent. It is wrong when consent
        already exists somewhere else and this latch is being brought into
        agreement with it: re-stamping would restart both clocks and turn a
        consent given three minutes ago into a fresh one, which is the exact
        stale-consent bug the two clocks exist to catch.

        The caller in py/scrubbot.py has an older latch it cannot delete -- a
        bare `ARMED` flag written from fifteen sites -- and adopting it here
        is what lets the clocks apply to arming routes this object never saw.
        Without this, a flag raised by any of those routes reads as IDLE and
        the very next frame revokes a perfectly valid consent.

        STAMP FIRST, THEN LATCH, like press(), for the same reason.
        """
        self._pressed_at = float(pressed_at)     # stamp, at ITS time
        self._started_at = None
        self._state = ARMED                      # then latch
        self.log.append((self._pressed_at, "adopt", given_by))
        return True

    def start_motion(self):
        """The first commanded motion. -> (allowed, why)."""
        st, why = self.state()
        if st != ARMED:
            return False, f"cannot start motion while {st}: {why}"
        self._started_at = self.clock()
        self._state = RUNNING
        self.log.append((self._started_at, "start_motion", ""))
        return True, "ok"

    def finish(self, why="completed"):
        self._state = DONE
        self.log.append((self.clock(), "finish", why))

    def revoke(self, why="operator"):
        """Withdraw consent immediately. Always allowed, never refused."""
        self._state = IDLE
        self._pressed_at = self._started_at = None
        self.log.append((self.clock(), "revoke", why))

    # --- the clocks --------------------------------------------------------
    def state(self):
        """-> (state, why). Evaluates the clocks; never cached."""
        if self._state in (IDLE, DONE):
            return self._state, ""
        if self._pressed_at is None:
            return IDLE, "no consent on record"
        age = self.clock() - self._pressed_at

        if age > self.budget:
            if self._state != EXPIRED:
                self._state = EXPIRED
                self.log.append((self.clock(), "expired", "session budget"))
            return EXPIRED, (f"session budget of {self.budget:.0f}s is spent")
        if self._state == ARMED and age > self.arm_timeout:
            self._state = EXPIRED
            self.log.append((self.clock(), "expired", "never started"))
            return EXPIRED, (f"armed {age:.0f}s ago and never started; "
                             f"the limit is {self.arm_timeout:.0f}s")
        return self._state, ""

    def may_move(self):
        """The question every motion asks. -> (bool, why)."""
        st, why = self.state()
        if st in (ARMED, RUNNING):
            return True, ""
        return False, why or f"consent is {st}"

    def remaining_s(self):
        if self._pressed_at is None:
            return 0.0
        return max(0.0, self.budget - (self.clock() - self._pressed_at))


if __name__ == "__main__":
    print("consent")

    now = [0.0]
    c = Consent(arm_timeout_s=30.0, budget_s=180.0, clock=lambda: now[0])

    ok, why = c.may_move()
    print(f"\n  before any press           -> may_move {ok}  ({why})")
    assert not ok

    c.press()
    ok, why = c.may_move()
    print(f"  just pressed               -> may_move {ok}")
    assert ok

    # The bug this file exists to prevent: press, walk away, somebody else sits
    # down. The short clock has to catch that.
    now[0] = 45.0
    ok, why = c.may_move()
    print(f"\n  pressed, then 45s of nothing:")
    print(f"    may_move {ok}  ({why})")
    assert not ok, "a stale consent still authorised motion"

    # A session that DOES start is bounded by the other clock, not this one.
    now[0] = 100.0
    c.press()
    started, why = c.start_motion()
    print(f"\n  pressed again and started  -> {started}")
    assert started
    now[0] = 100.0 + 45.0
    ok, _ = c.may_move()
    print(f"    45s into a running session -> may_move {ok} "
          f"(the 30s arm clock does NOT apply once running)")
    assert ok, "the arming clock wrongly ended a running session"

    now[0] = 100.0 + 181.0
    ok, why = c.may_move()
    print(f"    181s in                    -> may_move {ok}  ({why})")
    assert not ok, "the session budget did not end the session"

    # Revocation is immediate and unconditional.
    now[0] = 400.0
    c.press()
    c.revoke("volunteer asked to stop")
    ok, why = c.may_move()
    print(f"\n  revoked mid-session        -> may_move {ok}  ({why})")
    assert not ok

    print(f"\n  one latch, two clocks, stamped before it is latched. OK")
