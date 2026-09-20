"""devconsole/events.py -- how a job writes down what it did.

Runs INSIDE the job process, loaded by file path under a private name, so it
imports nothing but the standard library at module level. numpy and cv2 are
reached for lazily, and only when a value or an image actually needs them.

ONE FILE, APPEND ONLY
---------------------
Every event is one JSON object on one line of `events.jsonl`. A reader can tail
it while the job runs, a crashed job leaves everything up to its last flush,
and history needs no database.

THE WRITER OWNS ALL THE SLOW WORK
---------------------------------
Probes run on the pipeline's own threads, some of them twenty times a second,
so they only ever put a small dict on a queue. Serialising it, encoding an
image and touching the disk all happen here, on one background thread, every
100 ms. A probe that had to wait for a disk write would be measuring the disk.
"""
import atexit
import itertools
import json
import os
import queue
import threading
import time

FLUSH_S = 0.10

# Frames are pictures of a person. They are overwritten in place and never
# accumulated, so a run directory holds at most one of each.
FRAME_NAMES = ("color", "depth", "seg", "carve", "still")


def _np():
    # Only if the job already loaded it. A value cannot be a numpy object in a
    # process that never imported numpy, and importing it here cost 160 ms on
    # the first summary of `session.py`, which never needs it.
    import sys
    return sys.modules.get("numpy")


def summarise(x, depth=0, max_items=24, max_str=400):
    """Any value -> something small, JSON-safe and still informative.

    Arrays become their shape, dtype and range rather than their contents; long
    sequences keep their head and say how much was dropped. A summary must never
    raise: the pipeline is running when this is called, and a formatting problem
    here must not become a failure there.
    """
    try:
        if x is None or isinstance(x, (bool, int)):
            return x
        if isinstance(x, float):
            if x != x or x in (float("inf"), float("-inf")):
                return repr(x)
            return round(x, 4)
        if isinstance(x, str):
            return x if len(x) <= max_str else x[:max_str] + f"...(+{len(x) - max_str})"
        if isinstance(x, bytes):
            return f"<{len(x)} bytes>"
        np = _np()
        if np is not None:
            if isinstance(x, np.generic):
                return summarise(x.item(), depth, max_items, max_str)
            if isinstance(x, np.ndarray):
                if x.size <= 16 and x.ndim <= 2 and x.dtype.kind in "biuf":
                    return summarise(x.tolist(), depth, max_items, max_str)
                out = {"array": list(x.shape), "dtype": str(x.dtype)}
                if x.size and x.dtype.kind in "biuf":
                    finite = x[np.isfinite(x)] if x.dtype.kind == "f" else x
                    if finite.size:
                        out["min"] = round(float(finite.min()), 4)
                        out["max"] = round(float(finite.max()), 4)
                elif x.size and x.dtype.kind == "b":
                    out["true"] = int(x.sum())
                return out
        if depth >= 4:
            return f"<{type(x).__name__}>"
        if isinstance(x, dict):
            items = list(x.items())
            out = {str(k): summarise(v, depth + 1, max_items, max_str)
                   for k, v in items[:max_items * 2]}
            if len(items) > max_items * 2:
                out["..."] = f"+{len(items) - max_items * 2} keys"
            return out
        if isinstance(x, (list, tuple, set, frozenset)):
            seq = list(x)
            out = [summarise(v, depth + 1, max_items, max_str)
                   for v in seq[:max_items]]
            if len(seq) > max_items:
                out.append(f"...(+{len(seq) - max_items})")
            return out
        name = type(x).__name__
        if hasattr(x, "__dict__") and depth < 2:
            fields = {k: v for k, v in vars(x).items() if not k.startswith("_")}
            small = {k: summarise(v, depth + 2, 8, 120)
                     for k, v in list(fields.items())[:8]}
            return {"object": name, **small}
        return f"<{name}>"
    except Exception as exc:                                  # noqa: BLE001
        return f"<unsummarisable {type(x).__name__}: {exc}>"


def _default(o):
    return summarise(o)


