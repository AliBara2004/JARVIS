"""Google sign-in (installed-app loopback flow with PKCE) and authorised requests.

Holds tokens, never data. Reading Gmail/Calendar happens in data.py.
Scopes: Gmail read-only, and Calendar events (created only after Ali confirms).
The token lives in .secrets/ — gitignored, readable only by this Windows user.
"""
import base64
import hashlib
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = ROOT / ".secrets" / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose",      # drafts only: JARVIS has no send code
          "https://www.googleapis.com/auth/calendar.events"]


def has_scope(scope):
    """Whether the saved sign-in includes a permission (older sign-ins predate drafts)."""
    try:
        return scope in json.loads(TOKEN.read_text()).get("scope", "")
    except (OSError, json.JSONDecodeError):
        return False
_pending = {}


class GoogleError(Exception):
    pass


def configured():
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def connected():
    try:
        return bool(json.loads(TOKEN.read_text()).get("refresh_token"))
    except (OSError, json.JSONDecodeError):
        return False


def auth_url(redirect):
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    _pending[state] = (verifier, redirect)
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    })


def finish(state, code):
    if state not in _pending:
        raise GoogleError("Sign-in link expired or was not started here. Try Connect again.")
    verifier, redirect = _pending.pop(state)
    tok = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                          "code_verifier": verifier})
    if "refresh_token" not in tok:
        raise GoogleError("Google didn't return a refresh token. Remove JARVIS at myaccount.google.com/permissions and connect again.")
    _save(tok)


def disconnect():
    if not TOKEN.exists():
        return
    try:
        TOKEN.unlink()
    except PermissionError:
        # Older sign-ins were locked read/write only (no delete). Emptying it disconnects just as well.
        TOKEN.write_text("{}", encoding="utf-8")


def _token_request(fields):
    fields = {**fields, "client_id": os.environ["GOOGLE_CLIENT_ID"],
              "client_secret": os.environ["GOOGLE_CLIENT_SECRET"]}
    req = urllib.request.Request("https://oauth2.googleapis.com/token",
                                 data=urllib.parse.urlencode(fields).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = _json(e)
        if body.get("error") == "invalid_grant":
            disconnect()
            raise GoogleError("Google sign-in has expired (test-mode apps expire after 7 days). Click Google to reconnect.")
        raise GoogleError(f"Google token error: {body.get('error_description') or body.get('error') or e.code}")
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    return tok


def _save(tok):
    TOKEN.parent.mkdir(exist_ok=True)
    old = json.loads(TOKEN.read_text()) if TOKEN.exists() else {}
    old.update(tok)
    fresh = not TOKEN.exists()
    TOKEN.write_text(json.dumps(old), encoding="utf-8")
    if fresh and os.name == "nt":
        subprocess.run(["icacls", str(TOKEN), "/inheritance:r", "/grant:r",
                        f"{os.environ.get('USERNAME', '')}:(R,W,D)"], capture_output=True)


def _access_token(force=False):
    if not connected():
        raise GoogleError("Google isn't connected. Click the Google chip to sign in.")
    tok = json.loads(TOKEN.read_text())
    if force or time.time() > tok.get("expires_at", 0):
        new = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        _save(new)
        tok.update(new)
    return tok["access_token"]


def request(method, url, params=None, body=None):
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    for attempt in (0, 1):
        req = urllib.request.Request(url, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {_access_token(force=attempt == 1)}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 0:
                continue
            err = _json(e).get("error", {})
            msg = err.get("message") if isinstance(err, dict) else err
            raise GoogleError(f"Google API {e.code}: {msg or e.reason}")
        except urllib.error.URLError as e:
            raise GoogleError(f"Can't reach Google: {e.reason}")


def _json(e):
    try:
        return json.loads(e.read() or b"{}")
    except Exception:
        return {}
