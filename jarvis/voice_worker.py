"""Jarvis's human voice: Resemble AI's Chatterbox (Turbo, or the smaller and faster Nano),
turning text into speech.

It runs as its own process with the Python of the voice environment (macos/install_voice.sh),
so PyTorch and the model never touch Jarvis's own environment. Jarvis (human_voice.py) talks to
it with one JSON object per line:

  to it:    {"id": "3-0", "job": "3", "text": "Good evening.", "voice": "/path/to/sample.wav" or null,
             "effect": "helmet" or null}
            {"cancel": "3"}       skip what is left of job 3
            {"warm": ["Okay.", "Done."], "voice": null, "effect": null}   make these while idle
            {"quit": true}        (or closing stdin)
  from it:  {"event": "ready", "device": "mps", "load_seconds": 9.8}
            {"event": "error", "message": "..."}
            {"id": "3-0", "wav": "/path/3-0.wav", "audio_seconds": 1.6, "took": 0.9, "cached": false}
            {"id": "3-1", "cancelled": true}   or   {"id": "3-1", "error": "..."}

Everything it says is kept in a cache (by model, voice and text), so what Jarvis says often
comes out at once. Before saying it's ready, it says one word to itself: the first sentence on
the Mac's graphics chip is slow while the chip prepares its programs.

Nothing is downloaded here (Hugging Face is set to offline): the model files come from the
installer, which checks them. Weights are read as safetensors and the built-in voice with
torch.load(weights_only=True), so a model file can only hold numbers, never code.

    python voice_worker.py --model DIR --out DIR [--cache DIR]   serve Jarvis
    python voice_worker.py --check                               can the packages load?
    python voice_worker.py --model DIR --benchmark FILE.wav      the fastest way on this Mac, and a sample
"""
import argparse
import collections
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

SELFTEST_TEXT = "Good evening. This is my new voice. All systems are online."
BENCHMARK_TEXTS = ("Right away.", "Tomorrow in Bristol: overcast skies, between 8 and 13 degrees.")
CACHE_LIMIT = 400_000_000  # bytes of remembered speech; the oldest goes first

# Nano is Turbo with GPT-2 small instead of GPT-2 medium inside (Chatterbox 0.1.7 only knows medium)
GPT2_SMALL = {
    "activation_function": "gelu_new", "architectures": ["GPT2LMHeadModel"], "attn_pdrop": 0.1,
    "bos_token_id": 50256, "embd_pdrop": 0.1, "eos_token_id": 50256, "initializer_range": 0.02,
    "layer_norm_epsilon": 1e-05, "model_type": "gpt2", "n_ctx": 8196, "n_embd": 768, "hidden_size": 768,
    "n_head": 12, "n_layer": 12, "n_positions": 8196, "n_special": 0, "predict_special_tokens": True,
    "resid_pdrop": 0.1, "summary_activation": None, "summary_first_dropout": 0.1, "summary_proj_to_labels": True,
    "summary_type": "cls_index", "summary_use_proj": True,
    "task_specific_params": {"text-generation": {"do_sample": True, "max_length": 50}}, "vocab_size": 50276,
}
T3_FILES = {"t3_nano_v1.safetensors": "GPT2_small", "t3_turbo_v1.safetensors": "GPT2_medium"}


def helmet(samples, rate):
    """The "deep helmet" effect: the voice a little lower, the short metallic ring of a mask, a touch
    of grit and a small hard room. A sound effect made here, not anybody's voice."""
    import librosa
    import numpy
    from scipy import signal

    voice = librosa.effects.pitch_shift(numpy.asarray(samples, dtype="float32"), sr=rate, n_steps=-4)
    ring = max(1, int(rate * 0.0035))
    masked = voice.copy()
    masked[ring:] += 0.45 * voice[:-ring]
    low, high = signal.butter(2, [80 / (rate / 2), min(5000, 0.45 * rate) / (rate / 2)], btype="band")
    shaped = signal.lfilter(low, high, masked).astype("float32")
    shaped = numpy.tanh(2.0 * shaped) / numpy.tanh(2.0)
    out = shaped.copy()
    for milliseconds, gain in ((19, 0.22), (37, 0.14), (61, 0.07)):
        delay = int(rate * milliseconds / 1000)
        out[delay:] += gain * shaped[:-delay]
    peak = float(numpy.max(numpy.abs(out))) if len(out) else 0.0
    return (out * (0.9 / peak if peak else 1.0)).astype("float32")


