"""py/dirt.py — fluorescent-tracer detection. Makes "is the dirt real?" answerable.

THE SETUP: a 365nm UV lamp on the forearm, a fluorescent tracer on the skin.
Fluorescence EMITS VISIBLE light, so an ordinary webcam sees it fine -- no UV
camera needed. Buy 365nm, NOT 395nm: 395 floods the frame with visible violet,
which IS blue-excess, so it attacks your signal and your discriminator at once.

THE DISCRIMINATOR IS HUE + BRIGHTNESS, NOT SATURATION. Measured on this
machine over skin tones and four tracer colours:

    sample                       H    S    V   blue-excess
    skin, dim                   14   94  152        -41
    skin, bright                16   81  205        -50
    skin, warm/orange           14  153  175        -78
    Glo Germ (blue-white)       96  105  255        +63
    Glo Germ, dimmer            96  113  215        +58
    yellow highlighter (green)  39  165  255       -140
    green paint                 53  175  255       -108

Two traps this table kills:

1. BLUE-EXCESS (B-(R+G)/2) works for Glo Germ (+63) and MISSES the yellow
   highlighter entirely (-140). Most people buy the highlighter.
2. SATURATION ALONE is worse: Glo Germ is blue-WHITE, so S=105 -- BELOW warm
   skin at S=153. An S>120 rule detects the highlighter and misses the actual
   hospital tracer, while false-positiving on an orange-lit forearm. (My first
   version did exactly this; the synthetic suite caught it.)

What actually separates them: skin is ALWAYS H=14-16 (orange-red). Every
fluorophore is H=39-96 (green->cyan->blue). And fluorescence is BRIGHT while a
darkened UV scene is not. So: bright AND not-skin-hued.

WHY NOT THE PAPER'S NNLS SPECTRAL UNMIXING: its own ablation says full unmixing
beats the plain channel by only 22-28%. Cite it, ship 30 lines.
(Journal of Imaging 2026, DOI 10.3390/jimaging12040178)

THE HONEST CLAIM for judges: "365nm UV plus a hue and brightness threshold
on the same fluorescent tracer hospitals use for hand-hygiene training."
Say HUE, not saturation: the measured table above is the proof that
saturation alone fails, and fluor_mask() thresholds V and the hue band.
docs/RECOVERY-CARD.md:126 says hue and brightness; these two must not
drift. Never claim it is running if it is not -- one caught overclaim
discounts everything else.
"""
import math
import cv2
import numpy as np

# Defaults tuned for a darkened scene under 365nm. RE-TUNE UNDER YOUR LAMP
# with tools/tune_dirt.py, then FREEZE the numbers -- do not keep fiddling on
# demo day.
V_MIN = 190        # fluorescence is BRIGHT (measured 215-255); dim skin is 152
S_MIN = 60         # reject near-grey highlights (specular glare on skin)
SKIN_HUE_MAX = 26  # OpenCV hue is 0-179. Skin measured H=14-16; every tracer
SKIN_HUE_MIN = 160 # is H=39-96. Wrap-around covers deep reds near 179.
MIN_AREA = 80      # px; smaller blobs are sensor noise


