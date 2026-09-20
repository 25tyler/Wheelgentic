"""py/calibrate.py — click 4 corners, get pixel->robot millimetres. Run once.

    python py/calibrate.py         # interactive
    rm homography.pkl              # force recalibration after a bumped tripod

WHY HOMOGRAPHY, NOT DEPTH: the forearm rests on a table, so the scrub surface
is a KNOWN PLANE. Depth is a constant you measure with a ruler.

WHY NOT hand-eye: the camera is on a STATIC tripod and the robot base is
static, so this is a fixed plane-to-plane map, not AX=XB. And
cv2.calibrateHandEye is DELETED in OpenCV 5.0 while its CALIB_HAND_EYE_*
enums survive -- code that looks correct fails at CALL time, not import time.

WHAT scrub3d/handeye.py HAS THAT THIS ONE DID NOT, and now does: it refuses a
fit whose SAMPLES are badly spread, however good the residual looks, because
"the residual looks excellent exactly when the answer is worst". That applies
here with more force, not less -- a 4-point residual is zero BY CONSTRUCTION.
GUARD 3 in solve() is that idea, in this file's geometry. handeye.py itself
stays where it is: it needs 15 to 20 poses of the arm's OWN tool-point
feedback, and this arm has never moved. See docs/CALIBRATION.md.

THE 30-SECOND DETAIL THAT DELETES PARALLAX: tape the A4 sheet on top of a
BOOK so its surface sits at FOREARM HEIGHT (~45mm), not flat on the table.
Calibrating at table height leaves ~16mm of parallax error; at forearm height
it is 0.
"""
import datetime
import json
import os
import pickle, sys
import cv2, numpy as np

# How many mm the answer moves per 1px of error on ONE clicked corner.
# MEASURED, not chosen: 90 legitimate camera placements were rendered through
# a pinhole model (0.5m to 3.5m, 0 to 70 degrees oblique, 1280x720, fx=900).
# The WORST legitimate placement -- 3.5m away at 70 degrees -- reads 5.18.
# Degenerate click sets start at 9.86. So the two bands below cannot reject a
# real setup, and they do catch every degenerate one measured.
#
# Restated the way the operator already reads this file: at 5 mm/px a careful
# 2px mis-click is 10mm of target error, which is exactly verify()'s GO limit;
# at 10 mm/px it is 20mm, which is exactly verify()'s "you mis-clicked, redo"
# line. The bands fall out of the sponge's own tolerance, not out of taste.
SENS_WARN_MM_PER_PX = 5.0
SENS_BAD_MM_PER_PX = 10.0

# A4 = 297 x 210 mm, expressed in the ROBOT's frame (+X forward from base,
# +Y to the arm's left, mm). Measure the offset once with a tape from the
# robot base to the sheet's top-left corner, then edit these two numbers.
SHEET_X0, SHEET_Y0 = 150.0, -105.0
WORLD_MM = np.float32([
    [SHEET_X0,       SHEET_Y0],          # TL
    [SHEET_X0 + 297, SHEET_Y0],          # TR
    [SHEET_X0 + 297, SHEET_Y0 + 210],    # BR
    [SHEET_X0,       SHEET_Y0 + 210],    # BL
])
LABELS = ["TOP-LEFT", "TOP-RIGHT", "BOTTOM-RIGHT", "BOTTOM-LEFT"]


