"""The real SpeechRecognition package with a fake sounddevice playing prepared audio."""
import struct
import sys
import types

import pytest
import speech_recognition as sr

from jarvis import config, dictation

CHUNK = 1024
SILENCE = b"\x00\x00" * CHUNK
SPEECH = struct.pack(f"<{CHUNK}h", *([12000, -12000] * (CHUNK // 2)))  # loud enough to count as talking


def fake_sounddevice(chunks=(), fail_to_open=False):
    module = types.ModuleType("sounddevice")

    class PortAudioError(Exception):
        pass

    class RawInputStream:
        opened = []

        def __init__(self, **settings):
            if fail_to_open:
                raise PortAudioError("Error querying device -1")
            self.settings = settings
            self.chunks = list(chunks)
            self.closed = False
            RawInputStream.opened.append(self)

        def start(self):
            pass

        def read(self, frames):
            data = self.chunks.pop(0) if self.chunks else b"\x00\x00" * frames
            return memoryview(data), False

        def stop(self):
            pass

        def close(self):
            self.closed = True

    module.PortAudioError = PortAudioError
    module.RawInputStream = RawInputStream
    return module


@pytest.fixture
def microphone(monkeypatch):
    """Install a fake sounddevice; returns it."""
    def install(**kwargs):
        module = fake_sounddevice(**kwargs)
        monkeypatch.setitem(sys.modules, "sounddevice", module)
        return module
    return install


def recognizer_says(monkeypatch, result):
    """Make Google "hear" `result`: a text, a list of guesses, or an error."""
    calls = []

    def recognize_google(self, audio, language=None, show_all=False, **kwargs):
        calls.append((audio, language))
        if isinstance(result, sr.UnknownValueError) and show_all:
            return []  # what the library returns for "no words" when asked for every guess
        if isinstance(result, Exception):
            raise result
        guesses = [result] if isinstance(result, str) else result
        return {"alternative": [{"transcript": text} for text in guesses], "final": True} if show_all else guesses[0]

    monkeypatch.setattr(sr.Recognizer, "recognize_google", recognize_google)
    return calls


def test_speech_is_recorded_and_turned_into_text(monkeypatch, microphone):
    sd = microphone(chunks=[SILENCE] * 10 + [SPEECH] * 15 + [SILENCE] * 30)
    calls = recognizer_says(monkeypatch, "See you at ten")
    monkeypatch.setattr(config, "DICTATION_LANGUAGE", "en-GB")

    assert dictation.listen() == "See you at ten"
    audio, language = calls[0]
    assert language == "en-GB"
    assert audio.sample_rate == 16000 and audio.sample_width == 2
    assert len(audio.frame_data) >= 15 * len(SPEECH)  # the whole spoken part was kept
    stream = sd.RawInputStream.opened[0]
    assert stream.settings == {"samplerate": 16000, "channels": 1, "dtype": "int16", "blocksize": CHUNK}
    assert stream.closed


def test_silence_ends_in_a_dictation_error(monkeypatch, microphone):
    microphone()  # nothing but silence
    recognizer_says(monkeypatch, "never used")
    with pytest.raises(dictation.DictationError, match="didn't hear"):
        dictation.listen(timeout=1)


@pytest.mark.parametrize("error", [sr.UnknownValueError(), sr.RequestError("offline")])
def test_unclear_speech_or_no_internet_is_a_dictation_error(monkeypatch, microphone, error):
    microphone(chunks=[SILENCE] * 10 + [SPEECH] * 10)
    recognizer_says(monkeypatch, error)
    with pytest.raises(dictation.DictationError):
        dictation.listen()


def test_no_microphone_means_dictation_is_unavailable(monkeypatch, microphone):
    microphone(fail_to_open=True)
    with pytest.raises(dictation.DictationUnavailable, match="no microphone"):
        dictation.listen()


def test_missing_packages_mean_dictation_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # import now fails
    with pytest.raises(dictation.DictationUnavailable, match="voice packages missing"):
        dictation.listen()


def test_every_guess_can_be_asked_for(monkeypatch, microphone):
    microphone(chunks=[SILENCE] * 10 + [SPEECH] * 15 + [SILENCE] * 30)
    recognizer_says(monkeypatch, ["open what's up", "open WhatsApp"])
    assert dictation.listen(alternatives=True) == ["open what's up", "open WhatsApp"]


def test_the_ready_sound_comes_after_measuring_the_room(monkeypatch, microphone):
    microphone(chunks=[SILENCE] * 10 + [SPEECH] * 15 + [SILENCE] * 30)
    recognizer_says(monkeypatch, "hello")
    order = []
    original = sr.Recognizer.adjust_for_ambient_noise
    monkeypatch.setattr(sr.Recognizer, "adjust_for_ambient_noise",
                        lambda self, source, duration=1: (order.append("measured"), original(self, source, duration)))
    dictation.listen(on_ready=lambda: order.append("ready"))
    assert order == ["measured", "ready"]


def test_recorded_audio_is_recognised(monkeypatch):
    calls = recognizer_says(monkeypatch, ["what time is it", "what time is in"])
    raw = SPEECH * 20
    assert dictation.recognize(raw) == ["what time is it", "what time is in"]
    audio, _language = calls[0]
    assert audio.frame_data == raw and audio.sample_rate == 16000 and audio.sample_width == 2


def test_recognising_nothing_is_a_dictation_error(monkeypatch):
    recognizer_says(monkeypatch, sr.UnknownValueError())
    with pytest.raises(dictation.DictationError, match="couldn't make out"):
        dictation.recognize(SILENCE * 10)


def test_a_mac_makes_its_own_flac(monkeypatch):
    from tests.test_flac import read

    raw = SPEECH * 3
    monkeypatch.setattr(dictation.sys, "platform", "darwin")
    audio = dictation._audio(sr, raw, 16000)
    rate, total, samples = read(audio.get_flac_data(convert_width=2))  # what recognize_google asks for
    assert (rate, total) == (16000, len(raw) // 2) and samples[:4] == [12000, -12000, 12000, -12000]
    assert read(audio.get_flac_data(convert_rate=8000, convert_width=2))[0] == 8000
    monkeypatch.setattr(dictation.sys, "platform", "win32")
    assert type(dictation._audio(sr, raw, 16000)) is sr.AudioData  # elsewhere SpeechRecognition's own way
