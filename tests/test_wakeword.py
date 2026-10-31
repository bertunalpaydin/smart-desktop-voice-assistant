import sys

import numpy
import pytest

from jarvis import config, wakeword

QUIET = numpy.zeros(wakeword.CHUNK, dtype=numpy.int16)
LOUD = numpy.full(wakeword.CHUNK, 3000, dtype=numpy.int16)


W = wakeword.WARMUP  # frames that are only listened to after (re)starting


class FakeModel:
    def __init__(self, scores):
        self.scores = list(scores)
        self.resets = 0
        self.fed = 0

    def reset(self):
        self.resets += 1

    def predict(self, samples):
        self.fed += 1
        return {"hey_jarvis": self.scores.pop(0) if self.scores else 0.0}


class FakeMic:
    """Plays the given frames, then stops the listener."""

    def __init__(self, frames, ears=None):
        self.frames = list(frames)
        self.ears = ears
        self.opened = self.closed = 0

    def __call__(self):
        return self

    def __enter__(self):
        self.opened += 1
        return self

    def read(self, count):
        assert count == wakeword.CHUNK
        if not self.frames:
            self.ears._stopped.set()
            return QUIET
        return self.frames.pop(0)

    def __exit__(self, *exc):
        self.closed += 1


def run(frames, scores, **settings):
    """Run the listener over some audio; returns what it reported."""
    ears = wakeword.WakeWordListener(load_model=lambda: model, retry_after=0, **settings)
    model = FakeModel(scores)
    mic = FakeMic(frames, ears)
    ears._microphone = mic
    got = {"heard": 0, "commands": [], "nothing": 0, "failed": []}
    ears.heard.connect(lambda: got.__setitem__("heard", got["heard"] + 1))
    ears.command.connect(got["commands"].append)
    ears.nothing_said.connect(lambda: got.__setitem__("nothing", got["nothing"] + 1))
    ears.failed.connect(got["failed"].append)
    ears.run()
    return ears, model, mic, got


def test_hey_jarvis_then_a_command_in_one_go():
    # quiet room, "hey jarvis" (detected on its 3rd frame), the command, then quiet
    frames = [QUIET] * (W + 5) + [LOUD] * 3 + [LOUD] * 8 + [QUIET] * 20
    ears, model, mic, got = run(frames, [0.0] * (W + 7) + [0.9])
    assert got["heard"] == 1 and len(got["commands"]) == 1
    audio = numpy.frombuffer(got["commands"][0], dtype=numpy.int16)
    # it kept the last second before the wake word, then the chime gap (2), the rest of the
    # command (6) and the quiet that ended it (11)
    kept = wakeword.PRE_ROLL + wakeword.IGNORE_START + 6 + wakeword.END_SILENCE
    assert len(audio) == kept * wakeword.CHUNK
    assert (audio == 3000).sum() == 11 * wakeword.CHUNK  # every spoken frame
    assert model.resets == 2 and mic.opened == mic.closed == 1


def test_the_command_can_come_after_a_pause():
    frames = [QUIET] * (W + 5) + [LOUD] * 3 + [QUIET] * 15 + [LOUD] * 6 + [QUIET] * 20
    _ears, _model, _mic, got = run(frames, [0.0] * (W + 7) + [0.9])
    audio = numpy.frombuffer(got["commands"][0], dtype=numpy.int16)
    assert (audio == 3000).sum() == 9 * wakeword.CHUNK  # "hey jarvis" (3 frames) and the whole command (6)


def test_woken_up_but_nothing_said():
    frames = [QUIET] * (W + 5) + [LOUD] * 3 + [QUIET] * (wakeword.START_TIMEOUT + 5)
    _ears, _model, _mic, got = run(frames, [0.0] * (W + 7) + [0.9])
    assert got["heard"] == 1 and got["commands"] == [] and got["nothing"] == 1