def sensitivity(px, world_mm=WORLD_MM, probe=None):
    """How many mm the answer moves per 1px of click error. -> float.

    GRAFTED FROM scrub3d/handeye.py::conditioning(). That file's hardest-won
    line is "the residual looks excellent exactly when the answer is worst":
    a fit to a degenerate sample set is beautiful and useless, so it gates on
    the SPREAD OF THE SAMPLES rather than on the residual. A 4-point
    homography has the identical disease in a worse form -- its residual is
    not merely optimistic, it is ZERO BY CONSTRUCTION (see solve() below) --
    and nothing here was checking the click geometry at all.

    handeye's own number does not port: it is sigma_min/sigma_max on a 3D
    sample cloud, and four clicked pixels are coplanar by definition, so it
    reads 0.000 for a perfect click set and a hopeless one alike (measured).
    The 2D analogue sigma_min/sigma_max on the pixel quad is better but still
    WRONG: it rates a sheet clicked tiny-and-far-away at 0.49, healthy-looking,
    while that setup actually turns 1px into 19.7mm. Shape is only half of it;
    scale is the other half.

    So this measures the quantity the operator actually cares about, directly:
    nudge each corner 1px in each direction, re-solve, and see how far the
    mapped centre moves. Worst of the 16. MEASURED against a Monte Carlo over
    the same click sets, it tracks across a 35x range -- 0.35 vs 0.64 mm on a
    good quad, 12.19 vs 130.05 on an edge-on one.

    Why the centre and not a corner: the corners are exact by construction on
    a 4-point solve, so they move by roughly the click itself and report
    nothing. The interior is where the error actually lives.
    """
    P = np.float32(px)
    H0 = cv2.getPerspectiveTransform(P, world_mm)
    if probe is None:
        probe = P.mean(axis=0)
    base = cv2.perspectiveTransform(
        np.float32([[[probe[0], probe[1]]]]), H0).ravel()
    worst = 0.0
    for i in range(4):
        for ax in (0, 1):
            for d in (-1.0, 1.0):
                Q = P.copy()
                Q[i, ax] += d
                Hq = cv2.getPerspectiveTransform(Q, world_mm)
                got = cv2.perspectiveTransform(
                    np.float32([[[probe[0], probe[1]]]]), Hq).ravel()
                worst = max(worst, float(np.linalg.norm(got - base)))
    return worst


def solve(px, world_mm=WORLD_MM, verbose=True):
    """4 clicked pixel corners -> homography, with both silent-failure guards.

    Split out from the UI so it is unit-testable. Returns H or raises.
    """
    px = np.float32(px)

    # getPerspectiveTransform is the purpose-built EXACTLY-4-point call.
    # findHomography with 4 points does the same plain DLT solve -- default
    # method=0 is NOT RANSAC, so its mask is meaningless and there is zero
    # outlier rejection either way.
    H = cv2.getPerspectiveTransform(px, world_mm)

    # ---- GUARD 1: round-trip residual. A clean solve is ~1e-13 mm. Anything
    # nonzero means a mis-click. This is the ONLY way a 4-point solve tells
    # you it went wrong.
    back = cv2.perspectiveTransform(px.reshape(1, 4, 2), H).reshape(4, 2)
    resid = float(np.abs(back - world_mm).max())
    if verbose:
        print(f"max corner residual: {resid:.6f} mm")
    assert resid < 1.0, f"BAD CALIBRATION: {resid:.3f}mm residual -- re-click"

    # ---- GUARD 2: corner ORDER. Clicking TL,TR,BL,BR instead of TL,TR,BR,BL
    # raises NO exception and silently produces a ~4000mm error. This assert
    # is the only thing that catches it -- and it is the likeliest way to lose
    # an hour at 3am.
    ctr = cv2.perspectiveTransform(np.float32([[px.mean(axis=0)]]), H).ravel()
    if verbose:
        print(f"sheet centre -> {ctr[0]:.1f}, {ctr[1]:.1f} mm")
    assert 0 <= ctr[0] <= 700 and -400 <= ctr[1] <= 400, \
        f"CORNER ORDER WRONG: centre maps to {ctr} mm. Re-click TL,TR,BR,BL."

    # ---- MIS-CLICK: NOT DETECTABLE FROM 4 POINTS. Do not pretend otherwise.
    # A 4-point homography is EXACT by construction, so the residual is always
    # ~0 no matter where you clicked. MEASURED on synthetic data: a corner
    # dragged 30px -> 14.1mm true error, 60px -> 27.0mm, 150px -> 60.2mm, and
    # the residual reads 0.000000 in every single case. Geometric checks
    # (diagonal ratio, diagonal-midpoint separation, split-triangle areas)
    # all drift SMOOTHLY with the error and never cross a threshold that
    # would not also reject a legitimately oblique camera. There is no
    # information in 4 points to catch this.
    #
    # The ONLY real check is a 5th INDEPENDENT point: verify() below.
    # Run it. It is 30 seconds and it is the difference between a scrub that
    # lands on a forearm and one that lands 3cm away.

    # ---- GUARD 3: CLICK GEOMETRY. The paragraph above is right that four
    # points cannot tell you WHERE you mis-clicked. It is not the whole story:
    # they CAN tell you how much a mis-click would COST, and that is a
    # different question with a real answer.
    #
    # This is the idea grafted from scrub3d/handeye.py, which refuses a fit
    # whose samples are too poorly spread no matter how good its residual
    # looks. MEASURED here on click sets this function used to accept in
    # silence: a sheet clicked near-collinear (camera almost edge-on) turns
    # 1px of hand tremor into 80mm at the sheet centre, and a sheet clicked
    # tiny because the camera is far away turns it into 20mm. Both passed
    # GUARD 1 with a 0.000000 residual and passed GUARD 2 because the centre
    # still lands inside the box. Nothing anywhere said a word.
    #
    # THIS WARNS, IT DOES NOT RAISE, and that is deliberate. An assert here
    # can refuse to calibrate at the venue -- and a slightly ill-conditioned
    # calibration that the operator knows about still runs the demo, while a
    # hard stop does not. The numbers are actionable on their own: move the
    # camera squarer to the sheet, or closer, and re-click.
    sens = sensitivity(px, world_mm)
    if verbose:
        print(f"click sensitivity: {sens:.2f} mm per pixel "
              f"(a 2px mis-click costs {sens * 2:.0f}mm)")
    if sens >= SENS_BAD_MM_PER_PX:
        print("\n" + "!" * 64)
        print(f"  ILL-CONDITIONED CLICK GEOMETRY: {sens:.1f} mm per pixel.")
        print(f"  One pixel of hand tremor is {sens:.0f}mm on the forearm, and")
        print(f"  a careful 2px mis-click is {sens * 2:.0f}mm -- past the 20mm")
        print("  redo line that verify() prints below.")
        print("  The residual above CANNOT see this. It reads 0.000000 here.")
        print("  FIX IT: move the camera squarer to the sheet, or closer so")
        print("  the sheet fills more of the frame. Then re-click.")
        print("!" * 64 + "\n")
    elif sens >= SENS_WARN_MM_PER_PX and verbose:
        print(f"  NOTE: {sens:.1f} mm/px is on the edge. A 2px mis-click is "
              f"{sens * 2:.0f}mm.\n  Squarer or closer would help. "
              f"Run verify() and believe the tape.")
    return H


