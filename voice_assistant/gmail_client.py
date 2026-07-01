"""Minimal Gmail SEND client for the assistant.

Uses OAuth (not an app password) with the gmail.send scope only. Keeps its own
token file, written 0600, separate from the read-only token used by
briefing.py/digest.py. `build_raw` is pure and unit-testable offline; only
`send` and `authorize` touch the network.
"""

import base64
import os
from email.message import EmailMessage

from . import config


def build_raw(to, subject, body, sender=None):
    """Return the base64url-encoded RFC-2822 message Gmail's API expects."""
    msg = EmailMessage()
    msg["To"] = to
    if sender:
        msg["From"] = sender
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _save_token(creds):
    """Write the token with owner-only perms (0600)."""
    fd = os.open(config.ASSISTANT_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())


def authorize(interactive=True):
    """Return valid creds, running the one-time browser consent if needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if os.path.exists(config.ASSISTANT_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(
            config.ASSISTANT_TOKEN_PATH, config.GMAIL_SCOPES
        )
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)
        return creds
    if not interactive:
        raise RuntimeError(
            "No Gmail authorization yet. Run once:  "
            "python -m voice_assistant.main --auth-email"
        )
    if not os.path.exists(config.CREDENTIALS_PATH):
        raise RuntimeError(f"Missing credentials.json at {config.CREDENTIALS_PATH}")
    flow = InstalledAppFlow.from_client_secrets_file(
        config.CREDENTIALS_PATH, config.GMAIL_SCOPES
    )
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    return creds


def _service(interactive=False):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=authorize(interactive=interactive))


def send(to, subject, body):
    """Send the email. Returns the Gmail API response (has the message 'id')."""
    raw = build_raw(to, subject, body)
    return (
        _service()
        .users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
