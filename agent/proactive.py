"""Proactive nudges: JARVIS speaks first when something needs him, on Telegram and on the page.

- news:    a red folder in about 30 minutes (with his no-trade window if Risk Rules has one)
- session: the NY open in about 30 minutes and today's pre-session checklist isn't done
- replies: an unread email from one of his prospects
- streak:  evening, trained yesterday, nothing logged today

Each fires once (keyed in reminders' alert list). Nothing between QUIET_FROM and QUIET_TO. Which kinds are
on: JARVIS_NUDGES in .env (comma list, default all; "off" for none).
"""
import datetime as dt
import os
import re
import threading
import time

import clock
import data
import fitness
import reminders
import tools

KINDS = {"news", "session", "replies", "streak"}
LEAD_MIN = 30                 # how far ahead news and the open are flagged
QUIET_FROM, QUIET_TO = dt.time(22, 30), dt.time(7, 30)
STREAK_FROM = 19              # evening hour after which a missing workout is mentioned
INBOX_EVERY = 600             # seconds between inbox looks
CHECK_EVERY = 60


def enabled():
    v = os.environ.get("JARVIS_NUDGES", "all").strip().lower()
    if v in ("off", "none", "0"):
        return set()
    return KINDS if v in ("all", "") else {k.strip() for k in v.split(",")} & KINDS


def _quiet(now):
    t = now.time()
    return t >= QUIET_FROM or t < QUIET_TO


def _domain(url):
    m = re.search(r"(?:https?://)?(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})", str(url or "").lower())
    return m.group(1) if m else ""


def prospect_for_email(sender, prospects):
    """The prospect an email is from: their website's domain in the address, or their name in the sender."""
    s = str(sender or "").lower()
    for n in prospects:
        dom = _domain(tools._facts(n).get("website"))
        if dom and dom in s:
            return n
        words = [w for w in re.findall(r"[a-z0-9]+", n.title.lower()) if len(w) > 2]
        if words and all(w in s for w in words):
            return n
    return None


def check(notify, now=None, state=None):
    """One pass. Returns the alerts raised (text list), for tests."""
    kinds, now = enabled(), now or clock.uk_now().replace(tzinfo=None)
    state = state if state is not None else {}
    raised = []
    if not kinds or _quiet(now):
        return raised
    day = now.date().isoformat()
    ts = time.time()

    def alert(text, key):
        if reminders.raise_alert(text, key, notify):
            raised.append(text)

    if kinds & {"news", "session"}:
        s = tools._session_widget()
        buf = s.get("buffer_min")
        if "news" in kinds:
            for e in s["events"]:
                mins = (e["at"] - ts) / 60
                if e["red"] and LEAD_MIN - 5 < mins <= LEAD_MIN + 1:
                    window = ""
                    if buf:
                        a = dt.datetime.fromtimestamp(e["at"] - buf * 60).strftime("%H:%M")
                        b = dt.datetime.fromtimestamp(e["at"] + buf * 60).strftime("%H:%M")
                        window = f" Your no-trade window: {a}–{b}."
                    alert(f"🔴 {e['currency']} {e['title']} at {e['time']}, in {round(mins)} minutes.{window}",
                          f"news-{day}-{e['time']}-{e['title']}")
        if "session" in kinds and s.get("open"):
            mins = (s["open"] - ts) / 60
            cl = tools.checklist_state()
            if LEAD_MIN - 5 < mins <= LEAD_MIN + 1 and not cl["complete"]:
                alert(f"NY opens in {round(mins)} minutes. Pre-session checklist: {cl['done']}/{cl['total']} done. "
                      "Say \"run my checklist\" and we'll go through it.", f"session-{day}")

    if "replies" in kinds and ts - state.get("inbox_at", 0) > INBOX_EVERY:
        state["inbox_at"] = ts
        try:
            prospects = tools._pipeline()
            for m in data.inbox(15) if prospects else []:
                n = prospect_for_email(m.get("from"), prospects)
                if n:
                    alert(f"📩 {n.title} emailed you: \"{m.get('subject') or '(no subject)'}\". "
                          f"Say \"{n.title} replied\" and I'll move them on the board.", f"reply-{m.get('id')}")
        except Exception:
            pass                              # Google not connected, or offline

    if "streak" in kinds and now.hour >= STREAK_FROM:
        f = fitness.summary(now.date())
        last = f.get("last")
        if last and last["days_ago"] == 1 and f["day_streak"] >= 1:
            n = f["day_streak"]
            alert(f"No workout logged today. Your {n}-day streak ends at midnight." if n > 1
                  else "No workout logged today. You trained yesterday: keep it going?", f"streak-{day}")
    return raised


def _loop(notify):
    state = {}
    while True:
        try:
            check(notify, state=state)
        except Exception as e:
            print(f"[nudges] {e}", flush=True)
        time.sleep(CHECK_EVERY)


def start(notify):
    threading.Thread(target=_loop, args=(notify,), daemon=True).start()
