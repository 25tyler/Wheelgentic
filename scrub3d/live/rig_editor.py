"""Place the arms by hand: drag them around the chair, turn them, save.

    python scrub3d/live/rig_editor.py                 # http://127.0.0.1:8078
    python scrub3d/live/rig_editor.py --rig other.json --port 8079

The person and the chair come from a recording (by default the newest in
scrub3d/data), so every check is against a seated person the camera
actually measured. Each change is checked at once: what every arm can reach,
bases too close together, an arm inside the chair or the person, how much
the arms hide from the camera, and whether each arm has somewhere safe to
wait, turned as its start pose asks (start_deg). Simulate runs the arms' real motion on the recording. Save writes the
rig file the live view reads, relative to the seat, and a live view that is
already running picks it up by itself.

Localhost only. It never talks to an arm.
"""
import argparse
import datetime
import http.server
import json
import math
import os
import shutil
import sys
import threading
import time
import traceback

import numpy as np
from scipy.spatial import ConvexHull, cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
SCRUB3D = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

PAGE = os.path.join(HERE, "rig_editor.html")
DEFAULT_RIG = os.path.join(HERE, "live_rig.json")
DATA = os.path.join(SCRUB3D, "data")


def newest_recording():
    """The latest recording with its frame times (the one in the repository
    if there is no other): the person and chair to place arms around."""
    names = []
    if os.path.isdir(DATA):
        names = sorted(n for n in os.listdir(DATA) if n.startswith("live_rec_")
                       and os.path.exists(os.path.join(DATA, n, "times.json")))
    return os.path.join(DATA, names[-1] if names else "live_rec_20260916")

# What a person can place: bases within this box around the seat, mm.
LIMITS = {"xy": 2500.0, "z_min": 100.0, "z_max": 1700.0, "arms_max": 8, "tilt": 180.0,
          "start": 88.0}
REACH_MM = 516.0          # shoulder to tool at full stretch (kinematics)
SHOULDER_MM = 123.0       # base to shoulder, up the base's own axis (kinematics)
BASE_R_MM = 45.0          # the base column's radius, as the capsules model it
KNOCK_MM = 20.0           # a base closer than this to the person is easy to knock


def hull(points):
    """2-D convex hull as a list of [a, b] points, or [] if degenerate."""
    P = np.asarray(points, float)
    if len(P) < 3:
        return []
    try:
        h = ConvexHull(P)
    except Exception:                                   # noqa: BLE001
        return []
    return [[round(float(P[i, 0]), 1), round(float(P[i, 1]), 1)] for i in h.vertices]


