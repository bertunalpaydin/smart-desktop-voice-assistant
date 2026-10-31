import base64
import json
import subprocess

import pytest
from fakes import use_run

from jarvis import powershell, system


class FakeUser32:
    def __init__(self):
        self.calls = []

    def keybd_event(self, key, scan, flags, extra):
        self.calls.append((key, flags))

    def LockWorkStation(self):  # noqa: N802 - Windows' own name
        self.calls.append("lock")


@pytest.fixture
def windows(monkeypatch):
    user32 = FakeUser32()
    monkeypatch.setattr(system, "_is_windows", lambda: True)
    monkeypatch.setattr(system, "_user32", lambda: user32)
    return user32


def test_media_and_volume_keys_are_pressed_and_released(windows):
    system.press("volume_up", times=2)
    up = system.KEYS["volume_up"]
    assert windows.calls == [(up, 0), (up, system.KEYEVENTF_KEYUP)] * 2
    system.press("play_pause")
    assert windows.calls[-1] == (0xB3, system.KEYEVENTF_KEYUP)


def test_lock(windows):
    system.lock()
    assert windows.calls == ["lock"]


def test_windows_only_things_say_so_elsewhere(monkeypatch):
    monkeypatch.setattr(system, "_is_windows", lambda: False)
    for action in (lambda: system.press("next"), system.lock, lambda: system.start("calc.exe"),
                   system.installed_apps, lambda: system.launch_app("x")):
        with pytest.raises(system.Unavailable):
            action()


def apps_reply(items):
    encoded = base64.b64encode(json.dumps(items).encode("utf-8")).decode("ascii")
    return lambda args, **kwargs: subprocess.CompletedProcess(args, 0, encoded + "\r\n", "")


def test_installed_apps_from_the_start_menu(windows, monkeypatch):
    use_run(monkeypatch, apps_reply([{"name": "Spotify", "id": "SpotifyAB.Spotify"}, {"name": "Calculator", "id": "calc"},
                                     {"name": "", "id": "x"}]))
    assert system.installed_apps() == {"Spotify": "SpotifyAB.Spotify", "Calculator": "calc"}
    use_run(monkeypatch, apps_reply({"name": "Only One", "id": "one"}))  # PowerShell sends a lone object
    assert system.installed_apps() == {"Only One": "one"}
    use_run(monkeypatch, lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""))
    with pytest.raises(powershell.PowerShellError):
        system.installed_apps()


APPS = {"Spotify": "s", "Visual Studio Code": "v", "Google Chrome": "c", "Steam": "st", "Uninstall Steam": "u",
        "Discord": "d", "Opera GX Browser": "o", "WhatsApp": "w", "Counter-Strike 2": "cs", "Xbox": "x"}


@pytest.mark.parametrize("spoken, found", [
    ("spotify", "Spotify"),
    ("vs code", "Visual Studio Code"),
    ("chrome", "Google Chrome"),
    ("steam", "Steam"),
    ("opera", "Opera GX Browser"),
    ("opera gx", "Opera GX Browser"),
    ("gx", None),  # too short to guess from
    ("what's up", "WhatsApp"),  # how speech recognition often writes it
    ("spot if i", "Spotify"),  # heard slightly wrong
    ("counter strike", "Counter-Strike 2"),
    ("x box", "Xbox"),
    ("photoshop", None),
])
def test_finding_an_app_by_its_spoken_name(spoken, found):
    assert system.find_app(spoken, APPS) == found


def test_launching_an_app(windows, monkeypatch):
    started = []
    monkeypatch.setattr(system.subprocess, "Popen", lambda args: started.append(args))
    system.launch_app("SpotifyAB.Spotify")
    assert started == [["explorer.exe", "shell:AppsFolder\\SpotifyAB.Spotify"]]


def test_random_song_from_the_music_folder(windows, monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(system.os, "startfile", started.append, raising=False)
    assert system.play_random_music(str(tmp_path)) is None
    (tmp_path / "Album").mkdir()
    (tmp_path / "Album" / "Song One.MP3").write_bytes(b"")
    (tmp_path / "cover.jpg").write_bytes(b"")
    assert system.play_random_music(str(tmp_path)) == "Song One"
    assert started == [str(tmp_path / "Album" / "Song One.MP3")]


def test_default_music_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(system.Path, "home", lambda: tmp_path)
    (tmp_path / "OneDrive" / "Music").mkdir(parents=True)
    assert system.music_folder() == tmp_path / "OneDrive" / "Music"
    assert system.music_folder("D:/Songs") == system.Path("D:/Songs")


def test_open_url_reports_failure(monkeypatch):
    monkeypatch.setattr(system.webbrowser, "open", lambda url: True)
    assert system.open_url("https://example.com") is True

    def broken(url):
        raise OSError("no app for spotify:")

    monkeypatch.setattr(system.webbrowser, "open", broken)
    assert system.open_url("spotify:search:x") is False



def test_closing_an_app_through_powershell(windows, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["env"])
        return subprocess.CompletedProcess(args, 0, "2\r\n", "")

    use_run(monkeypatch, run)
    assert system.close_app("vs code") == 2
    assert base64.b64decode(calls[0]["MA_NAME"]).decode("utf-8") == "visual studio code"
    use_run(monkeypatch, lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""))
    with pytest.raises(powershell.PowerShellError):
        system.close_app("x")


def test_the_close_script_only_asks_windows_to_close():
    script = system._CLOSE_SCRIPT
    assert "CloseMainWindow" in script and "Stop-Process" not in script and "Kill" not in script
    assert "WildcardPattern]::Escape" in script and "$env:MA_NAME" in script


def test_setting_the_volume_goes_down_then_up(windows):
    system.set_volume(30)
    down, up = system.KEYS["volume_down"], system.KEYS["volume_up"]
    presses = [key for key, flags in windows.calls if flags == 0]
    assert presses == [down] * 50 + [up] * 15


def test_opening_a_folder(windows, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(system.os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(system.Path, "home", lambda: tmp_path)
    assert system.open_folder("downloads") is None
    (tmp_path / "OneDrive" / "Documents").mkdir(parents=True)
    (tmp_path / "Downloads").mkdir()
    assert system.open_folder("downloads") == tmp_path / "Downloads"
    assert system.open_folder("documents") == tmp_path / "OneDrive" / "Documents"
    assert opened == [str(tmp_path / "Downloads"), str(tmp_path / "OneDrive" / "Documents")]
