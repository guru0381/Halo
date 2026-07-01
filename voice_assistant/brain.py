"""Gemma 4 turns a transcribed command into one structured action.

Uses Ollama's structured-output mode (a JSON schema passed as `format`), which
is far more reliable on a small edge model than free-form tool-calling: the
model is constrained to emit valid JSON matching SCHEMA.
"""

import json
import requests

from . import config

SYSTEM = """You convert a short spoken command into ONE structured action.
Reply ONLY with JSON matching the schema — no prose, no explanation.

Actions:
- "reminder": the user wants to be reminded to do something later.
    reminder_text = a concise imperative of what to do (e.g. "Call your sister").
    delay_minutes = minutes from now to fire, converted from natural language:
      "in 30 minutes" / "after half an hour" -> 30
      "in 2 hours" -> 120,  "in 10 mins" -> 10,  "in a minute" -> 1
    If a reminder has no clear time, set needs_clarification=true and delay_minutes=0.
- "email": the user wants to send an email.
    recipient = the person's NAME exactly as spoken (e.g. "Nikitha"). Do NOT invent
      an email address — just capture the name.
    subject = a short subject line you compose from the request (<= 8 words).
    body = the message to send, in the user's voice. If the user dictated the
      content, use it; otherwise write a brief, polite message for the stated topic.
    If you cannot tell who the recipient is OR there is no message content,
      set needs_clarification=true.
- "unknown": anything that is not a reminder or an email.

Examples:
"remind me to call my sister after 30 min"
  -> {"action":"reminder","reminder_text":"Call your sister","delay_minutes":30,"recipient":"","subject":"","body":"","needs_clarification":false}
"remind me to take the medicine"
  -> {"action":"reminder","reminder_text":"Take the medicine","delay_minutes":0,"recipient":"","subject":"","body":"","needs_clarification":true}
"send an email to Nikitha saying I'll be ten minutes late to lunch"
  -> {"action":"email","reminder_text":"","delay_minutes":0,"recipient":"Nikitha","subject":"Running late","body":"Hi, I'll be about ten minutes late to lunch. See you soon.","needs_clarification":false}
"email Gurunath the notes from today's meeting"
  -> {"action":"email","reminder_text":"","delay_minutes":0,"recipient":"Gurunath","subject":"Meeting notes","body":"Hi, sharing the notes from today's meeting.","needs_clarification":false}
"what's the weather like today"
  -> {"action":"unknown","reminder_text":"","delay_minutes":0,"recipient":"","subject":"","body":"","needs_clarification":false}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["reminder", "email", "unknown"]},
        "reminder_text": {"type": "string"},
        "delay_minutes": {"type": "number"},
        "recipient": {"type": "string"},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
    },
    "required": [
        "action", "reminder_text", "delay_minutes",
        "recipient", "subject", "body", "needs_clarification",
    ],
}


class BrainError(RuntimeError):
    """Raised when Ollama is unreachable or returns something unusable."""


def extract_intent(text):
    """Return a validated intent dict, or raise BrainError with a clear message."""
    payload = {
        "model": config.MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "think": False,            # gemma4 is a thinking model; keep content pure JSON
        "format": SCHEMA,          # constrain output to the schema
        "options": {"temperature": 0},
    }
    try:
        r = requests.post(f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=120)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise BrainError(
            f"Could not reach Ollama at {config.OLLAMA_URL}. "
            f"Is `ollama serve` running and is {config.MODEL} pulled?  ({e})"
        )

    body = r.json()
    content = (body.get("message") or {}).get("content", "")
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        raise BrainError(f"Model did not return JSON. Raw content: {content!r}")

    # Normalize / defend against partial objects.
    action = str(data.get("action", "unknown")).lower().strip()
    if action not in {"reminder", "email", "unknown"}:
        action = "unknown"
    try:
        delay = max(0, int(round(float(data.get("delay_minutes", 0)))))
    except (TypeError, ValueError):
        delay = 0
    return {
        "action": action,
        "reminder_text": str(data.get("reminder_text", "")).strip(),
        "delay_minutes": delay,
        "recipient": str(data.get("recipient", "")).strip(),
        "subject": str(data.get("subject", "")).strip(),
        "body": str(data.get("body", "")).strip(),
        "needs_clarification": bool(data.get("needs_clarification", False)),
    }
