import base64
import os
import subprocess
import threading

import pytest
from fakes import use_run

from jarvis import powershell


def fake_run(stdout="", returncode=0, stderr="", raises=None):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if raises:
            raise raises
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run, calls


def test_runs_the_script_with_the_extra_env(monkeypatch):
    run, calls = fake_run(stdout="done\n")
    made = use_run(monkeypatch, run)
    assert powershell.run("Write-Output 'x'", {"MA_X": "1"}, timeout=5) == "done\n"

    args, kwargs = calls[0]
    assert args[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    assert base64.b64decode(args[4]).decode("utf-16-le") == "Write-Output 'x'"
    assert kwargs["env"]["MA_X"] == "1"
    assert set(os.environ) <= set(kwargs["env"])
    assert made[0].timeout == 5


def test_failure_raises_with_the_error_text(monkeypatch):
    run, _ = fake_run(returncode=1, stderr="boom\n")
    use_run(monkeypatch, run)
    with pytest.raises(powershell.PowerShellError, match="boom"):
        powershell.run("x", {}, timeout=5)


def test_missing_powershell_raises(monkeypatch):
    run, _ = fake_run(raises=FileNotFoundError("powershell.exe"))
    use_run(monkeypatch, run)
    with pytest.raises(powershell.PowerShellError):
        powershell.run("x", {}, timeout=5)


def test_a_timeout_kills_the_script_and_raises(monkeypatch):
    run, _ = fake_run(raises=subprocess.TimeoutExpired("powershell.exe", 5))
    made = use_run(monkeypatch, run)
    with pytest.raises(powershell.PowerShellError, match="no answer"):
        powershell.run("x", {}, timeout=5)
    assert made[0].killed


class BlockingProcess:
    """A script that runs until it is killed."""

    def __init__(self):
        self.returncode = None
        self.finished = threading.Event()

    def communicate(self, timeout=None):
        self.finished.wait(5)
        return "", ""

    def kill(self):
        self.returncode = 1
        self.finished.set()


def test_a_tagged_script_can_be_stopped_from_another_thread(monkeypatch):
    process = BlockingProcess()
    monkeypatch.setattr(powershell.subprocess, "Popen", lambda args, **kwargs: process)
    errors = []

    def speak():
        try:
            powershell.run("x", {}, timeout=30, tag="speech")
        except powershell.PowerShellError as exc:
            errors.append(exc)

    thread = threading.Thread(target=speak)
    thread.start()
    for _ in range(100):
        if "speech" in powershell._running:
            break
        threading.Event().wait(0.01)
    assert powershell.stop("speech") is True
    thread.join(5)
    assert isinstance(errors[0], powershell.PowerShellStopped)
    assert powershell.stop("speech") is False  # nothing running any more


def test_b64_keeps_any_text():
    assert base64.b64decode(powershell.b64("Café 🎉")).decode("utf-8") == "Café 🎉"
