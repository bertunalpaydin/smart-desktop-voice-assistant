"""Small lookups on the web: YouTube, Wikipedia and the weather. No API keys needed."""
import html as html_lib
import http.client
import json
import math
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

USER_AGENT = "Jarvis/1.0 (personal desktop assistant)"
NEWS_FEED = "https://feeds.bbci.co.uk/news/rss.xml"
NEWS_FEEDS = [  # (outlet, section, address); a feed that doesn't answer is skipped
    ("BBC News", "Top stories", NEWS_FEED),
    ("BBC News", "World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC News", "UK", "https://feeds.bbci.co.uk/news/uk/rss.xml"),
    ("BBC News", "Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("BBC News", "Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("BBC News", "Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
    ("The Guardian", "Top stories", "https://www.theguardian.com/uk/rss"),
    ("Sky News", "Top stories", "https://feeds.skynews.com/feeds/rss/home.xml"),
]
SECTIONS = [("world", "World"), ("politics", "Politics"), ("business", "Business"), ("money", "Business"),
            ("technology", "Technology"), ("science", "Science"), ("environment", "Science"), ("health", "Health"),
            ("sport", "Sport"), ("football", "Sport"), ("entertainment", "Culture"), ("culture", "Culture"),
            ("uk", "UK")]
TIMEOUT = 8
RETRY_PAUSE = 1.5  # seconds before trying a request a second time

WEATHER_CODES = {
    0: "clear skies", 1: "mostly clear skies", 2: "partly cloudy skies", 3: "overcast skies", 45: "fog",
    48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    68: "light sleet", 69: "sleet",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "heavy showers", 83: "light sleet showers", 84: "sleet showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}
# MET Norway's weather symbols (without _day, _night or _polartwilight) as the codes above; any with
# thunder in the name are thunderstorms
MET_SYMBOLS = {
    "clearsky": 0, "fair": 1, "partlycloudy": 2, "cloudy": 3, "fog": 45,
    "lightrain": 61, "rain": 63, "heavyrain": 65,
    "lightrainshowers": 80, "rainshowers": 81, "heavyrainshowers": 82,
    "lightsleet": 68, "sleet": 69, "heavysleet": 69,
    "lightsleetshowers": 83, "sleetshowers": 84, "heavysleetshowers": 84,
    "lightsnow": 71, "snow": 73, "heavysnow": 75,
    "lightsnowshowers": 85, "snowshowers": 85, "heavysnowshowers": 86,
}
MET_URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
# MET Norway asks every app to say who it is and where to find out more
MET_USER_AGENT = "Jarvis/1.0 github.com/bertunalpaydin/smart-desktop-voice-assistant"
MPH = 2.236936  # miles per hour in a metre per second
# a last word that is a country, not part of the town's name: "Bristol UK", "Paris France"
COUNTRIES = {"uk", "england", "scotland", "wales", "britain", "ireland", "usa", "us", "america", "canada", "france",
             "germany", "spain", "italy", "portugal", "netherlands", "holland", "belgium", "turkey", "türkiye",
             "turkiye", "greece", "japan", "china", "india", "australia", "egypt", "brazil", "mexico", "russia"}


class WebError(Exception):
    """A request that failed. str() has the details for the log; `reason` says why in plain words."""

    def __init__(self, message, reason=None):
        super().__init__(message)
        self.reason = reason or message


def _cause(exc):
    """The error underneath a URLError ("<urlopen error timed out>" is a timeout)."""
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError) \
            and isinstance(exc.reason, BaseException):
        return exc.reason
    return exc


def reason(exc):
    """Why a request failed, in a few plain words, for the HUD and the spoken replies."""
    cause = _cause(exc)
    if isinstance(cause, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return "the connection's security certificate wasn't trusted"
    if isinstance(cause, TimeoutError) or "timed out" in str(exc):
        return "it took too long to answer"
    if isinstance(cause, socket.gaierror):
        return "there's no internet connection"
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return "it's had too many requests just now"
        if exc.code >= 500:
            return "the service is having problems"
        return f"it refused the request (error {exc.code})"
    if isinstance(cause, ConnectionError):
        return "the connection dropped"
    return "it didn't answer"


def _worth_retrying(exc):
    """A timeout, a dropped connection or a busy service may work a moment later; a refused
    request or an untrusted certificate won't."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    if isinstance(_cause(exc), ssl.SSLError) or isinstance(exc, ValueError):
        return False
    return True


def _get(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8", errors="replace")
        except (OSError, ValueError, http.client.HTTPException) as exc:  # URLError is an OSError
            if attempt == 1 and _worth_retrying(exc):
                time.sleep(RETRY_PAUSE)
                continue
            host = urllib.parse.urlsplit(url).hostname or url
            raise WebError(f"{host}: {exc}", reason(exc)) from exc


def _get_json(url, headers=None):
    try:
        return json.loads(_get(url, headers))
    except ValueError as exc:
        raise WebError("unexpected reply", "it sent a reply Jarvis couldn't read") from exc


def google_url(query):
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)


def youtube_search_url(query):
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)


def youtube_first_video(query):
    """The address of the first video YouTube finds, or None."""
    page = _get(youtube_search_url(query), {"Accept-Language": "en-GB,en;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"})
    match = re.search(r'"videoId":"([\w-]{11})"', page) or re.search(r"watch\?v=([\w-]{11})", page)
    return f"https://www.youtube.com/watch?v={match.group(1)}" if match else None


def wikipedia_summary(query, sentences=2):
    """The first sentences of the best matching Wikipedia article, or None."""
    found = _get_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "opensearch", "search": query, "limit": 1, "namespace": 0, "format": "json"}))
    titles = found[1] if isinstance(found, list) and len(found) > 1 else []
    if not titles:
        return None
    title = titles[0]
    page = _get_json("https://en.wikipedia.org/api/rest_v1/page/summary/"
                     + urllib.parse.quote(title.replace(" ", "_"), safe=""))
    if page.get("type") == "disambiguation":
        return f"{title} can mean several things. Try being more specific."
    extract = " ".join(page.get("extract", "").split())
    if not extract:
        return None
    return " ".join(re.split(r"(?<=[.!?])\s+", extract)[:sentences])


FORECAST_DAYS = 8  # today and the 7 days after it, so "next Friday" works on a Friday


_PLACES = {}  # places found before, so a busy gazetteer doesn't stop the weather


def _place(city):
    """The best match for a place name from Open-Meteo's gazetteer, or None. "Bristol UK" is
    looked up as "Bristol" if nothing has the whole name. A place found once is remembered."""
    key = " ".join(city.casefold().split())
    if key in _PLACES:
        return _PLACES[key]
    place = _look_up(city)
    if place is not None:
        _PLACES[key] = place
    return place


def _look_up(city):
    words, names = city.split(), [city]
    if len(words) > 1 and words[-1].casefold() in COUNTRIES:
        rest = words[:-1]
        while rest and rest[0].casefold() in ("the", "a", "an"):
            rest = rest[1:]
        if rest:
            names.append(" ".join(rest))
    for name in names:
        places = _get_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
            {"name": name, "count": 1, "language": "en", "format": "json"}))
        results = places.get("results") or []
        if results:
            return results[0]
    return None


def _round(value):
    return None if value is None else round(value)


def weather_data(city):
    """The weather in the city right now, today and the next days (by day and by hour), or None if
    there's no such place. Days and hours are the place's own, so "tomorrow" in Tokyo is Tokyo's.
    It comes from Open-Meteo, or from MET Norway when Open-Meteo doesn't answer."""
    place = _place(city)
    if place is None:
        return None
    try:
        return _open_meteo(place)
    except WebError as first:
        try:
            data = _met_norway(place)
        except WebError as second:
            raise WebError(f"{first}; MET Norway: {second}", first.reason) from first
        print(f"Open-Meteo didn't answer ({first}); the weather came from MET Norway.", file=sys.stderr, flush=True)
        return data


def _open_meteo(place):
    data = _get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "snowfall_sum,wind_speed_10m_max,sunrise,sunset",
        "hourly": "temperature_2m,weather_code,precipitation_probability",
        "wind_speed_unit": "mph",
        "timezone": "auto",
        "forecast_days": FORECAST_DAYS,
    }))
    current, daily, hourly = data.get("current") or {}, data.get("daily") or {}, data.get("hourly") or {}
    if "temperature_2m" not in current:
        raise WebError("no weather data", "the weather service sent no data")

    def column(table, key, i):
        values = table.get(key) or []
        return values[i] if i < len(values) else None

    def moment(table, key, i):
        try:
            return datetime.fromisoformat(column(table, key, i))
        except (TypeError, ValueError):
            return None

    days = []
    for i, day in enumerate(daily.get("time") or []):
        try:
            date = datetime.fromisoformat(day).date()
        except (TypeError, ValueError):
            continue
        code = column(daily, "weather_code", i)
        days.append({"date": date, "code": code, "sky": WEATHER_CODES.get(code, "changeable weather"),
                     "high": _round(column(daily, "temperature_2m_max", i)),
                     "low": _round(column(daily, "temperature_2m_min", i)),
                     "rain": column(daily, "precipitation_probability_max", i),
                     "snow": column(daily, "snowfall_sum", i), "wind": _round(column(daily, "wind_speed_10m_max", i)),
                     "sunrise": moment(daily, "sunrise", i), "sunset": moment(daily, "sunset", i)})
    hours = []
    for i in range(len(hourly.get("time") or [])):
        when = moment(hourly, "time", i)
        if when is None:
            continue
        code = column(hourly, "weather_code", i)
        hours.append({"time": when, "code": code, "temperature": _round(column(hourly, "temperature_2m", i)),
                      "rain": column(hourly, "precipitation_probability", i)})
    try:
        now = datetime.fromisoformat(current["time"])
    except (KeyError, TypeError, ValueError):
        now = None
    today = days[0] if days else {}
    return {
        "place": place["name"],
        "temperature": round(current["temperature_2m"]),
        "sky": WEATHER_CODES.get(current.get("weather_code"), "changeable weather"),
        "high": today.get("high"),
        "low": today.get("low"),
        "rain": today.get("rain"),
        "code": current.get("weather_code"),
        "raining": (current.get("precipitation") or 0) > 0,
        "wind": _round(current.get("wind_speed_10m")),
        "now": now,  # the time at the place
        "days": days,
        "hours": hours,
        "source": "Open-Meteo",
    }


