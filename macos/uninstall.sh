#!/bin/bash
# Removes Jarvis from this Mac: Jarvis.app, its private Python environment, its copy
# of the code, its login item and the human voice (its environment and model). Your data
# (Gmail login, settings, alarms, notes, voice recordings) stays in
# ~/Library/Application Support/Jarvis unless you run it with --everything.
#
#     bash macos/uninstall.sh               keep your data
#     bash macos/uninstall.sh --everything  remove that too
set -euo pipefail

SUPPORT="$HOME/Library/Application Support/Jarvis"

if pgrep -qx Jarvis; then
    pkill -TERM -x Jarvis || true
    sleep 2
    if pgrep -qx Jarvis; then
        echo "Jarvis is running. Quit it first (Cmd+Q in its window, or its menu bar icon > Quit), then run this again."
        exit 1
    fi
fi

rm -rf "$HOME/Applications/Jarvis.app" "$SUPPORT/venv" "$SUPPORT/app" "$SUPPORT/voice-engine"
rm -f "$HOME/Library/LaunchAgents/local.jarvis.assistant.plist"
echo "Removed Jarvis.app, its Python environment, its code, its login item and the human voice."

if [ "${1:-}" = "--everything" ]; then
    rm -rf "$SUPPORT"
    echo "Removed your Jarvis data as well ($SUPPORT)."
else
    echo "Your data is still in $SUPPORT (run with --everything to remove it)."
fi
