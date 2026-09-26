// Brain graph: force-directed, canvas-rendered.
// Repulsion uses a spatial grid with a distance cutoff, so cost stays near-linear.
const Graph = (() => {
  // ---- tuning ---------------------------------------------------------------
  const CELL = 150;             // repulsion cutoff and grid cell size, world px
  const REPEL = 2600;
  const LINK_LEN = 62;
  const GRAVITY = 0.010;
  const DAMP = 0.84;
  const COOL = 0.988;
  const ALPHA_FLOOR = 0.012;    // never reaches zero: the graph keeps breathing
  const DIM = 0.10;             // opacity of everything outside the lit set
  const PULSE_EVERY = 3200;     // idle pulse cadence, ms
  const PULSE_MS = 1300;
  const MAX_LABELS = 140;

  const TYPE_COLORS = {
    trade: '#f472b6', account: '#fb7185', trading: '#f9a8d4', setup: '#e879f9',
    tool: '#d8b4fe', offer: '#c084fc', prospect: '#a78bfa', niche: '#818cf8',
    workflow: '#67e8f9', tech: '#7dd3fc', person: '#f0abfc', meeting: '#fda4af',
    idea: '#f5d0fe', daily: '#6f5a92', hub: '#ffffff', topic: '#a5b4fc',
    video: '#ff6ad5', content: '#ff9bd2', script: '#ff85c8', journal: '#b794f6', note: '#e9d5ff',
  };
  function colorFor(type) {
    if (TYPE_COLORS[type]) return TYPE_COLORS[type];
    let h = 0;
    for (const c of type) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return `hsl(${260 + (h % 70)}, 80%, 72%)`;
  }

  // ---- state ----------------------------------------------------------------
  let cv, ctx, W = 0, H = 0, dpr = 1;
  let nodes = [], edges = [], adj = [], byDeg = [];
  const cam = { x: 0, y: 0, k: 1 }, camGoal = { x: 0, y: 0, k: 1 };
  let camAnim = false;
  let alpha = 1;
  let hover = null, focus = null;
  let path = null;               // { nodes:Set, edges:Set("a-b") }
  let found = null;              // Set of ids from search
  let hidden = new Set();        // hidden types
  let pulses = [], lastPulse = 0;
  let drag = null;
  let cb = { onFocus() {}, onPath() {} };
  let inset = { l: 0, r: 0, t: 0, b: 0 };   // screen space covered by panels
  const widthCache = new Map();

  // ---- setup ----------------------------------------------------------------
  function build(data) {
    nodes = data.nodes.map(n => ({ ...n, x: 0, y: 0, vx: 0, vy: 0, r: 2.2 + Math.sqrt(n.deg) * 1.3,
                                   color: colorFor(n.type), pinned: false }));
    edges = data.edges.map(([a, b]) => ({ a: nodes[a], b: nodes[b] }));
    adj = nodes.map(() => []);
    for (const e of edges) { adj[e.a.id].push(e.b.id); adj[e.b.id].push(e.a.id); }
    byDeg = [...nodes].sort((p, q) => q.deg - p.deg);
  }

  // Swap in a re-indexed graph (e.g. after JARVIS saves a note). Existing notes keep their
  // place; new ones appear beside a neighbour. Ids can shift, so match on path.
  function load(data) {
    const old = new Map(nodes.map(n => [n.rel, n]));
    build(data);
    for (const n of nodes) {
      const o = old.get(n.rel);
      if (o) { n.x = o.x; n.y = o.y; n.pinned = o.pinned; continue; }
      const nb = adj[n.id].map(i => old.get(nodes[i].rel)).find(Boolean);
      n.x = (nb ? nb.x : 0) + (Math.random() - 0.5) * 40;
      n.y = (nb ? nb.y : 0) + (Math.random() - 0.5) * 40;
    }
    hover = focus = path = found = null;
    alpha = Math.max(alpha, 0.3);
  }

  function init(canvas, data, callbacks) {
    cv = canvas; ctx = cv.getContext('2d');
    Object.assign(cb, callbacks || {});
    build(data);

    // Golden-angle spiral, hubs in the middle: settles fast and the same way every load.
    byDeg.forEach((n, i) => {
      const r = 14 * Math.sqrt(i + 1), t = i * 2.39996;
      n.x = r * Math.cos(t); n.y = r * Math.sin(t);
    });
    for (let i = 0; i < 220; i++) tick();

    resize();
    fit(false);
    window.addEventListener('resize', resize);
    bindInput();
    requestAnimationFrame(frame);
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = cv.clientWidth; H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr;
  }

  function fit(animate = true) {
    const vis = nodes.filter(n => !hidden.has(n.type));
    if (!vis.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of vis) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const aw = W - inset.l - inset.r, ah = H - inset.t - inset.b;
    const k = Math.min(aw / (x1 - x0 + 80), ah / (y1 - y0 + 80), 2.2);
    const [ox, oy] = offset(k);
    moveCam((x0 + x1) / 2 - ox, (y0 + y1) / 2 - oy, k, animate);
  }

  // World offset that puts a point in the middle of the free area, not the window.
  function offset(k) {
    return [(inset.l - inset.r) / 2 / k, (inset.t - inset.b) / 2 / k];
  }

  function moveCam(x, y, k, animate = true) {
    Object.assign(camGoal, { x, y, k });
    if (animate) camAnim = true; else Object.assign(cam, camGoal);
  }

  // ---- physics --------------------------------------------------------------
  function tick() {
    const grid = new Map();
    for (const n of nodes) {
      const key = Math.floor(n.x / CELL) + ',' + Math.floor(n.y / CELL);
      let cell = grid.get(key);
      if (!cell) grid.set(key, cell = []);
      cell.push(n);
    }
    const cut2 = CELL * CELL;
    for (const n of nodes) {
      const gx = Math.floor(n.x / CELL), gy = Math.floor(n.y / CELL);
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        const cell = grid.get((gx + dx) + ',' + (gy + dy));
        if (!cell) continue;
        for (const m of cell) {
          if (m.id <= n.id) continue;
          let ddx = n.x - m.x, ddy = n.y - m.y, d2 = ddx * ddx + ddy * ddy;
          if (d2 > cut2) continue;
          if (d2 < 0.01) { ddx = Math.random() - 0.5; ddy = Math.random() - 0.5; d2 = 0.5; }
          const f = REPEL * alpha / (d2 + 30);
          n.vx += ddx * f / Math.sqrt(d2); n.vy += ddy * f / Math.sqrt(d2);
          m.vx -= ddx * f / Math.sqrt(d2); m.vy -= ddy * f / Math.sqrt(d2);
        }
      }
    }
    for (const { a, b } of edges) {
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
      const da = a.deg || 1, db = b.deg || 1;
      const s = (d - LINK_LEN) / d * alpha / Math.min(da, db) * 0.9;
      const bias = da / (da + db);
      b.vx -= dx * s * bias; b.vy -= dy * s * bias;
      a.vx += dx * s * (1 - bias); a.vy += dy * s * (1 - bias);
    }
    const t = performance.now() * 0.0005;
    for (const n of nodes) {
      n.vx -= n.x * GRAVITY * alpha; n.vy -= n.y * GRAVITY * alpha;
      if (n.pinned) { n.vx = n.vy = 0; continue; }
      n.vx *= DAMP; n.vy *= DAMP;
      n.x += n.vx + Math.sin(t + n.id) * 0.012;
      n.y += n.vy + Math.cos(t * 1.3 + n.id) * 0.012;
    }
    alpha = Math.max(ALPHA_FLOOR, alpha * COOL);
  }

  // ---- coordinates ----------------------------------------------------------
  const sx = x => (x - cam.x) * cam.k + W / 2;
  const sy = y => (y - cam.y) * cam.k + H / 2;
  const wx = x => (x - W / 2) / cam.k + cam.x;
  const wy = y => (y - H / 2) / cam.k + cam.y;

  function hit(px, py) {
    const x = wx(px), y = wy(py);
    let best = null, bd = Infinity;
    for (const n of nodes) {
      if (hidden.has(n.type)) continue;
      const d = Math.hypot(n.x - x, n.y - y), lim = n.r + 5 / cam.k;
      if (d < lim && d < bd) { best = n; bd = d; }
    }
    return best;
  }

  // ---- lit set --------------------------------------------------------------
  function litSet() {
    if (hover) return new Set([hover.id, ...adj[hover.id]]);
    if (path) return path.nodes;
    if (found) return found;
    if (focus) return new Set([focus.id, ...adj[focus.id]]);
    return null;
  }
  const ekey = (a, b) => a < b ? a + '-' + b : b + '-' + a;

  // ---- render ---------------------------------------------------------------
  function frame(now) {
    tick();
    if (camAnim) {
      const e = 0.14;
      cam.x += (camGoal.x - cam.x) * e; cam.y += (camGoal.y - cam.y) * e; cam.k += (camGoal.k - cam.k) * e;
      if (Math.abs(camGoal.k - cam.k) < 0.001 && Math.hypot(camGoal.x - cam.x, camGoal.y - cam.y) < 0.3) camAnim = false;
    }
    const lit = litSet();
    if (!lit && now - lastPulse > PULSE_EVERY && edges.length) {
      lastPulse = now;
      const e = edges[Math.floor(Math.random() * edges.length)];
      if (!hidden.has(e.a.type) && !hidden.has(e.b.type)) pulses.push({ e, t0: now, rev: Math.random() < 0.5 });
    }
    draw(now, lit);
    requestAnimationFrame(frame);
  }

  function draw(now, lit) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const vis = n => !hidden.has(n.type);

    // edges — dim pass then lit pass
    ctx.lineWidth = Math.max(0.5, 0.8 * Math.min(cam.k, 1.5));
    ctx.strokeStyle = `rgba(190,150,255,${lit ? 0.035 : 0.13})`;
    ctx.beginPath();
    for (const { a, b } of edges) {
      if (!vis(a) || !vis(b)) continue;
      if (lit && lit.has(a.id) && lit.has(b.id)) continue;
      ctx.moveTo(sx(a.x), sy(a.y)); ctx.lineTo(sx(b.x), sy(b.y));
    }
    ctx.stroke();

    if (lit) {
      ctx.lineWidth = 1.3;
      for (const { a, b } of edges) {
        if (!vis(a) || !vis(b) || !lit.has(a.id) || !lit.has(b.id)) continue;
        const onPath = path && !hover && path.edges.has(ekey(a.id, b.id));
        if (path && !hover && !onPath) continue;
        if (found && !hover && !path && !focus) continue;
        const g = ctx.createLinearGradient(sx(a.x), sy(a.y), sx(b.x), sy(b.y));
        g.addColorStop(0, onPath ? 'rgba(255,120,200,.95)' : 'rgba(167,139,250,.75)');
        g.addColorStop(1, onPath ? 'rgba(255,120,200,.95)' : 'rgba(236,72,153,.75)');
        ctx.strokeStyle = g; ctx.lineWidth = onPath ? 2.4 : 1.3;
        ctx.beginPath(); ctx.moveTo(sx(a.x), sy(a.y)); ctx.lineTo(sx(b.x), sy(b.y)); ctx.stroke();
      }
    }

    // idle pulses
    pulses = pulses.filter(p => now - p.t0 < PULSE_MS);
    for (const p of pulses) {
      let t = (now - p.t0) / PULSE_MS; t = t * t * (3 - 2 * t);
      const [a, b] = p.rev ? [p.e.b, p.e.a] : [p.e.a, p.e.b];
      const x = sx(a.x + (b.x - a.x) * t), y = sy(a.y + (b.y - a.y) * t);
      const fade = Math.sin(Math.PI * (now - p.t0) / PULSE_MS);
      ctx.strokeStyle = `rgba(236,72,153,${0.5 * fade})`; ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(sx(a.x), sy(a.y)); ctx.lineTo(x, y); ctx.stroke();
      ctx.fillStyle = `rgba(255,190,235,${fade})`;
      ctx.shadowColor = '#ec4899'; ctx.shadowBlur = 12;
      ctx.beginPath(); ctx.arc(x, y, 2.4, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
    }

    // nodes
    for (const n of nodes) {
      if (!vis(n)) continue;
      const on = !lit || lit.has(n.id);
      const lift = n === hover ? 1.4 : n === focus ? 1.25 : 1;
      const r = Math.max(1.4, n.r * cam.k * lift);
      const x = sx(n.x), y = sy(n.y);
      if (x < -r || y < -r || x > W + r || y > H + r) continue;
      ctx.globalAlpha = on ? 1 : DIM;
      if (on && lit && (n === hover || n === focus || n.deg > 12)) { ctx.shadowColor = n.color; ctx.shadowBlur = 14; }
      ctx.fillStyle = n.color;
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      if (n === focus || (path && (n.id === path.from || n.id === path.to))) {
        ctx.strokeStyle = 'rgba(255,255,255,.85)'; ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.arc(x, y, r + 4, 0, Math.PI * 2); ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    drawLabels(lit);
  }

  // Most-connected first; any label whose box collides with a placed one is skipped.
  function drawLabels(lit) {
    const placed = [];
    const font = 11;
    ctx.font = `${font}px "Cascadia Mono", Consolas, ui-monospace, monospace`;
    ctx.textBaseline = 'middle';
    const order = lit
      ? [...byDeg.filter(n => lit.has(n.id)), ...byDeg.filter(n => !lit.has(n.id))]
      : byDeg;
    for (const n of order) {
      if (placed.length >= MAX_LABELS) break;
      if (hidden.has(n.type)) continue;
      const on = !lit || lit.has(n.id);
      const r = n.r * cam.k;
      const important = n === hover || n === focus || (lit && lit.has(n.id));
      if (!important && r < 7 && cam.k < 1.8) continue;
      if (lit && !on) continue;
      const x = sx(n.x), y = sy(n.y);
      if (x < -50 || x > W + 50 || y < -20 || y > H + 20) continue;
      const w = measure(n.title);
      const box = { x0: x + r + 5, y0: y - 8, x1: x + r + 5 + w, y1: y + 8 };
      if (placed.some(b => box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0)) continue;
      placed.push(box);
      ctx.strokeStyle = 'rgba(7,6,12,.9)'; ctx.lineWidth = 3; ctx.lineJoin = 'round';
      ctx.strokeText(n.title, box.x0, y);
      ctx.fillStyle = n === hover || n === focus ? '#fff' : `rgba(226,214,255,${important ? 0.95 : 0.62})`;
      ctx.fillText(n.title, box.x0, y);
    }
  }
  function measure(s) {
    let w = widthCache.get(s);
    if (w === undefined) widthCache.set(s, w = ctx.measureText(s).width);
    return w;
  }

  // ---- input ----------------------------------------------------------------
  function bindInput() {
    cv.addEventListener('pointerdown', e => {
      cv.setPointerCapture(e.pointerId);
      const n = hit(e.offsetX, e.offsetY);
      drag = { n, x: e.offsetX, y: e.offsetY, cx: cam.x, cy: cam.y, moved: false };
      camAnim = false;
    });
    cv.addEventListener('pointermove', e => {
      if (drag) {
        const dx = e.offsetX - drag.x, dy = e.offsetY - drag.y;
        if (Math.hypot(dx, dy) > 4) drag.moved = true;
        if (!drag.moved) return;
        if (drag.n) {
          drag.n.x = wx(e.offsetX); drag.n.y = wy(e.offsetY); drag.n.pinned = true;
          alpha = Math.max(alpha, 0.25);
        } else {
          cam.x = drag.cx - dx / cam.k; cam.y = drag.cy - dy / cam.k;
          Object.assign(camGoal, cam);
        }
        return;
      }
      const n = hit(e.offsetX, e.offsetY);
      if (n !== hover) { hover = n; cv.style.cursor = n ? 'pointer' : 'grab'; }
    });
    cv.addEventListener('pointerup', e => {
      const d = drag; drag = null;
      if (!d || d.moved) return;
      if (d.n && e.shiftKey && focus && d.n !== focus) return tracePath(focus.id, d.n.id);
      if (d.n) return setFocus(d.n.id, false);
      clear();
    });
    cv.addEventListener('pointerleave', () => { hover = null; });
    cv.addEventListener('dblclick', e => {
      const n = hit(e.offsetX, e.offsetY);
      if (n) n.pinned = false;
    });
    cv.addEventListener('wheel', e => {
      e.preventDefault();
      const k = Math.min(6, Math.max(0.12, cam.k * Math.exp(-e.deltaY * 0.0015)));
      const x = wx(e.offsetX), y = wy(e.offsetY);
      cam.k = k;
      cam.x = x - (e.offsetX - W / 2) / k; cam.y = y - (e.offsetY - H / 2) / k;
      Object.assign(camGoal, cam); camAnim = false;
    }, { passive: false });
  }

  // ---- public actions -------------------------------------------------------
  function setFocus(id, center = true) {
    focus = nodes[id]; path = null; found = null;
    if (center) { const k = Math.max(cam.k, 1.4), [ox, oy] = offset(k); moveCam(focus.x - ox, focus.y - oy, k); }
    cb.onFocus(id);
  }

  function tracePath(from, to) {
    const prev = new Map([[from, -1]]), q = [from];
    while (q.length) {
      const v = q.shift();
      if (v === to) break;
      for (const w of adj[v]) if (!prev.has(w) && !hidden.has(nodes[w].type)) { prev.set(w, v); q.push(w); }
    }
    if (!prev.has(to)) { path = null; cb.onPath(null, nodes[from], nodes[to]); return; }
    const ids = [];
    for (let v = to; v !== -1; v = prev.get(v)) ids.unshift(v);
    const es = new Set();
    for (let i = 1; i < ids.length; i++) es.add(ekey(ids[i - 1], ids[i]));
    path = { nodes: new Set(ids), edges: es, from, to };
    cb.onPath(ids.map(i => nodes[i]), nodes[from], nodes[to]);
  }

  function clear() { focus = null; path = null; found = null; cb.onFocus(null); }

  function highlight(ids) {
    found = ids && ids.length ? new Set(ids) : null;
    path = null;
    if (found) {
      const pts = ids.map(i => nodes[i]);
      const x = pts.reduce((s, n) => s + n.x, 0) / pts.length, y = pts.reduce((s, n) => s + n.y, 0) / pts.length;
      const k = Math.max(cam.k, 1.1), [ox, oy] = offset(k);
      moveCam(x - ox, y - oy, k);
    }
  }

  function setHidden(set) { hidden = new Set(set); if (focus && hidden.has(focus.type)) clear(); }

  return {
    init, load, fit, setInsets: v => { inset = v; }, setFocus, highlight, setHidden, clear, colorFor,
    nodeByTitle: t => nodes.find(n => n.title.toLowerCase() === t.toLowerCase()),
    get focused() { return focus; },
  };
})();