def fluor_mask(bgr, v_min=V_MIN, s_min=S_MIN,
               skin_lo=SKIN_HUE_MAX, skin_hi=SKIN_HUE_MIN):
    """Binary mask of fluorescing pixels.

    Rule: BRIGHT and NOT skin-hued. See the module docstring for the measured
    table that rules out saturation-only and blue-excess.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    H, S, V = cv2.split(hsv)
    bright = V > v_min
    not_grey = S > s_min
    # skin/red band is [0, skin_lo] U [skin_hi, 179]; a tracer is outside it
    not_skin = (H > skin_lo) & (H < skin_hi)
    m = (bright & not_grey & not_skin).astype(np.uint8) * 255
    # OPEN kills salt noise, CLOSE fills a speckled blob into one region.
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN,  np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    return m


def blobs(mask, min_area=MIN_AREA):
    """Connected components, largest first. Label 0 is background -- skip it."""
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    out = [{"area": int(stats[i, cv2.CC_STAT_AREA]),
            "cx": float(cent[i][0]), "cy": float(cent[i][1])}
           for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area]
    return sorted(out, key=lambda b: -b["area"])


def project_to_limb(px, py, ax, ay, bx, by):
    """-> (t, perp, t_raw).

    t is the SAME 0..1 number the cartoon uses for splotch placement, so
    detection and projector cannot drift apart. t_raw is unclamped so callers
    can reject off-limb blobs BEFORE the clamp hides them.

    perp is distance to the INFINITE LINE through the limb, deliberately NOT
    to the clamped segment. Measuring to the clamped point conflates "beside
    the limb" with "past its end": a blob 150px beyond the elbow but perfectly
    ON AXIS reported perp=150, so the perpendicular test silently did the
    t_raw test's job and the two guards were not independent. Caught by
    planting a bug in the t_raw check and watching nothing fail.
    """
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    if L2 == 0:
        return 0.0, math.hypot(px - ax, py - ay), 0.0
    t_raw = ((px - ax) * vx + (py - ay) * vy) / L2
    t = max(0.0, min(1.0, t_raw))
    # distance to the infinite line (2D cross product / |v|)
    perp = abs((px - ax) * vy - (py - ay) * vx) / math.sqrt(L2)
    return t, perp, t_raw


def assign(blob, ax, ay, bx, by, perp_frac=0.18, t_pad=0.1):
    """Blob -> {t, perp} on the limb, or None if it is not on the limb.

    TWO rejections that a naive version gets wrong:

    1. Reject on t_raw BEFORE the clamp. A blob 150px past the elbow and one
       190px past both report t=0.0 after clamping -- indistinguishable from a
       real blob AT the elbow.
    2. PROPORTIONAL perpendicular threshold. A fixed 90px band is ~2.3x wider
       than a real forearm (half-width ~35-40px at typical framing) and will
       happily claim blobs sitting on the table.
    """
    t, perp, t_raw = project_to_limb(blob["cx"], blob["cy"], ax, ay, bx, by)
    seg_len = math.hypot(bx - ax, by - ay)
    if seg_len <= 0:
        return None
    if not (-t_pad <= t_raw <= 1.0 + t_pad):
        return None
    if perp > perp_frac * seg_len:
        return None
    return {"t": round(t, 3), "perp": round(perp, 1), "area": blob["area"]}


def cleanliness(initial_area, current_area, min_area=MIN_AREA):
    """0..100 from remaining tracer area. NOT what the projector shows.

    IT USED TO BE. Until the count unification, the UV path sent this number to
    the browser while the scripted path sent the count of positions cleaned --
    two different numbers for the same physical event, agreeing only because the
    synthetic tracer blobs happen to be equal-area. Both paths now send the
    count. This survives as a vision-side helper and as UV's corroboration
    signal: it says how much tracer is left, which is the one thing geometry
    cannot tell you.

    Two guards that are not optional:
      - initial_area is 0 in the single most likely demo scenario: judges walk
        up, nobody has applied tracer yet, camera running. Dividing by it
        raises mid-demo.
      - CLAMP. A blob that grows slightly (the arm rotates, more tracer comes
        into view) yields NEGATIVE cleanliness. That used to drive the
        projector's counter, where it visibly ran BACKWARDS in front of judges;
        it no longer does, but a negative here would still break the 0..100
        contract for any caller that trusts it.
    """
    if initial_area < min_area:
        return 100.0
    return 100.0 * max(0.0, min(1.0, 1.0 - current_area / initial_area))


class DirtTracker:
    """Frame-to-frame state: baseline area, per-splotch cleanliness.

    Usage per frame:
        tr.observe(frame_bgr, elbow_px, wrist_px)   -> list of {t, perp, area}
        tr.cleanliness_pct()                        -> 0..100
    """

    def __init__(self, v_min=V_MIN, s_min=S_MIN, min_area=MIN_AREA,
                 skin_lo=SKIN_HUE_MAX, skin_hi=SKIN_HUE_MIN):
        # THE HUE BAND IS TUNABLE TOO. tools/tune_dirt.py has always printed
        # dirt_skin_hue_max / dirt_skin_hue_min for the operator to paste into
        # config.json, and NOTHING read them -- DirtTracker did not accept
        # them and fluor_mask() always got its module defaults. So an operator
        # whose venue lighting shifted skin hue would tune the slider, paste
        # the numbers, see no change, and have no way to tell why. Hue is the
        # discriminator the whole detector rests on (see the measured table in
        # the module docstring); it is the LAST control that should be dead.
        self.v_min, self.s_min, self.min_area = v_min, s_min, min_area
        self.skin_lo, self.skin_hi = skin_lo, skin_hi
        self.initial_area = 0
        self.current_area = 0
        self.spots = []
        self.frames = 0

    def observe(self, bgr, elbow, wrist):
        self.frames += 1
        if elbow is None or wrist is None:
            return []
        m = fluor_mask(bgr, self.v_min, self.s_min,
                       self.skin_lo, self.skin_hi)
        found = [a for a in (assign(b, elbow[0], elbow[1], wrist[0], wrist[1])
                             for b in blobs(m, self.min_area)) if a]
        self.spots = found
        self.current_area = sum(s["area"] for s in found)
        # Baseline is the LARGEST area seen, not the first frame: the tracer may
        # not be fully in view on frame one, and a first-frame baseline would
        # make cleanliness jump negative the moment more of it appears.
        self.initial_area = max(self.initial_area, self.current_area)
        return found

    def cleanliness_pct(self):
        return cleanliness(self.initial_area, self.current_area, self.min_area)

    def reset(self):
        self.initial_area = self.current_area = 0
        self.spots = []
