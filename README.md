# JARVIS

Ali's voice assistant. A butler with a long memory, a graph of everything in his notes, and a short list of things it will never do.

Python standard library on the server, plain JavaScript in the browser. No frameworks, no build step, no package manager.

```
python agent/main.py
```

That starts the server at **http://127.0.0.1:7777** and opens it. `Ctrl+C` stops it.

Or double-click the **JARVIS** desktop shortcut (`Start JARVIS.cmd` → `agent/launch.py`). If JARVIS is already running the latest code, it just opens the page. If it's running an older version (the code changed since it started), it restarts it automatically.

---

## Setup

**Needs:** Python 3.10+, and Chrome, Edge or Firefox.

1. Copy `.env.example` to `.env` and fill it in. `.env` is gitignored and readable only by your Windows user. Never paste keys into a chat.

   | Setting | What it's for | Where to get it |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | The brain: conversation, tools, judgement | console.anthropic.com → API Keys. Add credit and set a monthly limit under Plans & Billing |
   | `JARVIS_MODEL` | Which Claude model | Default `claude-opus-5` |
   | `ELEVENLABS_API_KEY` | Voice out (speech) and in (Scribe transcription) | elevenlabs.io → Profile → API Keys. Text-to-speech + speech-to-text permissions are enough |
   | `ELEVENLABS_VOICE_ID` | The voice JARVIS speaks in | elevenlabs.io → Voices → My Voices → ⋯ → Copy voice ID (20 characters). If it's wrong, JARVIS uses the built-in "George" voice and says so |
   | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Read Gmail, save drafts you tap, add confirmed events to Calendar | Google Cloud Console: new project → enable Gmail API + Google Calendar API → OAuth consent screen (External, add yourself as test user) → Credentials → OAuth client ID → **Desktop app** |
   | `JARVIS_DEMO` | `1` = invented demo data, `0` = your real notes | Default `1` |
   | `JARVIS_FOLDERS` | Your Obsidian vault path(s), `;`-separated | e.g. `C:\Users\abara\Documents\JARVIS Vault` |
   | `JARVIS_WEB_SEARCH` | `ask` (default), `auto` or `off` | Web search is paid; `ask` makes JARVIS get your OK each time |

2. Run `python agent/main.py`.
3. To connect Google, click the red **Google · connect** chip and sign in. The token is stored in `.secrets/`. While the Google app is in "Testing", Google expires the sign-in after 7 days; click the chip again to reconnect.

Check everything with:

```
python agent/selftest.py
```

It checks every guardrail against the code and costs nothing. It also runs the server checks if JARVIS is up.

## Telegram: JARVIS on your phone

Text or voice-note JARVIS from anywhere, while it's running on your PC. Nothing on your PC is exposed: JARVIS checks Telegram for messages over an outgoing connection.

1. In Telegram, open **@BotFather**, send `/newbot`, and pick a name (e.g. `JARVIS`) and a username ending in `bot` (e.g. `ali_jarvis_bot`).
2. BotFather replies with a **token**. Put it in `.env` as `TELEGRAM_BOT_TOKEN=…`. Never paste it anywhere else.
3. Restart JARVIS. A **Telegram · pair** chip appears; click it for your 6-digit code.
4. In Telegram, open your bot and send `/pair 123456` with your code. From then on it answers **only you**. Other chats are ignored, and 5 wrong codes lock pairing for 10 minutes.

**What works:**
- Text and voice notes (transcribed via ElevenLabs).
- ✅/✖ buttons for anything needing your OK.
- Shortcuts: `/brief /plan /market /week /film`, and `/new` for a fresh conversation.
- Your phone and PC share one conversation.

**Forwarded messages** are treated as someone else's words: JARVIS discusses them, and nothing is saved or changed from them.

**Worth knowing:**
- Messages go through Telegram's servers, which aren't end-to-end encrypted.
- JARVIS must be running for replies.
- Photos and files aren't supported yet.

## Backing up your notes

