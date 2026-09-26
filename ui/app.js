// JARVIS UI glue: panels, inspector, filters, conversation.
const EXAMPLES = [
  'Brief me',
  'Who emailed me that I already know?',
  'Which niche should I go after first?',
  'What did I write about revenge trades?',
  'Plan my day',
  'Draft a reply to Ben Carter',
  'Book a call with Ben Carter Tuesday at 11',
  'Any red folders today?',
];
const EXAMPLE_EVERY = 4000;
const CONVO_KEEP = 8;            // exchanges kept on screen
const STATUS_EVERY_MS = 15000;   // how often to pick up model/voice status and vault changes

// ---- voice tuning -----------------------------------------------------------
const SILENCE_MS = 900;          // quiet this long after you've spoken ends your turn
const SPEECH_LEVEL = 0.03;       // mic RMS (0-1) that counts as speech; raise it in a noisy room
const MIN_SPEECH_MS = 250;       // sounds shorter than this are blips, not speech
const MAX_TURN_MS = 120000;      // one turn can run two minutes: long thoughts are fine
const IDLE_RESTART_MS = 15000;   // throw away silent recordings older than this and start fresh
const LEVEL_EVERY_MS = 50;       // setInterval, not requestAnimationFrame: keeps listening in a background tab
const ECHO_GRACE_MS = 350;       // stay deaf this long after JARVIS stops talking
const DEAD_MIC_MS = 4000;        // a mic reporting pure silence this long is muted or the wrong device
const MAX_SPOKEN_CHARS = 1200;   // a reply longer than this is spoken up to here; the screen has the rest
const PLAY_START_TIMEOUT_MS = 5000;  // reply audio that hasn't started by now is abandoned (text stays on screen)
const CONVO_IDLE_MS = 20000;     // with "hey Jarvis" armed: this long without you speaking → back to standby

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let GRAPH = null;
let STATUS = null;
let busy = false;
const hiddenTypes = new Set();

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

