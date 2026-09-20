"""scrub3d/torque.py -- contact in newtons, and telling scrubbing from leaning.

THE BUG THIS EXISTS TO FIX
---------------------------
The shipped contact test is a fixed threshold on raw servo torque:

    contact = abs(torS) > 550 or abs(torE) > 450          py/arm.py:377

Raw torque is dominated by GRAVITY, and gravity on a servo depends entirely on
where the arm is. Fully extended, the shoulder carries the whole forearm on a
long lever and approaches 550 with nothing under it at all. Folded in at 250mm
it never will, no matter how hard the sponge presses. So one threshold means a
hair trigger in one part of the workspace and a dead zone in another, and the
sensitivity varies by roughly 2x across the reach.

Arbitrary arm placement makes that worse rather than better, because arms may
be mounted further out than the one this threshold was tuned against.

THE FIX IS TO SUBTRACT WHAT GRAVITY EXPLAINS
---------------------------------------------
Sweep the workspace with nothing under the arm and fit the static model:

    tau_shoulder = A*cos(j1) + B*cos(j1 + j2) + C
    tau_elbow    =             D*cos(j1 + j2) + E

Those are the moments of the two link masses about each joint, so the cosines
are not curve-fitting -- they are the physics, and the coefficients are what
the arm's own mass distribution happens to be. Contact is then

    delta = tau_measured - tau_baseline(j1, j2)

which is pose-independent by construction. Press onto a kitchen scale at three
poses and `delta` maps to NEWTONS, so the system can report "the sponge applied
2 to 6 N" instead of "a boolean was true".

TELLING SCRUBBING FROM LEANING
-------------------------------
A high steady delta means the arm is pressing. Real scrubbing oscillates, so it
puts energy at the SCRUB FREQUENCY specifically. One Goertzel bin at f_scrub
over a two-second window separates them, and the distinction matters: an arm
that is jammed, or dragging a sponge that has stopped moving, reads as solid
contact and must not be credited with coverage.

At 10Hz feedback and a 1.2Hz scrub that is 8.3 samples per cycle, comfortably
above the 2.4Hz Nyquist floor.
"""
import math

import numpy as np


class GravityModel:
    """Baseline torque from pose alone, fitted with nothing under the arm."""

    def __init__(self):
        self.cs = None          # shoulder coefficients (A, B, C)
        self.ce = None          # elbow coefficients (D, E)
        self.rms = (None, None)

    def fit(self, j1, j2, tau_s, tau_e):
        """Least squares on a free-air sweep. -> self."""
        j1, j2 = np.asarray(j1, float), np.asarray(j2, float)
        tau_s, tau_e = np.asarray(tau_s, float), np.asarray(tau_e, float)
        Ms = np.c_[np.cos(j1), np.cos(j1 + j2), np.ones_like(j1)]
        Me = np.c_[np.cos(j1 + j2), np.ones_like(j1)]
        self.cs, *_ = np.linalg.lstsq(Ms, tau_s, rcond=None)
        self.ce, *_ = np.linalg.lstsq(Me, tau_e, rcond=None)
        self.rms = (float(np.sqrt(np.mean((Ms @ self.cs - tau_s) ** 2))),
                    float(np.sqrt(np.mean((Me @ self.ce - tau_e) ** 2))))
        return self

    def baseline(self, j1, j2):
        """-> (tau_shoulder, tau_elbow) expected from gravity at this pose."""
        j1, j2 = np.asarray(j1, float), np.asarray(j2, float)
        s = (self.cs[0] * np.cos(j1) + self.cs[1] * np.cos(j1 + j2)
             + self.cs[2])
        e = self.ce[0] * np.cos(j1 + j2) + self.ce[1]
        return s, e

    def delta(self, j1, j2, tau_s, tau_e):
        """Torque that gravity does NOT explain. -> (ds, de)."""
        bs, be = self.baseline(j1, j2)
        return np.asarray(tau_s, float) - bs, np.asarray(tau_e, float) - be


class ForceScale:
    """Maps unexplained torque to newtons. Two points and a line through zero.

    Zero delta is zero force by construction, so the fit is a single gain and
    must not be given an intercept -- an intercept lets the calibration claim a
    force at rest, which is the one value it is certain about.
    """

    def __init__(self):
        self.gain = None        # newtons per torque unit
        self.rms_n = None

    def fit(self, delta, newtons):
        d, n = np.asarray(delta, float), np.asarray(newtons, float)
        self.gain = float(d @ n / max(d @ d, 1e-12))
        self.rms_n = float(np.sqrt(np.mean((self.gain * d - n) ** 2)))
        return self

    def newtons(self, delta):
        return np.asarray(delta, float) * self.gain


