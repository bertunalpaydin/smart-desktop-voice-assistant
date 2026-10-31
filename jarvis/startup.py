"""Opening Jarvis when you log in: a value under the Run registry key on Windows,
a launch agent on a Mac."""
from . import macos, windows


def available():
    return windows.available() or macos.available()


def label():
    return "Open Jarvis when you log in" if macos.available() else "Start Jarvis when Windows starts"


def enabled():
    if macos.available():
        return macos.starts_at_login()
    return windows.starts_with_windows()


def set_enabled(on):
    if macos.available():
        try:
            macos.set_start_at_login(on)
        except macos.MacError as exc:
            raise OSError(str(exc)) from exc
    else:
        windows.set_start_with_windows(on)
