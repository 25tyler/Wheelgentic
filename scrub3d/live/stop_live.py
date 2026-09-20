"""Stop running copies of a script: python processes whose script is it.

    python scrub3d/live/stop_live.py [live_body.py]

The live view runs until stopped, and on Windows a background python cannot
be interrupted from another shell, so this finds it by its script name.
"""
import os
import sys

import psutil

name = sys.argv[1] if len(sys.argv) > 1 else "live_body.py"
me = os.getpid()
hit = 0
for p in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        cl = p.info["cmdline"] or []
        if p.info["pid"] == me or not (p.info["name"] or "").lower().startswith("python"):
            continue
        if any(os.path.basename(c) == name for c in cl[1:2]):
            # the script itself, not a shell whose command line mentions it
            p.terminate()
            hit += 1
            print("stopped", p.info["pid"], " ".join(cl[:3]))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
print(hit, "stopped")
