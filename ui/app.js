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
  'What should I film this week?',
  'Find me five dental clinics that could use automation',
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
const BARGE_IN_THRESHOLD = 0.8;  // "Hey Jarvis" must be this clear to cut in while JARVIS talks (its own voice leaks into the mic)
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

let loadedVersion = null;
async function refreshStatus() {
  try { STATUS = await api('/api/status'); renderStatus(STATUS); }
  catch { setTimeout(refreshStatus, 2000); return; }       // server restarting: check back soon
  loadedVersion ??= STATUS.code_version;
  if (STATUS.code_version !== loadedVersion && !busy && Voice.state !== 'speaking') {
    location.reload();                                     // JARVIS was updated: pick up the new page
    return;
  }
  maybeCheckin();
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
    : g.connected && !g.drafts ? ['warn', 'Google · reconnect', 'Connected, but from before Gmail drafts existed. Click to disconnect, then click again to connect and allow drafts.']
    : g.connected ? ['ok', 'Google', 'Connected. Click to disconnect.']
    : ['warn action', 'Google · connect', g.demo ? 'Demo mode uses fixtures; connect now for live mode later.' : 'Click to sign in'];

  const chip = ([cls, label, title], id = '') =>
    `<span class="chip ${cls}" ${id ? `id="${id}"` : ''} title="${esc(title)}">${esc(label)}</span>`;
  const u = s.usage, spent = u.today.usd;
  const spend = [u.over_budget ? 'warn' : 'ok', `$${spent < 10 ? spent.toFixed(2) : spent.toFixed(0)} today`,
                 `Estimated spend today. This month: $${u.month.usd.toFixed(2)}. Daily budget $${u.budget_usd}. Click for detail.`];
  if (u.over_budget) warnBudgetOnce(u);
  $('#chips').innerHTML =
    chip(model) +
    chip(!s.voice.key ? ['warn', 'Voice off', 'No ElevenLabs key in .env']
      : s.voice.tts_error || s.voice.stt_error ? ['warn', 'Voice', s.voice.tts_error || s.voice.stt_error]
      : ['ok', 'Voice', 'ElevenLabs speech in and out. Press Mic or Space.']) +
    chip(google, 'google-chip') +
    (s.telegram.enabled ? chip(s.telegram.error ? ['warn', 'Telegram', s.telegram.error]
                               : s.telegram.paired ? ['ok', 'Telegram', `Paired with ${s.telegram.name || 'your phone'}. Click to manage.`]
                               : ['warn action', 'Telegram · pair', 'Click for your pairing code'], 'telegram-chip') : '') +
    chip(spend, 'spend-chip');
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
  content_board: 'Checking your video pipeline…', set_status: 'Updating…', find_prospects: 'Searching for businesses…',
  add_prospect: 'Adding the prospect…', weekly_review: 'Reviewing your week…',
};

