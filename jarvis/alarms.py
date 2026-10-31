"""Alarms, timers and reminders. They are kept in a JSON file, so they survive a
restart and the command line and the tray app see the same list."""
import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta

_UNITS = {
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
}
_ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
NUMBER_WORDS = frozenset(_ONES) | frozenset(_TENS)
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_COUNT = r"\d{1,3}|an?|" + "|".join(_ONES)
_DAY = re.compile(
    r"\b(?:(?:on|for|by|until|till) )?(?:"
    r"(?P<after>(?:the )?day after tomorrow)|(?P<before>(?:the )?day before yesterday)"
    r"|(?P<tomorrow>tomorrow)|(?P<yesterday>yesterday)|(?P<today>today|tonight)"
    rf"|(?:(?P<which>next|this coming|this|coming) )?(?P<weekday>{'|'.join(WEEKDAYS)})"
    rf"|in (?P<ahead>{_COUNT}) days?(?: time)?|(?P<ago>{_COUNT}) days? ago"
    r")(?:'s)?(?: (?:morning|afternoon|evening|night))?(?!\w)")
_PART = re.compile(r"\b(morning|afternoon|evening|tonight|night|overnight)\b")


def words_to_numbers(text):
    """'twenty five minutes' -> '25 minutes', 'seven thirty' -> '7 30'."""
    out, pending = [], None
    for word in text.split():
        if word in _TENS:
            if pending is not None:
                out.append(str(pending))
            pending = _TENS[word]
        elif word in _ONES:
            if pending in _TENS.values() and _ONES[word] < 10:
                pending += _ONES[word]
            else:
                if pending is not None:
                    out.append(str(pending))
                pending = _ONES[word]
        else:
            if pending is not None:
                out.append(str(pending))
                pending = None
            out.append(word)
    if pending is not None:
        out.append(str(pending))
    return " ".join(out)


def _count(word):
    return int(word) if word.isdigit() else 1 if word in ("a", "an") else _ONES[word]


def find_day(text, today):
    """The day the text names, as (days from today, the words that name it), or (None, "").
    'tomorrow' -> 1, 'the day after tomorrow' -> 2, 'yesterday' -> -1, 'in 3 days' -> 3,
    'on Friday' -> the coming Friday (0 if today is a Friday), 'next Friday' -> next week's Friday."""
    match = _DAY.search(text.casefold())
    if not match:
        return None, ""
    found = match.groupdict()
    if found["weekday"]:
        weekday = WEEKDAYS.index(found["weekday"])
        days = (weekday - today.weekday()) % 7
        if found["which"] == "next" and weekday >= today.weekday():
            days += 7  # next week's, as "next Friday" means on a Thursday; the coming one is "this Friday"
    elif found["ahead"] or found["ago"]:
        days = _count(found["ahead"]) if found["ahead"] else -_count(found["ago"])
    else:
        days = next(n for key, n in (("after", 2), ("before", -2), ("tomorrow", 1), ("yesterday", -1), ("today", 0))
                    if found[key])
    return days, match.group(0)


def find_part(text):
    """'morning', 'afternoon', 'evening' or 'night' if the text names a part of the day, else None."""
    match = _PART.search(text.casefold())
    if not match:
        return None
    return {"tonight": "night", "overnight": "night"}.get(match.group(1), match.group(1))


def parse_duration(text):
    """'10 minutes', 'an hour and a half', '1 hour 20 minutes' -> timedelta, or None."""
    text = text.casefold().replace("-", " ")
    text = re.sub(r"\b(an?|one) hour and a half\b|\b(one|1) and a half hours?\b", "90 minutes", text)
    text = re.sub(r"\bhalf an hour\b|\bhalf hour\b", "30 minutes", text)
    text = re.sub(r"\b(a )?quarter of an hour\b|\ba quarter hour\b", "15 minutes", text)
    text = re.sub(r"\bhalf a minute\b", "30 seconds", text)
    text = re.sub(r"\ban?\s+(hour|minute|second)\b", r"1 \1", text)
    text = words_to_numbers(text)
    seconds = sum(
        float(amount) * _UNITS[unit]
        for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", text)
    )
    return timedelta(seconds=round(seconds)) if 0 < seconds <= 24 * 3600 else None


_PART_OF_DAY = re.compile(r"\b(?:in the|this|tomorrow|at|on \w+|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\s+"
                          r"(morning|afternoon|evening|night)\b|\b(tonight|overnight)\b")


