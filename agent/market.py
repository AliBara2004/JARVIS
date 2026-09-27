"""Trading-day context: Forex Factory's economic calendar and pre-session prices.

Public data, no keys:
- Calendar: Forex Factory's own JSON feed (nfs.faireconomy.media). Fetched at most
  once an hour, well inside their fair-use guidance.
- Prices: Yahoo Finance's chart endpoint. Unofficial and delayed; if it stops
  working, the brief says so and the calendar still works.
"""
import datetime as dt
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import clock

FF = "https://nfs.faireconomy.media/ff_calendar_{week}.json"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2d&interval=15m"
CALENDAR_TTL = 3600          # seconds; Forex Factory asks for polite polling
PRICES_TTL = 120
STALE_AFTER = 3 * 3600       # a last price older than this means the market is shut
SYMBOLS = [("NQ=F", "Nasdaq 100 futures"), ("ES=F", "S&P 500 futures"), ("^VIX", "VIX"),
           ("DX-Y.NYB", "Dollar index"), ("^TNX", "US 10-year yield"), ("CL=F", "Crude oil"), ("GC=F", "Gold")]

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"   # survives restarts; gitignored
BACKOFF = 15 * 60            # after a 429, leave the host alone this long

_cache = {}
_backoff_until = {}
_failed = {}                 # url -> when it last failed (e.g. next week's calendar isn't published until later)
FAIL_TTL = 15 * 60


class MarketError(Exception):
    pass


def _disk(url):
    return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")


def _get(url, ttl):
    now = clock.utcnow().timestamp()
    hit = _cache.get(url)
    if not hit and _disk(url).exists():
        try:
            hit = (_disk(url).stat().st_mtime, json.loads(_disk(url).read_text(encoding="utf-8")))
            _cache[url] = hit
        except (OSError, json.JSONDecodeError):
            hit = None
    if hit and now - hit[0] < ttl:
        return hit[1]
    host = urllib.parse.urlparse(url).netloc
    if now - _failed.get(url, 0) < FAIL_TTL:  # failed just now: don't ask again on every call
        if hit:
            return hit[1]
        raise MarketError(f"{host} had nothing for this a few minutes ago")
    if now < _backoff_until.get(host, 0):
        if hit:
            return hit[1]
        raise MarketError(f"{host} asked us to slow down; trying again in a few minutes")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (JARVIS personal assistant)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        _failed[url] = now
        if e.code == 429:
            _backoff_until[host] = now + BACKOFF
        if hit:
            return hit[1]                     # stale beats nothing
        raise MarketError(f"{host} unavailable: {e.code} {e.reason}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        _failed[url] = now
        if hit:
            return hit[1]
        raise MarketError(f"{host} unavailable: {getattr(e, 'reason', e)}")
    _cache[url] = (now, data)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _disk(url).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass                                  # the cache is an optimisation, not a requirement
    return data


def _uk(iso):
    """Forex Factory timestamp (with offset) → naive UK wall-clock datetime."""
    t = dt.datetime.fromisoformat(iso).astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t + dt.timedelta(hours=clock.uk_offset(t))


def calendar(day):
    """Events on `day` (UK date), in UK time: [{time, at, currency, impact, title, forecast, previous}]."""
    events = []
    for week in ("thisweek", "nextweek"):
        try:
            events += _get(FF.format(week=week), CALENDAR_TTL)
        except MarketError:
            if week == "thisweek":
                raise
    out = []
    for e in events:
        try:
            at = _uk(e["date"])
        except (KeyError, ValueError):
            continue
        if at.date() != day:
            continue
        out.append({"at": at, "time": at.strftime("%H:%M"), "currency": e.get("country", ""),
                    "impact": e.get("impact", ""), "title": e.get("title", ""),
                    "forecast": e.get("forecast") or "", "previous": e.get("previous") or ""})
    seen, uniq = set(), []
    for e in sorted(out, key=lambda e: e["at"]):
        key = (e["at"], e["currency"], e["title"])
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    return uniq


def _quote(sym, name):
    d = _get(YAHOO.format(sym=urllib.parse.quote(sym)), PRICES_TTL)["chart"]
    if d.get("error") or not d.get("result"):
        raise MarketError(f"no data for {name}")
    r = d["result"][0]
    m, ts = r["meta"], r.get("timestamp") or []
    q = r["indicators"]["quote"][0]
    last, prev = m.get("regularMarketPrice"), m.get("chartPreviousClose") or m.get("previousClose")
    bars = [(dt.datetime.utcfromtimestamp(t), h, lo) for t, h, lo in zip(ts, q.get("high", []), q.get("low", []))
            if h is not None and lo is not None]
    last_at = bars[-1][0] if bars else None
    out = {"symbol": sym, "name": name, "last": last, "prev_close": prev,
           "change": (last - prev) if last is not None and prev else None,
           "pct": 100 * (last - prev) / prev if last is not None and prev else None,
           "as_of_utc": last_at.isoformat(timespec="minutes") if last_at else None,
           "stale": not last_at or (clock.utcnow() - last_at).total_seconds() > STALE_AFTER}
    if sym == "NQ=F" and bars:
        # Overnight = since the last Globex reopen, 18:00 New York time.
        now = clock.utcnow()
        reopen = dt.datetime.combine(now.date(), dt.time(18)) - dt.timedelta(hours=clock.ny_offset(now))
        if reopen > now:
            reopen -= dt.timedelta(days=1)
        night = [b for b in bars if b[0] >= reopen]
        if night:
            out["overnight_high"] = max(b[1] for b in night)
            out["overnight_low"] = min(b[2] for b in night)
    return out


def quotes():
    """Pre-session snapshot of the markets NQ traders watch. Failures are per symbol."""
    with ThreadPoolExecutor(max_workers=len(SYMBOLS)) as pool:
        futures = [(s, n, pool.submit(_quote, s, n)) for s, n in SYMBOLS]
    out, errors = [], []
    for s, n, f in futures:
        try:
            out.append(f.result())
        except (MarketError, KeyError, IndexError, TypeError) as e:
            errors.append(f"{n}: {e}")
    if not out:
        raise MarketError("; ".join(errors) or "no prices")
    return out, errors
