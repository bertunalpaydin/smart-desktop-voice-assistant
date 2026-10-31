"""Windows integration: starting with Windows, and a desktop shortcut."""
import base64
import sys
from pathlib import Path

from . import config, powershell

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Jarvis"
OLD_RUN_NAME = "MailAssistant"  # earlier versions

_SHORTCUT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
function Decode($name) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($value) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($value)) } else { '' }
}
try {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $old = Join-Path $desktop 'Mail Assistant.lnk'
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old }
    $path = Join-Path $desktop 'Jarvis.lnk'
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
    $link.TargetPath = Decode 'MA_TARGET'
    $link.Arguments = Decode 'MA_ARGS'
    $link.WorkingDirectory = Decode 'MA_DIR'
    $icon = Decode 'MA_ICON'
    if ($icon) { $link.IconLocation = $icon }
    $link.Description = 'Jarvis'
    $link.Save()
    [Console]::Out.WriteLine([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($path)))
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


def available():
    return sys.platform == "win32"


def pythonw():
    """pythonw.exe runs the app without a console window."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def app_command():
    return f'"{pythonw()}" "{config.ROOT / "app.pyw"}"'


def _winreg():
    import winreg  # only exists on Windows

    return winreg


def starts_with_windows():
    """True if Windows starts this copy of the app when you log in."""
    if not available():
        return False
    reg = _winreg()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = reg.QueryValueEx(key, RUN_NAME)
    except OSError:
        return False
    return value == app_command()


def take_over_start_entry():
    """If Windows starts another copy of Jarvis (say, the folder of an older version, maybe
    deleted by now) or the old Mail Assistant, start this copy instead. Returns True if it did."""
    if not available():
        return False
    reg = _winreg()
    entries = {}
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
            for name in (RUN_NAME, OLD_RUN_NAME):
                try:
                    entries[name] = reg.QueryValueEx(key, name)[0]
                except OSError:
                    pass  # not there
    except OSError:
        return False
    if not entries or entries == {RUN_NAME: app_command()}:
        return False  # starting with Windows is off, or already starts this copy
    set_start_with_windows(True)  # points it at this copy and removes the old entry
    return True


def set_start_with_windows(on):
    reg = _winreg()
    with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
        if on:
            reg.SetValueEx(key, RUN_NAME, 0, reg.REG_SZ, app_command())
        names = (OLD_RUN_NAME,) if on else (RUN_NAME, OLD_RUN_NAME)
        for name in names:
            try:
                reg.DeleteValue(key, name)
            except FileNotFoundError:
                pass  # was already off


def create_shortcut(icon=None):
    """Put a "Jarvis" shortcut on the desktop (and remove the old Mail Assistant one). Returns its path."""
    values = {
        "MA_TARGET": powershell.b64(str(pythonw())),
        "MA_ARGS": powershell.b64(f'"{config.ROOT / "app.pyw"}"'),
        "MA_DIR": powershell.b64(str(config.ROOT)),
        "MA_ICON": powershell.b64(str(icon) if icon else ""),
    }
    output = powershell.run(_SHORTCUT_SCRIPT, values, timeout=60)
    try:
        return base64.b64decode(output.split()[-1]).decode("utf-8")
    except (IndexError, ValueError) as exc:
        raise powershell.PowerShellError("unexpected reply from PowerShell") from exc


def dark_title_bar(hwnd):
    """Ask Windows 10/11 for a dark title bar on a window. Returns True if it worked."""
    if not available():
        return False
    try:
        import ctypes

        on = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, and its number on older Windows 10
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attribute, ctypes.byref(on),
                                                          ctypes.sizeof(on)) == 0:
                return True
    except (OSError, AttributeError):
        pass
    return False
