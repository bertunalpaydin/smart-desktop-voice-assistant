"""Listens for "Hey Jarvis" and records the command that follows.

The wake word is recognised offline with openWakeWord. The microphone stays open
while the command is spoken, so "Hey Jarvis, what time is it?" in one breath works:
nothing is lost between the wake word and the command. Only the command itself is
later sent for speech recognition.
"""
import collections
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

from . import config

RATE = 16000
CHUNK = 1280  # 80 ms of audio, what the model expects
SENSITIVITY = {"low": 0.5, "normal": 0.3, "high": 0.2}  # the score needed to wake up

WARMUP = 18  # ~1.4 s: right after starting, the model can give false scores even in a quiet room
PRE_ROLL = 12  # keep ~1 s from before the wake word, in case the command started straight away
IGNORE_START = 2  # ~0.16 s for the "listening" chime
SPEECH_FRAMES = 3  # ~0.24 s of sound counts as talking
END_SILENCE = 11  # ~0.9 s of quiet after talking ends the command
START_TIMEOUT = 62  # ~5 s to start talking
MAX_FRAMES = 150  # 12 s at most
MIN_LEVEL = 180  # quieter than this is never speech (RMS of 16-bit audio)


def available():
    try:
        import onnxruntime  # noqa: F401
        import openwakeword  # noqa: F401
    except ImportError:
        return False
    return True


def threshold():
    return SENSITIVITY.get(config.WAKE_WORD_SENSITIVITY, SENSITIVITY["normal"])


def level(samples):
    """How loud a piece of audio is (root mean square)."""
    import numpy

    data = numpy.asarray(samples, dtype=numpy.float64)
    return float(numpy.sqrt(numpy.mean(data * data))) if data.size else 0.0


def background(levels):
    """The usual loudness of the room, from the levels of the last few seconds."""
    ordered = sorted(levels)
    return ordered[len(ordered) // 5] if ordered else 0.0


def record_command(read, frames, room_level, stopped=None):
    """Keep recording until the speaker has finished.

    `frames` already holds what came before (if anything). Returns all of it as
    16-bit audio bytes, or None if nobody started talking."""
    import numpy

    loud_enough = max(room_level * 3, MIN_LEVEL)
    loud = quiet = 0
    talking = False
    for count in range(1, MAX_FRAMES + 1):
        if stopped is not None and stopped.is_set():
            return None
        samples = read(CHUNK)
        frames.append(samples)
        if count <= IGNORE_START:
            continue
        if level(samples) >= loud_enough:
            loud += 1
            quiet = 0
            talking = talking or loud >= SPEECH_FRAMES
        else:
            quiet += 1
        if talking and quiet >= END_SILENCE:
            break
        if not talking and count >= START_TIMEOUT:
            return None
    return numpy.concatenate([numpy.asarray(f, dtype=numpy.int16) for f in frames]).tobytes()


def _load_model():
    import openwakeword.utils
    from openwakeword.model import Model

    openwakeword.utils.download_models(model_names=["hey_jarvis"])  # a few MB, only the first time
    return Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")


class _Microphone:
    def __enter__(self):
        import numpy
        import sounddevice

        self._numpy = numpy
        self._stream = sounddevice.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK)
        self._stream.start()
        return self

    def read(self, frames):
        data, _overflowed = self._stream.read(frames)
        return self._numpy.frombuffer(bytes(data), dtype=self._numpy.int16)

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()


class WakeWordListener(QThread):
    heard = pyqtSignal()  # woke up: the command is being recorded now
    command = pyqtSignal(bytes)  # the recorded command, 16 kHz 16-bit mono
    nothing_said = pyqtSignal()  # woke up, but no command followed
    failed = pyqtSignal(str)

    def __init__(self, load_model=_load_model, microphone=_Microphone, retry_after=10):
        super().__init__()
        self._load_model = load_model
        self._microphone = microphone
        self._retry_after = retry_after
        self._paused = threading.Event()
        self._stopped = threading.Event()
        self._record_now = threading.Event()
        self._reported = None  # the last problem reported, so it isn't repeated every few seconds

    def pause(self):
        """Let go of the microphone (while Jarvis talks or something else records)."""
        self._paused.set()

    def resume(self):
        self._paused.clear()

    @property
    def paused(self):
        return self._paused.is_set()

    def record_now(self):
        """Record a command straight away, without the wake word (Ctrl+Alt+J)."""
        self._record_now.set()

    @property
    def recording_requested(self):
        return self._record_now.is_set()

    def stop(self):
        self._stopped.set()
        self.wait(3000)

    def run(self):
        try:
            model = self._load_model()
        except Exception as exc:  # missing packages, no internet for the first download, ...
            self.failed.emit(f'"Hey Jarvis" is off: {exc}')
            return
        while not self._stopped.is_set():
            if self._paused.is_set():
                time.sleep(0.05)
                continue
            try:
                self._listen(model)
            except Exception as exc:  # no microphone, unplugged, ...
                message = f'"Hey Jarvis" can\'t use the microphone: {exc}'
                if message != self._reported:
                    self.failed.emit(message)
                    self._reported = message
                self._stopped.wait(self._retry_after)

    def _listen(self, model):
        with self._microphone() as mic:
            self._reported = None
            model.reset()
            fed = 0  # frames since the model was (re)started
            recent = collections.deque(maxlen=PRE_ROLL)
            levels = collections.deque(maxlen=40)
            while not (self._stopped.is_set() or self._paused.is_set()):
                samples = mic.read(CHUNK)
                if self._record_now.is_set():
                    self._record_now.clear()
                    self._take_command(mic, [], levels)
                    model.reset()
                    fed = 0
                    continue
                recent.append(samples)
                levels.append(level(samples))
                score = max(model.predict(samples).values(), default=0)
                fed += 1
                if fed > WARMUP and score >= threshold():
                    self._take_command(mic, list(recent), levels)
                    model.reset()
                    fed = 0
                    recent.clear()

    def _take_command(self, mic, frames, levels):
        self.heard.emit()
        audio = record_command(mic.read, frames, background(levels), self._stopped)
        if audio:
            self.command.emit(audio)
        elif not self._stopped.is_set():
            self.nothing_said.emit()