def calibrate(cam_index=0, out="homography.pkl", mirror=True):
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        sys.exit("Camera blocked -- System Settings > Privacy & Security > Camera")

    pts = []
    def on_click(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
            pts.append([float(x), float(y)])
            print(f"  corner {len(pts)}: ({x},{y})")

    cv2.namedWindow("calib"); cv2.setMouseCallback("calib", on_click)
    print("\n*** PUT THE A4 SHEET ON A BOOK AT FOREARM HEIGHT, NOT THE TABLE. ***")
    print("That removes ~16mm of parallax for free. Click CLOCKWISE from top-left.\n")

    while len(pts) < 4:
        ok, f = cap.read()
        if not ok:
            continue
        if mirror:
            f = cv2.flip(f, 1)          # MUST match vision.py's mirror setting
        for i, p in enumerate(pts):
            cv2.circle(f, (int(p[0]), int(p[1])), 7, (0, 255, 0), -1)
            cv2.putText(f, str(i + 1), (int(p[0]) + 10, int(p[1])),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(f, f"CLICK: {LABELS[len(pts)]}", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 255), 2)
        cv2.imshow("calib", f)
        if cv2.waitKey(1) == 27:
            cap.release(); cv2.destroyAllWindows(); sys.exit("aborted")

    cap.release(); cv2.destroyAllWindows()
    H = solve(pts)
    # REMEMBER THE CLICK GEOMETRY, not just the transform it produced. The
    # pickle holds H alone, so nothing downstream -- load(), the demo, a
    # reviewer the next morning -- can ever recompute how shaky the clicks
    # that made it were. Stash it next to the tape check so the two halves of
    # "is this calibration trustworthy" live in one file: GUARD 3 says how
    # much a mis-click would COST, the tape check says whether one HAPPENED.
    # handeye.py's docstring is explicit that you need both and that neither
    # substitutes for the other.
    _stash_sensitivity(sensitivity(pts))
    pickle.dump(H, open(out, "wb"))
    # A REAL CALIBRATION RETIRES THE FIXTURE SENTINEL. The recovery card says
    # "rm homography.pkl && python py/calibrate.py", which leaves
    # .homography-is-synthetic behind -- so every run afterwards screamed
    # "THIS IS A TEST FIXTURE, NOT A REAL CALIBRATION" over a calibration that
    # was real. An operator who learns to ignore that banner has also learned
    # to ignore it on the day it is true, and it is the banner standing
    # between the sponge and a person's forearm 30cm off target.
    _sent = os.path.join(os.path.dirname(os.path.abspath(out)) or ".",
                         ".homography-is-synthetic")
    if os.path.exists(_sent):
        try:
            os.remove(_sent)
            print("removed .homography-is-synthetic — this calibration is real")
        except OSError as e:
            print(f"could not remove {_sent}: {e}\n"
                  f"  DELETE IT BY HAND or every run will claim this "
                  f"calibration is a test fixture.")
    print(f"wrote {out}. Bumped the tripod? Run this again -- 90 seconds.")
    return H


def load(path="homography.pkl"):
    """Load the calibration, LOUDLY refusing a test fixture.

    tests/fixture.py generates a synthetic homography under this exact
    filename so the suite can run without a camera. A leftover one is
    indistinguishable from a real calibration and would put the sponge ~30cm
    off a person's forearm with no warning. The sentinel makes that visible.
    """
    import os
    try:
        H = pickle.load(open(path, "rb"))
    except FileNotFoundError:
        raise SystemExit(f"\nNo {path}. Run: python py/calibrate.py\n")

    sentinel = os.path.join(os.path.dirname(os.path.abspath(path)) or ".",
                            ".homography-is-synthetic")
    if os.path.exists(sentinel):
        print("\n" + "!" * 64)
        print("!! THIS IS A TEST FIXTURE, NOT A REAL CALIBRATION.")
        print("!! The arm will scrub in the wrong place.")
        print("!!   rm homography.pkl .homography-is-synthetic")
        print("!!   python py/calibrate.py")
        print("!" * 64 + "\n")
    return H


def px_to_mm(H, px, py):
    p = cv2.perspectiveTransform(np.float32([[[px, py]]]), H)
    return float(p[0][0][0]), float(p[0][0][1])


def verify(H, cam_index=0, mirror=True):
    """THE mis-click check. Click ONE known point, compare to a tape measure.

    A 4-point solve cannot self-validate (see solve()). This adds the 5th,
    independent observation that actually can. Run it every time you
    calibrate -- especially at the venue, on the real table.

    Procedure: put a coin somewhere on the sheet. Measure its distance from
    the robot base with a tape, in mm. Click it. Compare.
    """
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        sys.exit("Camera blocked")
    pt = []
    def on_click(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN and not pt:
            pt.append((float(x), float(y)))
    cv2.namedWindow("verify"); cv2.setMouseCallback("verify", on_click)
    print("\nPut a coin on the sheet. CLICK IT. Then measure it with a tape.")
    while not pt:
        ok, f = cap.read()
        if not ok:
            continue
        if mirror:
            f = cv2.flip(f, 1)
        cv2.putText(f, "CLICK THE COIN", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 255), 2)
        cv2.imshow("verify", f)
        if cv2.waitKey(1) == 27:
            break
    cap.release(); cv2.destroyAllWindows()
    if not pt:
        # ESC IS ALSO A SILENT SKIP. 1846f23 closed this hole on the SKIP_VERIFY
        # side and left this door open: every print() below is after this return,
        # so pressing ESC without clicking gave the operator no GO/NO-GO band and
        # no warning that the solve is unvalidated. Same consequence as the env
        # var, reached by a keypress the window invites ("CLICK THE COIN" and a
        # waitKey(27) exit). Found by reading that commit as a hostile reviewer.
        print("\n" + "!" * 64)
        print("  NO POINT CLICKED -- THE TAPE CHECK DID NOT COMPLETE.")
        print("  This homography is UNVALIDATED. A mis-clicked corner looks")
        print("  exactly like a good solve from here.")
        print("  Re-run the check before anything touches a person:")
        print("    python py/calibrate.py")
        print("!" * 64 + "\n")
        return
    mm = px_to_mm(H, *pt[0])
    print(f"\n  that point is at X={mm[0]:.1f}mm  Y={mm[1]:.1f}mm from the base.")
    print(f"  MEASURE IT WITH A TAPE NOW, and type the two numbers in.")
    print(f"  Within {_GO_MM:.0f}mm  -> GO (the sponge compresses 10-15mm, it "
          f"absorbs this)")
    print(f"  {_GO_MM:.0f} to {_HOVER_MM:.0f}mm -> HOVER: the arm may move, it "
          f"must not touch a person.")
    print(f"  Over {_HOVER_MM:.0f}mm -> you mis-clicked a corner. "
          f"rm homography.pkl and redo.\n")

    # THE NUMBER THE OPERATOR MEASURED, ENTERED AND RECORDED. Until now the
    # two lines above were the end of it: the tape reading stayed in the
    # operator's head, nothing compared it to the band, and nothing
    # downstream could tell a calibration that had passed the check from one
    # where somebody glanced at the coin and walked away. That is the same
    # hole SKIP_VERIFY and the ESC path each got closed for, left open at the
    # one point where the check actually produces evidence.
    #
    # Blank still means "not entered", and that is recorded as a refusal
    # rather than a pass -- an unanswered question is not a good answer.
    _record_tape_check(mm)
    return mm


# The GO / HOVER / no-go bands. IMPORTED, not retyped: scrub3d/handeye.py is
# the project's calibration gate and already owns these numbers together with
# the argument for them (a fit exact by construction cannot see its own
# systematic error, so only an independent observation decides). This file had
# 10mm and 20mm written into a print(); handeye says 5mm and 10mm. Two
# different bands for the same decision, in one repo, is how an arm gets
# cleared to touch somebody on a calibration the other half of the codebase
# would have refused.
#
# The import is guarded because py/ must keep starting on a machine that has
# no scrub3d -- --replay exists for exactly the case where the heavy stack is
# gone. The fallback is handeye's OWN numbers, not the old looser pair, so a
# missing package can never silently widen the band that lets a sponge touch
# a person.
def _bands():
    try:
        import sys as _sys
        _s3d = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "scrub3d")
        if _s3d not in _sys.path:
            _sys.path.insert(0, _s3d)
        import handeye
        return float(handeye.GO_MM), float(handeye.HOVER_MM), "scrub3d/handeye.py"
    except Exception:                                           # noqa: BLE001
        return 5.0, 10.0, "handeye's values, inlined (scrub3d not importable)"


