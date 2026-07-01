#!/usr/bin/env bash
# Launch the voice assistant. Settings come from voice_assistant/.env
# (auto-loaded). Any args are passed through, e.g.:
#   ./run.sh                 # start the always-on daemon
#   ./run.sh --text "remind me to drink water in 2 minutes"
#   ./run.sh --auth-email    # one-time Gmail send consent
set -euo pipefail
cd "$(dirname "$0")"
exec venv/bin/python -m voice_assistant.main "$@"
