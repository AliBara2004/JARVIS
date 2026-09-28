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
    """Yield (root, path, text, mtime) for every indexable file. Read-only."""
    for root in folders():
        if not root.is_dir():
            print(f"[data] folder not found, skipped: {root}")
    for root, p in _walk():
        try:
            st = p.stat()
            if st.st_size > MAX_BYTES:
                continue
            text = read_text(p)
        except OSError:
            continue
        if text.strip():
            yield root, p, text, st.st_mtime


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

    def one(m):
        msg = google.request("GET", f"{GMAIL}/messages/{m['id']}",
                             {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        h = {x["name"].lower(): x["value"] for x in msg.get("payload", {}).get("headers", [])}
        return {"id": m["id"], "from": h.get("from", ""), "subject": h.get("subject", "(no subject)"),
                "date": h.get("date", ""), "snippet": _html.unescape(msg.get("snippet", ""))}
    # the messages are fetched side by side, not one after another (10 in about the time of 1)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=10) as pool:
        return list(pool.map(one, ids.get("messages", [])))


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
NOTE_FOLDERS = ("Scripts", "Ideas", "Journal", "Notes", "Video ideas", "Prospects", "Workouts", "Sources", "Proposals")
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
    meta = {"title": title.strip(), **meta}   # the filename carries the date; the note's name doesn't
    fm = "---\n" + "".join(f"{k}: {str(v).replace(chr(10), ' ')}\n" for k, v in meta.items()) + "---\n\n"
    with open(p, "x", encoding="utf-8", newline="\n") as f:
        f.write(fm + f"# {title.strip()}\n\n" + body.strip() + "\n")
    return p.relative_to(root).as_posix()


def create_gmail_draft(to, subject, body):
    """Put a draft in Ali's Gmail Drafts folder, only after he taps "Save to Gmail drafts".
    There is deliberately no send function anywhere in JARVIS: he reviews and sends it himself."""
    if DEMO:
        return {"demo": True}
    import base64
    from email.message import EmailMessage
    import google
    if not google.has_scope("https://www.googleapis.com/auth/gmail.compose"):
        raise RuntimeError("Your Google sign-in predates drafts. Click the Google light to disconnect, then "
                           "connect again and allow drafts.")
    msg = EmailMessage()
    if to and "@" in to:
        msg["To"] = to
    msg["Subject"] = subject or ""
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    res = google.request("POST", f"{GMAIL}/drafts", body={"message": {"raw": raw}})
    return {"demo": False, "id": res.get("id"), "link": "https://mail.google.com/mail/#drafts"}


# ---------------------------------------------------------------- editing an existing note
# Only after Ali asked in his own words AND confirmed a preview of the exact change (tools.edit_note).
# The note must still be exactly what the preview was built from, or nothing is written.
EDITABLE = {".md", ".markdown", ".txt"}


def note_file(rel):
    """Absolute path of a note inside the vault, refusing anything outside it or in hidden folders."""
    root = vault_root()
    if root is None:
        raise RuntimeError("No vault folder set.")
    p = (root / rel).resolve()
    if root.resolve() not in p.parents or p.suffix.lower() not in EDITABLE:
        raise RuntimeError("That isn't a note inside your vault.")
    if any(part.startswith(".") for part in p.relative_to(root.resolve()).parts):
        raise RuntimeError("JARVIS doesn't edit hidden folders like .obsidian.")
    return p


def read_note(rel):
    return note_file(rel).read_bytes().decode("utf-8")


def replace_note(rel, new_text, expect_sha):
    import hashlib
    p = note_file(rel)
    current = p.read_bytes()
    if hashlib.sha256(current).hexdigest() != expect_sha:
        raise RuntimeError("The note changed since I showed you the edit. Ask again and I'll redo it.")
    tmp = p.with_name(p.name + ".jarvis-tmp")
    tmp.write_bytes(new_text.encode("utf-8"))
    os.replace(tmp, p)                        # all or nothing: never a half-written note


# ---------------------------------------------------------------- today's goals (Daily note, Goals section only)
DAILY_DIR = "Daily"


def daily_rel(day):
    return f"{DAILY_DIR}/{day.isoformat()}.md"


def read_daily(day):
    """That day's Daily note text, or None if it doesn't exist yet."""
    root = vault_root()
    if root is None:
        raise RuntimeError("No vault folder set. Put your Obsidian vault path in JARVIS_FOLDERS in .env.")
    p = root / daily_rel(day)
    return p.read_bytes().decode("utf-8") if p.is_file() else None


def daily_template(day):
    """A new Daily note from Ali's Templates/Daily.md, or a bare one if he has no template."""
    t = vault_root() / "Templates" / "Daily.md"
    text = t.read_text(encoding="utf-8") if t.is_file() else "---\ntype: daily\ndate: {{date}}\n---\n# {{date}}\n"
    return text.replace("{{date}}", day.isoformat())


def save_goals(old_text, new_text):
    """Write today's Daily note. Only the Goals section may differ from what was read, and only today's
    note: anything else is refused. Creates the note from the template if it didn't exist (never overwrites)."""
    import goals
    day = clock.uk_today()
    p = vault_root() / daily_rel(day)
    base = old_text if old_text is not None else daily_template(day)
    if goals.without_section(new_text) != goals.without_section(base):
        raise RuntimeError("Refusing: that change reaches outside today's Goals section.")
    if old_text is None:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "x", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        return daily_rel(day)
    if p.read_bytes().decode("utf-8") != old_text:
        raise RuntimeError("Today's note changed while I was editing it. Try again.")
    tmp = p.with_name(p.name + ".jarvis-tmp")
    tmp.write_bytes(new_text.encode("utf-8"))
    os.replace(tmp, p)
    return daily_rel(day)


# ---------------------------------------------------------------- the LLM wiki (JARVIS/Wiki) and the daily log (JARVIS/Log)
# JARVIS owns these two folders outright: it may rewrite wiki pages and append to logs. Nothing here can
# reach Ali's own notes, and sources (JARVIS/Sources, via write_note) are new files only, never changed.
WIKI_DIR = "Wiki"
LOG_DIR = "Log"
WIKI_RESERVED = {"index", "log"}


def _jarvis_path(sub, name):
    root = vault_root()
    if root is None:
        raise RuntimeError("No vault folder set. Put your Obsidian vault path in JARVIS_FOLDERS in .env.")
    base = root / NOTES_SUBDIR / sub
    p = (base / name).resolve()
    if base.resolve() not in p.parents:
        raise RuntimeError(f"Refusing to write outside JARVIS/{sub}.")
    return root, base, p


def wiki_page_name(title):
    return _UNSAFE.sub("", str(title)).strip(" .")[:80]


def write_wiki(title, body, meta):
    """Create or replace one wiki page (JARVIS/Wiki/<title>.md). Atomic; can't touch index/log or leave the folder."""
    name = wiki_page_name(title)
    if not name or name.lower() in WIKI_RESERVED:
        raise RuntimeError("That page name is reserved or empty.")
    root, base, p = _jarvis_path(WIKI_DIR, f"{name}.md")
    base.mkdir(parents=True, exist_ok=True)
    fm = "---\n" + "".join(f"{k}: {str(v).replace(chr(10), ' ')}\n" for k, v in meta.items()) + "---\n\n"
    text = fm + f"# {name}\n\n" + body.strip() + "\n"
    existed = p.exists()
    tmp = p.with_name(p.name + ".jarvis-tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, p)
    return p.relative_to(root).as_posix(), existed


def write_wiki_index(text):
    root, base, p = _jarvis_path(WIKI_DIR, "index.md")
    base.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".jarvis-tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, p)
    return p.relative_to(root).as_posix()


def append_line(sub, name, line, header=""):
    """Append one line to JARVIS/Wiki/log.md or JARVIS/Log/<day>.md (created with `header` if new)."""
    if sub not in (WIKI_DIR, LOG_DIR):
        raise RuntimeError("Appending is only for the wiki log and the daily log.")
    root, base, p = _jarvis_path(sub, name)
    base.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        if f.tell() == 0 and header:
            f.write(header)
        f.write(line.replace("\n", " ").rstrip() + "\n")
    return p.relative_to(root).as_posix()
