"""py/replay.py — JSONL record/playback. The demo's safety net.

Append-only JSONL degrades gracefully: a crash-TRUNCATED file still yields
every intact frame before the break. A pickle or a single JSON array does not.
"""
import json, time


def replay_source(path, loop=True):
    rows = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            break                    # truncated tail -> use what we have
    if not rows:
        raise SystemExit(f"{path} has no usable frames")
    print(f"[replay] {len(rows)} frames")
    while True:
        t0, start = rows[0]["t"], time.perf_counter()
        for r in rows:
            d = (r["t"] - t0) - (time.perf_counter() - start)
            if d > 0:
                time.sleep(d)
            yield r
        if not loop:
            return