def goertzel(x, f_hz, fs_hz):
    """Energy at ONE frequency. -> magnitude.

    A whole FFT to read a single bin is wasted work in a control loop, and this
    is the standard way to avoid it: a two-tap recurrence, fifteen lines, no
    allocation proportional to the spectrum.
    """
    x = np.asarray(x, float)
    n = len(x)
    if n < 4 or fs_hz <= 0:
        return 0.0
    k = 2.0 * math.cos(2.0 * math.pi * f_hz / fs_hz)
    s1 = s2 = 0.0
    for v in x:
        s0 = v + k * s1 - s2
        s2, s1 = s1, s0
    power = s1 * s1 + s2 * s2 - k * s1 * s2
    return float(math.sqrt(max(power, 0.0)) / (n / 2.0))


class ContactSense:
    """Pose-independent contact, force in newtons, and a scrubbing test."""

    def __init__(self, gravity, scale=None, force_floor_n=1.5,
                 f_scrub_hz=1.2, fs_hz=10.0, window_s=2.0,
                 scrub_energy_floor=0.15):
        self.g = gravity
        self.scale = scale
        self.force_floor_n = force_floor_n
        self.f_scrub = f_scrub_hz
        self.fs = fs_hz
        self.window = max(int(window_s * fs_hz), 8)
        self.energy_floor = scrub_energy_floor
        self.hist = []

    def update(self, j1, j2, tau_s, tau_e):
        """One feedback sample. -> dict.

        `scrubbing` is the one a coverage counter should believe. High force
        with no energy at the scrub frequency is an arm leaning on someone, or
        a sponge that has stopped moving while the arm keeps pushing, and
        crediting either as coverage is how a run reports work it did not do.
        """
        ds, de = self.g.delta(j1, j2, tau_s, tau_e)
        ds, de = float(ds), float(de)
        mag = max(abs(ds), abs(de))
        self.hist.append(mag)
        if len(self.hist) > self.window:
            self.hist.pop(0)

        # CONTACT IS THE DC COMPONENT, not this sample. Scrubbing modulates the
        # load by design, so an instantaneous reading lands wherever in the
        # cycle it happens to land: measured, a steady 4N press with a 3N
        # oscillation read 1.39N at the trough and dropped below the contact
        # floor while the sponge was very much still on the person. Averaging
        # over the window separates "is it touching" from "is it moving", which
        # are the two different questions this class answers.
        mean_mag = float(np.mean(self.hist)) if self.hist else mag
        force = (float(self.scale.newtons(mean_mag))
                 if self.scale is not None else None)
        force_now = (float(self.scale.newtons(mag))
                     if self.scale is not None else None)
        touching = (force is not None and force >= self.force_floor_n)

        # REMOVE THE MEAN BEFORE THE TRANSFORM. The quantity of interest is the
        # oscillation, not the press it rides on, and a Goertzel bin is not
        # blind to DC: 1.2Hz over a 2s window at 10Hz is 2.4 cycles, not a
        # whole number, so a constant leaks into the bin. Measured, a perfectly
        # steady 4N lean scored 0.26 against a scrub floor of 0.15 and was
        # credited as scrubbing -- the exact overclaim this test exists to
        # prevent, produced by the test itself.
        ac = np.asarray(self.hist, float)
        ac = ac - ac.mean()
        energy = goertzel(ac, self.f_scrub, self.fs) if \
            len(self.hist) >= self.window else 0.0
        mean = float(np.mean(self.hist)) if self.hist else 0.0
        ratio = energy / max(mean, 1e-9)
        return {"delta_shoulder": ds, "delta_elbow": de,
                "force_n": force, "contact": touching,
                "scrub_energy": energy, "scrub_ratio": ratio,
                "scrubbing": bool(touching and ratio >= self.energy_floor)}


# --- a synthetic arm, so all of this is testable with no hardware -----------

