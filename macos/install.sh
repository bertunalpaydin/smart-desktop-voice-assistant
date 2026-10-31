#!/bin/bash
# Installs Jarvis on a Mac. Run it from Terminal:  bash macos/install.sh
#
# What it does, all inside your own user folder (no administrator password):
#   1. finds Python 3.13 (or 3.12) from python.org or Homebrew
#   2. makes a private Python environment in ~/Library/Application Support/Jarvis/venv
#   3. installs the exact package versions listed in requirements-py3XX.txt, from PyPI
#      only. pip checks every file against its SHA-256 hash and only accepts ready-made
#      packages, so no package runs code while it is being installed.
#   4. copies Jarvis's code to ~/Library/Application Support/Jarvis/app
#   5. builds Jarvis.app in ~/Applications
# Running it again updates Jarvis. Everything it prints also goes to macos/install.log.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(dirname "$HERE")"
SUPPORT="$HOME/Library/Application Support/Jarvis"
VENV="$SUPPORT/venv"
CODE="$SUPPORT/app"
APPS="$HOME/Applications"
LOG="$HERE/install.log"

exec > >(tee "$LOG") 2>&1

step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*"; exit 1; }

echo "Jarvis installer, $(date '+%Y-%m-%d %H:%M')"

# --- 1. the Mac and Python ------------------------------------------------------------
step "Checking this Mac"
[ "$(uname -s)" = "Darwin" ] || fail "This installer is for macOS. On Windows, run install.bat."
macos_version="$(sw_vers -productVersion)"
macos_major="${macos_version%%.*}"
echo "macOS $macos_version on $(uname -m)"
[ "$macos_major" -ge 13 ] || fail "Jarvis needs macOS 13 (Ventura) or newer."
[ "$(uname -m)" = "arm64" ] || fail "Jarvis's packages are locked for Apple silicon (M1 and later) Macs."

is_framework_python() {
    "$1" -c 'import sys, sysconfig; sys.exit(0 if sysconfig.get_config_var("PYTHONFRAMEWORK") else 1)' 2>/dev/null
}

PY=""
for candidate in \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
    /opt/homebrew/opt/python@3.13/bin/python3.13 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
    /opt/homebrew/opt/python@3.12/bin/python3.12; do
    if [ -x "$candidate" ] && is_framework_python "$candidate"; then
        PY="$candidate"
        break
    fi
done
if [ -z "$PY" ]; then
    fail "Python 3.13 isn't installed. Install it from python.org (it's free and signed by the
Python Software Foundation), then run this again:
    https://www.python.org/ftp/python/3.13.17/python-3.13.17-macos11.pkg
(Not 3.14 or 3.15: some of Jarvis's packages aren't ready for them on macOS yet.)"
fi
PY_VERSION="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
LOCK="$HERE/requirements-py${PY_VERSION/./}.txt"
echo "Using Python $("$PY" -c 'import sys; print(sys.version.split()[0])') ($PY)"
[ -f "$LOCK" ] || fail "There is no package list for Python $PY_VERSION ($LOCK)."

WAS_RUNNING=false
if pgrep -qx Jarvis; then
    step "Closing Jarvis to update it (it opens again at the end)"
    WAS_RUNNING=true
    pkill -TERM -x Jarvis || true
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        pgrep -qx Jarvis || break
        sleep 0.5
    done
    if pgrep -qx Jarvis; then
        fail "Jarvis didn't close. Quit it (Cmd+Q in its window, or its menu bar icon > Quit), then run this again."
    fi
    echo "Closed."
fi

# --- 2. the private Python environment ----------------------------------------------------
step "Preparing Jarvis's own Python environment"
mkdir -p "$SUPPORT"
if [ -x "$VENV/bin/python" ] && [ "$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" != "$PY_VERSION" ]; then
    echo "The old environment uses another Python version; making a new one."
    rm -rf "$VENV"
fi
if [ ! -x "$VENV/bin/python" ]; then
    "$PY" -m venv "$VENV"
fi
echo "$VENV"

# --- 3. packages ----------------------------------------------------------------------------
step "Installing packages (exact versions, every file checked against its SHA-256 hash)"
"$VENV/bin/python" -m pip install --isolated --disable-pip-version-check --no-input \
    --index-url https://pypi.org/simple --require-hashes --only-binary=:all: --no-deps \
    -r "$LOCK"

step "Checking the packages load"
"$VENV/bin/python" -c "import PyQt6.QtWidgets, numpy, onnxruntime, openwakeword, sounddevice, speech_recognition, googleapiclient, psutil, certifi" \
    || fail "A package didn't load; see the messages above."
echo "All load."

# --- 4. Jarvis's code ------------------------------------------------------------------------
step "Copying Jarvis's code"
mkdir -p "$CODE/macos"
rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' "$PROJECT/jarvis/" "$CODE/jarvis/"
cp "$PROJECT/main.py" "$PROJECT/app.pyw" "$PROJECT/README.md" "$CODE/"
cp "$HERE/launcher.py" "$HERE/setup.py" "$HERE/check.py" "$HERE/Jarvis.icns" "$CODE/macos/"
printf '%s\n' "$PROJECT" > "$CODE/source.txt"  # so Jarvis can say where credentials.json goes
echo "$CODE"

if [ -f "$PROJECT/secrets/credentials.json" ] && [ ! -f "$SUPPORT/credentials.json" ]; then
    cp "$PROJECT/secrets/credentials.json" "$SUPPORT/credentials.json"
    chmod 600 "$SUPPORT/credentials.json"
    echo "Copied the Gmail credentials file (secrets/credentials.json)."
fi

step "Downloading the \"Hey Jarvis\" model (openWakeWord's own release on GitHub, a few MB)"
"$VENV/bin/python" -c "import openwakeword.utils; openwakeword.utils.download_models(model_names=['hey_jarvis'])" \
    || echo "Couldn't download it now; Jarvis will try again when it starts."

# --- 5. Jarvis.app -----------------------------------------------------------------------------
step "Building Jarvis.app"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
(cd "$CODE/macos" && "$VENV/bin/python" setup.py -q py2app -A --dist-dir "$BUILD/dist" --bdist-base "$BUILD/build")
[ -d "$BUILD/dist/Jarvis.app" ] || fail "py2app didn't make Jarvis.app; see the messages above."
mkdir -p "$APPS"
rm -rf "$APPS/Jarvis.app"
mv "$BUILD/dist/Jarvis.app" "$APPS/Jarvis.app"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$APPS/Jarvis.app" || true
echo "$APPS/Jarvis.app"

step "Checking the Mac parts Jarvis uses"
"$VENV/bin/python" "$CODE/macos/check.py" || fail "Some packages don't work; see above."

step "Done"
echo "Jarvis is installed. Open it from Launchpad, Spotlight (Cmd+Space, \"Jarvis\") or ~/Applications."
echo "The first time, macOS asks whether Jarvis may use the microphone: choose Allow."
if [ ! -f "$SUPPORT/credentials.json" ]; then
    echo "For Gmail, put credentials.json from Google Cloud in $PROJECT/secrets and run this again."
fi
if [ ! -f "$SUPPORT/voice-engine/ready.json" ]; then
    echo "For a human voice instead of the Mac's (optional, about 4 GB): bash macos/install_voice.sh"
fi
echo "To remove Jarvis later: bash macos/uninstall.sh"
if [ "$WAS_RUNNING" = true ]; then
    open "$APPS/Jarvis.app"
    echo "Jarvis is open again, updated."
fi
