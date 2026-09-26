"""THE ONLY FILE THAT TOUCHES ALI'S REAL DATA.

Every read of a real folder, inbox or calendar goes through here. Nothing in
this module writes — files are opened "rb" and never anything else.

JARVIS_DEMO is read here and nowhere else:
    1 (default) — invented fixtures in data/demo_vault, safe to screen-record
    0           — the real folders listed in JARVIS_FOLDERS (";"-separated)
"""
import os
import re
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "data" / "demo_vault"

SKIP_DIRS = {"node_modules", ".git", ".obsidian", ".trash", "__pycache__", "Templates"}   # templates hold {{placeholders}}
EXTS = {".md", ".markdown", ".txt", ".pdf"}
MAX_BYTES = 2 * 1024 * 1024


def _load_env():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
DEMO = os.environ.get("JARVIS_DEMO", "1").strip() != "0"


def mode():
    return "demo" if DEMO else "live"


def folders():
    if DEMO:
        return [DEMO_DIR]
    raw = os.environ.get("JARVIS_FOLDERS", "")
    return [Path(p.strip()).expanduser() for p in raw.split(";") if p.strip()]


def _walk():
    """(root, path) for every indexable file, without reading any of them."""
    for root in folders():
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower() in EXTS:
                    yield root, p


def signature():
    """Cheap fingerprint of the vault (paths, sizes, mtimes): changes when any note is added, edited or removed."""
    sig = []
    for _, p in _walk():
        try:
            st = p.stat()
        except OSError:
            continue
        sig.append((str(p), st.st_size, st.st_mtime_ns))
    return hash(tuple(sorted(sig)))


def iter_files():
    """Yield (root, path, text) for every indexable file. Read-only."""
    for root in folders():
        if not root.is_dir():
            print(f"[data] folder not found, skipped: {root}")
    for root, p in _walk():
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            text = read_text(p)
        except OSError:
            continue
        if text.strip():
            yield root, p, text


def read_text(p):
    with open(p, "rb") as f:
        raw = f.read()
    if p.suffix.lower() == ".pdf":
        return _pdf_text(raw)
    return raw.decode("utf-8", errors="replace")


# --- PDF: stdlib-only, best effort. Handles plain and Flate-compressed text
# streams; scanned or exotically-encoded PDFs come back empty and are skipped.
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
_BT = re.compile(rb"BT(.*?)ET", re.S)
_STR = re.compile(rb"\((?:\\.|[^\\)])*\)")


def _pdf_text(raw):
    out = []
    for m in _STREAM.finditer(raw):
        data = m.group(1)
        try:
            data = zlib.decompress(data)
        except zlib.error:
            pass
        for block in _BT.finditer(data):
            parts = [_unescape(s[1:-1]) for s in _STR.findall(block.group(1))]
            if parts:
                out.append("".join(parts))
    return "\n".join(out)


def _unescape(b):
    b = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"", b"t": b"\t", b"b": b"",
                                                b"f": b"", b"(": b"(", b")": b")", b"\\": b"\\"}[m.group(1)], b)
    return b.decode("latin-1", errors="replace")


# ---------------------------------------------------------------- inbox & calendar
# Gmail is read-only. The calendar is read, and written only by create_event(),
# which runs solely after Ali confirms on screen or says "confirm".
import datetime as _dt
import html as _html
import json as _json