def test_the_sensitivity_setting_decides_when_it_wakes(monkeypatch):
    frames = [QUIET] * (W + 4) + [LOUD] * 5 + [QUIET] * 20
    monkeypatch.setattr(config, "WAKE_WORD_SENSITIVITY", "low")
    assert run(frames, [0.0] * (W + 1) + [0.4, 0.45])[3]["heard"] == 0
    monkeypatch.setattr(config, "WAKE_WORD_SENSITIVITY", "high")
    assert run(frames, [0.0] * (W + 1) + [0.25])[3]["heard"] == 1
    monkeypatch.setattr(config, "WAKE_WORD_SENSITIVITY", "nonsense")
    assert wakeword.threshold() == wakeword.SENSITIVITY["normal"]


def test_false_scores_right_after_starting_are_ignored():
    # just after the microphone opens (and after every pause), the model can score a quiet room highly
    frames = [QUIET] * (W + 30)
    _ears, model, _mic, got = run(frames, [0.0] * 10 + [0.35, 0.6, 0.3] + [0.0] * 40)
    assert got["heard"] == 0 and model.fed > W


def test_the_hotkey_records_without_the_wake_word():
    frames = [QUIET] * 3 + [LOUD] * 6 + [QUIET] * 20
    ears = wakeword.WakeWordListener(load_model=lambda: FakeModel([]), retry_after=0)
    ears._microphone = FakeMic(frames, ears)
    commands = []
    ears.command.connect(commands.append)
    ears.record_now()
    assert ears.recording_requested
    ears.run()
    assert len(commands) == 1 and not ears.recording_requested
    audio = numpy.frombuffer(commands[0], dtype=numpy.int16)
    assert (audio == 3000).sum() == 6 * wakeword.CHUNK


def test_record_command_ignores_the_chime_and_stops_at_the_limit():
    chime_only = iter([LOUD] * wakeword.IGNORE_START + [QUIET] * (wakeword.START_TIMEOUT + 5))
    assert wakeword.record_command(lambda n: next(chime_only), [], room_level=0) is None
    endless = iter([LOUD] * (wakeword.MAX_FRAMES + 10))
    audio = wakeword.record_command(lambda n: next(endless), [], room_level=0)
    assert len(audio) == wakeword.MAX_FRAMES * wakeword.CHUNK * 2


def test_a_noisy_room_needs_louder_speech():
    noisy = numpy.full(wakeword.CHUNK, 900, dtype=numpy.int16)
    frames = iter([noisy] * (wakeword.START_TIMEOUT + 5))
    assert wakeword.record_command(lambda n: next(frames), [], room_level=wakeword.level(noisy)) is None
    assert wakeword.background([50, 60, 3000, 3000, 55]) == 55


def test_pausing_lets_go_of_the_microphone():
    ears = wakeword.WakeWordListener(load_model=lambda: FakeModel([]), retry_after=0)
    model = FakeModel([0.0] * 10)
    mic = FakeMic([QUIET] * 10, ears)
    ears._microphone = mic
    original = model.predict

    def predict(samples):
        if model.fed == 2:
            ears.pause()
        return original(samples)

    model.predict = predict
    ears._listen(model)
    assert model.fed == 3 and mic.closed == 1 and ears.paused
    ears.resume()
    assert not ears.paused


def test_a_missing_model_or_package_reports_and_stops():
    def broken():
        raise ImportError("No module named 'openwakeword'")

    ears = wakeword.WakeWordListener(load_model=broken)
    failed = []
    ears.failed.connect(failed.append)
    ears.run()
    assert "is off" in failed[0] and "openwakeword" in failed[0]


def test_a_microphone_problem_is_reported_once_and_retried():
    attempts = []

    class NoMic:
        def __enter__(self):
            attempts.append(1)
            if len(attempts) == 3:
                ears._stopped.set()
            raise OSError("no microphone")

        def __exit__(self, *exc):
            pass

    ears = wakeword.WakeWordListener(load_model=lambda: FakeModel([]), microphone=NoMic, retry_after=0)
    failed = []
    ears.failed.connect(failed.append)
    ears.run()
    assert len(attempts) == 3
    assert len(failed) == 1 and "no microphone" in failed[0]


@pytest.mark.parametrize("module", ["openwakeword", "onnxruntime"])
def test_available_needs_both_packages(monkeypatch, module):
    monkeypatch.setitem(sys.modules, module, None)  # importing it now fails
    assert wakeword.available() is False
