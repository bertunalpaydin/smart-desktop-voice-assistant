"""Checks the Mac parts Jarvis uses, without changing anything (install.sh runs it).

    python macos/check.py
"""
import os
import shutil
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from jarvis import hotkey, macos, sysinfo  # noqa: E402  (importing jarvis also sets up the certificates)

problems = []


def check(name, test):
    try:
        print(f"  ok    {name}: {test()}")
    except Exception as exc:  # report every problem, then carry on
        print(f"  FAIL  {name}: {exc}")
        problems.append(name)


def packages():
    import importlib
    from importlib.metadata import version

    for module in ("googleapiclient", "numpy", "onnxruntime", "openwakeword", "psutil", "PyQt6.QtWidgets",
                   "sounddevice", "speech_recognition"):
        importlib.import_module(module)  # each one must load, not just be there
    names = ["PyQt6", "numpy", "onnxruntime", "openwakeword", "SpeechRecognition", "sounddevice", "psutil",
             "google-api-python-client"]
    return ", ".join(f"{name} {version(name)}" for name in names)


def wake_model():
    import openwakeword

    paths = [p.replace(".tflite", ".onnx") for p in openwakeword.get_pretrained_model_paths() if "hey_jarvis" in p]
    found = [p for p in paths if os.path.exists(p)]  # Jarvis runs the ONNX version
    if not found:
        raise RuntimeError("not downloaded yet; Jarvis downloads it when it starts")
    return os.path.basename(found[0])


def voices():
    found = macos.voices()
    chosen = macos.pick_voice("", found) or "the system voice"
    return f"{len(found)} English voices, Jarvis will use {chosen}"


def applescript():
    tricky = "-e quit"  # must arrive as text, never as an option
    answer = macos.osascript(["return valueAt(1, argv)"], tricky)
    if answer != tricky:
        raise RuntimeError(f"expected {tricky!r}, got {answer!r}")
    return "runs, and values stay values"


def tools():
    missing = [t for t in ("say", "afplay", "osascript", "open", "pmset", "pgrep", "screencapture") if not shutil.which(t)]
    if missing:
        raise RuntimeError("missing " + ", ".join(missing))
    return "say, afplay, osascript, open, pmset, pgrep, screencapture"


def apps():
    found = macos.installed_apps()
    sample = ", ".join(sorted(found)[:6])
    return f"{len(found)} apps (e.g. {sample})"


def microphones():
    import sounddevice

    inputs = [d["name"] for d in sounddevice.query_devices() if d["max_input_channels"] > 0]
    if not inputs:
        raise RuntimeError("no microphone found")
    return ", ".join(inputs)


def carbon():
    hotkey._Carbon()
    return "loads (the key is only claimed when Jarvis runs)"


def readings():
    sysinfo.snapshot()
    time.sleep(0.5)
    info = sysinfo.snapshot()
    return ", ".join(f"{k} {v}" for k, v in info.items())


def internet():
    with urllib.request.urlopen("https://feeds.bbci.co.uk/news/rss.xml", timeout=15) as response:
        return f"HTTPS works (BBC News answered {response.status})"


def main():
    print(f"  Python {sys.version.split()[0]} at {sys.prefix}")
    for name, test in (("packages", packages), ("\"Hey Jarvis\" model", wake_model), ("macOS tools", tools), ("voices", voices),
                       ("AppleScript", applescript), ("apps", apps), ("microphones", microphones),
                       ("Control+Option+J", carbon), ("system readings", readings), ("internet", internet)):
        check(name, test)
    if problems:
        print(f"\n  {len(problems)} check(s) failed: {', '.join(problems)}")
    return 1 if "packages" in problems else 0


if __name__ == "__main__":
    sys.exit(main())
