"""On a Mac, each part of Jarvis uses the Mac way of doing things (with macos.py faked),
and the installer's files fit together."""
import importlib
import json
import os
import re
import struct
import subprocess
import sys
import types
from pathlib import Path

import pytest

from jarvis import commands, config, hotkey, macos, notifier, sound, speech, startup, sysinfo, system

ROOT = Path(__file__).resolve().parent.parent
MACOS = ROOT / "macos"


class Calls(list):
    fake = None  # set by the fixture: replace one macos function's answer


@pytest.fixture
def mac(monkeypatch):
    """Pretend to be a Mac; every call into macos.py is recorded instead of run."""
    calls = Calls()

    def fake(name, result=None):
        def function(*args, **kwargs):
            calls.append((name, *args))
            if isinstance(result, Exception):
                raise result
            return result(*args) if callable(result) else result
        return function

    monkeypatch.setattr(macos, "available", lambda: True)
    for name, result in {
        "voices": [{"name": "Albert", "culture": "en-US"}, {"name": "Daniel", "culture": "en-GB"}],
        "say": True, "stop_speaking": True, "play": None, "loop": None, "stop_sounds": None, "notify": None,
        "media": "Spotify", "toggle_mute": None, "change_volume": None, "set_volume": None, "sleep_display": None,
        "open_path": None, "open_app": None, "installed_apps": {"Spotify": "/Applications/Spotify.app"},
        "quit_app": 1, "starts_at_login": False, "set_start_at_login": None,
    }.items():
        monkeypatch.setattr(macos, name, fake(name, result))
    calls.fake = lambda name, result: monkeypatch.setattr(macos, name, fake(name, result))
    monkeypatch.setattr(speech, "_mac_voices", None)
    return calls


# --- speech, sounds, notifications ------------------------------------------------------

def test_speech_on_a_mac(mac, monkeypatch):
    monkeypatch.setattr(config, "VOICE", "")
    monkeypatch.setattr(config, "SPEECH_RATE", 3)
    assert speech.speak("Hello there") is True
    assert ("say", "Hello there", "Daniel", 3) in mac
    assert speech.voices() == ([{"name": "Albert", "culture": "en-US"}, {"name": "Daniel", "culture": "en-GB"}],
                               "Daniel")
    assert [c[0] for c in mac].count("voices") == 1  # asked once, then remembered
    assert speech.stop() is True and mac[-1] == ("stop_speaking",)


def test_speech_cut_off_or_failing_on_a_mac(mac, capsys):
    mac.fake("say", False)  # stopped
    assert speech.speak("Hello") is False
    assert "speech failed" not in capsys.readouterr().out
    mac.fake("say", macos.MacError("Voice `Nobody' not found."))
    assert speech.speak("Hello") is False
    out = capsys.readouterr().out
    assert "(speech failed: Voice `Nobody' not found.)" in out and "[speech] Hello" in out


