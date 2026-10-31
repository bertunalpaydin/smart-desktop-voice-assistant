import base64
import subprocess

from fakes import use_run

from jarvis import notifier


def fake_run(returncode=0, stderr="", raises=None):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if raises:
            raise raises
        return subprocess.CompletedProcess(args, returncode, "", stderr)

    return run, calls


def windows(monkeypatch, run):
    monkeypatch.setattr(notifier, "_is_windows", lambda: True)
    use_run(monkeypatch, run)


def test_not_windows_prints_instead(monkeypatch, capsys):
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    assert notifier.notify("Ali", "Hello") is False
    assert "[notification] Ali - Hello" in capsys.readouterr().out


def test_windows_passes_text_as_base64_and_never_inside_the_script(monkeypatch):
    run, calls = fake_run()
    windows(monkeypatch, run)
    hostile = 'Re: $(calc) `x` "quoted" <b>&amp; ]]>'
    assert notifier.notify(hostile, "Café – résumé 🎉") is True

    args, kwargs = calls[0]
    assert args[0] == "powershell.exe"
    script = base64.b64decode(args[args.index("-EncodedCommand") + 1]).decode("utf-16-le")
    assert script == notifier._SCRIPT
    assert "calc" not in script
    env = kwargs["env"]
    assert base64.b64decode(env["MA_TITLE"]).decode("utf-8") == hostile
    assert base64.b64decode(env["MA_BODY"]).decode("utf-8") == "Café – résumé 🎉"


def test_failed_toast_falls_back_and_says_why(monkeypatch, capsys):
    run, _ = fake_run(returncode=1, stderr="boom\n")
    windows(monkeypatch, run)
    assert notifier.notify("Ali", "Hello") is False
    out = capsys.readouterr().out
    assert "toast failed: boom" in out
    assert "[notification] Ali - Hello" in out


def test_missing_powershell_falls_back(monkeypatch, capsys):
    run, _ = fake_run(raises=FileNotFoundError("powershell.exe"))
    windows(monkeypatch, run)
    assert notifier.notify("Ali", "Hello") is False
    assert "toast failed" in capsys.readouterr().out


def test_text_is_cleaned_and_shortened():
    assert notifier._clean("a\x00b\n  c\u00a0d\x07", 50) == "ab c d"
    cut = notifier._clean("x" * 300, 50)
    assert len(cut) == 50 and cut.endswith("…")


def test_empty_text_gets_defaults(monkeypatch, capsys):
    monkeypatch.setattr(notifier, "_is_windows", lambda: False)
    notifier.notify("", "")
    assert "[notification] Jarvis - (no subject)" in capsys.readouterr().out
