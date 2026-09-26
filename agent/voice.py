"""ElevenLabs speech out (TTS) and in (Scribe STT).

The browser never sees the key: it posts text to /api/speak and audio to
/api/listen, and this module makes the calls.
"""
import json
import os
import re
import secrets
import urllib.error
import urllib.request

import data  # noqa: F401 — loads .env

API = "https://api.elevenlabs.io/v1"
# Multilingual v2 sounds the most natural; eleven_flash_v2_5 is faster and half the credits but flatter.
TTS_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2")
# Lower stability = more expression (too low wanders); style adds delivery; speaker boost firms up the voice.
VOICE_SETTINGS = {"stability": 0.4, "similarity_boost": 0.8, "style": 0.25, "use_speaker_boost": True}
STT_MODEL = "scribe_v1"
MAX_SPEAK_CHARS = 1200             # longer replies are spoken up to here; the screen has the rest
FALLBACK_VOICE = "JBFqnCBsd6RMkjVDRZzb"   # ElevenLabs premade "George" (British), used if the configured ID 404s

_last_error = {"tts": "", "stt": ""}


class VoiceError(Exception):
    pass


def _key():
    return os.environ.get("ELEVENLABS_API_KEY", "").strip()


def _voice_id():
    return os.environ.get("ELEVENLABS_VOICE_ID", "").strip()


def status():
    return {"key": bool(_key()), "voice_id": bool(_voice_id()),
            "tts_error": _last_error["tts"], "stt_error": _last_error["stt"]}


def speakable(text):
    """Strip what shouldn't be read aloud: markdown, URLs, stray symbols."""
    t = re.sub(r"https?://\S+", "", text or "")
    t = re.sub(r"[*_`#>|]+", "", t)
    t = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", t)
    t = re.sub(r"\s*\n+\s*", " ", t).strip()
    if len(t) > MAX_SPEAK_CHARS:
        cut = t[:MAX_SPEAK_CHARS].rsplit(". ", 1)[0]
        t = cut + ". The rest is on screen."
    return t


def tts(text, previous_text=""):
    if not _key() or not _voice_id():
        raise VoiceError("ElevenLabs key or voice ID missing in .env")
    text = speakable(text)
    if not text:
        raise VoiceError("Nothing to say")
    req_body = {"text": text, "model_id": TTS_MODEL, "voice_settings": VOICE_SETTINGS}
    if previous_text.strip():
        # What was just said, so a reply spoken sentence-by-sentence still flows as one delivery.
        req_body["previous_text"] = speakable(previous_text)[-500:]
    body = json.dumps(req_body).encode()
    def request(vid):
        return urllib.request.Request(f"{API}/text-to-speech/{vid}?output_format=mp3_44100_128",
                                      data=body, method="POST",
                                      headers={"xi-api-key": _key(), "Content-Type": "application/json",
                                               "Accept": "audio/mpeg"})
    try:
        audio = _send(request(_voice_id()), "tts")
        _last_error["tts"] = ""
    except VoiceError as e:
        if "voice not found" not in str(e):
            raise
        audio = _send(request(FALLBACK_VOICE), "tts")
        _last_error["tts"] = "Your ELEVENLABS_VOICE_ID wasn't found, so I'm using the built-in George voice. Fix the ID in .env."
    return audio


def stt(audio, mime="audio/webm"):
    if not _key():
        raise VoiceError("ElevenLabs key missing in .env")
    if len(audio) < 1200:
        raise VoiceError("Recording too short")
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3",
           "audio/wav": "wav"}.get(mime.split(";")[0].strip(), "webm")
    boundary = "----jarvis" + secrets.token_hex(8)
    parts = [
        _field(boundary, "model_id", STT_MODEL),
        _field(boundary, "tag_audio_events", "false"),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"turn.{ext}\"\r\n"
         f"Content-Type: {mime.split(';')[0]}\r\n\r\n").encode() + audio + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(f"{API}/speech-to-text", data=b"".join(parts), method="POST",
                                 headers={"xi-api-key": _key(),
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    res = json.loads(_send(req, "stt"))
    _last_error["stt"] = ""
    return (res.get("text") or "").strip()


def _field(boundary, name, value):
    return f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()


def _send(req, kind):
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        msg = _explain(e)
        _last_error[kind] = msg
        raise VoiceError(msg)
    except urllib.error.URLError as e:
        msg = f"Can't reach ElevenLabs: {e.reason}"
        _last_error[kind] = msg
        raise VoiceError(msg)


def _explain(e):
    try:
        d = json.loads(e.read() or b"{}").get("detail")
    except Exception:
        d = None
    msg = d.get("message") if isinstance(d, dict) else d if isinstance(d, str) else e.reason
    if e.code == 401 and msg and "permission" in msg.lower():
        return f"ElevenLabs key lacks a permission: {msg} Edit the key's permissions on elevenlabs.io."
    if e.code == 404:
        return "ElevenLabs voice not found. Check ELEVENLABS_VOICE_ID (20 characters, from My Voices → Copy voice ID)."
    if e.code == 401 and "quota" in str(msg).lower():
        return "ElevenLabs credits used up for this month."
    return f"ElevenLabs {e.code}: {msg}"
