from datetime import datetime

import pytest

from jarvis import forecast

from fakes import weather_sample

NOW = datetime(2026, 10, 1, 14, 0)  # a Thursday afternoon


@pytest.mark.parametrize("command, place", [
    ("what's the weather in london tomorrow", "london"),
    ("what's the weather like tomorrow in london", "london"),
    ("will it rain in london in the afternoon", "london"),
    ("what's the forecast for tomorrow in istanbul", "istanbul"),
    ("weather in new york city on saturday", "new york city"),
    ("what's the weather like in the hague", "the hague"),
    ("how's the weather in rio de janeiro", "rio de janeiro"),
    ("what's the time in tokyo right now", "tokyo"),
    ("london weather", "london"),
    ("what's the new york weather like", "new york"),
    ("what's the weather", ""),
    ("tell me the weather", ""),
    ("today's weather", ""),
    ("what's the weather like at the moment", ""),
    ("will it rain in the afternoon", ""),
    ("what's the weather in 2 hours", ""),
    ("what's the weather in two hours", ""),
    ("what's the weather like in my area", ""),
    ("is it cold in here", ""),
    ("what's the weather for the weekend", ""),
    ("what time is it at the moment", ""),
])
def test_the_place_asked_about(command, place):
    assert forecast.place_in(command) == place


def test_without_a_forecast_by_day():
    data = dict(weather_sample(now=NOW), days=[], hours=[])
    now = "Right now in Bristol it's 14 degrees with light rain."
    assert forecast.answer(data, "what's the weather tomorrow").startswith(now)
    assert forecast.answer(data, "will it rain").startswith(now)
    assert forecast.answer(data, "what's the weather this weekend").startswith(now)


def test_rain_right_now():
    dry = dict(weather_sample(now=NOW), code=3, sky="overcast skies", raining=False)
    assert forecast.answer(dry, "is it raining") == (
        "No, it's not raining in Bristol right now. It's 14 degrees with overcast skies. "
        "But there's a 70 percent chance of rain later today.")
    assert forecast.answer(dict(dry, raining=True), "is it raining") == "Yes, it's raining a little in Bristol right now."
    assert forecast.answer(dry, "is it raining later").startswith("Yes, probably. There's a 70 percent chance")
    assert forecast.answer(dry, "how windy is it") == "The wind in Bristol is 12 miles per hour right now."
    assert forecast.answer(dry, "is it snowing") == (
        "No, it's not snowing in Bristol right now. It's 14 degrees with overcast skies.")


@pytest.mark.parametrize("chance, umbrella, said", [
    (0, False, "No. There's almost no chance of rain in Bristol tomorrow."),
    (0, True, "You won't need one. There's almost no chance of rain in Bristol tomorrow."),
    (25, False, "Probably not. There's only a 25 percent chance of rain in Bristol tomorrow."),
    (40, True, "Maybe take one to be safe. There's a 40 percent chance of rain in Bristol tomorrow."),
    (45, False, "It might. There's a 45 percent chance of rain in Bristol tomorrow."),
    (80, False, "Yes, probably. There's an 80 percent chance of rain in Bristol tomorrow."),
    (80, True, "Yes, take one. There's an 80 percent chance of rain in Bristol tomorrow."),
])
def test_straight_answers_about_rain(chance, umbrella, said):
    data = weather_sample(now=NOW)
    data["days"][1] = dict(data["days"][1], rain=chance)
    question = "do i need an umbrella tomorrow" if umbrella else "will it rain tomorrow"
    assert forecast.answer(data, question) == said


def test_rain_for_the_rest_of_today_and_in_the_morning():
    data = weather_sample(now=NOW)
    assert forecast.answer(data, "will it rain today") == (
        "Yes, probably. There's a 70 percent chance of rain in Bristol for the rest of today.")
    early = weather_sample(now=NOW.replace(hour=8))
    assert forecast.answer(early, "will it rain today") == (
        "Yes, probably. There's a 70 percent chance of rain in Bristol today.")
    no_hours = dict(data, rain=None, days=[dict(data["days"][0], rain=None)] + data["days"][1:], hours=[])
    assert forecast.answer(no_hours, "do i need an umbrella") == "Yes, take one: light rain in Bristol for the rest of today."


def test_parts_of_the_day():
    data = weather_sample(now=NOW)
    assert forecast.answer(data, "what's the weather like this morning") == (  # it's over: all of it, then
        "This morning in Bristol: overcast skies, between 9 and 12 degrees, with a 35 percent chance of rain.")
    assert forecast.answer(data, "will it snow on monday night") == (
        "Yes, snow is expected in Bristol on Monday night: light snow, between -1 and 1 degrees.")
    assert forecast.answer(data, "what's the weather on saturday evening") == (
        "On Saturday evening in Bristol: overcast skies, between 10 and 13 degrees, with a 5 percent chance of rain.")
