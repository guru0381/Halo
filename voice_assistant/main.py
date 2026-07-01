"""Entry point + command dispatch.

Modes:
  python -m voice_assistant.main                      # daemon: wake word -> ... -> action
  python -m voice_assistant.main --text "remind me to call my sister in 30 minutes"
  python -m voice_assistant.main --text "email Nikitha I'll be late" --yes   # auto-confirm send
  python -m voice_assistant.main --auth-email         # one-time Gmail send consent
  python -m voice_assistant.main --test-notify        # fire a sample banner now
"""

import argparse
import sys
import time

from . import actions, brain, config, email_flow
from .scheduler import Scheduler

AFFIRMATIVE = {"yes", "yeah", "yep", "yup", "send", "send it", "confirm",
               "do it", "sure", "ok", "okay", "go ahead", "please do"}
NEGATIVE = {"no", "nope", "cancel", "stop", "don't", "do not", "nevermind",
            "never mind", "discard"}


def _is_affirmative(text):
    t = (text or "").lower().strip().rstrip(".!")
    if any(w in t for w in NEGATIVE):
        return False
    return any(w in t for w in AFFIRMATIVE)


def _handle_email(intent, listen_again, auto_yes):
    res = email_flow.prepare(intent)
    if not res["ok"]:
        print(f"   ❓ {res['message']}")
        actions.notify("Assistant", res["message"])
        return

    draft = res["draft"]
    print("\n   ✉️  Draft ready:\n" + "\n".join(
        "      " + ln for ln in email_flow.render(draft).splitlines()) + "\n")
    actions.notify("✉️ Draft ready",
                   f"To {draft['to']} — {draft['subject']}. Confirm to send.")

    # Confirmation gate — nothing is sent without an explicit yes.
    if config.EMAIL_REQUIRE_CONFIRM:
        if listen_again is not None:            # voice mode: ask out loud
            print("   🎤 Say 'send' to confirm, or 'cancel'…")
            actions.notify("✉️ Confirm", "Say 'send' to confirm or 'cancel'.")
            reply = listen_again() or ""
            print(f'   confirm heard: "{reply}"')
            confirmed = _is_affirmative(reply)
        else:                                   # text mode: only --yes confirms
            confirmed = auto_yes
            if not confirmed:
                print("   ⏸  Not sent (confirmation required). "
                      "Re-run with --yes to send, or confirm by voice in daemon mode.")
    else:
        confirmed = True

    if not confirmed:
        print("   🚫 Cancelled.")
        actions.notify("Assistant", "Okay, cancelled — nothing sent.")
        return

    try:
        resp = email_flow.send(draft)
        print(f"   ✅ Sent to {draft['to']} (id {resp.get('id', '?')}).")
        actions.notify("✅ Email sent", f"To {draft['to']}: {draft['subject']}")
    except Exception as e:
        print(f"   ⚠️  Send failed: {e}")
        actions.notify("Assistant", f"Couldn't send the email: {e}")


def dispatch(text, sched, listen_again=None, auto_yes=False):
    """Interpret one command and take the matching action."""
    try:
        intent = brain.extract_intent(text)
    except brain.BrainError as e:
        print(f"⚠️  {e}")
        actions.notify("Assistant", "I couldn't reach the local model.")
        return

    action = intent["action"]

    if action == "reminder":
        if intent["needs_clarification"] or intent["delay_minutes"] <= 0:
            print("   ❓ Reminder has no clear time — say e.g. 'in 30 minutes'.")
            actions.notify("Assistant", "When should I remind you? Try 'in 30 minutes'.")
            return
        fire_at = sched.add(intent["reminder_text"], intent["delay_minutes"])
        when = time.strftime("%-I:%M %p", time.localtime(fire_at))
        print(f'   ⏰ Reminder set: "{intent["reminder_text"]}" in '
              f'{intent["delay_minutes"]} min (≈ {when}).')
        # A *scheduled* acknowledgment — future-tense and styled differently from
        # the actual reminder banner, so it isn't mistaken for the reminder firing.
        if config.ANNOUNCE_SCHEDULED:
            actions.notify(
                "✅ Reminder scheduled",
                f'I\'ll remind you to {intent["reminder_text"].lower().rstrip(".")} '
                f'at {when} (in {intent["delay_minutes"]} min).',
                sound="Pop",
            )

    elif action == "email":
        _handle_email(intent, listen_again, auto_yes)

    else:
        print("   🤷 Not a reminder or email — ignoring.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="voice_assistant")
    p.add_argument("--text", help="Run one command as text (no mic/Whisper needed).")
    p.add_argument("--yes", action="store_true",
                   help="Auto-confirm an email send in --text mode (testing).")
    p.add_argument("--auth-email", action="store_true",
                   help="Run the one-time Gmail send-scope consent in your browser.")
    p.add_argument("--test-notify", action="store_true",
                   help="Fire a sample banner to verify notifications work.")
    args = p.parse_args(argv)

    if args.test_notify:
        ok = actions.notify("⏰ Reminder", "This is a test banner from your assistant.")
        print("Banner sent." if ok else "Banner failed.")
        return

    if args.auth_email:
        from . import gmail_client
        print("→ Opening browser for Gmail send-scope consent…")
        gmail_client.authorize(interactive=True)
        print(f"✅ Authorized. Token saved to {config.ASSISTANT_TOKEN_PATH} (0600).")
        return

    sched = Scheduler()
    sched.start()

    if args.text:
        dispatch(args.text, sched, listen_again=None, auto_yes=args.yes)
        # Keep the process alive so a just-scheduled reminder can actually fire,
        # otherwise the daemon thread dies on return and short reminders are lost.
        pending = sched.pending()
        if pending:
            nxt = min(r["fire_at"] for r in pending)
            secs = max(0, int(nxt - time.time()))
            print(f"   ⏳ Waiting for {len(pending)} pending reminder(s) to fire "
                  f"(next in ~{secs}s). Ctrl-C to exit.")
            try:
                while sched.pending():
                    time.sleep(1)
                print("   ✅ All reminders fired.")
            except KeyboardInterrupt:
                print("\n👋 Exiting — unfired reminders are saved and will resume "
                      "next run (or in daemon mode).")
        sched.stop()
        return

    # Daemon mode — import the audio stack lazily so --text works without it.
    print(f"🎙️  Voice assistant up. Model: {config.MODEL}.  Ctrl-C to quit.")
    if not config.IMESSAGE_HANDLE:
        print("   (iMessage off — set ASSISTANT_IMESSAGE_HANDLE + "
              "ASSISTANT_ENABLE_IMESSAGE=1 to also text yourself.)")
    try:
        from . import listen
        listen.wake_word_loop(
            lambda text, listen_again: dispatch(text, sched, listen_again=listen_again)
        )
    except KeyboardInterrupt:
        print("\n👋 Bye.")
    except ImportError as e:
        print(f"\n⚠️  Audio stack not installed: {e}\n"
              "    pip install -r voice_assistant/requirements.txt\n"
              "    (Or test the brain now:  python -m voice_assistant.main "
              "--text \"remind me to call my sister in 1 minute\")")
        sys.exit(1)
    finally:
        sched.stop()


if __name__ == "__main__":
    main()
