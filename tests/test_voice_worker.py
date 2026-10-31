import json
import os
import subprocess
import sys
import time
import types
import wave
from pathlib import Path

import numpy
import pytest

from jarvis import voice_worker

WORKER = Path(voice_worker.__file__)


class FakeModel:
    """Looks like ChatterboxTurboTTS: conds, sr, generate(), prepare_conditionals(), norm_loudness()."""

    sr = 24000

    def __init__(self, conds="built-in voice"):
        self.conds = conds
        self.prepared = []

    def prepare_conditionals(self, path):
        wav = self.norm_loudness(numpy.ones(4, dtype="float32"), self.sr)
        assert wav.dtype == numpy.float32  # the NumPy 2 fix
        self.prepared.append(path)
        self.conds = f"voice of {Path(path).name}"

    def norm_loudness(self, wav, sr, target_lufs=-27):
        return wav * numpy.float64(0.5)  # what pyloudnorm's gain does with NumPy 2: 64-bit floats

    def generate(self, text):
        import torch

        return torch.zeros(1, 240) + 0.25 if self.conds else None


@pytest.fixture
def torch_stub(monkeypatch):
    """Just enough of torch for Engine.speak: inference_mode() and tensors with .cpu().numpy()."""
    import contextlib
    import types

    class Tensor:
        def __init__(self, value):
            self.value = value

        def __add__(self, other):
            return Tensor(self.value + other)

        def squeeze(self, axis):
            return Tensor(self.value[0])

        def cpu(self):
            return self

        def numpy(self):
            return self.value

    stub = types.SimpleNamespace(inference_mode=contextlib.nullcontext,
                                 zeros=lambda *shape: Tensor(numpy.zeros(shape, dtype="float32")))
    monkeypatch.setitem(sys.modules, "torch", stub)
    return stub


def test_the_engine_uses_the_built_in_voice_or_a_recording(torch_stub, tmp_path):
    model = FakeModel()
    engine = voice_worker.Engine(model, "mps")
    audio = engine.speak("Hello.")
    assert audio.shape == (240,) and model.conds == "built-in voice"
    sample = tmp_path / "me.wav"
    sample.write_bytes(b"RIFF")
    engine.speak("Hello.", str(sample))
    engine.speak("Again.", str(sample))
    assert model.prepared == [str(sample)]  # worked out once
    assert model.conds == "voice of me.wav"
    engine.speak("Back to normal.")
    assert model.conds == "built-in voice"


def test_without_a_built_in_voice_a_recording_is_needed(torch_stub):
    engine = voice_worker.Engine(FakeModel(conds=None), "cpu")
    with pytest.raises(RuntimeError, match="no voice of its own"):
        engine.speak("Hello.")


def test_wav_files(tmp_path):
    seconds = voice_worker.write_wav(tmp_path / "a.wav", [0.0, 0.5, -2.0, 1.0], 8000)
    assert seconds == 4 / 8000
    with wave.open(str(tmp_path / "a.wav")) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 8000)
        assert numpy.frombuffer(audio.readframes(4), "<i2").tolist() == [0, 16383, -32767, 32767]


class Speaker(voice_worker.Engine):
    """The real Engine (cache and all) with a pretend model that says everything as 8 samples."""

    def __init__(self, cache=None, delay=0.0, device="cpu"):
        super().__init__(types.SimpleNamespace(sr=8000, conds="built-in"), device, cache, "pretend-1")
        self.said, self.delay = [], delay

    def speak(self, text, sample=None):
        if text == "bad":
            raise ValueError("cannot say that")
        time.sleep(self.delay)
        self.said.append((text, sample))
        return numpy.zeros(8 * len(text), dtype="float32")


class Lines:
    """stdin that stays open a little after its last line, as Jarvis's does."""

    def __init__(self, messages, linger=0.0):
        self.lines, self.linger = [json.dumps(m) + "\n" for m in messages], linger

    def __iter__(self):
        yield from self.lines
        time.sleep(self.linger)


def run_serve(lines, load, tmp_path, linger=0.0):
    sent = []
    code = voice_worker.serve(load, tmp_path / "out", Lines(lines, linger), sent.append)
    return code, sent


def test_serving_requests(tmp_path):
    speaker = Speaker()
    code, sent = run_serve([
        {"id": "1-0", "job": "1", "text": "Hello.", "voice": None},
        {"cancel": "2"},
        {"id": "2-0", "job": "2", "text": "Skipped."},
        {"id": "3-0", "job": "3", "text": "bad"},
        {"id": "3-1", "job": "3", "text": "  "},
        {"id": "../4", "job": "4", "text": "Mine.", "voice": None},
        {"quit": True},
        {"id": "5-0", "job": "5", "text": "Never read."},
    ], lambda: speaker, tmp_path)
    assert code == 0
    assert sent[0]["event"] == "ready" and sent[0]["device"] == "cpu" and sent[0]["built_in"] is True
    assert sent[1] == {"id": "1-0", "wav": str(tmp_path / "out" / "1-0.wav"), "audio_seconds": 0.01,
                       "took": sent[1]["took"], "cached": False}
    assert (tmp_path / "out" / "1-0.wav").exists()
    assert sent[2] == {"id": "2-0", "cancelled": True}
    assert sent[3] == {"id": "3-0", "error": "ValueError: cannot say that"}
    assert sent[4] == {"id": "3-1", "error": "no text"}
    assert sent[5]["wav"] == str(tmp_path / "out" / "___4.wav")  # no way out of the folder
    assert speaker.said == [("Ready.", None), ("Hello.", None), ("Mine.", None)]  # warmed up first
    assert len(sent) == 6


