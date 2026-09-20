"""scrub3d/tools/plan_rig.py -- where do the four arms go, for THIS rig?

This is the end of the chain and the question the whole package exists to
answer. Everything upstream feeds it:

    frames.py   the world, from the floor the camera can see
    scan.py     this person, where they actually sat, from one capture
    scan.py     the obstacles, as measured points: chair, lap, head, trunk
    place_arms  search base poses, score each with the real partition

Output is four sets of numbers someone can take to a bench with a tape
measure and a drill.

WHY THIS IS NOT THE SAME AS RUNNING place_arms.py
--------------------------------------------------
place_arms.py's own self-test searches against the PROCEDURAL body: a
population-average adult at an assumed height, with its own mesh vertices as
the only obstacle. That is the right thing for checking the search works.

It is the wrong thing for deciding where to drill, because it does not know
where this chair is, how high this person's shoulders sit, or that there is a
wall 210mm behind the backrest. This does.
"""
import argparse
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import frames as FRAME                                           # noqa: E402
import partition as P                                            # noqa: E402
import place_arms as PA                                          # noqa: E402
import scan as SCAN                                              # noqa: E402

# The layout currently hardcoded in control.py and viz.py, kept as the thing
# to beat. A search that cannot beat a guess is not earning its runtime.
SHIPPED = [(21, -500, 760, 80.8), (472, -218, 760, 155.2),
           (472, 218, 760, -155.2), (21, 500, 760, -130.8)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", default=os.path.join(FRAME.DATA, "scan01"))
    ap.add_argument("--sweeps", type=int, default=2)
    ap.add_argument("--envelope-k", type=int, default=4,
                    help="poses used DURING the search; the winner is always "
                         "re-verified at full fidelity")
    ap.add_argument("--verify-k", type=int, default=4)
    ap.add_argument("--samples", type=int, default=900,
                    help="random rigs sampled before any local refinement")
    ap.add_argument("--shortlist", type=int, default=20,
                    help="cheap-scored candidates re-ranked across the envelope")
    ap.add_argument("--rank-k", type=int, default=4,
                    help="envelope size for that re-ranking")
    ap.add_argument("--keep", type=int, default=3,
                    help="how many of the best samples get refined")
    ap.add_argument("--search-voxel", type=float, default=30.0,
                    help="obstacle downsample DURING the search, mm. The "
                         "winner is always re-verified against the full cloud")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the answer without writing config.json")
    a = ap.parse_args()

    print(f"planning the rig from {os.path.basename(a.capture)}\n")
    body, meshes, obstacles, rep = SCAN.scan(a.capture)
    obs_region = rep["obstacle_region"]
    rig = FRAME.solve(a.capture)
    T_cam = rig["T_world_camera"]

    f = rep["floor"]
    print(f"  the rig, measured, not assumed:")
    print(f"    camera   {f['camera_height_mm']:.0f}mm above the floor, "
          f"{f['pitch_down_deg']:.1f} deg down, {f['roll_deg']:+.1f} deg roll")
    print(f"    subject  acromion {rep['shoulder_z_mm']:.0f}mm up, "
          f"measuring {rep['clothing']['measuring']}")
    print(f"    surface  {rep['scrubbable_cm2']:.0f}cm2 scrubbable across "
          f"{sum(1 for d in rep['parts'].values() if d.get('ok') and d['scrubbable'])} limbs")
    print(f"    obstacles {len(obstacles)} measured points "
          f"(chair, lap, head and trunk, no shape assumed)")

    def report(tag, layout, k):
        terr, planes, phases, r = P.solve(body, layout, envelope_k=k,
                                          obstacle_points=obstacles,
                                          obstacle_region=obs_region)
        occl = PA.camera_occlusion(body, layout, T_cam, terr)
        load = np.array(r["per_arm_cm2"], float)
        imb = (load.max() / max(load.mean(), 1e-9)) - 1.0
        print(f"    {tag:22s} coverage {100 * r['covered_frac']:5.1f}%  "
              f"imbalance {imb:4.2f}  phases {len(phases)}  "
              f"camera-blocked {100 * occl:4.1f}%  "
              f"per-arm cm2 {[round(v) for v in load]}")
        return r, phases

    shipped = [PA.pose(x, y, z, math.radians(d)) for x, y, z, d in SHIPPED]
    print(f"\n  before searching, at full fidelity (k={a.verify_k}):")
    report("the shipped layout", shipped, a.verify_k)

    # A coarser obstacle cloud DURING the search only. Clearance is a minimum
    # distance to a surface, and sampling that surface every 60mm moves the
    # minimum by at most ~30mm while the search runs an order of magnitude
    # faster. The winner is re-verified against every point, and that is the
    # number quoted.
    coarse, coarse_region = SCAN._voxel_labelled(obstacles, obs_region,
                                                 a.search_voxel)
    print(f"\n  searching: {a.samples} random rigs, then refining the best "
          f"{a.keep}")
    print(f"    {len(coarse)} obstacle points at {a.search_voxel:.0f}mm during "
          f"the search; the full {len(obstacles)} for the verify")
    layout, _ = PA.search(body, n_random=a.samples, keep=a.keep,
                          shortlist=a.shortlist, sweeps=a.sweeps,
                          obstacle_points=coarse,
                          obstacle_region=coarse_region, envelope_k=0,
                          rank_k=a.rank_k, refine_k=a.envelope_k,
                          T_world_camera=T_cam)

    print(f"\n  the answer, in numbers you can take to a bench:")
    PA.describe(layout)
    gap = PA._too_close(layout)
    print(f"    closest two bases {gap:.0f}mm "
          f"(minimum buildable {PA.MIN_BASE_SEPARATION_MM:.0f}mm)")
    assert gap >= PA.MIN_BASE_SEPARATION_MM, "search returned an unbuildable rig"

    print(f"\n  both layouts at full fidelity (k={a.verify_k}), which is the "
          f"only comparison worth quoting:")
    r_ship, _ = report("shipped", shipped, a.verify_k)
    r_best, phases = report("searched", layout, a.verify_k)

    d = 100 * (r_best["covered_frac"] - r_ship["covered_frac"])
    print(f"\n    the search is worth {d:+.1f} points of coverage on this rig")
    print(f"    phases {phases}  "
          f"(arms sharing a phase may run at the same time)")

    # A searched layout that loses to the guess means the objective disagrees
    # with the verifier, which is worth knowing loudly rather than shipping.
    if d < -0.5:
        print("\n    WARNING: the search LOST to the shipped guess. That means "
              "the cheap envelope it optimised against does not predict the "
              "full one. Raise --envelope-k and rerun before trusting either.")

    # Write it where everything else reads from, but only if it actually won.
    # A search that lost and then overwrote the rig would be worse than one
    # that never ran.
    # AND IT MUST ALSO BEAT WHAT IS ALREADY IN THE FILE, not just the
    # hardcoded baseline. This search is stochastic: 900 samples with two
    # refined candidates does not reliably find the optimum of a space of
    # positions-plus-yaw, so a later run can be worse than an earlier one.
    # Measured -- one run found +0.496 and the next found +0.403, and without
    # this the second would have overwritten the first and called it progress.
    beat_file = True
    try:
        import rigconfig
        current = rigconfig.load().layout()
        s_cur, _ = PA.score(body, current, envelope_k=a.verify_k,
                            T_world_camera=T_cam, obstacle_points=obstacles,
                            obstacle_region=obs_region)
        s_new, _ = PA.score(body, layout, envelope_k=a.verify_k,
                            T_world_camera=T_cam, obstacle_points=obstacles,
                            obstacle_region=obs_region)
        beat_file = s_new > s_cur
        print(f"\n    against the rig already in config.json: "
              f"{s_new:+.3f} vs {s_cur:+.3f}"
              f"  -> {'replace' if beat_file else 'KEEP THE EXISTING RIG'}")
    except Exception as exc:                                    # noqa: BLE001
        print(f"\n    could not score the existing rig ({exc}); "
              f"treating this search as the better one")

    if d > 0.5 and beat_file and not a.dry_run:
        import rigconfig
        cfg = rigconfig.from_layout(
            layout, name=f"searched against {os.path.basename(a.capture)}",
            provenance=(
                f"scrub3d/tools/plan_rig.py, {a.samples} random rigs, top "
                f"{a.shortlist} re-ranked across a k={a.rank_k} pose envelope, "
                f"best {a.keep} refined over {a.sweeps} sweeps. Verified at "
                f"k={a.verify_k} against {len(obstacles)} measured obstacle "
                f"points: {100 * r_best['covered_frac']:.1f}% coverage against "
                f"{100 * r_ship['covered_frac']:.1f}% for the hand-placed "
                f"layout."))
        path = rigconfig.save(cfg)
        print(f"\n    wrote {path} -- viz.py, main.py and control.py now read "
              f"this rig")
    elif a.dry_run:
        print("\n    --dry-run: config.json not written")
    elif not beat_file:
        print("\n    the search LOST to the rig already in config.json, so "
              "the file is left alone. A run's best is not the best known.")
    else:
        print("\n    the search did not beat the shipped layout, so "
              "config.json is left alone")

    print("\nOK")


if __name__ == "__main__":
    main()
