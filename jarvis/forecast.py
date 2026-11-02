"""What Jarvis says when asked about the weather: right now, today, tomorrow, on Saturday
afternoon, at the weekend or over the next days, and straight answers to "will it rain?",
"do I need an umbrella?", "will it snow?" and "how windy is it?". The numbers come from
web.weather_data(); this file only reads the question and words the answer."""
import re

from . import alarms
from .web import WEATHER_CODES, a_or_an, weather_text

RAIN_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 68, 69, 80, 81, 82, 83, 84, 95, 96, 99})
SNOW_CODES = frozenset({68, 69, 71, 73, 75, 77, 83, 84, 85, 86})  # sleet (68, 69, 83, 84) is rain and snow
PARTS = {"morning": (6, 12), "afternoon": (12, 18), "evening": (18, 23), "night": (18, 24)}

# words that end a place's name ("in London tomorrow") or show that what follows "in" is no place
_NOT_PLACE = {
    "today", "today's", "tomorrow", "tomorrow's", "tonight", "tonight's", "yesterday", "now", "right", "currently",
    "later", "soon", "this", "next", "coming", "on", "for", "over", "during", "at", "in", "by", "until", "like",
    "be", "please", "and", "or", "going", "gonna", "will", "is", "are", "was", "morning", "afternoon", "evening",
    "night", "weekend", "week", "weeks", "days", "day", "hour", "hours", "minute", "minutes", "second", "seconds",
    "moment", "bit", "while", "home", "here", "there", "outside", "out", "you", "me", "us", "it", "celsius",
    "fahrenheit", "degrees", "general", "noon", "midday", "midnight", "lunchtime", "lunch", "dinner", "breakfast",
    "work", "school", "uni", "university", "office", "am", "pm", "o'clock", "of", "rain", "raining", "rainy", "snow",
    "snowing", "showers", "sun", "sunshine", "wind", "windy", "storm", "storms", "thunder", "fog", "weather", "forecast",
    "temperature", "umbrella", "sunrise", "sunset", "degrees", "how", "what", "it", *alarms.WEEKDAYS,
    *alarms.NUMBER_WORDS,
}
# words before "weather" that aren't a place: "tell me the weather", "today's weather"
_NOT_PLACE_BEFORE = _NOT_PLACE | {
    "the", "a", "an", "my", "your", "our", "what's", "whats", "what", "how's", "hows", "how", "tell", "give",
    "show", "get", "check", "update", "refresh", "read", "say", "find", "look", "see", "know", "want", "need",
    "about", "current", "local", "latest", "weekly", "daily", "hourly", "week's", "weekend's", "full", "detailed",
    "quick", "good", "bad", "nice", "s", "any", "some", "real", "actual",
}
_POSSESSIVES = ("my", "your", "our", "his", "her", "their")


def place_in(command):
    """The place a weather or time question is about, or "" for home: "in London", "for Paris",
    "weather of Istanbul", "London weather". Times such as "in the morning" or "for tomorrow" aren't places."""
    words = command.split()
    for i, word in enumerate(words):
        if word not in ("in", "for", "at", "of"):
            continue
        name = []
        for following in words[i + 1:]:
            if following in _NOT_PLACE:
                break
            name.append(following)
        real = [w for w in name if w not in ("the", "a", "an")]
        if real and name[0] not in _POSSESSIVES and not any(ch.isdigit() for ch in "".join(name)):
            return " ".join(name)
    for i, word in enumerate(words):
        if word in ("weather", "forecast", "temperature"):
            name = []
            for before in reversed(words[:i]):
                if before in _NOT_PLACE_BEFORE or before in _POSSESSIVES or any(ch.isdigit() for ch in before):
                    break
                name.insert(0, before)
            return " ".join(name)
    return ""


def _topic(command):
    """What the question is about, if it's one thing: the sun, rain, an umbrella, snow or wind."""
    if re.search(r"\b(sunrise|sunset|sun (rise|set|come up|go down))\b", command):
        return "sunrise" if re.search(r"\b(sunrise|rise|come up)\b", command) else "sunset"
    if re.search(r"\bumbrella\b", command):
        return "umbrella"
    if re.search(r"\bsnow(ing|y|fall)?\b", command):
        return "snow"
    if re.search(r"\b(rain|raining|rains|rainy|showers?|drizzle|drizzling|pouring|wet)\b", command):
        return "rain"
    if re.search(r"\bwind(y|s)?\b", command):
        return "wind"
    return None


def _right_now(command):
    """'is it raining?' asks about now; 'will it rain?' and 'is it raining later?' don't."""
    return bool(re.search(r"\b(is it|it's|its|is there)( still)? (raining|snowing|drizzling|pouring|any rain)\b",
                          command)) and not re.search(r"\b(later|going|will|gonna)\b", command)


