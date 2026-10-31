"""The human voice on a Mac: Chatterbox, an open speech model by Resemble AI, in two sizes:
Nano (fast) and Turbo (a little better, slower).

macos/install_voice.sh puts it in its own Python environment next to Jarvis's. This file starts
voice_worker.py there and keeps the delay short: the text goes in pieces, the first one short, and
each piece plays as soon as it is ready while the next is made; what Jarvis says often (and
everything said before) comes from a cache, made while it's idle. While the model loads (a few
seconds after Jarvis starts), or if it fails, Jarvis uses the Mac's own voice instead. It can
speak in Chatterbox's own voice or in a voice from a short recording.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

from . import config, macos

WORKER = Path(__file__).with_name("voice_worker.py")
WAIT = 30  # seconds to wait for a piece of speech before falling back to the Mac's voice
SAMPLE_SECONDS = 12  # how long "record my voice" listens
SAMPLE_RATE = 24000
SAMPLE_TEXT = ("I'm recording my voice for Jarvis. The quick brown fox jumps over the lazy dog, "
               "and the five boxing wizards jump quickly. Good morning, good evening, and good night.")


class VoiceError(Exception):
    def __init__(self, message, rest=""):
        super().__init__(message)
        self.rest = rest  # what wasn't said yet, for the Mac's voice to finish


def engine_dir():
    return config.APP_DIR / "voice-engine"


def python():
    return engine_dir() / "venv" / "bin" / "python"


MODELS = {"nano": "model-nano", "turbo": "model"}  # the model's folder in engine_dir()


def model_dir(name=None):
    return engine_dir() / MODELS[name or model_name()]


def samples_dir():
    return config.APP_DIR / "voices"


def install_info(name=None):
    """What the installer measured for a model: {"device", "speed", "first_piece", ...}, or {}."""
    try:
        info = json.loads((engine_dir() / "ready.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    models = info.get("models") if isinstance(info, dict) else None
    if not isinstance(models, dict):
        models = {"turbo": info} if isinstance(info, dict) and info.get("device") else {}  # the first installer
    found = models.get(name or model_name(models))
    return found if isinstance(found, dict) else {}


def models_installed(models=None):
    """The models the installer set up and measured, fastest first."""
    if models is None:
        try:
            info = json.loads((engine_dir() / "ready.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        models = info.get("models") if isinstance(info, dict) else None
        if not isinstance(models, dict):
            models = {"turbo": info} if isinstance(info, dict) and info.get("device") else {}
    return [name for name in ("nano", "turbo") if isinstance(models.get(name), dict)
            and (engine_dir() / MODELS[name] / "source.json").exists()]


QUICK_ENOUGH = 1.0  # times as fast as speech: then Turbo keeps up with itself while it talks


def model_name(models=None):
    """The model in the settings if it's installed, else whichever is. "auto": Turbo, which sounds
    clearly better, if this Mac makes its speech quicker than it's spoken; else Nano."""
    found = models_installed(models)
    if config.VOICE_MODEL in found:
        return config.VOICE_MODEL
    if "turbo" in found and (len(found) == 1 or (install_info("turbo").get("speed") or 0) >= QUICK_ENOUGH):
        return "turbo"
    return found[0] if found else "turbo"


def installed():
    """Has macos/install_voice.sh finished on this Mac?"""
    return macos.available() and python().exists() and bool(models_installed())