def test_sounds_on_a_mac(mac, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    sound.play_alarm()
    sound.stop()
    sound.chime()
    sound.listening()
    assert mac == [("loop", tmp_path / "alarm.wav"), ("stop_sounds",), ("play", sound.MAC_CHIME),
                   ("play", tmp_path / "listening.wav")]
    import wave

    with wave.open(str(tmp_path / "alarm.wav")) as file:
        assert 0.9 < file.getnframes() / file.getframerate() < 1.2  # beep beep beep, then a pause


def test_notifications_on_a_mac(mac, capsys):
    assert notifier.notify("New mail", "Interview next steps") is True
    assert mac == [("notify", "New mail", "Interview next steps")]
    mac.fake("notify", macos.MacError("osascript failed"))
    assert notifier.notify("New mail", "Hello") is False
    assert "(notification failed: osascript failed)" in capsys.readouterr().out


# --- the system commands -----------------------------------------------------------------

def test_media_keys_on_a_mac(mac):
    system.press("play_pause")
    system.press("mute")
    system.press("volume_up", 5)
    system.press("volume_down", 3)
    assert mac == [("media", "play_pause"), ("toggle_mute",), ("change_volume", 10), ("change_volume", -6)]
    mac.fake("media", None)  # neither Spotify nor Music is open
    with pytest.raises(system.Unavailable, match="Spotify and Apple Music"):
        system.press("next")


def test_apps_folders_volume_and_lock_on_a_mac(mac, monkeypatch, tmp_path):
    monkeypatch.setattr(system.Path, "home", lambda: tmp_path)
    (tmp_path / "Movies").mkdir()
    song = tmp_path / "song.mp3"
    song.write_bytes(b"")
    system.start("System Settings")
    system.start(str(song))
    system.launch_app("/Applications/Spotify.app")
    system.set_volume(30)
    system.lock()
    assert system.installed_apps() == {"Spotify": "/Applications/Spotify.app"}
    assert system.open_folder("videos") == tmp_path / "Movies"
    assert system.open_folder("downloads") is None
    assert mac[:5] == [("open_app", "System Settings"), ("open_path", str(song)),
                       ("open_path", "/Applications/Spotify.app"), ("set_volume", 30), ("sleep_display",)]
    assert ("open_path", tmp_path / "Movies") in mac


def test_closing_apps_on_a_mac(mac):
    assert system.close_app("spotify") == 1  # matched to the installed app's real name
    assert mac[-1] == ("quit_app", "Spotify", "/Applications/Spotify.app")
    assert system.close_app("nothing like it") == 0  # never asks macOS about an app that isn't there
    assert mac[-1][0] == "installed_apps"


def test_mac_names_for_windows_apps():
    assert system._MAC_APPS["settings"] == "System Settings"
    assert system._MAC_APPS["task manager"] == "Activity Monitor"
    assert system._MAC_APPS["notepad"] == "TextEdit"
    assert set(system._MAC_APPS) >= {"calculator", "file explorer", "terminal", "camera", "store"}
    assert system.MAC_FOLDERS["videos"] == "Movies" and system.FOLDERS["videos"] == "Videos"


def test_commands_report_what_the_mac_refused(monkeypatch):
    class RefusingSystem:
        def press(self, key, times=1):
            raise macos.MacError("Not authorised to send Apple events to Spotify.")

        def lock(self):
            pass

    assistant = commands.Assistant(system_api=RefusingSystem())
    assert assistant.handle("pause").say == ("Your Mac wouldn't let me do that: "
                                             "Not authorised to send Apple events to Spotify.")
    monkeypatch.setattr(macos, "available", lambda: True)
    assert assistant.handle("lock the computer").say == "Turning the screen off."


# --- system readings, hot key, opening at login ----------------------------------------------

def test_system_readings_on_a_mac(monkeypatch):
    fake = types.SimpleNamespace(
        cpu_percent=lambda interval=None: 37.4,
        virtual_memory=lambda: types.SimpleNamespace(percent=61.6),
        sensors_battery=lambda: types.SimpleNamespace(percent=83.5, power_plugged=True),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake)
    monkeypatch.setattr(sysinfo.sys, "platform", "darwin")
    monkeypatch.setattr(sysinfo, "_mac_started", False)
    first = sysinfo.snapshot()
    assert first["cpu"] is None  # nothing to compare with yet
    assert (first["memory"], first["battery"], first["plugged_in"]) == (62, 84, True)
    assert sysinfo.snapshot()["cpu"] == 37
    fake.sensors_battery = lambda: None  # a Mac mini
    assert sysinfo.snapshot()["battery"] is None
    monkeypatch.setitem(sys.modules, "psutil", None)  # not installed
    assert sysinfo.snapshot()["cpu"] is None


class FakeCarbon:
    def __init__(self, taken=False):
        self.taken, self.calls, self.callback_function = taken, [], None

    def callback(self, function):
        self.callback_function = function
        return "callback"

    def install_handler(self, callback):
        self.calls.append(("install", callback))
        return "handler"

    def register(self, key, modifiers, hotkey_id):
        self.calls.append(("register", key, modifiers, hotkey_id))
        return None if self.taken else "hotkey"

    def unregister(self, ref):
        self.calls.append(("unregister", ref))

    def remove_handler(self, handler):
        self.calls.append(("remove", handler))


def test_control_option_j_on_a_mac():
    pressed = []
    carbon = FakeCarbon()
    key = hotkey.MacHotkey(lambda: pressed.append(1), carbon=carbon)
    key.start()
    assert key.ready.is_set() and key.registered
    assert carbon.calls == [("install", "callback"), ("register", 38, 0x1000 | 0x0800, hotkey.HOTKEY_ID)]
    assert carbon.callback_function(None, None, None) == 0 and pressed == [1]
    key.stop()
    assert carbon.calls[-2:] == [("unregister", "hotkey"), ("remove", "handler")] and not key.registered
    key.stop()  # twice is fine


def test_control_option_j_taken_or_missing():
    carbon = FakeCarbon(taken=True)
    key = hotkey.MacHotkey(lambda: None, carbon=carbon)
    key.start()
    assert not key.registered and key.ready.is_set() and carbon.calls[-1] == ("remove", "handler")

    def broken(*args):
        raise RuntimeError("boom")

    survivor = hotkey.MacHotkey(broken, carbon=FakeCarbon())
    survivor.start()
    assert survivor._pressed(None, None, None) == 0  # an error never reaches Carbon

    class NoCarbon(FakeCarbon):
        def install_handler(self, callback):
            raise OSError("Carbon not found")

    missing = hotkey.MacHotkey(lambda: None, carbon=NoCarbon())
    missing.start()
    assert not missing.registered and missing.ready.is_set()


def test_the_right_hot_key_for_each_system(monkeypatch):
    assert hotkey._four_chars("keyb") == 0x6B657962
    monkeypatch.setattr(hotkey.sys, "platform", "darwin")
    assert isinstance(hotkey.create(lambda: None), hotkey.MacHotkey) and hotkey.available()
    monkeypatch.setattr(hotkey.sys, "platform", "win32")
    assert isinstance(hotkey.create(lambda: None), hotkey.Hotkey) and hotkey.available()
    monkeypatch.setattr(hotkey.sys, "platform", "linux")
    assert not hotkey.available()


def test_opening_at_login_on_a_mac(mac):
    assert startup.available() and startup.label() == "Open Jarvis when you log in"
    assert startup.enabled() is False
    startup.set_enabled(True)
    assert mac[-1] == ("set_start_at_login", True)
    mac.fake("set_start_at_login", macos.MacError("Jarvis.app isn't installed"))
    with pytest.raises(OSError, match="isn't installed"):
        startup.set_enabled(True)


# --- settings that depend on the system ----------------------------------------------------------

@pytest.fixture
def as_mac(monkeypatch):
    """Reload modules as a Mac would load them, and put them back afterwards."""
    reloaded = []

    def load(module):
        monkeypatch.setattr(sys, "platform", "darwin")
        reloaded.append(module)
        return importlib.reload(module)

    yield load
    monkeypatch.setattr(sys, "platform", "linux")
    for module in reloaded:
        importlib.reload(module)


def test_a_mac_keeps_its_data_in_application_support(as_mac):
    mac_config = as_mac(config)
    assert mac_config.APP_DIR == Path.home() / "Library" / "Application Support" / "Jarvis"
    assert mac_config.HOTKEY_LABEL == "⌃⌥J" and mac_config.HOTKEY_SPOKEN == "Control Option J"


def test_a_mac_uses_certifis_certificates(as_mac, monkeypatch):
    import jarvis

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    as_mac(jarvis)
    import certifi

    assert os.environ["SSL_CERT_FILE"] == certifi.where()
    monkeypatch.setenv("SSL_CERT_FILE", "/my/own.pem")
    as_mac(jarvis)
    assert os.environ["SSL_CERT_FILE"] == "/my/own.pem"  # never overrides a choice already made


# --- the installer's files --------------------------------------------------------------------------

def locked(path):
    """{package: version} from a requirements file, and whether every one has hashes."""
    text = path.read_text(encoding="utf-8")
    entries = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)[^\n]*\\\n((?:\s+--hash=sha256:[0-9a-f]{64}\s*\\?\n?)+)",
                         text, re.MULTILINE)
    names = re.findall(r"^([A-Za-z0-9_.-]+)==", text, re.MULTILINE)
    return {name.lower(): version for name, version, _ in entries}, len(entries) == len(names)