def _span(command, days):
    """(what to call it, the days it covers) for "the weekend" or "the next few days", or None."""
    if not days:
        return None
    if re.search(r"\bweekend\b", command):
        weekday = days[0]["date"].weekday()
        return "the weekend", [0] if weekday == 6 else [0, 1] if weekday == 5 else [5 - weekday, 6 - weekday]
    count = re.search(r"\bnext (\d+) days\b", alarms.words_to_numbers(command))
    if count and int(count.group(1)) > 0:
        return f"the next {int(count.group(1))} days", list(range(1, int(count.group(1)) + 1))
    if re.search(r"\b(few|couple of|coming) days\b", command):
        return "the next few days", [1, 2, 3]
    if re.search(r"\b(this|next|the|coming) week\b|\bweek ahead\b|\bweekly\b|\b(7|seven) ?-?day\b|\bweek's\b",
                 command):
        return "the next 7 days", list(range(7))
    return None


def _cap(text):
    return text[:1].upper() + text[1:]


def _chance(number, code=None):
    """'a 70 percent chance of rain' ('of snow' when snow is what's expected)."""
    return f"{a_or_an(number)} {number} percent chance of {'snow' if code in SNOW_CODES else 'rain'}"


def _degrees(forecast):
    low, high = forecast.get("low"), forecast.get("high")
    if low is None or high is None:
        return ""
    return f"around {high} degrees" if low == high else f"between {low} and {high} degrees"


def _when(offset, part, date):
    """'today', 'tonight', 'tomorrow morning', 'on Saturday afternoon'."""
    if offset == 0:
        return {None: "today", "morning": "this morning", "afternoon": "this afternoon",
                "evening": "this evening", "night": "tonight"}[part]
    if offset == 1:
        return "tomorrow" + (f" {part}" if part else "")
    day = f"{date:%A}" if offset < 7 else f"{date:%A} {date.day} {date:%B}"
    return f"on {day}" + (f" {part}" if part else "")


def _forecast(data, offset, part, rest_of_today=False):
    """One day's forecast, or the hours of it that `part` covers, as a dict like a day's."""
    day = data["days"][offset]
    now = data.get("now")
    if part is None and not rest_of_today:
        return day
    start, end = PARTS[part] if part else (0, 24)
    hours = [h for h in data.get("hours") or [] if h["time"].date() == day["date"] and start <= h["time"].hour < end]
    if now and day["date"] == now.date():
        hours = [h for h in hours if h["time"].hour >= now.hour] or hours  # what's still to come
    if not hours:
        return day
    temperatures = [h["temperature"] for h in hours if h["temperature"] is not None]
    rain = [h["rain"] for h in hours if h["rain"] is not None]
    codes = [h["code"] for h in hours if h["code"] is not None]
    code = max(codes) if codes else day.get("code")  # the worst of it, as Open-Meteo does for a day
    if part is None:  # the rest of today: the day's temperatures, the rain still to come
        return dict(day, rain=max(rain) if rain else day.get("rain"))
    return {"date": day["date"], "code": code, "sky": WEATHER_CODES.get(code, "changeable weather"),
            "high": max(temperatures) if temperatures else None, "low": min(temperatures) if temperatures else None,
            "rain": max(rain) if rain else None, "snow": 1 if any(c in SNOW_CODES for c in codes) else 0,
            "wind": day.get("wind")}


def _day_text(place, when, forecast):
    text = f"{_cap(when)} in {place}: {forecast['sky']}"
    if _degrees(forecast):
        text += f", {_degrees(forecast)}"
    if forecast.get("rain") == 0:
        text += ", and no rain expected"
    elif forecast.get("rain") is not None:
        text += f", with {_chance(forecast['rain'], forecast.get('code'))}"
    return text + "."


def _rain_text(place, when, forecast, umbrella):
    chance = forecast.get("rain")
    if chance is None:
        wet = forecast.get("code") in RAIN_CODES
        verdict = ("Yes, take one" if wet else "You shouldn't need one") if umbrella else ("Yes" if wet else "Probably not")
        return f"{verdict}: {forecast['sky']} in {place} {when}."
    if chance <= 5:
        verdict = "You won't need one." if umbrella else "No."
        return f"{verdict} There's almost no chance of rain in {place} {when}."
    if umbrella:
        verdict = "Yes, take one." if chance >= 50 else "Maybe take one to be safe." if chance >= 30 else \
            "You shouldn't need one."
    else:
        verdict = "Yes, probably." if chance >= 60 else "It might." if chance >= 30 else "Probably not."
    return f"{verdict} There's {'only ' if chance < 30 else ''}{_chance(chance)} in {place} {when}."


def _snow_text(place, when, forecast):
    expect = forecast["sky"] + (f", {_degrees(forecast)}" if _degrees(forecast) else "")
    if forecast.get("code") in SNOW_CODES or (forecast.get("snow") or 0) > 0:
        return f"Yes, snow is expected in {place} {when}: {expect}."
    return f"No snow is expected in {place} {when}. Expect {expect}."


