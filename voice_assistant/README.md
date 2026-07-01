# Voice Assistant — reminder slice

Always-on wake word → record → Whisper (speech-to-text) → Gemma 4 (intent) →
action. This first slice handles **reminders** end to end:

> "Hey Jarvis … remind me to call my sister in 30 minutes"

…schedules it and, 30 minutes later, pops a macOS banner (and optionally texts
you over iMessage). The **email** command is recognized but not yet executed —
that's phase 2 (search Sent mail → identify recipient → draft → confirm → send).

## Pipeline

```
mic ──▶ openWakeWord ──▶ record until silence ──▶ faster-whisper ──▶ Gemma 4 ──▶ action
        (wake word)                                 (STT)            (intent)    (reminder)
```

Why this split: Gemma 4 e2b *can* take audio, but its speech recognition loops
and hallucinates today — so transcription is done by Whisper, and Gemma is used
for what it's good at (turning text into a structured action).

## Setup

1. **Ollama** running with your model:
   ```bash
   ollama serve            # if not already running
   ollama list             # confirm gemma4:e2b is present
   ```

2. **Python deps** (use your existing venv):
   ```bash
   venv/bin/pip install -r voice_assistant/requirements.txt
   ```
   `faster-whisper` downloads its model on first run; `openwakeword` downloads
   its pretrained wake-word models on first import.

3. **macOS permissions** (System Settings → Privacy & Security):
   - **Microphone** → allow your terminal / Python.
   - **Automation** → allow Terminal to control **Messages** (only needed if you
     enable iMessage). The banner notification needs no special permission.

## Run

Settings live in [`voice_assistant/.env`](.env) (auto-loaded — see
[`.env.example`](.env.example) for all options; shell exports override it), so
plain commands "just work" with your config. A convenience launcher is included:
```bash
./run.sh                 # start the always-on daemon (uses .env)
./run.sh --text "..."    # one-shot command, no mic
./run.sh --auth-email    # one-time Gmail send consent
```

Test the brain + action **without a mic** (works as soon as deps + Ollama are up):
```bash
venv/bin/python -m voice_assistant.main --text "remind me to call my sister in 1 minute"
# keep the process alive to see it fire:
#   the daemon mode below keeps running; --text exits right away.
```

Verify notifications fire:
```bash
venv/bin/python -m voice_assistant.main --test-notify
```

Start the always-on daemon:
```bash
venv/bin/python -m voice_assistant.main
# say: "hey jarvis"  →  (beep)  →  "remind me to drink water in 2 minutes"
```

## Email (phase 2) — draft → confirm → send

Send email by voice to a **hardcoded allowlist** of contacts. The assistant will
*only* ever send to an address on that list — a spoken name is matched against
the list and nothing else, so it can never email an address it guessed.

Edit the list in [`config.py`](config.py) → `CONTACTS`:
```python
CONTACTS = [
    {"email": "friend@example.com", "names": ["friend"]},
    {"email": "colleague@example.com", "names": ["colleague"]},
]
```

**One-time Gmail authorization** (adds the `gmail.send` scope — your existing
briefing/digest token is read-only and is left untouched; the assistant keeps
its own `token_assistant.json`, written `0600`):
```bash
venv/bin/python -m voice_assistant.main --auth-email   # opens a browser once
```

**Flow:** say *"hey jarvis"* → *"send an email to Nikitha saying I'll be ten
minutes late"*. The assistant resolves Nikitha → her address, drafts subject +
body with Gemma, reads the draft back, and **waits for you to say "send" or
"cancel"** before anything goes out. Nothing is sent without that confirmation.

Test the whole path except the network send (no mic, no auth needed):
```bash
venv/bin/python -m voice_assistant.main --text "email Nikitha I'll be late"
# shows the draft, then: "Not sent (confirmation required)"
# add --yes to actually send (after --auth-email):
venv/bin/python -m voice_assistant.main --text "email Nikitha I'll be late" --yes
```

Safety properties:
- **Allowlist-only recipients** — an unknown name is refused, not guessed.
- **Confirm-before-send** — voice "send"/"cancel" in daemon mode, `--yes` in text mode.
- **OAuth, not an app password** — uses `gmail.send` scope only (minimal privilege).

## Custom wake word — "Hey Guru" (Porcupine)

