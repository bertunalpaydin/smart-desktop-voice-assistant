import json
from datetime import datetime, timedelta

import pytest

from jarvis import alarms

NOW = datetime(2026, 10, 1, 14, 0)  # a Thursday afternoon
TODAY, TOMORROW = NOW.date(), NOW.date() + timedelta(days=1)


@pytest.mark.parametrize("text, expected", [
    ("twenty five minutes", "25 minutes"),
    ("seven thirty", "7 30"),
    ("ten", "10"),
    ("set a timer", "set a timer"),
])
def test_words_to_numbers(text, expected):
    assert alarms.words_to_numbers(text) == expected


@pytest.mark.parametrize("text, seconds", [
    ("10 minutes", 600),
    ("set a timer for ten minutes", 600),
    ("1 hour 20 minutes", 4800),
    ("an hour and a half", 5400),
    ("half an hour", 1800),
    ("90 seconds", 90),
    ("twenty five minutes", 1500),
    ("a minute", 60),
    ("2 hours", 7200),
    ("10 min", 600),
])
def test_durations(text, seconds):
    assert alarms.parse_duration(text) == timedelta(seconds=seconds)


@pytest.mark.parametrize("text", ["nothing here", "48 hours", "0 minutes"])
def test_no_duration(text):
    assert alarms.parse_duration(text) is None


@pytest.mark.parametrize("text, expected", [
    ("7 am", datetime.combine(TOMORROW, datetime.min.time()).replace(hour=7)),
    ("set an alarm for 7.30 a.m.", datetime.combine(TOMORROW, datetime.min.time()).replace(hour=7, minute=30)),
    ("6:45 pm", datetime.combine(TODAY, datetime.min.time()).replace(hour=18, minute=45)),
    ("7", datetime.combine(TODAY, datetime.min.time()).replace(hour=19)),  # the next 7 o'clock
    ("half past six", datetime.combine(TODAY, datetime.min.time()).replace(hour=18, minute=30)),
    ("quarter to 8 am", datetime.combine(TOMORROW, datetime.min.time()).replace(hour=7, minute=45)),
    ("seven thirty", datetime.combine(TODAY, datetime.min.time()).replace(hour=19, minute=30)),
    ("noon", datetime.combine(TOMORROW, datetime.min.time()).replace(hour=12)),
    ("midnight", datetime.combine(TOMORROW, datetime.min.time())),
    ("tomorrow at 9", datetime.combine(TOMORROW, datetime.min.time()).replace(hour=9)),
    ("7 in the morning", datetime.combine(TOMORROW, datetime.min.time()).replace(hour=7)),
    ("9 tonight", datetime.combine(TODAY, datetime.min.time()).replace(hour=21)),
    ("16:20", datetime.combine(TODAY, datetime.min.time()).replace(hour=16, minute=20)),
    ("at 5 o'clock", datetime.combine(TODAY, datetime.min.time()).replace(hour=17)),
])
def test_clock_times(text, expected):
    assert alarms.parse_clock(text, NOW) == expected


@pytest.mark.parametrize("text", ["hello", "25:00", "13 pm", "7:75"])
def test_no_clock_time(text):
    assert alarms.parse_clock(text, NOW) is None


def test_how_times_are_said():
    assert alarms.clock(datetime(2026, 1, 1, 7, 5)) == "7:05 AM"
    assert alarms.when_text(NOW + timedelta(hours=1), NOW) == "3:00 PM"
    assert alarms.when_text(NOW + timedelta(days=1), NOW) == "2:00 PM tomorrow"
    assert alarms.when_text(NOW + timedelta(days=3), NOW) == "2:00 PM on Sunday"
    assert alarms.duration_text(timedelta(minutes=90)) == "1 hour and 30 minutes"
    assert alarms.duration_text(timedelta(seconds=61)) == "1 minute and 1 second"


def test_what_each_kind_says():
    due = NOW + timedelta(minutes=10)
    timer = alarms.Alarm("1", "timer", due, "10 minutes")
    reminder = alarms.Alarm("2", "reminder", due, "call Mum")
    alarm = alarms.Alarm("3", "alarm", due)
    assert timer.describe(NOW) == "a timer for 10 minutes, ending at 2:10 PM (10 minutes left)"
    assert timer.describe(NOW + timedelta(minutes=6, seconds=20)) == "a timer for 10 minutes, ending at 2:10 PM (4 minutes left)"
    assert reminder.describe(NOW) == "a reminder to call Mum at 2:10 PM"
    assert alarm.describe(NOW) == "an alarm at 2:10 PM"
    assert timer.announcement() == "Your timer for 10 minutes is done."
    assert reminder.announcement() == "Reminder: call Mum."
    assert alarms.Alarm("4", "reminder", due).announcement() == "This is your reminder."
    assert alarms.Alarm("4", "reminder", due).describe(NOW) == "a reminder at 2:10 PM"
    assert alarm.announcement() == "It's 2:10 PM. This is your alarm."


