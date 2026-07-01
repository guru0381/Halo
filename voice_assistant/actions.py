"""Side effects: native macOS banners and (optional) iMessage, via osascript.

All dynamic text is passed as a properly-quoted AppleScript string literal —
never f-string-interpolated into the script body — so a reminder containing
quotes or backslashes can't break out of the script.
"""

import subprocess

from . import config


def _as(s):
    """Quote a Python string as an AppleScript string literal."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title, message, sound="Glass"):
    """Show a native macOS notification banner. Returns True on success."""
    script = (
        f"display notification {_as(message)} "
        f"with title {_as(title)} sound name {_as(sound)}"
    )
    res = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True
    )
    if res.returncode != 0:
        print(f"  (banner failed: {res.stderr.strip()})")
    return res.returncode == 0


def send_imessage(handle, text):
    """Send an iMessage to `handle` (phone or email). Returns True on success."""
    if not handle:
        return False
    script = f'''
    tell application "Messages"
        set svc to 1st account whose service type = iMessage
        set toBuddy to participant {_as(handle)} of svc
        send {_as(text)} to toBuddy
    end tell
    '''
    res = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True
    )
    if res.returncode != 0:
        print(f"  (iMessage failed: {res.stderr.strip()})")
    return res.returncode == 0


def fire_reminder(text):
    """What happens when a reminder comes due: banner + optional self-iMessage."""
    if config.NOTIFY:
        notify("⏰ Reminder", text)
    if config.ENABLE_IMESSAGE and config.IMESSAGE_HANDLE:
        send_imessage(config.IMESSAGE_HANDLE, f"⏰ Reminder: {text}")
