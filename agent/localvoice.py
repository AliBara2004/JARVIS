"""The local voice pack: Piper to speak and whisper.cpp to listen, both running on this PC.

Free, offline and private. JARVIS uses them when ElevenLabs is out of credits or failing (voice.py
decides), and the browser's own voice/recognition only if these aren't installed.

    python agent/localvoice.py setup      download the programs and models into local/ (about 150 MB)
    python agent/localvoice.py test       speak a line to local/test.wav and transcribe it back

Everything lives in local/ (gitignored). Downloads come from the projects' official releases and are
checked against the sizes those releases publish; the SHA-256 of each file is printed and saved to
local/SOURCES.txt.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import wave
import zipfile
from pathlib import Path

import data  # noqa: F401 — loads .env

LOCAL = data.ROOT / "local"
PIPER_DIR = LOCAL / "piper"
WHISPER_DIR = LOCAL / "whisper"
VOICE = os.environ.get("JARVIS_PIPER_VOICE", "en_GB-alan-medium")
WHISPER_MODEL = os.environ.get("JARVIS_WHISPER_MODEL", "ggml-base.en-q5_1.bin")

HF_VOICES = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB"
DOWNLOADS = [   # (url, saved as, expected size or None)
    ("https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip", "piper.zip", 22477236),
    ("https://github.com/ggml-org/whisper.cpp/releases/download/v1.9.2/whisper-bin-x64.zip", "whisper.zip", 8194445),
    (f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{WHISPER_MODEL}", f"whisper/{WHISPER_MODEL}", None),
]


def _voice_urls(name):
    lang, speaker, quality = name.split("-", 2)
    base = f"{HF_VOICES}/{speaker}/{quality}/{name}.onnx"
    return [(base, f"piper/{name}.onnx", None), (base + ".json", f"piper/{name}.onnx.json", None)]


# ------------------------------------------------------------------ where things are
def _find(root, name):
    hits = sorted(Path(root).rglob(name)) if Path(root).exists() else []
    return hits[0] if hits else None


def piper_exe():
    return _find(PIPER_DIR, "piper.exe")


def whisper_exe():
    return _find(WHISPER_DIR, "whisper-cli.exe") or _find(WHISPER_DIR, "main.exe")


def voice_model():
    p = PIPER_DIR / f"{VOICE}.onnx"
    return p if p.exists() and (PIPER_DIR / f"{VOICE}.onnx.json").exists() else None


def whisper_model():
    p = WHISPER_DIR / WHISPER_MODEL
    return p if p.exists() else None


def available():
    return {"tts": bool(piper_exe() and voice_model()), "stt": bool(whisper_exe() and whisper_model()),
            "voice": VOICE if voice_model() else None}


# ------------------------------------------------------------------ speaking (Piper)
def tts(text):
    """Speech for `text` as WAV bytes. Text should already be speakable()."""
    exe, model = piper_exe(), voice_model()
    if not (exe and model):
        raise RuntimeError("Local voice isn't installed. Run: python agent/localvoice.py setup")
    rate = json.loads((PIPER_DIR / f"{VOICE}.onnx.json").read_text(encoding="utf-8")).get("audio", {}).get("sample_rate", 22050)
    line = " ".join(text.split())
    r = subprocess.run([str(exe), "--model", str(model), "--output_raw", "--sentence_silence", "0.15"],
                       input=(line + "\n").encode("utf-8"), capture_output=True, timeout=60, cwd=str(exe.parent),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"Piper failed: {r.stderr.decode(errors='ignore')[-300:]}")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(r.stdout)
    return buf.getvalue()


# ------------------------------------------------------------------ listening (whisper.cpp)
def stt(wav_bytes):
    """Text from a 16 kHz mono WAV. The audio goes to a temp file only for whisper to read, then it's deleted."""
    exe, model = whisper_exe(), whisper_model()
    if not (exe and model):
        raise RuntimeError("Local transcription isn't installed. Run: python agent/localvoice.py setup")
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="jarvis-turn-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wav_bytes)
        threads = str(max(1, min(4, (os.cpu_count() or 2) - 1)))
        r = subprocess.run([str(exe), "-m", str(model), "-f", path, "-l", "en", "-nt", "-np", "-t", threads],
                           capture_output=True, timeout=120, cwd=str(exe.parent),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if r.returncode != 0:
        raise RuntimeError(f"whisper failed: {r.stderr.decode(errors='ignore')[-300:]}")
    text = " ".join(r.stdout.decode("utf-8", errors="ignore").split())
    return "" if text in ("[BLANK_AUDIO]", "(silence)") else text.replace("[BLANK_AUDIO]", "").strip()


# ------------------------------------------------------------------ setup
def _download(url, dest, size=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (size is None or dest.stat().st_size == size):
        print(f"  have     {dest.relative_to(LOCAL)}")
        return dest
    print(f"  fetching {dest.relative_to(LOCAL)}  <- {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    if size is not None and tmp.stat().st_size != size:
        tmp.unlink()
        raise RuntimeError(f"{dest.name}: expected {size} bytes, got something else. Not installed.")
    os.replace(tmp, dest)
    return dest


def setup():
    LOCAL.mkdir(exist_ok=True)
    sums = []
    for url, name, size in DOWNLOADS + _voice_urls(VOICE):
        p = _download(url, LOCAL / name, size)
        sums.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}  {url}")
    for zname, target in (("piper.zip", PIPER_DIR), ("whisper.zip", WHISPER_DIR)):
        with zipfile.ZipFile(LOCAL / zname) as z:
            for m in z.namelist():                                   # refuse paths that climb out of local/
                if (target / m).resolve() != target.resolve() and target.resolve() not in (target / m).resolve().parents:
                    raise RuntimeError(f"{zname} contains an unsafe path: {m}")
            z.extractall(target)
    (LOCAL / "SOURCES.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print("\n".join("  " + s for s in sums))
    print("installed:", available())


def test():
    wav = tts("Good evening, Ali. This is the local voice, running on your own machine.")
    (LOCAL / "test.wav").write_bytes(wav)
    with wave.open(io.BytesIO(wav)) as w:
        frames, rate = w.readframes(w.getnframes()), w.getframerate()
    # whisper wants 16 kHz: resample the test clip crudely (nearest sample) for the round trip
    import array
    a = array.array("h", frames)
    step = rate / 16000
    b = array.array("h", (a[int(i * step)] for i in range(int(len(a) / step))))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b.tobytes())
    print("spoke    local/test.wav", f"({len(wav) // 1024} KB)")
    print("heard   ", stt(buf.getvalue()))


if __name__ == "__main__":
    {"setup": setup, "test": test}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