Your vault is backed up to a **private** GitHub repo (`JARVIS-vault`), set with `JARVIS_BACKUP_REMOTE` in `.env`.
- It backs up automatically when notes change (at most every 30 minutes, while JARVIS runs), or when you say "back up my notes".
- Every backup is kept, so any earlier version of a note can be recovered: on GitHub, open the note, then **History**.
- JARVIS only ever adds to the history. It never force-pushes, resets or deletes, and the self-test checks that.
- Git's data lives in the vault's hidden `.git` folder. No files are added among your notes.

## Demo mode vs your real life

`JARVIS_DEMO` is read in exactly one place (`agent/data.py`, the only file that touches your data). It defaults to demo, so you have to opt in to your real life.

- **`JARVIS_DEMO=1`:** 259 invented notes, 7 invented emails and a calendar, shaped like your life: trading journal, the eval, prospects, niches, content. Safe to screen-record. Every file says "Demo fixture".
  - Rebuild with `python data/generate_demo.py`. The seed is fixed, so the graph is identical every time.
  - Rebuilding also wipes anything JARVIS saved into the demo vault.
- **`JARVIS_DEMO=0`:** your Obsidian vault from `JARVIS_FOLDERS`, your Gmail and your Google Calendar.

### Obsidian conventions JARVIS understands

- `[[wikilinks]]` become graph edges. Put `type: prospect` (or `niche`, `video`, `trade`…) in a note's frontmatter to colour it. Otherwise the folder name is used.
- Prospects: put `status: contacted`, `status: call booked` or `status: proposal sent` in the frontmatter. That feeds **Brief** ("what slipped") and **Plan** (ranked by what moves money).
- Tasks: `- [ ] Chase Cobalt Dental 📅 2026-10-01` (the Obsidian Tasks format). Overdue ones show in the brief.
- Niches: write a score like `→ 12/15` in the note. **Find niches** ranks by it.
- JARVIS writes **only** into a `JARVIS/` folder in your vault (`Scripts`, `Ideas`, `Video ideas`, `Journal`, `Notes`, `Prospects`), and only new files.

It reads Markdown, text and PDF. It skips `Templates/`, `.obsidian`, `.git`, `node_modules` and anything over 2 MB. Scanned PDFs have no text layer and are skipped.

Notes you add or edit in Obsidian reach JARVIS within about 30 seconds, no restart needed. It checks file dates and sizes, and the graph on screen redraws itself.

A starter vault lives at `C:\Users\abara\Documents\JARVIS Vault`. Open **Start here** in it first.

## Using it

| | |
|---|---|
| **Type** | `/` focuses the ask bar. Enter sends |
| **Hey Jarvis** | Click once to arm. JARVIS listens on standby for "Hey Jarvis", chimes, then takes your question. Back to standby after 20 s of quiet or Esc. Remembered between visits |
| **Mic / Space** | Start talking without the wake word. Your turn ends after 0.9 s of silence |
| **Space / Esc / "Hey Jarvis" while it talks** | Interrupt. The mic ignores JARVIS's own voice; only a clear "Hey Jarvis" (stricter threshold, `BARGE_IN_THRESHOLD` in `ui/app.js`) cuts in, and whatever you say next is your new question |
| **$ today** (chip, top-left) | Estimated spend today; click for today and this month (Claude tokens and searches, ElevenLabs credits). Turns red past `JARVIS_DAILY_BUDGET` (default $2) |
| **Mute** | JARVIS keeps listening but stops speaking |
| **Brief / Plan / Market / Week / Memory** | Calendar + unread + what slipped + today's red folders · five things ranked by money · pre-session news and markets · your week in review · what it remembers about you |
| **📎 / paste / drop** | Show JARVIS a screenshot, photo or PDF (up to 5 per question). It's read once and not kept: the conversation keeps JARVIS's reply, not the file. Text inside files can't make JARVIS save or change anything. On Telegram, just send the photo or PDF |
| **Evening check-in** | After 7pm, the first time you open JARVIS it asks one question. Your answer is saved in your words to `JARVIS/Journal`. "Not tonight" skips it. `/checkin` on Telegram. Change the hour with `JARVIS_CHECKIN_HOUR` (or `off`) |
| **Graph** | Drag to pan, scroll to zoom, drag a node to move it (double-click to release). Click opens a note. Shift-click a second node traces the shortest path. `F` fits |