@pytest.mark.parametrize("version", ["312", "313"])
def test_every_mac_package_is_pinned_with_hashes(version):
    packages, all_hashed = locked(MACOS / f"requirements-py{version}.txt")
    assert all_hashed and len(packages) > 40
    wanted = [line.split("==")[0].strip().lower() for line in (MACOS / "requirements.in").read_text().splitlines()
              if line.strip() and not line.startswith("#")]
    assert set(wanted) <= set(packages)
    assert packages["py2app"] == "0.28.10" and packages["setuptools"] == "82.0.1"
    assert "tflite-runtime" not in packages  # Linux only
    if version == "313":
        assert {"audioop-lts", "standard-aifc"} <= set(packages)  # what Python 3.13 removed


def test_the_installer_scripts_are_valid_bash():
    for script in ("install.sh", "uninstall.sh"):
        subprocess.run(["bash", "-n", str(MACOS / script)], check=True)
        assert os.access(MACOS / script, os.X_OK)


def test_the_installer_only_installs_locked_ready_made_packages():
    text = (MACOS / "install.sh").read_text(encoding="utf-8")
    pip = re.search(r"-m pip install(.*?)-r \"\$LOCK\"", text, re.DOTALL).group(1)
    for option in ("--require-hashes", "--only-binary=:all:", "--no-deps", "--isolated",
                   "--index-url https://pypi.org/simple"):
        assert option in pip
    assert "sudo" not in text and "curl" not in text  # no admin rights, no downloads besides PyPI and the model
    for name in ("launcher.py", "setup.py", "check.py", "Jarvis.icns"):
        assert (MACOS / name).exists() and name in text


