#!/usr/bin/env bash
# SessionStart hook: make the project runnable in a fresh Claude Code (web)
# container by installing dependencies so tests, the preview tool, and the
# Streamlit app all work. Kept quiet and non-fatal so it never blocks a session.
set -u

cd "$(dirname "$0")/../.." || exit 0

if [ -f requirements.txt ]; then
  python3 -m pip install -q -r requirements.txt 2>/dev/null \
    && echo "[setup] dependencies installed" \
    || echo "[setup] dependency install skipped (offline or already present)"
fi

exit 0