_GO_MM, _HOVER_MM, _BAND_SOURCE = _bands()

# Where the answer is written down. scrubbot.py reads this at startup, so a
# calibration that never passed the tape check cannot quietly become the one
# a demo runs on.
TAPE_CHECK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".homography-tape-check.json")


def classify(error_mm):
    """A measured tape error -> the band it falls in. -> (verdict, why).

    The SAME three-way split handeye.solve() applies, against the same two
    thresholds, because it is the same decision: may this thing touch a
    person. Split out as a pure function so a test can call it without a
    camera, a coin or a tape measure.
    """
    if not (error_mm == error_mm) or error_mm < 0:      # NaN or nonsense
        return "no-go", "no usable tape measurement"
    if error_mm <= _GO_MM:
        return "go", "may make contact"
    if error_mm <= _HOVER_MM:
        return "hover", "may move but must not touch"
    return "no-go", (f"{error_mm:.1f}mm over {_HOVER_MM:.0f}mm -- a corner was "
                     f"mis-clicked; rm homography.pkl and redo")


def _stash_sensitivity(mm_per_px):
    """Carry GUARD 3's number forward into the tape-check record.

    MERGE-SAFE BY CONSTRUCTION: it reads whatever is there, sets one key and
    writes it back atomically. _record_tape_check() runs AFTER this during a
    calibration and rebuilds the record from scratch, so it re-reads this key
    and carries it over rather than dropping it. Neither function has to know
    the other's field list.
    """
    rec = {}
    try:
        with open(TAPE_CHECK_PATH) as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        rec = {}                     # absent or torn: start clean, never throw
    rec["sensitivity_mm_per_px"] = round(float(mm_per_px), 2)
    rec["sensitivity_bands"] = {"warn": SENS_WARN_MM_PER_PX,
                                "bad": SENS_BAD_MM_PER_PX}
    tmp = TAPE_CHECK_PATH + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(rec, fh, indent=2)
        os.replace(tmp, TAPE_CHECK_PATH)
    except OSError as e:
        # NEVER let bookkeeping kill a calibration. The operator is standing
        # at the tripod; losing the click geometry is a note, losing the
        # calibration is the demo.
        print(f"  (could not record click sensitivity: {e})")


