"""Alarm and listening sounds: Windows' own sound files on Windows, afplay on a Mac."""
import math
import os
import struct
import sys
import wave
from pathlib import Path

from . import config, macos

ALARM_FILE = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Media" / "Alarm01.wav"
MAC_CHIME = Path("/System/Library/Sounds/Glass.aiff")


def _is_windows():
    return sys.platform == "win32"


def _winsound():
    import winsound  # only exists on Windows

    return winsound


def play_alarm():
    """Ring until stop() is called."""
    if macos.available():
        macos.loop(_alarm_file())
        return
    if not _is_windows():
        return
    ws = _winsound()
    if ALARM_FILE.exists():
        ws.PlaySound(str(ALARM_FILE), ws.SND_FILENAME | ws.SND_ASYNC | ws.SND_LOOP)
    else:
        ws.PlaySound("SystemExclamation", ws.SND_ALIAS | ws.SND_ASYNC | ws.SND_LOOP)


def stop():
    if macos.available():
        macos.stop_sounds()
    elif _is_windows():
        _winsound().PlaySound(None, 0)


def chime():
    """A short sound: Jarvis is listening, or a reminder."""
    if macos.available():
        macos.play(MAC_CHIME)
    elif _is_windows():
        ws = _winsound()
        ws.MessageBeep(ws.MB_ICONASTERISK)


def _tones(path, notes, volume=9000, rate=22050):
    """Write notes ((frequency, seconds), ...; frequency 0 is a pause) as a WAV file, once."""
    if not path.exists():
        samples = []
        for frequency, length in notes:
            count = int(rate * length)
            for i in range(count):
                fade = min(1.0, i / (rate * 0.01), (count - i) / (rate * 0.02))  # no clicks
                samples.append(int(volume * fade * math.sin(2 * math.pi * frequency * i / rate)))
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(rate)
            file.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def _listening_file():
    """A short, soft two-note chime, made once and kept with Jarvis's other files."""
    return _tones(config.APP_DIR / "listening.wav", ((880, 0.07), (1320, 0.09)))


def _alarm_file():
    """An alarm clock's beep-beep-beep, then a pause; played over and over."""
    beeps = ((1046, 0.12), (0, 0.08)) * 3 + ((0, 0.5),)
    return _tones(config.APP_DIR / "alarm.wav", beeps, volume=14000)


def listening():
    """Jarvis is listening now."""
    if macos.available():
        macos.play(_listening_file())
    elif _is_windows():
        ws = _winsound()
        ws.PlaySound(str(_listening_file()), ws.SND_FILENAME | ws.SND_ASYNC | ws.SND_NODEFAULT)
