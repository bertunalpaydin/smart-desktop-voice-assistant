"""The Mac integration, with every macOS tool replaced by a fake."""
import plistlib
import subprocess
import threading

import pytest

from jarvis import macos

SAY_VOICES = """\
Albert              en_US    # Hello! My name is Albert.
Amélie              fr_CA    # Bonjour, je m’appelle Amélie.
Daniel              en_GB    # Hello! My name is Daniel.
Eddy (English (UK)) en_GB    # Hello! My name is Eddy.
Karen               en_AU    # Hello! My name is Karen.
Yelda               tr_TR    # Merhaba, benim adım Yelda.
"""


class Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class Calls(list):
    """The commands run so far; `answers` says what each tool replies."""

    def __init__(self):
        super().__init__()
        self.answers = {}


@pytest.fixture
def ran(monkeypatch):
    """Records every command macos runs with subprocess.run; answers from `ran.answers`."""
    calls = Calls()

    def run(args, **kwargs):
        calls.append(list(args))
        answer = calls.answers.get(args[0], Done())
        if isinstance(answer, Exception):
            raise answer
        return answer(args) if callable(answer) else answer

    monkeypatch.setattr(macos.subprocess, "run", run)
    return calls


class FakeProcess:
    """A Popen that finishes when told to (or straight away)."""

    made = []

    def __init__(self, args, stdin=None, stdout=None, stderr=None, text=None, returncode=0, wait_for_stop=False):
        self.args, self.returncode = list(args), None
        self._final, self._stopped = returncode, threading.Event()
        self._wait_for_stop = wait_for_stop
        self.given, self.terminated = None, False
        FakeProcess.made.append(self)

    def communicate(self, text=None, timeout=None):
        self.given = text
        if self._wait_for_stop:
            self._stopped.wait(5)
        if self.returncode is None:
            self.returncode = self._final
        return "", "" if self.returncode >= 0 else ""

    def wait(self, timeout=None):
        self._stopped.wait(5) if self._wait_for_stop else None
        if self.returncode is None:
            self.returncode = self._final
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self._stopped.set()

    def kill(self):
        self.terminate()


@pytest.fixture
def processes(monkeypatch):
    FakeProcess.made = []
    settings = {}

    def popen(args, **kwargs):
        return FakeProcess(args, **kwargs, **settings)

    popen.settings = settings
    monkeypatch.setattr(macos.subprocess, "Popen", popen)
    return popen


# --- AppleScript ----------------------------------------------------------------------

def test_values_reach_applescript_as_arguments_never_as_script(ran):
    ran.answers["osascript"] = Done(stdout="done\n")
    assert macos.osascript(["return valueAt(1, argv)"], "-e do shell script \"rm -rf ~\"", 5) == "done"
    args = ran[0]
    script = [args[i + 1] for i, a in enumerate(args) if a == "-e"]
    assert script[:5] == macos._ARG_HANDLER and script[5:] == ["on run argv", "return valueAt(1, argv)", "end run"]
    values = args[len(script) * 2 + 1:]
    assert values == ["x-e do shell script \"rm -rf ~\"", "x5"]  # can't be read as an option
    assert all(v.startswith("x") for v in values)


def test_a_failing_tool_is_a_mac_error(ran):
    ran.answers["osascript"] = Done(1, stderr="execution error: Not authorised (-1743)")
    with pytest.raises(macos.MacError, match="Not authorised"):
        macos.osascript(["quit"])
    ran.answers["pmset"] = FileNotFoundError("pmset")
    with pytest.raises(macos.MacError):
        macos.sleep_display()
    ran.answers["open"] = subprocess.TimeoutExpired("open", 15)
    with pytest.raises(macos.MacError):
        macos.open_app("Calculator")


def test_notifications(ran):
    macos.notify("Important mail", "-50% off: Interview")
    script, values = ran[0], ran[0][-2:]
    assert "display notification (valueAt(2, argv)) with title (valueAt(1, argv))" in script
    assert values == ["xImportant mail", "x-50% off: Interview"]


# --- the voice ----------------------------------------------------------------------------

def test_english_voices_are_listed(ran):
    ran.answers["say"] = Done(stdout=SAY_VOICES)
    assert macos.voices() == [
        {"name": "Albert", "culture": "en-US"}, {"name": "Daniel", "culture": "en-GB"},
        {"name": "Eddy (English (UK))", "culture": "en-GB"}, {"name": "Karen", "culture": "en-AU"},
    ]
    assert ran[0] == ["say", "-v", "?"]


@pytest.mark.parametrize("wanted, installed, chosen", [
    ("", ["Albert/en-US", "Daniel/en-GB"], "Daniel"),
    ("", ["Albert/en-US", "Eddy (English (UK))/en-GB"], "Eddy (English (UK))"),
    ("", ["Albert/en-US", "Karen/en-AU"], "Albert"),
    ("", [], ""),
    ("kar", ["Daniel/en-GB", "Karen/en-AU"], "Karen"),
    ("Nobody", ["Daniel/en-GB"], ""),  # not installed: the system's voice
])
def test_picking_a_voice(wanted, installed, chosen):
    voices = [dict(zip(("name", "culture"), v.split("/"))) for v in installed]
    assert macos.pick_voice(wanted, voices) == chosen


