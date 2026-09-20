"""scrub3d/handeye.py -- where is each arm bolted, relative to the camera?

    T_world_arm  <- the ONLY thing the partition needs to place an arm

This is what makes "put the arms anywhere" cost nothing extra. The procedure
is identical wherever they are, so arbitrary placement adds no calibration
work; it just means the answer is different.

THIS IS NOT calibrateHandEye()
-------------------------------
OpenCV's `calibrateHandEye` solves AX=XB, for a camera mounted ON a gripper
with an unknown rigid offset. Our camera is bolted to a tripod and the arm
reports the Cartesian position of its own tool point, so we have two sets of
CORRESPONDING 3D points and want the rigid transform between them. That is
point-set registration, and Kabsch solves it exactly in closed form. Reaching
for AX=XB here would be solving a harder problem than we have, with more
unknowns and worse conditioning.

TWO PASSES, BECAUSE THEY BUY DIFFERENT THINGS
----------------------------------------------
    COARSE   a board on each arm's base, all four seen at once, seconds.
             Position lands within 5-15mm and rotation is the weak axis: 2
             degrees of base rotation is 17mm of error at 500mm of reach.
             Good enough to PARTITION -- a 10mm error almost never changes
             which arm owns a patch.

    FINE     a plate on the tool, the arm driving ITSELF to 15-20 poses.
             3-5mm RMS. Required before any arm is allowed to touch someone.

That split is what lets you re-place an arm, re-run the coarse pass and see
new territories immediately, with the slow pass only before contact.

THE GATE THAT MATTERS MOST IS NOT THE RESIDUAL
-----------------------------------------------
Kabsch will fit any point set, and a set clustered in a plane fits BEAUTIFULLY
while being almost unconstrained perpendicular to that plane. So the residual
looks excellent exactly when the answer is worst. `conditioning()` measures the
spread of the samples themselves, and a run that fails it is refused however
good its RMS looks.

And a fit that is exact by construction cannot detect its own systematic error
-- a wrong tool offset or a wrong tag size moves every point identically and
Kabsch absorbs it into `t` with zero residual. So `verify()` takes a FIFTH
observation the fit never saw, and that is the only number that can catch it.
"""
import numpy as np

# Minimum sigma_min/sigma_max on the centred sample matrix. Below this the
# poses are effectively coplanar and the transform is under-determined in the
# direction you cannot see.
MIN_CONDITION = 0.30

# RMS gates, millimetres.
GO_MM = 5.0             # may make contact
HOVER_MM = 10.0         # may move, may not touch
# above HOVER_MM: no-go


def conditioning(points):
    """How well-spread a set of samples is. -> sigma_min / sigma_max in [0,1].

    1.0 is a perfect sphere of samples, 0.0 is exactly planar. This is the
    check that catches the failure Kabsch cannot report: a planar sample set
    gives a tiny residual and an answer that is free to slide perpendicular to
    the plane.
    """
    P = np.asarray(points, float)
    if len(P) < 4:
        return 0.0
    s = np.linalg.svd(P - P.mean(0), compute_uv=False)
    return float(s[-1] / max(s[0], 1e-12))


def kabsch(A, B):
    """Rigid transform taking A onto B. -> (R, t, rms, residuals).

    No scale. The arm reports millimetres and so does the camera, so a fitted
    scale would only absorb a real error -- a wrong tool offset, say -- and
    hide it in a number that looks like a unit conversion.
    """
    A, B = np.asarray(A, float), np.asarray(B, float)
    ca, cb = A.mean(0), B.mean(0)
    H = (A - ca).T @ (B - cb)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    # The reflection guard. Without it a noisy or near-degenerate set can
    # produce a determinant of -1, which fits the points and mirrors the arm.
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    t = cb - R @ ca
    res = np.linalg.norm((A @ R.T + t) - B, axis=1)
    return R, t, float(np.sqrt(np.mean(res ** 2))), res


def solve(arm_points, camera_points, labels=None):
    """Arm-frame points + camera-frame points -> the calibration and its gates.

    `arm_points` come from the arm's OWN confirmed feedback, not from the
    target that was commanded: the firmware clamps, and a clamped target that
    is recorded as achieved puts the error straight into the calibration.
    """
    A = np.asarray(arm_points, float)
    B = np.asarray(camera_points, float)
    if len(A) != len(B):
        raise ValueError(f"{len(A)} arm points against {len(B)} camera points")

    cond = conditioning(A)
    R, t, rms, res = kabsch(A, B)
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, t

    if len(A) < 8:
        verdict, why = "no-go", f"only {len(A)} poses; want 15 to 20"
    elif cond < MIN_CONDITION:
        verdict, why = "no-go", (
            f"poses are nearly coplanar (conditioning {cond:.3f} < "
            f"{MIN_CONDITION}). The residual is meaningless here.")
    elif rms <= GO_MM:
        verdict, why = "go", "may make contact"
    elif rms <= HOVER_MM:
        verdict, why = "hover", "may move but must not touch"
    else:
        verdict, why = "no-go", f"RMS {rms:.1f}mm over {HOVER_MM}mm"

    return {"T_world_arm": T, "rms_mm": rms, "conditioning": cond,
            "n": len(A), "residuals_mm": res, "verdict": verdict, "why": why,
            "labels": labels or [f"pose {i}" for i in range(len(A))]}


def verify(cal, arm_point, camera_point):
    """A FIFTH observation the fit never saw. -> (error_mm, ok).

    calibrate.py:68-80 in the existing project says it plainly: a fit that is
    exact by construction cannot detect its own systematic error. A wrong tool
    offset or a wrong tag size displaces every calibration point identically,
    Kabsch folds it into the translation, and the residual stays beautiful.
    Only a point outside the fit can catch it.
    """
    T = cal["T_world_arm"]
    got = T[:3, :3] @ np.asarray(arm_point, float) + T[:3, 3]
    err = float(np.linalg.norm(got - np.asarray(camera_point, float)))
    return err, err <= GO_MM


