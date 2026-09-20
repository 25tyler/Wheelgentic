"""scrub3d/live/openyam_rig.py -- the two OpenYAMs' rig file from a tape measure.

    python scrub3d/live/openyam_rig.py --span 800 --height 700 --ahead 100 \
        --facing 0 [--facing-right 0] [--out scrub3d/live/live_rig_openyam.json]

Three numbers, measured on the chair with nobody in it:

  --span     mm between the two arm bases, centre to centre, across the plank
  --height   mm from the floor to the top of the plank the bases sit on
  --ahead    mm from the SEAT POINT (where the hips go, the middle of the seat
             cushion) forward to the line between the two bases; negative
             if that line is behind the hips
  --facing   which way the LEFT-PORT arm's base points, in degrees: 0 is the
             way the person faces, 90 is the person's left, -90 their right,
             180 toward the backrest. An arm points the way it reaches when
             it stands at its home pose. --facing-right for the other arm if
             it is turned differently (default: the same).

The arm on --left-can-port is a0 and sits on the person's LEFT (+y);
the arm on --right-can-port is a1 on their right. Swap the CAN ports if
the wiring came out the other way round, not the file.
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def entries(span, height, ahead, facing_left, facing_right=None, start_deg=90.0):
    """-> the two rig entries, seat-relative. `start_deg` turns each arm's
    start pose away from the person (arms_live clamps it to START_MAX_DEG)."""
    if facing_right is None:
        facing_right = facing_left
    half = float(span) / 2.0
    return [
        {"id": "a0", "side": "left", "x_from_seat_mm": float(ahead),
         "y_from_seat_mm": half, "z_mm": float(height), "facing_deg": float(facing_left),
         "start_deg": float(start_deg)},
        {"id": "a1", "side": "right", "x_from_seat_mm": float(ahead),
         "y_from_seat_mm": -half, "z_mm": float(height), "facing_deg": float(facing_right),
         "start_deg": -float(start_deg)},
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("--span", type=float, required=True)
    ap.add_argument("--height", type=float, required=True)
    ap.add_argument("--ahead", type=float, required=True)
    ap.add_argument("--facing", type=float, default=0.0)
    ap.add_argument("--facing-right", type=float, default=None)
    ap.add_argument("--out", default=os.path.join(HERE, "live_rig_openyam.json"))
    ap.add_argument("--name", default="two OpenYAMs on a plank across the armrests, measured")
    a = ap.parse_args()
    rig = {
        "name": a.name,
        "frame": ("x and y from the seat point, z above the floor. a0 is the arm on "
                  "--left-can-port (the person's left), a1 the one on --right-can-port. "
                  "Made by openyam_rig.py from a tape measure; edit the numbers there, "
                  "not here."),
        "measured": {"span_mm": a.span, "height_mm": a.height, "ahead_mm": a.ahead,
                     "facing_deg": a.facing,
                     "facing_right_deg": a.facing if a.facing_right is None else a.facing_right},
        "arms": entries(a.span, a.height, a.ahead, a.facing, a.facing_right),
        "phases": [[0, 1]],
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rig, f, indent=2)
        f.write("\n")
    print(f"  wrote {a.out}")
    for e in rig["arms"]:
        print(f"    {e['id']} ({e['side']}): {e['x_from_seat_mm']:+.0f} mm ahead of the seat, "
              f"{e['y_from_seat_mm']:+.0f} mm to the side, {e['z_mm']:.0f} mm up, "
              f"facing {e['facing_deg']:.0f} deg")
    print("  check it before driving: python scrub3d/live/arms_check.py "
          "scrub3d/data/live_rec_20260917f300 " + a.out)


if __name__ == "__main__":
    main()
