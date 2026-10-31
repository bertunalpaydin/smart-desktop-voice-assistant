#!/bin/bash
# Installs Jarvis's human voice (Chatterbox-Turbo by Resemble AI, MIT licence) on a Mac.
# Run it after macos/install.sh:   bash macos/install_voice.sh
#
# What it does, all inside your own user folder (no administrator password):
#   1. makes a separate Python environment for it in
#      ~/Library/Application Support/Jarvis/voice-engine/venv, so Jarvis's own stays as it is
#   2. installs the exact package versions in macos/voice/requirements-py3XX.txt from PyPI only.
#      pip checks every file against its SHA-256 hash and only accepts ready-made packages.
#   3. downloads the models from Resemble AI's pages on Hugging Face and checks every file against
#      its checksum (macos/voice/download_model.py): Nano, the fast one, and Turbo, the larger one,
#      if it was installed before or you add --turbo
#   4. measures whether this Mac's graphics chip or its processor is faster, and plays a sentence
# Then choose it in Jarvis: Settings > Human voice. bash macos/uninstall.sh removes it with Jarvis.
# Everything it prints also goes to macos/install_voice.log.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(dirname "$HERE")"
SUPPORT="$HOME/Library/Application Support/Jarvis"
ENGINE="$SUPPORT/voice-engine"
VENV="$ENGINE/venv"
MODEL="$ENGINE/model"
LOG="$HERE/install_voice.log"

exec > >(tee "$LOG") 2>&1

step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*"; exit 1; }

echo "Jarvis human voice installer, $(date '+%Y-%m-%d %H:%M')"

step "Checking this Mac"
[ "$(uname -s)" = "Darwin" ] || fail "The human voice is for macOS."
[ "$(uname -m)" = "arm64" ] || fail "The human voice needs an Apple silicon Mac (M1 or later)."
[ -x "$SUPPORT/venv/bin/python" ] || fail "Install Jarvis first: bash macos/install.sh"
[ -f "$SUPPORT/app/jarvis/human_voice.py" ] || fail "Update Jarvis first: bash macos/install.sh"
memory_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
free_gb=$(( $(df -Pk "$HOME" | awk 'NR==2 {print $4}') / 1048576 ))
echo "macOS $(sw_vers -productVersion), ${memory_gb} GB memory, ${free_gb} GB free disk space"
[ "$free_gb" -ge 8 ] || fail "It needs about 4 GB of disk space, and some room to spare; only ${free_gb} GB is free."
if [ "$memory_gb" -lt 16 ]; then
    echo "Note: with ${memory_gb} GB of memory the human voice works, but the Mac may slow down while it talks."
fi

# the same Python as Jarvis's own environment (python.org's 3.13 or 3.12)
PY_VERSION="$("$SUPPORT/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY="$(sed -n 's/^home = //p' "$SUPPORT/venv/pyvenv.cfg")/python$PY_VERSION"
[ -x "$PY" ] || fail "Can't find the Python Jarvis was installed with ($PY). Run bash macos/install.sh again."
LOCK="$HERE/voice/requirements-py${PY_VERSION/./}.txt"
[ -f "$LOCK" ] || fail "There is no package list for Python $PY_VERSION ($LOCK)."
echo "Using Python $PY_VERSION ($PY)"

WAS_RUNNING=false
MEASURED=""
finish() {  # however it ends: tidy up, and open Jarvis again if it was open
    [ -n "$MEASURED" ] && rm -rf "$MEASURED"
    if [ "$WAS_RUNNING" = true ]; then
        open "$HOME/Applications/Jarvis.app" || true
        echo "Jarvis is open again."
    fi
}
trap finish EXIT
if pgrep -qx Jarvis; then
    step "Closing Jarvis meanwhile, so it doesn't slow the measuring down (it opens again at the end)"
    WAS_RUNNING=true
    pkill -TERM -x Jarvis || true
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        pgrep -qx Jarvis || break
        sleep 0.5
    done
    pgrep -qx Jarvis && fail "Jarvis didn't close. Quit it (Cmd+Q in its window, or its menu bar icon > Quit), then run this again."
    echo "Closed."
fi

