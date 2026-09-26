"""The evening check-in: one honest question, once a day, after CHECKIN_HOUR.

His answer is saved in his own words as a journal note (the model does that with write_note,
guided by prompt.md), so his real days become material for his videos. Skipping is one click.
State (which day it last ran) is in data/checkin.json, gitignored.
"""
import json
import os

import clock
import data

FILE = data.ROOT / "data" / "checkin.json"
HOUR = os.environ.get("JARVIS_CHECKIN_HOUR", "19")        # "off" turns it off
QUESTIONS = [
    "How was today, honestly?",
    "What's one thing you felt today that you didn't say out loud?",
    "Where were you most yourself today?",
    "What did today cost you, and was it worth it?",
    "What did you avoid today?",
    "What went better than you expected?",
    "What would you tell this morning's version of you?",
]


def _load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _mark(**kw):
    s = _load()
    s.update(kw)
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s), encoding="utf-8")
    os.replace(tmp, FILE)


def question(day=None):
    day = day or clock.uk_today()
    return QUESTIONS[day.toordinal() % len(QUESTIONS)]


def due():
    if not HOUR.isdigit():
        return False
    return clock.uk_now().hour >= int(HOUR) and _load().get("last") != clock.uk_today().isoformat()


def status():
    return {"due": due(), "question": question() if due() else None, "hour": HOUR}


def start():
    """Ask now. Returns the question; the caller puts it into the conversation."""
    _mark(last=clock.uk_today().isoformat())
    return question()


def skip():
    _mark(last=clock.uk_today().isoformat(), skipped=clock.uk_today().isoformat())
