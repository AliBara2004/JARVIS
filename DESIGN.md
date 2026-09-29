# JARVIS — DESIGN.md

The design system for the JARVIS web UI (`ui/`). Read this before changing anything visual, so every change
stays in the same language. Source of truth for values is `ui/styles.css` (`:root` and the theme blocks);
this file says what they mean and how to use them.

## 1. Visual theme & atmosphere

A tactical HUD projected over a living 3D memory graph. Void-dark, glassy, precise. Panels are *projected
frames* hanging in space around the core, not app chrome: they tilt slightly with the mouse, carry corner
brackets instead of full borders, and sit on blurred glass. The feeling is calm, expensive and technical,
closer to a film prop than a dashboard. Motion is slow and ambient; information is dense but quiet.

Keep: the violet → pink gradient identity, corner brackets, glass decks, monospace telemetry, the core.
Avoid: cartoon neon, rounded "SaaS cards", pastel fills, emoji, stock dashboard widgets.

## 2. Colour palette & roles

Violet is JARVIS's own theme (default). Stark amber and tactical cyan are alternates (`T` cycles), defined by
overriding the same tokens on `body[data-theme]`. Never hard-code a theme colour: use the token or its
`rgba(var(--v-rgb), a)` form so every theme works.

| Token | Violet value | Role |
|---|---|---|
| `--bg` | `#07080c` | the void behind everything |
| `--glass` / `--surface` | `rgba(15,17,24,.72–.78)` | deck and card backgrounds (always with backdrop blur) |
| `--focus` | `#181b26` | raised row / hover surface |
| `--violet` / `--v-rgb` | `#8b5cf6` | identity, primary accents, section diamonds |
| `--violet-2` | `#a78bfa` | secondary accent, titles inside cards, focus ring |
| `--pink` / `--p-rgb` | `#ec4899` | gradient end, confirm cards, the speaking caret |
| `--grad` | violet → pink, 120° | wordmark, primary button, meters, weight bars |
| `--cyan` | `#06b6d4` | status: listening, "YOU", new items, active controls |
| `--emerald` | `#10b981` | live / done / speaking / healthy |
| `#fb7185` family | — | warnings and errors only |
| `--text` | `#ecebf7` | body text |
| `--muted` | `#9c98b8` | secondary text |
| `--faint` | `#827e9e` | labels and meta; the floor for contrast (~4.6:1 on panels) — never go darker |
| `--line` / `--line-2` | violet at .14 / .30 | hairlines; `--line-2` on hover |

States map to colour consistently: listening = cyan, thinking = violet-2, memory = pink, speaking = emerald,
standby = faint.

## 3. Typography

Three local fonts (`ui/vendor/fonts`, preloaded, `font-display: swap`):

- **Sora** (`--sans`) — body, replies, notes. 13px base, 14–14.5px for JARVIS's replies.
- **Space Grotesk** (`--display`) — headings, big numbers, wordmark. 600 weight, 15–18px.
- **JetBrains Mono** (`--mono`) — telemetry, labels, badges, buttons, timestamps. Uppercase with
  0.12–0.22em tracking.

Rules: nothing smaller than 10px. Numbers that change or compare use `font-variant-numeric: tabular-nums`.
Use `…` not `...`, and loading text ends in `…`. Headings may carry the faint chromatic split
(`text-shadow` red/cyan at .28); body text never does.

## 4. Components

- **Deck** (`.panel`): fixed glass column, 300px, corner brackets via `::before`, radius 2px, blur 24px.
  Collapses to a 52px icon dock (`.docked`); narrow windows start docked.
- **Label** (`.label`): mono 10px uppercase, faint, led by a rotated violet diamond. Every section starts with one.
- **Widget** (`.widget`): dark card with a 2px gradient spine on the left; `h4` label row with a violet badge on the right.
- **Button** (`.btn`): mono uppercase pill, glass, hairline border; hover = white text + violet glow.
  `.primary` = gradient fill (one per view: Execute). `.live` = pink ring for active voice.
- **Icon button**: 32–34px square (round in the console), always with `title` *and* `aria-label`, icon `aria-hidden`.
- **Chip** (`.chip`): status pill with a leading dot; green dot = connected, rose = needs attention.
- **Card in a reply** (`.card`): `--focus` surface with a 2px left border — violet default, pink = needs
  confirmation, rose = error. Rows use `.rtag` (tiny mono tag) + text + mono meta on the right.
- **Console** (`#ask`): the one fully rounded element — a pill with a slowly rotating conic gradient border,
  mic orb on the left, Execute on the right. Shortcut pills sit under it in a gentle arc.