def test_what_was_said_before_comes_from_the_cache(tmp_path):
    speaker = Speaker(cache=tmp_path / "cache")
    first = speaker.speak_to(tmp_path / "a.wav", "Good evening.")
    again = speaker.speak_to(tmp_path / "b.wav", "Good evening.")
    assert first == (0.013, False) and again == (0.013, True)
    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()
    sample = tmp_path / "me.wav"
    sample.write_bytes(b"x")
    assert speaker.speak_to(tmp_path / "c.wav", "Good evening.", str(sample))[1] is False  # another voice
    assert speaker.speak_to(tmp_path / "d.wav", "Good evening.", str(sample))[1] is True
    sample.write_bytes(b"xy")  # a new recording: made again
    assert speaker.speak_to(tmp_path / "e.wav", "Good evening.", str(sample))[1] is False
    assert [text for text, _ in speaker.said] == ["Good evening."] * 3
    assert Speaker().speak_to(tmp_path / "f.wav", "No cache.") == (0.009, False)


def test_the_cache_keeps_the_newest(tmp_path):
    speaker = Speaker(cache=tmp_path / "cache")
    for i in range(5):
        speaker.speak_to(tmp_path / "x.wav", f"Sentence {i}.")
        os.utime(speaker._cached(f"Sentence {i}.", None), (i, i))
    size = (tmp_path / "cache").glob("*.wav").__next__().stat().st_size
    speaker._tidy_cache(limit=size * 2)
    left = sorted(p.stat().st_mtime for p in (tmp_path / "cache").glob("*.wav"))
    assert left == [3, 4]


def test_usual_replies_are_made_while_idle(tmp_path):
    speaker = Speaker(cache=tmp_path / "cache")
    code, sent = run_serve([{"warm": ["Okay.", "Done.", " "], "voice": None}], lambda: speaker, tmp_path, linger=0.5)
    assert code == 0 and [m.get("event") for m in sent] == ["ready"]
    assert [text for text, _ in speaker.said] == ["Ready.", "Okay.", "Done."]
    assert speaker.remember("Okay.") is False  # already there
    assert speaker.speak_to(tmp_path / "o.wav", "Done.")[1] is True


def test_a_model_that_doesnt_load(tmp_path):
    def load():
        raise MemoryError("not enough memory")

    code, sent = run_serve([], load, tmp_path)
    assert code == 1 and sent == [{"event": "error", "message": "MemoryError: not enough memory"}]


def test_it_ends_when_jarvis_goes(tmp_path):
    code, sent = run_serve([], Speaker, tmp_path)
    assert code == 0 and [m["event"] for m in sent] == ["ready"]


def test_the_faster_device_wins(tmp_path):
    def load(model, device):
        if device == "broken":
            raise RuntimeError("no such chip")
        return Speaker(delay=0.03 if device == "mps" else 0.001, device=device)

    result = voice_worker.benchmark("model", str(tmp_path / "test.wav"), ["mps", "cpu", "broken"], load)
    assert result["event"] == "ok" and result["device"] == "cpu"
    assert result["devices"]["mps"]["speed"] < result["devices"]["cpu"]["speed"]
    assert result["devices"]["broken"] == {"error": "RuntimeError: no such chip"}
    assert (tmp_path / "test.wav").exists() and list(tmp_path.glob("test.wav.*")) == []
    failed = voice_worker.benchmark("model", str(tmp_path / "x.wav"), ["broken"], load)
    assert failed == {"event": "error", "message": "broken: RuntimeError: no such chip"}


def test_the_check_says_what_is_missing():
    done = subprocess.run([sys.executable, "-I", str(WORKER), "--check"], capture_output=True, text=True, timeout=60)
    message = json.loads(done.stdout.strip().splitlines()[-1])
    if done.returncode == 0:  # the voice packages happen to be installed here
        assert message["event"] == "ok"
    else:
        assert message["event"] == "error" and "No module named" in message["message"]


def test_library_printing_cannot_spoil_the_messages(tmp_path):
    script = tmp_path / "noisy.py"
    script.write_text(f"""
import sys
sys.path.insert(0, {str(WORKER.parent.parent)!r})
from jarvis import voice_worker
send = voice_worker._protocol_output()
print("loaded PerthNet (Implicit) at step 250,000")  # what the libraries do
send({{"event": "ready"}})
""")
    done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
    assert done.stdout == '{"event": "ready"}\n'
    assert "PerthNet" in done.stderr


def test_the_helmet_effect_is_its_own_cache_entry(tmp_path, monkeypatch):
    monkeypatch.setitem(voice_worker.EFFECTS, "helmet", lambda audio, rate: audio * 0 + 0.5)
    speaker = Speaker(cache=tmp_path / "cache")
    speaker.speak_to(tmp_path / "plain.wav", "Hello.")
    assert speaker.speak_to(tmp_path / "deep.wav", "Hello.", effect="helmet") == (0.006, False)
    assert speaker.speak_to(tmp_path / "again.wav", "Hello.", effect="helmet")[1] is True
    with wave.open(str(tmp_path / "deep.wav")) as audio:
        assert set(numpy.frombuffer(audio.readframes(48), "<i2")) == {16383}
    with wave.open(str(tmp_path / "plain.wav")) as audio:
        assert set(numpy.frombuffer(audio.readframes(48), "<i2")) == {0}


def test_the_helmet_effect():
    pytest.importorskip("librosa")
    rate = 8000
    tone = (0.3 * numpy.sin(2 * numpy.pi * 200 * numpy.arange(rate) / rate)).astype("float32")
    deep = voice_worker.helmet(tone, rate)
    assert len(deep) == len(tone) and deep.dtype == numpy.float32
    assert 0.85 < float(numpy.abs(deep).max()) <= 0.9
    assert len(voice_worker.helmet(tone[:10], rate)) == 10