def met_code(symbol):
    """The weather code for one of MET Norway's symbols ("lightrain_day"), or None."""
    if not symbol:
        return None
    name = symbol.split("_")[0]
    return 95 if "thunder" in name else MET_SYMBOLS.get(name)


def sun_times(day, latitude, longitude, zone):
    """(sunrise, sunset) on `day` at a place, in its own time and without a time zone, or
    (None, None) when the sun doesn't rise or set that day. NOAA's solar calculator, right to about a minute."""
    lat = math.radians(latitude)
    julian_midnight = day.toordinal() + 1721424.5  # the Julian day at 0:00 UTC

    def minutes_utc(sign):  # sunrise (+1) or sunset (-1), in minutes after midnight UTC
        minutes = 720
        for _ in range(3):  # where the sun is at that moment, not at noon
            t = (julian_midnight + minutes / 1440 - 2451545) / 36525  # Julian centuries since 2000
            mean_longitude = math.radians((280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360)
            anomaly = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
            eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
            centre = (math.sin(anomaly) * (1.914602 - t * (0.004817 + 0.000014 * t))
                      + math.sin(2 * anomaly) * (0.019993 - 0.000101 * t) + math.sin(3 * anomaly) * 0.000289)
            omega = math.radians(125.04 - 1934.136 * t)
            apparent = math.radians(math.degrees(mean_longitude) + centre - 0.00569 - 0.00478 * math.sin(omega))
            obliquity = math.radians(23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
                                     + 0.00256 * math.cos(omega))
            declination = math.asin(math.sin(obliquity) * math.sin(apparent))
            y = math.tan(obliquity / 2) ** 2
            equation = 4 * math.degrees(
                y * math.sin(2 * mean_longitude) - 2 * eccentricity * math.sin(anomaly)
                + 4 * eccentricity * y * math.sin(anomaly) * math.cos(2 * mean_longitude)
                - 0.5 * y * y * math.sin(4 * mean_longitude) - 1.25 * eccentricity ** 2 * math.sin(2 * anomaly))
            cos_hour = math.cos(math.radians(90.833)) / (math.cos(lat) * math.cos(declination)) \
                - math.tan(lat) * math.tan(declination)
            if not -1 <= cos_hour <= 1:  # the midnight sun, or the polar night
                return None
            minutes = 720 - 4 * (longitude + sign * math.degrees(math.acos(cos_hour))) - equation
        return minutes

    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    rise, set_ = minutes_utc(1), minutes_utc(-1)
    if rise is None or set_ is None:
        return None, None
    return tuple((midnight + timedelta(minutes=round(m))).astimezone(zone).replace(tzinfo=None) for m in (rise, set_))


def _met_norway(place):
    """The same as _open_meteo(), from MET Norway's forecast (in UTC, with symbols; no snowfall)."""
    try:
        zone = zoneinfo.ZoneInfo(place["timezone"])
    except (KeyError, TypeError, ValueError, OSError) as exc:  # no time zone database (Windows)
        raise WebError(f"no time zone for {place.get('name')}", "there's no time zone for that place") from exc
    query = {"lat": round(place["latitude"], 4), "lon": round(place["longitude"], 4)}
    if isinstance(place.get("elevation"), (int, float)):
        query["altitude"] = round(place["elevation"])
    data = _get_json(MET_URL + "?" + urllib.parse.urlencode(query), {"User-Agent": MET_USER_AGENT})
    series = ((data.get("properties") or {}) if isinstance(data, dict) else {}).get("timeseries") or []

    entries = []  # (local time, now's details, the period that starts then)
    for entry in series:
        try:
            when = datetime.fromisoformat(entry["time"].replace("Z", "+00:00")).astimezone(zone).replace(tzinfo=None)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        values = entry.get("data") or {}
        instant = (values.get("instant") or {}).get("details") or {}
        period = values.get("next_1_hours") or values.get("next_6_hours") or values.get("next_12_hours") or {}
        entries.append((when, instant, period, values.get("next_6_hours") or {}))
    if not entries or not isinstance(entries[0][1].get("air_temperature"), (int, float)):
        raise WebError("no weather data", "the weather service sent no data")

    def code(period):
        return met_code((period.get("summary") or {}).get("symbol_code"))

    def chance(period):
        return _round((period.get("details") or {}).get("probability_of_precipitation"))

    def mph(details):
        speed = details.get("wind_speed")
        return None if speed is None else speed * MPH

    hours = [{"time": when, "code": code(period), "temperature": _round(instant.get("air_temperature")),
              "rain": chance(period)} for when, instant, period, _ in entries]
    days = []
    for date in sorted({when.date() for when, *_ in entries})[:FORECAST_DAYS]:
        same = [e for e in entries if e[0].date() == date]
        temperatures = [i.get("air_temperature") for _, i, _, _ in same]
        temperatures += [(six.get("details") or {}).get(key) for *_, six in same
                         for key in ("air_temperature_max", "air_temperature_min")]
        temperatures = [t for t in temperatures if isinstance(t, (int, float))]
        codes = [c for c in (code(p) for _, _, p, _ in same) if c is not None]
        chances = [c for c in (chance(p) for _, _, p, _ in same) if c is not None]
        winds = [w for w in (mph(i) for _, i, _, _ in same) if w is not None]
        sunrise, sunset = sun_times(date, place["latitude"], place["longitude"], zone)
        day_code = max(codes) if codes else None
        days.append({"date": date, "code": day_code, "sky": WEATHER_CODES.get(day_code, "changeable weather"),
                     "high": _round(max(temperatures)) if temperatures else None,
                     "low": _round(min(temperatures)) if temperatures else None,
                     "rain": max(chances) if chances else None, "snow": None,
                     "wind": _round(max(winds)) if winds else None, "sunrise": sunrise, "sunset": sunset})

    first_time, now_details, now_period, _ = entries[0]
    current = code(now_period)
    today = days[0] if days else {}
    return {
        "place": place["name"],
        "temperature": round(now_details["air_temperature"]),
        "sky": WEATHER_CODES.get(current, "changeable weather"),
        "high": today.get("high"),
        "low": today.get("low"),
        "rain": today.get("rain"),
        "code": current,
        "raining": ((now_period.get("details") or {}).get("precipitation_amount") or 0) > 0,
        "wind": _round(mph(now_details)),
        "now": datetime.now(zone).replace(tzinfo=None, second=0, microsecond=0),
        "days": days,
        "hours": hours,
        "source": "MET Norway",
    }


def place_time(city, now=None):
    """(the place's name, the time there as a datetime without a time zone), or None if there's no
    such place. `now` is the current time in UTC (for tests)."""
    place = _place(city)
    if place is None:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        zone = zoneinfo.ZoneInfo(place["timezone"])
    except (KeyError, TypeError, ValueError, OSError):  # no zone, or no time zone database (Windows)
        data = _get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": place["latitude"], "longitude": place["longitude"],
            "current": "temperature_2m", "timezone": "auto", "forecast_days": 1}))
        offset = data.get("utc_offset_seconds")
        if not isinstance(offset, (int, float)):
            raise WebError("no time zone for that place", "there's no time zone for that place")
        return place["name"], (now + timedelta(seconds=offset)).replace(tzinfo=None)
    return place["name"], now.astimezone(zone).replace(tzinfo=None)


