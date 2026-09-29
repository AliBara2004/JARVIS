"""ElevenLabs speech out (TTS) and in (Scribe STT).

The browser never sees the key: it posts text to /api/speak and audio to
/api/listen, and this module makes the calls.
"""
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request

import data  # noqa: F401 — loads .env
import localvoice
import usage

API = "https://api.elevenlabs.io/v1"
# Multilingual v2 sounds the most natural; eleven_flash_v2_5 is faster and half the credits but flatter.
TTS_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2")
# Lower stability = more expression (too low wanders); style adds delivery; speaker boost firms up the voice.
VOICE_SETTINGS = {"stability": 0.4, "similarity_boost": 0.8, "style": 0.25, "use_speaker_boost": True}
STT_MODEL = "scribe_v1"
MAX_SPEAK_CHARS = 600              # longer replies are spoken up to here; the screen has the rest
FALLBACK_VOICE = "JBFqnCBsd6RMkjVDRZzb"   # ElevenLabs premade "George" (British), used if the configured ID 404s

_last_error = {"tts": "", "stt": ""}
RETRY_ELEVEN = 30 * 60             # after ElevenLabs fails, use the local pack this long before trying it again
RETRY_QUOTA = 6 * 3600             # ...but out of credits won't fix itself in 30 minutes: don't keep paying the round trip
SEND_TIMEOUT = {"tts": 12, "stt": 20}   # seconds: a slow ElevenLabs is worse than the local voice
_out = {"tts": 0.0, "stt": 0.0}    # when ElevenLabs last failed, per direction


class VoiceError(Exception):
    pass


def _key():
    return os.environ.get("ELEVENLABS_API_KEY", "").strip()


def _voice_id():
    return os.environ.get("ELEVENLABS_VOICE_ID", "").strip()


def _eleven_out(kind):
    wait = RETRY_QUOTA if "credits used up" in _last_error[kind] else RETRY_ELEVEN
    return time.time() - _out[kind] < wait


def status():
    return {"key": bool(_key()), "voice_id": bool(_voice_id()),
            "tts_error": _last_error["tts"], "stt_error": _last_error["stt"],
            "local": localvoice.available(), "out": {k: _eleven_out(k) for k in _out}}


def speak(text, previous_text=""):
    """(audio, mime, engine): ElevenLabs if it's working, else Piper on this PC."""
    local = localvoice.available()["tts"]
    if not (local and _eleven_out("tts")):
        try:
            return tts(text, previous_text), "audio/mpeg", "elevenlabs"
        except VoiceError:
            if not local:
                raise
            _out["tts"] = time.time()
    try:
        audio, mime = localvoice.speech(speakable(text))
        return audio, mime, "local"
    except Exception as e:
        raise VoiceError(f"Local voice failed: {e}")


def listen(audio, mime="audio/webm"):
    """(text, engine): ElevenLabs Scribe if it's working, else whisper on this PC (needs WAV, which the
    page sends whenever the local pack is standing in)."""
    local, wav = localvoice.available()["stt"], mime.startswith("audio/wav")
    if not (local and wav and _eleven_out("stt")):
        try:
            return stt(audio, mime), "elevenlabs"
        except VoiceError:
            if not local:
                raise
            _out["stt"] = time.time()
            if not wav and not localvoice.kokoro_python():
                raise                      # no converter: the page resends this turn as WAV
    try:
        if not wav:
            audio = localvoice.convert(audio, "wav16k")    # e.g. a Telegram voice note (OGG/Opus)
        return localvoice.stt(audio), "local"
    except Exception as e:
        raise VoiceError(f"Local transcription failed: {e}")


