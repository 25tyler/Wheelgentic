"""Search a rig for the person as they sit now, with the project's own search.

    python scrub3d/live/search_rig.py RECORDING [--frame N] [--samples 500]
        [--sep 400] [--zmin 650] [--zmax 1050] [--out FILE]

Replays a recording to a resting frame, takes the posed live model and the
chair as obstacles, and runs place_arms.search on it: random mounts around
the person, the best re-ranked across the pose envelope, then refined. The
answer is written relative to the SEAT (x and y from the seat point, z above
the floor, facing in degrees), because a rig is bolted around a chair and the
world origin moves with how the person happens to sit.

The search plans on every other cell (areas scaled to match), which is four
times cheaper and changes the coverage figures by about a point; the winner is
scored again on every cell at the end.
"""
import argparse
import json
import os
import math
import sys
import time

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("frames")
ap.add_argument("--frame", type=int, default=80)
ap.add_argument("--samples", type=int, default=500)
ap.add_argument("--shortlist", type=int, default=12)
ap.add_argument("--keep", type=int, default=2)
ap.add_argument("--sweeps", type=int, default=1)
ap.add_argument("--k", type=int, default=2)
ap.add_argument("--out", default=os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "live_rig.json"))
ap.add_argument("--sep", type=float, default=400.0,
                help="mm between any two arm bases")
ap.add_argument("--zmin", type=float, default=650.0)
ap.add_argument("--zmax", type=float, default=1050.0)
ap.add_argument("--arc", type=float, default=190.0,
                help="degrees of the arc in front of the person bases may go on")
ap.add_argument("--park-gap", type=float, default=110.0,
                help="mm a parked arm must keep from the others' working poses")
a = ap.parse_args()
sys.argv = sys.argv[:1]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import live_body as LB  # noqa: E402
import arms_live as AL  # noqa: E402
import place_arms as PA  # noqa: E402

intr, frames = LB.replay_source(a.frames, loop=False)
live = LB.Live(intr, 420.0)
for i, (c, d, t) in enumerate(frames):
    ev = live.step(c, d, t)
    if i >= a.frame and "posed" in ev and live.seat is not None:
        break
body = live.body
full = body.as_model()
obs, reg = body.obstacles()
seat = live.seat
sx, sy = float(seat["xy"][0]), float(seat["xy"][1])
print(f"snapshot at frame {i}: seat at ({sx:.0f}, {sy:.0f}), {seat['z']:.0f} mm up; "
      f"{sum(r.n for r in full.regions)} cells, {len(obs)} obstacle points", flush=True)


