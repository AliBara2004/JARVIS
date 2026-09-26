"""JARVIS — HTTP server + API.

    python agent/main.py        → http://127.0.0.1:7777

Binds to localhost only. API keys stay in this process; the browser only ever
sees whether a key is present. POSTs need a custom header and a local
Origin/Host, so other websites open in the same browser can't drive JARVIS.
"""
import html
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import brain
import data
import google
import llm
import memory
import tools
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
         ".wasm": "application/wasm", ".onnx": "application/octet-stream"}
MAX_BODY = 64 * 1024
MAX_AUDIO = 12 * 1024 * 1024       # ~2 minutes of opus is well under this


def status():
    return {
        "mode": data.mode(),
        "notes": len(vault.get().notes),
        "links": len(vault.get().edges),
        "model": llm.status(),
        "voice": voice.status(),
        "google": {"configured": google.configured(), "connected": google.connected(), "demo": data.DEMO},
        "web_search": tools.WEB_SEARCH,
        "pending": tools.pending_list(),
    }


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

    # ------------------------------------------------------------ GET
    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "bad host"}, 403)
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}

        if u.path == "/api/status":
            return self._json(status())
        if u.path == "/api/graph":
            return self._json(vault.get().graph_json())
        if u.path == "/api/note":
            try:
                return self._json(vault.get().note_json(int(q.get("id", ""))))
            except (ValueError, IndexError):
                return self._json({"error": "no such note"}, 404)
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
        self._send(200, p.read_bytes(), TYPES.get(p.suffix.lower(), "application/octet-stream"), cache)

    # ------------------------------------------------------------ POST
    def do_POST(self):
        origin = self.headers.get("Origin")
        if not self._host_ok() or (origin and origin not in (ORIGIN, f"http://localhost:{PORT}")) \
                or self.headers.get("X-Jarvis") != "1":
            return self._json({"error": "forbidden"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        path = urlparse(self.path).path

        if path == "/api/listen":
            if n > MAX_AUDIO:
                return self._json({"error": "recording too long"}, 413)
            audio = self.rfile.read(n)
            try:
                return self._json({"text": voice.stt(audio, self.headers.get("Content-Type", "audio/webm"))})
            except voice.VoiceError as e:
                return self._json({"error": str(e)}, 502)

        if n > MAX_BODY:
            return self._json({"error": "too large"}, 413)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)

        if path == "/api/speak":
            try:
                audio = voice.tts(str(body.get("text", "")))
            except voice.VoiceError as e:
                return self._json({"error": str(e)}, 502)
            notice = voice.status()["tts_error"]
            return self._send(200, audio, "audio/mpeg", {"X-Voice-Notice": notice} if notice else None)

        if path == "/api/ask":
            return self._json(brain.ask(str(body.get("text", ""))[:4000]))
        if path == "/api/confirm":
            return self._json(brain.confirm(str(body.get("id", "")), bool(body.get("ok"))))
        if path == "/api/reset":
            brain.reset()
            return self._json({"ok": True})
        if path == "/api/google/disconnect":
            google.disconnect()
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    threading.Thread(target=llm.check, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"JARVIS · {data.mode()} mode · {len(vault.get().notes)} notes · {len(vault.get().edges)} links · model {llm.MODEL}")
    print(f"open {ORIGIN}   (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(ORIGIN)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