class Writer:
    """Queue in, JSON lines and image files out, on one thread."""

    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.path = os.path.join(run_dir, "events.jsonl")
        self.frames_dir = os.path.join(run_dir, "frames")
        os.makedirs(self.frames_dir, exist_ok=True)
        self._q = queue.SimpleQueue()
        self._seq = itertools.count(1)
        self._fh = open(self.path, "a", encoding="utf-8", newline="\n")
        self._stop = threading.Event()
        self._frame_seq = {}
        self.cost_s = 0.0
        self._thread = threading.Thread(target=self._loop, name="dc-writer",
                                        daemon=True)
        self._thread.start()
        atexit.register(self.close)

    # --- producers (any thread) -------------------------------------------

    def emit(self, kind, **fields):
        fields["k"] = kind
        fields.setdefault("t", time.time())
        fields["seq"] = next(self._seq)
        self._q.put(("evt", fields))

    def image(self, name, array, how):
        """Hand an image to the writer. `how` is 'bgr', 'depth', 'seg' or 'mask'.

        The caller has already copied the array; the writer owns it from here.
        """
        if name not in FRAME_NAMES:
            return
        self._q.put(("img", (name, array, how)))

    def still(self, path):
        """A still render that already exists on disk: point the console at it."""
        self._q.put(("still", path))

    # --- the thread ---------------------------------------------------------

    def _loop(self):
        while not self._stop.is_set():
            time.sleep(FLUSH_S)
            self._drain()
        self._drain()

    def _drain(self):
        t0 = time.perf_counter()
        wrote = False
        while True:
            try:
                kind, payload = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "evt":
                    self._fh.write(json.dumps(payload, default=_default,
                                              separators=(",", ":")) + "\n")
                    wrote = True
                elif kind == "img":
                    self._write_image(*payload)
                elif kind == "still":
                    self._copy_still(payload)
            except Exception as exc:                          # noqa: BLE001
                try:
                    self._fh.write(json.dumps(
                        {"k": "writer_error", "t": time.time(),
                         "seq": next(self._seq), "err": repr(exc)}) + "\n")
                except Exception:                             # noqa: BLE001
                    pass
        if wrote:
            try:
                self._fh.flush()
            except Exception:                                 # noqa: BLE001
                pass
        self.cost_s += time.perf_counter() - t0

    def _write_image(self, name, array, how):
        import cv2
        np = _np()
        if how == "bgr":
            img = array
        elif how == "depth":
            d = np.nan_to_num(array.astype(np.float32))
            valid = d > 0
            lo, hi = (np.percentile(d[valid], [2, 98]) if valid.any()
                      else (0.0, 1.0))
            norm = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
            img = cv2.applyColorMap((255 * (1 - norm)).astype(np.uint8),
                                    cv2.COLORMAP_TURBO)
            img[~valid] = (20, 20, 24)
        elif how == "seg":
            lut = _class_lut(np)
            img = lut[np.clip(array.astype(np.int64), 0, len(lut) - 1)]
        elif how == "mask":
            img = array
        else:
            return
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            return
        final = os.path.join(self.frames_dir, f"{name}.jpg")
        tmp = final + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(buf.tobytes())
        try:
            os.replace(tmp, final)
        except PermissionError:
            # The console is reading it right now. Skip this frame; the next
            # one is half a second away and nothing is lost that matters.
            try:
                os.remove(tmp)
            except OSError:
                pass
            return
        seq = self._frame_seq.get(name, 0) + 1
        self._frame_seq[name] = seq
        self._fh.write(json.dumps({"k": "frame", "t": time.time(),
                                   "seq": next(self._seq), "name": name,
                                   "n": seq, "shape": list(img.shape)}) + "\n")

    def _copy_still(self, path):
        import shutil
        if not path or not os.path.exists(path):
            return
        final = os.path.join(self.frames_dir, "still.jpg")
        if os.path.abspath(path) == os.path.abspath(final):
            pass
        elif path.lower().endswith((".jpg", ".jpeg")):
            shutil.copyfile(path, final + ".tmp")
            os.replace(final + ".tmp", final)
        else:
            import cv2
            img = cv2.imread(path)
            if img is None:
                return
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                return
            with open(final + ".tmp", "wb") as fh:
                fh.write(buf.tobytes())
            os.replace(final + ".tmp", final)
        seq = self._frame_seq.get("still", 0) + 1
        self._frame_seq["still"] = seq
        self._fh.write(json.dumps({"k": "frame", "t": time.time(),
                                   "seq": next(self._seq), "name": "still",
                                   "n": seq}) + "\n")

    def close(self):
        if self._stop.is_set():
            return
        self._stop.set()
        self._thread.join(timeout=2.0)
        try:
            self._drain()
            self._fh.close()
        except Exception:                                     # noqa: BLE001
            pass


_LUT = None


def _class_lut(np):
    """28 distinguishable colours for the Sapiens classes, background dark."""
    global _LUT
    if _LUT is None:
        rng = np.random.default_rng(7)
        lut = rng.integers(60, 255, size=(32, 3)).astype(np.uint8)
        lut[0] = (24, 24, 28)
        _LUT = lut
    return _LUT