class Scene:
    """The seated person, the chair and the camera, relative to the seat."""

    def __init__(self, recording, seat_mm):
        import arms_live as AL                     # puts scrub3d/ on the path
        import live_body as LB
        import rig_sim
        self.AL, self.LB, self.SIM, self.armmesh = AL, LB, rig_sim, AL.armmesh
        t0 = time.time()
        self.track = rig_sim.Track(recording, seat_mm)
        if self.track.rest is None or self.track.seat is None:
            raise SystemExit("the recording never showed a seated person")
        body = self.track.rest
        self.body = body
        self.seat = self.track.seat
        self.sx, self.sy = self.track.seat_xy()
        self.bm = body.as_model()
        self.obs, self.reg = body.obstacles()
        self.ceiling = body.head_ceiling()
        self.T_wc = self.track.T_wc
        self.cells = self.bm.world_cells()
        self.took = time.time() - t0

    def rel(self, p):
        return float(p[0]) - self.sx, float(p[1]) - self.sy

    def geometry(self):
        """Outlines for the page: top (x, y) and side (x, z), seat-relative."""
        LB = self.LB
        parts = LB.part_arrays(self.body)
        top, side = [], []
        for name, (V, _C, _F) in parts.items():
            V = np.asarray(V, float)
            xy = np.stack([V[:, 0] - self.sx, V[:, 1] - self.sy], 1)
            xz = np.stack([V[:, 0] - self.sx, V[:, 2]], 1)
            kind = "head" if name in ("head", "neck") else "body"
            top.append({"name": name, "kind": kind, "z": float(V[:, 2].mean()),
                        "pts": hull(xy)})
            side.append({"name": name, "kind": kind, "y": float(V[:, 1].mean() - self.sy),
                         "pts": hull(xz)})
        chair_top, chair_side = [], []
        for bv, _bf in self.body.chair:
            bv = np.asarray(bv, float)
            chair_top.append(hull(np.stack([bv[:, 0] - self.sx, bv[:, 1] - self.sy], 1)))
            chair_side.append(hull(np.stack([bv[:, 0] - self.sx, bv[:, 2]], 1)))
        cam = self.T_wc[:3, 3]
        look = self.T_wc[:3, 2]                        # the camera's optical axis
        P, _N, I, A = self.cells
        return {
            "top": top, "side": side, "chair_top": chair_top, "chair_side": chair_side,
            "camera": {"x": float(cam[0] - self.sx), "y": float(cam[1] - self.sy),
                       "z": float(cam[2]), "dx": float(look[0]), "dy": float(look[1]),
                       "dz": float(look[2])},
            "seat_z": float(self.seat["z"]),
            "head_z": None if self.ceiling is None else float(self.ceiling),
            "cells": [[round(float(p[0] - self.sx)), round(float(p[1] - self.sy)),
                       round(float(p[2]))] for p in P],
            "area_cm2": round(float(A.sum()) / 100.0),
        }

    # --- a rig, checked ----------------------------------------------------
    def layout(self, rel):
        return self.SIM.layout_of(rel, self.seat)

    def check(self, rel):
        """Everything the page shows about a rig. -> dict"""
        AL, PART, PA, COL = self.AL, self.AL.PART, self.AL.PA, self.AL.COL
        layout = self.layout(rel)
        n = len(layout)
        out = {"warnings": [], "blocking": [], "arms": [{} for _ in range(n)]}
        names = [AL.NAMES[a % 4] for a in range(n)]

        # Bases: apart from each other, and out of the chair and the person.
        pos = np.array([T[:3, 3] for T in layout])
        for i in range(n):
            for j in range(i + 1, n):
                d = float(np.linalg.norm(pos[i] - pos[j]))
                if d < PA.MIN_BASE_SEPARATION_MM:
                    msg = (f"{names[i]} and {names[j]} bases are {d:.0f} mm apart; "
                           f"they need {PA.MIN_BASE_SEPARATION_MM:.0f}")
                    (out["blocking"] if d < 2 * BASE_R_MM + 10 else
                     out["warnings"]).append(msg)
        tree = cKDTree(self.obs)

        for a in range(n):
            base = COL.arm_capsules(layout[a], *AL.start_joints(rel[a].get("start_deg", 0)))[0]
            ts = np.linspace(0.0, 1.0, 12)[:, None]
            d, _ = tree.query(base[0] + (base[1] - base[0]) * ts)
            gap = float(d.min()) - COL.CAPSULE_RADII[0]
            out["arms"][a]["base_gap_mm"] = round(gap)
            if gap < 0:
                out["blocking"].append(f"{names[a]}'s base is inside the chair or the person")
            elif gap < KNOCK_MM:
                # The arm still works: moves are checked on what moves. The
                # base is just easy to knock this close.
                out["warnings"].append(f"{names[a]}'s base is {gap:.0f} mm from the "
                                       f"person or chair, easy to knock")
        if out["blocking"]:
            return out

        # Who scrubs what, and what each can reach safely right now.
        terr, _planes, phases, rep = PART.solve(
            self.bm, layout, envelope_k=PART.ENVELOPE_K,
            obstacle_points=self.obs, obstacle_region=self.reg, moving=True,
            d_body=AL.D_BODY_MM, standoff=AL.STANDOFF_MM, corridor=AL.CORRIDOR)
        _P, _N, _I, A = self.cells
        M = AL.reach_matrix(self.bm, layout, self.obs, self.reg, self.ceiling)
        terr.owner, ok = AL.assign_now(terr.owner, M, _P, layout)
        out["coverage"] = float(A[ok].sum() / max(A.sum(), 1e-9))
        out["planned_coverage"] = float(rep["covered_frac"])
        # Painted as the live view does: skin out of reach now in the colour
        # of the arm that takes it once the person moves (give_rest).
        shown = (AL.give_rest(terr.owner, _P, self.cells[1], layout) if AL.GIVE_REST
                 else terr.owner)
        out["owner"] = [int(v) for v in shown]
        out["ok"] = [bool(v) for v in ok]
        for a in range(n):
            mine = terr.owner == a
            out["arms"][a]["reach_cm2"] = round(float(A[mine & ok].sum()) / 100.0)
            if not (mine & ok).any():
                out["warnings"].append(f"{names[a]} can reach nothing safely")
        try:
            out["camera_blocked"] = float(PA.camera_occlusion(self.bm, layout,
                                                              self.T_wc, terr))
            if out["camera_blocked"] > 0.10:
                out["warnings"].append(f"the arms hide {100 * out['camera_blocked']:.0f}% "
                                       f"of the person from the camera")
        except Exception:                               # noqa: BLE001
            out["camera_blocked"] = None

        # Somewhere safe for each arm to wait, clear of the others' work.
        arms = AL.Arms(layout=layout, rel=rel)
        arms.gov = AL.FLEET.FleetGovernor(layout, z_ceiling_mm=self.ceiling,
                                          body_points=self.obs, moving=True,
                                          d_body=AL.D_BODY_MM, d_hold=AL.D_HOLD_MM,
                                          d_estop=AL.D_ESTOP_MM)
        P, N, _I, _A = self.cells
        work = {a: arms._work_samples(a, P[(terr.owner == a) & ok],
                                      N[(terr.owner == a) & ok], n=24)
                for a in range(n)}
        arms._park(self.ceiling, work)
        area = {a: float(A[(terr.owner == a) & ok].sum()) for a in range(n)}
        still = arms._crowded(area)
        for a in arms.unsafe_park:
            if a not in still:
                out["warnings"].append(f"{names[a]} has nowhere to wait well clear of "
                                       f"the person and the other arms")
        for a, why in sorted(still.items()):
            out["warnings"].append(f"{names[a]} will stay still: {why}")
        for a in range(n):
            want = max(-AL.START_MAX_DEG, min(AL.START_MAX_DEG,
                                              float(rel[a].get("start_deg", 0.0) or 0.0)))
            tcp = AL.K.fk(*tuple(arms.park_j[a])[:3])
            got = math.degrees(math.atan2(float(tcp[1]), float(tcp[0]) - AL.K.BASE_X_MM))
            out["arms"][a]["start_deg"] = round(got, 1)
            if abs(got - want) > 5.0 and a not in still:
                out["warnings"].append(
                    f"{names[a]} cannot wait turned {want:+.0f} degrees clear of the person "
                    f"and the other arms; it waits turned {got:+.0f}")
        # Who works when, as the live view does it: every arm with something
        # to do at once, or (TAKE_TURNS) apart where working poses meet.
        busy = {a for a in range(n) if (terr.owner == a).sum() >= 8 and a not in still}
        turns, _frac = AL.interference_phases(
            {a: w for a, w in work.items() if a in busy}, phases, n)
        if not AL.TAKE_TURNS:
            turns = [sorted(busy)]
        out["phases"] = [p for p in ([int(a) for a in ph if a in busy] for ph in turns) if p]
        for a in range(n):
            out["arms"][a]["still"] = still.get(a)
            out["arms"][a]["park"] = [round(float(v)) for v in
                                      (arms.home[a] - np.array([self.sx, self.sy, 0.0]))]
            # Where each link sits in the arm's own frame, waiting: the 3D view
            # places the arm by its base and draws the links from these.
            tf = self.armmesh.link_transforms(*tuple(arms.park_j[a])[:3])
            out["arms"][a]["links"] = {k: np.round(np.asarray(tf[k], float), 3).ravel().tolist()
                                       for k in self.armmesh.LINKS}
        return out

    def geometry3d(self):
        """Meshes for the 3D view, seat-relative, mm: the posed person, the
        chair, and each arm link in its own frame.

        Each vertex of a scrubbed part carries the scrub cell nearest it (an
        index into the check's owner list), so the page paints the skin by
        the arm that scrubs it; -1 elsewhere."""
        off = np.array([self.sx, self.sy, 0.0])
        _P, _N, I, _A = self.cells
        scrub = list(self.LB.SCRUB_PARTS)

        def flat(V):
            return np.round(np.asarray(V, float), 1).ravel().tolist()

        parts = []
        for name, (V, C, F) in self.LB.part_arrays(self.body).items():
            V = np.asarray(V, float)
            cell = np.full(len(V), -1)
            if name in scrub:
                mine = np.flatnonzero(I == scrub.index(name))
                if len(mine) > 5 and len(mine) == len(C):
                    tree = cKDTree(np.asarray(C, float))
                    d, j = tree.query(V)
                    # a vertex further from the patch than its cells are
                    # spaced is not on it (the back of a limb, say)
                    near = 1.25 * float(np.median(tree.query(C, k=5)[0][:, 4]))
                    cell = np.where(d <= near, mine[j], -1)
            parts.append({"name": name, "V": flat(V - off),
                          "F": np.asarray(F, int).ravel().tolist(),
                          "cell": cell.astype(int).tolist()})
        cm = self.body.chair_mesh()
        chair = None if cm is None else {"V": flat(np.asarray(cm[0], float) - off),
                                         "F": np.asarray(cm[1], int).ravel().tolist()}
        links = {name: {"V": flat(V), "F": np.asarray(F, int).ravel().tolist()}
                 for name, (V, F) in self.armmesh.meshes().items()}
        home = self.armmesh.link_transforms(*tuple(self.AL.HOME_J)[:3])
        home = {k: np.round(np.asarray(home[k], float), 3).ravel().tolist()
                for k in self.armmesh.LINKS}
        return {"parts": parts, "chair": chair, "links": links, "home_links": home,
                "shoulder_mm": SHOULDER_MM, "yaw_axis_mm": float(self.AL.K.BASE_X_MM)}


