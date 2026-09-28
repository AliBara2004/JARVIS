// Brain graph in 3D: the JARVIS core at the centre of a holographic memory network.
// Same public API as graph.js (the 2D fallback), so app.js doesn't care which one runs.
//
//   core     — layered violet/pink object at the origin: ticked outer ring, HUD arcs, gyroscope rings,
//              particle field, fresnel sphere, wireframe icosahedron, emblem, waveform, pulse
//   memories — every note is a point in a depth-faded field; the most connected, and any that
//              are relevant / hovered / focused, become full data structures (hero objects)
//   network  — note↔note links arc outward; beams join the core to relevant memories, with
//              light flowing in (recall) or out (responding) and particles travelling along them
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

const Graph3D = (() => {
  // ---- tuning ---------------------------------------------------------------
  const CORE_R = 11;              // outer ring radius (world units)
  const SHELL_MIN = 21;           // most-connected memories orbit here...
  const LINK_LEN = 9;
  const REPEL = 60;
  const CUT = 22;                 // repulsion cutoff and grid cell size
  const DAMP = 0.82, COOL = 0.985, ALPHA_FLOOR = 0.008;
  const HERO_MAX = 30;            // full 3D data structures on screen at once
  const HERO_TOP = 16;            // ...of which this many are simply the best-connected notes
  const LABEL_MAX = 28;
  const TRAVELERS = 180;
  const BEAM_SEG = 28, EDGE_SEG = 14;
  const FOV = 42;
  const IDLE_SPIN = 0.018;        // rad/s camera drift when nobody's touching it
  const BG = 0x07060c;

  // The JARVIS palette: violet through pink (same colours as the 2D graph).
  const TYPE_COLORS = {
    trade: '#f472b6', account: '#fb7185', trading: '#f9a8d4', setup: '#e879f9',
    tool: '#d8b4fe', offer: '#c084fc', prospect: '#a78bfa', niche: '#818cf8',
    workflow: '#67e8f9', tech: '#7dd3fc', person: '#f0abfc', meeting: '#fda4af',
    idea: '#f5d0fe', daily: '#6f5a92', hub: '#ffffff', topic: '#a5b4fc',
    video: '#ff6ad5', content: '#ff9bd2', script: '#ff85c8', journal: '#b794f6', note: '#e9d5ff', wiki: '#c4b5fd', source: '#9d8cc9', log: '#7c6a9e', 'wiki-index': '#ffffff', 'wiki-log': '#6f5a92', workout: '#f0abfc',
  };
  // Three looks. Violet is JARVIS's own and the default; amber is the Stark workshop, cyan the lab.
  const THEMES = {
    violet: { bg: 0x07080c, ring: '#c4b5fd', ring2: '#8b5cf6', ticks: '#f472b6', arcs: '#e9d5ff', marker: '#ec4899', faint: '#7c3aed',
              gyro: ['#a78bfa', '#fbcfe8', '#e879f9'], beads: ['#f5ecff', '#f5ecff', '#ec4899'], core: '#e879f9', icosa: '#e9d5ff',
              glow: '#f0abfc', emblem: '#ffffff', emblemRing: '#fbcfe8', wave: '#f9a8d4', pulse: '#f0abfc', grid: '#6d3fb0',
              pA: '#a78bfa', pB: '#f266b8', shell: '#c084fc', shell2: '#f472b6', beam: '#f272cc', hue: null },
    amber:  { bg: 0x0a0705, ring: '#ffd27a', ring2: '#ff8c00', ticks: '#ffb347', arcs: '#ffe3a3', marker: '#ff3300', faint: '#b35900',
              gyro: ['#ffae42', '#ffe9b0', '#ff7a1a'], beads: ['#fff3d6', '#fff3d6', '#ff3300'], core: '#ffb347', icosa: '#ffe3a3',
              glow: '#ffc266', emblem: '#fff6e0', emblemRing: '#ffd27a', wave: '#ffcc66', pulse: '#ffd27a', grid: '#7a4a12',
              pA: '#ffb847', pB: '#ff6a1a', shell: '#ffb000', shell2: '#ff5a00', beam: '#ffbf5a', hue: [18, 36] },
    cyan:   { bg: 0x03070c, ring: '#9be8ff', ring2: '#0066ff', ticks: '#00f0ff', arcs: '#c9f5ff', marker: '#8b5cf6', faint: '#0a4fb3',
              gyro: ['#00f0ff', '#d6fbff', '#3d7bff'], beads: ['#e6fdff', '#e6fdff', '#8b5cf6'], core: '#00d8ff', icosa: '#c9f5ff',
              glow: '#7ff3ff', emblem: '#ffffff', emblemRing: '#aef6ff', wave: '#7ff3ff', pulse: '#9be8ff', grid: '#11507a',
              pA: '#5ae8ff', pB: '#4d7dff', shell: '#00e5ff', shell2: '#3d7bff', beam: '#80f2ff', hue: [182, 48] },
  };
  let theme = 'violet', T = THEMES.violet;
  const R = {};                                   // role → materials, recoloured by applyTheme
  const tag = (role, m) => ((R[role] ||= []).push(m), m);

  function colorFor(type) {
    let h = 0;
    for (const c of type) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    if (T.hue) {
      const money = /offer|prospect|trade|account|trading/.test(type);   // the hottest colour goes to money
      return `hsl(${T.hue[0] + (money ? 0 : 8 + (h % T.hue[1]))}, ${money ? 100 : 85}%, ${money ? 62 : 66 + (h % 12)}%)`;
    }
    if (TYPE_COLORS[type]) return TYPE_COLORS[type];
    return `hsl(${260 + (h % 70)}, 80%, 72%)`;
  }

  const STATES = {        //  spin   inflow  core  particles  wave  beams
    idle:      { d: 'Standing by',             spin: 1.0, inflow: 0.0, core: 0.75, speed: 0.5, wave: 0, beam: 0.55 },
    standby:   { d: 'Listening for "Hey Jarvis"', spin: 1.0, inflow: 0.0, core: 0.8, speed: 0.55, wave: 0.35, beam: 0.35 },
    listening: { d: 'Listening',               spin: 1.4, inflow: 0.1, core: 1.0, speed: 0.9, wave: 1, beam: 0.4 },
    thinking:  { d: 'Working through it',                spin: 3.2, inflow: 1.0, core: 1.25, speed: 1.8, wave: 0, beam: 0.6 },
    memory:    { d: 'Searching memory',        spin: 2.4, inflow: 0.7, core: 1.15, speed: 1.4, wave: 0, beam: 1 },
    speaking:  { d: 'Responding',              spin: 0.9, inflow: 0.0, core: 1.05, speed: 0.7, wave: 0.8, beam: 0.9 },
  };

  // ---- state ----------------------------------------------------------------
  let renderer, scene, camera, cv, overlay, W = 1, H = 1, dpr = 1;
  let nodes = [], edges = [], adj = [], byDeg = [];
  let hover = null, focus = null, path = null, found = null, hidden = new Set();
  let cb = { onFocus() {}, onPath() {} };
  let inset = { l: 0, r: 0, t: 0, b: 0 }, insetGoal = { l: 0, r: 0, t: 0, b: 0 };
  let alpha = 1, state = 'idle', level = 0, pulseT = -1;
  const S = { ...STATES.idle };                 // current (eased) state values
  let time = 0, lastT = performance.now();

  const cam = { theta: 0.6, phi: 1.2, dist: 90, target: new THREE.Vector3(), goal: null };
  const view = { theta: 0.6, phi: 1.2, dist: 90, target: new THREE.Vector3() };
  let lastTouch = -1e9, lastMove = -1e9, mouse = { x: 0, y: 0, sx: 0, sy: 0 }, drag = null, locked = false;

  // scene parts
  let core, coreBill, fresnelCore, icosa, gyro = [], outer, arcs, wave, pulse, emblem, coreGlow, particles, dataShell;
  let composer, bloom, lens, poke = 0, preview = null, fps = 60, frames = 0, fpsT = 0;
  let pointsGeo, pointsMat, edgeGeo, edgeMat, beamGeo, beamMat, travGeo, travMat;
  let heroes = [], heroByNode = new Map();
  let labels = [], card, coreTag;
  let travelers = [];
  let screen = new Float32Array(0);           // projected x, y, depth per node

  // ---- shaders --------------------------------------------------------------
  // Everything is additive light on black, so "fog" = fade to nothing with distance.
  const FADE = /* glsl */`
    uniform float uFade;
    float fade(float d) { float f = uFade * max(d - 30.0, 0.0); return exp(-f * f); }`;

  function fresnelMaterial(color, opts = {}) {
    return new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color(color) }, uI: { value: opts.i ?? 1 }, uOp: { value: 1 },
                  uTime: { value: 0 }, uFade: { value: 0.012 }, uScan: { value: opts.scan ?? 1 } },
      vertexShader: `
        varying vec3 vN; varying vec3 vV; varying vec3 vW; varying float vD;
        void main() {
          vec4 wp = modelMatrix * vec4(position, 1.0); vW = wp.xyz;
          vec4 mv = viewMatrix * wp; vD = -mv.z;
          vN = normalize(mat3(modelMatrix) * normal); vV = normalize(cameraPosition - wp.xyz);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `
        uniform vec3 uColor; uniform float uI, uOp, uTime, uScan; varying vec3 vN; varying vec3 vV; varying vec3 vW; varying float vD;
        ${FADE}
        void main() {
          float f = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 2.4);
          float scan = 1.0 - uScan * 0.22 * (0.5 + 0.5 * sin(vW.y * 7.0 - uTime * 2.2));
          vec3 c = uColor * (0.05 + f * 1.25) * uI * scan;
          gl_FragColor = vec4(c * fade(vD) * uOp, 1.0);
        }`,
      transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    });
  }

  function lineMaterial(color, opacity) {
    return new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color(color) }, uOp: { value: opacity }, uFade: { value: 0.012 } },
      vertexShader: `varying float vD; void main(){ vec4 mv = modelViewMatrix * vec4(position,1.0); vD=-mv.z; gl_Position = projectionMatrix*mv; }`,
      fragmentShader: `uniform vec3 uColor; uniform float uOp; varying float vD; ${FADE}
        void main(){ gl_FragColor = vec4(uColor * uOp * fade(vD), 1.0); }`,
      transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    });
  }

  // Soft round point sprites whose size is in world units (shrink with distance).
  const POINT_VS = `
    attribute vec3 aColor; attribute float aSize; attribute float aAlpha;
    uniform float uScale; varying vec3 vC; varying float vA; varying float vD;
    void main() {
      vec4 mv = modelViewMatrix * vec4(position, 1.0); vD = -mv.z;
      gl_PointSize = max(aSize * uScale / vD, aSize > 0.0 ? 1.5 : 0.0);
      vC = aColor; vA = aAlpha; gl_Position = projectionMatrix * mv;
    }`;
  const POINT_FS = `
    varying vec3 vC; varying float vA; varying float vD; ${FADE}
    void main() {
      float d = length(gl_PointCoord - 0.5) * 2.0;
      if (d > 1.0) discard;
      float core = smoothstep(0.42, 0.0, d), halo = smoothstep(1.0, 0.3, d) * 0.28;
      gl_FragColor = vec4(vC * (core + halo) * vA * fade(vD), 1.0);
    }`;
  function pointMaterial() {
    return new THREE.ShaderMaterial({
      uniforms: { uScale: { value: 300 }, uFade: { value: 0.012 } }, vertexShader: POINT_VS, fragmentShader: POINT_FS,
      transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    });
  }

  // Curves with light flowing along them: aT runs 0→1 along each curve, aDir picks the flow direction.
  function flowMaterial() {
    return new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uFade: { value: 0.012 } },
      vertexShader: `
        attribute float aT; attribute float aAlpha; attribute float aDir; attribute vec3 aColor;
        varying float vT; varying float vA; varying float vDir; varying vec3 vC; varying float vD;
        void main(){ vT=aT; vA=aAlpha; vDir=aDir; vC=aColor; vec4 mv=modelViewMatrix*vec4(position,1.0); vD=-mv.z; gl_Position=projectionMatrix*mv; }`,
      fragmentShader: `
        uniform float uTime; varying float vT; varying float vA; varying float vDir; varying vec3 vC; varying float vD; ${FADE}
        void main(){
          float ends = smoothstep(0.0, 0.08, vT) * smoothstep(1.0, 0.9, vT);
          float flow = vDir == 0.0 ? 0.0 : pow(0.5 + 0.5 * sin((vT * 9.0 + uTime * 2.6 * vDir) * 3.14159), 8.0);
          gl_FragColor = vec4(vC * vA * ends * (0.35 + 1.3 * flow) * fade(vD), 1.0);
        }`,
      transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    });
  }

  function glowTexture() {
    const c = document.createElement('canvas'); c.width = c.height = 128;
    const g = c.getContext('2d'), grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grd.addColorStop(0, 'rgba(255,240,250,1)'); grd.addColorStop(0.18, 'rgba(244,164,222,.55)');
    grd.addColorStop(0.5, 'rgba(139,92,246,.14)'); grd.addColorStop(1, 'rgba(0,0,0,0)');
    g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
    const t = new THREE.CanvasTexture(c); return t;
  }

  // ---- geometry helpers -----------------------------------------------------
  function circlePts(r, n = 128, a0 = 0, a1 = Math.PI * 2) {
    const pts = [];
    for (let i = 0; i <= n; i++) { const a = a0 + (a1 - a0) * i / n; pts.push(new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r)); }
    return pts;
  }
  const tagged = (role, obj) => (tag(role, obj.material), obj);
  const lineLoop = (r, color, op, n) => new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(circlePts(r, n).slice(0, -1)), lineMaterial(color, op));

  // ---- the core -------------------------------------------------------------
  function buildCore() {
    core = new THREE.Group(); scene.add(core);

    // outer ring + tick marks (a flat instrument dial, slightly tilted)
    outer = new THREE.Group(); outer.rotation.x = 0.18; core.add(outer);
    outer.add(tagged('ring', lineLoop(CORE_R, '#c4b5fd', 0.6, 256)));
    outer.add(tagged('ring2', lineLoop(CORE_R + 0.9, '#8b5cf6', 0.22, 256)));
    const tk = [];
    for (let i = 0; i < 180; i++) {
      const a = i / 180 * Math.PI * 2, long = i % 15 === 0, mid = i % 5 === 0;
      const r0 = CORE_R + 0.15, r1 = CORE_R + (long ? 1.3 : mid ? 0.7 : 0.4);
      tk.push(Math.cos(a) * r0, 0, Math.sin(a) * r0, Math.cos(a) * r1, 0, Math.sin(a) * r1);
    }
    const tg = new THREE.BufferGeometry(); tg.setAttribute('position', new THREE.Float32BufferAttribute(tk, 3));
    outer.add(new THREE.LineSegments(tg, tag('ticks', lineMaterial('#f472b6', 0.5))));

    // segmented HUD arcs, counter-rotating, one warm marker
    arcs = new THREE.Group(); arcs.rotation.x = 0.18; core.add(arcs);
    const segs = [[0.1, 1.2], [1.45, 2.1], [2.4, 3.9], [4.2, 4.5], [4.8, 6.0]];
    for (const [a0, a1] of segs) {
      arcs.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(circlePts(9.4, 48, a0, a1)), tag('arcs', lineMaterial('#e9d5ff', 0.5))));
    }
    arcs.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(circlePts(9.9, 16, 3.95, 4.15)), tag('marker', lineMaterial('#ec4899', 1.0))));
    arcs.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(circlePts(8.7, 96, 0, Math.PI * 1.55)), tag('faint', lineMaterial('#7c3aed', 0.35))));

    // gyroscope: three thin rings on different axes, each spinning on its own
    const tilt = [[1.2, 0.0, 0.3], [0.2, 0.9, 1.4], [2.1, 0.4, 0.7]];
    tilt.forEach(([x, y, z], i) => {
      const holder = new THREE.Group(); holder.rotation.set(x, y, z);
      const ring = new THREE.Mesh(new THREE.TorusGeometry(7.2 - i * 0.55, 0.035, 6, 160),
                                  tag('gyro' + i, fresnelMaterial(i === 1 ? '#fbcfe8' : i === 0 ? '#a78bfa' : '#e879f9', { i: 1.6, scan: 0 })));
      holder.add(ring); core.add(holder);
      // a bead riding each ring
      const bead = new THREE.Mesh(new THREE.SphereGeometry(0.13, 10, 10), tag('bead' + i, new THREE.MeshBasicMaterial({ color: i === 2 ? 0xec4899 : 0xf5ecff })));
      bead.position.x = 7.2 - i * 0.55; ring.add(bead);
      gyro.push({ holder, ring, speed: [0.35, -0.5, 0.27][i] });
    });

    // particle field: orbits on random planes; "inflow" spirals them into the centre
    const N = 900, seed = new Float32Array(N * 4), phase = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      seed[i * 4] = 3.2 + Math.pow(Math.random(), 0.7) * 5.2;   // radius
      seed[i * 4 + 1] = Math.random() * Math.PI * 2;            // start angle
      seed[i * 4 + 2] = (Math.random() - 0.5) * Math.PI;        // plane tilt
      seed[i * 4 + 3] = 0.3 + Math.random() * 0.9;              // speed
      phase[i] = Math.random();
    }
    const pg = new THREE.BufferGeometry();
    pg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * 3), 3));
    pg.setAttribute('aSeed', new THREE.BufferAttribute(seed, 4));
    pg.setAttribute('aPhase', new THREE.BufferAttribute(phase, 1));
    particles = new THREE.Points(pg, new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uInflow: { value: 0 }, uSpeed: { value: 0.5 }, uScale: { value: 300 }, uI: { value: 1 }, uFade: { value: 0.012 },
                  uColA: { value: new THREE.Color('#a78bfa') }, uColB: { value: new THREE.Color('#f266b8') } },
      vertexShader: `
        attribute vec4 aSeed; attribute float aPhase; uniform float uTime, uInflow, uSpeed, uScale; uniform vec3 uColA, uColB;
        varying float vA; varying float vD; varying vec3 vCol;
        void main() {
          vCol = mix(uColA, uColB, aPhase);
          float cyc = fract(aPhase + uTime * 0.22 * aSeed.w);
          float r = mix(aSeed.x, mix(aSeed.x * 1.3, 1.6, cyc), uInflow);
          float a = aSeed.y + uTime * aSeed.w * uSpeed * 0.6;
          vec3 p = vec3(cos(a) * r, 0.0, sin(a) * r);
          float t = aSeed.z, s = sin(t), c = cos(t);
          p = vec3(p.x, p.y * c - p.z * s, p.y * s + p.z * c);
          float u = aPhase * 6.2831, su = sin(u), cu = cos(u);
          p = vec3(p.x * cu + p.z * su, p.y, -p.x * su + p.z * cu);
          vec4 mv = modelViewMatrix * vec4(p, 1.0); vD = -mv.z;
          gl_PointSize = max(0.16 * uScale / vD, 1.0);
          vA = mix(0.35, 0.35 + 0.65 * cyc, uInflow) * (0.4 + 0.6 * aSeed.w);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `uniform float uI; varying float vA; varying float vD; varying vec3 vCol; ${FADE}
        void main(){ float d = length(gl_PointCoord - 0.5) * 2.0; if (d > 1.0) discard;
          gl_FragColor = vec4(vCol * smoothstep(1.0, 0.0, d) * vA * uI * fade(vD), 1.0); }`,
      transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    core.add(particles);

    // data shell: short latitude / longitude fragments on a sphere, each flickering on its own clock
    const F = 2600, fp = new Float32Array(F * 6), fs = new Float32Array(F * 2);
    for (let i = 0; i < F; i++) {
      const u = Math.random() * 2 - 1, t = Math.random() * Math.PI * 2, r = 5.1 + Math.random() * 0.9;
      const lat = Math.asin(u), len = 0.05 + Math.pow(Math.random(), 3) * 0.35, along = Math.random() < 0.6;
      const p0 = [lat, t], p1 = along ? [lat, t + len / Math.max(0.2, Math.cos(lat))] : [lat + len, t];
      const xyz = ([la, lo]) => [Math.cos(la) * Math.cos(lo) * r, Math.sin(la) * r, Math.cos(la) * Math.sin(lo) * r];
      fp.set([...xyz(p0), ...xyz(p1)], i * 6);
      const sd = Math.random(); fs[i * 2] = sd; fs[i * 2 + 1] = sd;
    }
    const fg = new THREE.BufferGeometry();
    fg.setAttribute('position', new THREE.BufferAttribute(fp, 3));
    fg.setAttribute('aSeed', new THREE.BufferAttribute(fs, 1));
    dataShell = new THREE.LineSegments(fg, tag('shellMat', new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uI: { value: 1 }, uFade: { value: 0.012 },
                  uCol: { value: new THREE.Color('#c084fc') }, uCol2: { value: new THREE.Color('#f472b6') } },
      vertexShader: `attribute float aSeed; varying float vS; varying float vD; varying float vY;
        void main(){ vS = aSeed; vY = position.y; vec4 mv = modelViewMatrix * vec4(position, 1.0); vD = -mv.z; gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `uniform float uTime, uI; uniform vec3 uCol, uCol2; varying float vS; varying float vD; varying float vY; ${FADE}
        void main(){
          float blink = step(0.35, fract(vS * 17.0 + uTime * (0.15 + vS * 0.5)));        // fragments switch on and off
          float band = 0.6 + 0.4 * sin(vY * 1.4 - uTime * 1.3 + vS * 6.0);                 // a slow sweep of light
          vec3 c = mix(uCol, uCol2, vS) * (0.25 + 0.75 * blink) * band;
          gl_FragColor = vec4(c * 0.55 * uI * fade(vD), 1.0);
        }`,
      transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    })));
    core.add(dataShell);

    // inner core: fresnel sphere (scan lines) + rotating wireframe icosahedron
    fresnelCore = new THREE.Mesh(new THREE.SphereGeometry(2.3, 48, 48), tag('core', fresnelMaterial('#e879f9', { i: 1.3 })));
    core.add(fresnelCore);
    icosa = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(3.1, 1)), tag('icosa', lineMaterial('#e9d5ff', 0.3)));
    core.add(icosa);

    coreGlow = new THREE.Sprite(tag('glow', new THREE.SpriteMaterial({ map: glowTexture(), color: 0xf0abfc, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.55 })));
    coreGlow.scale.set(9, 9, 1); core.add(coreGlow);

    // billboarded parts: emblem, voice waveform, outward pulse
    coreBill = new THREE.Group(); core.add(coreBill);
    emblem = new THREE.Group(); coreBill.add(emblem);
    const tri = [0, 1, 2].map(i => { const a = -Math.PI / 2 + i * Math.PI * 2 / 3; return new THREE.Vector3(Math.cos(a) * 1.05, -Math.sin(a) * 1.05, 0); });
    emblem.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(tri), tag('emblem', lineMaterial('#ffffff', 0.9))));
    const ring = circlePts(1.45, 64).map(p => new THREE.Vector3(p.x, p.z, 0));
    emblem.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(ring), tag('emblemRing', lineMaterial('#fbcfe8', 0.6))));
    const wg = new THREE.BufferGeometry(); wg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(160 * 3), 3));
    wave = new THREE.LineLoop(wg, tag('wave', lineMaterial('#f9a8d4', 0))); coreBill.add(wave);
    pulse = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(circlePts(1, 96).slice(0, -1).map(p => new THREE.Vector3(p.x, p.z, 0))),
                               tag('pulse', lineMaterial('#f0abfc', 0))); coreBill.add(pulse);
  }

  function applyTheme(name) {
    theme = THEMES[name] ? name : 'violet';
    T = THEMES[theme];
    const set = (role, color) => (R[role] || []).forEach(m => (m.uniforms?.uColor?.value || m.color).set(color));
    for (const k of ['ring', 'ring2', 'ticks', 'arcs', 'marker', 'faint', 'core', 'icosa', 'glow', 'emblem', 'emblemRing', 'wave', 'pulse', 'grid']) set(k, T[k]);
    T.gyro.forEach((c, i) => set('gyro' + i, c));
    T.beads.forEach((c, i) => set('bead' + i, c));
    particles.material.uniforms.uColA.value.set(T.pA); particles.material.uniforms.uColB.value.set(T.pB);
    for (const m of R.shellMat || []) { m.uniforms.uCol.value.set(T.shell); m.uniforms.uCol2.value.set(T.shell2); }
    beamRGB.set(T.beam);
    renderer.setClearColor(T.bg, 1);
    for (const n of nodes) n.color.set(colorFor(n.type));
    for (const h of heroes) if (h.node) paintHero(h);
  }
  const beamRGB = new THREE.Color('#f272cc');

  // ---- environment: floor grid + distant dust ----------------------------------
  function buildEnvironment() {
    const g = [], y = -30;
    for (let r = 12; r <= 120; r += 12) {
      const pts = circlePts(r, 96);
      for (let i = 0; i < pts.length - 1; i++) g.push(pts[i].x, y, pts[i].z, pts[i + 1].x, y, pts[i + 1].z);
    }
    for (let i = 0; i < 24; i++) {
      const a = i / 24 * Math.PI * 2;
      g.push(Math.cos(a) * 12, y, Math.sin(a) * 12, Math.cos(a) * 120, y, Math.sin(a) * 120);
    }
    const gg = new THREE.BufferGeometry(); gg.setAttribute('position', new THREE.Float32BufferAttribute(g, 3));
    const grid = new THREE.LineSegments(gg, tag('grid', lineMaterial('#6d3fb0', 0.16))); scene.add(grid);

    const N = 1400, pos = new Float32Array(N * 3), col = new Float32Array(N * 3), size = new Float32Array(N), al = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      const u = Math.random() * 2 - 1, t = Math.random() * Math.PI * 2, r = 140 + Math.random() * 260;
      pos.set([Math.sqrt(1 - u * u) * Math.cos(t) * r, u * r * 0.6, Math.sqrt(1 - u * u) * Math.sin(t) * r], i * 3);
      col.set(Math.random() < 0.5 ? [0.72, 0.6, 0.98] : [0.95, 0.55, 0.8], i * 3); size[i] = 0.6 + Math.random() * 1.2; al[i] = 0.15 + Math.random() * 0.35;
    }
    const dg = new THREE.BufferGeometry();
    dg.setAttribute('position', new THREE.BufferAttribute(pos, 3)); dg.setAttribute('aColor', new THREE.BufferAttribute(col, 3));
    dg.setAttribute('aSize', new THREE.BufferAttribute(size, 1)); dg.setAttribute('aAlpha', new THREE.BufferAttribute(al, 1));
    const dm = pointMaterial(); dm.uniforms.uFade.value = 0.0025;
    scene.add(new THREE.Points(dg, dm));
    envMats.push(dm);
  }
  const envMats = [];

  // ---- memories: data --------------------------------------------------------
  function build(data) {
    const maxDeg = Math.max(1, ...data.nodes.map(n => n.deg));
    const spread = 10 + Math.sqrt(data.nodes.length) * 1.9;
    nodes = data.nodes.map(n => ({
      ...n, p: new THREE.Vector3(), v: new THREE.Vector3(), color: new THREE.Color(colorFor(n.type)),
      rt: SHELL_MIN + (1 - Math.sqrt(n.deg / maxDeg)) * spread + (n.deg === 0 ? 6 : 0),
      size: 0.9 + Math.sqrt(n.deg) * 0.45, lit: 0, hov: 0, vis: 1,
    }));
    edges = data.edges.map(([a, b]) => ({ a: nodes[a], b: nodes[b] }));
    adj = nodes.map(() => []);
    for (const e of edges) { adj[e.a.id].push(e.b.id); adj[e.b.id].push(e.a.id); }
    byDeg = [...nodes].sort((p, q) => q.deg - p.deg);
    screen = new Float32Array(nodes.length * 3);
    buildBuffers();
  }

  function seedPositions() {
    // Fibonacci sphere, hubs first so they take the band nearest the camera's eye line
    byDeg.forEach((n, i) => {
      const k = (i + 0.5) / Math.max(1, nodes.length), y = 1 - 2 * k, r = Math.sqrt(1 - y * y), t = i * 2.39996;
      n.p.set(Math.cos(t) * r * n.rt, y * n.rt * 0.62, Math.sin(t) * r * n.rt);
    });
  }

  // ---- memories: physics -------------------------------------------------------
  const tmp = new THREE.Vector3();
  function tick() {
    const grid = new Map(), key = (x, y, z) => x + ',' + y + ',' + z;
    for (const n of nodes) {
      const k = key(Math.floor(n.p.x / CUT), Math.floor(n.p.y / CUT), Math.floor(n.p.z / CUT));
      (grid.get(k) || grid.set(k, []).get(k)).push(n);
    }
    const cut2 = CUT * CUT;
    for (const n of nodes) {
      const gx = Math.floor(n.p.x / CUT), gy = Math.floor(n.p.y / CUT), gz = Math.floor(n.p.z / CUT);
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
        const cell = grid.get(key(gx + dx, gy + dy, gz + dz));
        if (!cell) continue;
        for (const m of cell) {
          if (m.id <= n.id) continue;
          tmp.subVectors(n.p, m.p);
          let d2 = tmp.lengthSq();
          if (d2 > cut2) continue;
          if (d2 < 0.01) { tmp.set(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5); d2 = 0.3; }
          const f = REPEL * alpha / (d2 + 3) / Math.sqrt(d2);
          n.v.addScaledVector(tmp, f); m.v.addScaledVector(tmp, -f);
        }
      }
    }
    for (const { a, b } of edges) {
      tmp.subVectors(b.p, a.p);
      const d = tmp.length() || 1, s = (d - LINK_LEN) / d * alpha * 0.06;
      a.v.addScaledVector(tmp, s); b.v.addScaledVector(tmp, -s);
    }
    for (const n of nodes) {
      const r = n.p.length() || 1;
      n.v.addScaledVector(n.p, (n.rt - r) / r * 0.05 * Math.max(alpha, 0.05));   // stay on its shell
      n.v.y -= n.p.y * 0.004 * alpha;                                            // a wide ellipsoid suits a wide screen
      n.v.multiplyScalar(DAMP);
      n.p.add(n.v);
    }
    alpha = Math.max(ALPHA_FLOOR, alpha * COOL);
  }

  // ---- memories: buffers --------------------------------------------------------
  function dyn(geo, name, arr, size) {
    const a = new THREE.BufferAttribute(arr, size); a.setUsage(THREE.DynamicDrawUsage); geo.setAttribute(name, a); return a;
  }
  function buildBuffers() {
    for (const o of [pointsGeo, edgeGeo]) o?.dispose();
    const n = nodes.length;
    pointsGeo = new THREE.BufferGeometry();
    dyn(pointsGeo, 'position', new Float32Array(n * 3), 3);
    dyn(pointsGeo, 'aColor', new Float32Array(n * 3), 3);
    dyn(pointsGeo, 'aSize', new Float32Array(n), 1);
    dyn(pointsGeo, 'aAlpha', new Float32Array(n), 1);
    if (!pointsMat) { pointsMat = pointMaterial(); }
    if (memPoints) scene.remove(memPoints);
    memPoints = new THREE.Points(pointsGeo, pointsMat); memPoints.frustumCulled = false; scene.add(memPoints);

    const v = edges.length * EDGE_SEG * 2;
    edgeGeo = new THREE.BufferGeometry();
    dyn(edgeGeo, 'position', new Float32Array(v * 3), 3);
    dyn(edgeGeo, 'aColor', new Float32Array(v * 3), 3);
    dyn(edgeGeo, 'aAlpha', new Float32Array(v), 1);
    const at = new Float32Array(v), ad = new Float32Array(v);
    for (let e = 0; e < edges.length; e++) for (let s = 0; s < EDGE_SEG; s++) {
      at[(e * EDGE_SEG + s) * 2] = s / EDGE_SEG; at[(e * EDGE_SEG + s) * 2 + 1] = (s + 1) / EDGE_SEG;
    }
    edgeGeo.setAttribute('aT', new THREE.BufferAttribute(at, 1));
    dyn(edgeGeo, 'aDir', ad, 1);
    if (!edgeMat) edgeMat = flowMaterial();
    if (edgeLines) scene.remove(edgeLines);
    edgeLines = new THREE.LineSegments(edgeGeo, edgeMat); edgeLines.frustumCulled = false; scene.add(edgeLines);
  }
  let memPoints, edgeLines;

  function buildBeams() {
    const maxBeams = HERO_MAX, v = maxBeams * BEAM_SEG * 2;
    beamGeo = new THREE.BufferGeometry();
    dyn(beamGeo, 'position', new Float32Array(v * 3), 3);
    dyn(beamGeo, 'aColor', new Float32Array(v * 3), 3);
    dyn(beamGeo, 'aAlpha', new Float32Array(v), 1);
    dyn(beamGeo, 'aDir', new Float32Array(v), 1);
    const at = new Float32Array(v);
    for (let b = 0; b < maxBeams; b++) for (let s = 0; s < BEAM_SEG; s++) {
      at[(b * BEAM_SEG + s) * 2] = s / BEAM_SEG; at[(b * BEAM_SEG + s) * 2 + 1] = (s + 1) / BEAM_SEG;
    }
    beamGeo.setAttribute('aT', new THREE.BufferAttribute(at, 1));
    beamMat = flowMaterial();
    const lines = new THREE.LineSegments(beamGeo, beamMat); lines.frustumCulled = false; scene.add(lines);

    travGeo = new THREE.BufferGeometry();
    dyn(travGeo, 'position', new Float32Array(TRAVELERS * 3), 3);
    dyn(travGeo, 'aColor', new Float32Array(TRAVELERS * 3), 3);
    dyn(travGeo, 'aSize', new Float32Array(TRAVELERS), 1);
    dyn(travGeo, 'aAlpha', new Float32Array(TRAVELERS), 1);
    travMat = pointMaterial();
    const tp = new THREE.Points(travGeo, travMat); tp.frustumCulled = false; scene.add(tp);
    travelers = Array.from({ length: TRAVELERS }, () => ({ live: false }));
  }

  // Curves. Core beams leave the core's surface and arc up into the memory; note links bow outward.
  const P0 = new THREE.Vector3(), P1 = new THREE.Vector3(), P2 = new THREE.Vector3(), UP = new THREE.Vector3(0, 1, 0);
  function beamCurve(n) {
    const target = heroByNode.get(n.id)?.group.position || n.p;
    P2.copy(target); P0.copy(target).normalize().multiplyScalar(3.4);
    P1.addVectors(P0, P2).multiplyScalar(0.5);
    tmp.crossVectors(P2, UP).normalize().multiplyScalar(P2.length() * 0.18);
    P1.add(tmp).addScaledVector(UP, P2.length() * 0.12);
  }
  function edgeCurve(a, b) {
    P0.copy(heroByNode.get(a.id)?.group.position || a.p); P2.copy(heroByNode.get(b.id)?.group.position || b.p);
    P1.addVectors(P0, P2).multiplyScalar(0.5);
    const r = (P0.length() + P2.length()) / 2;
    P1.setLength(r * 1.12);
  }
  function bez(t, out) {
    const u = 1 - t;
    return out.set(u * u * P0.x + 2 * u * t * P1.x + t * t * P2.x, u * u * P0.y + 2 * u * t * P1.y + t * t * P2.y,
                   u * u * P0.z + 2 * u * t * P1.z + t * t * P2.z);
  }

  // ---- hero objects (full data structures) ----------------------------------------
  function makeHero() {
    const group = new THREE.Group();
    const nucleus = new THREE.Mesh(new THREE.OctahedronGeometry(0.55, 0), fresnelMaterial('#ffffff', { i: 1.8, scan: 0.6 }));
    const shellGeo = new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.25, 0));
    const shell = new THREE.LineSegments(shellGeo, lineMaterial('#ffffff', 0.55));
    const inner = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.OctahedronGeometry(0.85, 0)), lineMaterial('#ffffff', 0.25));
    const orbitHolder = new THREE.Group();
    const orbit = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(circlePts(1.9, 64).slice(0, -1)), lineMaterial('#ffffff', 0.18));
    orbitHolder.add(orbit);
    const dg = new THREE.BufferGeometry();
    dg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(8 * 3), 3));
    dg.setAttribute('aColor', new THREE.BufferAttribute(new Float32Array(8 * 3).fill(1), 3));
    dg.setAttribute('aSize', new THREE.BufferAttribute(new Float32Array(8).fill(0.28), 1));
    dg.setAttribute('aAlpha', new THREE.BufferAttribute(new Float32Array(8), 1));
    const dm = pointMaterial();
    const dots = new THREE.Points(dg, dm); orbitHolder.add(dots);
    // a vertical data tether: metadata line dropping to the node's "floor" position
    const tether = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, -1.4, 0), new THREE.Vector3(0, -3.2, 0)]),
                                  lineMaterial('#ffffff', 0.12));
    group.add(nucleus, shell, inner, orbitHolder, tether);
    scene.add(group);
    group.visible = false;
    return { group, nucleus, shell, inner, orbit, orbitHolder, dots, dm, tether, node: null, s: 0, spin: Math.random() * 6 };
  }

  function assignHeroes(lit) {
    const want = [];
    const add = n => { if (n && !hidden.has(n.type) && !want.includes(n) && want.length < HERO_MAX) want.push(n); };
    add(focus); add(hover);
    if (path) for (const i of path.nodes) add(nodes[i]);
    if (found) for (const i of found) add(nodes[i]);
    if (hover) for (const i of adj[hover.id]) add(nodes[i]);
    if (focus) for (const i of adj[focus.id]) add(nodes[i]);
    let top = 0;
    for (const n of byDeg) { if (top >= HERO_TOP) break; if (!hidden.has(n.type)) { add(n); top++; } }
    const wanted = new Set(want.map(n => n.id));
    for (const h of heroes) if (h.node && !wanted.has(h.node.id) && h.s < 0.02) { heroByNode.delete(h.node.id); h.node = null; h.group.visible = false; }
    for (const n of want) {
      if (heroByNode.has(n.id)) continue;
      const h = heroes.find(x => !x.node);
      if (!h) break;
      h.node = n; h.s = 0; heroByNode.set(n.id, h); h.group.visible = true;
      paintHero(h);
      h.orbitHolder.rotation.set(Math.random() * 1.2 - 0.6, 0, Math.random() * 1.2 - 0.6);
    }
    return wanted;
  }

  function paintHero(h) {
    const n = h.node, c = n.color;
    for (const m of [h.nucleus.material, h.shell.material, h.inner.material, h.orbit.material, h.tether.material]) m.uniforms.uColor.value.copy(c);
    const count = Math.min(8, 2 + Math.round(Math.sqrt(n.deg) * 1.5));   // importance: more orbiting data
    const al = h.dots.geometry.attributes.aAlpha, col = h.dots.geometry.attributes.aColor;
    for (let i = 0; i < 8; i++) {
      al.array[i] = i < count ? 0.9 : 0;
      col.setXYZ(i, c.r * 0.6 + 0.4, c.g * 0.6 + 0.4, c.b * 0.6 + 0.4);
    }
    al.needsUpdate = col.needsUpdate = true;
  }

  // ---- overlay: labels, hover card, core status -------------------------------------
  function buildOverlay() {
    overlay = document.createElement('div');
    overlay.className = 'hud3d';
    overlay.innerHTML = `<div class="hud-core" id="hud-core"><i></i><b>IDLE</b><span>Standing by</span></div>
      <div class="hud-card" id="hud-card" hidden></div>`;
    cv.insertAdjacentElement('afterend', overlay);
    coreTag = overlay.querySelector('#hud-core');
    card = overlay.querySelector('#hud-card');
    for (let i = 0; i < LABEL_MAX; i++) {
      const el = document.createElement('div'); el.className = 'hud-label'; el.style.opacity = 0;
      overlay.appendChild(el); labels.push({ el, text: '' });
    }
  }

  // ---- setup ------------------------------------------------------------------
  function init(canvas, data, callbacks) {
    cv = canvas;
    Object.assign(cb, callbacks || {});
    renderer = new THREE.WebGLRenderer({ canvas: cv, antialias: true, powerPreference: 'high-performance' });
    renderer.setClearColor(BG, 1);
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(FOV, 1, 0.5, 1200);
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.38, 0.5, 0.32);   // subtle: glow on the brightest lines only
    composer.addPass(bloom);
    lens = new ShaderPass({
      uniforms: { tDiffuse: { value: null }, uAmount: { value: 0.0022 }, uVignette: { value: 0.55 } },
      vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
      fragmentShader: `uniform sampler2D tDiffuse; uniform float uAmount, uVignette; varying vec2 vUv;
        void main(){
          vec2 d = vUv - 0.5; float r2 = dot(d, d);
          vec2 off = d * uAmount * (1.0 + r2 * 6.0);                       // stronger toward the edges, like real glass
          vec3 c = vec3(texture2D(tDiffuse, vUv + off).r, texture2D(tDiffuse, vUv).g, texture2D(tDiffuse, vUv - off).b);
          float vig = smoothstep(0.85, 0.2, length(d) * 1.15);
          gl_FragColor = vec4(c * mix(1.0, vig, uVignette), 1.0);
        }`,
    });
    composer.addPass(lens);
    composer.addPass(new OutputPass());
    buildEnvironment();
    buildCore();
    buildBeams();
    for (let i = 0; i < HERO_MAX; i++) heroes.push(makeHero());
    buildOverlay();
    build(data);
    seedPositions();
    for (let i = 0; i < 260; i++) tick();
    resize();
    fit(false);
    window.addEventListener('resize', resize);
    bindInput();
    renderer.setAnimationLoop(frame);
  }

  function load(data) {
    const old = new Map(nodes.map(n => [n.rel, n.p.clone()]));
    for (const h of heroes) { h.node = null; h.group.visible = false; }
    heroByNode.clear();
    build(data);
    for (const n of nodes) {
      const o = old.get(n.rel);
      if (o) { n.p.copy(o); continue; }
      const nb = adj[n.id].map(i => old.get(nodes[i].rel)).find(Boolean);
      if (nb) n.p.copy(nb).add(new THREE.Vector3(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5).multiplyScalar(4));
      else n.p.set(Math.random() - 0.5, (Math.random() - 0.5) * 0.6, Math.random() - 0.5).setLength(n.rt);
    }
    hover = focus = path = found = null;
    alpha = Math.max(alpha, 0.3);
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 1.75);
    W = cv.clientWidth || innerWidth; H = cv.clientHeight || innerHeight;
    renderer.setPixelRatio(dpr);
    renderer.setSize(W, H, false);
    composer.setPixelRatio(dpr);
    composer.setSize(W, H);
    bloom.resolution.set(W / 2, H / 2);
    camera.aspect = W / H;
    applyViewOffset();
    const scale = H * dpr / (2 * Math.tan(THREE.MathUtils.degToRad(FOV / 2)));
    for (const m of [pointsMat, travMat, particles.material, ...envMats, ...heroes.map(h => h.dm)]) if (m) m.uniforms.uScale.value = scale;
  }

  // Centre the core in the space the panels leave free, not the window.
  function applyViewOffset() {
    const cx = (inset.l + W - inset.r) / 2, cy = (inset.t + H - inset.b) / 2;
    camera.setViewOffset(W, H, -(cx - W / 2), -(cy - H / 2), W, H);
    camera.updateProjectionMatrix();
  }

  function fit(animate = true) {
    const vis = nodes.filter(n => !hidden.has(n.type));
    const R = Math.max(CORE_R + 4, ...vis.map(n => n.p.length())) + 6;
    const aw = Math.max(200, W - inset.l - inset.r), ah = Math.max(200, H - inset.t - inset.b);
    const half = THREE.MathUtils.degToRad(FOV / 2);
    const dv = R / Math.tan(half) * (H / ah), dh = R / (Math.tan(half) * (aw / H)) * 1.0;
    const dist = Math.min(260, Math.max(dv, dh) * 0.72);
    cam.goal = { target: new THREE.Vector3(), dist, phi: 1.22 };
    if (!animate) { cam.dist = view.dist = dist; cam.target.set(0, 0, 0); view.target.set(0, 0, 0); cam.phi = view.phi = 1.22; cam.goal = null; }
  }

  // ---- lit set ------------------------------------------------------------------
  function litSet() {
    if (hover) return new Set([hover.id, ...adj[hover.id]]);
    if (preview) return preview;
    if (path) return path.nodes;
    if (focus) return new Set([focus.id, ...adj[focus.id]]);
    if (found) return found;
    return null;
  }
  const ekey = (a, b) => a < b ? a + '-' + b : b + '-' + a;

  // ---- frame --------------------------------------------------------------------
  const V = new THREE.Vector3(), camDir = new THREE.Vector3(), white = new THREE.Color(1, 1, 1);
  // Idle and untouched: draw every other frame (30 fps) so the GPU is free for everything else on the PC.
  // Any movement, hover, speech or thinking puts it straight back to full rate.
  let skip = 0, isQuiet = false;
  const IDLE_AFTER_MS = 8000;
  function frame() {
    const quiet = (state === 'idle' || state === 'standby') && !hover && !drag && !cam.goal && pulseT < 0
      && performance.now() - lastTouch > IDLE_AFTER_MS && performance.now() - lastMove > IDLE_AFTER_MS;
    isQuiet = quiet;
    if (quiet && (++skip & 1)) return;
    const nowT = performance.now(), dt = Math.min((nowT - lastT) / 1000, 0.05);
    lastT = nowT;
    time += dt;
    tick();
    easeState(dt);
    easeInsets(dt);
    moveCamera(dt);
    const lit = litSet();
    const wanted = assignHeroes(lit);
    animateCore(dt);
    updateMemories(dt, lit, wanted);
    updateEdges(lit);
    updateBeams(lit);
    updateTravelers(dt, lit);
    composer.render();
    frames++; fpsT += dt;
    if (fpsT >= 1) { fps = frames / fpsT; frames = 0; fpsT = 0; }
    updateOverlay(lit);
  }

  // panels opening or closing slide the core over smoothly instead of jumping
  function easeInsets(dt) {
    const k = 1 - Math.exp(-dt * 6);
    let moved = false;
    for (const key of ['l', 'r', 't', 'b']) {
      const d = insetGoal[key] - inset[key];
      if (Math.abs(d) > 0.5) { inset[key] += d * k; moved = true; } else inset[key] = insetGoal[key];
    }
    if (moved) applyViewOffset();
  }

  function easeState(dt) {
    const T = STATES[state] || STATES.idle, k = 1 - Math.exp(-dt * 3);
    for (const key of ['spin', 'inflow', 'core', 'speed', 'wave', 'beam']) S[key] += (T[key] - S[key]) * k;
  }

  function moveCamera(dt) {
    if (cam.goal) {
      const k = 1 - Math.exp(-dt * 3.5);
      cam.target.lerp(cam.goal.target, k);
      cam.dist += (cam.goal.dist - cam.dist) * k;
      if (cam.goal.phi != null) cam.phi += (cam.goal.phi - cam.phi) * k;
      if (cam.target.distanceTo(cam.goal.target) < 0.05 && Math.abs(cam.goal.dist - cam.dist) < 0.1) cam.goal = null;
    }
    if (!drag && !locked && performance.now() - lastTouch > 6000) cam.theta += IDLE_SPIN * dt;     // slow orbit when idle
    mouse.sx += (mouse.x - mouse.sx) * (1 - Math.exp(-dt * 2));
    mouse.sy += (mouse.y - mouse.sy) * (1 - Math.exp(-dt * 2));
    const k = 1 - Math.exp(-dt * 8);
    view.theta += (cam.theta - view.theta) * k; view.phi += (cam.phi - view.phi) * k; view.dist += (cam.dist - view.dist) * k;
    view.target.lerp(cam.target, k);
    const par = locked ? 0 : 1;                                    // locked: no parallax either
    const th = view.theta + mouse.sx * 0.06 * par, ph = THREE.MathUtils.clamp(view.phi - mouse.sy * 0.04 * par, 0.35, 2.6);
    camera.position.set(Math.sin(ph) * Math.sin(th), Math.cos(ph), Math.sin(ph) * Math.cos(th)).multiplyScalar(view.dist).add(view.target);
    camera.lookAt(view.target);
    const fadeK = 0.55 / Math.max(60, view.dist);        // depth fade scales with how far out we are
    for (const m of allFadeMats()) m.uniforms.uFade.value = fadeK;
  }
  let fadeMats = null;
  function allFadeMats() {
    if (fadeMats) return fadeMats;
    fadeMats = [];
    scene.traverse(o => { const u = o.material?.uniforms; if (u?.uFade && !envMats.includes(o.material)) fadeMats.push(o.material); });
    return fadeMats;
  }

  function animateCore(dt) {
    const sp = S.spin;
    outer.rotation.y += dt * 0.04 * sp;
    arcs.rotation.y -= dt * 0.09 * sp;
    for (const g of gyro) { g.ring.rotation.z += dt * g.speed * sp; g.holder.rotation.y += dt * 0.05 * sp; }
    icosa.rotation.y += dt * 0.12 * sp; icosa.rotation.x += dt * 0.05 * sp;
    poke *= Math.exp(-dt * 5);
    core.scale.setScalar(1 + level * 0.07 + poke * 0.05);                // voice and typing pulse the whole core
    dataShell.rotation.y += dt * 0.05 * sp; dataShell.rotation.x = Math.sin(time * 0.1) * 0.15;
    dataShell.material.uniforms.uTime.value = time;
    dataShell.material.uniforms.uI.value = 0.7 + 0.6 * S.core + level * 0.8 + poke * 0.6;
    const breath = 1 + Math.sin(time * 1.3) * 0.035 * (state === 'idle' || state === 'standby' ? 1 : 0.4);
    fresnelCore.scale.setScalar(breath);
    fresnelCore.material.uniforms.uI.value = 1.1 * S.core;
    fresnelCore.material.uniforms.uTime.value = time;
    for (const g of gyro) g.ring.material.uniforms.uTime.value = time;
    coreGlow.material.opacity = 0.32 + 0.3 * S.core + (state === 'thinking' ? 0.08 * Math.sin(time * 6) : 0);
    coreGlow.scale.setScalar(8 + S.core * 2.5);
    const pm = particles.material.uniforms;
    pm.uTime.value = time; pm.uInflow.value = S.inflow; pm.uSpeed.value = S.speed; pm.uI.value = 0.6 + 0.5 * S.core;

    coreBill.quaternion.copy(camera.quaternion);
    emblem.rotation.z = Math.sin(time * 0.3) * 0.08;
    // voice waveform: a ring deformed by the live mic / speech level
    const wa = wave.geometry.attributes.position, amp = S.wave * (0.25 + level * 1.6);
    for (let i = 0; i < 160; i++) {
      const a = i / 160 * Math.PI * 2;
      const r = 4.3 + amp * (0.5 * Math.sin(a * 6 + time * 7) + 0.3 * Math.sin(a * 11 - time * 11) + 0.2 * Math.sin(a * 17 + time * 5));
      wa.setXYZ(i, Math.cos(a) * r, Math.sin(a) * r, 0);
    }
    wa.needsUpdate = true;
    wave.material.uniforms.uOp.value = 0.6 * S.wave;
    // outward energy pulse when a reply lands
    if (pulseT >= 0) {
      pulseT += dt / 1.4;
      const t = Math.min(pulseT, 1);
      pulse.scale.setScalar(3 + t * 17);
      pulse.material.uniforms.uOp.value = (1 - t) * 0.7;
      if (pulseT >= 1) pulseT = -1;
    }
  }

  function updateMemories(dt, lit, wanted) {
    const pos = pointsGeo.attributes.position, col = pointsGeo.attributes.aColor, siz = pointsGeo.attributes.aSize, al = pointsGeo.attributes.aAlpha;
    const k = 1 - Math.exp(-dt * 6);
    camDir.subVectors(camera.position, view.target).normalize();
    for (const n of nodes) {
      const vis = hidden.has(n.type) ? 0 : 1;
      n.vis += (vis - n.vis) * k;
      const on = !lit || lit.has(n.id);
      n.lit += ((lit ? (on ? 1 : -1) : 0) - n.lit) * k;
      n.hov += ((n === hover || n === focus ? 1 : 0) - n.hov) * k;
      const i = n.id;
      pos.setXYZ(i, n.p.x, n.p.y, n.p.z);
      const hero = heroByNode.get(i);
      const heroShown = hero ? hero.s : 0;
      col.setXYZ(i, n.color.r, n.color.g, n.color.b);
      siz.setX(i, n.vis * n.size * (1 + 0.35 * Math.max(0, n.lit)) * (1 - 0.7 * heroShown));
      al.setX(i, n.vis * (n.lit < 0 ? 0.12 + 0.88 * (1 + n.lit) : 0.85 + 0.15 * n.lit));
    }
    pos.needsUpdate = col.needsUpdate = siz.needsUpdate = al.needsUpdate = true;

    for (const h of heroes) {
      if (!h.node) continue;
      const n = h.node, want = wanted.has(n.id) && !hidden.has(n.type);
      h.s += ((want ? 1 : 0) - h.s) * (1 - Math.exp(-dt * 5));
      const dim = n.lit < 0 ? 1 + n.lit : 1;                  // 1 = normal, 0 = pushed back
      const lift = n.hov * 5 + Math.max(0, n.lit) * 1.2 - (1 - dim) * 3;    // hover pulls toward you, dimmed pushes away
      h.group.position.copy(n.p).addScaledVector(camDir, lift);
      h.group.position.y += Math.sin(time * 0.8 + n.id) * 0.25;             // float
      const sc = h.s * n.size * 0.66 * (1 + n.hov * 0.45 + Math.max(0, n.lit) * 0.12) * (0.85 + 0.15 * dim);
      h.group.scale.setScalar(Math.max(0.0001, sc));
      h.spin += dt * (0.25 + n.hov * 0.8);
      h.shell.rotation.set(h.spin * 0.6, h.spin, 0);
      h.inner.rotation.set(-h.spin * 0.4, -h.spin * 0.7, 0);
      h.nucleus.rotation.y = h.spin * 1.5;
      const bright = (0.25 + 0.75 * dim) * (1 + n.hov * 0.6 + Math.max(0, n.lit) * 0.35);
      h.nucleus.material.uniforms.uI.value = 1.6 * bright; h.nucleus.material.uniforms.uTime.value = time;
      h.shell.material.uniforms.uOp.value = 0.5 * bright;
      h.inner.material.uniforms.uOp.value = 0.22 * bright;
      h.orbit.material.uniforms.uOp.value = 0.16 * bright;
      h.tether.material.uniforms.uOp.value = 0.1 * bright * (1 - n.hov);
      const dp = h.dots.geometry.attributes.position;
      for (let j = 0; j < 8; j++) {
        const a = time * (0.6 + j * 0.07) + j * 0.785;
        dp.setXYZ(j, Math.cos(a) * 1.9, 0, Math.sin(a) * 1.9);
      }
      dp.needsUpdate = true;
      h.dm.uniforms.uFade.value = fadeMats ? fadeMats[0].uniforms.uFade.value : 0.01;
    }
  }

  function updateEdges(lit) {
    const pos = edgeGeo.attributes.position, col = edgeGeo.attributes.aColor, al = edgeGeo.attributes.aAlpha, dir = edgeGeo.attributes.aDir;
    edgeMat.uniforms.uTime.value = time;
    let v = 0;
    for (const e of edges) {
      const { a, b } = e;
      const vis = Math.min(a.vis, b.vis);
      const both = lit && lit.has(a.id) && lit.has(b.id);
      const onPath = path && path.edges.has(ekey(a.id, b.id));
      const strength = 0.22 + Math.min(0.25, Math.sqrt(Math.min(a.deg, b.deg)) * 0.07);  // stronger between well-linked notes
      const alpha = vis * (lit ? (both ? (path ? (onPath ? 1.2 : 0.08) : 0.8) : 0.03) : strength);
      edgeCurve(a, b);
      for (let s = 0; s < EDGE_SEG; s++) {
        for (const t of [s / EDGE_SEG, (s + 1) / EDGE_SEG]) {
          bez(t, V);
          pos.setXYZ(v, V.x, V.y, V.z);
          const c = t < 0.5 ? a.color : b.color;
          col.setXYZ(v, c.r * 0.7 + 0.3, c.g * 0.7 + 0.3, c.b * 0.7 + 0.3);
          al.setX(v, alpha); dir.setX(v, both ? 1 : 0);
          v++;
        }
      }
    }
    pos.needsUpdate = col.needsUpdate = al.needsUpdate = dir.needsUpdate = true;
  }

  // Beams from the core: to relevant memories (strong), else faintly to the biggest hubs.
  function beamTargets(lit) {
    const out = [];
    if (found) for (const i of found) out.push({ n: nodes[i], s: 1 });
    if (focus && !out.some(o => o.n === focus)) out.push({ n: focus, s: 0.9 });
    if (hover && !out.some(o => o.n === hover)) out.push({ n: hover, s: 0.8 });
    for (const n of byDeg.slice(0, 8)) if (!out.some(o => o.n === n)) out.push({ n, s: lit ? 0.05 : 0.32 });
    return out.filter(o => !hidden.has(o.n.type)).slice(0, HERO_MAX);
  }
  let lastBeams = [];
  function updateBeams(lit) {
    const pos = beamGeo.attributes.position, col = beamGeo.attributes.aColor, al = beamGeo.attributes.aAlpha, dir = beamGeo.attributes.aDir;
    beamMat.uniforms.uTime.value = time;
    const list = beamTargets(lit);
    lastBeams = list;
    // recall: light flows into the core; responding: out to the memories
    const flow = state === 'memory' || state === 'thinking' ? -1 : state === 'speaking' ? 1 : found ? 1 : 0;
    let v = 0;
    for (let b = 0; b < HERO_MAX; b++) {
      const o = list[b];
      if (o) beamCurve(o.n);
      const a = o ? o.s * S.beam * o.n.vis : 0;
      const c = o ? o.n.color : white;
      for (let s = 0; s < BEAM_SEG; s++) for (const t of [s / BEAM_SEG, (s + 1) / BEAM_SEG]) {
        if (o) bez(t, V); else V.set(0, 0, 0);
        pos.setXYZ(v, V.x, V.y, V.z);
        const m = t;                                            // core end is hot pink, memory end takes its colour
        col.setXYZ(v, beamRGB.r * (1 - m) + c.r * m, beamRGB.g * (1 - m) + c.g * m, beamRGB.b * (1 - m) + c.b * m);
        al.setX(v, a); dir.setX(v, o && o.s > 0.5 ? flow : 0);
        v++;
      }
    }
    pos.needsUpdate = col.needsUpdate = al.needsUpdate = dir.needsUpdate = true;
  }

  function updateTravelers(dt, lit) {
    const strong = lastBeams.filter(o => o.s > 0.5);
    const rate = state === 'memory' ? 40 : state === 'thinking' ? 14 : state === 'speaking' ? 22 : strong.length ? 5 : 1.2;
    let spawn = rate * dt;
    for (const t of travelers) {
      if (!t.live && spawn > Math.random()) {
        spawn -= 1;
        // searching: from anywhere in memory toward the core; otherwise along real beams / links
        if (state === 'memory' || state === 'thinking' || !strong.length) {
          const pool = strong.length && Math.random() < 0.6 ? strong.map(o => o.n) : byDeg.filter(n => !hidden.has(n.type));
          if (!pool.length) continue;
          const n = pool[Math.floor(Math.random() * pool.length)];
          Object.assign(t, { live: true, n, t: 0, sp: 0.35 + Math.random() * 0.4, inward: state !== 'speaking' });
        } else {
          const n = strong[Math.floor(Math.random() * strong.length)].n;
          Object.assign(t, { live: true, n, t: 0, sp: 0.3 + Math.random() * 0.3, inward: false });
        }
      }
    }
    const pos = travGeo.attributes.position, col = travGeo.attributes.aColor, siz = travGeo.attributes.aSize, al = travGeo.attributes.aAlpha;
    travelers.forEach((t, i) => {
      if (t.live) {
        t.t += dt * t.sp;
        if (t.t >= 1 || hidden.has(t.n.type)) t.live = false;
      }
      if (!t.live) { siz.setX(i, 0); al.setX(i, 0); return; }
      beamCurve(t.n);
      bez(t.inward ? 1 - t.t : t.t, V);
      pos.setXYZ(i, V.x, V.y, V.z);
      const c = t.n.color;
      col.setXYZ(i, 0.6 + c.r * 0.4, 0.6 + c.g * 0.4, 0.6 + c.b * 0.4);
      siz.setX(i, 0.55); al.setX(i, Math.sin(Math.PI * t.t) * 0.95);
    });
    pos.needsUpdate = col.needsUpdate = siz.needsUpdate = al.needsUpdate = true;
  }

  // ---- overlay (HTML: crisp text) --------------------------------------------------
  function project(p, out) {
    V.copy(p).project(camera);
    out[0] = (V.x + 1) / 2 * W; out[1] = (1 - V.y) / 2 * H; out[2] = V.z;
    return out;
  }
  const pr = [0, 0, 0];
  function updateOverlay(lit) {
    for (const n of nodes) {
      const h = heroByNode.get(n.id);
      project(h && h.s > 0.1 ? h.group.position : n.p, pr);
      screen[n.id * 3] = pr[0]; screen[n.id * 3 + 1] = pr[1]; screen[n.id * 3 + 2] = pr[2];
    }
    // labels: hero nodes, most important first, no overlaps, fade with depth
    const cand = heroes.filter(h => h.node && h.s > 0.3 && !hidden.has(h.node.type)).map(h => h.node);
    cand.sort((a, b) => (b.hov - a.hov) || (b.lit - a.lit) || (b.deg - a.deg));
    project(ORIGIN, pr);
    const cx = pr[0], cy = pr[1];
    project(tmpUp.set(0, CORE_R, 0), pr);
    const coreR = Math.abs(pr[1] - cy) * 1.05, coreDist = camera.position.length();
    const placed = [{ x0: cx - coreR, y0: cy - coreR * 0.55, x1: cx + coreR, y1: cy + coreR * 0.55, core: true }];
    let li = 0;
    const depth = new THREE.Vector3();
    for (const n of cand) {
      if (li >= LABEL_MAX) break;
      const x = screen[n.id * 3], y = screen[n.id * 3 + 1], z = screen[n.id * 3 + 2];
      if (z > 1 || x < 0 || x > W || y < 0 || y > H) continue;
      if (lit && !lit.has(n.id)) continue;
      if (n === hover) continue;
      const w = Math.min(220, n.title.length * 6.6 + 16), box = { x0: x + 14, y0: y - 9, x1: x + 14 + w, y1: y + 9 };
      if (box.x1 > W - inset.r - 64 || box.x0 < inset.l + 8 || y > H - inset.b - 8 || y < inset.t + 8) continue;   // never under a panel or the scene controls
      const behindCore = Math.hypot(x - cx, y - cy) < coreR && (heroByNode.get(n.id)?.group.position || n.p).distanceTo(camera.position) > coreDist;
      const key = n === hover || n === focus || (found && found.has(n.id)) || (path && path.nodes.has(n.id));
      if (behindCore && !key) continue;
      if (placed.some(b => (!b.core || !key) && box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0)) continue;
      placed.push(box);
      depth.copy(heroByNode.get(n.id)?.group.position || n.p);
      const d = depth.distanceTo(camera.position);
      const near = THREE.MathUtils.clamp(1.25 - (d - view.dist * 0.7) / (view.dist * 0.9), 0.25, 1);
      const L = labels[li++];
      const text = n.title;
      if (L.text !== text) { L.el.textContent = text; L.text = text; }
      L.el.style.transform = `translate(${x + 14}px, ${y - 8}px)`;
      L.el.style.opacity = (n === hover || n === focus ? 1 : (lit ? 0.95 : 0.8) * near).toFixed(2);
      L.el.classList.toggle('on', n === hover || n === focus || !!(lit && lit.has(n.id)));
    }
    for (; li < LABEL_MAX; li++) if (labels[li].el.style.opacity !== '0') labels[li].el.style.opacity = 0;

    // hover card: what this memory is and how it connects
    const n = hover;
    if (n) {
      const x = screen[n.id * 3], y = screen[n.id * 3 + 1];
      if (card.dataset.id !== String(n.id)) {
        card.dataset.id = n.id;
        const rel = adj[n.id].slice(0, 4).map(i => nodes[i].title);
        card.innerHTML = `<div class="hc-t">${escHtml(n.title)}</div>
          <div class="hc-m"><span style="color:${colorFor(n.type)}">${escHtml(n.type)}</span> · ${n.deg} link${n.deg === 1 ? '' : 's'}</div>
          <div class="hc-p">${escHtml(n.rel || '')}</div>
          ${rel.length ? `<div class="hc-r">${rel.map(t => `<span>${escHtml(t)}</span>`).join('')}</div>` : ''}
          <div class="hc-h">click to open${focus && focus !== n ? ' · shift-click to trace path' : ''}</div>`;
      }
      card.hidden = false;
      const left = x + 28 + 250 > W - inset.r ? x - 28 - 250 : x + 28;
      card.style.transform = `translate(${left}px, ${Math.max(inset.t + 10, y - 22)}px)`;
    } else if (!card.hidden) { card.hidden = true; card.dataset.id = ''; }

    // system state, anchored under the core
    project(tmpUp.set(0, -CORE_R * 0.62, 0), pr);
    coreTag.style.transform = `translate(${pr[0]}px, ${pr[1] + 26}px) translateX(-50%)`;
  }
  const ORIGIN = new THREE.Vector3(), tmpUp = new THREE.Vector3();
  const escHtml = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function setState(s, detail) {
    state = STATES[s] ? s : 'idle';
    if (!coreTag) return;
    coreTag.dataset.state = state;
    coreTag.querySelector('b').textContent = state === 'speaking' ? 'RESPONDING' : state === 'memory' ? 'MEMORY' : state.toUpperCase();
    const n = found ? found.size : 0;
    const lead = n ? nodes[[...found][0]]?.title : '';
    const ctx = lead ? ` · ${lead}${n > 1 ? ` +${n - 1}` : ''}` : '';
    coreTag.querySelector('span').textContent = detail
      ? detail + (state === 'memory' ? '' : ctx)
      : (state === 'idle' && n ? `${n} memor${n === 1 ? 'y' : 'ies'} linked${ctx}` : STATES[state].d + ctx);
  }

  // ---- input ------------------------------------------------------------------
  function hit(px, py) {
    let best = null, bd = Infinity;
    for (const n of nodes) {
      if (hidden.has(n.type) || n.vis < 0.5) continue;
      const x = screen[n.id * 3], y = screen[n.id * 3 + 1], z = screen[n.id * 3 + 2];
      if (z > 1) continue;
      const h = heroByNode.get(n.id), lim = h && h.s > 0.3 ? 18 : 10;
      const d = Math.hypot(x - px, y - py);
      if (d < lim && d - (1 - z) * 400 < bd) { best = n; bd = d - (1 - z) * 400; }   // nearer objects win ties
    }
    return best;
  }

  function bindInput() {
    cv.addEventListener('pointerdown', e => {
      cv.setPointerCapture(e.pointerId);
      drag = { x: e.offsetX, y: e.offsetY, th: cam.theta, ph: cam.phi, moved: false, n: hit(e.offsetX, e.offsetY) };
      lastTouch = performance.now();
    });
    cv.addEventListener('pointermove', e => {
      lastMove = performance.now();
      mouse.x = (e.clientX / innerWidth) * 2 - 1; mouse.y = (e.clientY / innerHeight) * 2 - 1;
      if (drag) {
        const dx = e.offsetX - drag.x, dy = e.offsetY - drag.y;
        if (Math.hypot(dx, dy) > 4) drag.moved = true;
        if (drag.moved) {
          cam.theta = drag.th - dx * 0.006;
          cam.phi = THREE.MathUtils.clamp(drag.ph - dy * 0.005, 0.35, 2.6);
          cam.goal = cam.goal && { ...cam.goal, phi: null };
          lastTouch = performance.now();
          if (hover) hover = null;
        }
        return;
      }
      const n = hit(e.offsetX, e.offsetY);
      if (n !== hover) { hover = n; cv.style.cursor = n ? 'pointer' : 'grab'; }
    });
    cv.addEventListener('pointerup', e => {
      const d = drag; drag = null;
      if (!d || d.moved) return;
      const n = hit(e.offsetX, e.offsetY);
      if (n && e.shiftKey && focus && n !== focus) return tracePath(focus.id, n.id);
      if (n) return setFocus(n.id, true);
      clear();
    });
    cv.addEventListener('pointerleave', () => { hover = null; mouse.x = mouse.y = 0; });
    cv.addEventListener('wheel', e => {
      e.preventDefault();
      cam.dist = THREE.MathUtils.clamp(cam.dist * Math.exp(e.deltaY * 0.0012), 18, 320);
      if (cam.goal) cam.goal.dist = cam.dist;
      lastTouch = performance.now();
    }, { passive: false });
  }

  // ---- public actions -----------------------------------------------------------
  function setFocus(id, center = true) {
    focus = nodes[id]; path = null;
    if (center && focus) cam.goal = { target: focus.p.clone().multiplyScalar(0.55), dist: Math.max(34, Math.min(cam.dist, 70)), phi: null };
    lastTouch = performance.now();
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

  function clear() {
    focus = null; path = null; found = null; cb.onFocus(null);
    cam.goal = { target: new THREE.Vector3(), dist: cam.dist, phi: null };
    setState(state);
  }

  function highlight(ids) {
    found = ids && ids.length ? new Set(ids.filter(i => nodes[i])) : null;
    path = null;
    if (found && found.size) {
      const c = new THREE.Vector3();
      for (const i of found) c.add(nodes[i].p);
      c.divideScalar(found.size);
      // swing round so the relevant memories face you, keeping the core in view
      cam.theta = Math.atan2(c.x, c.z);
      cam.goal = { target: c.clone().multiplyScalar(0.3), dist: cam.dist, phi: null };
      lastTouch = performance.now();
    }
    setState(state);
  }

  function setHidden(set) { hidden = new Set(set); if (focus && hidden.has(focus.type)) clear(); }

  return {
    init, load, fit, setFocus, highlight, setHidden, clear, colorFor, setState,
    setInsets: v => { insetGoal = { ...v }; if (!camera) inset = { ...v }; },
    setLevel: l => { level = Math.min(1, Math.max(0, l || 0)); },
    pulse: () => { pulseT = 0; },
    zoom: f => { cam.dist = THREE.MathUtils.clamp(cam.dist * f, 18, 320); if (cam.goal) cam.goal.dist = cam.dist; lastTouch = performance.now(); },
    setLocked: v => { locked = !!v; },
    setTheme: name => { applyTheme(name); },
    get theme() { return theme; },
    poke: () => { poke = Math.min(1.5, poke + 0.6); },
    preview: type => { preview = type ? new Set(nodes.filter(n => n.type === type).map(n => n.id)) : null; },
    get fps() { return fps; },
    get quiet() { return isQuiet; },          // true while drawing at the idle rate
    // Tab: the next memory worth looking at (relevant ones first, then the biggest hubs)
    cycle: (dir = 1) => {
      const pool = (found && found.size ? [...found].map(i => nodes[i]) : byDeg.slice(0, 12)).filter(n => !hidden.has(n.type));
      if (!pool.length) return;
      const i = pool.indexOf(focus);
      setFocus(pool[(i + dir + pool.length) % pool.length].id, true);
    },
    // positions and links of some memories, for the chat's little 3D popout
    positions: ids => {
      const set = new Set(ids);
      for (const i of ids) for (const j of adj[i] || []) set.add(j);
      const list = [...set].filter(i => nodes[i]).slice(0, 18);
      return { nodes: list.map(i => ({ id: i, x: nodes[i].p.x, y: nodes[i].p.y, z: nodes[i].p.z, color: '#' + nodes[i].color.getHexString(),
                                       title: nodes[i].title, key: ids.includes(i) })),
               edges: edges.filter(e => set.has(e.a.id) && set.has(e.b.id)).map(e => [e.a.id, e.b.id]) };
    },
    nodeByTitle: t => nodes.find(n => n.title.toLowerCase() === t.toLowerCase()),
    get focused() { return focus; },
  };
})();

// Use the 3D brain only where WebGL works; otherwise app.js keeps the 2D canvas graph.
try {
  const probe = document.createElement('canvas');
  if (probe.getContext('webgl2') || probe.getContext('webgl')) window.Graph3D = Graph3D;
} catch { /* 2D fallback */ }
window.__graphReady?.();