@pytest.mark.parametrize("rate, wpm", [(0, 185), (10, 295), (-10, 75), (99, 295), (-99, 75)])
def test_speed(rate, wpm):
    assert macos.words_per_minute(rate) == wpm


def test_saying_text_through_stdin(processes):
    assert macos.say("Hello, -v Fred", "Daniel", 2) is True
    process = FakeProcess.made[0]
    assert process.args == ["say", "-r", "207", "-v", "Daniel"]
    assert process.given == "Hello, -v Fred"  # text never becomes an argument
    macos.say("Hi")
    assert FakeProcess.made[1].args == ["say", "-r", "185"]


def test_saying_can_be_stopped(processes):
    processes.settings["wait_for_stop"] = True
    result = []
    worker = threading.Thread(target=lambda: result.append(macos.say("A long mail")))
    worker.start()
    for _ in range(100):
        if macos._speaking is not None:
            break
        threading.Event().wait(0.01)
    assert macos.stop_speaking() is True
    worker.join(5)
    assert result == [False] and macos._speaking is None
    assert macos.stop_speaking() is False  # nothing to stop now


def test_a_say_failure_is_a_mac_error(processes, monkeypatch):
    processes.settings["returncode"] = 1
    with pytest.raises(macos.MacError, match="say failed"):
        macos.say("Hi", "Nobody")

    def broken(*args, **kwargs):
        raise OSError("no such file: say")

    monkeypatch.setattr(macos.subprocess, "Popen", broken)
    with pytest.raises(macos.MacError, match="no such file"):
        macos.say("Hi")


# --- sounds ------------------------------------------------------------------------------

def test_a_sound_plays_once_without_waiting(processes, monkeypatch):
    macos.play("/System/Library/Sounds/Glass.aiff")
    assert FakeProcess.made[0].args == ["afplay", "/System/Library/Sounds/Glass.aiff"]

    def broken(*args, **kwargs):
        raise OSError("no afplay")

    monkeypatch.setattr(macos.subprocess, "Popen", broken)
    macos.play("x.wav")  # no sound, no crash


def test_a_looping_sound_stops(processes):
    processes.settings["wait_for_stop"] = True
    macos.loop("/tmp/alarm.wav")
    for _ in range(100):
        if FakeProcess.made:
            break
        threading.Event().wait(0.01)
    assert FakeProcess.made[0].args == ["afplay", "/tmp/alarm.wav"]
    macos.stop_sounds()
    assert FakeProcess.made[0].terminated and not macos._looping.is_set()
    macos.stop_sounds()  # twice is fine


# --- apps ----------------------------------------------------------------------------------

def make_app(folder, name, program=None):
    app = folder / f"{name}.app"
    (app / "Contents").mkdir(parents=True)
    if program:
        with open(app / "Contents" / "Info.plist", "wb") as file:
            plistlib.dump({"CFBundleExecutable": program}, file)
    return app


def test_finding_installed_apps(tmp_path):
    apps, system, missing = tmp_path / "Applications", tmp_path / "System", tmp_path / "nowhere"
    make_app(apps, "Spotify")
    make_app(apps / "Adobe Photoshop 2026", "Adobe Photoshop 2026")
    (apps / ".hidden").mkdir()
    make_app(apps / ".hidden", "Secret")
    make_app(system, "Spotify")  # the first one found wins
    finder = make_app(tmp_path / "CoreServices", "Finder")
    found = macos.installed_apps([apps, system, missing], [finder, tmp_path / "Gone.app"])
    assert found == {
        "Spotify": str(apps / "Spotify.app"),
        "Adobe Photoshop 2026": str(apps / "Adobe Photoshop 2026" / "Adobe Photoshop 2026.app"),
        "Finder": str(finder),
    }


def test_an_apps_program_name(tmp_path):
    assert macos.executable(make_app(tmp_path, "Visual Studio Code", "Electron")) == "Electron"
    assert macos.executable(make_app(tmp_path, "Spotify")) == "Spotify"  # no Info.plist
    broken = make_app(tmp_path, "Broken")
    (broken / "Contents" / "Info.plist").write_text("not a plist")
    assert macos.executable(broken) == "Broken"


def test_running_apps_are_found_without_apple_events(ran):
    ran.answers["pgrep"] = lambda args: Done(0 if args[-1] == "Spotify" else 1)
    assert macos.is_running("Spotify") and not macos.is_running("Music")
    assert ran[0] == ["pgrep", "-x", "Spotify"]
    ran.answers["pgrep"] = OSError("no pgrep")
    assert macos.is_running("Spotify") is False


