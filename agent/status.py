"""Where things are in a pipeline — videos (idea → posted) and prospects (lead → won) — without
touching Ali's notes.

When Ali says something moved ("I filmed the ego one", "Cobalt Dental replied"), JARVIS records
it here in data/status_log.json (gitignored). A note's stage is whichever is newer: the latest
entry here, or the `status:` Ali set in the note himself (judged by when he last saved it).
"""
import json
import os
import threading
import time

import data

FILE = data.ROOT / "data" / "status_log.json"
CONTENT_STAGES = ["idea", "scripted", "filmed", "posted"]
PROSPECT_STAGES = ["lead", "contacted", "call booked", "proposal sent", "won", "lost"]

_lock = threading.Lock()


def _load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def record(rel, stage, note=""):
    with _lock:
        log = _load()
        log.setdefault(rel, []).append({"stage": stage, "at": time.time(), "note": note})
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(log, indent=1), encoding="utf-8")
        os.replace(tmp, FILE)


def history(rel):
    return _load().get(rel, [])


def events_since(t0):
    """[(rel, entry)] recorded after unix time t0, oldest first."""
    out = [(rel, e) for rel, es in _load().items() for e in es if e["at"] >= t0]
    return sorted(out, key=lambda x: x[1]["at"])


def effective(note, default):
    """Stage for a vault note: the newer of JARVIS's log and the note's own status: field."""
    own = (note.meta.get("status") or "").strip().lower() or None
    logged = history(note.rel)
    if logged and (own is None or logged[-1]["at"] >= (note.mtime or 0)):
        return logged[-1]["stage"], "jarvis"
    return own or default, "note" if own else "default"
