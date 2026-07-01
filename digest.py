#!/usr/bin/env python3
"""
Monthly Mail Digest — fully local classification, emailed to you.

Scans ALL Gmail from the last month, classifies each message one-at-a-time
with Gemma 4 (offline in Ollama), and emails you a categorized digest:
ACTION, TRAVEL, ORDERS (with arrival date), RETURNS (with drop-off deadline),
SUBSCRIPTION/PAYMENT, and FYI. Promotions/noise are dropped.

Each email is classified individually so nothing overflows the model's
context — this trades runtime (15-30 min for ~200 mails on an 8 GB Air)
for reliability. Classification happens on-device.
"""

import os
import re
import json
import base64
import smtplib
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
# Config
# ---------------------------------------------------------------------------
MODEL = "gemma4:e2b"
OLLAMA_URL = "http://localhost:11434/api/generate"
LOOKBACK_DAYS = 30
MAX_EMAILS = 250                 # safety cap
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
HERE = os.path.dirname(os.path.abspath(__file__))

EMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")

# Category order as it appears in the digest. PROMO is dropped from output.
CATEGORIES = [
    ("ACTION",       "✅ Action / Reminders"),
    ("TRAVEL",       "✈️  Travel"),
    ("ORDER",        "📦 Orders / Deliveries"),
    ("RETURN",       "↩️  Returns"),
    ("SUBSCRIPTION", "💳 Subscriptions / Payments"),
    ("FYI",          "📰 Worth Knowing"),
]
VALID = {c for c, _ in CATEGORIES} | {"PROMO", "JOB"}


# ---------------------------------------------------------------------------
# Gmail (read)
# ---------------------------------------------------------------------------
def gmail_service():
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
                raise SystemExit("Missing credentials.json next to this script.")
            flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _decode_body(payload):
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
    """All mail from the last LOOKBACK_DAYS, paginating through results."""
    after = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y/%m/%d")
    # Primary tab only — skips the Promotions and Social tabs entirely.
    query = f"after:{after} category:primary"
    ids, page_token = [], None
    while len(ids) < MAX_EMAILS:
        resp = (
            service.users().messages()
            .list(userId="me", q=query, maxResults=100, pageToken=page_token)
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    ids = ids[:MAX_EMAILS]

    out = []
    for mid in ids:
        msg = (service.users().messages()
               .get(userId="me", id=mid, format="full").execute())
        headers = {h["name"].lower(): h["value"]
                   for h in msg["payload"]["headers"]}
        body = _decode_body(msg["payload"]).strip()
        out.append({
            "from": headers.get("from", "(unknown)"),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "snippet": (body or msg.get("snippet", ""))[:1200],
        })
    return out


# ---------------------------------------------------------------------------
# Gemma 4 classification (one email at a time)
# ---------------------------------------------------------------------------
CLASSIFY_SYSTEM = """You classify a single email into exactly ONE category and
write ONE short summary line. Reply ONLY with a JSON object, no other text:
{"category": "<CATEGORY>", "summary": "<one line>", "date": "<date or empty>"}

Categories:
- ACTION: needs the user to do something (reply, confirm, a deadline, a meeting)
- TRAVEL: flights, hotels, reservations, itineraries
- ORDER: a purchase shipped or in transit. Put the expected ARRIVAL/delivery date in "date" if present.
- RETURN: a return the user initiated. Put the drop-off / return-by DEADLINE in "date" if present.
- SUBSCRIPTION: recurring charge, renewal, or receipt for something paid
- FYI: genuinely informative, worth knowing, not promotional
- JOB: anything about job applications, job postings, application status, recruiting, or hiring ("thanks for applying", "keep track of your application", "new jobs posted", interview scheduling for a job)
- PROMO: marketing, newsletters, sales, ads, social notifications — anything not worth the user's attention

Rules: Pick the single best category. Keep "summary" under 18 words, concrete,
naming the sender/company. Use "date" only for ORDER and RETURN (format like
"Jun 18"); otherwise leave it "". Job-related mail is always JOB. When unsure
between PROMO and FYI, choose PROMO."""


def classify(email):
    prompt = (f"From: {email['from']}\nSubject: {email['subject']}\n"
              f"Body:\n{email['snippet']}")
    payload = {
        "model": MODEL,
        "system": CLASSIFY_SYSTEM,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_ctx": 4096},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        r.raise_for_status()
        data = json.loads(r.json()["response"])
        cat = str(data.get("category", "PROMO")).upper().strip()
        if cat not in VALID:
            cat = "PROMO"
        return {
            "category": cat,
            "summary": str(data.get("summary", "")).strip(),
            "date": str(data.get("date", "")).strip(),
        }
    except Exception:
        return {"category": "PROMO", "summary": "", "date": ""}


# ---------------------------------------------------------------------------
# Build + send digest
# ---------------------------------------------------------------------------
def build_digest(results, scanned, today):
    buckets = {c: [] for c, _ in CATEGORIES}
    for r in results:
        if r["category"] in buckets and r["summary"]:
            line = r["summary"]
            if r["date"]:
                line += f"  —  {r['date']}"
            buckets[r["category"]].append(line)

    kept = sum(len(v) for v in buckets.values())
    lines = [f"Monthly Mail Digest — {today}",
             f"Scanned {scanned} emails from the last {LOOKBACK_DAYS} days; "
             f"{kept} worth your attention (promotions dropped).", ""]

    for cat, label in CATEGORIES:
        items = buckets[cat]
        if not items:
            continue
        lines.append(label)
        for it in items:
            lines.append(f"  • {it}")
        lines.append("")

    if kept == 0:
        lines.append("Nothing notable — it was all promotions and noise.")
    return "\n".join(lines)


def send_email(body, today):
    if not APP_PASSWORD:
        print("⚠️  No app password set — printing instead of emailing.\n")
        print(body)
        return
    msg = EmailMessage()
    msg["Subject"] = f"📬 Monthly Mail Digest — {today}"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = EMAIL_ADDRESS
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, APP_PASSWORD)
            server.send_message(msg)
        print(f"✉️  Digest emailed to {EMAIL_ADDRESS}")
    except Exception as e:
        print(f"⚠️  Could not send email: {e}\n")
        print(body)


# ---------------------------------------------------------------------------
def main():
    today = dt.date.today().strftime("%A, %B %d")
    print(f"\n📬  Monthly Mail Digest — {today}")
    print(f"    Scanning all mail from the last {LOOKBACK_DAYS} days, "
          f"classifying locally with Gemma 4.\n")

    print("→ Fetching email…")
    emails = fetch_emails(gmail_service())
    print(f"  {len(emails)} emails to classify.")
    print("  (this takes a while — roughly a few seconds each)\n")

    results = []
    for i, e in enumerate(emails, 1):
        res = classify(e)
        results.append(res)
        tag = res["category"]
        print(f"  [{i}/{len(emails)}] {tag:<12} {e['subject'][:50]}")

    print("\n→ Building digest…\n")
    digest = build_digest(results, len(emails), today)

    out_path = os.path.join(HERE, "digest.txt")
    with open(out_path, "w") as f:
        f.write(digest + "\n")
    print(f"Saved to {out_path}\n")

    send_email(digest, today)


if __name__ == "__main__":
    main()