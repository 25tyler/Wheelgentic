"""py/config.py — hot-reload. ~1us/frame, no thread, no dependency.

Deliberately NOT watchdog: its callback fires on the FIRST write event, so it
happily reads a half-written JSON file and hands you garbage. This version
returns False and KEEPS last-good calibration on a malformed edit.
"""
import json, os


class Config:
    def __init__(self, path="config.json"):
        self.path, self._mt, self.data = path, 0.0, {}
        self.reload()

    def reload(self):
        try:
            mt = os.path.getmtime(self.path)
            if mt != self._mt:
                self.data, self._mt = json.load(open(self.path)), mt
                print(f"[config] reloaded {self.path}")
                return True
        except (json.JSONDecodeError, OSError, ValueError):
            pass                    # keep last-good; NEVER crash on stage
        return False
