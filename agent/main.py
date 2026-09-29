"""JARVIS — HTTP server + API.

    python agent/main.py        → http://127.0.0.1:7777

Binds to localhost only. API keys stay in this process; the browser only ever
sees whether a key is present. POSTs need a custom header and a local
Origin/Host, so other websites open in the same browser can't drive JARVIS.
"""
import datetime as dt
import hashlib
import html
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
from urllib.parse import parse_qs, urlparse

import attach
import backup
import brain
import days
import proactive
import status
import checkin
import data
import google
import telegram
import llm
import memory
import capture
import clock
import reminders
import tools
import usage
import vault
import voice

HOST = "127.0.0.1"
PORT = int(os.environ.get("JARVIS_PORT", "7777"))
ORIGIN = f"http://{HOST}:{PORT}"
ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}"}
UI = (data.ROOT / "ui").resolve()
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".ico": "image/x-icon", ".json": "application/json", ".mjs": "text/javascript; charset=utf-8",
         ".wasm": "application/wasm", ".onnx": "application/octet-stream", ".woff2": "font/woff2"}
REINDEX_EVERY = 30               # seconds between checks for notes added or edited in Obsidian
MAX_BODY = 64 * 1024
SERVER = {}                      # the running server, so /api/shutdown can stop it
PAGE_SEEN = {"at": 0.0}          # last time an open JARVIS page checked in
BROWSER_WAIT = 5                 # seconds to let an already-open tab reconnect before opening a new one
MAX_AUDIO = 12 * 1024 * 1024       # ~2 minutes of opus is well under this


def code_version():
    """Fingerprint of the server-side code. The launcher restarts a running JARVIS when this changes.
    (The page itself is served fresh on every load, so UI changes only need a refresh.)"""
    h = hashlib.sha1()
    # .env too, so changed settings (a new key, a moved vault) also restart JARVIS. Only this one-way
    # fingerprint leaves the process, never the values.
    for p in sorted((data.ROOT / "agent").glob("*.py")) + [data.ROOT / "agent" / "prompt.md", data.ROOT / "CLAUDE.md",
                                                           data.ROOT / ".env"]:
        if p.exists():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


CODE_VERSION = code_version()


_proc = {"cpu": None, "at": 0.0, "pct": 0.0}


