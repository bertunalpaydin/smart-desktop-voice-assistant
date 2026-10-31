import json
import subprocess
import sys
import threading
import wave
from pathlib import Path

import pytest

from jarvis import config, human_voice, macos, speech

FAKE_WORKER = Path(__file__).with_name("fake_voice_worker.py")


@pytest.mark.parametrize("text, expected", [
    ("", []),
    ("Hello.", ["Hello."]),
    ("It's 8 PM. The weather is fine. Anything else?", ["It's 8 PM. The weather is fine. Anything else?"]),
    ("A" * 100 + ". " + "B" * 100 + ".", ["A" * 100 + ".", "B" * 100 + "."]),
    ("Tomorrow in Bristol: overcast skies, between 8 and 13 degrees, with a 20 percent chance of rain.",
     ["Tomorrow in Bristol:", "overcast skies, between 8 and 13 degrees, with a 20 percent chance of rain."]),
    ("Good evening, sir. What can I do for you?", ["Good evening, sir. What can I do for you?"]),
    ("Hi. " + ". ".join(["Word " * 10] * 6), None),
])
def test_text_in_pieces(text, expected):
    """The first piece is short, so Jarvis starts talking at once; the next ones can be longer."""
    parts = human_voice.pieces(text)
    if expected is not None:
        assert parts == expected
    assert " ".join(parts) == " ".join(text.split())
    assert all(len(p) <= 260 for p in parts) and (not parts or len(parts[0]) <= 70 or "." not in parts[0][:-1])