def test_store_keeps_them_in_order_and_survives_a_restart(tmp_path):
    store = alarms.AlarmStore(tmp_path / "alarms.json")
    later = store.add("alarm", NOW + timedelta(hours=2))
    sooner = store.add("reminder", NOW + timedelta(minutes=5), "stretch")
    assert [a.id for a in store.all()] == [sooner.id, later.id]
    again = alarms.AlarmStore(tmp_path / "alarms.json")  # e.g. the app after a restart
    assert again.all() == store.all()
    assert again.all()[0].text == "stretch"


def test_store_remove_and_clear(tmp_path):
    store = alarms.AlarmStore(tmp_path / "alarms.json")
    first = store.add("alarm", NOW)
    store.add("timer", NOW, "5 minutes")
    store.add("timer", NOW, "1 minute")
    assert store.remove(first.id) is True
    assert store.remove("missing") is False
    assert store.clear("reminder") == 0
    assert store.clear("timer") == 2
    assert store.all() == []


def test_due_ones_are_taken_out(tmp_path):
    store = alarms.AlarmStore(tmp_path / "alarms.json")
    store.add("timer", NOW - timedelta(seconds=1), "1 minute")
    store.add("alarm", NOW + timedelta(hours=1))
    due = store.pop_due(NOW)
    assert [a.kind for a in due] == ["timer"]
    assert [a.kind for a in store.all()] == ["alarm"]
    assert store.pop_due(NOW) == []


def test_a_broken_file_is_ignored(tmp_path):
    path = tmp_path / "alarms.json"
    path.write_text("{not json", encoding="utf-8")
    store = alarms.AlarmStore(path)
    assert store.all() == []
    path.write_text(json.dumps([{"id": "x"}, {"id": "y", "kind": "alarm", "due": "2026-10-01T15:00:00"}]))
    assert [a.id for a in store.all()] == ["y"]


@pytest.mark.parametrize("text, days", [
    ("tomorrow", 1), ("what's the weather tomorrow", 1), ("tomorrow's date", 1), ("today", 0), ("tonight", 0),
    ("the day after tomorrow", 2), ("yesterday", -1), ("the day before yesterday", -2), ("in 3 days", 3),
    ("in three days", 3), ("in a day", 1), ("2 days ago", -2),
    ("on friday", 1), ("saturday", 2), ("this saturday", 2), ("on thursday", 0),  # NOW is a Thursday
    ("next friday", 8), ("next thursday", 7), ("next monday", 4), ("this coming monday", 4),
    ("nothing here", None), ("fridays", None),
])
def test_finding_the_day(text, days):
    assert alarms.find_day(text, TODAY)[0] == days


def test_the_words_that_name_the_day():
    assert alarms.find_day("remind me on friday morning to call", TODAY) == (1, "on friday morning")
    assert alarms.find_day("in 2 days at 9", TODAY) == (2, "in 2 days")
    assert alarms.find_day("hello", TODAY) == (None, "")


@pytest.mark.parametrize("text, part", [("this morning", "morning"), ("tomorrow afternoon", "afternoon"),
                                        ("tonight", "night"), ("overnight", "night"), ("at noon", None)])
def test_finding_the_part_of_the_day(text, part):
    assert alarms.find_part(text) == part


def at(day, hour, minute=0):
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)


@pytest.mark.parametrize("text, expected", [
    ("monday at 8", at(TODAY + timedelta(days=4), 8)),
    ("7 on saturday", at(TODAY + timedelta(days=2), 7)),
    ("6:30 the day after tomorrow", at(TODAY + timedelta(days=2), 6, 30)),
    ("tomorrow evening at 7", at(TOMORROW, 19)),
    ("7 tomorrow morning", at(TOMORROW, 7)),
    ("friday night at 10", at(TOMORROW, 22)),
    ("thursday at 9 am", at(TODAY + timedelta(days=7), 9)),  # this Thursday's 9 am is over
    ("thursday at 5", at(TODAY, 17)),  # but 5 pm today isn't
    ("in 3 days at 8 am", at(TODAY + timedelta(days=3), 8)),
    ("tonight at 11", at(TODAY, 23)),
    ("yesterday at 5", None),
])
def test_clock_times_on_other_days(text, expected):
    assert alarms.parse_clock(text, NOW) == expected


def test_reminders_are_for_the_daytime():
    assert alarms.parse_clock("at 1", NOW) == at(TOMORROW, 1)  # an alarm: the next 1 o'clock
    assert alarms.parse_clock("at 1", NOW, daytime=True) == at(TOMORROW, 13)
    assert alarms.parse_clock("at 5", NOW, daytime=True) == at(TODAY, 17)
    assert alarms.parse_clock("at 12", NOW, daytime=True) == at(TOMORROW, 12)
    assert alarms.parse_clock("at 9", NOW, daytime=True) == at(TODAY, 21)
    assert alarms.parse_clock("at 3 am", NOW, daytime=True) == at(TOMORROW, 3)


def test_a_week_or_more_away_says_the_date():
    assert alarms.when_text(NOW + timedelta(days=6), NOW) == "2:00 PM on Wednesday"
    assert alarms.when_text(NOW + timedelta(days=7), NOW) == "2:00 PM on Thursday 8 October"
