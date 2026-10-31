import copy
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # the window tests need no screen

from jarvis import config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Tests never touch the real per-user files, and setting changes don't leak between tests."""
    saved = {name: copy.copy(getattr(config, name)) for name in dir(config) if name.isupper()}
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "assistant.db")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "jarvis")
    monkeypatch.setattr(config, "OLD_APP_DIR", tmp_path / "mail-assistant")
    monkeypatch.setattr(config, "ALARMS_FILE", tmp_path / "alarms.json")
    monkeypatch.setattr(config, "NOTES_FILE", tmp_path / "notes.txt")
    yield
    for name, value in saved.items():
        setattr(config, name, value)
