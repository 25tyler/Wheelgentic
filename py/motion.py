"""py/motion.py — trajectory shaping. Two functions, no dependencies.

Rejected ruckig and toppra: both solve ONLINE RE-PLANNING under jerk limits,
and you have a fixed scripted routine. toppra additionally has no macOS wheel.
"""
import math, time


def smooth5(t):
    """SMOOTHERSTEP, t^3(t(6t-15)+10). Zero velocity AND zero acceleration at
    both endpoints; bit-identical to the Flash & Hogan minimum-jerk quintic.

    NOT 'smoothstep' (3t^2-2t^3), which lives on the SAME Wikipedia page, has
    NON-ZERO endpoint acceleration, and still produces the torque step you can
    hear as a click. Grabbing the first formula silently loses the benefit.
    """
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


def _pace(send, points, hz):
    """Pace against an ABSOLUTE wall-clock deadline. time.sleep(1/hz)
    accumulates drift and your 1.5s move silently becomes 1.6-2.3s."""
    t0 = time.perf_counter()
    for i, p in enumerate(points):
        send(*p)
        d = t0 + (i + 1) / hz - time.perf_counter()
        if d > 0:
            time.sleep(d)


def travel(send, a, b, secs, hz=40):
    """Smooth point-to-point. Used for the bucket dip and the approach."""
    # A MISMATCH IS NOT A ROUNDING ERROR. zip() below would silently drop the
    # extra axis, so a 2-tuple against a 3-tuple travels in x,y and leaves z
    # wherever it was -- the sponge arrives at the wrong HEIGHT, on a person,
    # and nothing raises. Every caller builds 3-tuples today (HOME is a
    # 4-tuple, sliced [:3] at scripted.py:63); this refuses the day one stops.
    if len(a) != len(b):
        raise ValueError(f"travel endpoints differ: {len(a)} vs {len(b)}")
    # hz reaches _pace's deadline arithmetic as a DIVISOR. No caller passes it
    # today, but scrub_offset one function below was crashed by exactly this
    # shape from a hot-reloaded config, and a dead vision thread leaves the arm
    # holding its last target. Clamp rather than trust.
    hz = max(1.0, float(hz))
    n = max(2, int(secs * hz))
    _pace(send, [tuple(p + (q - p) * smooth5(i / n) for p, q in zip(a, b))
                 for i in range(n + 1)], hz)


def scrub_offset(elapsed, duration, freq_hz=1.2, amp_mm=35.0):
    """Signed offset along the forearm axis, in mm.

    A raw sine reads as *vibrating*. Two cheap fixes:

    1. Hann envelope sin(pi*f)**2 so the swing fades IN and OUT instead of
       snapping to full amplitude on stroke one. Do NOT use sin(pi*f)**0.6 --
       ANY exponent < 1 has an INFINITE derivative at the endpoints, so the
       'gentle fade-in' exponent actually fades in MORE abruptly than a plain
       sine. Exactly backwards.

    2. |sin|**1.15 with the sign preserved dwells slightly at the stroke ends,
       so it reads to the eye as PRESSING rather than wobbling. That exponent
       is >1 so its slope is finite and safe.

    Four lines of comedy value.
    """
    # config.json HOT-RELOADS, so a typo at the venue lands here mid-demo.
    # scrub_seconds=0 raised ZeroDivisionError, which kills the vision thread
    # -- the arm then holds its last target on a forearm with no state machine
    # left to retreat it. Clamp instead of trusting the config.
    if not (duration > 1e-6):
        return 0.0
    f = min(max(elapsed / duration, 0.0), 1.0)
    env = math.sin(math.pi * f) ** 2                   # Hann. NOT **0.6.
    ph = 2.0 * math.pi * freq_hz * elapsed
    s = math.sin(ph)
    stroke = math.copysign(abs(s) ** 1.15, s)          # end-dwell
    return amp_mm * env * stroke