def proc_stats():
    """JARVIS's own process: CPU share since the last look, and memory in use. Stdlib only."""
    t, now = os.times(), time.time()
    cpu = t.user + t.system
    if _proc["cpu"] is not None and now - _proc["at"] > 0.5:
        _proc["pct"] = max(0.0, 100 * (cpu - _proc["cpu"]) / (now - _proc["at"]) / (os.cpu_count() or 1))
    if _proc["cpu"] is None or now - _proc["at"] > 0.5:
        _proc.update(cpu=cpu, at=now)
    mem = 0.0
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] +                            [(f, ctypes.c_size_t) for f in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                                           "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                                           "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
            pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            info = k32.K32GetProcessMemoryInfo                 # argtypes matter: a 64-bit handle gets truncated without them
            info.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            info.restype = wintypes.BOOL
            if info(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                mem = pmc.WorkingSetSize / 2**20
        else:
            import resource
            mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        pass
    return {"cpu_pct": round(_proc["pct"], 1), "mem_mb": round(mem), "threads": threading.active_count()}


def status():
    return {
        "mode": data.mode(),
        "code_version": CODE_VERSION,
        "notes": len(vault.get().notes),
        "links": len(vault.get().edges),
        "graph_version": vault.version(),
        "model": llm.status(),
        "voice": voice.status(),
        "google": {"configured": google.configured(), "connected": google.connected(), "demo": data.DEMO,
                   "drafts": google.has_scope("https://www.googleapis.com/auth/gmail.compose")},
        "web_search": tools.WEB_SEARCH,
        "usage": usage.summary(),
        "telegram": telegram.status(),
        "checkin": checkin.status(),
        "backup": backup.status(),
        "pending": tools.pending_list(),
        "vault": data.vault_root().name if data.vault_root() else "",     # for obsidian:// links
        "proc": proc_stats(),
        "alerts": reminders.alerts(),                   # reminders that fired and the page hasn't shown yet
    }

# Open the page in the system browser. JARVIS_BROWSER=edge opens Microsoft Edge instead (it has the natural
# neural voices Ryan, Thomas, Sonia for the free voice fallback).
EDGE_PATHS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")


def open_page(url):
    if os.environ.get("JARVIS_BROWSER", "default").lower() == "edge" and os.name == "nt":
        edge = next((p for p in EDGE_PATHS if os.path.exists(p)), None)
        if edge:
            subprocess.Popen([edge, url], close_fds=True)
            return
    webbrowser.open(url)



def ui_version():
    """Changes whenever a page file changes (name, size, time), so each version gets fresh URLs."""
    h = hashlib.sha256()
    for f in sorted(UI.glob("*")):
        if f.suffix in (".js", ".css"):
            st = f.stat()
            h.update(f"{f.name}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:10]


def versioned(html):
    """index.html with ?v=<version> on our own scripts and stylesheet. Browsers always load the current
    code, and nothing between browser and server (a security scanner, a stale cache) can stay stuck on
    one fixed address."""
    v = ui_version().encode()
    for name in (b"styles.css", b"wake.js", b"graph3d.js", b"graph.js", b"app.js"):
        html = html.replace(b'"' + name + b'"', b'"' + name + b"?v=" + v + b'"')
    return html

class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128         # the page opens ~20 connections at once; Python's default backlog of 5 dropped some

    def handle_error(self, request, client_address):
        """A tab that reloads or closes mid-reply hangs up on us. That's normal, not an error worth a traceback."""
        if isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not (extra and "Cache-Control" in extra):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str).encode("utf-8"), "application/json")

    def _page(self, title, msg, code=200):
        body = f"""<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>
<body style="background:#07060c;color:#ece6ff;font:15px system-ui;display:grid;place-items:center;height:100vh;margin:0">
<div style="max-width:480px;text-align:center"><h2 style="color:#f0abfc">{html.escape(title)}</h2><p>{html.escape(msg)}</p></div>"""
        self._send(code, body.encode(), "text/html; charset=utf-8")

    def _host_ok(self):
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def _stream_ask(self, text, attachments=None, spoken=False):
        """Newline-delimited JSON events while JARVIS answers, then {"type": "done", ...result}.
        HTTP/1.0: the response ends when the connection closes, so no chunked encoding needed."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

        gone = {"yes": False}

        def emit(ev):
            # If Ali interrupts, the page stops reading. Finish the answer anyway (quietly), so the
            # conversation history is complete for his follow-up.
            if gone["yes"]:
                return
            try:
                self.wfile.write((json.dumps(ev, default=str, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                gone["yes"] = True

        emit({"type": "done", **brain.ask(text, emit, attachments=attachments, spoken=spoken)})

    # ------------------------------------------------------------ GET
    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "bad host"}, 403)
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}

        if u.path == "/api/status":
            st = status()
            st["page_seen_ago"] = round(time.time() - PAGE_SEEN["at"]) if PAGE_SEEN["at"] else None
            if self.headers.get("X-Jarvis-Launcher") != "1":        # the launcher asking doesn't count as a tab
                PAGE_SEEN["at"] = time.time()
            return self._json(st)
        if u.path == "/api/graph":
            return self._json(vault.get().graph_json())
        if u.path == "/api/note":
            try:
                return self._json(vault.get().note_json(int(q.get("id", ""))))
            except (ValueError, IndexError):
                return self._json({"error": "no such note"}, 404)
        if u.path == "/api/capture":                   # the latest screenshot, for the page's attachment tray
            return self._json({"capture": capture.latest()})
        if u.path == "/api/widgets":
            return self._json(tools.widgets())
        if u.path == "/api/pipeline":
            return self._json(tools.pipeline_board())
        if u.path == "/api/history":
            return self._json({"turns": brain.transcript()})
        if u.path == "/api/memory":
            return self._json({"facts": memory.all_facts()})
        if u.path == "/api/search":
            hits = vault.get().search(q.get("q", ""), k=int(q.get("k", "8")))
            return self._json({"results": [{"id": n.id, "title": n.title, "type": n.type,
                                             "rel": n.rel, "score": round(s, 2)} for s, n in hits]})

        if u.path == "/oauth/start":
            if not google.configured():
                return self._page("Google not configured", "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env, then restart JARVIS.", 400)
            return self._send(302, b"", "text/plain", {"Location": google.auth_url(f"{ORIGIN}/oauth/callback")})
        if u.path == "/oauth/callback":
            if "error" in q:
                return self._page("Google sign-in cancelled", q["error"], 400)
            try:
                google.finish(q.get("state", ""), q.get("code", ""))
            except google.GoogleError as e:
                return self._page("Google sign-in failed", str(e), 400)
            return self._page("Google connected", "JARVIS can now read your Gmail and calendar. You can close this tab.")

        rel = "index.html" if u.path in ("", "/") else u.path.lstrip("/")
        p = (UI / rel).resolve()
        if UI not in p.parents or not p.is_file():
            return self._json({"error": "not found"}, 404)
        # Vendored runtime/models are large and never change: let the browser cache them.
        cache = {"Cache-Control": "public, max-age=604800"} if "vendor" in p.relative_to(UI).parts else None
        body = p.read_bytes()
        if rel == "index.html":
            body = versioned(body)
        self._send(200, body, TYPES.get(p.suffix.lower(), "application/octet-stream"), cache)

    # ------------------------------------------------------------ POST
    def do_POST(self):
        origin = self.headers.get("Origin")
        if not self._host_ok() or (origin and origin not in (ORIGIN, f"http://localhost:{PORT}")) \
                or self.headers.get("X-Jarvis") != "1":
            return self._json({"error": "forbidden"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        path = urlparse(self.path).path

        if path == "/api/attach":
            if n > attach.MAX_PDF:
                return self._json({"error": "That file is over 20 MB."}, 413)
            name = urllib.parse.unquote(self.headers.get("X-Filename", "file"))[:80]
            try:
                return self._json(attach.add(self.rfile.read(n), name, self.headers.get("Content-Type", "")))
            except attach.AttachError as e:
                return self._json({"error": str(e)}, 415)

        if path == "/api/listen":
            if n > MAX_AUDIO:
                return self._json({"error": "recording too long"}, 413)
            audio = self.rfile.read(n)
            try:
                text, engine = voice.listen(audio, self.headers.get("Content-Type", "audio/webm"))
                return self._json({"text": text, "engine": engine})
            except voice.VoiceError as e:
                return self._json({"error": str(e), "local": voice.status()["local"]["stt"]}, 502)

        if n > MAX_BODY:
            return self._json({"error": "too large"}, 413)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)

        if path == "/api/sayable":                     # the same cleaning, for the browser's own voice
            return self._json({"text": voice.speakable(str(body.get("text", ""))[:2000])})
        if path == "/api/speak":
            try:
                audio, mime, engine = voice.speak(str(body.get("text", "")), str(body.get("previous_text", ""))[-500:])
            except voice.VoiceError as e:
                return self._json({"error": str(e)}, 502)
            notice = voice.status()["tts_error"] if engine == "elevenlabs" else ""
            return self._send(200, audio, mime, {"X-Voice-Engine": engine, **({"X-Voice-Notice": notice} if notice else {})})

        if path == "/api/ask":
            return self._json(brain.ask(str(body.get("text", ""))[:4000]))
        if path == "/api/ask/stream":
            ids = [str(a) for a in body.get("attachments") or []][:5]
            return self._stream_ask(str(body.get("text", ""))[:4000], ids, bool(body.get("spoken")))
        if path == "/api/confirm":
            return self._json(brain.confirm(str(body.get("id", "")), bool(body.get("ok"))))
        if path == "/api/capture":
            try:
                return self._json({"capture": capture.grab()})
            except Exception as e:
                return self._json({"error": str(e)}, 500)
        if path == "/api/reminders/seen":
            reminders.seen([str(i) for i in body.get("ids") or []][:50])
            return self._json({"ok": True})
        if path == "/api/goals":
            try:
                b = body
                if b.get("action") == "add" and str(b.get("text", "")).strip():
                    return self._json(tools.add_goal_from_widget(str(b["text"])))
                if b.get("action") == "tick":
                    return self._json(tools.set_goal(b.get("index"), b.get("done", True)))
                return self._json({"error": "action must be add or tick"}, 400)
            except (IndexError, ValueError, TypeError, RuntimeError) as e:
                return self._json({"error": str(e)}, 409)
        if path == "/api/checklist":
            key = str(body.get("key", ""))
            tools.tick_checklist("all" if key == "all" else [key], bool(body.get("done", True)))
            return self._json(tools.widgets())
        if path == "/api/pipeline":
            stage = str(body.get("stage", "")).lower()
            if stage not in status.PROSPECT_STAGES:
                return self._json({"error": "unknown stage"}, 400)
            return self._json(tools.move_prospect(str(body.get("title", ""))[:200], stage))
        if path == "/api/reset":
            brain.reset()
            return self._json({"ok": True})
        if path == "/api/shutdown":
            # Only the launcher uses this, to swap an old JARVIS for a new one. Same local-only checks as every POST.
            self._json({"ok": True})
            threading.Thread(target=SERVER["srv"].shutdown, daemon=True).start()
            return
        if path == "/api/checkin":
            if body.get("action") == "skip":
                checkin.skip()
                return self._json({"ok": True})
            return self._json({"question": brain.ask_checkin()})
        if path == "/api/telegram/unpair":
            telegram.unpair()
            return self._json({"ok": True})
        if path == "/api/google/disconnect":
            google.disconnect()
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)


PREP_MINUTES = 10


def watch_meetings():
    """Ten minutes before a calendar event with one of his prospects: a prep brief on Telegram and the page."""
    cache = {"at": 0.0, "events": []}
    while True:
        try:
            if time.time() - cache["at"] > 300:          # the calendar is read at most every 5 minutes
                cache.update(at=time.time(), events=data.calendar(clock.uk_today(), 1))
            now = clock.uk_now().replace(tzinfo=None)
            for e in cache["events"]:
                if e.get("all_day"):
                    continue
                start = dt.datetime.strptime(e["start"][:16], "%Y-%m-%dT%H:%M")
                mins = (start - now).total_seconds() / 60
                if 0 < mins <= PREP_MINUTES + 1:
                    n = tools._prospect_for(e["title"])
                    if n:
                        reminders.raise_alert(tools.meeting_alert_text(e, n), f"prep-{e['start']}-{n.title}", telegram.notify)
        except Exception:
            pass
        time.sleep(60)


def watch_hotkey():
    """Ctrl+Alt+J anywhere in Windows: screenshot the screen and attach it to the next question."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x0312
    if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("J")):
        return                                           # something else owns Ctrl+Alt+J; the 📸 button still works
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            try:
                capture.grab()
            except Exception:
                pass


