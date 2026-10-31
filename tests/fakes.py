"""Shared stand-ins for the tests."""
import subprocess


class FakePopen:
    """Stands in for subprocess.Popen and hands the call to a subprocess.run-style function."""

    def __init__(self, run, args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.returncode = None
        self.killed = False
        self.timeout = None
        self._timeout_error = None
        try:
            self._result = run(args, **kwargs)  # an OSError here is like Popen failing to start
        except subprocess.TimeoutExpired as exc:
            self._result, self._timeout_error = None, exc

    def communicate(self, timeout=None):
        self.timeout = timeout
        if self._timeout_error is not None and not self.killed:
            raise self._timeout_error
        if self._result is None or self.killed:
            self.returncode = 1
            return "", ""
        self.returncode = self._result.returncode
        return self._result.stdout, self._result.stderr

    def kill(self):
        self.killed = True
        self.returncode = 1


def use_run(monkeypatch, run):
    """Send PowerShell calls to `run(args, **kwargs)`, which returns a CompletedProcess. Returns the processes."""
    from jarvis import powershell

    made = []

    def popen(args, **kwargs):
        process = FakePopen(run, args, **kwargs)
        made.append(process)
        return process

    monkeypatch.setattr(powershell.subprocess, "Popen", popen)
    return made


# (code, low, high, chance of rain, snow in cm, wind in mph) for 8 days from a Thursday
WEEK = [(61, 9, 15, 70, 0, 18), (3, 8, 13, 20, 0, 12), (2, 10, 16, 10, 0, 9), (63, 7, 12, 90, 0, 25),
        (71, -1, 3, 40, 2.5, 14), (0, 5, 11, 0, 0, 6), (80, 9, 14, 50, 0, 20), (1, 9, 15, 5, 0, 8)]


def weather_sample(place="Bristol", now=None):
    """What web.weather_data() returns: now light rain at 14 degrees, then WEEK by day and by hour.
    In the afternoons (12 to 18) it rains most and is warmest; the rest of the day is drier."""
    from datetime import datetime, timedelta

    from jarvis import web

    now = now or datetime(2026, 10, 1, 14, 0)
    days, hours = [], []
    for i, (code, low, high, rain, snow, wind) in enumerate(WEEK):
        date = now.date() + timedelta(days=i)
        midnight = datetime.combine(date, datetime.min.time())
        days.append({"date": date, "code": code, "sky": web.WEATHER_CODES[code], "high": high, "low": low,
                     "rain": rain, "snow": snow, "wind": wind, "sunrise": midnight + timedelta(hours=7, minutes=10 + i),
                     "sunset": midnight + timedelta(hours=18, minutes=40 - 2 * i)})
        for hour in range(24):
            afternoon = 12 <= hour < 18
            hours.append({"time": datetime.combine(date, datetime.min.time()).replace(hour=hour),
                          "code": code if afternoon or code in (71, 0, 1) else 3,
                          "temperature": high if afternoon else low + (high - low) // 2 if 9 <= hour < 21 else low,
                          "rain": rain if afternoon else rain // 2})
    return {"place": place, "temperature": 14, "sky": "light rain", "high": 15, "low": 9, "rain": 70, "code": 61,
            "raining": True, "wind": 12, "now": now, "days": days, "hours": hours}
