"""All tunables in one place, read from the environment with safe defaults.

No secrets live in source (lesson from the briefing/digest review). Settings are
read from the environment; a `.env` file next to this package (or at the project
root) is auto-loaded so you don't have to re-export every session. Real shell
exports always win over the file. See `.env.example` for the full list.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)


def _load_dotenv(path):
    """Minimal .env reader: KEY=value lines, # comments, optional quotes.

    Uses setdefault so anything already exported in the shell takes precedence
    over the file (env > file). Zero dependencies.
    """
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


# Package-local .env first, then project root — first writer wins (so the
# package .env can override a shared root one).
for _p in (os.path.join(HERE, ".env"), os.path.join(PROJECT_ROOT, ".env")):
    _load_dotenv(_p)

# --- Gemma 4 via Ollama ------------------------------------------------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("ASSISTANT_MODEL", "gemma4:e2b")

# --- Wake word ---------------------------------------------------------------
# Engine: "openwakeword" (built-in pretrained models) or "porcupine" (custom
# phrases like "Hey Guru" via a Picovoice keyword file).
WAKEWORD_ENGINE = os.environ.get("ASSISTANT_WAKEWORD_ENGINE", "openwakeword").lower()

# openWakeWord: a pretrained model name ("hey_jarvis", "alexa", "hey_mycroft", …).
WAKEWORD_MODEL = os.environ.get("ASSISTANT_WAKEWORD", "hey_jarvis")
WAKEWORD_THRESHOLD = float(os.environ.get("ASSISTANT_WAKEWORD_THRESHOLD", "0.5"))

# Porcupine: a free access key + a custom keyword (.ppn) file you generate at
# console.picovoice.ai (e.g. "Hey Guru"). Default keyword path is next to this
# package; override with ASSISTANT_PORCUPINE_KEYWORD.
PORCUPINE_KEY = os.environ.get("ASSISTANT_PORCUPINE_KEY", "")
PORCUPINE_KEYWORD_PATH = os.environ.get(
    "ASSISTANT_PORCUPINE_KEYWORD",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "hey-guru.ppn"),
)

# Whisper phrase-spotting: no account/key/training. Reuses the Whisper model to
# transcribe a rolling window and trigger when it hears WAKE_PHRASE. Heavier on
# CPU and a touch laggier than the dedicated engines, but zero setup.
WAKE_PHRASE = os.environ.get("ASSISTANT_WAKE_PHRASE", "hey guru").lower()
WAKE_WINDOW_SECONDS = float(os.environ.get("ASSISTANT_WAKE_WINDOW_SECONDS", "1.6"))
WAKE_CHECK_SECONDS = float(os.environ.get("ASSISTANT_WAKE_CHECK_SECONDS", "0.8"))

# --- Whisper (faster-whisper) ------------------------------------------------
WHISPER_MODEL = os.environ.get("ASSISTANT_WHISPER_MODEL", "base.en")
WHISPER_COMPUTE = os.environ.get("ASSISTANT_WHISPER_COMPUTE", "int8")

# --- Audio capture -----------------------------------------------------------
SAMPLE_RATE = 16000           # openWakeWord + Whisper both expect 16 kHz mono
FRAME_SAMPLES = 1280          # 80 ms frames, openWakeWord's expected chunk
RECORD_MAX_SECONDS = float(os.environ.get("ASSISTANT_RECORD_MAX_SECONDS", "8"))
SILENCE_SECONDS = float(os.environ.get("ASSISTANT_SILENCE_SECONDS", "1.2"))
SILENCE_RMS = float(os.environ.get("ASSISTANT_SILENCE_RMS", "350"))  # int16 RMS
# After the wake word, wait up to this long for you to START speaking before
# giving up — so a pause before the command doesn't capture pure silence.
SPEECH_ONSET_TIMEOUT = float(os.environ.get("ASSISTANT_SPEECH_ONSET_TIMEOUT", "3.0"))

# --- Reminder fire actions ---------------------------------------------------
NOTIFY = True                 # always show a native banner when a reminder fires
# Show a brief "scheduled" acknowledgment the moment a reminder is set. This is
# distinct from the reminder itself, which fires later. Set to "0" to stay
# silent until the reminder actually comes due.
ANNOUNCE_SCHEDULED = os.environ.get("ASSISTANT_ANNOUNCE_SCHEDULED", "1") == "1"
ENABLE_IMESSAGE = os.environ.get("ASSISTANT_ENABLE_IMESSAGE", "0") == "1"
# Your OWN handle — self-reminders are sent here. Empty = banner only.
IMESSAGE_HANDLE = os.environ.get("ASSISTANT_IMESSAGE_HANDLE", "")

# --- Storage -----------------------------------------------------------------
STORE_PATH = os.environ.get(
    "ASSISTANT_STORE", os.path.join(HERE, "reminders.json")
)

# --- Email (phase 2) ---------------------------------------------------------
# Gmail SEND scope only — minimal privilege. The assistant gets its OWN token
# file (separate from the read-only token used by briefing.py/digest.py) so the
# scopes don't collide. credentials.json is reused from the project root.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
CREDENTIALS_PATH = os.environ.get(
    "ASSISTANT_CREDENTIALS", os.path.join(PROJECT_ROOT, "credentials.json")
)
ASSISTANT_TOKEN_PATH = os.environ.get(
    "ASSISTANT_TOKEN", os.path.join(HERE, "token_assistant.json")
)

# Hardcoded contact allowlist. The assistant will ONLY ever send to an address
# on this list — a spoken name is matched against `names`/`email` here and
# nothing else, so it can never email an address it guessed. Edit freely.
CONTACTS = [
    # Committed defaults are placeholders. Put your REAL contacts in
    # voice_assistant/contacts.local.json (gitignored) to override these.
    {"email": "friend@example.com", "names": ["friend"]},
    {"email": "colleague@example.com", "names": ["colleague"]},
]
_local_contacts = os.path.join(HERE, "contacts.local.json")
if os.path.exists(_local_contacts):
    import json as _json
    try:
        with open(_local_contacts) as _f:
            CONTACTS = _json.load(_f)
    except Exception:
        pass
# 0..1 — a spoken name must match a contact at least this well to be accepted.
CONTACT_MATCH_THRESHOLD = float(os.environ.get("ASSISTANT_CONTACT_THRESHOLD", "0.6"))

# Require explicit confirmation before any email is sent. Keep this True.
EMAIL_REQUIRE_CONFIRM = os.environ.get("ASSISTANT_EMAIL_REQUIRE_CONFIRM", "1") == "1"