def _record_tape_check(predicted_mm, _unused=None):
    """Ask for the tape reading, judge it, write the verdict down. -> verdict."""
    try:
        raw = input("  tape reading, X Y in mm (blank = did not measure): ")
    except (EOFError, KeyboardInterrupt):
        raw = ""
    parts = raw.replace(",", " ").split()
    if len(parts) != 2:
        rec = {"verdict": "no-go", "why": "the tape check was not completed",
               "error_mm": None, "predicted_mm": [round(v, 1) for v in predicted_mm]}
    else:
        try:
            tx, ty = float(parts[0]), float(parts[1])
        except ValueError:
            tx = ty = float("nan")
        err = float(np.hypot(predicted_mm[0] - tx, predicted_mm[1] - ty))
        verdict, why = classify(err)
        rec = {"verdict": verdict, "why": why, "error_mm": round(err, 1),
               "predicted_mm": [round(v, 1) for v in predicted_mm],
               "tape_mm": [round(tx, 1), round(ty, 1)]}
    rec["bands_mm"] = {"go": _GO_MM, "hover": _HOVER_MM, "source": _BAND_SOURCE}
    # CARRY THE CLICK GEOMETRY OVER. This function rebuilds `rec` from
    # scratch, so without these two lines _stash_sensitivity()'s keys -- written
    # moments earlier by the same calibration run -- are silently dropped and
    # the record answers only half of "is this calibration trustworthy".
    for _k in ("sensitivity_mm_per_px", "sensitivity_bands"):
        try:
            with open(TAPE_CHECK_PATH) as _fh:
                _prev = json.load(_fh)
        except (OSError, ValueError):
            break
        if _k in _prev:
            rec[_k] = _prev[_k]
    rec["when"] = datetime.datetime.now().isoformat(timespec="seconds")
    tmp = TAPE_CHECK_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rec, fh, indent=2)
    os.replace(tmp, TAPE_CHECK_PATH)     # atomic: a torn file reads as absent
    print(f"\n  tape check: {rec['verdict'].upper()} -- {rec['why']}")
    print(f"  recorded in {os.path.basename(TAPE_CHECK_PATH)}\n")
    return rec["verdict"]