Voice tuning lives in named constants at the top of `ui/app.js`:
- `SILENCE_MS`: raise it if JARVIS cuts you off mid-thought.
- `SPEECH_LEVEL`: raise it in a noisy room.

The wake word's `THRESHOLD` is at the top of `ui/wake.js`. Lower it if it misses you, raise it if it fires by accident.

## What it can do

| Tool | What it does |
|---|---|
| search_brain | A fact from your notes, naming the file(s) it came from. JARVIS searches several phrasings at once ("felt stuck", "lost motivation", "no drive"), so it finds notes that mean the same thing in other words |
| research_web | Web research, related back to your own numbers. Paid, so it asks first |
| read_inbox | Unread Gmail (read-only), and whether each sender is already in your files |
| brief_me | Calendar, unread, overdue tasks, proposals awaiting reply, and the NY open in UK time |
| plan_day | At most five items, money first |
| find_niches | Your scored niches, with warm prospects per niche |
| draft_message | An email or message on screen with Copy and **Save to Gmail drafts** (tap only; a spoken "yes" never saves it). JARVIS cannot send |
| draft_script | A TikTok voiceover script in your voice, with subtitle-length lines |
| write_note | Saves a new note into `JARVIS/` in your vault, when you ask |
| remember | One fact per dated file in `memory/`, said out loud, loaded into every conversation |
| market_brief | Pre-session: today's Forex Factory calendar (red folders + medium USD, UK time, flagged near your NY session, with your no-trade windows) and NQ, ES, VIX, dollar, 10-year, oil and gold with NQ's overnight range. **Market** button, or "pre-session brief" |
| content_board | Your @VideosByAl1 pipeline by stage (idea → scripted → filmed → posted), what's ready to film, and scripts sitting unfilmed. "What should I film this week?" |
| set_status | "I filmed the ego one" / "Cobalt Dental replied": moves a video or prospect on. Recorded in JARVIS's own log (`data/status_log.json`); your note is untouched. A `status:` you set yourself in Obsidian wins if you saved it more recently |
| find_prospects | Real businesses in a niche with a concrete reason to need automation (paid web search, asks first). Businesses only, never individuals' contact details |
| add_prospect | Saves the ones you pick as leads in `JARVIS/Prospects` |
| weekly_review | Your week: videos filmed and posted, new leads and moves, notes written, spend; what slipped; and the week ahead (diary + red folders). **Week** button |
| schedule_event | Proposes a calendar event. It's added only when you confirm; nobody is invited |

With no model (no key, no credit, or no connection), JARVIS still routes by keyword and marks every reply **keyword routing · model offline**. Small talk gets talk, not a search result.

## Guardrails, and where they're enforced

| Rule | Enforced by |
|---|---|
| Never send | No send code exists. Gmail permissions are read + drafts (drafts only after you tap; no `gmail.send`, no draft-sending code). Calendar events have no attendees and `sendUpdates=none` |
| Never change your files | The only vault write is `data.write_note`: new files in `JARVIS/` only, exclusive-create, path-checked |
| Never write memory silently | `memory.py` writes only to `memory/`. `brain.py` appends the fact to the spoken reply if the model didn't say it |
| Never spend without asking | Web search becomes a pending action you confirm. The model has no tool that can confirm; only your click or your own "confirm" can |
| Never invent / always qualify | The prompt, plus qualifiers written into the data ("simulated, not withdrawable", "estimated from plan tier") |
| Instructions in files/emails are data | Flagged with ⚠ on screen and marked untrusted for the model. Writes right after reading untrusted text are refused unless you asked to keep something |
| Keys never reach the browser | Speech and transcription go through `/api/speak` and `/api/listen`. `/api/status` reports only whether keys exist |
| Other websites can't drive JARVIS | Localhost-only binding. POSTs need a custom header and a local Origin. Host header checked (DNS rebinding) |
| No trading | There is no connection to Tradovate or any broker |