def thin(bm):
    """Every other cell along and around each part; areas scaled to match."""
    out = []
    for r in bm.regions:
        n = r.n
        k = np.arange(n)
        # _swept lays cells out as 16 stations x 28 around, row-major.
        keep = ((k // 28) % 2 == 0) & ((k % 28) % 2 == 0)
        scale = r.area.sum() / max(r.area[keep].sum(), 1e-9)
        out.append(LB.BM.Region(r.name, r.T, r.pts[keep], r.nrm[keep],
                                r.area[keep] * scale, r.scrubbable[keep],
                                r.arc, r.n_exp))
    return LB.BM.BodyModel(out)


coarse = thin(full)

# --- what the search did not know: the arms get in each other's way ---------
# A rig can divide the body cleanly and still be unusable: an arm parked while
# another works has to be somewhere, and if every place it can wait is inside
# the other's working space, one of them is always held. So every candidate is
# also scored on its worst parking clearance, and bases are kept further apart.
PA.MIN_BASE_SEPARATION_MM = a.sep
_random_layout = PA.random_layout


def random_wide(body_, n_, rng, **kw):
    # Beside the person as well as in front: spread four bases that far apart.
    kw.setdefault("arc_deg", a.arc)
    kw.setdefault("z_range", (a.zmin, a.zmax))
    return _random_layout(body_, n_, rng, **kw)


PA.random_layout = random_wide
_solve = LB.PART.solve
_last = {}


def solve_keep(body, layout, **kw):
    out = _solve(body, layout, **kw)
    _last["key"] = (id(body), tuple(np.round(np.asarray(layout).ravel(), 3)))
    _last["terr"] = out[0]
    return out


LB.PART.solve = solve_keep
PARK = [np.array([60.0 * math.cos(math.radians(y)), 60.0 * math.sin(math.radians(y)), z])
        for z in (40.0, -40.0, -120.0) for y in (-68.0, 0.0, 68.0)]


def park_clearance(body, layout, terr, ceiling):
    """The worst, over arms, of the best clearance any folded parking pose
    keeps from every other arm's working poses. mm."""
    P, N, _I, _A = body.world_cells()
    helper = AL.Arms(layout=layout)
    work = {}
    for k in range(len(layout)):
        m = terr.owner == k
        if m.sum():
            work[k] = helper._work_samples(k, P[m], N[m], n=24)
    worst = float("inf")
    for k, T in enumerate(layout):
        best = -float("inf")
        for tcp in PARK:
            if ceiling is not None and (T @ np.r_[tcp, 1.0])[2] > ceiling - 60.0:
                continue
            j = LB.K.ik(*tcp)
            if j is None:
                continue
            mine = np.asarray(LB.COL.arm_capsules(T, *j))
            g = min([AL.capsule_gap(mine, w) for b, w in work.items()
                     if b != k and len(w)], default=float("inf"))
            best = max(best, g)
        worst = min(worst, best)
    return worst


_score = PA.score
CEILING = body.head_ceiling()


def score_parked(body_, layout, **kw):
    s, rep = _score(body_, layout, **kw)
    if s <= -0.5 or "terr" not in _last:
        return s, rep
    gap = park_clearance(body_, layout, _last["terr"], CEILING)
    rep["park_gap_mm"] = gap
    if gap < a.park_gap:
        s -= 0.6 * min(1.0, (a.park_gap - gap) / a.park_gap)
    rep["score"] = s
    return s, rep


PA.score = score_parked
cfg = dict(obstacle_points=obs, obstacle_region=reg, T_world_camera=live.T_wc)

old = LB.rigconfig.load().layout()
s_old, r_old = PA.score(full, old, envelope_k=a.k, **cfg)
prev = None
if os.path.exists(a.out):
    with open(a.out, encoding="utf-8") as f:
        prev_rel = json.load(f)["arms"]
    prev = [PA.pose(sx + r["x_from_seat_mm"], sy + r["y_from_seat_mm"], r["z_mm"],
                    math.radians(r["facing_deg"])) for r in prev_rel]
    s_prev, r_prev = PA.score(full, prev, envelope_k=a.k, **cfg)
    print(f"the last searched rig on this body: score {s_prev:+.3f}, coverage "
          f"{100 * r_prev['covered_frac']:.1f}%, parking clearance "
          f"{r_prev.get('park_gap_mm', float('nan')):.0f} mm", flush=True)
print(f"the rig in config.json on this body: score {s_old:+.3f}, coverage "
      f"{100 * r_old['covered_frac']:.1f}%, per arm "
      f"{[round(v) for v in r_old['per_arm_cm2']]} cm2", flush=True)

t0 = time.time()
lay, rep = PA.search(coarse, n_arms=4, n_random=a.samples, keep=a.keep,
                     shortlist=a.shortlist, sweeps=a.sweeps, rank_k=a.k,
                     refine_k=a.k, **cfg)
s_new, r_new = PA.score(full, lay, envelope_k=a.k, **cfg)
print(f"search took {(time.time() - t0) / 60:.1f} min", flush=True)
print(f"found: score {s_new:+.3f}, coverage {100 * r_new['covered_frac']:.1f}%, "
      f"imbalance {r_new['imbalance']:.2f}, phases {r_new['phases']}, per arm "
      f"{[round(v) for v in r_new['per_arm_cm2']]} cm2, camera blocked "
      f"{100 * r_new.get('camera_occluded', 0):.0f}%, parking clearance "
      f"{r_new.get('park_gap_mm', float('nan')):.0f} mm", flush=True)

arms = []
for k, T in enumerate(lay):
    yaw = math.degrees(math.atan2(T[1, 0], T[0, 0]))
    arms.append({"id": f"a{k}", "x_from_seat_mm": round(float(T[0, 3]) - sx, 1),
                 "y_from_seat_mm": round(float(T[1, 3]) - sy, 1),
                 "z_mm": round(float(T[2, 3]), 1), "facing_deg": round(yaw, 1)})
with open(a.out, "w", encoding="utf-8") as f:
    json.dump({"name": "searched on the live model, seated at rest",
               "frame": "x and y from the seat point, z above the floor",
               "score": s_new, "coverage": r_new["covered_frac"],
               "per_arm_cm2": r_new["per_arm_cm2"], "phases": r_new["phases"],
               "config_json_on_same_body": {"score": s_old,
                                            "coverage": r_old["covered_frac"]},
               "arms": arms}, f, indent=2)
print("wrote", a.out)
for arm in arms:
    print("  ", arm)
