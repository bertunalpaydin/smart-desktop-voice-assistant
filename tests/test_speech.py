import base64
import json
import subprocess

from fakes import use_run

import pytest

from jarvis import config, speech

INFO = {
    "voices": [{"name": "Microsoft Hazel Desktop", "culture": "en-GB"}],
    "selected": "Microsoft Hazel Desktop",
}


def decode(value):
    return base64.b64decode(value).decode("utf-8")


def reply(info):
    """What the script prints: its info as base64 JSON."""
    return base64.b64encode(json.dumps(info).encode("utf-8")).decode("ascii") + "\r\n"


@pytest.fixture
def windows(monkeypatch):
    """Pretend to be on Windows. Call it to set what PowerShell does; returns the calls."""
    monkeypatch.setattr(speech, "_is_windows", lambda: True)

    def set_result(stdout="", returncode=0, stderr=""):
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, returncode, stdout, stderr)

        use_run(monkeypatch, run)
        return calls

    return set_result


def test_links_are_read_as_the_word_link():
    text = "See https://example.com/a?b=1 or www.uwe.ac.uk today"
    assert speech.speakable(text) == "See link or link today"


def test_links_in_angle_brackets_and_image_labels_are_tidied():
    text = "[image: Google] New sign-in. Check activity <https://accounts.google.com/x?y=1> now"
    assert speech.speakable(text) == "New sign-in. Check activity link now"


def test_whitespace_and_invisible_characters_are_cleaned():
    assert speech.speakable("Hi\n\n  there\u200b\u00a0friend\x07") == "Hi there friend"


def test_long_text_is_cut_at_a_word(monkeypatch):
    monkeypatch.setattr(config, "READ_MAX_CHARS", 20)
    assert speech.speakable("one two three four five six seven") == "one two three four. Message shortened."


def test_off_windows_the_text_is_printed(monkeypatch, capsys):
    monkeypatch.setattr(speech, "_is_windows", lambda: False)
    assert speech.speak("Hello") is False
    assert "[speech] Hello" in capsys.readouterr().out


def test_text_voice_and_rate_go_in_as_data(windows, monkeypatch):
    monkeypatch.setattr(config, "VOICE", "Hazel")
    monkeypatch.setattr(config, "SPEECH_RATE", 99)
    calls = windows(stdout=reply(INFO))
    hostile = 'Re: $(calc) "quoted" <b>'
    assert speech.speak(hostile) is True

    args, kwargs = calls[0]
    script = base64.b64decode(args[args.index("-EncodedCommand") + 1]).decode("utf-16-le")
    assert script == speech._SCRIPT
    env = kwargs["env"]
    assert decode(env["MA_TEXT"]) == hostile
    assert decode(env["MA_VOICE"]) == "Hazel"
    assert env["MA_RATE"] == "10"  # kept within -10..10


def test_failed_speech_falls_back_to_printing(windows, capsys):
    windows(returncode=1, stderr="No audio device\n")
    assert speech.speak("Hello") is False
    out = capsys.readouterr().out
    assert "(speech failed: No audio device)" in out
    assert "[speech] Hello" in out


def test_empty_text_is_not_sent(windows):
    calls = windows(stdout=reply(INFO))
    assert speech.speak(" \n ") is False
    assert calls == []


def test_voices_come_from_the_script_output(windows):
    calls = windows(stdout=reply(INFO))
    installed, selected = speech.voices()
    assert installed == INFO["voices"]
    assert selected == "Microsoft Hazel Desktop"
    assert decode(calls[0][1]["env"]["MA_TEXT"]) == ""  # nothing is spoken


@pytest.mark.parametrize("stdout", ["", "not base64 !!", reply(["a list"])])
def test_unexpected_output_is_an_error(windows, stdout):
    windows(stdout=stdout)
    with pytest.raises(speech.SpeechError):
        speech.voices()


def test_voices_need_windows(monkeypatch):
    monkeypatch.setattr(speech, "_is_windows", lambda: False)
    with pytest.raises(speech.SpeechError):
        speech.voices()


def test_a_single_voice_sent_as_an_object_still_works(windows):
    voice = {"name": "Microsoft Hazel Desktop", "culture": "en-GB"}
    windows(stdout=reply({"voices": voice, "selected": voice["name"]}))
    assert speech.voices() == ([voice], "Microsoft Hazel Desktop")


def test_voice_and_rate_can_be_given_for_one_sentence(windows, monkeypatch):
    monkeypatch.setattr(config, "VOICE", "Hazel")
    calls = windows(stdout=reply(INFO))
    assert speech.speak("Testing", voice="Zira", rate=-3) is True
    env = calls[0][1]["env"]
    assert decode(env["MA_VOICE"]) == "Zira"
    assert env["MA_RATE"] == "-3"


def test_summary_sentences():
    assert speech.summary(0) == "You have no important unread emails."
    assert speech.summary(1) == "You have 1 important unread email."
    assert speech.summary(3) == "You have 3 important unread emails."
