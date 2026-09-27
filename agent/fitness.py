"""Workouts: one note per session in JARVIS/Workouts, read back for streaks and personal bests.

The note is written by tools.log_workout through data.write_note (new files only). Everything here
reads: it parses the lines log_workout writes, which are also easy for Ali to type by hand in Obsidian:

    ## Lifts
    - Bench press: 60kg x 8, 60kg x 8, 62.5kg x 6
    - Pull-ups: bw x 10, bw x 8
"""
import datetime as dt
import re

import clock
import vault

LB = 0.45359237
_LIFT = re.compile(r"^\s*[-*]\s+(.+?):\s*(.+)$")
_SET = re.compile(r"(bw|\d+(?:\.\d+)?)\s*(kg|lbs?)?\s*[x×]\s*(\d+)", re.I)


def norm(name):
    n = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
    return re.sub(r"s$", "", n)


def matches(query, name):
    """'bench' finds 'Bench press'; 'pullup' finds 'Pull-ups'."""
    q, n = norm(query).replace(" ", ""), norm(name).replace(" ", "")
    return bool(q) and (q == n or n.startswith(q) or q in n)


def fmt_w(kg):
    return "bw" if kg == 0 else (f"{kg:g}kg")


def fmt_time(minutes):
    s = round(minutes * 60)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def lift_line(name, sets):
    """sets: [(kg, reps)] -> '- Bench press: 60kg x 8, 62.5kg x 6'"""
    return f"- {name.strip()}: " + ", ".join(f"{fmt_w(w)} x {r}" for w, r in sets)


def parse_lifts(text):
    out, on = {}, False
    for line in text.splitlines():
        if line.startswith("#"):
            on = line.strip().lower() == "## lifts"
            continue
        m = _LIFT.match(line) if on else None
        if not m:
            continue
        sets = []
        for w, unit, reps in _SET.findall(m.group(2)):
            kg = 0.0 if w.lower() == "bw" else float(w) * (LB if unit.lower().startswith("lb") else 1)
            sets.append((round(kg, 2), int(reps)))
        if sets:
            out[m.group(1).strip()] = sets
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sessions(before=None):
    """Every logged session, oldest first."""
    out = []
    for n in vault.get().notes:
        if n.type != "workout":
            continue
        try:
            day = dt.date.fromisoformat(str(n.meta.get("date", ""))[:10])
        except ValueError:
            continue
        if before and day > before:
            continue
        out.append({"date": day, "title": n.meta.get("title") or n.title, "kind": n.meta.get("kind", "session"),
                    "minutes": _num(n.meta.get("duration_min")), "activity": n.meta.get("activity") or None,
                    "km": _num(n.meta.get("distance_km")), "cardio_min": _num(n.meta.get("time_min")),
                    "lifts": parse_lifts(n.text), "id": n.id, "rel": n.rel})
    return sorted(out, key=lambda s: (s["date"], s["rel"]))


def bests(history):
    """Heaviest set per exercise, and longest distance / best pace per cardio activity."""
    lifts, cardio = {}, {}
    for s in history:
        for name, sets in s["lifts"].items():
            top = max(sets)
            if norm(name) not in lifts or top > lifts[norm(name)]["set"]:
                lifts[norm(name)] = {"name": name, "set": top, "date": s["date"]}
        if s["activity"] and s["km"]:
            c = cardio.setdefault(norm(s["activity"]), {"name": s["activity"]})
            if s["km"] > c.get("km", 0):
                c.update(km=s["km"], km_date=s["date"])
            if s["cardio_min"] and s["km"] >= 1:
                pace = s["cardio_min"] / s["km"]
                if pace < c.get("pace", 1e9):
                    c.update(pace=pace, pace_date=s["date"])
    return lifts, cardio


def new_bests(history, lifts, activity=None, km=None, minutes=None):
    """What a new session would beat. First time doing something isn't a personal best."""
    old_l, old_c = bests(history)
    out = []
    for name, sets in lifts.items():
        prev = old_l.get(norm(name))
        top = max(sets)
        if prev and top[0] > prev["set"][0] and top[0] > 0:
            out.append(f"{name} {fmt_w(top[0])} (was {fmt_w(prev['set'][0])})")
        elif prev and top[0] == prev["set"][0] and top[1] > prev["set"][1]:
            out.append(f"{name} {fmt_w(top[0])} x {top[1]} (was x {prev['set'][1]})")
    prev = old_c.get(norm(activity or ""))
    if prev and km:
        if km > prev.get("km", 0):
            out.append(f"longest {activity}: {km:g} km (was {prev['km']:g})")
        if minutes and km >= 1 and minutes / km < prev.get("pace", 1e9):
            out.append(f"fastest {activity} pace: {fmt_time(minutes / km)}/km (was {fmt_time(prev['pace'])})")
    return out


def summary(today=None):
    today = today or clock.uk_today()
    hist = sessions(before=today)
    days = sorted({s["date"] for s in hist})
    streak, d = 0, today if today in days else today - dt.timedelta(days=1)
    while d in days:
        streak += 1
        d -= dt.timedelta(days=1)
    monday = today - dt.timedelta(days=today.weekday())
    weeks, w = 0, monday if any(x >= monday for x in days) else monday - dt.timedelta(days=7)
    while any(w <= x < w + dt.timedelta(days=7) for x in days):
        weeks += 1
        w -= dt.timedelta(days=7)
    last = hist[-1] if hist else None
    return {"total": len(hist), "this_week": sum(1 for x in hist if x["date"] >= monday),
            "day_streak": streak, "week_streak": weeks,
            "last": {"date": last["date"].isoformat(), "title": last["title"], "days_ago": (today - last["date"]).days,
                     "id": last["id"]} if last else None}