# How Ali's world should sound. Every voice (ElevenLabs, Piper, the browser's) reads what speakable() returns.
SAY = [
    (r"\bn8n\b", "n eight n"), (r"\bNQ\b", "N Q"), (r"\bES\b", "E S"), (r"\bDXY\b", "D X Y"), (r"\bVIX\b", "vix"),
    (r"\bCPI\b", "C P I"), (r"\bFOMC\b", "F O M C"), (r"\bNFP\b", "N F P"), (r"\bPPI\b", "P P I"), (r"\bGDP\b", "G D P"),
    (r"\bAPI\b", "A P I"), (r"\bAI\b", "A I"), (r"\bUK\b", "U K"), (r"\bUS\b", "U S"), (r"\bTikTok\b", "Tik Tok"),
    (r"\bJARVIS\b", "Jarvis"), (r"\bPB\b", "P B"), (r"\bPnL\b|\bP&L\b", "P and L"), (r"\bROI\b", "R O I"),
    (r"\be\.g\.", "for example"), (r"\bi\.e\.", "that is"), (r"\betc\.", "and so on"), (r"\bvs\.?\b", "versus"),
    (r"\bkm\b", "kilometres"), (r"\bkg\b", "kilos"), (r"\bmins?\b", "minutes"), (r"\bhrs?\b", "hours"),
    (r"/(month|mo|week|wk|day|hour|hr|year|yr)\b", r" per \1"), (r"\s&\s", " and "), (r"\s+/\s+", " or "),
]


def _money(m):
    sym, whole, pence = m.group(1), m.group(2).replace(",", ""), m.group(3)
    unit, small = ("dollars", "cents") if sym == "$" else ("euros", "cents") if sym == "€" else ("pounds", "pence")
    if whole in ("", "0") and pence:
        return f"{int(pence)} {small}"
    return f"{whole} {unit}" + (f" {int(pence)}" if pence and int(pence) else "")


def _clock(m):
    h, mm = int(m.group(1)), int(m.group(2))
    if h > 23 or mm > 59:
        return m.group(0)
    half = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12} {half}" if mm == 0 else f"{h12} {mm:02d} {half}" if mm < 10 else f"{h12} {mm} {half}"


def speakable(text):
    """What a voice should actually say: no markdown, URLs, emoji or symbols it would trip over, and
    money, times and Ali's jargon written the way they're spoken."""
    t = re.sub(r"https?://\S+", "the link on screen", text or "")
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)                      # [label](url)
    t = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", t)              # [[wikilinks]]
    t = re.sub(r"`?(?:[\w\-]+/)*([\w\-]+(?: [\w\-]+)*)\.md\b`?", r"\1", t)  # `Trading/Risk Rules.md` → Risk Rules
    t = re.sub(r"^\s*([-*•]|\d+[.)])\s+", "", t, flags=re.M)               # list markers
    t = re.sub(r"\s*(->|→|=>)\s*", " to ", t)                                # before ">" is stripped
    t = re.sub(r"[*_`#>|~]+", "", t)
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)       # emoji
    t = re.sub(r"([$£€])\s?(\d{1,3}(?:,\d{3})+|\d+)?(?:\.(\d{1,2}))?", lambda m: _money(m) if (m.group(2) or m.group(3)) else m.group(0), t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", _clock, t)
    t = re.sub(r"(\d)%", r"\1 percent", t)
    for pat, say in SAY:
        t = re.sub(pat, say, t)
    t = re.sub(r"\s*\n+\s*", ". ", t)
    t = re.sub(r"\.\s*\.", ".", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
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
        usage.record_tts(len(text), TTS_MODEL)
        _last_error["tts"] = ""
    except VoiceError as e:
        if "voice not found" not in str(e):
            raise
        audio = _send(request(FALLBACK_VOICE), "tts")
        usage.record_tts(len(text), TTS_MODEL)
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
    ends = [w.get("end") for w in res.get("words") or [] if isinstance(w.get("end"), (int, float))]
    usage.record_stt(max(ends) if ends else 0)       # length of speech heard; Scribe bills per audio time
    _last_error["stt"] = ""
    return (res.get("text") or "").strip()


def _field(boundary, name, value):
    return f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()


def _send(req, kind):
    try:
        with urllib.request.urlopen(req, timeout=SEND_TIMEOUT[kind]) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        msg = _explain(e)
        _last_error[kind] = msg
        raise VoiceError(msg)
    except urllib.error.URLError as e:
        msg = f"Can't reach ElevenLabs: {e.reason}"
        _last_error[kind] = msg
        raise VoiceError(msg)
    except (TimeoutError, OSError) as e:        # a stall or a dropped connection: fall back, don't drop the request
        msg = f"ElevenLabs didn't answer: {e}"
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