def test_quitting_only_asks_apps_that_are_open(ran, tmp_path):
    code = make_app(tmp_path, "Visual Studio Code", "Electron")
    ran.answers["pgrep"] = Done(1)
    assert macos.quit_app("Visual Studio Code", code) == 0
    assert [c[0] for c in ran] == ["pgrep"]  # not open: no AppleScript, nothing gets launched
    ran.answers["pgrep"] = lambda args: Done(0 if args[-1] == "Electron" else 1)
    assert macos.quit_app("Visual Studio Code", code) == 1
    assert "tell application (valueAt(1, argv)) to quit" in ran[-1] and ran[-1][-1] == "xVisual Studio Code"


def test_opening(ran):
    macos.open_path("/Users/me/Downloads")
    macos.open_app("System Settings")
    assert ran == [["open", "/Users/me/Downloads"], ["open", "-a", "System Settings"]]


@pytest.mark.parametrize("running, action, expected", [
    ({"Spotify"}, "play_pause", ("Spotify", 'tell application "Spotify" to playpause')),
    ({"Music"}, "next", ("Music", 'tell application "Music" to next track')),
    ({"Spotify", "Music"}, "previous", ("Spotify", 'tell application "Spotify" to previous track')),
    ({"Music"}, "stop", ("Music", 'tell application "Music" to pause')),
    (set(), "play_pause", (None, None)),
])
def test_music_players(ran, running, action, expected):
    ran.answers["pgrep"] = lambda args: Done(0 if args[-1] in running else 1)
    assert macos.media(action) == expected[0]
    scripts = [c for c in ran if c[0] == "osascript"]
    assert (expected[1] in scripts[0]) if expected[1] else scripts == []


# --- volume, screen ------------------------------------------------------------------------

def test_volume(ran):
    macos.set_volume(140)
    macos.set_volume(37.6)
    macos.change_volume(-10)
    macos.toggle_mute()
    assert [c[-1] for c in ran[:3]] == ["x100", "x38", "x-10"]
    assert "set volume output volume (valueAt(1, argv) as integer)" in ran[0]
    assert "set volume output muted (not (output muted of (get volume settings)))" in ran[3]


def test_screen(ran, tmp_path):
    macos.sleep_display()
    path = tmp_path / "Screenshots" / "shot.png"
    ran.answers["screencapture"] = lambda args: (path.write_bytes(b"png"), Done())[1]
    assert macos.screenshot(path) is True
    assert ran == [["pmset", "displaysleepnow"], ["screencapture", "-x", str(path)]]


# --- opening at login ---------------------------------------------------------------------------

def test_the_app_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(macos.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("RESOURCEPATH", "/Users/me/Applications/Jarvis.app/Contents/Resources")
    assert str(macos.app_bundle()) == "/Users/me/Applications/Jarvis.app"
    monkeypatch.delenv("RESOURCEPATH")
    assert macos.app_bundle() is None
    make_app(tmp_path / "Applications", "Jarvis")
    assert macos.app_bundle() == tmp_path / "Applications" / "Jarvis.app"


def test_opening_at_login(monkeypatch, tmp_path):
    monkeypatch.setattr(macos.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("RESOURCEPATH", raising=False)
    assert macos.starts_at_login() is False
    with pytest.raises(macos.MacError, match="install.sh"):
        macos.set_start_at_login(True)  # no Jarvis.app yet
    app = make_app(tmp_path / "Applications", "Jarvis")
    macos.set_start_at_login(True)
    agent = plistlib.loads(macos.launch_agent().read_bytes())
    assert agent["Label"] == macos.BUNDLE_ID and agent["RunAtLoad"] is True
    assert agent["ProgramArguments"] == ["/usr/bin/open", "-g", "-a", str(app)]
    assert agent["AssociatedBundleIdentifiers"] == [macos.BUNDLE_ID]
    assert macos.starts_at_login() is True
    monkeypatch.setenv("RESOURCEPATH", "/Volumes/Other/Jarvis.app/Contents/Resources")
    assert macos.starts_at_login() is False  # it opens another copy
    macos.set_start_at_login(False)
    assert not macos.launch_agent().exists() and macos.starts_at_login() is False
    macos.set_start_at_login(False)  # already off


def test_playing_spoken_audio_waits_and_can_be_stopped(processes):
    assert macos.speak_file("/voice/1-0.wav") is True
    assert FakeProcess.made[0].args == ["afplay", "/voice/1-0.wav"]
    processes.settings["wait_for_stop"] = True
    result = []
    worker = threading.Thread(target=lambda: result.append(macos.speak_file("/voice/1-1.wav")))
    worker.start()
    for _ in range(100):
        if macos._speaking is not None:
            break
        threading.Event().wait(0.01)
    assert macos.stop_speaking() is True
    worker.join(5)
    assert result == [False] and macos._speaking is None
    processes.settings.update(wait_for_stop=False, returncode=1)
    with pytest.raises(macos.MacError, match="afplay failed"):
        macos.speak_file("/voice/missing.wav")
