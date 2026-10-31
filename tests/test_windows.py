import base64

import pytest

from jarvis import config, windows


class FakeRegistry:
    """Just enough of winreg for the Run key."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, reserved=0, access=0):
        assert root == "HKCU" and path == windows.RUN_KEY
        return self._Key()

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.fixture
def registry(monkeypatch):
    reg = FakeRegistry()
    monkeypatch.setattr(windows, "available", lambda: True)
    monkeypatch.setattr(windows, "_winreg", lambda: reg)
    return reg


def test_start_with_windows_can_be_switched_on_and_off(registry):
    assert not windows.starts_with_windows()
    windows.set_start_with_windows(True)
    assert registry.values[windows.RUN_NAME] == windows.app_command()
    assert windows.starts_with_windows()
    windows.set_start_with_windows(False)
    assert registry.values == {}
    windows.set_start_with_windows(False)  # already off is fine


def test_an_entry_for_an_old_copy_of_the_app_does_not_count(registry):
    registry.values[windows.RUN_NAME] = '"C:\\old\\pythonw.exe" "C:\\old\\app.pyw"'
    assert not windows.starts_with_windows()


def test_off_windows_nothing_starts_with_windows(monkeypatch):
    monkeypatch.setattr(windows, "available", lambda: False)
    assert not windows.starts_with_windows()


def test_the_app_runs_without_a_console_window(monkeypatch, tmp_path):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    monkeypatch.setattr(windows.sys, "executable", str(tmp_path / "python.exe"))
    assert windows.app_command() == f'"{tmp_path / "pythonw.exe"}" "{config.ROOT / "app.pyw"}"'


def test_the_desktop_shortcut_is_made_through_powershell(monkeypatch, tmp_path):
    calls = []

    def run(script, env, timeout):
        calls.append((script, env))
        return base64.b64encode(b"C:\\Users\\me\\Desktop\\Jarvis.lnk").decode("ascii") + "\r\n"

    monkeypatch.setattr(windows.powershell, "run", run)
    assert windows.create_shortcut(tmp_path / "icon.ico").endswith("Jarvis.lnk")
    script, env = calls[0]
    value = lambda name: base64.b64decode(env[name]).decode("utf-8")  # noqa: E731
    assert script == windows._SHORTCUT_SCRIPT
    assert value("MA_TARGET") == str(windows.pythonw())
    assert value("MA_ARGS") == f'"{config.ROOT / "app.pyw"}"'
    assert value("MA_DIR") == str(config.ROOT)
    assert value("MA_ICON") == str(tmp_path / "icon.ico")


def test_a_strange_reply_from_powershell_is_an_error(monkeypatch):
    monkeypatch.setattr(windows.powershell, "run", lambda script, env, timeout: "")
    with pytest.raises(windows.powershell.PowerShellError):
        windows.create_shortcut()


def test_the_old_mail_assistant_shortcut_is_replaced():
    script = windows._SHORTCUT_SCRIPT
    assert "'Mail Assistant.lnk'" in script and "Remove-Item" in script
    assert "'Jarvis.lnk'" in script


def test_the_old_start_up_entry_is_cleaned_up(registry):
    registry.values[windows.OLD_RUN_NAME] = '"C:\\x\\pythonw.exe" "C:\\x\\app.pyw"'
    windows.set_start_with_windows(True)
    assert set(registry.values) == {windows.RUN_NAME}
    registry.values[windows.OLD_RUN_NAME] = "old"
    windows.set_start_with_windows(False)
    assert registry.values == {}


def test_jarvis_takes_over_the_old_start_up_entry(registry):
    assert windows.take_over_start_entry() is False  # starting with Windows is off: leave it off
    assert registry.values == {}
    registry.values[windows.OLD_RUN_NAME] = '"C:\\old\\pythonw.exe" "C:\\old\\app.pyw"'
    assert windows.take_over_start_entry() is True
    assert registry.values == {windows.RUN_NAME: windows.app_command()}


def test_an_entry_for_another_copy_of_jarvis_is_moved_to_this_one(registry):
    # e.g. the folder of the previous version, which has been deleted since
    registry.values[windows.RUN_NAME] = '"C:\\Python\\pythonw.exe" "C:\\Users\\me\\jarvis-assistant\\app.pyw"'
    assert windows.take_over_start_entry() is True
    assert registry.values == {windows.RUN_NAME: windows.app_command()}
    assert windows.starts_with_windows()
    assert windows.take_over_start_entry() is False  # already this copy: nothing to do


def test_no_start_up_changes_off_windows(monkeypatch):
    monkeypatch.setattr(windows, "available", lambda: False)
    assert windows.take_over_start_entry() is False


def test_no_dark_title_bar_off_windows(monkeypatch):
    monkeypatch.setattr(windows, "available", lambda: False)
    assert windows.dark_title_bar(1234) is False
