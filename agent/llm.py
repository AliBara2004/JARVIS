"""Claude over raw HTTP (stdlib only — no SDK, by design of this project).

The key never leaves this process.
"""
import http.client
import json
import os
import threading
import time
import urllib.error
import urllib.parse
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
# If a model fails (no credit, provider down), the turn is re-run on the first of these that has a key and
# isn't known to be down. Different providers, so one running out never leaves JARVIS silent.
FAILOVER = [m.strip() for m in os.environ.get("JARVIS_FAILOVER", "openrouter:openai/gpt-6-luna,claude-sonnet-5").split(",")
            if m.strip()]
FALLBACK_BETA = "server-side-fallback-2026-07-01"    # re-runs a declined request on Anthropic's recommended model

_state = {"status": "unchecked", "detail": "", "error_at": 0.0}
_notices = []             # recent failovers, shown by the page as a notice: {id, at, text}
_no_fallbacks = set()     # models this account can't use the fallback beta on
RETRY_AFTER = 60          # seconds before trying the model again after a hard failure


class LLMError(Exception):
    pass


def _has_key(model):
    return bool(_or_key()) if is_openrouter(model) else bool(os.environ.get("ANTHROPIC_API_KEY"))


def _usable(model):
    """Has a key, and (for Anthropic direct) hasn't just failed hard: no credit, bad key, unreachable."""
    if not _has_key(model):
        return False
    return is_openrouter(model) or _state["status"] != "error" or time.time() - _state["error_at"] > RETRY_AFTER


def _label(model):
    return str(model).split("/")[-1].replace("claude-", "").replace("openrouter:", "")


def available():
    return any(_usable(m) for m in [MODEL] + FAILOVER)


def _fail(detail):
    _state.update(status="error", detail=detail, error_at=time.time())


def status():
    info = {"model": MODEL, "fast_model": FAST_MODEL if ROUTING != "off" else None,
            "notices": [n for n in _notices if time.time() - n["at"] < 600],
            "failover": [m for m in FAILOVER if _has_key(m)]}
    if not any(_has_key(m) for m in [MODEL] + FAILOVER):
        return {"state": "missing", "detail": "No API key in .env (ANTHROPIC_API_KEY or OPENROUTER_API_KEY)", **info}
    if _state["status"] == "error" and available():       # Anthropic is down, but the turn has somewhere to go
        return {"state": "ready", "detail": f"on failover ({_state['detail']})", **info}
    return {"state": _state["status"], "detail": _state["detail"], **info}


def _headers(fallbacks=False):
    h = {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01",
         "content-type": "application/json"}
    if fallbacks:
        h["anthropic-beta"] = FALLBACK_BETA
    return h


def check():
    """Free reachability check: look each model up (OpenRouter: check the key). Never spends tokens."""
    for model in dict.fromkeys([MODEL] + ([FAST_MODEL, CHEAP_MODEL] if ROUTING != "off" else [])):
        if not _has_key(model):
            _state.update(status="missing", detail=f"No key for {model}")
            return
        if is_openrouter(model):
            req = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                         headers={"Authorization": f"Bearer {_or_key()}"})
        else:
            req = urllib.request.Request(f"{API}/models/{model}", headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=15):
                pass
        except urllib.error.HTTPError as e:
            return _fail(f"{model}: {e.code}: {_msg(e)}")
        except urllib.error.URLError as e:
            return _fail(f"unreachable: {e.reason}")
    _state.update(status="ready", detail="")


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


def _with_failover(model, run, said=None):
    """run(model); if that fails, run it again on the first usable FAILOVER model. A streamed reply that has
    already started speaking isn't re-run (Ali would hear it twice), and a provider known to be down is
    skipped straight away rather than waited on."""
    backups = [m for m in FAILOVER if m != model and _usable(m)]
    if backups and not _usable(model):
        model, backups = backups[0], backups[1:]
    while True:
        try:
            return run(model)
        except LLMError as e:
            if not backups or said or "malformed" in str(e):
                raise
            print(f"[llm] {model} failed ({e}); trying {backups[0]}", flush=True)
            _notices.append({"id": f"{time.time():.3f}", "at": time.time(),
                             "text": f"{_label(model)} didn't answer ({str(e)[:80]}). Switched to {_label(backups[0])}."})
            del _notices[:-5]
            model, backups = backups[0], backups[1:]


def call(system, messages, tools=None, max_tokens=4000, effort=None, model=None):
    return _with_failover(model or MODEL, lambda m: _call(system, messages, tools, max_tokens, effort, m))


def _call(system, messages, tools, max_tokens, effort, model):
    if is_openrouter(model):
        return _or_request(system, messages, tools, max_tokens, model, None, effort)
    body = _body(system, messages, tools, max_tokens, effort, model)
    with _open(body) as r:
        resp = json.loads(r.read())
    usage.record_llm(body["model"], resp.get("usage"))
    return resp