def test_the_app_bundle_settings():
    text = (MACOS / "setup.py").read_text(encoding="utf-8")
    for key in ("NSMicrophoneUsageDescription", "NSAppleEventsUsageDescription", "LSUIElement",
                f'"CFBundleIdentifier": "{macos.BUNDLE_ID}"', '"iconfile": "Jarvis.icns"', 'app=["launcher.py"]'):
        assert key in text


def test_the_icon_file():
    data = (MACOS / "Jarvis.icns").read_bytes()
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    kinds, offset = [], 8
    while offset < len(data):
        kind, length = data[offset:offset + 4].decode(), struct.unpack(">I", data[offset + 4:offset + 8])[0]
        assert data[offset + 8:offset + 16] == b"\x89PNG\r\n\x1a\n"
        kinds.append(kind)
        offset += length
    assert offset == len(data) and {"ic07", "ic08", "ic09", "ic10", "icp4", "icp5"} <= set(kinds)


def test_the_icon_can_be_drawn_again(qt_app, tmp_path):
    sys.path.insert(0, str(MACOS))
    try:
        import make_icon
    finally:
        sys.path.remove(str(MACOS))
    image = make_icon.draw(64)
    assert image.width() == image.height() == 64 and image.pixelColor(32, 32).alpha() == 255
    assert image.pixelColor(0, 0).alpha() == 0  # the corners are clear
    assert make_icon.icns({"ic07": b"png"}) == b"icns" + struct.pack(">I", 19) + b"ic07" + struct.pack(">I", 11) + b"png"
    target = make_icon.main(tmp_path / "Jarvis.icns")
    assert target.read_bytes()[:4] == b"icns"


