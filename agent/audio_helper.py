"""Audio conversions for the local voice pack. Runs inside local/kokoro/venv (needs numpy + soundfile, which
JARVIS itself never imports), one conversion per run, audio in on stdin and out on stdout.

    python audio_helper.py wav16k     any audio (OGG/Opus voice note, MP3, WAV…) → 16 kHz mono WAV for whisper
    python audio_helper.py opus       WAV → OGG/Opus, what Telegram plays as a voice note
"""
import io
import sys

import numpy as np
import soundfile as sf

mode = sys.argv[1] if len(sys.argv) > 1 else ""
raw = sys.stdin.buffer.read()
data, rate = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
mono = data.mean(axis=1)
out = io.BytesIO()
if mode == "wav16k":
    if rate != 16000:                                    # linear resample: fine for speech recognition
        n = int(round(len(mono) * 16000 / rate))
        mono = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono).astype("float32")
    sf.write(out, mono, 16000, format="WAV", subtype="PCM_16")
elif mode == "opus":
    if rate not in (8000, 12000, 16000, 24000, 48000):   # Opus's own rates
        n = int(round(len(mono) * 48000 / rate))
        mono = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono).astype("float32")
        rate = 48000
    sf.write(out, mono, rate, format="OGG", subtype="OPUS")
else:
    sys.exit("mode must be wav16k or opus")
sys.stdout.buffer.write(out.getvalue())