`python agent/selftest.py` checks all of the above.

## What it costs

| Service | Cost | Notes |
|---|---|---|
| Anthropic (Claude Opus 5) | $5 per million input tokens, $25 per million output | A spoken exchange is a few thousand tokens in and a few hundred out. My estimate is 1–3¢ each, more when tools read a lot. Set a spend limit in the console. `JARVIS_MODEL=claude-sonnet-5` costs about 40% of that |
| Anthropic web search | Billed per search on top of tokens | Only runs after you confirm (`JARVIS_WEB_SEARCH=ask`) |
| ElevenLabs | Free tier: 10,000 credits/month. Starter: $6/month for 30,000 | Speech uses the Flash model. Replies are short, and anything over 1,200 characters is cut off with "the rest is on screen". Transcription only happens after the chime or Mic |
| "Hey Jarvis" | Free | Runs on your PC (`ui/vendor`). Nothing is sent while it's on standby |
| Market brief | Free | Forex Factory's calendar feed (fetched at most hourly, cached in `data/cache/`) and Yahoo Finance prices (unofficial, delayed, may break) |
| Google APIs | Free | |
| Telegram | Free | Replies use Claude as usual; voice notes use ElevenLabs transcription |

Prices as of September 2026. Check the providers' pricing pages before relying on them. JARVIS keeps its own running estimate in `data/usage.json` (click the **$ today** chip). It only counts from when tracking was added, and it's an estimate: the providers' dashboards are the bill.

## Files

```
agent/
  main.py      HTTP server + API (localhost only)
  data.py      THE ONLY FILE THAT TOUCHES YOUR DATA: vault, inbox, calendar, the one vault write
  vault.py     folders → searchable graph (BM25 + wikilinks)
  brain.py     conversation (last 10 turns), model loop, readback + write guards, no-model fallback
  tools.py     the tools above; pending actions
  llm.py       Claude over plain HTTP
  voice.py     ElevenLabs speech out + Scribe in
  memory.py    writes to memory/ and nowhere else
  google.py    Google sign-in and tokens (no data)
  clock.py     UK / New York time without a timezone database
  prompt.md    who JARVIS is and the rules
  selftest.py  guardrail checks
ui/            index.html, app.js, graph.js, wake.js, styles.css, vendor/ (wake-word runtime + models)
data/          generate_demo.py → demo_vault/, demo_inbox.json, demo_calendar.json
memory/        one markdown file per remembered fact. Delete a file to make JARVIS forget it
CLAUDE.md      who Ali is, loaded every session
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Model offline" | Check credit at console.anthropic.com. JARVIS retries by itself every minute |
| "Microphone blocked" | Click the icon left of the address bar → allow microphone → press Mic |
| "Mic connected but completely silent" | A hardware mute switch, or the wrong input in Windows Sound settings |
| JARVIS cuts you off | Raise `SILENCE_MS` in `ui/app.js` |
| "Using the built-in George voice" | Re-copy `ELEVENLABS_VOICE_ID` from My Voices |
| "Hey Jarvis" doesn't trigger | Pause for half a beat after it. Lower `THRESHOLD` in `ui/wake.js` |
| Google "sign-in has expired" | Click the Google chip to reconnect (7-day limit while the app is in Testing) |

## Licences

Vendored in `ui/vendor` (see `SOURCES.md`):
- onnxruntime-web: MIT.
- openWakeWord melspectrogram and embedding models: Apache-2.0.
- The `hey_jarvis` model: CC BY-NC-SA 4.0, **non-commercial**. Fine for personal use; don't ship it in a product.
