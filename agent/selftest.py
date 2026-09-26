"""Guardrail self-test. No paid calls.

    python agent/selftest.py

Checks the rules in prompt.md against the code itself, so a rule that only
lives in the prompt shows up here as a gap. Server checks run if JARVIS is up.
"""
import http.client
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import brain  # noqa: E402
import data  # noqa: E402
import memory  # noqa: E402
import tools  # noqa: E402

ROOT = data.ROOT
AGENT = sorted((ROOT / "agent").glob("*.py"))
UI = [p for p in (ROOT / "ui").glob("*") if p.suffix in (".js", ".html", ".css")]
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))


def src(p):
    return p.read_text(encoding="utf-8")


def code_only(p):
    """Source minus comments and docstrings, so rules described in prose don't count as code."""
    s = re.sub(r'"""[\s\S]*?"""', "", src(p))
    return re.sub(r"#.*", "", s)


print("\nStatic: what the code can and can't do")
own = [p for p in AGENT if p.name != "selftest.py"]

hits = [p.name for p in own + UI if "JARVIS_DEMO" in code_only(p)]
check("JARVIS_DEMO is read in exactly one file (data.py)", hits == ["data.py"], f"found in {hits}")

secrets_ = ["ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "GOOGLE_CLIENT_SECRET", "xi-api-key", "x-api-key"]
leaks = [(p.name, s) for p in UI for s in secrets_ if s in src(p)]
check("No API key names in browser code", not leaks, str(leaks))

WRITE = re.compile(r"open\([^)]*['\"][wax]b?['\"]|write_text|write_bytes|\.unlink\(|rmtree|os\.remove|\.rename\(")
writers = sorted({p.name for p in own if WRITE.search(code_only(p))})
check("Only data.py (JARVIS/ notes), memory.py (memory/) and google.py (its token) write files",
      writers == ["data.py", "google.py", "memory.py"], f"writers: {writers}")
check("Vault and memory writes use exclusive-create (can't overwrite)",
      'open(p, "x"' in src(ROOT / "agent" / "data.py") and 'open(p, "x"' in src(ROOT / "agent" / "memory.py"))

allsrc = "\n".join(code_only(p) for p in own)
check("No way to send email (no Gmail send/drafts, no SMTP)",
      not re.search(r"messages/send|/drafts|smtplib|smtp\.", allsrc))
check("Calendar events: sendUpdates=none and no attendees",
      '"sendUpdates": "none"' in allsrc and "attendees" not in allsrc)
check("Gmail scope is read-only", "gmail.readonly" in allsrc and "gmail.send" not in allsrc
      and "gmail.modify" not in allsrc)
check("No connection to Tradovate or any broker", not re.search(r"tradovate\.com|/order|placeorder", allsrc, re.I))
check("The model has no tool that confirms pending actions",
      not any(s["name"] in ("resolve", "confirm", "confirm_action") for s in tools.SPECS))
check("Web research is gated (default ask)", tools.WEB_SEARCH in ("ask", "off"),
      f"JARVIS_WEB_SEARCH={tools.WEB_SEARCH}")

print("\nWrites stay in their sandboxes")
made = []
try:
    base = data.vault_root() / data.NOTES_SUBDIR
    for title, folder in [("../../../escape", "Notes"), (r"..\..\C:\Windows\x", "../.."), ("ok", "Nope")]:
        rel = data.write_note(folder, title, "selftest", {"type": "note"})
        made.append(data.vault_root() / rel)
        check(f"write_note({title!r}, {folder!r}) stays in JARVIS/", (data.vault_root() / rel).resolve().is_relative_to(
            base.resolve()), rel)
    rel2 = data.write_note("Notes", "ok", "second", {"type": "note"})
    made.append(data.vault_root() / rel2)
    check("Same title twice makes a second file, never overwrites", rel2 != made[-2].relative_to(data.vault_root()).as_posix())
    name = memory.remember("Selftest fact, please ignore", "test")
    made.append(memory.DIR / name)
    check("remember() writes one file inside memory/", (memory.DIR / name).is_file())
    try:
        memory.remember("x" * 600)
        check("remember() refuses oversized facts", False)
    except ValueError:
        check("remember() refuses oversized facts", True)
finally:
    for p in made:
        if p.exists():
            p.unlink()
    for d in [data.vault_root() / data.NOTES_SUBDIR / f for f in data.NOTE_FOLDERS] + [data.vault_root() / data.NOTES_SUBDIR]:
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()

print("\nInjected instructions are data, not commands")
check("Writes refused after reading files/email when Ali didn't ask",
      brain._write_blocked("remember", "who emailed me?", ["read_inbox"]))
check("…but allowed when Ali asked", not brain._write_blocked("remember", "remember that I charge £1500", ["search_brain"]))
check("…and allowed with nothing untrusted read", not brain._write_blocked("remember", "I've decided on £600 a workflow", []))
check("'give me some notes on…' counts as asking",
      not brain._write_blocked("write_note", "research the UN conference and give me some notes", ["research_web"]))
if data.DEMO:
    r = tools.run("search_brain", {"query": "forum zapier pricing"})
    check("Planted instruction in a note is flagged", bool(r["cards"][0].get("warn")))
    r = tools.run("read_inbox", {})
    check("Planted instruction in an email is flagged", bool(r["cards"][0].get("warn")))

print("\nSaying what was written")
check("Readback check spots a missing fact", not brain._said("Done, sir.", "I charge 1500 for migrations"))
check("…accepts a paraphrase that keeps the facts", brain._said("Got it: £1,500 to start for migrations, you charge.",
                                                                 "I charge £1500 for migrations"))
check("…but not one with the wrong number", not brain._said("Noted: you charge £2,000 for migrations.",
                                                              "I charge £1500 for migrations"))

print("\nCalendar needs Ali's confirm")
before = len(tools.pending_list())
r = tools.run("schedule_event", {"title": "Selftest", "start": "2099-01-01T10:00"})
check("schedule_event only creates a pending action", r["pending"] and len(tools.pending_list()) == before + 1)
tools.resolve(r["pending"], False)
check("Cancelling clears it", len(tools.pending_list()) == before)

print("\nTalking without a model")
for text, bad in [("hello jarvis", "Best match"), ("why?", "Best match"), ("can you hear me", "Nothing in your notes")]:
    reply = brain.fallback(text)["reply"]
    check(f"{text!r} gets conversation, not a search", bad not in reply, reply)

print("\nSecrets on disk")
for f in (".env", ".secrets/google_token.json"):
    ign = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", f]).returncode == 0
    check(f"{f} is gitignored", ign)

print("\nServer (skipped if JARVIS isn't running)")
try:
    def req(method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", 7777, timeout=5)
        c.request(method, path, body=body, headers={"Host": "127.0.0.1:7777", **(headers or {})})
        return c.getresponse().status
    req("GET", "/api/status")
    check("POST without X-Jarvis header is refused", req("POST", "/api/reset", {}, "{}") == 403)
    check("POST from another website's origin is refused",
          req("POST", "/api/reset", {"X-Jarvis": "1", "Origin": "https://evil.example"}, "{}") == 403)
    check("Wrong Host header (DNS rebinding) is refused", req("GET", "/api/status", {"Host": "evil.example"}) == 403)
    check("Path traversal can't reach .env", req("GET", "/../.env") == 404 and req("GET", "/%2e%2e/.env") == 404)
    import json
    c = http.client.HTTPConnection("127.0.0.1", 7777, timeout=5)
    c.request("GET", "/api/status", headers={"Host": "127.0.0.1:7777"})
    body = c.getresponse().read().decode()
    check("/api/status leaks no key material", not re.search(r"sk-ant|sk_[a-z0-9]{10}|GOCSPX", body))
except (ConnectionRefusedError, OSError):
    print("  SKIP  server not running")

failed = results.count(False)
print(f"\n{len(results) - failed}/{len(results)} passed" + (f", {failed} FAILED" if failed else ""))
sys.exit(1 if failed else 0)