EFFECTS = {"helmet": helmet}


def _quiet_environment():
    os.environ["HF_HUB_OFFLINE"] = "1"  # the installer downloads; this process never goes online
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("TQDM_DISABLE", "1")  # no progress bars for every sentence
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # the odd operation the Mac's GPU lacks: CPU


def check():
    """Can everything load? Perth (the watermark) quietly turns into None when it can't."""
    import importlib

    _quiet_environment()
    import perth
    import torch

    if not hasattr(importlib.import_module("chatterbox.tts_turbo"), "ChatterboxTurboTTS"):
        raise RuntimeError("this Chatterbox has no Turbo model")
    if perth.PerthImplicitWatermarker is None:
        raise RuntimeError("the Perth watermarker didn't load")
    return {"torch": torch.__version__, "mps": bool(torch.backends.mps.is_available())}


class Engine:
    """The model, with the voices worked out so far (a voice sample takes a moment the first time)
    and the speech it made before."""

    def __init__(self, model, device, cache=None, model_id=""):
        self.model, self.device, self.rate = model, device, model.sr
        self.built_in = model.conds
        self._samples = {}
        self.cache, self.model_id = (Path(cache) if cache else None), model_id
        normalise = getattr(model, "norm_loudness", None)
        if normalise is not None:
            # With NumPy 2, its loudness step turns the recording into 64-bit floats, which the
            # tokenizer's 32-bit filters then refuse ("expected scalar type Double but found Float")
            model.norm_loudness = lambda wav, sr, *args, **kwargs: normalise(wav, sr, *args, **kwargs).astype("float32")

    def _use(self, sample):
        if not sample:
            self.model.conds = self.built_in
            return
        path = Path(sample)
        key = (str(path), path.stat().st_mtime_ns)
        if key not in self._samples:
            self.model.prepare_conditionals(str(path))  # sets model.conds
            self._samples = {key: self.model.conds}  # one sample at a time is plenty
        self.model.conds = self._samples[key]

    def speak(self, text, sample=None):
        """The text as audio: a sequence of floats between -1 and 1 at self.rate per second."""
        import torch

        self._use(sample)
        if self.model.conds is None:
            raise RuntimeError("this model has no voice of its own; choose a recording in Jarvis's settings")
        with torch.inference_mode():
            audio = self.model.generate(text)
        return audio.squeeze(0).cpu().numpy()

    def _cached(self, text, sample, effect=None):
        if self.cache is None:
            return None
        voice = "built-in"
        if sample:
            info = Path(sample).stat()
            voice = f"{sample}:{info.st_mtime_ns}:{info.st_size}"
        key = hashlib.sha256(f"{self.model_id}\n{voice}\n{effect or ''}\n{text}".encode("utf-8")).hexdigest()[:40]
        return self.cache / f"{key}.wav"

    def _audio(self, text, sample, effect):
        audio = self.speak(text, sample)
        return EFFECTS[effect](audio, self.rate) if effect in EFFECTS else audio

    def speak_to(self, path, text, sample=None, effect=None):
        """Say the text into a WAV file, from the cache if it was said before. (seconds, cached?)"""
        remembered = self._cached(text, sample, effect)
        if remembered is not None and remembered.exists():
            shutil.copyfile(remembered, path)
            os.utime(remembered)  # recently used: kept longest
            return wav_seconds(path), True
        seconds = write_wav(path, self._audio(text, sample, effect), self.rate)
        if remembered is not None:
            self.cache.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, remembered)
            self._tidy_cache()
        return seconds, False

    def remember(self, text, sample=None, effect=None):
        """Make the speech for later, if it isn't in the cache yet (while there's nothing else to do)."""
        remembered = self._cached(text, sample, effect)
        if remembered is None or remembered.exists():
            return False
        self.cache.mkdir(parents=True, exist_ok=True)
        part = remembered.with_suffix(".part")
        write_wav(part, self._audio(text, sample, effect), self.rate)
        part.replace(remembered)
        self._tidy_cache()
        return True

    def _tidy_cache(self, limit=CACHE_LIMIT):
        files = sorted(self.cache.glob("*.wav"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        while files and total > limit:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink(missing_ok=True)

    def warm_up(self):
        """The first sentence on the Mac's graphics chip is slow; say a word now rather than in a reply."""
        if self.built_in is not None:
            audio = self.speak("Ready.")
            try:
                helmet(audio, self.rate)  # the effect's first use is slow too (numba compiles it)
            except Exception:  # speaking doesn't need it
                pass


def _performance_cores():
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.perflevel0.physicalcpu"], capture_output=True, text=True,
                                  timeout=5).stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _load_model(model_dir, device):
    """ChatterboxTurboTTS.from_local, but also for Nano (whichever T3 file the folder has)."""
    import torch
    from chatterbox.models.s3gen import S3Gen
    from chatterbox.models.t3 import T3, llama_configs
    from chatterbox.models.t3.modules.t3_config import T3Config
    from chatterbox.models.voice_encoder import VoiceEncoder
    from chatterbox.tts_turbo import ChatterboxTurboTTS, Conditionals
    from safetensors.torch import load_file
    from transformers import AutoTokenizer

    folder = Path(model_dir)
    t3_file = next((name for name in T3_FILES if (folder / name).exists()), None)
    if t3_file is None:
        raise FileNotFoundError(f"no {' or '.join(T3_FILES)} in {folder}")
    llama_configs.LLAMA_CONFIGS.setdefault("GPT2_small", GPT2_SMALL)

    ve = VoiceEncoder()
    ve.load_state_dict(load_file(folder / "ve.safetensors"))
    ve.to(device).eval()
    hp = T3Config(text_tokens_dict_size=50276)
    hp.llama_config_name = T3_FILES[t3_file]
    hp.speech_tokens_dict_size = 6563
    hp.input_pos_emb = None
    hp.speech_cond_prompt_len = 375
    hp.use_perceiver_resampler = False
    hp.emotion_adv = False
    t3 = T3(hp)
    state = load_file(folder / t3_file)
    if "model" in state.keys():
        state = state["model"][0]
    t3.load_state_dict(state)
    del t3.tfmr.wte
    t3.to(device).eval()
    s3gen = S3Gen(meanflow=True)
    s3gen.load_state_dict(load_file(folder / "s3gen_meanflow.safetensors"), strict=True)
    s3gen.to(device).eval()
    tokenizer = AutoTokenizer.from_pretrained(folder)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    conds = None
    if (folder / "conds.pt").exists():
        conds = Conditionals.load(folder / "conds.pt", map_location=torch.device("cpu")).to(device)
    return ChatterboxTurboTTS(t3, s3gen, ve, tokenizer, device, conds=conds)


def load_engine(model_dir, device=None, cache=None):
    _quiet_environment()
    import torch

    device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
    if device == "cpu" and _performance_cores():
        torch.set_num_threads(_performance_cores())  # the fast cores; the efficiency ones only slow it down
    try:
        model_id = json.loads((Path(model_dir) / "source.json").read_text(encoding="utf-8"))["revision"]
    except (OSError, ValueError, KeyError, TypeError):
        model_id = Path(model_dir).name
    return Engine(_load_model(model_dir, device), device, cache, model_id)


def wav_seconds(path):
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / float(audio.getframerate())


def write_wav(path, samples, rate):
    """16-bit mono WAV from floats between -1 and 1."""
    import numpy

    pcm = (numpy.clip(numpy.asarray(samples, dtype="float32"), -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(int(rate))
        out.writeframes(pcm.tobytes())
    return len(pcm) / rate


def serve(load, out_dir, stdin, send):
    """Answer requests until stdin closes. `load()` returns the Engine; `send(dict)` writes a line."""
    started = time.monotonic()
    try:
        engine = load()
        engine.warm_up()
    except Exception as exc:  # anything: a missing file, an old Mac, too little memory
        send({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        return 1
    send({"event": "ready", "device": engine.device, "load_seconds": round(time.monotonic() - started, 1),
          "built_in": engine.built_in is not None})

    requests, cancelled = queue.Queue(), set()

    def read():
        for line in stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if "cancel" in message:
                cancelled.add(str(message["cancel"]))  # read at once, even while a sentence is being made
            else:
                requests.put(message)
        requests.put(None)  # stdin closed: Jarvis has gone

    threading.Thread(target=read, daemon=True).start()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    later = collections.deque()  # phrases to make while idle
    while True:
        try:
            message = requests.get(timeout=0.05 if later else None)
        except queue.Empty:
            text, sample, effect = later.popleft()
            try:
                engine.remember(text, sample, effect)
            except Exception:  # a phrase that can't be made now is made when it's needed
                pass
            continue
        if message is None or message.get("quit"):
            return 0
        if "warm" in message:
            phrases = message["warm"] if isinstance(message["warm"], list) else []
            later.clear()
            later.extend((str(text).strip(), message.get("voice") or None, message.get("effect") or None)
                         for text in phrases if str(text).strip())
            continue
        rid, job = str(message.get("id", "")), str(message.get("job", ""))
        if job and job in cancelled:
            send({"id": rid, "cancelled": True})
            continue
        text = str(message.get("text", "")).strip()
        if not text:
            send({"id": rid, "error": "no text"})
            continue
        try:
            began = time.monotonic()
            path = out_dir / (re.sub(r"[^\w-]", "_", rid)[:40] + ".wav")
            seconds, cached = engine.speak_to(path, text, message.get("voice") or None, message.get("effect") or None)
            send({"id": rid, "wav": str(path), "audio_seconds": round(seconds, 2),
                  "took": round(time.monotonic() - began, 2), "cached": cached})
        except Exception as exc:  # one bad sentence shouldn't end the voice
            send({"id": rid, "error": f"{type(exc).__name__}: {exc}"})


def _protocol_output():
    """A private copy of stdout for the messages; everything printed by the libraries goes to stderr."""
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr

    def send(message):
        channel.write(json.dumps(message) + "\n")
        channel.flush()

    return send


def benchmark(model_dir, out_file, devices=None, load=None):
    """Try the graphics chip and the processor; the faster one wins. Also says SELFTEST_TEXT into
    out_file with it. Returns the message for the installer."""
    load = load or load_engine
    if devices is None:
        import torch

        devices = (["mps"] if torch.backends.mps.is_available() else []) + ["cpu"]
    results = {}
    for device in devices:
        try:
            started = time.monotonic()
            engine = load(model_dir, device)
            result = {"load_seconds": round(time.monotonic() - started, 1), "built_in": engine.built_in is not None}
            if engine.built_in is not None:
                engine.warm_up()
                timings = []
                for text in BENCHMARK_TEXTS:
                    began = time.monotonic()
                    audio = engine.speak(text)
                    timings.append((len(audio) / engine.rate, time.monotonic() - began))
                spoken, took = sum(a for a, _ in timings), sum(t for _, t in timings)
                result.update(first_piece=round(timings[0][1], 2), audio_seconds=round(spoken, 2),
                              took=round(took, 2), speed=round(spoken / max(took, 1e-6), 2))
                write_wav(f"{out_file}.{device}.wav", engine.speak(SELFTEST_TEXT), engine.rate)
            results[device] = result
            del engine
        except Exception as exc:
            results[device] = {"error": f"{type(exc).__name__}: {exc}"}
        if device == "mps":
            try:
                import torch

                torch.mps.empty_cache()
            except Exception:
                pass
    working = {d: r for d, r in results.items() if "error" not in r}
    if not working:
        return {"event": "error", "message": "; ".join(f"{d}: {r['error']}" for d, r in results.items())}
    best = max(working, key=lambda d: working[d].get("speed", 0))
    sample = Path(f"{out_file}.{best}.wav")
    if sample.exists():
        sample.replace(out_file)
    for device in results:
        Path(f"{out_file}.{device}.wav").unlink(missing_ok=True)
    return {"event": "ok", "device": best, **working[best], "devices": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jarvis's human voice (Chatterbox)")
    parser.add_argument("--model", help="the folder with the model files")
    parser.add_argument("--out", help="where to put the spoken sentences")
    parser.add_argument("--cache", help="where to keep speech for next time")
    parser.add_argument("--device", choices=("mps", "cpu"), help="default: the Mac's GPU if there is one")
    parser.add_argument("--check", action="store_true", help="only check that the packages load")
    parser.add_argument("--benchmark", metavar="FILE", help="find the faster device; say a sentence into FILE")
    args = parser.parse_args(argv)
    _quiet_environment()  # before anything imports tqdm or Hugging Face's libraries
    send = _protocol_output()
    if args.check:
        try:
            send({"event": "ok", **check()})
            return 0
        except Exception as exc:
            send({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
            return 1
    if not args.model:
        parser.error("--model is needed")
    if args.benchmark:
        result = benchmark(args.model, args.benchmark, [args.device] if args.device else None)
        send(result)
        return 0 if result["event"] == "ok" else 1
    if not args.out:
        parser.error("--out is needed")
    return serve(lambda: load_engine(args.model, args.device, args.cache), args.out, sys.stdin, send)


if __name__ == "__main__":
    sys.exit(main())