import clock

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
GCAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def _demo(name):
    p = ROOT / "data" / name
    return _json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def inbox(limit=10):
    """Unread inbox: [{id, from, subject, date, snippet}]."""
    if DEMO:
        now = clock.uk_now()
        return [{**m, "date": (now - _dt.timedelta(hours=m.pop("hours_ago", 1))).strftime("%a %d %b %H:%M")}
                for m in _demo("demo_inbox.json")][:limit]
    import google
    ids = google.request("GET", f"{GMAIL}/messages", {"q": "in:inbox is:unread", "maxResults": limit})
    out = []
    for m in ids.get("messages", []):
        msg = google.request("GET", f"{GMAIL}/messages/{m['id']}",
                             {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        h = {x["name"].lower(): x["value"] for x in msg.get("payload", {}).get("headers", [])}
        out.append({"id": m["id"], "from": h.get("from", ""), "subject": h.get("subject", "(no subject)"),
                    "date": h.get("date", ""), "snippet": _html.unescape(msg.get("snippet", ""))})
    return out


def calendar(start, days=1):
    """Events from `start` (date) for `days` days: [{title, start, end, all_day, location}], UK local times."""
    if DEMO:
        out = []
        for e in _demo("demo_calendar.json"):
            d = clock.uk_today() + _dt.timedelta(days=e["day"])
            if not (start <= d < start + _dt.timedelta(days=days)):
                continue
            h, m = map(int, e["time"].split(":"))
            s = _dt.datetime.combine(d, _dt.time(h, m))
            out.append({"title": e["title"], "start": s.strftime("%Y-%m-%dT%H:%M"),
                        "end": (s + _dt.timedelta(minutes=e["dur"])).strftime("%Y-%m-%dT%H:%M"),
                        "all_day": False, "location": e.get("location", "")})
        return sorted(out, key=lambda e: e["start"])
    import google
    t0 = _dt.datetime.combine(start, _dt.time(0))
    t1 = t0 + _dt.timedelta(days=days)
    res = google.request("GET", GCAL, {
        "timeMin": t0.isoformat() + clock.uk_iso_offset(t0), "timeMax": t1.isoformat() + clock.uk_iso_offset(t1),
        "singleEvents": "true", "orderBy": "startTime", "timeZone": "Europe/London", "maxResults": 50})
    out = []
    for e in res.get("items", []):
        s, en = e.get("start", {}), e.get("end", {})
        out.append({"title": e.get("summary", "(untitled)"),
                    "start": (s.get("dateTime") or s.get("date", ""))[:16],
                    "end": (en.get("dateTime") or en.get("date", ""))[:16],
                    "all_day": "date" in s, "location": e.get("location", "")})
    return out


def create_event(title, start, minutes, notes=""):
    """Only called after Ali confirms. No attendees, sendUpdates=none: nobody gets an invite."""
    s = _dt.datetime.strptime(start, "%Y-%m-%dT%H:%M")
    e = s + _dt.timedelta(minutes=minutes)
    if DEMO:
        return {"demo": True, "link": None}
    import google
    body = {"summary": title, "description": notes,
            "start": {"dateTime": s.isoformat(), "timeZone": "Europe/London"},
            "end": {"dateTime": e.isoformat(), "timeZone": "Europe/London"}}
    res = google.request("POST", GCAL, {"sendUpdates": "none"}, body)
    return {"demo": False, "link": res.get("htmlLink")}


# ---------------------------------------------------------------- the one write into the vault
# write_note() creates a NEW markdown file under <vault>/JARVIS/<folder>/. It opens with mode "x",
# so it can never overwrite, and there is no edit or delete anywhere in this module.
NOTES_SUBDIR = "JARVIS"
NOTE_FOLDERS = ("Scripts", "Ideas", "Journal", "Notes")
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\[\]#^]')


def vault_root():
    if DEMO:
        return DEMO_DIR
    f = folders()
    return f[0] if f else None


def write_note(folder, title, body, meta):
    root = vault_root()
    if root is None:
        raise RuntimeError("No vault folder set. Put your Obsidian vault path in JARVIS_FOLDERS in .env.")
    folder = folder if folder in NOTE_FOLDERS else "Notes"
    base = (root / NOTES_SUBDIR / folder)
    base.mkdir(parents=True, exist_ok=True)
    name = _UNSAFE.sub("", title).strip(" .")[:80] or "Untitled"
    day = clock.uk_today().isoformat()
    p = base / f"{day} {name}.md"
    n = 2
    while p.exists():
        p = base / f"{day} {name} ({n}).md"
        n += 1
    sandbox = (root / NOTES_SUBDIR).resolve()
    if sandbox not in p.resolve().parents:
        raise RuntimeError("Refusing to write outside the JARVIS folder.")
    fm = "---\n" + "".join(f"{k}: {str(v).replace(chr(10), ' ')}\n" for k, v in meta.items()) + "---\n\n"
    with open(p, "x", encoding="utf-8", newline="\n") as f:
        f.write(fm + f"# {title.strip()}\n\n" + body.strip() + "\n")
    return p.relative_to(root).as_posix()
