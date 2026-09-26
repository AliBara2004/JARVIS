"""Files Ali shows JARVIS: screenshots, photos and PDFs, from the PC page or Telegram.

Held in memory only (never written to disk) until the question that uses them is answered,
then dropped: the conversation keeps JARVIS's reply about the file, not the file, so it isn't
re-sent (and re-billed) on every later turn.
"""
import base64
import secrets
import threading
import time

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
PDF = "application/pdf"
MAX_IMAGE = 5 * 1024 * 1024          # Claude's per-image limit
MAX_PDF = 20 * 1024 * 1024
KEEP_SECONDS = 30 * 60               # an attachment never used is dropped after this

_store = {}
_lock = threading.Lock()


class AttachError(Exception):
    pass


def sniff(raw, declared=""):
    """Trust the bytes, not the label."""
    if raw.startswith(b"%PDF"):
        return PDF
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    raise AttachError(f"I can read images (PNG, JPEG, GIF, WebP) and PDFs, not {declared or 'that file type'}.")


def add(raw, name="file", declared=""):
    kind = sniff(raw, declared)
    limit = MAX_PDF if kind == PDF else MAX_IMAGE
    if len(raw) > limit:
        raise AttachError(f"That {'PDF' if kind == PDF else 'image'} is over {limit // (1024 * 1024)} MB.")
    aid = "a_" + secrets.token_hex(6)
    with _lock:
        now = time.time()
        for k in [k for k, v in _store.items() if now - v["t"] > KEEP_SECONDS]:
            _store.pop(k)
        _store[aid] = {"raw": raw, "kind": kind, "name": name[:80], "t": now}
    return {"id": aid, "kind": "pdf" if kind == PDF else "image", "name": name[:80], "bytes": len(raw)}


def take(ids):
    """Content blocks for these attachments, removed from the store (each is used once)."""
    blocks, names = [], []
    with _lock:
        for aid in ids or []:
            a = _store.pop(aid, None)
            if not a:
                continue
            b64 = base64.b64encode(a["raw"]).decode()
            if a["kind"] == PDF:
                blocks.append({"type": "document", "source": {"type": "base64", "media_type": PDF, "data": b64},
                               "title": a["name"]})
            else:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": a["kind"], "data": b64}})
            names.append(a["name"])
    return blocks, names