class Jobs:
    """One simulation at a time, off the request thread."""

    def __init__(self, scene):
        self.scene = scene
        self.lock = threading.Lock()
        self.state = {"running": False, "progress": 0.0, "result": None, "error": None}

    def start(self, rel, loops):
        with self.lock:
            if self.state["running"]:
                return False
            self.state = {"running": True, "progress": 0.0, "result": None,
                          "error": None, "rel": rel}

        def work():
            try:
                def progress(f):
                    self.state["progress"] = round(f, 3)
                rep = self.scene.SIM.simulate(self.scene.track, rel, loops=loops,
                                              progress=progress)
                rep["text"] = self.scene.SIM.summary(rep)
                self.state["result"] = plain(rep)
            except Exception:                           # noqa: BLE001
                self.state["error"] = traceback.format_exc()[-1500:]
            finally:
                self.state["progress"] = 1.0
                self.state["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return True


def plain(v):
    """Something JSON.parse takes: numpy made plain, and no Infinity or NaN
    (a run where no two arms met has an infinite closest gap)."""
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [plain(x) for x in v]
    if isinstance(v, np.ndarray):
        return plain(v.tolist())
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return float(v) if math.isfinite(v) else None
    if v is None or isinstance(v, str):
        return v
    return str(v)


def clean_rig(data):
    """The page's arms, validated. -> list of seat-relative arms."""
    arms = data.get("arms")
    if not isinstance(arms, list) or not 1 <= len(arms) <= LIMITS["arms_max"]:
        raise ValueError("a rig has 1 to 8 arms")
    out = []
    for k, r in enumerate(arms):
        vals = {}
        for key in ("x_from_seat_mm", "y_from_seat_mm", "z_mm", "facing_deg"):
            v = float(r[key])
            if not math.isfinite(v):
                raise ValueError(f"arm {k}: {key} is not a number")
            vals[key] = round(v, 1)
        if abs(vals["x_from_seat_mm"]) > LIMITS["xy"] or \
                abs(vals["y_from_seat_mm"]) > LIMITS["xy"]:
            raise ValueError(f"arm {k} is more than {LIMITS['xy']:.0f} mm from the seat")
        if not LIMITS["z_min"] <= vals["z_mm"] <= LIMITS["z_max"]:
            raise ValueError(f"arm {k}: height must be {LIMITS['z_min']:.0f} to "
                             f"{LIMITS['z_max']:.0f} mm")
        vals["facing_deg"] = round((vals["facing_deg"] + 180.0) % 360.0 - 180.0, 1)
        v = float(r.get("start_deg", 0.0) or 0.0)
        if not math.isfinite(v) or abs(v) > LIMITS["start"]:
            raise ValueError(f"arm {k}: its start turn must be within "
                             f"{LIMITS['start']:.0f} degrees of straight ahead")
        if abs(v) >= 0.05:
            vals["start_deg"] = round(v, 1)
        for key in ("tilt_deg", "roll_deg"):
            v = float(r.get(key, 0.0) or 0.0)
            if not math.isfinite(v) or abs(v) > LIMITS["tilt"]:
                raise ValueError(f"arm {k}: {key} must be within "
                                 f"{LIMITS['tilt']:.0f} degrees of level")
            if abs(v) >= 0.05:
                vals[key] = round(v, 1)
        out.append({"id": str(r.get("id") or f"a{k}"), **vals})
    return out


def load_rig(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_rig(path, arms, check):
    """Write the rig, keeping the one it replaces beside it."""
    if os.path.exists(path):
        shutil.copyfile(path, os.path.splitext(path)[0] + ".previous.json")
    doc = {
        "name": "placed by hand in the rig editor",
        "frame": "x and y from the seat point, z above the floor",
        "saved": datetime.datetime.now().isoformat(timespec="seconds"),
        "arms": arms,
    }
    if check and "coverage" in check:
        doc["coverage"] = round(check["coverage"], 4)
        doc["per_arm_cm2"] = [a.get("reach_cm2") for a in check["arms"]]
        doc["phases"] = check.get("phases")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return doc


def make_handler(scene, jobs, rig_path):
    geometry = scene.geometry()
    mesh3d = json.dumps(plain(scene.geometry3d())).encode("utf-8")
    check_lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):                  # quiet
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(
                plain(body), allow_nan=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if self.path == "/api/scene":
                doc = load_rig(rig_path)
                return self._send(200, {
                    "geometry": geometry, "rig": doc, "rig_path": rig_path,
                    "limits": LIMITS, "reach_mm": REACH_MM, "base_r_mm": BASE_R_MM,
                    "min_gap_mm": scene.AL.PA.MIN_BASE_SEPARATION_MM,
                    "names": list(scene.AL.NAMES),
                })
            if self.path == "/api/mesh3d":
                return self._send(200, mesh3d)
            if self.path == "/api/simulate":
                return self._send(200, {k: v for k, v in jobs.state.items() if k != "rel"})
            return self._send(404, {"error": "no such page"})

        def do_POST(self):
            try:
                data = self._body()
                if self.path == "/api/check":
                    rel = clean_rig(data)
                    with check_lock:
                        t0 = time.time()
                        res = scene.check(rel)
                        res["seconds"] = round(time.time() - t0, 2)
                    return self._send(200, res)
                if self.path == "/api/simulate":
                    rel = clean_rig(data)
                    started = jobs.start(rel, int(data.get("loops", 2)))
                    return self._send(200 if started else 409,
                                      {"started": started})
                if self.path == "/api/save":
                    rel = clean_rig(data)
                    with check_lock:
                        res = scene.check(rel)
                    if res["blocking"]:
                        return self._send(409, {"saved": False,
                                                "why": res["blocking"]})
                    doc = save_rig(rig_path, rel, res)
                    return self._send(200, {"saved": True, "rig": doc,
                                            "path": rig_path})
                return self._send(404, {"error": "no such call"})
            except (ValueError, KeyError, TypeError) as exc:
                return self._send(400, {"error": str(exc)})
            except Exception:                           # noqa: BLE001
                return self._send(500, {"error": traceback.format_exc()[-1500:]})

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rig", default=DEFAULT_RIG)
    ap.add_argument("--recording", default=None,
                    help="default: the newest recording in scrub3d/data")
    ap.add_argument("--seat-mm", type=float, default=420.0)
    ap.add_argument("--port", type=int, default=8078)
    a = ap.parse_args()
    sys.argv = sys.argv[:1]
    rig_path = os.path.abspath(a.rig)
    if not os.path.exists(rig_path):
        raise SystemExit(f"no rig file at {rig_path}")
    recording = a.recording or newest_recording()
    print(f"following the person in {os.path.basename(recording)}...", flush=True)
    scene = Scene(recording, a.seat_mm)
    print(f"ready in {scene.took:.0f}s", flush=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", a.port),
                                             make_handler(scene, Jobs(scene), rig_path))
    print(f"rig editor on http://127.0.0.1:{a.port}  (saves {rig_path})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
