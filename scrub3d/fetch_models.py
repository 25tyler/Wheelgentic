"""scrub3d/fetch_models.py -- get the weights, after accepting their terms.

    python scrub3d/fetch_models.py

WHY THIS IS A SCRIPT AND NOT A LINE IN A README
------------------------------------------------
Sapiens v1 is **CC-BY-NC-4.0 on both its code and its weights**. That is
non-commercial only. The project accepted that deliberately, because the
permissive alternatives genuinely cannot do this job -- SAM 2 is Apache-2.0 but
class-agnostic, so it will segment an arm without being able to tell you it is
an arm -- and because this work stays non-commercial.

A licence that restrictive is not something to bundle into a repository and
hope the next person reads the footnote. So the weights are NEVER committed,
scrub3d/weights/ is gitignored, and each user downloads their own copy after
being shown the terms. That is the difference between a decision somebody made
and a decision somebody inherited.

SAPIENS2 IS DELIBERATELY NOT OFFERED, despite being newer and better. Its
bespoke Meta licence prohibits use "for biometric processing" and in activities
presenting "a risk of death or bodily harm... operation of... machinery". A
robot arm in contact with a person is arguably both, regardless of commercial
status. v1's CC-BY-NC carries no such acceptable-use clause.
"""
import argparse
import os
import sys

try:
    from . import sapiens
except ImportError:
    import sapiens

MODELS = {
    "sapiens-seg-0.3b": {
        "file": sapiens.SEG_CKPT,
        "url": ("https://huggingface.co/facebook/sapiens-seg-0.3b-torchscript/"
                "resolve/main/" + sapiens.SEG_CKPT),
        "size_mb": 1360,
        "licence": "CC-BY-NC-4.0 (code AND weights) -- NON-COMMERCIAL",
        "terms": "https://huggingface.co/facebook/sapiens-seg-0.3b-torchscript",
        "why": ("28-class body-part segmentation. scrub3d uses it for the "
                "person's OUTLINE against the room, which is the measurement "
                "depth is worst at."),
    },
    "sapiens-depth-0.3b": {
        "file": sapiens.DEPTH_CKPT,
        "url": ("https://huggingface.co/facebook/sapiens-depth-0.3b-torchscript/"
                "resolve/main/" + sapiens.DEPTH_CKPT),
        "sha256": None,
        "size_mb": 1360,
        "licence": "CC-BY-NC-4.0",
        "terms": "https://huggingface.co/facebook/sapiens-depth-0.3b-torchscript",
        "why": ("Depth from colour, used ONLY to fill the holes stereo leaves "
                "on dark clothing and hair. Fitted to the measured depth, "
                "never replacing it, and never reaching the planning surface: "
                "an inferred surface is not one an arm may press against."),
    },
    "sapiens-normal-0.3b": {
        "file": sapiens.NORMAL_CKPT,
        "url": ("https://huggingface.co/facebook/sapiens-normal-0.3b-torchscript/"
                "resolve/main/" + sapiens.NORMAL_CKPT),
        "size_mb": 1360,
        "licence": "CC-BY-NC-4.0 (code AND weights) -- NON-COMMERCIAL",
        "terms": "https://huggingface.co/facebook/sapiens-normal-0.3b-torchscript",
        "why": ("Surface normals from colour. A normal differentiated from "
                "depth is noisiest on the SIDE of a limb, which is exactly "
                "where the sponge has to know which way to press. Optional: "
                "scan.py falls back to depth-fitted normals without it."),
    },
}

TERMS = """
  This model is licensed CC-BY-NC-4.0, on the CODE AND THE WEIGHTS.

  NON-COMMERCIAL USE ONLY. If this project ever becomes commercial, this
  download has to be removed and the permissive fallback used instead: SAM 2
  (Apache-2.0) prompted by MediaPipe landmarks, with no parametric cross-check,
  because no permissively-licensed parametric body model exists.

  The weights are never committed. They stay in scrub3d/weights/, which is
  gitignored. The scans they produce are a person's body geometry, which is
  biometric data, and stay on this machine.
"""


def status():
    """-> {name: (present, path)}."""
    out = {}
    for name, m in MODELS.items():
        p = os.path.join(sapiens.WEIGHTS, m["file"])
        out[name] = (os.path.exists(p), p)
    return out