async function post(path, body) {
  const r = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Jarvis': '1' }, body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

function banner(msg) {
  const b = $('#banner');
  b.textContent = msg || ''; b.hidden = !msg;
}

// ---------------------------------------------------------------- boot
async function boot() {
  try {
    [STATUS, GRAPH] = await Promise.all([api('/api/status'), api('/api/graph')]);
  } catch (e) {
    banner(`Can't reach the JARVIS server (${e.message}). Is python agent/main.py running?`);
    return;
  }
  if (!GRAPH.nodes.length) {
    banner(STATUS.mode === 'live'
      ? 'No notes found. Check JARVIS_FOLDERS in .env.'
      : 'Demo vault is empty. Run: python data/generate_demo.py');
  }

  renderStatus(STATUS);
  renderStats();
  renderHubs();
  renderFilters();
  measureInsets();
  Graph.init($('#graph'), GRAPH, { onFocus: openNote, onPath: showPath });
  window.addEventListener('resize', measureInsets);
  bindUI();
  drawTicks();
  // The model check runs in the background on the server; pick up its verdict.
  setTimeout(refreshStatus, 2500);
  window.addEventListener('focus', refreshStatus);
  setInterval(refreshStatus, STATUS_EVERY_MS);
}

async function refreshStatus() {
  try { STATUS = await api('/api/status'); renderStatus(STATUS); } catch { return; /* banner shows on next action */ }
  // The server re-indexes when notes change in Obsidian; redraw if it has.
  if (GRAPH && STATUS.graph_version !== GRAPH.version && !busy) reloadGraph();
}

// Tell the graph which parts of the screen the panels cover, so it centres in the free space.
function measureInsets() {
  const vis = el => el && getComputedStyle(el).display !== 'none';
  const L = $('.left'), R = $('.right'), A = $('.askbar');
  const wide = innerWidth > 820;
  Graph.setInsets({
    l: wide && vis(L) ? L.getBoundingClientRect().right : 0,
    r: vis(R) ? innerWidth - R.getBoundingClientRect().left : 0,
    t: 0,
    b: innerHeight - A.getBoundingClientRect().top,
  });
}

function renderStatus(s) {
  const m = $('#mode');
  m.textContent = s.mode === 'demo' ? 'DEMO DATA' : 'LIVE DATA';
  m.className = 'badge ' + s.mode;

  const model = {
    ready: ['ok', 'Model', `${s.model.model} reachable`],
    unchecked: ['', 'Model', 'Checking…'],
    missing: ['warn', 'Model offline', 'No Anthropic key. Keyword routing only.'],
    error: ['warn', 'Model offline', `${s.model.detail}. Keyword routing only.`],
  }[s.model.state] || ['warn', 'Model', s.model.detail];

  const g = s.google;
  const google = !g.configured ? ['warn', 'Google', 'No Google client in .env']
    : g.connected ? ['ok', 'Google', 'Connected. Click to disconnect.']
    : ['warn action', 'Google · connect', g.demo ? 'Demo mode uses fixtures; connect now for live mode later.' : 'Click to sign in'];

  const chip = ([cls, label, title], id = '') =>
    `<span class="chip ${cls}" ${id ? `id="${id}"` : ''} title="${esc(title)}">${esc(label)}</span>`;
  $('#chips').innerHTML =
    chip(model) +
    chip(!s.voice.key ? ['warn', 'Voice off', 'No ElevenLabs key in .env']
      : s.voice.tts_error || s.voice.stt_error ? ['warn', 'Voice', s.voice.tts_error || s.voice.stt_error]
      : ['ok', 'Voice', 'ElevenLabs speech in and out. Press Mic or Space.']) +
    chip(google, 'google-chip');
  $('#model-badge').hidden = s.model.state === 'ready' || s.model.state === 'unchecked';
  const tips = { '#mic': 'Talk (Space). Esc to stop.', '#mute': 'Keep listening, stop speaking',
                 '#wake': 'Standby: say "Hey Jarvis" to start talking. Detected on this PC; nothing is sent until then.' };
  for (const id of Object.keys(tips)) {
    $(id).disabled = !s.voice.key;
    $(id).title = s.voice.key ? tips[id] : 'No ElevenLabs key in .env';
  }
}

function renderStats() {
  const n = GRAPH.nodes.length, e = GRAPH.edges.length;
  const types = new Set(GRAPH.nodes.map(x => x.type)).size;
  const orphans = GRAPH.nodes.filter(x => x.deg === 0).length;
  const avg = n ? (2 * e / n).toFixed(1) : '0';
  const stat = (v, k) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`;
  $('#stats').innerHTML = stat(n, 'notes') + stat(e, 'links') + stat(types, 'types') +
    stat(avg, 'avg deg') + stat(Math.max(0, ...GRAPH.nodes.map(x => x.deg)), 'max deg') + stat(orphans, 'orphans');
}

function renderHubs() {
  const top = [...GRAPH.nodes].sort((a, b) => b.deg - a.deg).slice(0, 8);
  $('#hubs').innerHTML = top.map(n =>
    `<li data-id="${n.id}"><span class="dot" style="background:${Graph.colorFor(n.type)}"></span>
     <span class="t">${esc(n.title)}</span><span class="n">${n.deg}</span></li>`).join('');
}

function renderFilters() {
  const counts = {};
  for (const n of GRAPH.nodes) counts[n.type] = (counts[n.type] || 0) + 1;
  const types = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  $('#filters').innerHTML = types.map(t =>
    `<li data-type="${esc(t)}" class="${hiddenTypes.has(t) ? 'off' : ''}">
       <span class="sw" style="background:${Graph.colorFor(t)}"></span>
       <span class="t">${esc(t)}</span><span class="n">${counts[t]}</span></li>`).join('');
}

// ---------------------------------------------------------------- inspector
async function openNote(id) {
  const el = $('#note');
  $('.left').classList.toggle('reading', id !== null);
  if (id === null) {
    el.className = 'note empty';
    el.innerHTML = 'Click a node to open it.<br><span class="hint">Shift-click a second node to trace the path between them.</span>';
    return;
  }
  let n;
  try { n = await api(`/api/note?id=${id}`); }
  catch (e) { el.textContent = `Couldn't load note: ${e.message}`; return; }
  const color = Graph.colorFor(n.type);
  const meta = Object.entries(n.meta).filter(([k]) => k !== 'type');
  el.className = 'note';
  el.innerHTML = `
    <span class="type" style="color:${color}">${esc(n.type)}</span>
    <h2>${esc(n.title)}</h2>
    <div class="meta">${esc(n.rel)}</div>
    ${meta.length ? `<dl class="kv">${meta.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>` : ''}
    <div class="body">${md(n.text)}</div>
    <div class="label" style="margin-top:14px">Linked · ${n.links.length}</div>
    <div class="links">${n.links.map(l =>
      `<button data-id="${l.id}"><i style="background:${Graph.colorFor(l.type)}"></i>${esc(l.title)}</button>`).join('')}</div>`;
  el.scrollTop = 0;
}

function showPath(ids, from, to) {
  const el = $('#note');
  $('.left').classList.add('reading');
  el.className = 'note';
  if (!ids) {
    el.innerHTML = `<div class="label">Path</div><p>No route between <b>${esc(from.title)}</b> and <b>${esc(to.title)}</b> with the current filters.</p>`;
    return;
  }
  el.innerHTML = `<div class="label">Shortest path · ${ids.length - 1} hops</div>
    <div class="path">${ids.map(n => `<span class="step wl" data-id="${n.id}">${esc(n.title)}</span>`).join('<span class="arrow">→</span>')}</div>`;
}

// Minimal markdown: headings, lists, tasks, quotes, bold, [[wikilinks]]. Escaped first.
function md(src) {
  const inline = s => esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g,
      (_, t, alias) => `<span class="wl" data-title="${t.trim()}">${alias || t}</span>`);
  const out = [];
  let list = false;
  for (const raw of src.split('\n')) {
    const line = raw.trimEnd();
    const li = /^\s*[-*] (.*)/.exec(line);
    if (list && !li) { out.push('</ul>'); list = false; }
    if (li) {
      if (!list) { out.push('<ul>'); list = true; }
      out.push(`<li>${inline(li[1].replace(/^\[ \] /, '☐ ').replace(/^\[x\] /i, '☑ '))}</li>`);
    }
    else if (/^#{1,6} /.test(line)) { if (!/^# /.test(line)) out.push(`<h3>${inline(line.replace(/^#+ /, ''))}</h3>`); }
    else if (line.startsWith('> ')) out.push(`<blockquote>${inline(line.slice(2))}</blockquote>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push('</ul>');
  return out.join('');
}

// ---------------------------------------------------------------- conversation
// After JARVIS saves a note: re-index on screen before lighting up the new ids.
async function reloadGraph() {
  try {
    GRAPH = await api('/api/graph');
    Graph.load(GRAPH);
    renderStats(); renderHubs(); renderFilters();
  } catch (e) { banner(`Couldn't refresh the graph: ${e.message}`); }
}

const TOOL_CAPTIONS = {
  search_brain: 'Checking your notes…', research_web: 'Researching…', read_inbox: 'Reading your inbox…',
  brief_me: 'Pulling your day together…', plan_day: 'Planning your day…', find_niches: 'Ranking niches…',
  draft_message: 'Drafting…', draft_script: 'Writing the script…', write_note: 'Saving to Obsidian…',
  remember: 'Remembering…', schedule_event: 'Checking your calendar…', market_brief: 'Checking the calendar and markets…',
};

// Streams the answer: text appears as it's written, and with opts.speak each sentence is
// voiced as soon as it's complete instead of after the whole reply.
async function ask(text, opts = {}) {
  text = text.trim();
  if (!text || busy) return null;
  busy = true;
  $('#q').value = '';
  const ex = addExchange(text);
  setReactor('thinking');
  let r = null, shown = '';
  const sayEl = () => {
    const j = ex.querySelector('.jarvis');
    if (j.classList.contains('pending')) { j.classList.remove('pending'); j.innerHTML = '<p class="say"></p>'; }
    return j.querySelector('.say');
  };
  try {
    const res = await fetch('/api/ask/stream', { method: 'POST', body: JSON.stringify({ text }),
      headers: { 'Content-Type': 'application/json', 'X-Jarvis': '1' } });
    if (!res.ok || !res.body) throw new Error(`server said ${res.status}`);
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === 'text') { shown += ev.delta; sayEl().textContent = shown; }
        else if (ev.type === 'sentence') { if (opts.speak) enqueueSpeech(ev.text); }
        else if (ev.type === 'tool') caption(TOOL_CAPTIONS[ev.name] || 'Working…', 'dim');
        else if (ev.type === 'reset') { shown = ''; speechClear(); }
        else if (ev.type === 'done') r = ev;
      }
    }
    if (!r) throw new Error('the reply was cut off');
    if (r.graph_changed) await reloadGraph();
    fillExchange(ex, r);
    banner(r.error || '');
  } catch (e) {
    fillExchange(ex, { reply: `Couldn't reach the server: ${e.message}`, cards: [], mode: 'error' });
  } finally {
    busy = false;
    if (!Voice.on) setReactor('idle');
  }
  return r;
}

async function resolvePending(id, ok, btn) {
  const cardEl = btn.closest('.card');
  cardEl.querySelectorAll('button').forEach(b => { b.disabled = true; });
  const ex = addExchange(ok ? 'Confirm' : 'Cancel', true);
  setReactor('thinking');
  try {
    const r = await post('/api/confirm', { id, ok });
    fillExchange(ex, r);
    cardEl.classList.add('done');
  } catch (e) {
    fillExchange(ex, { reply: `Couldn't reach the server: ${e.message}`, cards: [], mode: 'error' });
  } finally { setReactor('idle'); }
}

function addExchange(text, quiet = false) {
  const box = $('#convo');
  box.hidden = false;
  const ex = document.createElement('div');
  ex.className = 'ex';
  ex.innerHTML = `<div class="you ${quiet ? 'quiet' : ''}">${esc(text)}</div><div class="jarvis pending"><span class="dots"><i></i><i></i><i></i></span></div>`;
  $('#convo-list').appendChild(ex);
  while ($('#convo-list').children.length > CONVO_KEEP) $('#convo-list').firstElementChild.remove();
  $('#convo-list').scrollTop = $('#convo-list').scrollHeight;
  return ex;
}

function fillExchange(ex, r) {
  const j = ex.querySelector('.jarvis');
  j.classList.remove('pending');
  const tag = r.mode === 'fallback' ? '<span class="modetag">keyword routing · model offline</span>'
    : r.mode === 'error' ? '<span class="modetag">error</span>' : '';
  j.innerHTML = `<p class="say">${esc(r.reply || '…')}</p>${tag}${(r.cards || []).map(renderCard).join('')}`;
  if (r.notes && r.notes.length) Graph.highlight([...new Set(r.notes)]);
  // Scroll so the start of this reply is visible, not the bottom of its last card.
  $('#convo-list').scrollTop = ex.offsetTop - $('#convo-list').offsetTop;
  if (r.mode !== 'model') refreshStatus();
}

function renderCard(c) {
  const rows = c.rows.map(r => {
    const tagCls = r.tag === 'new' ? 'new' : r.tag === 'overdue' ? 'hot' : '';
    const attrs = r.note != null ? `data-id="${r.note}" class="row link"` : r.url ? `class="row"` : 'class="row"';
    const title = r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.text)}</a>` : esc(r.text);
    return `<div ${attrs}>
      ${r.tag ? `<span class="rtag ${tagCls}">${esc(r.tag)}</span>` : ''}
      <div class="rmain"><div class="rtext">${title}</div>${r.sub ? `<div class="rsub">${esc(r.sub)}</div>` : ''}</div>
      ${r.meta ? `<span class="rmeta">${esc(r.meta)}</span>` : ''}
    </div>`;
  }).join('');
  const actions = c.actions.map(a => {
    if (a.id === 'copy') return `<button class="btn primary" data-action="copy">Copy</button>`;
    if (a.id === 'google-connect') return `<button class="btn primary" data-action="google">Connect Google</button>`;
    return `<button class="btn ${a.style === 'primary' ? 'primary' : ''}" data-action="${a.style === 'cancel' ? 'cancel' : 'confirm'}" data-pid="${esc(a.id)}">${esc(a.label)}</button>`;
  }).join('');
  return `<div class="card kind-${esc(c.kind)}">
    <div class="ctitle">${esc(c.title)}</div>
    ${c.warn ? `<div class="cwarn">⚠ ${esc(c.warn)}</div>` : ''}
    ${rows}
    ${c.body ? `<pre class="cbody">${esc(c.body)}</pre>` : ''}
    ${c.foot ? `<div class="cfoot">${esc(c.foot)}</div>` : ''}
    ${actions ? `<div class="cactions">${actions}</div>` : ''}
  </div>`;
}

// Memory is read straight from memory/: no model call, nothing spoken.
async function showMemory() {
  let facts;
  try { facts = (await api('/api/memory')).facts; }
  catch (e) { return banner(`Couldn't read memory: ${e.message}`); }
  const ex = addExchange('Memory', true);
  fillExchange(ex, {
    reply: facts.length ? `${facts.length} thing${facts.length > 1 ? 's' : ''} I remember about you.`
                        : "Nothing yet. Say \"remember that…\" and I'll keep it.",
    mode: 'direct',
    cards: facts.length ? [{
      kind: 'memory', title: 'Memory · newest last', actions: [],
      rows: facts.map(f => ({ text: f.fact, meta: f.date, tag: f.topic, sub: `memory/${f.file}` })),
      foot: 'One fact per file in jarvis/memory/. JARVIS never edits or deletes these; delete a file yourself to make it forget.',
    }] : [],
  });
}

function rotatePlaceholder() {
  let i = 0;
  const q = $('#q');
  const set = () => { q.placeholder = `Try: "${EXAMPLES[i++ % EXAMPLES.length]}"   ·   press / to type`; };
  set();
  setInterval(() => { if (!q.value && document.activeElement !== q) set(); }, EXAMPLE_EVERY);
}

// ---------------------------------------------------------------- reactor
function drawTicks() {
  const g = document.querySelector('.ticks');
  const NS = 'http://www.w3.org/2000/svg';
  for (let i = 0; i < 48; i++) {
    const a = i / 48 * Math.PI * 2, r0 = i % 4 ? 94 : 91, r1 = 98;
    const l = document.createElementNS(NS, 'line');
    l.setAttribute('x1', 100 + r0 * Math.cos(a)); l.setAttribute('y1', 100 + r0 * Math.sin(a));
    l.setAttribute('x2', 100 + r1 * Math.cos(a)); l.setAttribute('y2', 100 + r1 * Math.sin(a));
    g.appendChild(l);
  }
}

function setReactor(state) {
  $('#reactor').dataset.state = state;
  $('#reactor-state').textContent = state === 'standby' ? 'STANDBY' : state.toUpperCase();
}

// ---------------------------------------------------------------- voice
// Three layers:
//   mic open      — one getUserMedia stream + AudioContext, shared by everything below
//   standby       — only the local "hey Jarvis" detector hears the mic; nothing recorded or sent
//   conversation  — turns are recorded, ended by silence, transcribed, answered, spoken
// Say "hey Jarvis" (or press Mic / Space) to start a conversation. It drops back to standby
// after CONVO_IDLE_MS of quiet, or on Esc / Mic. While JARVIS speaks the mic is deaf;
// interrupting is explicit (Mic, Space or Esc).
const Voice = {
  on: false, armed: false, state: 'idle', muted: false, stream: null, ctx: null, mic: null, out: null,
  rec: null, chunks: [], mime: '', timer: null, audio: null, stopSpeaking: null, lastActivity: 0,
  turnStart: 0, speechStart: 0, lastLoud: 0, heard: false, deafUntil: 0, onSince: 0, peak: 0, warnedDead: false,
};
const WAKE_PREF = 'jarvis.wake';

function pickMime() {
  return ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']
    .find(m => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || '';
}

function caption(text, cls = '') {
  const c = $('#caption');
  c.textContent = text || '';
  c.className = 'caption ' + cls;
}

function setVoiceState(s) {
  Voice.state = s;
  setReactor(s);
  $('#mic').classList.toggle('live', Voice.on);
  $('#mic').textContent = !Voice.on ? 'Mic' : s === 'speaking' ? 'Interrupt' : 'Mic on';
  $('#wake').classList.toggle('live', Voice.armed);
}

async function openMic() {
  if (Voice.stream) return true;
  if (!STATUS?.voice.key) { banner('No ElevenLabs key in .env, so voice is off. Typing still works.'); return false; }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    banner("This browser can't record audio here. Use Chrome, Edge or Firefox on http://127.0.0.1:7777.");
    return false;
  }
  try {
    Voice.stream = await navigator.mediaDevices.getUserMedia(
      { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) {
    const why = {
      NotAllowedError: 'Microphone blocked. Click the icon at the left of the address bar, allow the microphone, then press Mic again.',
      NotFoundError: 'No microphone found. Plug one in or pick an input device in Windows sound settings.',
      NotReadableError: 'The microphone is busy in another app (Zoom, Discord, OBS?). Close it and try again.',
    }[e.name] || `Microphone error: ${e.name}: ${e.message}`;
    banner(why);
    return false;
  }
  Voice.ctx = new AudioContext();
  const src = Voice.ctx.createMediaStreamSource(Voice.stream);
  // Levels are measured per audio block on the audio side, which keeps running at full rate
  // in a background tab; the setInterval tick below only makes decisions.
  Voice.mic = Voice.ctx.createScriptProcessor(2048, 1, 1);
  Voice.mic.onaudioprocess = onAudioBlock;
  src.connect(Voice.mic);
  Voice.mic.connect(Voice.ctx.destination);    // required for the node to run; its output is silence
  Voice.mime = pickMime();
  Voice.onSince = performance.now(); Voice.peak = 0; Voice.warnedDead = false;
  Voice.timer = setInterval(levelTick, LEVEL_EVERY_MS);
  if (Voice.ctx.state === 'suspended') {
    // Browsers only start audio after a click on the page. Say so rather than sit deaf.
    banner('Click anywhere on the page to switch the microphone on.');
    document.addEventListener('pointerdown', () => { Voice.ctx?.resume(); banner(''); }, { once: true });
  } else banner('');
  return true;
}

function closeMic() {
  clearInterval(Voice.timer);
  if (Voice.mic) Voice.mic.onaudioprocess = null;
  Voice.stream?.getTracks().forEach(t => t.stop());
  Voice.ctx?.close();
  Object.assign(Voice, { stream: null, ctx: null, mic: null, out: null, rec: null });
  drawLevel(0);
}

// Start a conversation: from the Mic button, Space, or the wake word.
async function voiceStart() {
  if (!await openMic()) return;
  Voice.on = true;
  listenAgain(0);
}

// End the conversation: back to standby if "hey Jarvis" is armed, otherwise mic off.
function voiceStop() {
  interrupt();
  if (Voice.rec && Voice.rec.state !== 'inactive') { Voice.rec.onstop = null; Voice.rec.stop(); }
  Voice.on = false;
  if (Voice.armed && Voice.stream) return standby();
  closeMic();
  setVoiceState('idle');
  caption('');
}

function standby() {
  Voice.on = false;
  Wake.reset();
  setVoiceState('standby');
  caption('Say "Hey Jarvis"', 'dim');
}

async function arm() {
  if (!await openMic()) return;
  Voice.armed = true;
  try { localStorage.setItem(WAKE_PREF, '1'); } catch { /* private mode: preference just isn't remembered */ }
  setVoiceState(Voice.on ? Voice.state : 'standby');
  if (!Wake.ready) {
    caption('Loading the wake word…', 'dim');
    try { await Wake.load(); }
    catch (e) {
      banner(`"Hey Jarvis" couldn't start: ${e.message}. Mic and Space still work.`);
      return disarm();
    }
  }
  if (!Voice.on) standby();
}

function disarm() {
  Voice.armed = false;
  try { localStorage.removeItem(WAKE_PREF); } catch { /* ignore */ }
  if (!Voice.on) { closeMic(); setVoiceState('idle'); caption(''); }
  else setVoiceState(Voice.state);
}

function chime() {
  const c = Voice.ctx;
  if (!c) return;
  const t = c.currentTime;
  [660, 990].forEach((f, i) => {
    const o = c.createOscillator(), g = c.createGain(), t0 = t + i * 0.09;
    o.frequency.value = f;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(0.08, t0 + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.16);
    o.connect(g).connect(c.destination);
    o.start(t0); o.stop(t0 + 0.18);
  });
}

Wake.onWake = () => {
  if (Voice.state !== 'standby') return;
  chime();
  Voice.on = true;
  listenAgain(300);                    // stay deaf through the chime
};

function rms(analyser) {
  const buf = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(buf);
  let s = 0;
  for (const v of buf) s += v * v;
  return Math.sqrt(s / buf.length);
}

function drawLevel(l) {
  const t = performance.now() / 180;
  document.querySelectorAll('#bars i').forEach((b, i) =>
    b.style.setProperty('--h', Math.min(1, l * (0.55 + 0.45 * Math.abs(Math.sin(t + i * 1.3))))));
  $('#reactor').style.setProperty('--level', Math.min(1, l));
}

// Runs for every ~46ms block of mic audio: tracks speech onset and the last loud moment.
function onAudioBlock(e) {
  const d = e.inputBuffer.getChannelData(0);
  let s = 0;
  for (let i = 0; i < d.length; i++) s += d[i] * d[i];
  const lvl = Math.sqrt(s / d.length), now = performance.now();
  Voice.blockMax = Math.max(Voice.blockMax || 0, lvl);
  Voice.peak = Math.max(Voice.peak, lvl);
  if (Voice.state === 'standby') { Wake.feed(d, e.inputBuffer.sampleRate); return; }
  if (Voice.state !== 'listening' || now < Voice.deafUntil) return;
  if (lvl > SPEECH_LEVEL) {
    if (!Voice.speechStart) Voice.speechStart = now;
    Voice.lastLoud = now;
    Voice.lastActivity = now;
    if (!Voice.heard && now - Voice.speechStart >= MIN_SPEECH_MS) { Voice.heard = true; caption('Hearing you…', 'live'); }
  } else if (Voice.speechStart && !Voice.heard && now - Voice.lastLoud > 300) {
    Voice.speechStart = 0;            // a blip, not speech
  }
}

// setInterval tick: draws the bars and decides when a turn is over.
function levelTick() {
  const now = performance.now();
  if (Voice.state === 'speaking' && Voice.out) { drawLevel(rms(Voice.out) * 4); return; }
  const lvl = Voice.blockMax || 0;
  Voice.blockMax = 0;
  drawLevel(Voice.state === 'listening' ? lvl * 7 : Voice.state === 'standby' ? lvl * 2.5 : 0);

  if (!Voice.warnedDead && Voice.peak === 0 && now - Voice.onSince > DEAD_MIC_MS) {
    Voice.warnedDead = true;
    banner('The microphone is connected but completely silent. Check its mute switch, or the input device in Windows sound settings.');
  }
  if (Voice.state !== 'listening') return;
  if (Voice.heard && (now - Voice.lastLoud >= SILENCE_MS || now - Voice.turnStart > MAX_TURN_MS)) endTurn();
  else if (!Voice.heard && Voice.armed && now - Voice.lastActivity > CONVO_IDLE_MS) voiceStop();   // back to standby
  else if (!Voice.heard && now - Voice.turnStart > IDLE_RESTART_MS) startRecorder();
}

function startRecorder() {
  if (Voice.rec && Voice.rec.state !== 'inactive') { Voice.rec.onstop = null; Voice.rec.stop(); }
  Voice.chunks = [];
  Voice.rec = new MediaRecorder(Voice.stream, Voice.mime ? { mimeType: Voice.mime } : undefined);
  Voice.rec.ondataavailable = e => { if (e.data.size) Voice.chunks.push(e.data); };
  Voice.rec.start(250);
  Object.assign(Voice, { turnStart: performance.now(), speechStart: 0, lastLoud: 0, heard: false });
}

function stopRecorder() {
  return new Promise(res => {
    const rec = Voice.rec;
    if (!rec || rec.state === 'inactive') return res(null);
    rec.onstop = () => res(new Blob(Voice.chunks, { type: rec.mimeType || Voice.mime || 'audio/webm' }));
    rec.stop();
  });
}

function listenAgain(grace = ECHO_GRACE_MS) {
  if (!Voice.on) return;
  Voice.deafUntil = performance.now() + grace;
  Voice.lastActivity = performance.now();
  setVoiceState('listening');
  caption('Listening…');
  startRecorder();
}

async function endTurn() {
  setVoiceState('thinking');                       // deaf from here until JARVIS finishes speaking
  const blob = await stopRecorder();
  if (!blob) return listenAgain();
  caption('Transcribing…');
  let text = '';
  try {
    const r = await fetch('/api/listen', { method: 'POST', body: blob,
      headers: { 'Content-Type': blob.type || 'audio/webm', 'X-Jarvis': '1' } });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.status);
    text = j.text;
  } catch (e) {
    banner(`Couldn't transcribe that: ${e.message}`);
    return listenAgain();
  }
  if (!text || !/[a-z0-9]/i.test(text)) { caption("Didn't catch that.", 'dim'); return listenAgain(); }
  caption(`“${text}”`, 'said');
  speechReset();
  await ask(text, { speak: !Voice.muted && Voice.on });   // sentences start playing while the rest streams in
  await speechDone();
  listenAgain();
}

// ---------------------------------------------------------------- speech queue
// Sentences are voiced as they arrive: one TTS request at a time (so the next is being made
// while the current one plays), played back to back. Each request carries what was just said
// so the joins sound like one delivery.
const Speech = { queue: [], chain: Promise.resolve(), playing: false, said: '', chars: 0, cut: false,
                 cancelled: false, idle: null };

function speechReset() {
  Object.assign(Speech, { queue: [], chain: Promise.resolve(), said: '', chars: 0, cut: false, cancelled: false });
}

function speechClear() {                 // drop what's queued; new sentences may still follow
  Speech.queue = [];
  Voice.stopSpeaking?.();
}

function interrupt() {                   // barge-in: stop now and ignore the rest of this reply
  Speech.cancelled = true;
  speechClear();
}

function enqueueSpeech(text) {
  if (Speech.cancelled || Speech.cut || !text.trim()) return;
  if (Speech.chars + text.length > MAX_SPOKEN_CHARS) { text = 'The rest is on screen.'; Speech.cut = true; }
  Speech.chars += text.length;
  const previous = Speech.said.slice(-500);
  Speech.said += ' ' + text;
  const audio = Speech.chain = Speech.chain.then(() => fetchSpeech(text, previous));
  Speech.queue.push(audio);
  if (!Speech.playing) playQueue();
}

async function playQueue() {
  Speech.playing = true;
  while (Speech.queue.length) {
    const blob = await Speech.queue.shift();
    if (blob && !Speech.cancelled) await playBlob(blob);
  }
  Speech.playing = false;
  const idle = Speech.idle;
  Speech.idle = null;
  idle?.();
}

function speechDone() {
  return Speech.playing ? new Promise(res => { Speech.idle = res; }) : Promise.resolve();
}

async function fetchSpeech(text, previous) {
  if (Speech.cancelled) return null;
  try {
    const r = await fetch('/api/speak', { method: 'POST', body: JSON.stringify({ text, previous_text: previous }),
      headers: { 'Content-Type': 'application/json', 'X-Jarvis': '1' } });
    if (!r.ok) throw new Error((await r.json()).error || r.status);
    const notice = r.headers.get('X-Voice-Notice');
    if (notice) banner(notice);
    return await r.blob();
  } catch (e) {
    banner(`Voice output failed: ${e.message}. The reply is on screen.`);
    return null;
  }
}

async function playBlob(blob) {
  const audio = new Audio(URL.createObjectURL(blob));
  // Play the voice straight to the speakers, not through the mic's audio graph: that graph's
  // ScriptProcessor stalls whenever the page is busy, which made speech choppy. The reactor
  // only measures a copy (captureStream; browsers without it just skip the meter).
  audio.addEventListener('playing', () => {
    try {
      const copy = audio.captureStream?.();
      if (copy && Voice.ctx && copy.getAudioTracks().length) {
        Voice.out = Voice.ctx.createAnalyser();
        Voice.out.fftSize = 1024;
        Voice.ctx.createMediaStreamSource(copy).connect(Voice.out);
      }
    } catch { /* the level meter is cosmetic */ }
  }, { once: true });
  setVoiceState('speaking');
  caption('');
  await new Promise(done => {
    let settled = false, overrun = null;
    // Watchdog: Chrome can hold back playback in a background tab, so "ended" may never
    // arrive. Never leave the loop stuck in "speaking".
    const noStart = setTimeout(() => {
      banner("Reply audio didn't start (is the JARVIS tab in the background?). The reply is on screen.");
      Speech.cancelled = true;         // don't wait this long again for every remaining sentence
      finish();
    }, PLAY_START_TIMEOUT_MS);
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(noStart); clearTimeout(overrun);
      audio.pause(); URL.revokeObjectURL(audio.src);
      Voice.stopSpeaking = null; Voice.out = null;
      done();
    };
    Voice.stopSpeaking = finish;
    audio.onplaying = () => {
      clearTimeout(noStart);
      if (isFinite(audio.duration)) overrun = setTimeout(finish, (audio.duration + 3) * 1000);
    };
    audio.onended = finish;
    audio.onerror = () => { banner("Couldn't play the reply audio."); finish(); };
    audio.play().catch(e => {
      if (settled) return;             // we paused it ourselves (barge-in or watchdog)
      banner(`Browser blocked audio playback: ${e.message}`);
      finish();
    });
  });
}

function micButton() {
  if (!Voice.on) return voiceStart();
  if (Voice.state === 'speaking') { interrupt(); return; }   // barge-in; endTurn resumes listening
  voiceStop();
}

// ---------------------------------------------------------------- events
function bindUI() {
  rotatePlaceholder();
  $('#ask').addEventListener('submit', e => { e.preventDefault(); ask($('#q').value); });
  $('#brief').addEventListener('click', () => ask('Brief me.'));
  $('#plan').addEventListener('click', () => ask('Plan my day.'));
  $('#market').addEventListener('click', () => ask('Pre-session brief: news and markets.'));
  $('#memory').addEventListener('click', showMemory);
  $('#new-chat').addEventListener('click', async () => {
    await post('/api/reset');
    $('#convo-list').innerHTML = '';
    $('#convo').hidden = true;
    Graph.highlight(null);
  });
  $('#hide-convo').addEventListener('click', () => { $('#convo').hidden = true; });

  document.addEventListener('click', async e => {
    const act = e.target.closest('[data-action]');
    if (act) {
      const a = act.dataset.action;
      if (a === 'confirm' || a === 'cancel') return resolvePending(act.dataset.pid, a === 'confirm', act);
      if (a === 'copy') {
        const body = act.closest('.card').querySelector('.cbody')?.textContent || '';
        try { await navigator.clipboard.writeText(body); act.textContent = 'Copied'; }
        catch { act.textContent = 'Copy failed'; }
        return;
      }
      if (a === 'google') return window.open('/oauth/start', '_blank', 'noopener');
    }
    if (e.target.closest('#google-chip')) {
      if (STATUS?.google.connected) {
        await post('/api/google/disconnect'); refreshStatus();
      } else if (STATUS?.google.configured) window.open('/oauth/start', '_blank', 'noopener');
      return;
    }
    if (e.target.closest('a')) return;
    const byId = e.target.closest('[data-id]');
    if (byId && !e.target.closest('#graph')) {
      Graph.setFocus(+byId.dataset.id);
      return;
    }
    const wl = e.target.closest('.wl[data-title]');
    if (wl) {
      const n = Graph.nodeByTitle(wl.dataset.title);
      if (n) Graph.setFocus(n.id);
    }
  });

  $('#filters').addEventListener('click', e => {
    const li = e.target.closest('li');
    if (!li) return;
    const t = li.dataset.type;
    hiddenTypes.has(t) ? hiddenTypes.delete(t) : hiddenTypes.add(t);
    li.classList.toggle('off', hiddenTypes.has(t));
    Graph.setHidden(hiddenTypes);
  });
  $('#show-all').addEventListener('click', () => {
    hiddenTypes.clear(); Graph.setHidden(hiddenTypes); renderFilters();
  });
  $('#fit').addEventListener('click', () => Graph.fit());

  $('#mic').addEventListener('click', micButton);
  $('#wake').addEventListener('click', () => (Voice.armed ? disarm() : arm()));
  try { if (localStorage.getItem(WAKE_PREF) === '1' && STATUS?.voice.key) arm(); } catch { /* storage blocked */ }
  $('#mute').addEventListener('click', () => {
    Voice.muted = !Voice.muted;
    $('#mute').classList.toggle('live', Voice.muted);
    $('#mute').textContent = Voice.muted ? 'Muted' : 'Mute';
    if (Voice.muted) interrupt();
  });

  document.addEventListener('keydown', e => {
    const typing = document.activeElement === $('#q');
    const onButton = document.activeElement?.tagName === 'BUTTON';
    if (e.key === '/' && !typing) { e.preventDefault(); $('#q').focus(); }
    else if (e.key === ' ' && !typing && !onButton) {
      e.preventDefault();
      if (!Voice.on) voiceStart();
      else if (Voice.state === 'speaking') interrupt();                  // barge in
      else if (Voice.state === 'listening' && Voice.heard) endTurn();   // "I'm done", without waiting for silence
    }
    else if (e.key === 'Escape') {
      if (typing) $('#q').blur();
      if (Voice.on && Voice.state === 'speaking') interrupt();
      else if (Voice.on) voiceStop();
      Graph.highlight(null); Graph.clear();
    }
    else if ((e.key === 'f' || e.key === 'F') && !typing) Graph.fit();
  });
}

boot();
