"""Turns speech from the microphone into text, using Google's free speech service.

Needs the SpeechRecognition and sounddevice packages. Without them, or without a
microphone, DictationUnavailable is raised and the caller lets you type instead."""
import sys

from . import config, flac


class DictationUnavailable(Exception):
    """No microphone or no speech packages."""


class DictationError(Exception):
    """Nothing usable was heard this time."""


def _microphone(sd, sr):
    """A SpeechRecognition audio source that records with sounddevice (no PyAudio needed)."""

    class Microphone(sr.AudioSource):
        SAMPLE_RATE = 16000
        SAMPLE_WIDTH = 2  # 16-bit samples
        CHUNK = 1024

        def __init__(self):
            self.stream = None
            self._input = None

        def __enter__(self):
            self._input = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE, channels=1, dtype="int16", blocksize=self.CHUNK
            )
            self._input.start()
            self.stream = self  # SpeechRecognition reads through source.stream.read()
            return self

        def read(self, frames):
            data, _overflowed = self._input.read(frames)
            return bytes(data)

        def __exit__(self, *exc):
            self._input.stop()
            self._input.close()
            self.stream = None

    return Microphone()


def _import():
    try:
        import sounddevice as sd
        import speech_recognition as sr
    except (ImportError, OSError) as exc:
        raise DictationUnavailable(f"voice packages missing: {exc}") from exc
    return sd, sr


def _audio(sr, raw, rate, width=2):
    """SpeechRecognition's audio. On a Mac its FLAC is made here: the `flac` program the
    package brings for Macs is built for Intel only and won't run on Apple silicon."""
    if sys.platform != "darwin":
        return sr.AudioData(raw, rate, width)

    class MacAudio(sr.AudioData):
        def get_flac_data(self, convert_rate=None, convert_width=None):
            return flac.encode(self.get_raw_data(convert_rate, 2), convert_rate or self.sample_rate)

    return MacAudio(raw, rate, width)


def _transcripts(recognizer, audio, sr):
    """Every way Google heard it, most likely first."""
    try:
        result = recognizer.recognize_google(audio, language=config.DICTATION_LANGUAGE, show_all=True)
    except sr.RequestError as exc:
        raise DictationError(f"the speech service didn't answer ({exc})") from exc
    alternatives = result.get("alternative", []) if isinstance(result, dict) else []
    texts = [a["transcript"].strip() for a in alternatives if isinstance(a, dict) and a.get("transcript", "").strip()]
    if not texts:
        raise DictationError("I couldn't make out the words")
    return texts


def listen(timeout=8, phrase_limit=60, alternatives=False, on_ready=None, pause=1.2):
    """Record one spoken message and return it as text (or a list of guesses, best first).

    on_ready is called once the room noise has been measured, right before listening,
    so a "speak now" sound doesn't come too early."""
    sd, sr = _import()
    recognizer = sr.Recognizer()
    recognizer.pause_threshold = pause  # how long a pause ends the message
    try:
        with _microphone(sd, sr) as mic:
            recognizer.adjust_for_ambient_noise(mic, duration=0.3)
            if on_ready:
                on_ready()
            audio = recognizer.listen(mic, timeout=timeout, phrase_time_limit=phrase_limit)
    except sd.PortAudioError as exc:
        raise DictationUnavailable(f"no microphone: {exc}") from exc
    except sr.WaitTimeoutError as exc:
        raise DictationError("I didn't hear anything") from exc
    texts = _transcripts(recognizer, _audio(sr, audio.frame_data, audio.sample_rate, audio.sample_width), sr)
    return texts if alternatives else texts[0]


def recognize(raw, rate=16000):
    """Turn recorded 16-bit mono audio into text. Returns the guesses, best first."""
    try:
        import speech_recognition as sr  # no microphone package needed for this
    except ImportError as exc:
        raise DictationUnavailable(f"voice packages missing: {exc}") from exc
    return _transcripts(sr.Recognizer(), _audio(sr, raw, rate), sr)