openWakeWord only ships a few fixed phrases (`hey_jarvis`, `alexa`, …). For a
custom phrase like **"Hey Guru"**, the assistant can switch to Picovoice
Porcupine, which runs locally once set up:

1. **Install** the engine (already in requirements):
   ```bash
   venv/bin/pip install pvporcupine
   ```
2. **Create the keyword** at [console.picovoice.ai](https://console.picovoice.ai)
   (free account): *Porcupine → type `Hey Guru` → platform **macOS (arm64)** →
   Download `.ppn`*. Copy your free **AccessKey** from the dashboard.
3. **Drop the file + point the assistant at it:**
   ```bash
   mv ~/Downloads/Hey-Guru_en_mac_*.ppn voice_assistant/hey-guru.ppn
   export ASSISTANT_WAKEWORD_ENGINE=porcupine
   export ASSISTANT_PORCUPINE_KEY="<your AccessKey>"
   venv/bin/python -m voice_assistant.main
   # ✅ Listening (porcupine). Say "hey guru"…
   ```

Switch back to `hey_jarvis` anytime by unsetting `ASSISTANT_WAKEWORD_ENGINE`.
The rest of the pipeline (Whisper → Gemma → actions) is identical either way.

### No-account alternative — Whisper phrase-spotting

Don't want a Picovoice account at all? Use the Whisper you already have to listen
for the phrase directly — **no key, no `.ppn`, no training:**
```bash
export ASSISTANT_WAKEWORD_ENGINE=whisper
export ASSISTANT_WAKE_PHRASE="hey guru"     # any phrase you like
venv/bin/python -m voice_assistant.main
# → Whisper phrase-spotting for "hey guru"…
```
It transcribes a rolling ~1.6 s window every ~0.8 s and triggers when it hears
the phrase (a voice-energy gate skips silence, so it's not running Whisper
constantly). Trade-off: higher CPU and a bit more latency than Porcupine —
fine on an M-series Mac, and the simplest way to get a custom phrase today.

## Configuration (all via env vars)

| Variable | Default | Meaning |
|----------|---------|---------|
| `ASSISTANT_MODEL` | `gemma4:e2b` | Ollama model for intent |
| `ASSISTANT_WAKEWORD_ENGINE` | `openwakeword` | `openwakeword` (built-in), `porcupine` (custom, needs key), or `whisper` (custom, no account) |
| `ASSISTANT_WAKE_PHRASE` | `hey guru` | phrase to listen for (whisper engine) |
| `ASSISTANT_WAKEWORD` | `hey_jarvis` | openWakeWord model (`alexa`, `hey_mycroft`, …) |
| `ASSISTANT_WAKEWORD_THRESHOLD` | `0.5` | 0–1; raise to reduce false triggers |
| `ASSISTANT_PORCUPINE_KEY` | _(empty)_ | Picovoice AccessKey (porcupine engine) |
| `ASSISTANT_PORCUPINE_KEYWORD` | `voice_assistant/hey-guru.ppn` | path to your `.ppn` keyword file |
| `ASSISTANT_WHISPER_MODEL` | `base.en` | `tiny.en` (faster) … `small.en` (more accurate) |
| `ASSISTANT_ENABLE_IMESSAGE` | `0` | `1` to also text yourself when a reminder fires |
| `ASSISTANT_IMESSAGE_HANDLE` | _(empty)_ | your phone/email for self-reminders |
| `ASSISTANT_CONTACT_THRESHOLD` | `0.6` | 0–1 name-match strictness for the email allowlist |
| `ASSISTANT_EMAIL_REQUIRE_CONFIRM` | `1` | keep `1` — require confirmation before any send |

## Notes & limits

- **Wake word**: `hey_jarvis` is a built-in. A custom **"Hey Gemma"** requires
  training an openWakeWord model — straightforward but a separate step.
- **Reminders persist** to `reminders.json` and reload on restart, so a pending
  one still fires after a bounce.
- **Time parsing** handles relative phrasing ("in 30 minutes", "in 2 hours").
  Absolute times ("at 5pm") are a small follow-up.
- **Email (phase 2)** will reuse the Gmail client from the briefing/digest
  scripts, but needs the `gmail.send` scope (current token is read-only) and a
  draft→confirm step before anything is sent.