// Streams the answer: text appears as it's written, and with opts.speak each sentence is
// voiced as soon as it's complete instead of after the whole reply.
async function ask(text, opts = {}) {
  text = text.trim();
  const files = Attach.items.splice(0);
  if (files.length && !text) text = "What's this? Tell me what matters in it.";
  if (!text || busy) { Attach.items.unshift(...files); return null; }
  busy = true;
  $('#q').value = '';
  renderTray();
  const ex = addExchange(text + (files.length ? `  📎 ${files.map(f => f.name).join(', ')}` : ''));
  setReactor('thinking');
  let r = null, shown = '';
  const ctrl = new AbortController();
  Voice.abortAnswer = () => ctrl.abort();      // barge-in stops waiting for the rest of this answer
  const sayEl = () => {
    const j = ex.querySelector('.jarvis');
    if (j.classList.contains('pending')) { j.classList.remove('pending'); j.innerHTML = '<p class="say"></p>'; }
    return j.querySelector('.say');
  };
  try {
    const res = await fetch('/api/ask/stream', { method: 'POST', signal: ctrl.signal,
      body: JSON.stringify({ text, attachments: files.map(f => f.id) }),
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
    refreshWidgets();
    banner(r.error || '');
  } catch (e) {
    if (e.name === 'AbortError') fillExchange(ex, { reply: shown ? `${shown.trim()} …` : '(interrupted)', cards: [], mode: 'direct' });
    else fillExchange(ex, { reply: `Couldn't reach the server: ${e.message}`, cards: [], mode: 'error' });
  } finally {
    busy = false;
    Voice.abortAnswer = null;
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
    const tagCls = r.tag === 'new' ? 'new' : r.tag === 'overdue' ? 'hot' : (r.tag === 'done' || r.tag === 'PB') ? r.tag : '';
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
    if (a.id === 'telegram-unpair') return `<button class="btn" data-action="telegram-unpair">Unpair</button>`;
    if (a.id === 'checkin-answer') return `<button class="btn primary" data-action="checkin-answer">Answer</button>`;
    if (a.id === 'checkin-skip') return `<button class="btn" data-action="checkin-skip">Not tonight</button>`;
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

// ---------------------------------------------------------------- attachments
// Screenshots, photos and PDFs: attach with 📎, paste (Ctrl+V) or drop. They go with the next
// question only; the server keeps them in memory until then and never saves them.
const Attach = { items: [] };          // {id, name, kind, bytes}
const MAX_ATTACHMENTS = 5;

async function addFiles(list) {
  for (const f of [...list]) {
    if (Attach.items.length >= MAX_ATTACHMENTS) { banner(`Up to ${MAX_ATTACHMENTS} files per question.`); break; }
    try {
      const r = await fetch('/api/attach', { method: 'POST', body: f, headers: {
        'Content-Type': f.type || 'application/octet-stream', 'X-Filename': encodeURIComponent(f.name || 'pasted image.png'),
        'X-Jarvis': '1' } });
      const j = await r.json();
      if (!r.ok) { banner(j.error || `Couldn't attach ${f.name}`); continue; }
      Attach.items.push(j);
    } catch (e) { banner(`Couldn't attach ${f.name}: ${e.message}`); }
  }
  renderTray();
  $('#q').focus();
}

function renderTray() {
  const t = $('#tray');
  t.innerHTML = Attach.items.map(a =>
    `<span class="att"><b>${a.kind === 'pdf' ? 'PDF' : 'IMG'}</b> ${esc(a.name)} <button class="link" data-rm="${a.id}" title="Remove">✕</button></span>`).join('');
  t.hidden = !Attach.items.length;
  measureInsets();
}

function bindAttachments() {
  $('#clip').addEventListener('click', () => $('#file').click());
  $('#file').addEventListener('change', e => { addFiles(e.target.files); e.target.value = ''; });
  $('#tray').addEventListener('click', e => {
    const id = e.target.dataset?.rm;
    if (id) { Attach.items = Attach.items.filter(a => a.id !== id); renderTray(); }
  });
  document.addEventListener('paste', e => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  let depth = 0;
  const zone = $('#dropzone');
  document.addEventListener('dragenter', e => { if (e.dataTransfer?.types?.includes('Files')) { depth++; zone.hidden = false; } });
  document.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; zone.hidden = true; } });
  document.addEventListener('dragover', e => { if (e.dataTransfer?.types?.includes('Files')) e.preventDefault(); });
  document.addEventListener('drop', e => {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault(); depth = 0; zone.hidden = true;
    addFiles(e.dataTransfer.files);
  });
}

// Spend is estimated server-side from every paid call; this just shows it.
function warnBudgetOnce(u) {
  const key = 'jarvis.budgetWarned.' + new Date().toISOString().slice(0, 10);
  try { if (localStorage.getItem(key)) return; localStorage.setItem(key, '1'); } catch { /* show it anyway */ }
  banner(`Today's estimated spend ($${u.today.usd.toFixed(2)}) has passed your $${u.budget_usd} daily budget. `
    + 'JARVIS still works; set JARVIS_DAILY_BUDGET in .env to change the limit.');
}

async function showSpend() {
  let u;
  try { u = (await api('/api/status')).usage; } catch (e) { return banner(`Couldn't read spend: ${e.message}`); }
  const money = x => (x > 0 && x < 0.01 ? 'under 1¢' : `$${x.toFixed(2)}`), n = x => Math.round(x).toLocaleString();
  const rows = (t, label) => [
    { text: `${label}: ${money(t.usd)} on Claude`, tag: label === 'Today' ? 'today' : 'month',
      sub: `${n(t.calls)} calls · ${n(t.input)} in / ${n(t.output)} out tokens · ${n(t.cache_read)} cached · ${n(t.searches)} web searches` },
    { text: `${label}: ${n(t.tts_credits)} ElevenLabs credits`,
      sub: `${n(t.tts_chars)} characters spoken · ${(t.stt_seconds / 60).toFixed(1)} min of you transcribed` },
  ];
  const ex = addExchange('Spend', true);
  fillExchange(ex, {
    mode: 'direct',
    reply: `About ${money(u.today.usd)} on Claude today, ${money(u.month.usd)} this month.`
      + (u.over_budget ? ` That's past your $${u.budget_usd} daily budget.` : ''),
    cards: [{ kind: 'spend', title: `Spend · estimates · budget $${u.budget_usd}/day`, actions: [],
              rows: [...rows(u.today, 'Today'), ...rows(u.month, u.month_label)],
              foot: u.note + ' The free ElevenLabs tier is 10,000 credits a month.' }],
  });
}

// Evening check-in: offered once, the first time JARVIS is open after the check-in hour.
let checkinOffered = false;
function maybeCheckin() {
  if (checkinOffered || busy || !STATUS?.checkin?.due) return;
  checkinOffered = true;
  const ex = addExchange('Evening check-in', true);
  fillExchange(ex, { mode: 'direct', reply: STATUS.checkin.question, cards: [{
    kind: 'checkin', title: 'Evening check-in', rows: [], actions: [{ id: 'checkin-answer' }, { id: 'checkin-skip' }],
    foot: 'Your answer is saved in your own words to JARVIS/Journal. Talk or type.' }] });
}

async function answerCheckin(yes, btn) {
  btn.closest('.card').querySelectorAll('button').forEach(b => { b.disabled = true; });
  if (!yes) { await post('/api/checkin', { action: 'skip' }); btn.closest('.card').classList.add('done'); return; }
  const { question } = await post('/api/checkin', { action: 'start' });
  btn.closest('.card').classList.add('done');
  $('#q').placeholder = 'Your answer…';
  $('#q').focus();
  if (Voice.on && !Voice.muted) { speechReset(); enqueueSpeech(question); }
}

// Pairing happens here on the PC: the code never leaves this page, so only someone at your PC can pair.
async function showTelegram() {
  await refreshStatus();
  const t = STATUS.telegram, ex = addExchange('Telegram', true);
  const card = t.paired
    ? { kind: 'telegram', title: `Telegram · paired with ${t.name || 'your phone'}`, rows: [], actions: [{ id: 'telegram-unpair', label: 'Unpair' }],
        foot: 'Only this chat is answered. Messages pass through Telegram (not end-to-end encrypted). JARVIS must be running on this PC to reply.' }
    : { kind: 'telegram', title: 'Telegram · pair your phone', actions: [],
        rows: [{ text: 'Open your JARVIS bot in Telegram (the one you made with @BotFather)', tag: '1' },
               { text: `Send:  /pair ${t.pair_code}`, tag: '2', sub: 'or just the 6 digits. The code changes every time JARVIS restarts; 5 wrong tries locks pairing for 10 minutes.' },
               { text: 'JARVIS replies "Paired." and from then on answers only you', tag: '3' },
               { text: t.last ? `Last message JARVIS received: ${t.last}` : 'No message received from Telegram yet',
                 tag: 'check', sub: t.last ? null : 'If you have sent one, JARVIS may have been restarted since: send it again.' }],
        foot: t.error || 'Just the 6 digits works too. Voice notes work once paired: they are transcribed on this PC via ElevenLabs.' };
  fillExchange(ex, { mode: 'direct', reply: t.paired ? `Paired with ${t.name || 'your phone'}.` : 'Here is how to pair your phone.', cards: [card] });
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

// ---------------------------------------------------------------- one voice across tabs
// Two JARVIS tabs with the mic open both hear "Hey Jarvis" and both answer. Only one tab may hold
// the mic: opening it tells the others to let go.
const Tabs = (() => {
  const id = Math.random().toString(36).slice(2);
  const ch = 'BroadcastChannel' in window ? new BroadcastChannel('jarvis') : null;
  const answers = [];
  if (ch) ch.onmessage = ({ data: m }) => {
    if (m.tab === id) return;
    if (m.type === 'voice-claim' && Voice.stream) {
      interrupt();
      Voice.on = false;
      Voice.armed = false;                 // this tab only; the saved preference stays
      closeMic();
      setVoiceState('idle');
      caption('Voice moved to your other JARVIS tab', 'dim');
    }
    if (m.type === 'who-has-voice') ch.postMessage({ type: 'voice-here', tab: id, has: !!Voice.stream });
    if (m.type === 'voice-here') answers.push(m.has);
  };
  return {
    claimVoice() { ch?.postMessage({ type: 'voice-claim', tab: id }); },
    async voiceElsewhere() {
      if (!ch) return false;
      answers.length = 0;
      ch.postMessage({ type: 'who-has-voice', tab: id });
      await new Promise(r => setTimeout(r, 300));
      return answers.some(Boolean);
    },
  };
})();

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
  Tabs.claimVoice();
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
  Wake.load().catch(() => { /* barge-in by voice just isn't available; Space/Esc still work */ });
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

Wake.onWake = score => {
  if (Voice.state === 'standby') {
    chime();
    Voice.on = true;
    listenAgain(300);                  // stay deaf through the chime
  } else if (Voice.state === 'speaking' && score >= BARGE_IN_THRESHOLD) {
    chime();
    interrupt();                       // endTurn resumes listening once the reply has stopped
  }
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
  // The wake detector hears everything while the mic is open, so it's primed the moment it's needed;
  // Wake.onWake only acts on standby (start talking) or while JARVIS speaks (cut in).
  Wake.feed(d, e.inputBuffer.sampleRate);
  if (Voice.state === 'standby') return;
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
                 cancelled: false, idle: null, gen: 0 };   // gen: which reply the queue belongs to

function speechReset() {
  Voice.stopSpeaking?.();                // whatever is still playing stops before a new reply starts
  Object.assign(Speech, { queue: [], chain: Promise.resolve(), said: '', chars: 0, cut: false, cancelled: false,
                          playing: false, gen: Speech.gen + 1 });
}

function speechClear() {                 // drop what's queued; new sentences may still follow
  Speech.queue = [];
  Voice.stopSpeaking?.();
}

function stopSpeech() {                  // silence this reply now; its text keeps arriving on screen
  Speech.cancelled = true;
  speechClear();
  // Release anyone waiting for the reply to finish right away, rather than after the audio
  // that's still being generated arrives and gets thrown away.
  Speech.playing = false;
  const idle = Speech.idle;
  Speech.idle = null;
  idle?.();
}

function interrupt() {                   // barge-in: stop talking and stop waiting for the rest of the answer
  stopSpeech();
  Voice.abortAnswer?.();
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
  const gen = Speech.gen;
  Speech.playing = true;
  while (Speech.queue.length) {
    const blob = await Speech.queue.shift();
    if (gen !== Speech.gen) return;       // a newer reply owns the queue now
    if (Speech.cancelled) break;          // stopped: fall through so whoever's waiting is released
    if (blob) await playBlob(blob);
  }
  if (gen !== Speech.gen) return;
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
      finish();
      stopSpeech();                    // don't wait this long again for every remaining sentence
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
  bindAttachments();
  bindWidgets();
  $('#ask').addEventListener('submit', e => { e.preventDefault(); ask($('#q').value); });
  $('#brief').addEventListener('click', () => ask('Brief me.'));
  $('#plan').addEventListener('click', () => ask('Plan my day.'));
  $('#market').addEventListener('click', () => ask('Pre-session brief: news and markets.'));
  $('#week').addEventListener('click', () => ask('Weekly review.'));
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
      if (a === 'checkin-answer' || a === 'checkin-skip') return answerCheckin(a === 'checkin-answer', act);
      if (a === 'telegram-unpair') {
        await post('/api/telegram/unpair'); act.textContent = 'Unpaired'; act.disabled = true; return refreshStatus();
      }
    }
    if (e.target.closest('#spend-chip')) return showSpend();
    if (e.target.closest('#telegram-chip')) return showTelegram();
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
  try {
    if (localStorage.getItem(WAKE_PREF) === '1' && STATUS?.voice.key) {
      Tabs.voiceElsewhere().then(other => other
        ? caption('"Hey Jarvis" is on in your other JARVIS tab', 'dim')
        : arm());
    }
  } catch { /* storage blocked */ }
  $('#mute').addEventListener('click', () => {
    Voice.muted = !Voice.muted;
    $('#mute').classList.toggle('live', Voice.muted);
    $('#mute').textContent = Voice.muted ? 'Muted' : 'Mute';
    if (Voice.muted) stopSpeech();
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

// ---------------------------------------------------------------- widgets
// Small live cards in the right panel. Which ones show, and their order, is a per-browser
// preference (localStorage); the data comes from /api/widgets and costs nothing.
const WIDGETS_EVERY_MS = 20000;
const WIDGET_PREF = 'jarvis.widgets';
const WIDGET_DEFS = {
  goals:    { title: 'Today', render: wGoals },
  training: { title: 'Training', render: wTraining },
  next:     { title: 'Next up', render: wNext },
  spend:    { title: 'Spend today', render: wSpend },
};
const Widgets = { data: null, editing: false, pref: { order: Object.keys(WIDGET_DEFS), hidden: [] } };
try { Object.assign(Widgets.pref, JSON.parse(localStorage.getItem(WIDGET_PREF) || '{}')); } catch {}
for (const k of Object.keys(WIDGET_DEFS)) if (!Widgets.pref.order.includes(k)) Widgets.pref.order.push(k);

function saveWidgetPref() { try { localStorage.setItem(WIDGET_PREF, JSON.stringify(Widgets.pref)); } catch {} }

function wGoals(d) {
  const g = d.goals || [], done = g.filter(x => x.done).length;
  const list = g.map((x, i) => `<div class="goal ${x.done ? 'done' : ''}" data-goal="${i}" data-done="${x.done ? 1 : 0}">
      <span class="box"></span><span class="gt">${esc(x.text)}</span></div>`).join('');
  return { badge: g.length ? `${done}/${g.length}` : '',
    html: (list || '<div class="sub">No goals yet. Add one, or tell me what you want done today.</div>') +
      (g.length ? `<div class="meter"><i style="width:${Math.round(100 * done / g.length)}%"></i></div>` : '') +
      '<input class="goal-add" id="goal-add" placeholder="+ add a goal" maxlength="200">' };
}
function wTraining(d) {
  const t = d.training || {};
  if (!t.total) return { html: '<div class="sub">Nothing logged yet. Tell me what you trained.</div>' };
  const last = t.last ? `${esc(t.last.title)} · ${t.last.days_ago === 0 ? 'today' : t.last.days_ago === 1 ? 'yesterday' : t.last.days_ago + ' days ago'}` : '';
  return { badge: `${t.day_streak}d streak`,
    html: `<div class="big">${t.this_week} this week</div><div class="sub">${t.week_streak}-week streak</div><div class="sub">Last: ${last}</div>` };
}
function wNext(d) {
  const n = d.next;
  if (!n) return { html: '<div class="sub">Nothing on the calendar in the next two days.</div>' };
  if (n.error) return { html: `<div class="sub">${esc(n.error)}</div>` };
  return { html: `<div class="big">${esc(n.when)}</div><div class="sub">${esc(n.title)}</div>` };
}
function wSpend(d) {
  const s = d.spend || {}, pct = s.budget ? Math.min(100, Math.round(100 * s.usd / s.budget)) : 0;
  const split = Object.entries(s.models || {}).map(([m, v]) => `${esc(m.replace('claude-', ''))} $${v.toFixed(2)}`).join(' · ');
  return { badge: `of $${(s.budget || 0).toFixed(0)}`,
    html: `<div class="big">$${(s.usd || 0).toFixed(2)}</div>${split ? `<div class="sub">${split}</div>` : ''}<div class="meter"><i style="width:${pct}%"></i></div>` };
}

function renderWidgets() {
  const box = $('#widgets'), d = Widgets.data;
  if (!d) return;
  const typing = document.activeElement?.id === 'goal-add' ? document.activeElement.value : null;
  box.innerHTML = Widgets.pref.order.map(k => {
    const def = WIDGET_DEFS[k], off = Widgets.pref.hidden.includes(k);
    if (!def || (off && !Widgets.editing)) return '';
    const w = def.render(d);
    return `<div class="widget ${off ? 'off' : ''}" data-w="${k}" draggable="${Widgets.editing}">
      <h4><span>${def.title}</span><span class="wctl"><button data-wmove="-1">↑</button><button data-wmove="1">↓</button>
        <button data-wtoggle>${off ? 'show' : 'hide'}</button></span>${w.badge && !Widgets.editing ? `<b>${esc(w.badge)}</b>` : ''}</h4>
      ${w.html}</div>`;
  }).join('');
  if (typing !== null) { const i = $('#goal-add'); if (i) { i.value = typing; i.focus(); } }
}

async function refreshWidgets() {
  try { Widgets.data = await api('/api/widgets'); renderWidgets(); } catch {}
}

function bindWidgets() {
  const sec = document.querySelector('.widgets'), box = $('#widgets');
  $('#widgets-edit').addEventListener('click', () => {
    Widgets.editing = !Widgets.editing;
    sec.classList.toggle('editing', Widgets.editing);
    $('#widgets-edit').textContent = Widgets.editing ? 'done' : 'edit';
    renderWidgets();
  });
  box.addEventListener('click', async e => {
    const w = e.target.closest('.widget')?.dataset.w;
    const mv = e.target.closest('[data-wmove]'), tg = e.target.closest('[data-wtoggle]');
    const order = Widgets.pref.order;
    if (mv) {
      const i = order.indexOf(w), j = i + Number(mv.dataset.wmove);
      if (j >= 0 && j < order.length) { [order[i], order[j]] = [order[j], order[i]]; saveWidgetPref(); renderWidgets(); }
      return;
    }
    if (tg) {
      const h = Widgets.pref.hidden;
      Widgets.pref.hidden = h.includes(w) ? h.filter(x => x !== w) : [...h, w];
      saveWidgetPref(); renderWidgets();
      return;
    }
    const g = e.target.closest('[data-goal]');
    if (g && !Widgets.editing) {
      g.classList.toggle('done');                     // feels instant; the server's answer redraws it
      try { Widgets.data = await post('/api/goals', { action: 'tick', index: Number(g.dataset.goal), done: g.dataset.done !== '1' }); }
      catch (err) { banner(`Couldn't tick that: ${err.message}`); }
      renderWidgets();
    }
  });
  box.addEventListener('keydown', async e => {
    if (e.target.id !== 'goal-add' || e.key !== 'Enter' || !e.target.value.trim()) return;
    const text = e.target.value.trim();
    e.target.value = '';
    try { Widgets.data = await post('/api/goals', { action: 'add', text }); renderWidgets(); $('#goal-add')?.focus(); }
    catch (err) { banner(`Couldn't add that: ${err.message}`); }
  });
  // drag to reorder while editing
  let dragged = null;
  box.addEventListener('dragstart', e => { dragged = e.target.closest('.widget')?.dataset.w; });
  box.addEventListener('dragover', e => { if (dragged) e.preventDefault(); });
  box.addEventListener('drop', e => {
    const over = e.target.closest('.widget')?.dataset.w;
    if (!dragged || !over || over === dragged) return;
    const o = Widgets.pref.order;
    o.splice(o.indexOf(dragged), 1);
    o.splice(o.indexOf(over), 0, dragged);
    dragged = null; saveWidgetPref(); renderWidgets();
  });
  refreshWidgets();
  setInterval(refreshWidgets, WIDGETS_EVERY_MS);
  window.addEventListener('focus', refreshWidgets);
}

boot();
