"""Load KEY=value pairs from a local .env into the environment.

Used by briefing.py and digest.py so secrets (GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
live in a gitignored .env instead of in source. Shell exports take precedence.
Zero dependencies.
"""

import os


def load_env(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
