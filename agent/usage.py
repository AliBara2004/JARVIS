"""What JARVIS spends: Claude tokens, web searches, ElevenLabs characters and audio.

Every paid call reports here (llm.py and voice.py), so the totals are complete.
Figures are ESTIMATES from list prices; the providers' dashboards are the bill.
Kept in data/usage.json (gitignored), one row per UK day.
"""
import json
import os
import threading

import clock
import data

FILE = data.ROOT / "data" / "usage.json"
DAILY_BUDGET_USD = float(os.environ.get("JARVIS_DAILY_BUDGET", "2"))

# $ per million tokens (Anthropic list prices). Cache reads bill at 0.1x input, cache writes at 1.25x.
MODEL_PRICES = {"claude-opus-5": (5.0, 25.0), "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5": (2.0, 10.0),
                "claude-haiku-4-5": (1.0, 5.0), "claude-fable-5-1": (10.0, 50.0)}
CACHE_READ, CACHE_WRITE = 0.1, 1.25
WEB_SEARCH_USD = 10.0 / 1000          # per search
TTS_CREDITS_PER_CHAR = {"eleven_multilingual_v2": 1.0, "eleven_flash_v2_5": 0.5, "eleven_turbo_v2_5": 0.5}

_lock = threading.Lock()
_FIELDS = ("usd", "calls", "input", "output", "cache_read", "cache_write", "searches",
           "tts_chars", "tts_credits", "stt_seconds")


def _load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"days": {}}


def _save(db):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=1), encoding="utf-8")
    os.replace(tmp, FILE)


def _add(**amounts):
    with _lock:
        db = _load()
        day = db["days"].setdefault(clock.uk_today().isoformat(), {k: 0 for k in _FIELDS})
        for k, v in amounts.items():
            day[k] = round(day.get(k, 0) + v, 6)
        _save(db)


def record_llm(model, u):
    """u is the API's usage object (input_tokens, output_tokens, cache_*_input_tokens, server_tool_use)."""
    if not u:
        return
    pin, pout = MODEL_PRICES.get(model, MODEL_PRICES["claude-opus-5"])
    inp, out = u.get("input_tokens") or 0, u.get("output_tokens") or 0
    cr, cw = u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0
    searches = (u.get("server_tool_use") or {}).get("web_search_requests") or 0
    usd = (inp * pin + out * pout + cr * pin * CACHE_READ + cw * pin * CACHE_WRITE) / 1e6 + searches * WEB_SEARCH_USD
    _add(usd=usd, calls=1, input=inp, output=out, cache_read=cr, cache_write=cw, searches=searches)


def record_tts(chars, model):
    _add(tts_chars=chars, tts_credits=chars * TTS_CREDITS_PER_CHAR.get(model, 1.0))


def record_stt(seconds):
    _add(stt_seconds=seconds)


def summary():
    db = _load()
    today = clock.uk_today().isoformat()
    month = today[:7]
    blank = {k: 0 for k in _FIELDS}
    t = {**blank, **db["days"].get(today, {})}
    m = dict(blank)
    for d, row in db["days"].items():
        if d.startswith(month):
            for k in _FIELDS:
                m[k] += row.get(k, 0)
    return {"today": t, "month": m, "month_label": month, "budget_usd": DAILY_BUDGET_USD,
            "over_budget": t["usd"] >= DAILY_BUDGET_USD,
            "note": "Estimates from list prices. The bill is at console.anthropic.com and elevenlabs.io."}