def a_or_an(number):
    """'an 80 percent chance', 'a 70 percent chance': the article for a number read aloud."""
    number = abs(int(number))
    return "an" if number in (8, 11, 18) or 80 <= number <= 89 or 800 <= number <= 899 else "a"


def weather_text(data):
    text = f"Right now in {data['place']} it's {data['temperature']} degrees with {data['sky']}."
    if data["high"] is not None and data["low"] is not None:
        text += f" Today's high is {data['high']} and the low is {data['low']}."
    if data["rain"] == 0:
        text += " No rain is expected."
    elif data["rain"] is not None:
        text += f" There's {a_or_an(data['rain'])} {data['rain']} percent chance of rain."
    return text


def weather(city):
    """A sentence about the weather in the city right now and today, or None if there's no such place."""
    data = weather_data(city)
    return weather_text(data) if data else None


def news(count=5):
    """The top headlines from BBC News."""
    try:
        feed = ElementTree.fromstring(_get(NEWS_FEED).encode("utf-8"))
    except ElementTree.ParseError as exc:
        raise WebError("unexpected reply", "it sent a reply Jarvis couldn't read") from exc
    titles = [" ".join((item.findtext("title") or "").split()) for item in feed.iter("item")]
    return [title for title in titles if title][:count]


@dataclass(frozen=True)
class NewsItem:
    title: str
    summary: str
    link: str
    section: str
    source: str
    rank: int = 0  # its place in its feed, 0 = the top
    published: datetime | None = None


