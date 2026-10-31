"""Small lookups on the web: YouTube, Wikipedia and the weather. No API keys needed."""
import html as html_lib
import http.client
import json
import re
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

WEATHER_CODES = {
    0: "clear skies", 1: "mostly clear skies", 2: "partly cloudy skies", 3: "overcast skies", 45: "fog",
    48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
    96: "thunderstorms with hail", 99: "thunderstorms with hail",
}
# a last word that is a country, not part of the town's name: "Bristol UK", "Paris France"
COUNTRIES = {"uk", "england", "scotland", "wales", "britain", "ireland", "usa", "us", "america", "canada", "france",
             "germany", "spain", "italy", "portugal", "netherlands", "holland", "belgium", "turkey", "türkiye",
             "turkiye", "greece", "japan", "china", "india", "australia", "egypt", "brazil", "mexico", "russia"}


class WebError(Exception):
    pass


def _get(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read().decode("utf-8", errors="replace")
    except (OSError, ValueError, http.client.HTTPException) as exc:  # URLError is an OSError
        raise WebError(str(exc)) from exc


def _get_json(url):
    try:
        return json.loads(_get(url))
    except ValueError as exc:
        raise WebError("unexpected reply") from exc


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


def _place(city):
    """The best match for a place name from Open-Meteo's gazetteer, or None. "Bristol UK" is
    looked up as "Bristol" if nothing has the whole name."""
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
    there's no such place. Days and hours are the place's own, so "tomorrow" in Tokyo is Tokyo's."""
    place = _place(city)
    if place is None:
        return None
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
        raise WebError("no weather data")

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
            raise WebError("no time zone for that place")
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
        raise WebError("unexpected reply") from exc
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
    except (WebError, ElementTree.ParseError):
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
    with ThreadPoolExecutor(max_workers=max(1, len(feeds))) as pool:
        results = list(pool.map(lambda feed: _feed(*feed, per_feed), feeds))
    items, seen = [], set()
    for item in (item for result in results for item in result):
        key = item.link or item.title.casefold()
        if key not in seen:
            seen.add(key)
            items.append(item)
    if not items:
        raise WebError("no news feed answered")
    return items
