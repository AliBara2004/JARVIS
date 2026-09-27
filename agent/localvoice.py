"""The local voice pack: Kokoro (or Piper) to speak and whisper.cpp to listen, all running on this PC.

Free, offline and private. JARVIS uses them when ElevenLabs is out of credits or failing (voice.py
decides), and the browser's own voice/recognition only if these aren't installed.

    python agent/localvoice.py setup      download the programs and models into local/ (about 550 MB)
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
import struct
import subprocess
import sys
import tempfile
import threading
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
WHISPER_PORT = 7779                # whisper-server on localhost only, keeping the model loaded between turns
THREADS = str(os.cpu_count() or 4)
KOKORO_DIR = LOCAL / "kokoro"
KOKORO_VOICE = os.environ.get("JARVIS_KOKORO_VOICE", "bm_lewis")     # Ali's pick; bm_george, bm_daniel also British
KOKORO_SPEED = float(os.environ.get("JARVIS_KOKORO_SPEED", "1.12"))
KOKORO_FILES = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_PACKAGE = "kokoro-onnx==0.6.1"

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


def kokoro_python():
    p = KOKORO_DIR / "venv" / "Scripts" / "python.exe"
    return p if p.exists() and (KOKORO_DIR / "kokoro-v1.0.onnx").exists() and (KOKORO_DIR / "voices-v1.0.bin").exists() else None


def available():
    kokoro, piper = bool(kokoro_python()), bool(piper_exe() and voice_model())
    return {"tts": kokoro or piper, "stt": bool(whisper_exe() and whisper_model()),
            "voice": f"Kokoro {KOKORO_VOICE.split('_')[-1].title()}" if kokoro else VOICE if piper else None}


class _Helper:
    """Kokoro kept loaded in its own process (agent/kokoro_helper.py, run by the venv's Python)."""
    def __init__(self):
        self.proc, self.lock = None, threading.Lock()

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.proc.stdout.read(n - len(buf))
            if not chunk:
                raise RuntimeError("Kokoro helper stopped")
            buf += chunk
        return buf

    def _start(self):
        helper = data.ROOT / "agent" / "kokoro_helper.py"
        self.proc = subprocess.Popen([str(kokoro_python()), str(helper), str(KOKORO_DIR)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._read(4)                      # "ready": the model has loaded

    def say(self, text):
        with self.lock:
            for attempt in (1, 2):         # a crashed helper is restarted once
                try:
                    if not self.proc or self.proc.poll() is not None:
                        self._start()
                    req = json.dumps({"text": text, "voice": KOKORO_VOICE, "speed": KOKORO_SPEED}) + "\n"
                    self.proc.stdin.write(req.encode("utf-8"))
                    self.proc.stdin.flush()
                    (n,) = struct.unpack(">I", self._read(4))
                    if n:
                        return self._read(n)
                    raise RuntimeError("Kokoro couldn't say that")
                except (OSError, RuntimeError, ValueError):
                    if self.proc:
                        self.proc.kill()
                    self.proc = None
                    if attempt == 2:
                        raise


_kokoro = _Helper()


def warm():
    """Load Kokoro and whisper in the background at start-up, so the first turn is quick."""
    try:
        _whisper.ensure()
    except Exception:
        pass
    if kokoro_python():
        try:
            _kokoro.say("Ready.")
        except Exception:
            pass


# ------------------------------------------------------------------ speaking (Piper)
def tts(text):
    """Speech for `text` as WAV bytes: Kokoro if installed, else Piper. Text should already be speakable()."""
    if kokoro_python():
        try:
            return _kokoro.say(" ".join(text.split()))
        except Exception:
            if not (piper_exe() and voice_model()):
                raise
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
class _Whisper:
    """whisper-server kept running (model loaded), so a turn costs only the transcription."""
    def __init__(self):
        self.proc, self.lock = None, threading.Lock()

    def _up(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{WHISPER_PORT}/", timeout=1):
                return True
        except Exception:
            return False

    def _exe(self):
        exe = whisper_exe()
        server = exe.parent / "whisper-server.exe" if exe else None
        return server if server and server.exists() else None

    def ensure(self):
        with self.lock:
            if self._up():                 # already running (possibly started by an earlier JARVIS): reuse it
                return True
            server, model = self._exe(), whisper_model()
            if not (server and model):
                return False
            self.proc = subprocess.Popen([str(server), "-m", str(model), "--host", "127.0.0.1", "--port", str(WHISPER_PORT),
                                          "-t", THREADS, "-l", "en", "-nt"], cwd=str(server.parent),
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for _ in range(40):            # up to 8 s for the model to load
                if self._up():
                    return True
                if self.proc.poll() is not None:
                    return False
                threading.Event().wait(0.2)
            return False

    def transcribe(self, wav_bytes):
        boundary = "----jarvis" + os.urandom(8).hex()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"turn.wav\"\r\n"
                f"Content-Type: audio/wav\r\n\r\n").encode() + wav_bytes + \
               (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\ntext\r\n"
                f"--{boundary}--\r\n").encode()
        req = urllib.request.Request(f"http://127.0.0.1:{WHISPER_PORT}/inference", data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8", errors="ignore")


_whisper = _Whisper()


def _clean(text):
    text = " ".join(text.split())
    return "" if text in ("[BLANK_AUDIO]", "(silence)") else text.replace("[BLANK_AUDIO]", "").strip()


def stt(wav_bytes):
    """Text from a 16 kHz mono WAV: the loaded whisper-server if it's up, else a one-shot whisper run."""
    try:
        if _whisper.ensure():
            return _clean(_whisper.transcribe(wav_bytes))
    except Exception:
        pass                               # fall through to the one-shot CLI
    return _stt_cli(wav_bytes)


def _stt_cli(wav_bytes):
    """The audio goes to a temp file only for whisper to read, then it's deleted."""
    exe, model = whisper_exe(), whisper_model()
    if not (exe and model):
        raise RuntimeError("Local transcription isn't installed. Run: python agent/localvoice.py setup")
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="jarvis-turn-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wav_bytes)
        r = subprocess.run([str(exe), "-m", str(model), "-f", path, "-l", "en", "-nt", "-np", "-t", THREADS],
                           capture_output=True, timeout=120, cwd=str(exe.parent),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if r.returncode != 0:
        raise RuntimeError(f"whisper failed: {r.stderr.decode(errors='ignore')[-300:]}")
    return _clean(r.stdout.decode("utf-8", errors="ignore"))


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
    for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        p = _download(f"{KOKORO_FILES}/{name}", KOKORO_DIR / name)
        sums.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  kokoro/{name}  {KOKORO_FILES}/{name}")
    venv = KOKORO_DIR / "venv"
    if not (venv / "Scripts" / "python.exe").exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    # truststore: use Windows' certificates (Norton inspects HTTPS with its own, which pip otherwise rejects)
    subprocess.run([str(venv / "Scripts" / "python.exe"), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                    "--use-feature=truststore", KOKORO_PACKAGE], check=True)
    sums.append(f"pip  {KOKORO_PACKAGE}  (into local/kokoro/venv)")
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