def cross_validate(cals, arm_points_of_common_fiducial):
    """Every arm touches ONE fiducial; they must agree. -> (spread_mm, ok).

    Without this the collision model is fiction. Each arm can be individually
    well-calibrated to a camera and still disagree with its neighbour about
    where a shared point in the room is, and the capsule separation between
    two arms is computed from exactly that disagreement.
    """
    pts = []
    for cal, p in zip(cals, arm_points_of_common_fiducial):
        T = cal["T_world_arm"]
        pts.append(T[:3, :3] @ np.asarray(p, float) + T[:3, 3])
    pts = np.array(pts)
    spread = float(np.linalg.norm(pts - pts.mean(0), axis=1).max())
    return spread, spread <= GO_MM


def report(cal):
    """Print it the way calibrate.verify does: the band, then the table."""
    print(f"    {cal['n']} poses   RMS {cal['rms_mm']:.2f}mm   "
          f"conditioning {cal['conditioning']:.3f}   -> "
          f"{cal['verdict'].upper()}: {cal['why']}")
    r = cal["residuals_mm"]
    worst = np.argsort(r)[::-1][:3]
    for i in worst:
        print(f"      worst residual: {cal['labels'][i]:14s} {r[i]:6.2f}mm")


if __name__ == "__main__":
    print("hand-eye: where is each arm bolted?")
    rng = np.random.default_rng(1)

    def make_truth(yaw, pos):
        c, s = np.cos(yaw), np.sin(yaw)
        T = np.eye(4)
        T[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
        T[:3, 3] = pos
        return T

    truth = make_truth(np.radians(155.2), [472.0, -218.0, 760.0])

    def observe(A, T, noise_mm=0.0):
        B = A @ T[:3, :3].T + T[:3, 3]
        return B + rng.normal(0, noise_mm, B.shape) if noise_mm else B

    # --- a good spread of 18 poses -----------------------------------------
    A = np.c_[rng.uniform(150, 420, 18), rng.uniform(-200, 200, 18),
              rng.uniform(80, 420, 18)]
    cal = solve(A, observe(A, truth, noise_mm=1.5))
    print("\n  18 well-spread poses, 1.5mm detection noise:")
    report(cal)
    err = np.degrees(np.arccos(np.clip(
        (np.trace(cal["T_world_arm"][:3, :3].T @ truth[:3, :3]) - 1) / 2,
        -1, 1)))
    print(f"      recovered pose within "
          f"{np.linalg.norm(cal['T_world_arm'][:3, 3] - truth[:3, 3]):.2f}mm "
          f"and {err:.3f} deg of truth")
    assert cal["verdict"] == "go", cal["why"]
    assert err < 0.5

    # --- THE ONE THAT MATTERS: a beautiful residual on a useless set --------
    flat = np.c_[rng.uniform(150, 420, 18), rng.uniform(-200, 200, 18),
                 np.full(18, 250.0) + rng.normal(0, 1.0, 18)]
    bad = solve(flat, observe(flat, truth, noise_mm=1.5))
    print("\n  18 poses all at the same height (a plane):")
    report(bad)
    assert bad["verdict"] == "no-go" and "coplanar" in bad["why"], \
        "a coplanar pose set was accepted"
    print(f"      note the RMS is {bad['rms_mm']:.2f}mm -- BETTER than the good "
          f"set. Residual\n      cannot see this failure; conditioning can.")

    # --- a systematic error the fit absorbs silently ------------------------
    offset = np.array([0.0, 0.0, 14.0])          # wrong tool offset, 14mm
    biased = solve(A, observe(A, truth, noise_mm=1.5) + offset)
    print(f"\n  a 14mm tool-offset error applied to every point:")
    report(biased)
    print(f"      the fit still passes. It cannot see a shift it can absorb "
          f"into t.")
    assert biased["verdict"] == "go"

    fifth_arm = np.array([300.0, 40.0, 500.0])
    fifth_cam = truth[:3, :3] @ fifth_arm + truth[:3, 3]      # the TRUE place
    e, ok = verify(biased, fifth_arm, fifth_cam)
    print(f"      a fifth observation the fit never saw: {e:.1f}mm  "
          f"-> {'ok' if ok else 'CAUGHT IT'}")
    assert not ok, "the independent check failed to catch a 14mm bias"

    # --- four arms agreeing about one point in the room --------------------
    print("\n  four arms, one common fiducial:")
    cals, arm_pts = [], []
    for yaw, pos in ((80.8, [21, -500, 760]), (155.2, [472, -218, 760]),
                     (-155.2, [472, 218, 760]), (-130.8, [21, 500, 760])):
        T = make_truth(np.radians(yaw), pos)
        Ai = np.c_[rng.uniform(150, 420, 18), rng.uniform(-200, 200, 18),
                   rng.uniform(80, 420, 18)]
        cals.append(solve(Ai, observe(Ai, T, noise_mm=1.5)))
        world_fid = np.array([300.0, 0.0, 900.0])
        arm_pts.append(T[:3, :3].T @ (world_fid - T[:3, 3]))
    spread, ok = cross_validate(cals, arm_pts)
    print(f"    they agree about it within {spread:.2f}mm  "
          f"-> {'ok' if ok else 'REFUSE: the collision model would be fiction'}")
    assert ok

    print("\n  conditioning gates the fit, and an unseen point gates the "
          "systematic\n  error the fit cannot see. OK")