def _plain(text):
    text = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())
    return re.sub(r"\s+([.,;:!?])", r"\1", text)  # "flooded ." -> "flooded."


def _text(item, tag):
    """All the text in a feed element, whether its HTML came escaped or as raw tags."""
    element = item.find(tag)
    return "".join(element.itertext()) if element is not None else ""


def _section(link, default):
    path = urllib.parse.urlparse(link).path.casefold()
    for key, name in SECTIONS:
        if re.search(rf"/{key}([/-]|$)", path):
            return name
    return "News" if default == "Top stories" else default


def _feed(source, section, url, limit):
    try:
        root = ElementTree.fromstring(_get(url).encode("utf-8"))
    except ElementTree.ParseError:
        return []
    items = []
    for rank, item in enumerate(root.iter("item")):
        title = _plain(_text(item, "title"))
        if not title:
            continue
        link = (item.findtext("link") or "").strip()
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError, IndexError):
            published = None
        items.append(NewsItem(title, _plain(_text(item, "description")), link, _section(link, section), source,
                              rank, published))
        if len(items) >= limit:
            break
    return items


def news_items(feeds=None, per_feed=15):
    """Today's stories from several outlets, without duplicates. Feeds are read at the same time."""
    feeds = NEWS_FEEDS if feeds is None else feeds
    errors = []

    def read(feed):  # a feed that doesn't answer is skipped
        try:
            return _feed(*feed, per_feed)
        except WebError as exc:
            errors.append(exc)
            return []

    with ThreadPoolExecutor(max_workers=max(1, len(feeds))) as pool:
        results = list(pool.map(read, feeds))
    items, seen = [], set()
    for item in (item for result in results for item in result):
        key = item.link or item.title.casefold()
        if key not in seen:
            seen.add(key)
            items.append(item)
    if not items:
        raise WebError("no news feed answered" + "".join(f"; {e}" for e in errors),
                       errors[0].reason if errors else "none of the news feeds answered")
    return items