def parse_clock(text, now, daytime=False):
    """'7 am', '6:45 pm', 'half past seven', 'tomorrow at 9', 'Friday evening at 8' -> the next such
    moment, or None. A bare '7' is 7:00 or 19:00, whichever comes first; with daytime=True (reminders)
    1 to 6 o'clock are in the afternoon, since nobody wants a reminder at 3 in the night."""
    text = text.casefold()
    part = _PART_OF_DAY.search(text)
    hint = None if not part else "am" if part.group(1) == "morning" else "pm"
    text = re.sub(r"\b([ap])\.\s?m\b\.?|\b([ap])\s?m\b", lambda m: (m.group(1) or m.group(2)) + "m", text)
    text = text.replace("o'clock", " ").replace("oclock", " ")
    text = re.sub(r"\bin the morning\b", "am", text)
    text = re.sub(r"\bin the (afternoon|evening)\b|\bat night\b|\btonight\b", "pm", text)
    text = re.sub(r"\b(noon|midday)\b", "12 pm", text)
    text = re.sub(r"\bmidnight\b", "12 am", text)
    days, day_words = find_day(text, now.date())
    if days is not None and days < 0:
        return None  # that day is over
    text = text.replace(day_words, " ", 1) if day_words else text  # "in 3 days" is not 3 o'clock
    text = words_to_numbers(text)
    text = re.sub(r"\bhalf past (\d{1,2})\b", r"\1:30", text)
    text = re.sub(r"\bquarter past (\d{1,2})\b", r"\1:15", text)
    text = re.sub(r"\bquarter to (\d{1,2})\b", lambda m: f"{(int(m.group(1)) - 1) or 12}:45", text)
    match = re.search(r"\b(\d{1,2})(?:[:.](\d{2})|\s+(\d{2})\b)?\s*(am|pm)?(?!\w)", text)
    if not match:
        return None
    hour, minute, period = int(match.group(1)), int(match.group(2) or match.group(3) or 0), match.group(4)
    if hour > 23 or minute > 59:
        return None
    if period:
        if not 1 <= hour <= 12:
            return None
        hours = [hour % 12 + (12 if period == "pm" else 0)]
    elif hint and 1 <= hour <= 11:  # "tomorrow evening at 7"
        hours = [hour + (12 if hint == "pm" else 0)]
    elif daytime and 1 <= hour <= 6:
        hours = [hour + 12]
    elif daytime and hour == 12:
        hours = [12]
    elif hour <= 12:
        hours = sorted({hour % 12, hour % 12 + 12})  # "7" is 7:00 or 19:00, whichever comes first
    else:
        hours = [hour]

    if days is None:  # the next such moment
        options = []
        for h in hours:
            moment = datetime.combine(now.date(), time(h, minute))
            if moment <= now:
                moment += timedelta(days=1)
            options.append(moment)
        return min(options)
    day = now.date() + timedelta(days=days)
    options = [datetime.combine(day, time(h, minute)) for h in hours]
    if all(moment <= now for moment in options):  # "Friday at 7 am" on a Friday afternoon: next week's
        weekday = any(name in day_words for name in WEEKDAYS)
        options = [moment + timedelta(days=7 if weekday else 1) for moment in options]
    return min(moment for moment in options if moment > now)


def clock(moment):
    """7:05 AM"""
    return moment.strftime("%I:%M %p").lstrip("0")


def when_text(moment, now):
    if moment.date() == now.date():
        return clock(moment)
    if moment.date() == now.date() + timedelta(days=1):
        return f"{clock(moment)} tomorrow"
    if moment.date() < now.date() + timedelta(days=7):
        return f"{clock(moment)} on {moment:%A}"
    return f"{clock(moment)} on {moment:%A} {moment.day} {moment:%B}"  # "Friday" alone would be this week's


def duration_text(delta):
    hours, rest = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(rest, 60)
    parts = [f"{n} {unit}{'' if n == 1 else 's'}"
             for n, unit in ((hours, "hour"), (minutes, "minute"), (seconds, "second")) if n]
    return " and ".join(parts) or "0 seconds"


@dataclass(frozen=True)
class Alarm:
    id: str
    kind: str  # "alarm", "timer" or "reminder"
    due: datetime
    text: str = ""  # what to remind about, or the timer's length

    def describe(self, now):
        if self.kind == "timer":
            left = duration_text(timedelta(seconds=max(0, round((self.due - now).total_seconds() / 60) * 60)))
            return f"a timer for {self.text}, ending at {when_text(self.due, now)} ({left} left)"
        if self.kind == "reminder":
            what = f" to {self.text}" if self.text else ""
            return f"a reminder{what} at {when_text(self.due, now)}"
        return f"an alarm at {when_text(self.due, now)}"

    def announcement(self):
        if self.kind == "timer":
            return f"Your timer for {self.text} is done."
        if self.kind == "reminder":
            return f"Reminder: {self.text}." if self.text else "This is your reminder."
        return f"It's {clock(self.due)}. This is your alarm."


class AlarmStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def _read(self):
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []  # no file yet, or a broken one: start with an empty list
        alarms = []
        for item in items if isinstance(items, list) else []:
            try:
                alarms.append(Alarm(str(item["id"]), str(item["kind"]),
                                    datetime.fromisoformat(item["due"]), str(item.get("text", ""))))
            except (KeyError, TypeError, ValueError):
                continue
        return alarms

    def _write(self, alarms):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [{"id": a.id, "kind": a.kind, "due": a.due.isoformat(timespec="seconds"), "text": a.text}
                for a in alarms]
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def all(self):
        with self._lock:
            return sorted(self._read(), key=lambda a: a.due)

    def add(self, kind, due, text=""):
        alarm = Alarm(uuid.uuid4().hex[:8], kind, due.replace(microsecond=0), text)
        with self._lock:
            self._write(self._read() + [alarm])
        return alarm

    def remove(self, alarm_id):
        with self._lock:
            alarms = self._read()
            kept = [a for a in alarms if a.id != alarm_id]
            self._write(kept)
        return len(kept) < len(alarms)

    def clear(self, kind=None):
        """Remove all of them (or all of one kind). Returns how many went."""
        with self._lock:
            alarms = self._read()
            kept = [a for a in alarms if kind and a.kind != kind]
            self._write(kept)
        return len(alarms) - len(kept)

    def pop_due(self, now):
        """Take out and return everything that is due."""
        with self._lock:
            alarms = self._read()
            due = [a for a in alarms if a.due <= now]
            if due:
                self._write([a for a in alarms if a.due > now])
        return sorted(due, key=lambda a: a.due)