def tape_check(path=None):
    """What the last tape check concluded. -> dict, or None if never run.

    `path=None` means "wherever TAPE_CHECK_PATH points NOW", not the value it
    had when this function was defined. A default argument is evaluated once
    at import, so `path=TAPE_CHECK_PATH` froze the location and made the
    constant un-overridable -- which is exactly how a test (or a second
    checkout) can end up reading a file nobody is writing and concluding the
    check was never run.

    Read by py/scrubbot.py at startup. Returning None for "never run" and a
    no-go record for "run and failed" are DIFFERENT answers on purpose: the
    first means nobody has checked, the second means somebody checked and it
    was bad, and an operator needs to be told which.
    """
    try:
        with open(path if path is not None else TAPE_CHECK_PATH) as fh:
            rec = json.load(fh)
    except (FileNotFoundError, ValueError):
        return None
    return rec if isinstance(rec, dict) and "verdict" in rec else None


if __name__ == "__main__":
    import os
    H = calibrate()
    if os.environ.get("SKIP_VERIFY") != "1":
        verify(H)
    else:
        # A SILENT SKIP HERE IS THE WORST CASE. verify() is the only independent
        # observation that the 4-point solve landed -- a mis-clicked corner is
        # invisible to the solve itself (see solve()), and RECOVERY-CARD.md sends
        # the operator here twice promising "the tape check". An inherited
        # SKIP_VERIFY used to remove it without printing anything, so a bumped
        # tripod could be "recalibrated" in 90s with nothing validating the result
        # and the sponge 30cm off a forearm. The escape hatch stays for bench work;
        # it no longer hides.
        print("\n" + "!" * 64)
        print("  SKIP_VERIFY=1 -- THE TAPE CHECK DID NOT RUN.")
        print("  This homography is UNVALIDATED. A mis-clicked corner looks")
        print("  exactly like a good solve from here.")
        print("  Before anything touches a person: unset SKIP_VERIFY and run")
        print("    python py/calibrate.py")
        print("  or call calibrate.verify(H) and measure with a tape.")
        print("!" * 64 + "\n")