def pieces(text, first=70, most=260):
    """Text in pieces to speak one after another: whole sentences where possible, none too long for
    the model, and the first one short (cut at a comma if need be) so Jarvis starts talking at once."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if s]
    if sentences and len(sentences[0]) > first:  # "Tomorrow in Bristol: overcast skies, ..."
        breaks = [m.end() for m in re.finditer(r"[,;:](?=\s)", sentences[0]) if 20 <= m.end() <= first]
        if breaks:
            sentences[:1] = [sentences[0][:breaks[0]], sentences[0][breaks[0]:].strip()]
    parts = []
    for sentence in sentences:  # long sentences: at commas, then at spaces
        while len(sentence) > most:
            cut = max(sentence.rfind(", ", 0, most), sentence.rfind("; ", 0, most))
            cut = cut + 1 if cut > most // 3 else sentence.rfind(" ", 0, most)
            if cut <= 0:
                cut = most
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            parts.append(sentence)
    merged = []
    for part in parts:
        if merged and len(merged[-1]) + 1 + len(part) <= (first if len(merged) == 1 else most):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


class Engine:
    """The worker process and the pieces of speech it has made."""

    def __init__(self, popen=subprocess.Popen, play=macos.speak_file, wait=WAIT):
        self._popen, self._play, self._wait_limit = popen, play, wait
        self._process = None
        self._model = None
        self.phrases = lambda: []  # what to make while idle (set by the app: Jarvis's usual replies)
        self._lock = threading.Lock()  # starting and stopping
        self._writing = threading.Lock()  # one message at a time into the worker
        self._changed = threading.Condition()  # new results, state changes, interruptions
        self._results = {}
        self.state, self.error, self.device = "off", "", ""
        self.built_in = True  # does the model have a voice of its own?
        self._jobs = 0
        self._current = None
        self._stopped = set()

    # --- the process ------------------------------------------------------------------

    def start(self):
        """Start loading the model in the background, if it isn't running yet (or runs another model)."""
        if self._process is not None and self._process.poll() is None and self._model != model_name():
            self.stop()
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            out = engine_dir() / "speech"
            out.mkdir(parents=True, exist_ok=True)
            for old in out.glob("*.wav"):  # left over from a crash
                old.unlink(missing_ok=True)
            env = {key: os.environ[key] for key in ("HOME", "PATH", "LANG", "TMPDIR", "USER") if key in os.environ}
            env.update({"HF_HOME": str(engine_dir() / "huggingface"), "HF_HUB_OFFLINE": "1",
                        "TQDM_DISABLE": "1", "PYTORCH_ENABLE_MPS_FALLBACK": "1"})
            name = model_name()
            device = install_info(name).get("device")  # the faster one when the installer measured them
            args = [str(python()), "-I", str(WORKER), "--model", str(model_dir(name)), "--out", str(out),
                    "--cache", str(engine_dir() / "cache")]
            if device in ("mps", "cpu"):
                args += ["--device", device]
            log_path = engine_dir() / "worker.log"
            if log_path.exists() and log_path.stat().st_size > 2_000_000:
                log_path.unlink()  # it only matters when something goes wrong; start it afresh
            log = open(log_path, "ab")
            try:
                process = self._popen(args,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                                      encoding="utf-8", bufsize=1, env=env, close_fds=True)
            except OSError as exc:
                self._set_state("failed", str(exc))
                return
            finally:
                log.close()
            self._process, self._model = process, name
            self._set_state("loading")
            threading.Thread(target=self._read, args=(process,), daemon=True).start()

    def stop(self):
        """End the worker (Jarvis is quitting, or the Mac's voice was chosen)."""
        with self._lock:
            process, self._process = self._process, None
        if process is None:
            return
        try:
            process.stdin.write(json.dumps({"quit": True}) + "\n")
            process.stdin.close()
            process.wait(timeout=3)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            process.kill()
        self._set_state("off")

    def _set_state(self, state, error=""):
        with self._changed:
            self.state, self.error = state, error
            self._changed.notify_all()

    def _read(self, process):
        for line in process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            with self._changed:
                ready = message.get("event") == "ready"
                if ready:
                    self.state, self.device = "ready", str(message.get("device", ""))
                    self.built_in = message.get("built_in") is not False
                elif message.get("event") == "error":
                    self.state, self.error = "failed", str(message.get("message", "the voice didn't load"))
                elif "id" in message:
                    self._results[str(message["id"])] = message
                self._changed.notify_all()
            if ready:
                self.warm()
        with self._changed:
            if self._process is process or self.state == "loading":
                self.state = "failed"
                self.error = self.error or "the voice engine stopped"
            self._changed.notify_all()

    def _send(self, message):
        process = self._process
        if process is None or process.poll() is not None:
            raise VoiceError(self.error or "the voice engine isn't running")
        try:
            with self._writing:
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise VoiceError(str(exc)) from exc

    def warm(self):
        """Have the worker make Jarvis's usual replies while it's idle, in the voice in use now."""
        if not self.ready():
            return False
        sample = sample_path()
        if not sample and not self.built_in:
            return False
        try:
            # short ones only: while it makes one, a real reply waits
            phrases = list(dict.fromkeys(p for text in self.phrases() for p in pieces(text) if len(p) <= 90))
            self._send({"warm": phrases, "voice": str(sample) if sample else None,
                        "effect": config.VOICE_EFFECT or None})
        except VoiceError:
            return False
        return True

    # --- speaking ---------------------------------------------------------------------

    def ready(self):
        return self.state == "ready"

    def wait_ready(self, timeout):
        """Wait while the model loads. True once it's ready, False if it failed or took too long."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while self.state == "loading" and time.monotonic() < deadline:
                self._changed.wait(deadline - time.monotonic())
            return self.state == "ready"

    def status(self):
        """For the HUD and the settings: "ready", "loading", "off" or "failed: why"."""
        return f"failed: {self.error}" if self.state == "failed" else self.state

    def say(self, text, sample=None, effect=None):
        """Speak and wait until done. False if interrupt() cut it off; VoiceError (with the rest of
        the text) if the engine couldn't."""
        parts = pieces(text)
        if not parts:
            return True
        if not self.ready():
            raise VoiceError(self.error or "the voice is still loading", " ".join(parts))
        if not sample and not self.built_in:
            raise VoiceError("the model has no voice of its own; choose a recording", " ".join(parts))
        with self._changed:
            self._jobs += 1
            job = str(self._jobs)
            self._current = job
        ids = [f"{job}-{i}" for i in range(len(parts))]
        try:
            for rid, part in zip(ids, parts):
                self._send({"id": rid, "job": job, "text": part, "voice": str(sample) if sample else None,
                            "effect": effect or None})
            for i, rid in enumerate(ids):
                result = self._result(rid, job)
                if result is None or result.get("cancelled"):  # interrupted
                    return False
                if "wav" not in result:
                    raise VoiceError(result.get("error") or "no speech came back", " ".join(parts[i:]))
                try:
                    played = self._play(result["wav"])
                finally:
                    Path(result["wav"]).unlink(missing_ok=True)
                if not played or job in self._stopped:
                    return False
            return True
        except VoiceError as exc:
            if not exc.rest:
                exc.rest = " ".join(parts)
            raise
        finally:
            self._forget(job, ids)

    def _result(self, rid, job):
        deadline = time.monotonic() + self._wait_limit
        with self._changed:
            while rid not in self._results:
                if job in self._stopped:
                    return None
                if self.state != "ready":
                    raise VoiceError(self.error or "the voice engine stopped")
                left = deadline - time.monotonic()
                if left <= 0:
                    raise VoiceError("the voice took too long")
                self._changed.wait(left)
            result = self._results.pop(rid)
            if job in self._stopped:  # it came in just as it was stopped
                if result.get("wav"):
                    Path(result["wav"]).unlink(missing_ok=True)
                return None
            return result

    def _forget(self, job, ids):
        try:
            self._send({"cancel": job})  # anything still queued for it
        except VoiceError:
            pass
        with self._changed:
            for rid in ids:
                result = self._results.pop(rid, None)
                if result and result.get("wav"):
                    Path(result["wav"]).unlink(missing_ok=True)
            self._stopped.discard(job)
            if self._current == job:
                self._current = None

    def interrupt(self):
        """Stop the speech that's going on (the sound itself stops with macos.stop_speaking())."""
        with self._changed:
            job = self._current
            if job is None:
                return False
            self._stopped.add(job)
            self._changed.notify_all()
        try:
            self._send({"cancel": job})
        except VoiceError:
            pass
        return True


