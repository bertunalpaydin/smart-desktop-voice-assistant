"""What Jarvis does on the computer itself: media keys, volume, opening apps and
websites, playing your own music, locking the screen. Windows and macOS each have
their own way of doing these; the Mac's is in macos.py."""
import base64
import difflib
import json
import os
import random
import re
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import macos, powershell

KEYS = {
    "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
    "mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
}
KEYEVENTF_KEYUP = 0x0002

# Windows' own apps have translated names in the Start menu, so these go by command.
_WINDOWS_APPS = {
    "calculator": "calc.exe", "calc": "calc.exe",
    "notepad": "notepad.exe",
    "file explorer": "explorer.exe", "explorer": "explorer.exe", "files": "explorer.exe",
    "settings": "ms-settings:", "windows settings": "ms-settings:",
    "task manager": "taskmgr.exe",
    "command prompt": "cmd.exe", "cmd": "cmd.exe",
    "terminal": "wt.exe",
    "paint": "mspaint.exe",
    "control panel": "control.exe",
    "camera": "microsoft.windows.camera:",
    "microsoft store": "ms-windows-store:", "store": "ms-windows-store:",
    "snipping tool": "ms-screenclip:",
    "photos": "ms-photos:",
    "clock": "ms-clock:", "alarms and clock": "ms-clock:",
    "media player": "mswindowsmusic:",
    "edge": "microsoft-edge:", "microsoft edge": "microsoft-edge:",
}
# A Mac's own apps, also under the names people know from Windows.
_MAC_APPS = {
    "calculator": "Calculator", "calc": "Calculator",
    "notepad": "TextEdit", "text edit": "TextEdit",
    "file explorer": "Finder", "explorer": "Finder", "files": "Finder", "finder": "Finder",
    "settings": "System Settings", "system settings": "System Settings", "system preferences": "System Settings",
    "mac settings": "System Settings", "control panel": "System Settings",
    "task manager": "Activity Monitor", "activity monitor": "Activity Monitor",
    "terminal": "Terminal", "command prompt": "Terminal", "cmd": "Terminal",
    "camera": "Photo Booth", "photo booth": "Photo Booth",
    "store": "App Store", "app store": "App Store", "microsoft store": "App Store",
    "snipping tool": "Screenshot",
    "photos": "Photos",
    "clock": "Clock", "alarms and clock": "Clock",
    "media player": "Music",
}
SYSTEM_APPS = _MAC_APPS if sys.platform == "darwin" else _WINDOWS_APPS
FOLDERS = {"downloads": "Downloads", "documents": "Documents", "pictures": "Pictures", "photos folder": "Pictures",
           "desktop": "Desktop", "music folder": "Music", "videos": "Videos"}
MAC_FOLDERS = dict(FOLDERS, videos="Movies")
ALIASES = {"vs code": "visual studio code", "vscode": "visual studio code", "chrome": "google chrome",
           "what's up": "whatsapp", "whats app": "whatsapp", "you tube": "youtube"}
AUDIO_TYPES = {".mp3", ".m4a", ".wav", ".flac", ".wma", ".ogg", ".aac", ".opus"}

