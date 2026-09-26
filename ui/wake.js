// "Hey Jarvis" wake word, detected entirely in the browser.
// openWakeWord pipeline: 16 kHz audio → melspectrogram model → embedding model
// (76 mel frames → 96 numbers, one per 80 ms) → hey_jarvis model (last 16 embeddings → score).
// Runs on onnxruntime-web from /vendor. No audio leaves the machine.
const Wake = (() => {
  const THRESHOLD = 0.5;          // score (0-1) that counts as "hey Jarvis"; raise if it fires by accident
  const COOLDOWN_MS = 2000;       // ignore repeat detections this soon after one
  const RATE = 16000;
  const CHUNK = 1280;             // 80 ms at 16 kHz: the model's step
  const WARMUP_STEPS = 5;         // openWakeWord ignores its first few predictions

  let mel, emb, kw, loading = null, ready = false;
  let inRate = 48000, carry = new Float32Array(0), phase = 0;
  let pending = [], pendingLen = 0, tail = new Float32Array(480);
  let melFrames = [], feats = [], steps = 0, lastFire = 0, busy = false;
  let onWake = () => {}, lastScore = 0;

  async function load() {
    if (ready) return;
    if (loading) return loading;
    loading = (async () => {
      if (!window.ort) throw new Error('wake-word runtime missing (ui/vendor/ort.wasm.min.js)');
      ort.env.wasm.wasmPaths = '/vendor/';
      ort.env.wasm.numThreads = 1;          // no cross-origin isolation needed
      const opt = { executionProviders: ['wasm'] };
      [mel, emb, kw] = await Promise.all([
        ort.InferenceSession.create('/vendor/melspectrogram.onnx', opt),
        ort.InferenceSession.create('/vendor/embedding_model.onnx', opt),
        ort.InferenceSession.create('/vendor/hey_jarvis_v0.1.onnx', opt),
      ]);
      ready = true;
    })();
    try { await loading; } finally { loading = null; }
  }

  function reset() {
    carry = new Float32Array(0); phase = 0; pending = []; pendingLen = 0;
    tail = new Float32Array(480); melFrames = []; feats = []; steps = 0;
  }

  // Feed mic samples at the AudioContext's rate; resampled to 16 kHz by block averaging.
  function feed(samples, rate) {
    if (!ready) return;
    inRate = rate;
    const buf = new Float32Array(carry.length + samples.length);
    buf.set(carry); buf.set(samples, carry.length);
    const r = inRate / RATE, out = [];
    let i = phase;
    while (i + r <= buf.length) {
      const a = Math.floor(i), b = Math.floor(i + r);
      let s = 0;
      for (let k = a; k < b; k++) s += buf[k];
      out.push(b > a ? s / (b - a) : buf[a]);
      i += r;
    }
    carry = buf.slice(Math.floor(i));
    phase = i - Math.floor(i);
    pending.push(Float32Array.from(out));
    pendingLen += out.length;
    if (!busy) pump();
  }

  async function pump() {
    busy = true;
    try {
      while (pendingLen >= CHUNK) {
        const chunk = take(CHUNK);
        await step(chunk);
      }
    } catch (e) {
      console.error('[wake]', e);
    } finally { busy = false; }
  }

  function take(n) {
    const out = new Float32Array(n);
    let o = 0;
    while (o < n) {
      const head = pending[0], k = Math.min(head.length, n - o);
      out.set(head.subarray(0, k), o);
      o += k;
      if (k === head.length) pending.shift(); else pending[0] = head.subarray(k);
    }
    pendingLen -= n;
    return out;
  }

  async function step(chunk) {
    // melspectrogram over this chunk plus 480 samples of history, in int16 scale
    const x = new Float32Array(480 + CHUNK);
    x.set(tail); x.set(chunk, 480);
    tail = chunk.slice(CHUNK - 480);
    for (let i = 0; i < x.length; i++) x[i] = Math.max(-1, Math.min(1, x[i])) * 32767;
    const mOut = (await mel.run({ [mel.inputNames[0]]: new ort.Tensor('float32', x, [1, x.length]) }))[mel.outputNames[0]];
    const nFrames = mOut.dims[mOut.dims.length - 2];
    for (let f = 0; f < nFrames; f++) {
      const fr = new Float32Array(32);
      for (let j = 0; j < 32; j++) fr[j] = mOut.data[f * 32 + j] / 10 + 2;
      melFrames.push(fr);
    }
    if (melFrames.length > 120) melFrames.splice(0, melFrames.length - 120);
    if (melFrames.length < 76) return;

    const win = new Float32Array(76 * 32);
    melFrames.slice(-76).forEach((fr, i) => win.set(fr, i * 32));
    const eOut = (await emb.run({ [emb.inputNames[0]]: new ort.Tensor('float32', win, [1, 76, 32, 1]) }))[emb.outputNames[0]];
    feats.push(Float32Array.from(eOut.data.slice(0, 96)));
    if (feats.length > 16) feats.shift();
    if (feats.length < 16) return;

    const f16 = new Float32Array(16 * 96);
    feats.forEach((v, i) => f16.set(v, i * 96));
    const kOut = (await kw.run({ [kw.inputNames[0]]: new ort.Tensor('float32', f16, [1, 16, 96]) }))[kw.outputNames[0]];
    const score = kOut.data[0];
    lastScore = score;
    steps++;
    const now = performance.now();
    if (steps > WARMUP_STEPS && score >= THRESHOLD && now - lastFire > COOLDOWN_MS) {
      lastFire = now;
      onWake(score);
    }
  }

  return {
    load, reset, feed,
    set onWake(fn) { onWake = fn; },
    get ready() { return ready; },
    get lastScore() { return lastScore; },
  };
})();
