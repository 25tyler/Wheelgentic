"""scrub3d/tools/segment_captures.py -- (re)build the cached body-part masks.

WHY THIS EXISTS
---------------
Every capture stores a `sapiens_seg.npy` beside its colour and depth so the
pipeline can be worked on with neither the camera nor the GPU attached. Four of
them are unusable: `bag01`, `pose_forward`, `pose_lean` and `empty` were written
before sapiens.py's letterbox fix and hold a 1024x768 array in the NETWORK's own
padded frame, not registered to the 1280x720 image it came from. Resizing one
onto the image gives an IoU of 0.125 against the person. It is not a mask of
anything.

Nothing warns you about that. The array loads, has plausible class ids in it,
and lines up with nothing. So this tool also GATES what it writes: a cached mask
is only good if the person it finds actually covers the person the depth sees.

Run it with no arguments to fix whatever is stale and leave the rest alone.
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import sapiens                                                   # noqa: E402
import frames as F                                               # noqa: E402

BODY = [i for i, n in enumerate(sapiens.CLASSES)
        if n not in ("Background",)]


def is_control(cap_dir):
    """Is this a rigid-object capture rather than a person?

    The dx_control* captures are a flask held at the working distance, shot to
    measure how badly depth flattens a cylinder of known diameter. Running a
    body-part segmenter on one is meaningless, and worse, the containment gate
    below then compares a person mask against a depth blob that is mostly
    flask and reports a failure that is really a category error. Skip them by
    what they are, and say so, rather than letting the gate refuse them.
    """
    try:
        with open(os.path.join(cap_dir, "meta.json")) as fh:
            return "CONTROL" in json.load(fh).get("label", "").upper()
    except OSError:
        return False


def person_mask(seg):
    """Class map -> everything that is not background."""
    return seg != sapiens.IDX["Background"]


def check(cap_dir, seg, z_range=(600.0, 1300.0)):
    """Does this mask agree with the depth? -> (ok, detail).

    The test is containment, not IoU, and the asymmetry is deliberate. Sapiens
    legitimately sees more person than the depth window does -- hair, dark
    clothing and the far shoulder all drop out of stereo -- so a low IoU can
    mean a good mask over a sparse depth image. What must never happen is the
    reverse: solid depth on the subject that the mask calls background. That is
    the failure the broken caches have, and containment is what detects it.
    """
    depth = np.load(os.path.join(cap_dir, "depth_median_mm.npy"))
    if seg.shape != depth.shape:
        return False, {"reason": f"seg {seg.shape} != image {depth.shape}"}
    body = F.subject_mask(depth, z_range)
    if body.sum() < 5000:
        return True, {"reason": "no subject in this capture, nothing to check"}
    pm = person_mask(seg)
    inter = int((pm & body).sum())
    contained = inter / float(body.sum())
    union = int((pm | body).sum())
    return contained > 0.60, {
        "depth_subject_px": int(body.sum()), "seg_person_px": int(pm.sum()),
        "contained": contained, "iou": inter / float(union) if union else 0.0}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=F.DATA)
    ap.add_argument("--force", action="store_true",
                    help="re-segment even caches that already pass")
    ap.add_argument("--only", default="", help="comma separated capture names")
    a = ap.parse_args()

    # NO data/ AT ALL IS THE NORMAL STATE OF A FRESH CLONE, not an error to
    # traceback on. data/ is gitignored, so anyone who has not had the camera
    # on a person has an empty directory or none. os.listdir() raised
    # FileNotFoundError naming a path, which reads as a broken install rather
    # than "you have no captures yet".
    if not a.only and not os.path.isdir(a.data):
        print(f"no captures: {a.data} does not exist.")
        print("  data/ is gitignored, so a fresh clone has none. Make one with")
        print("  the camera attached:  python tools/live-scan.py --out "
              "recordings/live01")
        return 1

    names = ([n.strip() for n in a.only.split(",") if n.strip()] or
             sorted(d for d in os.listdir(a.data)
                    if os.path.isdir(os.path.join(a.data, d))))

    todo = []
    print("cached body-part masks:")
    for n in names:
        d = os.path.join(a.data, n)
        png = os.path.join(d, "color_median.png")
        if not os.path.exists(png):
            continue
        if is_control(d):
            print(f"  {n:14s} skipped, rigid-object control, not a body")
            continue
        p = os.path.join(d, "sapiens_seg.npy")
        if not os.path.exists(p):
            print(f"  {n:14s} MISSING")
            todo.append(n)
            continue
        ok, det = check(d, np.load(p))
        if ok and not a.force:
            print(f"  {n:14s} ok      contained "
                  f"{det.get('contained', float('nan')):.3f}")
        else:
            print(f"  {n:14s} STALE   {det}")
            todo.append(n)

    if not todo:
        print("\n  nothing to rebuild.")
        return 0

    print(f"\n  rebuilding {len(todo)}: {', '.join(todo)}")
    import cv2
    seg = sapiens.SapiensSeg()
    print(f"  model on {seg.device} in {seg.dtype}")

    bad = []
    for n in todo:
        d = os.path.join(a.data, n)
        bgr = cv2.imread(os.path.join(d, "color_median.png"))
        # The network's MEAN/STD are in RGB order and cv2 hands back BGR.
        out = seg(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        # numpy APPENDS .npy to any path that lacks it, so the temp name
        # has to end in .npy or os.replace chases a file that was
        # never written under the name it was given.
        tmp = os.path.join(d, "sapiens_seg.tmp.npy")
        np.save(tmp, out.astype(np.int16))
        ok, det = check(d, out)
        if not ok:
            os.remove(tmp)
            bad.append((n, det))
            print(f"    {n:14s} REFUSED {det}")
            continue
        os.replace(tmp, os.path.join(d, "sapiens_seg.npy"))
        named = [sapiens.CLASSES[c] for c in np.unique(out) if c]
        print(f"    {n:14s} wrote {out.shape}  contained {det['contained']:.3f}"
              f"  iou {det['iou']:.3f}  {len(named)} classes")

    if bad:
        print(f"\n  {len(bad)} capture(s) REFUSED and were left untouched. A "
              f"bad mask on disk is worse than none:")
        for n, det in bad:
            print(f"    {n}: {det}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
