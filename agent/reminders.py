"""Reminders and nudges: things JARVIS tells Ali at a set time, on Telegram and on the page.

A reminder fires at its time (once, every day, or every weekday). A nudge is a reminder with a
condition, checked when it's due: it only fires if the thing still hasn't happened.

    no_workout_today     nothing logged in JARVIS/Workouts today
    goals_open           today's goals aren't all ticked
    goal_open            one named goal (arg) is still open
    prospect_contacted   a prospect (arg) is still at "contacted": the automatic outreach follow-up

Times are UK local, stored as "YYYY-MM-DDTHH:MM". Kept in data/reminders.json (gitignored);
JARVIS-private, never in Ali's notes. Anything missed while JARVIS was off fires on the next start,
marked as missed.
"""
import datetime as dt
import json
import os
import secrets
import threading
import time

import clock
import data

FILE = data.ROOT / "data" / "reminders.json"
CHECK_EVERY = 20                 # seconds
CONDITIONS = ("none", "no_workout_today", "goals_open", "goal_open", "prospect_contacted")
REPEATS = ("none", "daily", "weekdays")
LATE_AFTER_MIN = 10              # fired this late = "missed earlier"
KEEP_ALERTS = 20
_lock = threading.Lock()
FMT = "%Y-%m-%dT%H:%M"


def _load():
    try:
        db = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        db = {}
    db.setdefault("items", [])
    db.setdefault("alerts", [])
    return db


def _save(db):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=1), encoding="utf-8")
    os.replace(tmp, FILE)


def _now():
    return clock.uk_now().replace(tzinfo=None, second=0, microsecond=0)


def parse_when(when):
    """'2026-09-27T15:00' or '2026-09-27 15:00' (UK local) → datetime."""
    s = str(when).strip().replace(" ", "T")[:16]
    return dt.datetime.strptime(s, FMT)


def add(text, when, repeat="none", condition="none", arg=""):
    text = " ".join(str(text).split())[:200]
    if not text:
        raise ValueError("A reminder needs something to say.")
    repeat = repeat if repeat in REPEATS else "none"
    condition = condition if condition in CONDITIONS else "none"
    due = parse_when(when)
    if due < _now() - dt.timedelta(minutes=1):
        raise ValueError(f"{due.strftime('%a %d %b %H:%M')} has already passed.")
    item = {"id": secrets.token_hex(3), "text": text, "due": due.strftime(FMT), "repeat": repeat,
            "condition": condition, "arg": str(arg)[:120], "created": _now().strftime(FMT)}
    with _lock:
        db = _load()
        db["items"].append(item)
        _save(db)
    return item


def upcoming(limit=20):
    return sorted(_load()["items"], key=lambda i: i["due"])[:limit]


def cancel(which):
    """By id, or every reminder whose text contains `which`. Returns what was removed."""
    w = str(which).strip().lower()
    with _lock:
        db = _load()
        gone = [i for i in db["items"] if i["id"] == w or (w and w in i["text"].lower())]
        db["items"] = [i for i in db["items"] if i not in gone]
        _save(db)
    return gone


def _next(due, repeat):
    nxt = due + dt.timedelta(days=1)
    while repeat == "weekdays" and nxt.weekday() >= 5:
        nxt += dt.timedelta(days=1)
    return nxt


def still_true(item):
    """Does the nudge's condition still hold? (True for plain reminders.)"""
    c, arg = item.get("condition", "none"), item.get("arg", "")
    if c == "none":
        return True
    if c == "no_workout_today":
        import fitness
        last = fitness.summary()["last"]
        return not (last and last["days_ago"] == 0)
    if c in ("goals_open", "goal_open"):
        import goals
        items = goals.parse(data.read_daily(clock.uk_today()))
        if c == "goals_open":
            return any(not g["done"] for g in items)
        i = goals.match(items, arg)
        return i is not None and not items[i]["done"]
    if c == "prospect_contacted":
        import status
        import vault
        n = next((n for n in vault.get().notes if n.type == "prospect" and n.title.lower() == arg.lower()), None)
        return bool(n) and status.effective(n, "lead")[0] == "contacted"
    return True


def check(notify=None):
    """Fire everything due. Returns the alerts raised."""
    now, raised = _now(), []
    with _lock:
        db = _load()
        keep = []
        for item in db["items"]:
            due = parse_when(item["due"])
            if due > now:
                keep.append(item)
                continue
            try:
                fire = still_true(item)
            except Exception:
                fire = True                                  # can't tell: better to remind than stay quiet
            if fire:
                late = (now - due).total_seconds() / 60 > LATE_AFTER_MIN
                text = (f"Missed at {due.strftime('%H:%M')}: " if late else "") + item["text"]
                alert = {"id": item["id"] + "-" + now.strftime("%H%M"), "text": text, "at": now.strftime(FMT)}
                db["alerts"] = (db["alerts"] + [alert])[-KEEP_ALERTS:]
                raised.append(alert)
            if item["repeat"] != "none":
                nxt = _next(due, item["repeat"])
                while nxt <= now:
                    nxt = _next(nxt, item["repeat"])
                keep.append({**item, "due": nxt.strftime(FMT)})
        db["items"] = keep
        _save(db)
    for a in raised:
        if notify:
            try:
                notify(a["text"])
            except Exception:
                pass                                         # the page still shows it
    return raised


def raise_alert(text, key, notify=None):
    """An alert that isn't a scheduled reminder (e.g. meeting prep). `key` stops it firing twice."""
    with _lock:
        db = _load()
        if any(a["id"] == key for a in db["alerts"]):
            return False
        db["alerts"] = (db["alerts"] + [{"id": key, "text": text, "at": _now().strftime(FMT)}])[-KEEP_ALERTS:]
        _save(db)
    if notify:
        try:
            notify(text)
        except Exception:
            pass
    return True


def alerts():
    return [a for a in _load()["alerts"] if not a.get("seen")]


def seen(ids):
    with _lock:
        db = _load()
        for a in db["alerts"]:
            if a["id"] in ids:
                a["seen"] = True
        _save(db)


def _loop(notify):
    while True:
        try:
            check(notify)
        except Exception:
            pass
        time.sleep(CHECK_EVERY)


def start(notify=None):
    threading.Thread(target=_loop, args=(notify,), daemon=True).start()


def describe(item):
    d = parse_when(item["due"])
    when = d.strftime("%a %d %b, %H:%M")
    rep = {"daily": " · every day", "weekdays": " · weekdays"}.get(item["repeat"], "")
    cond = {"no_workout_today": " · only if you haven't trained", "goals_open": " · only if goals are open",
            "goal_open": f" · only if \"{item['arg']}\" is still open",
            "prospect_contacted": f" · only if {item['arg']} hasn't moved on"}.get(item["condition"], "")
    return when + rep + cond
