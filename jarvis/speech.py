"""Reads text aloud with the voices that come with the computer: System.Speech on
Windows, `say` on a Mac, or on a Mac the human voice (human_voice.py) if it's installed
and chosen in the settings."""
import base64
import json
import re
import sys

from . import config, human_voice, macos, powershell

TIMEOUT = 300  # seconds; a long mail read slowly takes a while

# Same idea as the toast script: the code is fixed and the text only comes in
# as data. Speak() takes plain text, not SSML, so markup in a mail is just read.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
function Decode($name) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($value) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($value)) } else { '' }
}
try {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $voices = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo })

    # The voice named in config.py, else British English, else any English one.
    $wanted = Decode 'MA_VOICE'
    $pick = $null
    if ($wanted) {
        $pick = $voices | Where-Object { $_.Name.IndexOf($wanted, [StringComparison]::OrdinalIgnoreCase) -ge 0 } | Select-Object -First 1
    }
    foreach ($culture in 'en-GB', 'en-*') {
        if (-not $pick) {
            $pick = $voices | Where-Object { $_.Culture.Name -like $culture } | Select-Object -First 1
        }
    }
    if ($pick) {
        try { $synth.SelectVoice($pick.Name) } catch { }  # keep the default voice
    }
    $synth.Rate = [int]$env:MA_RATE

    $info = @{
        voices = @($voices | ForEach-Object { @{ name = $_.Name; culture = $_.Culture.Name } })
        selected = $synth.Voice.Name
    }
    $json = ConvertTo-Json -InputObject $info -Compress -Depth 3
    [Console]::Out.WriteLine([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))

    $text = Decode 'MA_TEXT'
    if ($text) { $synth.Speak($text) }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""

_URL = re.compile(r"<?(?:https?://|www\.)[^\s>]+>?", re.IGNORECASE)
_IMAGE = re.compile(r"\[image: [^\]]*\]", re.IGNORECASE)


class SpeechError(Exception):
    pass


class SpeechStopped(Exception):
    """Reading was cut off on purpose (see stop())."""


_mac_voices = None  # `say -v ?` is asked once; new voices show up after a restart


def _is_windows():
    return sys.platform == "win32"


def speakable(text, limit=None):
    """Text that works read aloud: links become "link", odd characters go, long text is cut."""
    limit = limit or config.READ_MAX_CHARS
    text = _IMAGE.sub(" ", str(text))
    text = _URL.sub("link", text)
    text = "".join(ch for ch in " ".join(text.split()) if ch.isprintable())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.") + ". Message shortened."


def summary(count):
    """What to say before reading out the important mail."""
    return f"You have {count or 'no'} important unread email{'' if count == 1 else 's'}."


def mail_text(mail, body):
    return f"Email from {mail.sender}. Subject: {mail.subject}. {body}"


def _rate(value):
    return max(-10, min(10, int(value)))


def _human(text, engine, sample, effect=None):
    """Speak with the human voice if it's chosen and ready. Returns the text it couldn't say
    (all of it if it isn't ready, the rest if it failed half-way), "" when all was said."""
    if (config.VOICE_ENGINE if engine is None else engine) != "chatterbox" or not human_voice.installed():
        return text
    human_voice.ENGINE.start()  # loads in the background the first time; the Mac's voice meanwhile
    try:
        effect = config.VOICE_EFFECT if effect is None else effect
        if not human_voice.ENGINE.say(text, human_voice.sample_path(sample), effect):
            raise SpeechStopped()
        return ""
    except human_voice.VoiceError as exc:
        if human_voice.ENGINE.ready():  # a real failure, not just loading
            print(f"(human voice failed: {exc}; using the Mac's voice)")
        return exc.rest


def _run_mac(text, voice=None, rate=None, engine=None, sample=None, effect=None):
    global _mac_voices
    try:
        if _mac_voices is None:
            _mac_voices = macos.voices()
        selected = macos.pick_voice(config.VOICE if voice is None else voice, _mac_voices)
        rest = _human(text, engine, sample, effect) if text else ""
        if rest and not macos.say(rest, selected, _rate(config.SPEECH_RATE if rate is None else rate)):
            raise SpeechStopped()
    except macos.MacError as exc:
        raise SpeechError(str(exc)) from exc
    return {"voices": _mac_voices, "selected": selected}


def _run(text, voice=None, rate=None, engine=None, sample=None, effect=None):
    if macos.available():
        return _run_mac(text, voice, rate, engine, sample, effect)
    if not _is_windows():
        raise SpeechError("reading aloud only works on Windows and macOS")
    env = {
        "MA_TEXT": powershell.b64(text),
        "MA_VOICE": powershell.b64(config.VOICE if voice is None else voice),
        "MA_RATE": str(_rate(config.SPEECH_RATE if rate is None else rate)),
    }
    try:
        output = powershell.run(_SCRIPT, env, timeout=TIMEOUT, tag="speech")
    except powershell.PowerShellStopped:
        raise
    except powershell.PowerShellError as exc:
        raise SpeechError(str(exc)) from exc
    try:
        info = json.loads(base64.b64decode(output.split()[-1]))
    except (ValueError, IndexError) as exc:
        raise SpeechError("unexpected reply from PowerShell") from exc
    if not isinstance(info, dict):
        raise SpeechError("unexpected reply from PowerShell")
    return info


def speak(text, voice=None, rate=None, engine=None, sample=None, effect=None):
    """Read text aloud (voice, rate, engine and voice sample default to the settings). Where that
    can't be done, print it instead. Returns True only if it was spoken."""
    text = speakable(text)
    if not text:
        return False
    try:
        _run(text, voice, rate, engine, sample, effect)
        return True
    except (powershell.PowerShellStopped, SpeechStopped):
        return False  # stopped on purpose, nothing to report
    except SpeechError as exc:
        if _is_windows() or macos.available():
            print(f"(speech failed: {exc})")
    print(f"[speech] {text}")
    return False


def voices():
    """(installed voices, name of the voice in use). Raises SpeechError."""
    info = _run("")
    found = info.get("voices") or []
    if isinstance(found, dict):
        found = [found]
    return found, info.get("selected", "")


def stop():
    """Stop whatever is being read out right now."""
    if macos.available():
        stopped = human_voice.ENGINE.interrupt()
        return macos.stop_speaking() or stopped
    return powershell.stop("speech")