def _wind_text(place, when, forecast):
    if forecast.get("wind") is None:
        return _day_text(place, when, forecast)
    return f"{_cap(when)} in {place}, the wind will reach {forecast['wind']} miles per hour."


def _sun_text(place, offset, day, topic):
    moment = day.get(topic)
    if moment is None:
        return f"I don't have the {topic} time for {place}."
    verb = {"sunrise": "rises", "sunset": "sets"}[topic]
    if offset == 0:
        return f"The sun {verb} in {place} at {alarms.clock(moment)} today."
    return f"{_cap(_when(offset, None, day['date']))}, the sun {verb} in {place} at {alarms.clock(moment)}."


def _now_text(data, topic):
    """Is it raining, snowing or windy right now?"""
    place = data["place"]
    if topic == "wind":
        if data.get("wind") is None:
            return weather_text(data)
        return f"The wind in {place} is {data['wind']} miles per hour right now."
    if topic == "snow":
        if data.get("code") in SNOW_CODES:
            return f"Yes, it's snowing in {place} right now: {data['sky']}."
        return f"No, it's not snowing in {place} right now. It's {data['temperature']} degrees with {data['sky']}."
    if data.get("code") in RAIN_CODES:
        return f"Yes, it's raining in {place} right now: {data['sky']}."
    if data.get("raining"):
        return f"Yes, it's raining a little in {place} right now."
    text = f"No, it's not raining in {place} right now. It's {data['temperature']} degrees with {data['sky']}."
    days = data.get("days") or []
    later = _forecast(data, 0, None, rest_of_today=True).get("rain") if days else None
    if later is not None and later >= 40:
        text += f" But there's {_chance(later)} later today."
    return text


def _outlook(data, label, offsets, topic):
    days, place = data["days"], data["place"]
    offsets = [i for i in offsets if 0 <= i < len(days)]
    if not offsets:
        return f"I can only see {len(days) - 1} days ahead."

    def name(i):
        return "Today" if i == 0 else "Tomorrow" if i == 1 else f"{days[i]['date']:%A}"

    if topic in ("rain", "umbrella"):
        chances = [f"{name(i)}, {days[i]['rain']} percent" for i in offsets if days[i]["rain"] is not None]
        if chances:
            return f"The chance of rain in {place} over {label}: " + "; ".join(chances) + "."
    if topic == "snow":
        snowy = [name(i) for i in offsets if days[i]["code"] in SNOW_CODES or (days[i].get("snow") or 0) > 0]
        if not snowy:
            return f"No snow is expected in {place} over {label}."
        return f"Snow is expected in {place} on " + " and ".join(snowy).replace("Today", "today").replace(
            "Tomorrow", "tomorrow") + "."
    lines = []
    for i in offsets:
        day = days[i]
        line = f"{name(i)}: {day['sky']}"
        if day["low"] is not None and day["high"] is not None:
            line += f", {day['low']} to {day['high']} degrees"
        if (day["rain"] or 0) >= 30:
            line += f", {day['rain']} percent chance of {'snow' if day['code'] in SNOW_CODES else 'rain'}"
        lines.append(line + ".")
    return f"Here's {label} in {place}. " + " ".join(lines)


def answer(data, command):
    """What to say to a weather question (a normalised command) about the place in `data`."""
    days, place = data.get("days") or [], data["place"]
    topic = _topic(command)
    span = _span(command, days)
    if span:
        return _outlook(data, span[0], span[1], topic)
    offset, _ = alarms.find_day(command, days[0]["date"]) if days else (None, "")
    part = alarms.find_part(command)
    if topic in ("sunrise", "sunset") and days:
        offset = offset or 0
        if not 0 <= offset < len(days):
            return f"I can only see {len(days) - 1} days ahead." if offset > 0 else "I can only look ahead."
        return _sun_text(place, offset, days[offset], topic)
    if offset is None and part is None:  # about now, or today
        if topic == "wind" or (topic in ("rain", "snow") and _right_now(command)):
            return _now_text(data, topic)
        if topic is None or not days:
            return weather_text(data)
        offset = 0
    offset = offset or 0  # "in the afternoon" is this afternoon
    if offset < 0:
        return "I can only tell you the weather from today on."
    if not days:
        return weather_text(data)
    if offset >= len(days):
        return f"I can only see {len(days) - 1} days ahead."
    if offset == 0 and part is None and topic is None:
        return weather_text(data)  # now, and the rest of today
    rest_of_today = offset == 0 and part is None
    forecast = _forecast(data, offset, part, rest_of_today)
    when = _when(offset, part, forecast["date"])
    if rest_of_today and data.get("now") and data["now"].hour >= 12:
        when = "for the rest of today"
    if topic in ("rain", "umbrella"):
        return _rain_text(place, when, forecast, topic == "umbrella")
    if topic == "snow":
        return _snow_text(place, when, forecast)
    if topic == "wind":
        return _wind_text(place, when, forecast)
    return _day_text(place, when, forecast)