@pytest.fixture
def qt_app():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def check_script(monkeypatch):
    import importlib.util

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(
        __version__="0.5.6", query_devices=lambda: [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]))
    spec = importlib.util.spec_from_file_location("jarvis_check", MACOS / "check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.problems.clear()
    return module


def test_the_check_script_reports_versions_and_the_model(check_script, monkeypatch, tmp_path):
    report = check_script.packages()
    for name in ("PyQt6", "numpy", "onnxruntime", "SpeechRecognition", "google-api-python-client"):
        assert f"{name} " in report
    model = tmp_path / "hey_jarvis_v0.1.tflite"
    monkeypatch.setattr("openwakeword.get_pretrained_model_paths", lambda: [str(model)])
    with pytest.raises(RuntimeError, match="not downloaded"):
        check_script.wake_model()
    (tmp_path / "hey_jarvis_v0.1.onnx").write_bytes(b"model")
    assert check_script.wake_model() == "hey_jarvis_v0.1.onnx"  # the ONNX file is the one Jarvis runs
    assert check_script.microphones() == "MacBook Pro Microphone"


def test_the_check_script_only_fails_the_install_for_packages(check_script, monkeypatch, capsys):
    for name in ("packages", "wake_model", "tools", "voices", "applescript", "apps", "microphones", "carbon",
                 "readings", "internet"):
        monkeypatch.setattr(check_script, name, lambda: "fine")
    assert check_script.main() == 0
    monkeypatch.setattr(check_script, "internet", lambda: (_ for _ in ()).throw(OSError("offline")))
    assert check_script.main() == 0 and "FAIL  internet: offline" in capsys.readouterr().out
    check_script.problems.clear()
    monkeypatch.setattr(check_script, "packages", lambda: (_ for _ in ()).throw(ImportError("no numpy")))
    assert check_script.main() == 1


def test_the_installer_closes_a_running_jarvis_and_opens_it_again():
    text = (MACOS / "install.sh").read_text(encoding="utf-8")
    assert "pkill -TERM -x Jarvis" in text and 'open "$APPS/Jarvis.app"' in text
    assert text.index("pkill -TERM -x Jarvis") < text.index("rm -rf \"$APPS/Jarvis.app\"")  # closed before replaced
    assert "pkill -TERM -x Jarvis" in (MACOS / "uninstall.sh").read_text(encoding="utf-8")


# --- the human voice's installer ----------------------------------------------------------------------

VOICE = MACOS / "voice"


@pytest.mark.parametrize("version", ["312", "313"])
def test_every_voice_package_is_pinned_with_hashes(version):
    packages, all_hashed = locked(VOICE / f"requirements-py{version}.txt")
    assert all_hashed and len(packages) > 50
    assert packages["chatterbox-tts"] == "0.1.7" and packages["torch"] == "2.6.0"
    assert packages["setuptools"] == "80.9.0"  # Perth's watermark needs pkg_resources
    for unused in ("gradio", "spacy-pkuseg", "pykakasi", "pre-commit", "virtualenv", "nodeenv"):
        assert unused not in packages
    assert packages["numpy"].startswith("2." if version == "313" else "1.")  # as Chatterbox asks


def test_the_voice_installer_only_installs_locked_ready_made_packages():
    text = (MACOS / "install_voice.sh").read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(MACOS / "install_voice.sh")], check=True)
    assert os.access(MACOS / "install_voice.sh", os.X_OK)
    pip = re.search(r"-m pip install(.*?)-r \"\$LOCK\"", text, re.DOTALL).group(1)
    for option in ("--require-hashes", "--only-binary=:all:", "--no-deps", "--isolated",
                   "--index-url https://pypi.org/simple"):
        assert option in pip
    assert "sudo" not in text and "curl" not in text
    assert '"$VENV/bin/python" -I "$HERE/voice/download_model.py"' in text  # the checked download
    assert "ready.json" in text and "--benchmark" in text
    assert "download ResembleAI/chatterbox-nano" in text and '--repo "$1"' in text


def test_uninstalling_removes_the_voice_but_keeps_recordings():
    removals = [line for line in (MACOS / "uninstall.sh").read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("rm ")]
    assert any('"$SUPPORT/voice-engine"' in line for line in removals)
    assert not any("voices" in line for line in removals)  # recordings are data: only --everything takes them
    assert "voice-engine" in (MACOS / "diagnose.sh").read_text(encoding="utf-8")