def watch_vault():
    """Rebuild the index when notes change on disk, so Obsidian edits reach JARVIS without a restart."""
    sig = data.signature()
    while True:
        time.sleep(REINDEX_EVERY)
        try:
            new = data.signature()
            if new != sig:
                sig = new
                v = vault.reload()
                backup.mark_dirty()
                print(f"[vault] change detected, re-indexed: {len(v.notes)} notes, {len(v.edges)} links")
        except Exception as e:
            print(f"[vault] re-index failed: {e}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    threading.Thread(target=llm.check, daemon=True).start()
    threading.Thread(target=watch_vault, daemon=True).start()
    telegram.start()
    reminders.start(notify=telegram.notify)
    proactive.start(telegram.nudge)
    days.start(lambda x: [n.title for n in vault.get().notes] if x == "titles" else vault.reload())
    threading.Thread(target=voice.localvoice.warm, daemon=True).start()   # Kokoro loaded before the first reply
    threading.Thread(target=watch_meetings, daemon=True).start()
    threading.Thread(target=watch_hotkey, daemon=True).start()
    backup.start()
    srv = Server((HOST, PORT), Handler)
    SERVER["srv"] = srv
    print(f"JARVIS · {data.mode()} mode · {len(vault.get().notes)} notes · {len(vault.get().edges)} links · model {llm.MODEL}" + (f" (chat on {llm.FAST_MODEL})" if llm.ROUTING != "off" else ""))
    print(f"open {ORIGIN}   (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        # An already-open JARVIS tab reconnects (and reloads) by itself; only open one if none did.
        def open_if_needed():
            if not PAGE_SEEN["at"]:
                open_page(ORIGIN)
        threading.Timer(BROWSER_WAIT, open_if_needed).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
