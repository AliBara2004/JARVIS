"""Claude over raw HTTP (stdlib only — no SDK, by design of this project).

The key never leaves this process.
"""
import json
import os
import time
import urllib.error
import urllib.request

import data  # noqa: F401 — loads .env before the constants below are read
import usage

API = "https://api.anthropic.com/v1"
MODEL = os.environ.get("JARVIS_MODEL", "claude-opus-5-5")          # hard turns, research, attachments, filing
FAST_MODEL = os.environ.get("JARVIS_FAST_MODEL", "claude-sonnet-5")  # everyday questions (brain.route picks)
CHEAP_MODEL = os.environ.get("JARVIS_CHEAP_MODEL", "claude-haiku-4-5")   # small talk, ticks, logs, reminders
CACHE_TTL = os.environ.get("JARVIS_CACHE_TTL", "1h")                 # "1h" survives the gaps between chats; "5m" = default
ROUTING = os.environ.get("JARVIS_ROUTING", "auto").lower()           # "off" = every turn on MODEL
EFFORT = os.environ.get("JARVIS_EFFORT", "low")      # chat is fast at low; research uses medium
FALLBACK_BETA = "server-side-fallback-2026-07-01"    # re-runs a declined request on Anthropic's recommended model

_state = {"status": "unchecked", "detail": "", "error_at": 0.0}
_no_fallbacks = set()     # models this account can't use the fallback beta on
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
    info = {"model": MODEL, "fast_model": FAST_MODEL if ROUTING != "off" else None}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"state": "missing", "detail": "No ANTHROPIC_API_KEY in .env", **info}
    return {"state": _state["status"], "detail": _state["detail"], **info}


def _headers(fallbacks=False):
    h = {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01",
         "content-type": "application/json"}
    if fallbacks:
        h["anthropic-beta"] = FALLBACK_BETA
    return h


def check():
    """Free reachability check: look the model up. Never spends tokens."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _state.update(status="missing", detail="No key")
        return
    for model in dict.fromkeys([MODEL] + ([FAST_MODEL, CHEAP_MODEL] if ROUTING != "off" else [])):
        req = urllib.request.Request(f"{API}/models/{model}", headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=15):
                _state.update(status="ready", detail="")
        except urllib.error.HTTPError as e:
            return _fail(f"{model}: {e.code}: {_msg(e)}")
        except urllib.error.URLError as e:
            return _fail(f"unreachable: {e.reason}")


def cache_mark():
    return {"type": "ephemeral", "ttl": "1h"} if CACHE_TTL == "1h" else {"type": "ephemeral"}


def _cached(messages):
    """Mark the end of the conversation so the next turn reads it from cache (0.1x) instead of paying
    full input price for the whole history again. Copies the last message: history itself is never
    touched, so old marks don't pile up past the API's limit of 4."""
    if not messages:
        return messages
    last = dict(messages[-1])
    content = last["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    content = list(content)
    content[-1] = {**content[-1], "cache_control": cache_mark()}
    last["content"] = content
    return messages[:-1] + [last]


def _body(system, messages, tools, max_tokens, effort, model):
    model = model or MODEL
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": _cached(messages)}
    if "haiku" not in model:
        # Haiku 4.5 takes neither adaptive thinking nor effort; the cheap tier just answers
        body["thinking"] = {"type": "adaptive"}
        body["output_config"] = {"effort": effort or EFFORT}
    if tools:
        body["tools"] = tools
    return body


def call(system, messages, tools=None, max_tokens=4000, effort=None, model=None):
    body = _body(system, messages, tools, max_tokens, effort, model)
    with _open(body) as r:
        resp = json.loads(r.read())
    usage.record_llm(body["model"], resp.get("usage"))
    return resp


def stream(system, messages, tools=None, max_tokens=4000, effort=None, on_text=None, model=None):
    """Same result as call(), but text reaches on_text(delta) as it's generated.
    Rebuilds every content block (thinking + signature, text, tool_use) exactly, so the
    message can go back into the conversation unchanged."""
    body = _body(system, messages, tools, max_tokens, effort, model)
    body["stream"] = True
    try:
        resp = _read_stream(_open(body), on_text)
    except (TimeoutError, ConnectionError, OSError) as e:
        raise LLMError(f"connection dropped mid-reply: {e}")
    usage.record_llm(body["model"], resp["usage"])
    return resp


def _read_stream(resp, on_text):
    blocks, stop, used = {}, None, {}
    with resp as r:
        for raw in r:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            kind = ev.get("type")
            if kind == "message_start":
                used.update(ev.get("message", {}).get("usage") or {})
            elif kind == "content_block_start":
                b = dict(ev["content_block"])
                if b.get("type") == "tool_use":
                    b["_json"] = ""
                blocks[ev["index"]] = b
            elif kind == "content_block_delta":
                b, d = blocks[ev["index"]], ev["delta"]
                if d["type"] == "text_delta":
                    b["text"] = b.get("text", "") + d["text"]
                    if on_text:
                        on_text(d["text"])
                elif d["type"] == "thinking_delta":
                    b["thinking"] = b.get("thinking", "") + d["thinking"]
                elif d["type"] == "signature_delta":
                    b["signature"] = b.get("signature", "") + d["signature"]
                elif d["type"] == "input_json_delta":
                    b["_json"] += d["partial_json"]
            elif kind == "content_block_stop":
                b = blocks[ev["index"]]
                if b.get("type") == "tool_use":
                    raw_json = b.pop("_json")
                    try:
                        b["input"] = json.loads(raw_json) if raw_json else {}
                    except json.JSONDecodeError:
                        raise LLMError("tool call arrived malformed")
            elif kind == "message_delta":
                stop = ev.get("delta", {}).get("stop_reason") or stop
                used.update({k: v for k, v in (ev.get("usage") or {}).items() if v is not None})
            elif kind == "error":
                raise LLMError(ev.get("error", {}).get("message", "stream error"))
    return {"content": [blocks[i] for i in sorted(blocks)], "stop_reason": stop, "usage": used}


def _open(body):
    """POST /v1/messages with retries; returns the open response (caller reads or streams it)."""
    for attempt in range(3):
        fallbacks = body["model"] not in _no_fallbacks
        if fallbacks:
            body["fallbacks"] = "default"
        else:
            body.pop("fallbacks", None)
        req = urllib.request.Request(f"{API}/messages", data=json.dumps(body).encode(),
                                     headers=_headers(fallbacks), method="POST")
        try:
            r = urllib.request.urlopen(req, timeout=120)
            _state.update(status="ready", detail="")
            return r
        except urllib.error.HTTPError as e:
            msg = _msg(e)
            if e.code == 400 and "fallback" in msg.lower() and fallbacks:
                _no_fallbacks.add(body["model"])     # beta not available for this model; carry on without it
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
