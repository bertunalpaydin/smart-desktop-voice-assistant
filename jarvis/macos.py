"""What Jarvis needs from macOS: the voice, sounds, notifications, apps, music
players, the volume, the screen and opening at login.

Only tools that come with macOS are used (say, afplay, osascript, open, pmset,
pgrep, screencapture), so nothing extra is installed. Text never becomes part of
a script: it reaches `say` on stdin and AppleScript as an argument.
"""
import os
import plistlib
import re
import subprocess
import sys
import threading
from pathlib import Path

BUNDLE_ID = "local.jarvis.assistant"  # Jarvis.app's bundle id, also the login item's name
APP_NAME = "Jarvis.app"
APP_FOLDERS = [
    Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"),
    Path("/System/Applications/Utilities"), Path.home() / "Applications",
]
EXTRA_APPS = [Path("/System/Library/CoreServices/Finder.app")]
PLAYERS = ("Spotify", "Music")  # the music apps Jarvis can pause and skip in
PLAYER_COMMANDS = {"play_pause": "playpause", "next": "next track", "previous": "previous track", "stop": "pause"}
PREFERRED_VOICES = ("Daniel", "Oliver", "Arthur")  # British voices that come with macOS

# Every value given to a script starts with "x", so osascript never mistakes one for
# an option (a mail subject such as "-e ..." must stay text); valueAt() takes the x off.
_ARG_HANDLER = [
    "on valueAt(i, argv)",
    "set s to item i of argv",
    "if (count of s) < 2 then return \"\"",
    "return text 2 thru -1 of s",
    "end valueAt",
]
_VOICE_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<locale>[a-z]{2,3}[_-][A-Za-z0-9]{2,4})\s+#")


class MacError(Exception):
    """macOS said no, or one of its tools failed."""


def available():
    return sys.platform == "darwin"


def _run(args, timeout=15):
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MacError(str(exc)) from exc
    if done.returncode != 0:
        raise MacError((done.stderr or done.stdout).strip() or f"{args[0]} failed")
    return done.stdout.strip()


def osascript(lines, *values, timeout=15):
    """Run AppleScript lines inside `on run argv`. `values` are read with valueAt(1, argv), ..."""
    args = ["osascript"]
    for line in [*_ARG_HANDLER, "on run argv", *lines, "end run"]:
        args += ["-e", line]
    return _run(args + ["x" + str(value) for value in values], timeout=timeout)


# --- the voice ----------------------------------------------------------------------

_speaking = None  # the `say` process, so stop_speaking() can end it
_speaking_lock = threading.Lock()


def words_per_minute(rate):
    """Jarvis's speed setting (-10 to 10) as `say` understands it."""
    return 185 + 11 * max(-10, min(10, int(rate)))


def voices():
    """The English voices on this Mac, as [{"name", "culture"}]."""
    found = []
    for line in _run(["say", "-v", "?"]).splitlines():
        match = _VOICE_LINE.match(line.strip())
        if match and match.group("locale").lower().startswith("en"):
            found.append({"name": match.group("name"), "culture": match.group("locale").replace("_", "-")})
    return found


def pick_voice(wanted, installed):
    """The voice to use: the one asked for, else a British one, else any English one, else ""."""
    names = [v["name"] for v in installed]
    if wanted:
        return next((n for n in names if wanted.casefold() in n.casefold()), "")
    for preferred in PREFERRED_VOICES:
        if preferred in names:
            return preferred
    for culture in ("en-GB", "en-"):
        match = next((v["name"] for v in installed if v["culture"].startswith(culture)), "")
        if match:
            return match
    return ""


