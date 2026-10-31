"""How fast is the human voice on this Mac? Says a few sentences on the graphics chip and on the
processor, and times the two halves of the model (text to speech tokens, tokens to sound).
Run it with the voice environment's Python:

    "$HOME/Library/Application Support/Jarvis/voice-engine/venv/bin/python" -I macos/voice/benchmark.py

It writes what it finds to macos/voice_benchmark.txt as well.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "jarvis"))

import voice_worker  # noqa: E402

MODEL = Path.home() / "Library" / "Application Support" / "Jarvis" / "voice-engine" / "model"
SENTENCES = [
    "It's 9 PM.",
    "Tomorrow in Bristol: overcast skies, between 8 and 13 degrees, with a 20 percent chance of rain.",
    "Good evening, sir. All systems are online.",
]


def timed(owner, name, totals):
    original = getattr(owner, name)

    def wrapper(*args, **kwargs):
        started = time.monotonic()
        try:
            return original(*args, **kwargs)
        finally:
            totals[name] = totals.get(name, 0.0) + time.monotonic() - started

    setattr(owner, name, wrapper)


def run(device, rounds=2):
    import torch

    started = time.monotonic()
    engine = voice_worker.load_engine(str(MODEL), device)
    result = {"device": device, "load_seconds": round(time.monotonic() - started, 1), "sentences": []}
    for round_number in range(rounds):
        for text in SENTENCES:
            parts = {}
            timed(engine.model.t3, "inference_turbo", parts)
            timed(engine.model.s3gen, "inference", parts)
            began = time.monotonic()
            audio = engine.speak(text)
            took = time.monotonic() - began
            for name in ("inference_turbo", "inference"):  # undo the wrapping for the next sentence
                delattr(engine.model.t3 if name == "inference_turbo" else engine.model.s3gen, name)
            seconds = len(audio) / engine.rate
            row = {"round": round_number + 1, "chars": len(text), "audio": round(seconds, 1), "took": round(took, 1),
                   "tokens": round(parts.get("inference_turbo", 0), 1), "sound": round(parts.get("inference", 0), 1)}
            result["sentences"].append(row)
            print(f"  {device}: {seconds:4.1f} s of speech in {took:4.1f} s "
                  f"(text to tokens {row['tokens']} s, tokens to sound {row['sound']} s)", flush=True)
    del engine
    if device == "mps":
        torch.mps.empty_cache()
    return result


def main():
    import platform

    import torch

    voice_worker._quiet_environment()
    report = {"mac": platform.mac_ver()[0], "machine": platform.machine(), "torch": torch.__version__,
              "threads": torch.get_num_threads(), "runs": []}
    for device in ("mps", "cpu"):
        if device == "mps" and not torch.backends.mps.is_available():
            continue
        print(f"On the {'graphics chip' if device == 'mps' else 'processor'}:", flush=True)
        try:
            report["runs"].append(run(device))
        except Exception as exc:
            print(f"  failed: {type(exc).__name__}: {exc}")
            report["runs"].append({"device": device, "error": f"{type(exc).__name__}: {exc}"})
    out = HERE.parent / "voice_benchmark.txt"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