def test_a_long_sentence_is_cut_at_commas_then_spaces():
    sentence = ", ".join(["one two three four five six seven"] * 12) + "."
    parts = human_voice.pieces(sentence)
    assert all(len(p) <= 260 for p in parts) and len(parts) > 1
    assert " ".join(parts) == sentence


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A pretend install: the paths exist, and the worker is tests/fake_voice_worker.py."""
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Jarvis")
    monkeypatch.setattr(macos, "available", lambda: True)
    human_voice.python().parent.mkdir(parents=True)
    human_voice.python().write_text("")
    for name in ("nano", "turbo"):
        human_voice.model_dir(name).mkdir(parents=True)
        (human_voice.model_dir(name) / "source.json").write_text("{}")
    (human_voice.engine_dir() / "ready.json").write_text(json.dumps({"models": {
        "nano": {"device": "cpu", "speed": 4.2, "first_piece": 0.3},
        "turbo": {"device": "mps", "speed": 1.6, "first_piece": 0.9}}}))
    return tmp_path


def fake_popen(env_extra=None, seen=None):
    def popen(args, **kwargs):
        if seen is not None:
            seen.append(args)
        assert args[1] == "-I" and args[2] == str(human_voice.WORKER)  # what the real one runs
        env = dict(kwargs.pop("env"), **(env_extra or {}))
        return subprocess.Popen([sys.executable, str(FAKE_WORKER), *args[3:]], env=env, **kwargs)
    return popen


class Player:
    def __init__(self, during=None):
        self.played, self.during = [], during

    def __call__(self, path):
        with wave.open(path) as audio:
            self.played.append(audio.getnframes())
        if self.during:
            return self.during(len(self.played))
        return True


@pytest.fixture
def engine(installed):
    made = []

    def make(env_extra=None, during=None, wait=10, seen=None):
        player = Player(during)
        engine = human_voice.Engine(popen=fake_popen(env_extra, seen), play=player, wait=wait)
        engine.player = player
        made.append(engine)
        return engine

    yield make
    for engine in made:
        engine.stop()


def test_it_loads_then_speaks_piece_by_piece(engine):
    seen = []
    voice = engine(seen=seen)
    assert voice.status() == "off"
    voice.start()
    voice.start()  # once is enough
    assert voice.wait_ready(10) and voice.status() == "ready" and voice.device == "cpu"
    assert len(seen) == 1 and seen[0][4] == str(human_voice.model_dir("turbo"))  # it keeps up: the better one
    assert seen[0][-2:] == ["--device", "mps"]  # where the installer measured it to be faster
    text = "A" * 100 + ". " + "B" * 120 + "."
    assert voice.say(text) is True
    assert voice.player.played == [80 + 10 * 101, 80 + 10 * 121]
    assert list((human_voice.engine_dir() / "speech").glob("*.wav")) == []  # played and deleted
    assert len(list((human_voice.engine_dir() / "cache").glob("*.wav"))) == 2  # and remembered


def test_while_loading_or_failed_the_mac_voice_takes_over(engine):
    voice = engine(env_extra={"FAIL_LOAD": "1"})
    with pytest.raises(human_voice.VoiceError) as error:
        voice.say("Hello there.")
    assert error.value.rest == "Hello there."
    voice.start()
    assert voice.wait_ready(10) is False
    assert voice.status() == "failed: MemoryError: not enough memory"


def test_a_piece_that_fails_leaves_the_rest_for_the_mac_voice(engine):
    voice = engine()
    voice.start()
    assert voice.wait_ready(10)
    text = "The first one is fine. " + "It goes on " * 12 + "until here. Then BAD words. And the end."
    with pytest.raises(human_voice.VoiceError) as error:
        voice.say(text)
    assert "cannot say that" in str(error.value)
    assert error.value.rest == human_voice.pieces(text)[1] == text[len("The first one is fine. "):]
    assert len(voice.player.played) == 1
    assert voice.say("Still working.") is True  # one bad piece doesn't end the voice


def test_a_crash_is_noticed(engine):
    voice = engine()
    voice.start()
    assert voice.wait_ready(10)
    with pytest.raises(human_voice.VoiceError):
        voice.say("CRASH now.")
    assert voice.status().startswith("failed")
    voice.start()  # and it can start again
    assert voice.wait_ready(10)


def test_interrupting_stops_the_rest(engine):
    voice = engine(during=lambda count: False if count == 1 and voice.interrupt() else True)
    voice.start()
    assert voice.wait_ready(10)
    long_text = ("This is the first sentence and it is a fairly long one indeed, to stand alone. "
                 + "More words to say. " * 30)
    assert voice.say(long_text) is False
    assert len(voice.player.played) == 1
    assert voice.interrupt() is False  # nothing is being said now
    assert voice.say("Hello again.") is True


def test_interrupting_while_waiting_for_a_piece(engine):
    voice = engine()
    voice.start()
    assert voice.wait_ready(10)
    result = []
    talking = threading.Thread(target=lambda: result.append(voice.say("SLOW start.")))
    talking.start()
    while voice._current is None:
        pass
    voice.interrupt()
    talking.join(5)
    assert result == [False] and voice.player.played == []


def test_a_piece_that_takes_too_long(engine):
    voice = engine(wait=0.2)
    voice.start()
    assert voice.wait_ready(10)
    with pytest.raises(human_voice.VoiceError, match="too long"):
        voice.say("SLOW start.")


def test_without_a_voice_of_its_own_a_recording_is_needed(engine):
    voice = engine()
    voice.start()
    assert voice.wait_ready(10)
    voice.built_in = False
    with pytest.raises(human_voice.VoiceError, match="no voice of its own"):
        voice.say("Hello.")
    sample = human_voice.engine_dir() / "me.wav"
    sample.write_bytes(b"recording")
    assert voice.say("Hello.", sample=sample) is True


def test_what_is_installed_and_wanted(installed, monkeypatch):
    assert human_voice.installed()
    assert human_voice.install_info()["speed"] == 1.6  # Turbo: fast enough here, and it sounds better
    monkeypatch.setattr(config, "VOICE_ENGINE", "mac")
    assert not human_voice.wanted() and human_voice.label() == ""
    monkeypatch.setattr(config, "VOICE_ENGINE", "chatterbox")
    assert human_voice.wanted()
    monkeypatch.setattr(human_voice.ENGINE, "state", "loading")
    assert human_voice.label() == "Voice: human (loading)"
    monkeypatch.setattr(human_voice.ENGINE, "state", "ready")
    assert human_voice.label() == "Voice: human"
    (human_voice.engine_dir() / "ready.json").unlink()
    assert not human_voice.installed() and human_voice.label() == "Voice: Mac (human voice not installed)"
    monkeypatch.setattr(macos, "available", lambda: False)
    assert not human_voice.installed()


def test_the_voice_recording_in_use(tmp_path, monkeypatch):
    sample = tmp_path / "me.wav"
    monkeypatch.setattr(config, "VOICE_SAMPLE", str(sample))
    assert human_voice.sample_path() is None  # not there (yet)
    sample.write_bytes(b"")
    assert human_voice.sample_path() == sample
    assert human_voice.sample_path("") is None


def test_recording_my_voice(installed):
    loud = (b"\x00\x20" * human_voice.SAMPLE_RATE) * 2
    path = human_voice.record_sample(seconds=2, record=lambda frames: loud[: frames * 2])
    assert path == human_voice.samples_dir() / "my-voice.wav"
    assert human_voice.wav_seconds(path) == 2
    with pytest.raises(human_voice.VoiceError, match="couldn't hear you"):
        human_voice.record_sample(seconds=2, record=lambda frames: b"\x00\x00" * frames)

    def no_microphone(frames):
        raise OSError("no input device")

    with pytest.raises(human_voice.VoiceError, match="no input device"):
        human_voice.record_sample(seconds=2, record=no_microphone)


def test_using_a_recording(installed, tmp_path):
    ran = []

    def afconvert(seconds):
        def run(args, **kwargs):
            ran.append(args)
            with wave.open(args[-1], "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(24000)
                out.writeframes(b"\x00\x00" * int(24000 * seconds))
            return subprocess.CompletedProcess(args, 0, "", "")
        return run

    path = human_voice.import_sample(tmp_path / "Voice Memo.m4a", run=afconvert(12))
    assert path == human_voice.samples_dir() / "voice-sample.wav"
    assert ran[0][:6] == ["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c"]
    with pytest.raises(human_voice.VoiceError, match="needs at least 6"):
        human_voice.import_sample(tmp_path / "short.m4a", run=afconvert(3))
    assert not path.exists()
    with pytest.raises(human_voice.VoiceError, match="couldn't read that file"):
        human_voice.import_sample(tmp_path / "x.txt",
                                  run=lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "bad"))


# --- speech.py picks the voice ----------------------------------------------------------

class FakeEngine:
    def __init__(self, result=True, ready=True):
        self.result, self._ready, self.calls = result, ready, []

    def start(self):
        self.calls.append("start")

    def ready(self):
        return self._ready

    def say(self, text, sample=None, effect=None):
        self.calls.append(("say", text, sample) if not effect else ("say", text, sample, effect))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def interrupt(self):
        self.calls.append("interrupt")
        return True


@pytest.fixture
def mac_speech(monkeypatch, installed):
    said = []

    def say(text, voice="", rate=0):
        said.append(text)
        return True

    monkeypatch.setattr(macos, "voices", lambda: [{"name": "Daniel", "culture": "en-GB"}])
    monkeypatch.setattr(macos, "say", say)
    monkeypatch.setattr(macos, "stop_speaking", lambda: False)
    monkeypatch.setattr(speech, "_mac_voices", None)
    monkeypatch.setattr(config, "VOICE_ENGINE", "chatterbox")
    return said


def test_speech_uses_the_human_voice(mac_speech, monkeypatch, tmp_path):
    fake = FakeEngine()
    monkeypatch.setattr(human_voice, "ENGINE", fake)
    sample = tmp_path / "me.wav"
    sample.write_bytes(b"")
    monkeypatch.setattr(config, "VOICE_SAMPLE", str(sample))
    assert speech.speak("Hello there.") is True
    assert fake.calls == ["start", ("say", "Hello there.", sample)] and mac_speech == []
    assert speech.speak("Test.", engine="mac") is True and mac_speech == ["Test."]
    monkeypatch.setattr(config, "VOICE_EFFECT", "helmet")
    speech.speak("Deeper.")
    assert fake.calls[-1] == ("say", "Deeper.", sample, "helmet")
    assert speech.stop() is True and fake.calls[-1] == "interrupt"


def test_the_mac_voice_finishes_what_the_human_voice_couldnt(mac_speech, monkeypatch, capsys):
    monkeypatch.setattr(human_voice, "ENGINE", FakeEngine(human_voice.VoiceError("boom", rest="The rest.")))
    assert speech.speak("First. The rest.") is True
    assert mac_speech == ["The rest."] and "human voice failed: boom" in capsys.readouterr().out
    monkeypatch.setattr(human_voice, "ENGINE", FakeEngine(human_voice.VoiceError("loading", rest="All."),
                                                          ready=False))
    assert speech.speak("All.") is True
    assert mac_speech[-1] == "All." and "human voice failed" not in capsys.readouterr().out
    monkeypatch.setattr(human_voice, "ENGINE", FakeEngine(False))  # stopped on purpose
    assert speech.speak("Hello.") is False and mac_speech[-1] == "All."


def test_the_mac_voice_when_the_human_one_isnt_installed(mac_speech, monkeypatch):
    fake = FakeEngine()
    monkeypatch.setattr(human_voice, "ENGINE", fake)
    (human_voice.engine_dir() / "ready.json").unlink()
    assert speech.speak("Hello.") is True
    assert mac_speech == ["Hello."] and fake.calls == []


def test_the_usual_replies_are_made_while_idle(engine):
    voice = engine()
    voice.phrases = lambda: ["Okay.", "Done.", "Okay."]
    voice.start()
    assert voice.wait_ready(10)
    cache = human_voice.engine_dir() / "cache"
    for _ in range(200):
        if len(list(cache.glob("*.wav"))) == 2:
            break
        threading.Event().wait(0.02)
    assert len(list(cache.glob("*.wav"))) == 2
    assert voice.say("Okay.") is True  # from the cache


def test_choosing_the_other_model_restarts_the_worker(engine, monkeypatch):
    seen = []
    voice = engine(seen=seen)
    voice.start()
    assert voice.wait_ready(10)
    monkeypatch.setattr(config, "VOICE_MODEL", "nano")
    voice.start()
    assert voice.wait_ready(10)
    assert [args[4] for args in seen] == [str(human_voice.model_dir("turbo")), str(human_voice.model_dir("nano"))]
    assert seen[1][-2:] == ["--device", "cpu"]
    voice.start()
    assert len(seen) == 2  # already running that one


def test_which_model(installed, monkeypatch):
    assert human_voice.models_installed() == ["nano", "turbo"]
    assert human_voice.model_name() == "turbo"  # automatic: Turbo keeps up on this Mac
    ready = human_voice.engine_dir() / "ready.json"
    slow = json.loads(ready.read_text())
    slow["models"]["turbo"]["speed"] = 0.7
    ready.write_text(json.dumps(slow))
    assert human_voice.model_name() == "nano"  # it wouldn't: the faster one
    monkeypatch.setattr(config, "VOICE_MODEL", "turbo")
    assert human_voice.model_name() == "turbo"  # unless it's asked for
    slow["models"]["turbo"]["speed"] = 1.6
    ready.write_text(json.dumps(slow))
    monkeypatch.setattr(config, "VOICE_MODEL", "turbo")
    assert human_voice.model_name() == "turbo" and human_voice.install_info()["speed"] == 1.6
    (human_voice.model_dir("turbo") / "source.json").unlink()
    assert human_voice.model_name() == "nano"  # what is there
    old = human_voice.engine_dir() / "ready.json"
    old.write_text(json.dumps({"event": "ok", "device": "mps", "audio_seconds": 3.6, "took": 23.4}))  # first installer
    (human_voice.model_dir("turbo") / "source.json").write_text("{}")
    assert human_voice.models_installed() == ["turbo"] and human_voice.install_info()["device"] == "mps"
