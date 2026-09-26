"""Claude over raw HTTP (stdlib only — no SDK, by design of this project).

The key never leaves this process.
"""
import json
import os
import time
import urllib.error
import urllib.request

import data  # noqa: F401 — loads .env before the constants below are read

API = "https://api.anthropic.com/v1"
MODEL = os.environ.get("JARVIS_MODEL", "claude-opus-5")
EFFORT = os.environ.get("JARVIS_EFFORT", "low")      # chat is fast at low; research uses medium
FALLBACK_BETA = "server-side-fallback-2026-07-01"    # re-runs a declined request on Anthropic's recommended model

_state = {"status": "unchecked", "detail": "", "fallbacks": True, "error_at": 0.0}
RETRY_AFTER = 60          # seconds before trying the model again after a hard failure


class LLMError(Exception):
    pass


def available():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    return _state["status"] != "error" or time.time() - _state["error_at"] > RETRY_AFTER


def _fail(detail):
    _state.update(status="error", detail=detail, error_at=time.time())


def status():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"state": "missing", "detail": "No ANTHROPIC_API_KEY in .env", "model": MODEL}
    return {"state": _state["status"], "detail": _state["detail"], "model": MODEL}


def _headers(beta=True):
    h = {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01",
         "content-type": "application/json"}
    if beta and _state["fallbacks"]:
        h["anthropic-beta"] = FALLBACK_BETA
    return h


def check():
    """Free reachability check: look the model up. Never spends tokens."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _state.update(status="missing", detail="No key")
        return
    req = urllib.request.Request(f"{API}/models/{MODEL}", headers=_headers(beta=False))
    try:
        with urllib.request.urlopen(req, timeout=15):
            _state.update(status="ready", detail="")
    except urllib.error.HTTPError as e:
        _fail(f"{e.code}: {_msg(e)}")
    except urllib.error.URLError as e:
        _fail(f"unreachable: {e.reason}")


def call(system, messages, tools=None, max_tokens=4000, effort=None):
    body = {
        "model": MODEL, "max_tokens": max_tokens, "system": system, "messages": messages,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort or EFFORT},
    }
    if tools:
        body["tools"] = tools
    for attempt in range(3):
        if _state["fallbacks"]:
            body["fallbacks"] = "default"
        else:
            body.pop("fallbacks", None)
        req = urllib.request.Request(f"{API}/messages", data=json.dumps(body).encode(),
                                     headers=_headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read())
            _state.update(status="ready", detail="")
            return resp
        except urllib.error.HTTPError as e:
            msg = _msg(e)
            if e.code == 400 and "fallback" in msg.lower() and _state["fallbacks"]:
                _state["fallbacks"] = False          # account can't use the beta; carry on without it
                continue
            if e.code in (429, 500, 502, 503, 529) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if e.code in (401, 403) or "credit balance" in msg.lower():
                _fail("No API credit. Add credit at console.anthropic.com, Plans & Billing"
                      if "credit balance" in msg.lower() else f"{e.code}: {msg}")
            raise LLMError(f"{e.code}: {msg}")
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(1.5)
                continue
            _fail(f"unreachable: {e.reason}")
            raise LLMError(f"unreachable: {e.reason}")
        except TimeoutError:
            raise LLMError("timed out")
    raise LLMError("gave up after retries")


def text_of(content):
    return "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()


def _msg(e):
    try:
        b = json.loads(e.read() or b"{}")
        return (b.get("error") or {}).get("message") or str(b)
    except Exception:
        return e.reason or "error"
