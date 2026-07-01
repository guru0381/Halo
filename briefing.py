#!/usr/bin/env python3
"""
Daily Briefing — fully local summarization, emailed to you each morning.
Reads today's unread Gmail + recent Apple Notes, asks Gemma 4 (offline in
Ollama) for a prioritized to-do list, prints it, and emails it to you.

Summarization happens on-device. Network calls: Gmail API (read your mail)
and an SMTP send (deliver the briefing to your inbox).
"""

import os
import base64
import smtplib
import subprocess
import datetime as dt
from email.message import EmailMessage

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from envtools import load_env
load_env()  # GMAIL_ADDRESS / GMAIL_APP_PASSWORD from a local .env (gitignored)

# ---------------------------------------------------------------------------
# Config — tweak these
# ---------------------------------------------------------------------------
MODEL = "gemma4:e2b"          # edge model, comfortable on an 8 GB Air
OLLAMA_URL = "http://localhost:11434/api/generate"
MAX_EMAILS = 6                # cap so the prompt stays small on 8 GB
NOTES_LOOKBACK_DAYS = 3       # only pull notes edited in the last N days
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
HERE = os.path.dirname(os.path.abspath(__file__))

# --- Email delivery settings --------------------------------------------------
EMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")   # send to (and from) this
APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")  # from myaccount.google.com/apppasswords
SEND_EMAIL    = True          # set False to only print, not email


# ---------------------------------------------------------------------------
# Gmail (read)
# ---------------------------------------------------------------------------
def gmail_service():
    """Authorize once, then reuse the cached token silently."""
    creds = None
    token_path = os.path.join(HERE, "token.json")
    cred_path = os.path.join(HERE, "credentials.json")

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(cred_path):
                raise SystemExit(
                    "Missing credentials.json — see SETUP.md for the "
                    "one-time Google Cloud step."
                )
            flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _decode_body(payload):
    """Walk the MIME tree and pull the best plain-text body we can find."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", "ignore")
    for part in payload.get("parts", []) or []:
        text = _decode_body(part)
        if text:
            return text
    return ""


def fetch_emails(service):
    """Unread mail from the primary inbox, newest first."""
    resp = (
        service.users()
        .messages()
        .list(userId="me", q="is:unread in:inbox category:primary",
              maxResults=MAX_EMAILS)
        .execute()
    )
    out = []
    for ref in resp.get("messages", []):
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="full")
            .execute()
        )
        headers = {h["name"].lower(): h["value"]
                   for h in msg["payload"]["headers"]}
        body = _decode_body(msg["payload"]).strip()
        out.append({
            "from": headers.get("from", "(unknown)"),
            "subject": headers.get("subject", "(no subject)"),
            "snippet": (body or msg.get("snippet", ""))[:800],
        })
    return out


# ---------------------------------------------------------------------------
# Apple Notes (via AppleScript — clean plain text, no DB parsing)
# ---------------------------------------------------------------------------
def fetch_notes():
    """Return [{title, body}] for notes edited in the last N days."""
    cutoff = (dt.date.today()
              - dt.timedelta(days=NOTES_LOOKBACK_DAYS)).strftime("%m/%d/%Y")
    script = f'''
    set cutoff to date "{cutoff}"
    set out to ""
    tell application "Notes"
        repeat with n in notes
            if modification date of n > cutoff then
                set out to out & "###TITLE### " & (name of n) & linefeed
                set out to out & (plaintext of n) & linefeed & "###END###" & linefeed
            end if
        end repeat
    end tell
    return out
    '''
    try:
        raw = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=60
        ).stdout
    except Exception as e:
        print(f"(Could not read Notes: {e})")
        return []

    notes = []
    for block in raw.split("###END###"):
        block = block.strip()
        if not block.startswith("###TITLE###"):
            continue
        first, *rest = block.split("\n")
        notes.append({
            "title": first.replace("###TITLE###", "").strip(),
            "body": "\n".join(rest).strip()[:800],
        })
    return notes


# ---------------------------------------------------------------------------
# Gemma 4 via Ollama
# ---------------------------------------------------------------------------
def build_prompt(emails, notes):
    lines = ["Here is today's raw input.\n", "=== UNREAD EMAILS ==="]
    if emails:
        for i, e in enumerate(emails, 1):
            lines.append(f"\n[{i}] From: {e['from']}\n    Subject: {e['subject']}"
                         f"\n    {e['snippet']}")
    else:
        lines.append("(none)")

    lines.append("\n\n=== RECENT NOTES ===")
    if notes:
        for i, n in enumerate(notes, 1):
            lines.append(f"\n[{i}] {n['title']}\n    {n['body']}")
    else:
        lines.append("(none)")

    return "\n".join(lines)


SYSTEM = """You are a personal chief-of-staff. From the emails and notes below,
produce a concise daily briefing for today. Output exactly these sections:

TODAY'S PRIORITIES
  A numbered list of concrete action items. Each line: the action, then in
  parentheses one short phrase of context (who it's for / why it matters).
  Order by urgency. Max 8 items. Skip newsletters, promos, and noise.

FYI
  Up to 3 short bullets worth knowing but not acting on. Omit if nothing.

Be specific and brief. Do not invent tasks that aren't grounded in the input."""


def summarize(prompt):
    payload = {
        "model": MODEL,
        "system": SYSTEM,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_ctx": 8192},
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=300)
    r.raise_for_status()
    return r.json()["response"].strip()


# ---------------------------------------------------------------------------
# Email delivery (SMTP via Gmail App Password)
# ---------------------------------------------------------------------------
def send_briefing_email(briefing, today):
    if not SEND_EMAIL:
        return
    if not APP_PASSWORD:
        print("⚠️  No app password set — skipping email. "
              "Add APP_PASSWORD near the top of the script.")
        return

    msg = EmailMessage()
    msg["Subject"] = f"📋 Daily Briefing — {today}"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = EMAIL_ADDRESS
    msg.set_content(briefing)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, APP_PASSWORD)
            server.send_message(msg)
        print(f"✉️  Briefing emailed to {EMAIL_ADDRESS}")
    except Exception as e:
        print(f"⚠️  Could not send email: {e}")


# ---------------------------------------------------------------------------
def main():
    today = dt.date.today().strftime("%A, %B %d")
    print(f"\n📋  Daily Briefing — {today}")
    print("    (reading Gmail + Notes, summarizing locally with Gemma 4)\n")

    print("→ Fetching unread email…")
    emails = fetch_emails(gmail_service())
    print(f"  {len(emails)} unread.")

    print("→ Reading recent Notes…")
    notes = fetch_notes()
    print(f"  {len(notes)} notes.\n")

    if not emails and not notes:
        print("Nothing to brief on. Enjoy the quiet. ☕")
        return

    print("→ Thinking (Gemma 4)…\n")
    briefing = summarize(build_prompt(emails, notes))

    print("─" * 60)
    print(briefing)
    print("─" * 60)

    out_path = os.path.join(HERE, "today.txt")
    with open(out_path, "w") as f:
        f.write(f"Daily Briefing — {today}\n\n{briefing}\n")
    print(f"\nSaved to {out_path}")

    send_briefing_email(briefing, today)


if __name__ == "__main__":
    main()