- **Conversation** (`.convo`): angled glass card projected out of the core, with a beam back toward it. A
  scrolling log (40 exchanges, earlier turns reloaded from the server and dimmed), YOU / JARVIS labels, and
  copy / read-aloud links that appear on hover.
- **Today strip** (`.today` / `.trow`): the left deck's lead: NY session countdown, next key news (rose when
  it's inside the no-trade window), next calendar event, goals. Hairline-separated rows, mono key, display value.
- **Command palette** (`#palette`, Ctrl K): native `<dialog>`, fuzzy word match over commands, anything else
  becomes "Ask JARVIS". New features get a palette entry, not another button.
- **Notice** (`.toast`): top right, under the telemetry bar. Violet = info, rose = warning (failover), pink =
  reminder. Auto-dismiss, paused on hover, `aria-live="polite"`.
- **Skeleton** (`.skel`): shimmering bars where data will land; never show "—" placeholders.
- **Quiet line** (`.quiet-line`): empty widgets fold into one dashed row ("Nothing yet in …"), tap to show.

## 5. Layout

Full-screen canvas; everything else is `position: fixed` on top of it: telemetry bar (top, 44px), left deck
(system / inspector / hubs), right deck (widgets / filters), console (bottom centre, ≤780px), scene controls
(inside the right deck's edge). The graph is told the decks' insets so the core centres in the free space.
16px gutters. Spacing steps are 4 / 6 / 8 / 10 / 14 / 16px. No nested boxes beyond deck → card → row.

### Modes
- **Trading** (`body.trading`): automatic from an hour before the NY open to 2½ hours after (or pinned via
  the palette). The right deck leads with the Trading card (countdown, key news with no-trade windows, Risk
  Rules, eval P&L) and drops Training and Activity.
- **Focus** (`body.focus`): while voice is talking, the decks fade to 14% and blur; hover brings one back.

## 6. Depth & elevation

Depth comes from blur, glow and perspective, not drop shadows. Order: canvas → scanlines (z 30, pointer-events
none) → decks (z 4) → telemetry / conversation (z 5) → console (z 6) → banner (z 10) → dropzone (z 20).
Glows are `0 0 18px` of the accent at ~.35. Decks tilt with `perspective(1400px) rotateY(±9deg)`, plus a
couple of degrees from the mouse (`--mx`, `--my`); the tilt goes to 0 when docked or below 1180px.

## 7. Do's and don'ts

Do
- Use tokens and `rgba(var(--v-rgb), a)`; test all three themes (`T`).
- Give every interactive thing a real `<button>`, or `tabindex="0"` plus a role and Enter/Space support.
- Keep one visible `:focus-visible` ring (violet-2 outline + glow); never remove outlines without it.
- Animate `transform` and `opacity` only; list transition properties (never `transition: all`).
- Respect `prefers-reduced-motion` (it already disables all animation globally).
- Handle empty states in words ("No goals yet. Add one, or tell me…"), never a blank box.

Don't
- Add colours outside the palette, or use pink/cyan/emerald for decoration when they mean a state.
- Take over Tab or other browser keys; JARVIS shortcuts are single keys outside text fields.
- Add fake telemetry, pseudo-system jargon or badges that don't reflect real state.
- Use rounded "card" radii above 4px (the console pill is the one exception).

## 8. Responsive

- ≤1180px: decks narrow (270 / 260px), start docked, tilt off, model name hidden from telemetry.
- ≤820px: telemetry shows only the wordmark side, scene controls hide, conversation spans the width.
- ≤640px (phones): decks become bottom sheets opened from the Today / Widgets / Chat tabs above the console;
  the telemetry bar keeps only the wordmark and data badge; Execute shrinks to its icon.
- Phones: console respects `env(safe-area-inset-bottom)`; no zoom blocking.
- Short screens (≤820px tall): top hubs show five rows so the inspector keeps room.

## 9. Agent prompt guide

When asked to change the JARVIS UI:
1. Read this file and the relevant part of `ui/styles.css`; restyle the existing vanilla HTML/CSS/JS —
   no frameworks, no build step, no CDN (everything is vendored).
2. Reuse an existing component class before inventing one; new ones follow section 4's anatomy.
3. Check in the real browser (Chrome) in all three themes, with the keyboard (Tab, Enter, Space, Esc), and
   read the console for errors. For phone widths, drive headless Chrome over DevTools with
   `Emulation.setDeviceMetricsOverride` (plain `--headless --screenshot` doesn't wait for the app to boot).
4. Run `python agent/selftest.py` before committing.
