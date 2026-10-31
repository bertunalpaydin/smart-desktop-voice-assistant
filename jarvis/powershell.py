"""Runs a fixed PowerShell script. Values reach the script through environment
variables, never through its text, so words from a mail can't run as code."""
import base64
import os
import subprocess
import threading


class PowerShellError(Exception):
    pass


class PowerShellStopped(PowerShellError):
    """The script was stopped on purpose (see stop())."""


_lock = threading.Lock()
_running = {}  # tag -> process, for scripts that may be stopped


def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def run(script, env, timeout, tag=None):
    """Run `script` in Windows PowerShell with `env` added. Returns what it printed.

    A script started with a tag can be stopped from another thread with stop(tag)."""
    command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        process = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
            env=dict(os.environ, **env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise PowerShellError(str(exc)) from exc
    if tag:
        with _lock:
            _running[tag] = process
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise PowerShellError(f"no answer after {timeout} seconds") from exc
    finally:
        if tag:
            with _lock:
                if _running.get(tag) is process:
                    del _running[tag]
    if process.returncode != 0:
        if getattr(process, "stopped_on_purpose", False):
            raise PowerShellStopped("stopped")
        raise PowerShellError(stderr.strip() or f"exit code {process.returncode}")
    return stdout


def stop(tag):
    """Stop the running script with this tag. Returns True if there was one."""
    with _lock:
        process = _running.get(tag)
    if process is None:
        return False
    process.stopped_on_purpose = True
    process.kill()
    return True