_APPS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    $apps = @(Get-StartApps | ForEach-Object { @{ name = $_.Name; id = $_.AppID } })
    $json = ConvertTo-Json -InputObject $apps -Compress
    [Console]::Out.WriteLine([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


class Unavailable(Exception):
    """Something this computer can't do."""


def _is_windows():
    return sys.platform == "win32"


def _require_windows():
    if not _is_windows():
        raise Unavailable("That only works on Windows and macOS.")


def _user32():
    import ctypes

    return ctypes.windll.user32


def press(key, times=1):
    """Press a media or volume key, like the ones on a keyboard."""
    if macos.available():
        return _press_on_mac(key, times)
    _require_windows()
    user32 = _user32()
    for _ in range(times):
        user32.keybd_event(KEYS[key], 0, 0, 0)
        user32.keybd_event(KEYS[key], 0, KEYEVENTF_KEYUP, 0)


def _press_on_mac(key, times):
    if key in macos.PLAYER_COMMANDS:
        if macos.media(key) is None:
            raise Unavailable("On a Mac I can pause and skip songs in Spotify and Apple Music, "
                              "and neither of them is open.")
    elif key == "mute":
        macos.toggle_mute()
    else:  # volume_up, volume_down: 2% a press, like Windows' volume keys
        macos.change_volume(2 * times if key == "volume_up" else -2 * times)


def lock():
    if macos.available():
        macos.sleep_display()
        return
    _require_windows()
    _user32().LockWorkStation()


def open_url(url):
    """Open a web page (or an app link like spotify:...). Returns False if nothing could open it."""
    try:
        return bool(webbrowser.open(url))
    except OSError:
        return False


def start(target):
    """Start a program or a Windows link such as ms-settings: (on a Mac: a file, or an app by name)."""
    if macos.available():
        if Path(target).exists():
            macos.open_path(target)
        else:
            macos.open_app(target)
        return
    _require_windows()
    os.startfile(target)


def installed_apps():
    """The apps in the Start menu, as {name: app id} (on a Mac: {name: path})."""
    if macos.available():
        return macos.installed_apps()
    _require_windows()
    output = powershell.run(_APPS_SCRIPT, {}, timeout=30)
    try:
        items = json.loads(base64.b64decode(output.split()[-1]))
    except (IndexError, ValueError) as exc:
        raise powershell.PowerShellError("unexpected reply from PowerShell") from exc
    if isinstance(items, dict):
        items = [items]
    return {i["name"]: i["id"] for i in items if isinstance(i, dict) and i.get("name") and i.get("id")}


def compact(text):
    """'Counter-Strike 2' -> 'counterstrike2': spaces and punctuation don't matter when matching names."""
    return re.sub(r"[^0-9a-z]", "", text.casefold())


def find_app(name, apps):
    """The installed app that best matches a spoken name, or None."""
    wanted = compact(ALIASES.get(name, name))
    if not wanted:
        return None
    names = {n: compact(n) for n in apps if "uninstall" not in n.casefold()}
    rules = [lambda c: c == wanted, lambda c: c.startswith(wanted)]
    if len(wanted) >= 3:
        rules.append(lambda c: wanted in c)
    for rule in rules:
        found = sorted((n for n, c in names.items() if rule(c)), key=len)
        if found:
            return found[0]
    # heard slightly wrong ("spot if i" for Spotify): the closest name, if it is close enough
    scored = sorted(((difflib.SequenceMatcher(None, wanted, c).ratio(), n) for n, c in names.items()), reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 0.75 else None


def launch_app(app_id):
    if macos.available():
        macos.open_path(app_id)
        return
    _require_windows()
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])


_CLOSE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    $name = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:MA_NAME))
    $pattern = '*' + [Management.Automation.WildcardPattern]::Escape($name) + '*'
    $closed = 0
    Get-Process | Where-Object {
        $_.MainWindowHandle -ne 0 -and $_.Id -ne $PID -and $_.ProcessName -notmatch '^pythonw?$' -and
        ($_.ProcessName -like $pattern -or $_.MainWindowTitle -like $pattern)
    } | ForEach-Object { if ($_.CloseMainWindow()) { $closed++ } }
    [Console]::Out.WriteLine($closed)
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


def close_app(name):
    """Close the windows of an app, like clicking X (it can still ask to save). Returns how many."""
    if macos.available():
        apps = macos.installed_apps()
        found = name if name in apps else find_app(name, apps)
        return macos.quit_app(found, apps[found]) if found else 0  # only apps that exist: no "Where is ...?"
    _require_windows()
    output = powershell.run(_CLOSE_SCRIPT, {"MA_NAME": powershell.b64(ALIASES.get(name, name))}, timeout=20)
    try:
        return int(output.split()[-1])
    except (IndexError, ValueError) as exc:
        raise powershell.PowerShellError("unexpected reply from PowerShell") from exc


def set_volume(percent):
    """Each volume key press is 2%, so go all the way down, then up to the level."""
    if macos.available():
        macos.set_volume(percent)
        return
    press("volume_down", 50)
    press("volume_up", round(max(0, min(100, percent)) / 2))


def open_folder(name):
    """Open one of your own folders (Downloads, Documents, ...). Returns its path, or None."""
    if macos.available():
        folder = Path.home() / MAC_FOLDERS[name]
        if not folder.is_dir():
            return None
        macos.open_path(folder)
        return folder
    _require_windows()
    for folder in (Path.home() / "OneDrive" / FOLDERS[name], Path.home() / FOLDERS[name]):
        if folder.is_dir():
            os.startfile(str(folder))
            return folder
    return None


def music_folder(setting=""):
    if setting:
        return Path(setting).expanduser()
    for folder in (Path.home() / "Music", Path.home() / "OneDrive" / "Music"):
        if folder.is_dir():
            return folder
    return Path.home() / "Music"


def music_files(folder, limit=5000):
    found = []
    try:
        for path in folder.rglob("*"):
            if path.suffix.casefold() in AUDIO_TYPES and path.is_file():
                found.append(path)
                if len(found) >= limit:
                    break
    except OSError:
        pass
    return found


def play_random_music(setting=""):
    """Play a random song from the music folder. Returns its name, or None if there is none."""
    files = music_files(music_folder(setting))
    if not files:
        return None
    song = random.choice(files)
    start(str(song))
    return song.stem