def stream(system, messages, tools=None, max_tokens=4000, effort=None, on_text=None, model=None):
    """Same result as call(), but text reaches on_text(delta) as it's generated.
    Rebuilds every content block (thinking + signature, text, tool_use) exactly, so the
    message can go back into the conversation unchanged."""
    said = []

    def emit(d):
        said.append(d)
        if on_text:
            on_text(d)
    return _with_failover(model or MODEL, lambda m: _stream(system, messages, tools, max_tokens, effort, emit, m), said)


def _stream(system, messages, tools, max_tokens, effort, on_text, model):
    if is_openrouter(model):
        return _or_request(system, messages, tools, max_tokens, model, on_text, effort)
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


# ================================================================== other providers, via OpenRouter
# A model named "openrouter:<id>" (e.g. openrouter:deepseek/deepseek-v4.1-flash) is sent to OpenRouter's
# OpenAI-style API. Messages and tools are translated from Anthropic's shape and the reply is translated
# back, so brain.py and tools.py never know the difference. Every request demands zero data retention:
# only hosts that neither store nor train on it are used. Claude-only features (web search as a server
# tool, thinking blocks) are left out or translated: web search becomes OpenRouter's web plugin, whose
# citations come back as web_search_tool_result blocks; Claude models keep their cache marks.
OR_API = "https://openrouter.ai/api/v1/chat/completions"
OR_PREFIX = "openrouter:"


def is_openrouter(model):
    return str(model or "").startswith(OR_PREFIX)


def _or_key():
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def _or_text(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def _or_system(system, model):
    """Claude via OpenRouter still honours cache marks on the system prompt (the bulk of every request)."""
    if isinstance(system, str) or "/claude" not in model:
        return _or_text(system)
    return [{"type": "text", "text": b.get("text", ""), **({"cache_control": b["cache_control"]} if b.get("cache_control") else {})}
            for b in system if b.get("type") == "text"]


def _or_messages(system, messages, model=""):
    out = [{"role": "system", "content": _or_system(system, model)}]
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
            continue
        if m["role"] == "user":
            parts, results = [], []
            for b in c:
                t = b.get("type")
                if t == "text":
                    parts.append({"type": "text", "text": b["text"]})
                elif t == "image" and b.get("source", {}).get("type") == "base64":
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{b['source']['media_type']};base64,{b['source']['data']}"}})
                elif t == "document" and b.get("source", {}).get("type") == "base64":
                    parts.append({"type": "file", "file": {"filename": "attachment.pdf",
                                                            "file_data": f"data:application/pdf;base64,{b['source']['data']}"}})
                elif t == "tool_result":
                    results.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": _or_text(b.get("content")) or "(empty)"})
            out.extend(results)                  # tool results must come straight after the assistant's calls
            if parts:
                out.append({"role": "user", "content": parts})
        else:
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in c if b.get("type") == "tool_use"]
            msg = {"role": "assistant", "content": _or_text(c) or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
    return out


def _or_tools(tools):
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t["input_schema"]}}
            for t in tools or [] if "input_schema" in t]          # server tools (web search) are Claude-only


def _or_web(tools):
    """Anthropic's web_search server tool, as OpenRouter's web plugin."""
    ws = [t for t in tools or [] if t.get("name") == "web_search" and "input_schema" not in t]
    return [{"id": "web", "max_results": max(3, 2 * (ws[0].get("max_uses") or 3))}] if ws else None


def _or_cites(notes, into):
    for a in notes or []:
        c = a.get("url_citation") or {}
        if a.get("type") == "url_citation" and c.get("url") and c["url"] not in [x["url"] for x in into]:
            into.append({"type": "web_search_result", "url": c["url"], "title": c.get("title") or c["url"]})


def _or_blocks(text, calls, finish, usage_, cites=None):
    content = [{"type": "web_search_tool_result", "tool_use_id": "openrouter_web", "content": cites}] if cites else []
    content += [{"type": "text", "text": text}] if text else []
    for c in calls:
        try:
            args = json.loads(c["arguments"] or "{}")
        except json.JSONDecodeError:
            raise LLMError("tool call arrived malformed")
        content.append({"type": "tool_use", "id": c["id"] or f"call_{len(content)}", "name": c["name"], "input": args})
    stop = "tool_use" if calls else "max_tokens" if finish == "length" else "end_turn"
    u = usage_ or {}
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    used = {"input_tokens": max(0, (u.get("prompt_tokens") or 0) - cached), "output_tokens": u.get("completion_tokens") or 0,
            "cache_read_input_tokens": cached, "cache_creation_input_tokens": 0, "cost": u.get("cost")}
    return {"content": content, "stop_reason": stop, "usage": used}