def fetch(name, accept=False):
    """Download one model, after the user accepts its terms."""
    m = MODELS[name]
    dest = os.path.join(sapiens.WEIGHTS, m["file"])
    if os.path.exists(dest):
        print(f"  {name}: already present ({os.path.getsize(dest) / 1e6:.0f}MB)")
        return True

    print(f"\n  {name}")
    print(f"    {m['why']}")
    print(f"    {m['size_mb']}MB, licence: {m['licence']}")
    print(f"    terms: {m['terms']}")
    print(TERMS)
    if not accept:
        reply = input("  Type 'accept' to download, anything else to skip: ")
        if reply.strip().lower() != "accept":
            print("  skipped.")
            return False

    os.makedirs(sapiens.WEIGHTS, exist_ok=True)
    tmp = dest + ".part"
    print(f"  downloading to {dest} ...")
    try:
        import urllib.request
        # Write to a .part and rename, so an interrupted download can never be
        # mistaken for a complete one. A truncated TorchScript file fails at
        # load with an opaque error a long way from here.
        with urllib.request.urlopen(m["url"]) as r, open(tmp, "wb") as fh:
            total, done = int(r.headers.get("Content-Length", 0)), 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if total:
                    pct = 100.0 * done / total
                    print(f"\r    {done / 1e6:6.0f} / {total / 1e6:.0f} MB "
                          f"({pct:5.1f}%)", end="", flush=True)
        print()
    except Exception as exc:                                    # noqa: BLE001
        if os.path.exists(tmp):
            os.remove(tmp)
        print(f"  FAILED: {exc}")
        print(f"  Download it by hand from {m['terms']} and put it at {dest}")
        return False
    os.replace(tmp, dest)
    print(f"  done: {os.path.getsize(dest) / 1e6:.0f}MB")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--accept", action="store_true",
                    help="accept the licence terms without being asked")
    # CHOICES, not a free string. `--only sapiens-seg` (a plausible typo for
    # `sapiens-seg-0.3b`) reached MODELS[name] and came back as a raw KeyError
    # traceback -- from a script whose failure mode is otherwise a careful
    # printed instruction. argparse rejects it by name and lists the real ones.
    ap.add_argument("--only", default="", choices=("",) + tuple(MODELS),
                    help="download just this one model")
    # A READ-ONLY ANSWER TO "is the scan chain ready", which had no command.
    # Without it the only way to learn whether the weights are on disk was to
    # run the downloader, which prompts per model for a 3.7GB accept. Callers
    # that just want the answer -- an operator, a preflight check -- must not
    # have to start a download to get it.
    ap.add_argument("--status", action="store_true",
                    help="report what is on disk and exit; download nothing")
    a = ap.parse_args()

    print("scrub3d model weights")
    print(f"  weights directory: {sapiens.WEIGHTS}")
    for name, (present, path) in status().items():
        print(f"  {name:22s} {'present' if present else 'MISSING'}")

    if a.status:
        missing = [n for n, (p, _) in status().items() if not p]
        if missing:
            # The torch check belongs here and not in sapiens.py: sapiens
            # imports torch lazily so that the package works without it, which
            # means "weights present" alone never proves the model can run.
            try:
                import torch                                # noqa: F401
                have_torch = True
            except ImportError:
                have_torch = False
            print(f"\n  {len(missing)} of {len(MODELS)} missing; "
                  f"{sum(MODELS[n]['size_mb'] for n in missing) / 1024:.1f}GB "
                  f"to download.")
            print(f"  torch: {'present' if have_torch else 'MISSING -- the '
                  'weights alone are not enough, these are TorchScript'}")
            print("  scan.py needs sapiens-seg-0.3b (via a cached "
                  "sapiens_seg.npy per capture); the other two are optional.")
            return 1
        print("\n  all present.")
        return 0

    want = [a.only] if a.only else list(MODELS)
    ok = all(fetch(n, accept=a.accept) for n in want)

    print("\n  scrub3d runs WITHOUT these. record.py, frames.py, rsfeed.py,")
    print("  track.py, anatomy.py, partition.py, control.py and viz.py have no")
    print("  dependency on them; only the outline in scan.py does, and the")
    print("  captures on disk carry a cached copy of that.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