def say(text, voice="", rate=0, timeout=300):
    """Read text aloud and wait until it's done. Returns False if stop_speaking() cut it off."""
    global _speaking
    args = ["say", "-r", str(words_per_minute(rate))] + (["-v", voice] if voice else [])
    try:
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        raise MacError(str(exc)) from exc
    with _speaking_lock:
        _speaking = process
    try:
        _, error = process.communicate(text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise MacError("reading aloud took too long") from exc
    finally:
        with _speaking_lock:
            _speaking = None
    if process.returncode < 0:  # ended by a signal: stop_speaking()
        return False
    if process.returncode != 0:
        raise MacError((error or "").strip() or "say failed")
    return True


def speak_file(path, timeout=300):
    """Play spoken audio and wait until it's done; like say(), stop_speaking() cuts it off (False)."""
    global _speaking
    try:
        process = subprocess.Popen(["afplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   text=True)
    except OSError as exc:
        raise MacError(str(exc)) from exc
    with _speaking_lock:
        _speaking = process
    try:
        _, error = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise MacError("playing the speech took too long") from exc
    finally:
        with _speaking_lock:
            _speaking = None
    if process.returncode < 0:
        return False
    if process.returncode != 0:
        raise MacError((error or "").strip() or "afplay failed")
    return True


def stop_speaking():
    with _speaking_lock:
        process = _speaking
    if process is None or process.poll() is not None:
        return False
    process.terminate()
    return True


# --- sounds -------------------------------------------------------------------------

_player = None  # the afplay process of a looping sound
_player_lock = threading.Lock()
_looping = threading.Event()


def play(path):
    """Play a sound file once, without waiting."""
    try:
        subprocess.Popen(["afplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass  # no sound is better than a crash


def loop(path):
    """Play a sound file again and again until stop_sounds()."""
    stop_sounds()
    _looping.set()
    threading.Thread(target=_loop, args=(str(path),), daemon=True).start()


def _loop(path):
    global _player
    while _looping.is_set():
        try:
            process = subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return
        with _player_lock:
            _player = process
        if not _looping.is_set():  # stopped while it was starting
            process.terminate()
        if process.wait() != 0 and _looping.is_set():
            return  # afplay can't play it: don't spin


def stop_sounds():
    global _player
    _looping.clear()
    with _player_lock:
        process, _player = _player, None
    if process is not None and process.poll() is None:
        process.terminate()


# --- notifications --------------------------------------------------------------------

def notify(title, message):
    osascript(["display notification (valueAt(2, argv)) with title (valueAt(1, argv))"], title, message)


# --- apps ------------------------------------------------------------------------------

def installed_apps(folders=None, extra=None):
    """The apps on this Mac, as {name: path}, e.g. {"Spotify": "/Applications/Spotify.app"}."""
    apps = {}
    for folder in APP_FOLDERS if folders is None else folders:
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix == ".app":
                apps.setdefault(entry.stem, str(entry))
            elif entry.is_dir() and not entry.name.startswith("."):  # e.g. /Applications/Adobe Photoshop 2026/
                try:
                    inner = sorted(entry.iterdir())
                except OSError:
                    continue
                for app in inner:
                    if app.suffix == ".app":
                        apps.setdefault(app.stem, str(app))
    for app in EXTRA_APPS if extra is None else extra:
        if app.exists():
            apps.setdefault(app.stem, str(app))
    return apps


def open_path(path):
    """Open an app, a file or a folder, as a double-click would."""
    _run(["open", str(path)])


def open_app(name):
    """Open an app by its name, e.g. "System Settings"."""
    _run(["open", "-a", name])


def executable(app_path):
    """The name of an app's program (what Activity Monitor shows), from its Info.plist."""
    try:
        with open(Path(app_path) / "Contents" / "Info.plist", "rb") as file:
            name = plistlib.load(file).get("CFBundleExecutable")
    except (OSError, ValueError, plistlib.InvalidFileException):
        name = None
    return name if isinstance(name, str) and name else Path(app_path).stem


def is_running(program):
    """True if a program with exactly this name is running (no Apple Events needed)."""
    try:
        return subprocess.run(["pgrep", "-x", program], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def quit_app(name, app_path):
    """Ask a running app to quit, like Cmd+Q (it can still ask to save). Returns 1, or 0 if it wasn't open."""
    if not is_running(executable(app_path)):
        return 0
    osascript(["tell application (valueAt(1, argv)) to quit"], name)
    return 1


def media(action):
    """Play/pause, skip or go back in Spotify or Music, whichever is open. Returns its name."""
    for player in PLAYERS:
        if is_running(player):
            osascript([f'tell application "{player}" to {PLAYER_COMMANDS[action]}'])
            return player
    return None


# --- volume, screen -------------------------------------------------------------------

def set_volume(percent):
    osascript(["set volume output volume (valueAt(1, argv) as integer)"], max(0, min(100, round(percent))))


def change_volume(step):
    osascript([
        "set v to (output volume of (get volume settings)) + (valueAt(1, argv) as integer)",
        "if v > 100 then set v to 100",
        "if v < 0 then set v to 0",
        "set volume output volume v",
    ], int(step))


def toggle_mute():
    osascript(["set volume output muted (not (output muted of (get volume settings)))"])


def sleep_display():
    """Turn the screen off; it locks if your Mac asks for the password after sleep."""
    _run(["pmset", "displaysleepnow"])


def screenshot(path):
    """Save the whole screen as a picture. macOS asks once for Screen Recording permission."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _run(["screencapture", "-x", str(path)], timeout=30)
    return Path(path).exists()


# --- opening at login --------------------------------------------------------------------

def launch_agent():
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def app_bundle():
    """The Jarvis.app this is running from, else the installed one, else None."""
    resources = os.environ.get("RESOURCEPATH")  # set inside an app bundle
    if resources:
        bundle = Path(resources).parent.parent
        if bundle.suffix == ".app":
            return bundle
    installed = Path.home() / "Applications" / APP_NAME
    return installed if installed.exists() else None


def _agent():
    bundle = app_bundle()
    if bundle is None:
        return None
    return {
        "Label": BUNDLE_ID,
        "ProgramArguments": ["/usr/bin/open", "-g", "-a", str(bundle)],  # -g: in the background
        "RunAtLoad": True,
        "LimitLoadToSessionType": "Aqua",
        "AssociatedBundleIdentifiers": [BUNDLE_ID],  # Login Items then shows it as Jarvis
    }


def starts_at_login():
    try:
        with open(launch_agent(), "rb") as file:
            current = plistlib.load(file)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    return _agent() is not None and current.get("ProgramArguments") == _agent()["ProgramArguments"]


def set_start_at_login(on):
    path = launch_agent()
    if not on:
        path.unlink(missing_ok=True)
        return
    agent = _agent()
    if agent is None:
        raise MacError("Jarvis.app isn't installed; run macos/install.sh first")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as file:
        plistlib.dump(agent, file)