def _synthetic(j1, j2, load_n=0.0, scrub_amp_n=0.0, phase=0.0, noise=0.0,
               rng=None, gain=45.0):
    """Torque a real ST3215 pair would report. Same form the model fits.

    Coefficients are invented; the SHAPE is not, and the shape is what the
    model has to get right. A test that fits a model to data generated by that
    same model proves only the algebra, so the noise and the load are added
    outside it.
    """
    rng = rng or np.random.default_rng(0)
    tau_s = 420.0 * np.cos(j1) + 180.0 * np.cos(j1 + j2) + 25.0
    tau_e = 190.0 * np.cos(j1 + j2) - 10.0
    load = (load_n + scrub_amp_n * np.sin(phase)) * gain
    tau_s = tau_s + load
    tau_e = tau_e + load * 0.62
    if noise:
        tau_s = tau_s + rng.normal(0, noise, np.shape(tau_s))
        tau_e = tau_e + rng.normal(0, noise, np.shape(tau_e))
    return tau_s, tau_e


if __name__ == "__main__":
    print("torque: contact in newtons, not a boolean")
    rng = np.random.default_rng(3)

    # --- the free-air sweep -------------------------------------------------
    j1 = rng.uniform(-math.pi / 2, math.pi / 2, 400)
    j2 = rng.uniform(-1.0, math.pi, 400)
    ts, te = _synthetic(j1, j2, noise=4.0, rng=rng)
    g = GravityModel().fit(j1, j2, ts, te)
    print(f"\n  free-air fit over 400 poses:")
    print(f"    shoulder residual {g.rms[0]:5.1f}   elbow residual "
          f"{g.rms[1]:5.1f}   (injected noise 4.0)")
    assert g.rms[0] < 8.0 and g.rms[1] < 8.0, "gravity model does not fit"

    # --- THE POINT: the old fixed threshold is pose-dependent ---------------
    print(f"\n  why a fixed threshold cannot work. Free air, no contact:")
    print(f"    {'pose':26s} {'raw torS':>9s} {'old test':>9s} "
          f"{'delta':>7s} {'new test':>9s}")
    worst_raw, fired = 0.0, 0
    for label, a, b in (("extended, arm out", 0.0, 0.05),
                        ("half folded", 0.6, 1.2),
                        ("folded in close", 1.2, 2.2)):
        s, e = _synthetic(a, b, noise=0.0)
        ds, de = g.delta(a, b, s, e)
        old = abs(s) > 550.0
        fired += int(old)
        worst_raw = max(worst_raw, abs(float(s)))
        print(f"    {label:26s} {float(s):9.0f} {'FIRES' if old else 'quiet':>9s} "
              f"{float(ds):7.1f} {'FIRES' if abs(ds) > 40 else 'quiet':>9s}")
    print(f"    raw shoulder torque spans {worst_raw:.0f} across the workspace "
          f"with NOTHING under the arm")
    assert fired >= 1, "the synthetic arm never trips the old threshold"

    # --- newtons ------------------------------------------------------------
    loads = np.array([2.0, 4.0, 7.0])
    deltas = []
    for L in loads:
        s, e = _synthetic(0.4, 1.0, load_n=L, noise=2.0, rng=rng)
        ds, de = g.delta(0.4, 1.0, s, e)
        deltas.append(max(abs(float(ds)), abs(float(de))))
    fs = ForceScale().fit(np.array(deltas), loads)
    print(f"\n  scale calibration, three presses onto a kitchen scale:")
    for L, d in zip(loads, deltas):
        print(f"    {L:4.1f} N -> delta {d:7.1f} -> reads "
              f"{float(fs.newtons(d)):5.2f} N")
    print(f"    residual {fs.rms_n:.2f} N")
    assert fs.rms_n < 0.5, "force calibration is worse than half a newton"

    # --- scrubbing versus leaning ------------------------------------------
    print(f"\n  telling scrubbing from leaning, 2s of 10Hz feedback:")
    for label, amp in (("scrubbing at 1.2Hz", 3.0), ("leaning, no motion", 0.0)):
        cs = ContactSense(g, fs)
        out = None
        for i in range(40):
            ph = 2 * math.pi * 1.2 * (i / 10.0)
            s, e = _synthetic(0.4, 1.0, load_n=4.0, scrub_amp_n=amp, phase=ph,
                              noise=2.0, rng=rng)
            out = cs.update(0.4, 1.0, s, e)
        print(f"    {label:22s} force {out['force_n']:5.2f} N   "
              f"scrub ratio {out['scrub_ratio']:5.3f}   "
              f"{'SCRUBBING' if out['scrubbing'] else 'pressing only'}")
        if amp > 0:
            assert out["scrubbing"], "real scrubbing was not recognised"
        else:
            assert not out["scrubbing"], \
                "leaning on someone was credited as scrubbing"

    print("\n  contact is pose-independent, reported in newtons, and only "
          "counts\n  as coverage when the sponge is actually moving. OK")
