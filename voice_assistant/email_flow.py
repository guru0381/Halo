"""Email slice: intent -> resolve recipient -> draft -> (confirm elsewhere) -> send.

`prepare` is pure (no network): it validates the intent, resolves the recipient
against the allowlist, and returns a ready-to-review draft or a problem to read
back. Sending is a separate step so the caller can gate it behind confirmation.
"""

from . import contacts, gmail_client


def prepare(intent):
    """Return {ok, draft|None, message}.

    On ok=True, `draft` is {to, subject, body}. On ok=False, `message` explains
    what to tell the user (no contact match, ambiguous, or missing content).
    """
    recipient = intent.get("recipient", "")
    subject = intent.get("subject", "") or "(no subject)"
    body = intent.get("body", "")

    if intent.get("needs_clarification") or not recipient:
        return {"ok": False, "draft": None,
                "message": "Who should I email, and what should it say?"}
    if not body:
        return {"ok": False, "draft": None,
                "message": f"What should I say to {recipient}?"}

    match = contacts.resolve(recipient)
    if match["status"] == "none":
        known = ", ".join(match["candidates"])
        return {"ok": False, "draft": None,
                "message": f"I don't have a contact matching '{recipient}'. "
                           f"Known contacts: {known}."}
    if match["status"] == "ambiguous":
        opts = " or ".join(match["candidates"])
        return {"ok": False, "draft": None,
                "message": f"'{recipient}' could be {opts}. Which one?"}

    return {
        "ok": True,
        "draft": {"to": match["email"], "subject": subject, "body": body},
        "message": "",
    }


def render(draft):
    """A human-readable preview of a draft for console/voice read-back."""
    return (f"To: {draft['to']}\n"
            f"Subject: {draft['subject']}\n"
            f"\n{draft['body']}")


def send(draft):
    """Actually send the draft. Returns the Gmail API response."""
    return gmail_client.send(draft["to"], draft["subject"], draft["body"])
