#!/bin/bash
# Collects what is needed to see why Jarvis misbehaves on a Mac, into macos/diagnose.txt:
# whether it runs, the end of its log, its app settings and macOS's microphone decisions
# about it. Nothing private: no mail, no login, no settings.json (it can hold an API key).
#
#     bash macos/diagnose.sh
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/diagnose.txt"
SUPPORT="$HOME/Library/Application Support/Jarvis"
APP="$HOME/Applications/Jarvis.app"
{
    echo "== $(date '+%Y-%m-%d %H:%M:%S'), macOS $(sw_vers -productVersion) on $(uname -m)"
    echo
    echo "== Is Jarvis running?"
    pgrep -lx Jarvis || echo "No."
    echo
    echo "== Jarvis.app"
    /usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" -c "Print :NSMicrophoneUsageDescription" \
        -c "Print :LSUIElement" "$APP/Contents/Info.plist" 2>&1
    codesign -dv "$APP" 2>&1 | grep -E "^(Identifier|Signature|Format)" || true
    echo
    echo "== The last 150 lines of app.log"
    tail -n 150 "$SUPPORT/app.log" 2>&1
    echo
    echo "== The human voice"
    if [ -d "$SUPPORT/voice-engine" ]; then
        cat "$SUPPORT/voice-engine/ready.json" 2>&1
        echo
        du -sh "$SUPPORT/voice-engine/venv" "$SUPPORT/voice-engine/model" 2>&1
        tail -n 40 "$SUPPORT/voice-engine/worker.log" 2>&1
    else
        echo "Not installed."
    fi
    echo
    echo "== macOS's microphone decisions in the last hour"
    log show --last 1h --style compact \
        --predicate 'subsystem == "com.apple.TCC" AND (eventMessage CONTAINS[c] "jarvis" OR eventMessage CONTAINS "Microphone")' \
        2>&1 | tail -n 60
} > "$OUT" 2>&1
echo "Saved $OUT"
