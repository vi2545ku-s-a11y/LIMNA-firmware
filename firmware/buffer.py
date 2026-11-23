"""
LIMNA firmware - the local buffer (requirements K-03 and K-04).

One file per day, one JSON line per measurement, and one small file saying
how far sending has got. A measurement is written and closed BEFORE anything
tries to send it, and a line only counts as sent once the broker has
confirmed it (MQTT QoS 1). Out at a pond the link drops often; this is what
makes that cost a delay instead of data.

    matningar/20261123.jsonl  the measurements of one day, oldest first
    matningar/sent.json       {"file": "20261123.jsonl", "line": 412}
                              everything before that file, and the first 412
                              lines of it, have been confirmed

Today this lives in the board's flash. On the buoy it moves to the SD card,
and only `root` changes.

THE FOLDER MUST NOT BE CALLED "buffer". MicroPython imports a folder named
buffer/ in preference to this file, buffer.py, so the first run worked and
every run after it failed at "from buffer import Buffer". Found 2026-11-23.

K-04 asks for 30 days. At 187 bytes, 96 a day, that is about 540 KB.

Plain Python that runs both on the board and on the laptop, where
test_firmware.py checks it. No os.path, which MicroPython does not have.
"""

import json
import os

KEEP_DAYS = 30


class Buffer:
    def __init__(self, root="matningar"):
        self.root = root
        try:
            os.mkdir(root)
        except OSError:
            pass                                 # already there

    def _path(self, name):
        return self.root + "/" + name

    def _days(self):
        return sorted(n for n in os.listdir(self.root) if n.endswith(".jsonl"))

    def _pointer(self):
        try:
            with open(self._path("sent.json")) as fh:
                p = json.load(fh)
            return p["file"], p["line"]
        except (OSError, ValueError, KeyError):
            return "", 0                         # nothing sent yet

    def _lines(self, name):
        with open(self._path(name)) as fh:
            return [line.rstrip("\n") for line in fh if line.strip()]

    def store(self, payload):
        """Append one measurement. Closed before returning, so it is on disk."""
        name = payload["t"][:10].replace("-", "") + ".jsonl"
        with open(self._path(name), "a") as fh:
            fh.write(json.dumps(payload) + "\n")

    def unsent(self, limit=50):
        """Up to `limit` (file, line number, text) not yet confirmed, oldest first."""
        sent_file, sent_line = self._pointer()
        out = []
        for name in self._days():
            if name < sent_file:
                continue
            start = sent_line if name == sent_file else 0
            for i, line in enumerate(self._lines(name)):
                if i < start:
                    continue
                out.append((name, i, line))
                if len(out) >= limit:
                    return out
        return out

    def mark_sent(self, entries):
        """Move the pointer past the last confirmed entry."""
        if not entries:
            return
        name, i, _ = entries[-1]
        with open(self._path("sent.json"), "w") as fh:
            json.dump({"file": name, "line": i + 1}, fh)

    def pending(self):
        return len(self.unsent(limit=100000))

    def prune(self):
        """Delete whole days older than KEEP_DAYS, but never unsent ones."""
        days = self._days()
        sent_file, _ = self._pointer()
        for name in days[:-KEEP_DAYS] if len(days) > KEEP_DAYS else []:
            if name < sent_file:
                os.remove(self._path(name))