ENGINE = Engine()


def wanted():
    """Did the settings ask for the human voice, and can this Mac do it?"""
    return config.VOICE_ENGINE == "chatterbox" and installed()


def label():
    """'Voice: human' for the HUD while the human voice is chosen ('' otherwise)."""
    if config.VOICE_ENGINE != "chatterbox":
        return ""
    if not installed():
        return "Voice: Mac (human voice not installed)"
    return {"ready": "Voice: human", "loading": "Voice: human (loading)", "off": "Voice: human (starting)"}.get(
        ENGINE.state, "Voice: Mac (human voice failed)")


def sample_path(value=None):
    """The voice recording in use, or None for Chatterbox's own voice."""
    value = config.VOICE_SAMPLE if value is None else value
    path = Path(value) if value else None
    return path if path and path.is_file() else None


# --- voice samples ----------------------------------------------------------------------

def wav_seconds(path):
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / float(audio.getframerate())


def record_sample(seconds=SAMPLE_SECONDS, record=None):
    """Record the user reading SAMPLE_TEXT; returns the WAV's path. Raises VoiceError."""
    if record is None:
        import sounddevice

        def record(frames):
            audio = sounddevice.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="int16")
            sounddevice.wait()
            return audio.tobytes()
    try:
        data = record(int(seconds * SAMPLE_RATE))
    except Exception as exc:  # no microphone, or no permission
        raise VoiceError(f"I couldn't record: {exc}") from exc
    import array

    levels = array.array("h")
    levels.frombytes(data[: len(data) // 2 * 2])
    if sys.byteorder == "big":
        levels.byteswap()
    loud = sum(1 for value in levels if abs(value) > 1500)
    if loud < SAMPLE_RATE // 2:  # under half a second of anything louder than a whisper
        raise VoiceError("I couldn't hear you. Check the microphone and try again.")
    samples_dir().mkdir(parents=True, exist_ok=True)
    path = samples_dir() / "my-voice.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(data)
    return path


def import_sample(source, run=subprocess.run):
    """Copy a recording (WAV, AIFF, M4A, MP3, ...) as a WAV the model reads; returns its path.
    Uses the Mac's own afconvert. Raises VoiceError."""
    samples_dir().mkdir(parents=True, exist_ok=True)
    path = samples_dir() / "voice-sample.wav"
    try:
        done = run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1", str(source), str(path)],
                   capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VoiceError(f"I couldn't read that file: {exc}") from exc
    if done.returncode != 0 or not path.exists():
        raise VoiceError("I couldn't read that file. Try a WAV, M4A, MP3 or AIFF recording.")
    try:
        seconds = wav_seconds(path)
    except (OSError, EOFError, wave.Error) as exc:
        raise VoiceError(f"I couldn't read that file: {exc}") from exc
    if seconds < 6:
        path.unlink(missing_ok=True)
        raise VoiceError(f"That recording is {seconds:.0f} seconds long; it needs at least 6, ideally 10 to 15.")
    return path