@pytest.fixture
def downloader():
    import importlib.util

    spec = importlib.util.spec_from_file_location("download_model", VOICE / "download_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Hub:
    """Answers like huggingface_hub's HfApi; `files` is {path: contents}."""

    def __init__(self, files, scans=None, lfs=("t3_turbo_v1.safetensors",)):
        import hashlib

        self.files, self.asked = files, []
        self.entries = []
        for path, data in files.items():
            entry = types.SimpleNamespace(path=path, size=len(data),
                                          blob_id=hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(),
                                          lfs=None, security={"status": (scans or {}).get(path, "safe")})
            if path in lfs:
                entry.lfs = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            self.entries.append(entry)
        self.entries.append(types.SimpleNamespace(path="assets"))  # a folder: no blob_id

    def model_info(self, repo):
        self.asked.append(("info", repo))
        return types.SimpleNamespace(sha="abc123")

    def list_repo_tree(self, repo, revision, recursive, expand):
        self.asked.append(("tree", repo, revision, recursive, expand))
        return list(self.entries)

    def download(self, files=None):
        def download(repo, revision, local_dir, allow_patterns):
            self.asked.append(("download", repo, revision, tuple(allow_patterns)))
            for path, data in (files or self.files).items():
                (Path(local_dir) / path).write_bytes(data)
        return download


MODEL = {"ve.safetensors": b"ve", "t3_turbo_v1.safetensors": b"t3" * 100, "s3gen_meanflow.safetensors": b"s3",
         "conds.pt": b"voice", "tokenizer.json": b"{}", "README.md": b"# not needed", ".gitattributes": b"*"}


def test_the_model_is_downloaded_and_checked(downloader, tmp_path, capsys):
    hub = Hub(MODEL)
    assert downloader.main(["--dir", str(tmp_path / "model")], api=hub, download=hub.download()) == 0
    record = json.loads((tmp_path / "model" / "source.json").read_text())
    assert record["repo"] == "ResembleAI/chatterbox-turbo" and record["revision"] == "abc123"
    assert set(record["files"]) == {"ve.safetensors", "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors",
                                    "conds.pt", "tokenizer.json"}  # not the README or .gitattributes
    assert ("tree", "ResembleAI/chatterbox-turbo", "abc123", True, True) in hub.asked
    out = capsys.readouterr().out
    assert "malware scan: safe" in out and "All 5 files match" in out
    hub.asked.clear()
    assert downloader.main(["--dir", str(tmp_path / "model")], api=hub, download=hub.download()) == 0
    assert ("info", "ResembleAI/chatterbox-turbo") not in hub.asked  # the same version as before


def test_a_file_that_doesnt_match_is_removed(downloader, tmp_path, capsys):
    hub = Hub(MODEL)
    broken = dict(MODEL, **{"t3_turbo_v1.safetensors": b"tampered"})
    assert downloader.main(["--dir", str(tmp_path)], api=hub, download=hub.download(broken)) == 1
    assert "t3_turbo_v1.safetensors doesn't match its checksum" in capsys.readouterr().out
    assert not (tmp_path / "t3_turbo_v1.safetensors").exists() and not (tmp_path / "source.json").exists()


def test_a_flagged_or_changed_model_is_not_installed(downloader, tmp_path, capsys):
    hub = Hub(MODEL, scans={"conds.pt": "unsafe"})
    assert downloader.main(["--dir", str(tmp_path)], api=hub, download=hub.download()) == 1
    assert "malware scan flagged: conds.pt (unsafe)" in capsys.readouterr().out
    assert not any(step[0] == "download" for step in hub.asked)
    hub = Hub({k: v for k, v in MODEL.items() if k != "ve.safetensors"})
    assert downloader.main(["--dir", str(tmp_path)], api=hub, download=hub.download()) == 1
    assert "doesn't have ve.safetensors" in capsys.readouterr().out


def test_nano_needs_its_own_weights(downloader, tmp_path, capsys):
    nano = dict(MODEL, **{"t3_nano_v1.safetensors": b"nano"})
    del nano["t3_turbo_v1.safetensors"]
    hub = Hub(nano, lfs=("t3_nano_v1.safetensors",))
    assert downloader.main(["--repo", "ResembleAI/chatterbox-nano", "--dir", str(tmp_path)], api=hub,
                           download=hub.download()) == 0
    assert json.loads((tmp_path / "source.json").read_text())["repo"] == "ResembleAI/chatterbox-nano"
    download = next(step for step in hub.asked if step[0] == "download")
    assert "t3_nano_v1.safetensors" in download[3] and "README.md" not in download[3]
    assert downloader.main(["--dir", str(tmp_path)], api=Hub(MODEL), download=Hub(MODEL).download()) == 0
    assert "Newest version" in capsys.readouterr().out  # a folder of another model isn't reused