step "Preparing the voice's own Python environment"
mkdir -p "$ENGINE"
if [ -x "$VENV/bin/python" ] && [ "$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" != "$PY_VERSION" ]; then
    rm -rf "$VENV"
fi
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
echo "$VENV"

step "Installing packages (about 250 MB to download; exact versions, every file checked against its SHA-256 hash)"
"$VENV/bin/python" -m pip install --isolated --disable-pip-version-check --no-input \
    --index-url https://pypi.org/simple --require-hashes --only-binary=:all: --no-deps \
    -r "$LOCK"

step "Checking the packages load"
"$VENV/bin/python" -I "$PROJECT/jarvis/voice_worker.py" --check 2>>"$ENGINE/worker.log" \
    || fail "The voice packages didn't load; see $ENGINE/worker.log"

download() {  # repo, folder
    HF_HOME="$ENGINE/huggingface" HF_HUB_DISABLE_TELEMETRY=1 \
        "$VENV/bin/python" -I "$HERE/voice/download_model.py" --repo "$1" --dir "$2"
}

step "Downloading the fast model, Chatterbox Nano, from Hugging Face (this takes a few minutes)"
download ResembleAI/chatterbox-nano "$ENGINE/model-nano" \
    || echo "Nano couldn't be installed now (see above); running this again carries on where it stopped."
if [ -f "$MODEL/source.json" ] || [ "${1:-}" = "--turbo" ]; then
    step "Checking the larger model, Chatterbox Turbo (downloading what is missing)"
    download ResembleAI/chatterbox-turbo "$MODEL" \
        || echo "Turbo couldn't be checked now (see above); running this again carries on where it stopped."
fi
[ -f "$ENGINE/model-nano/source.json" ] || [ -f "$MODEL/source.json" ] \
    || fail "No model could be downloaded; see the messages above. Running this again carries on where it stopped."

step "Measuring what is faster on this Mac: its graphics chip or its processor (a minute or two)"
rm -f "$ENGINE/ready.json"
MEASURED="$(mktemp -d)"
for name in nano turbo; do
    folder="$ENGINE/model-nano"
    [ "$name" = turbo ] && folder="$MODEL"
    [ -f "$folder/source.json" ] || continue
    "$VENV/bin/python" -I "$PROJECT/jarvis/voice_worker.py" --model "$folder" \
        --benchmark "$ENGINE/test-$name.wav" 2>>"$ENGINE/worker.log" | tail -n 1 > "$MEASURED/$name.json" || true
done
"$VENV/bin/python" -I - "$MEASURED" "$ENGINE/ready.json" <<'PY' || fail "The human voice couldn't speak on this Mac; see $ENGINE/worker.log"
import json, sys
from pathlib import Path
models = {}
for name, label in (("nano", "Nano"), ("turbo", "Turbo")):
    try:
        result = json.loads((Path(sys.argv[1]) / f"{name}.json").read_text())
    except (OSError, ValueError):
        continue
    if result.get("event") != "ok":
        print(f"{label} couldn't speak: {result.get('message', 'no answer')}")
        continue
    models[name] = result
    where = "the graphics chip" if result["device"] == "mps" else "the processor"
    if result.get("speed"):
        print(f"{label}: fastest on {where}, {result['speed']:.1f} times as fast as speech; "
              f"a short reply takes {result['first_piece']:.1f} s.")
    else:
        print(f"{label}: runs on {where}. It has no voice of its own: record yours in Settings > Human voice.")
if not models:
    sys.exit(1)
Path(sys.argv[2]).write_text(json.dumps({"models": models}, indent=1))
PY
for name in nano turbo; do
    if [ -f "$ENGINE/test-$name.wav" ]; then
        afplay "$ENGINE/test-$name.wav" || true
        break
    fi
done

step "Done"
echo "To use it: open Jarvis's Settings > Human voice, choose \"Human voice: Chatterbox\" and Save."
echo "There you can also record your own voice for it to speak with."
echo "Jarvis uses the Mac's voice while the human voice loads (a few seconds after Jarvis starts)."
echo "To remove it: bash macos/uninstall.sh (it goes with Jarvis), or delete $ENGINE"
