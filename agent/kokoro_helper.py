"""Kokoro voice helper. Runs inside local/kokoro/venv (it needs numpy + kokoro-onnx, which JARVIS itself
never imports), started and fed by localvoice.py.

Protocol on stdin/stdout, one request at a time:
    in:  one JSON line  {"text": "...", "voice": "bm_lewis", "speed": 1.05}
    out: 4-byte big-endian length, then that many bytes of WAV (length 0 = failed; the reason is on stderr)
The model stays loaded between requests, so each sentence costs only the synthesis.
"""
import io
import json
import struct
import sys
import wave
from pathlib import Path

import numpy as np
from kokoro_onnx import Kokoro

HERE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "local" / "kokoro"
kokoro = Kokoro(str(HERE / "kokoro-v1.0.onnx"), str(HERE / "voices-v1.0.bin"))
out = sys.stdout.buffer


def wav(samples, rate):
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
        data = wav(samples, rate)
    except Exception as e:                  # never die on one bad sentence
        print(f"kokoro: {e}", file=sys.stderr, flush=True)
        data = b""
    out.write(struct.pack(">I", len(data)) + data)
    out.flush()