def _or_request(system, messages, tools, max_tokens, model, on_text, effort=None):
    if not _or_key():
        raise LLMError("No OPENROUTER_API_KEY in .env")
    body = {"model": model[len(OR_PREFIX):], "messages": _or_messages(system, messages, model), "max_tokens": max_tokens,
            "usage": {"include": True}, "provider": {"data_collection": "deny", "zdr": True},
            "reasoning": {"effort": effort or EFFORT, "exclude": True}}
    if _or_tools(tools):
        body["tools"] = _or_tools(tools)
    if _or_web(tools):
        body["plugins"] = _or_web(tools)
    if on_text is not None:
        body["stream"] = True
    body_bytes = json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {_or_key()}", "Content-Type": "application/json", "X-Title": "JARVIS"}
    for attempt in range(3):
        try:
            conn, r = _or_post(body_bytes, headers)
        except (http.client.HTTPException, OSError) as e:
            if attempt < 2:
                time.sleep(1.5)
                continue
            raise LLMError(f"OpenRouter unreachable: {e}")
        if r.status == 200:
            break
        raw = r.read()
        _or_give(conn)
        try:
            msg = (json.loads(raw or b"{}").get("error") or {}).get("message") or r.reason
        except Exception:
            msg = r.reason
        if r.status in (429, 500, 502, 503) and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        raise LLMError(f"OpenRouter {r.status}: {msg}")
    try:
        out = _or_read(r, on_text)
        r.read()                                 # drain the rest, so the connection can be reused
        _or_give(conn)
    except Exception:
        conn.close()                             # left half-read: this connection is finished
        raise
    usage.record_llm(model, out["usage"])
    return out


# A TLS handshake to OpenRouter costs a few hundred ms. Connections are kept open and reused (a turn with a
# tool makes two calls back to back), and warm() opens one while Ali is still talking.
_or_pool, _or_pool_lock = [], threading.Lock()
OR_POOL_MAX = 3


def _or_give(conn):
    with _or_pool_lock:
        if len(_or_pool) < OR_POOL_MAX:
            _or_pool.append(conn)
            return
    conn.close()


def _or_new():
    return http.client.HTTPSConnection(urllib.parse.urlparse(OR_API).netloc, timeout=120)


def _or_post(body_bytes, headers):
    """(connection, response) for a POST to OpenRouter, on a pooled connection if one is still open."""
    path = urllib.parse.urlparse(OR_API).path
    with _or_pool_lock:
        conn = _or_pool.pop() if _or_pool else None
    if conn is not None:
        try:
            conn.request("POST", path, body=body_bytes, headers=headers)
            return conn, conn.getresponse()
        except (http.client.HTTPException, OSError):
            conn.close()                         # idle too long and the server closed it: open a fresh one
    conn = _or_new()
    try:
        conn.request("POST", path, body=body_bytes, headers=headers)
        return conn, conn.getresponse()
    except Exception:
        conn.close()
        raise


def warm():
    """Open a connection to OpenRouter ahead of the next call. Cheap; a no-op if one is already open."""
    if not (_or_key() and any(is_openrouter(m) for m in (MODEL, FAST_MODEL, CHEAP_MODEL))):
        return False
    with _or_pool_lock:
        if _or_pool:
            return True
    try:
        conn = _or_new()
        conn.connect()
        _or_give(conn)
        return True
    except OSError:
        return False


def _or_read(r, on_text):
    """The reply, from a full JSON body or an SSE stream, as Anthropic-shaped blocks."""
    if True:
        if on_text is None:
            resp = json.loads(r.read())
            if resp.get("error"):
                raise LLMError(f"OpenRouter: {resp['error'].get('message')}")
            ch = resp["choices"][0]
            msg = ch.get("message") or {}
            calls = [{"id": t.get("id"), "name": t["function"]["name"], "arguments": t["function"].get("arguments")}
                     for t in msg.get("tool_calls") or []]
            cites = []
            _or_cites(msg.get("annotations"), cites)
            out = _or_blocks(msg.get("content") or "", calls, ch.get("finish_reason"), resp.get("usage"), cites)
        else:
            text, calls, finish, usage_, cites = "", {}, None, None, []
            for raw in r:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue                         # blank lines and ": OPENROUTER PROCESSING" keep-alives
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                ev = json.loads(payload)
                if ev.get("error"):
                    raise LLMError(f"OpenRouter: {ev['error'].get('message')}")
                usage_ = ev.get("usage") or usage_
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    _or_cites(d.get("annotations"), cites)
                    if d.get("content"):
                        text += d["content"]
                        on_text(d["content"])
                    for t in d.get("tool_calls") or []:
                        slot = calls.setdefault(t.get("index", 0), {"id": None, "name": "", "arguments": ""})
                        slot["id"] = t.get("id") or slot["id"]
                        fn = t.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
                    finish = ch.get("finish_reason") or finish
            out = _or_blocks(text, [calls[i] for i in sorted(calls)], finish, usage_, cites)
    return out
