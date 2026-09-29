"""Kokoro voice helper. Runs inside local/kokoro/venv (it needs numpy + kokoro-onnx, which JARVIS itself
never imports), started and fed by localvoice.py.

Protocol on stdin/stdout, one request at a time:
    in:  one JSON line  {"text": "...", "voice": "bm_lewis", "speed": 1.05, "format": "wav" | "ogg"}
    out: 4-byte big-endian length, then that many bytes of WAV or OGG/Opus (length 0 = failed; reason on stderr)
The model stays loaded between requests, so each sentence costs only the synthesis.
"""
import io
import json
import struct
import sys
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

HERE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "local" / "kokoro"
kokoro = Kokoro(str(HERE / "kokoro-v1.0.onnx"), str(HERE / "voices-v1.0.bin"))
out = sys.stdout.buffer


TAIL_KEEP = 0.12            # seconds of silence kept after the last sound: a natural gap, not a wait
SILENT = 0.004              # below this (about -48 dB) counts as silence
_rng = np.random.default_rng()


def wav(samples, rate, fmt="wav"):
    samples = np.asarray(samples, dtype=np.float32)
    loud = np.flatnonzero(np.abs(samples) > SILENT)
    if loud.size:                           # trim the trailing silence: the next sentence starts sooner
        samples = samples[:min(len(samples), loud[-1] + int(TAIL_KEEP * rate))]
    # Inaudible noise (about -80 dB). Pure digital silence is long runs of zero bytes, and something on this
    # PC's network path (Norton's scanner, by every test) stalls local replies that end in them for ~20 s.
    samples = samples + _rng.normal(0, 1e-4, samples.shape).astype(np.float32)
    if fmt == "ogg":                        # Opus: ~10x smaller, and compressed audio sails past that scanner
        buf = io.BytesIO()
        sf.write(buf, np.clip(samples, -1, 1), rate, format="OGG", subtype="OPUS")
        return buf.getvalue()
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


out.write(struct.pack(">I", 0))          # ready signal: the model is loaded
out.flush()
for line in sys.stdin:
    try:
        req = json.loads(line)
        samples, rate = kokoro.create(req["text"], voice=req.get("voice", "bm_lewis"),
                                      speed=float(req.get("speed", 1.05)), lang="en-gb")
        data = wav(samples, rate, req.get("format", "wav"))
    except Exception as e:                  # never die on one bad sentence
        print(f"kokoro: {e}", file=sys.stderr, flush=True)
        data = b""
    out.write(struct.pack(">I", len(data)) + data)
    out.flush()
