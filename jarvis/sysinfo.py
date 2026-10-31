"""How busy the computer is: processor, memory, battery and disk, for the HUD."""
import shutil
import sys
from pathlib import Path

_last_times = None  # the processor's idle and busy times at the last reading


def _windows():
    import ctypes
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong) for name in
            ("total", "free", "page_total", "page_free", "virtual_total", "virtual_free", "extended")]

    class PowerStatus(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("percent", ctypes.c_ubyte),
                    ("saver", ctypes.c_ubyte), ("life", wintypes.DWORD), ("full_life", wintypes.DWORD)]

    return ctypes, FileTime, MemoryStatus, PowerStatus


def _cpu(kernel32, ctypes, FileTime):
    global _last_times
    idle, kernel, user = FileTime(), FileTime(), FileTime()
    if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    value = lambda t: (t.high << 32) | t.low  # noqa: E731
    now = (value(idle), value(kernel) + value(user))  # kernel time includes idle time
    before, _last_times = _last_times, now
    if before is None or now[1] == before[1]:
        return None
    busy = 1 - (now[0] - before[0]) / (now[1] - before[1])
    return round(max(0.0, min(1.0, busy)) * 100)


def snapshot():
    """{"cpu", "memory", "battery", "plugged_in", "disk"}: percentages, or None where unknown."""
    info = {"cpu": None, "memory": None, "battery": None, "plugged_in": None, "disk": None}
    try:
        usage = shutil.disk_usage(Path.home().anchor or "/")
        info["disk"] = round(usage.used / usage.total * 100)
    except OSError:
        pass
    if sys.platform == "darwin":
        return _mac(info)
    if sys.platform != "win32":
        return info
    try:
        ctypes, FileTime, MemoryStatus, PowerStatus = _windows()
        kernel32 = ctypes.windll.kernel32
        info["cpu"] = _cpu(kernel32, ctypes, FileTime)
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(MemoryStatus)
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            info["memory"] = int(memory.load)
        power = PowerStatus()
        if kernel32.GetSystemPowerStatus(ctypes.byref(power)) and power.percent != 255:  # 255: no battery
            info["battery"] = int(power.percent)
            info["plugged_in"] = power.ac == 1
    except (OSError, AttributeError):
        pass
    return info


_mac_started = False  # psutil's first processor reading has nothing to compare with


def _mac(info):
    global _mac_started
    try:
        import psutil
    except ImportError:
        return info
    try:
        busy = psutil.cpu_percent(interval=None)
        info["cpu"] = round(busy) if _mac_started else None
        _mac_started = True
        info["memory"] = round(psutil.virtual_memory().percent)
        battery = psutil.sensors_battery()  # None on a Mac without one
        if battery is not None:
            info["battery"] = round(battery.percent)
            info["plugged_in"] = bool(battery.power_plugged)
    except (OSError, RuntimeError, AttributeError):
        pass
    return info
