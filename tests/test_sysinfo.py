import ctypes
import types

from jarvis import sysinfo


class FakeKernel:
    """Windows' kernel32, as far as the readings go."""

    def __init__(self):
        self.idle, self.busy, self.load, self.percent, self.ac = 1000, 1000, 42, 80, 1

    def GetSystemTimes(self, idle, kernel, user):  # noqa: N802 - Windows' name
        idle._obj.low, kernel._obj.low, user._obj.low = self.idle, self.idle + self.busy, 0
        return 1

    def GlobalMemoryStatusEx(self, status):  # noqa: N802
        assert status._obj.length == ctypes.sizeof(type(status._obj))
        status._obj.load = self.load
        return 1

    def GetSystemPowerStatus(self, status):  # noqa: N802
        status._obj.percent, status._obj.ac = self.percent, self.ac
        return 1


def test_off_windows_only_the_disk_is_known(monkeypatch):
    monkeypatch.setattr(sysinfo.sys, "platform", "linux")
    info = sysinfo.snapshot()
    assert info["cpu"] is info["memory"] is info["battery"] is info["plugged_in"] is None
    assert 0 <= info["disk"] <= 100


def test_windows_readings(monkeypatch):
    kernel = FakeKernel()
    monkeypatch.setattr(sysinfo.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel), raising=False)
    monkeypatch.setattr(sysinfo, "_last_times", None)
    first = sysinfo.snapshot()
    assert first["cpu"] is None  # the processor needs two readings
    assert (first["memory"], first["battery"], first["plugged_in"]) == (42, 80, True)
    kernel.idle += 25
    kernel.busy += 75
    assert sysinfo.snapshot()["cpu"] == 75
    kernel.percent, kernel.ac = 255, 0  # a desktop: no battery
    info = sysinfo.snapshot()
    assert info["battery"] is None and info["plugged_in"] is None and info["cpu"] is None  # no time passed


def test_a_failing_windows_call_leaves_gaps(monkeypatch):
    def broken(*args):
        raise OSError("access denied")

    kernel = types.SimpleNamespace(GetSystemTimes=broken, GlobalMemoryStatusEx=broken, GetSystemPowerStatus=broken)
    monkeypatch.setattr(sysinfo.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel), raising=False)
    info = sysinfo.snapshot()
    assert info["cpu"] is info["memory"] is info["battery"] is None and info["disk"] is not None
