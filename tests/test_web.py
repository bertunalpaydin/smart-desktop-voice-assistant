import json
import socket
import ssl
import urllib.error
from datetime import date, datetime, timedelta, timezone

import pytest

from jarvis import web


def fake_pages(monkeypatch, pages):
    """Answer requests from `pages`: {start of the URL: text or dict}."""
    asked = []

    def get(url, headers=None):
        asked.append((url, headers))
        for start, page in pages.items():
            if url.startswith(start):
                return page if isinstance(page, str) else json.dumps(page)
        raise web.WebError(f"no page for {url}")

    monkeypatch.setattr(web, "_get", get)
    return asked


def test_first_youtube_video(monkeypatch):
    asked = fake_pages(monkeypatch, {"https://www.youtube.com/results": 'x "videoId":"dQw4w9WgXcQ" y "videoId":"zzzzzzzzzzz"'})
    assert web.youtube_first_video("never gonna") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert asked[0][0].endswith("search_query=never+gonna")
    assert "CONSENT" in asked[0][1]["Cookie"]


def test_no_youtube_video_found(monkeypatch):
    fake_pages(monkeypatch, {"https://www.youtube.com/results": "<html>consent page</html>"})
    assert web.youtube_first_video("x") is None


def test_wikipedia_summary_is_the_first_two_sentences(monkeypatch):
    fake_pages(monkeypatch, {
        "https://en.wikipedia.org/w/api.php": ["alan turing", ["Alan Turing"], [""], ["https://en.wikipedia.org/wiki/Alan_Turing"]],
        "https://en.wikipedia.org/api/rest_v1/page/summary/Alan_Turing": {
            "type": "standard",
            "extract": "Alan Turing was a mathematician. He was a computer scientist. He was born in 1912.",
        },
    })
    assert web.wikipedia_summary("alan turing") == "Alan Turing was a mathematician. He was a computer scientist."


def test_wikipedia_disambiguation_and_nothing_found(monkeypatch):
    fake_pages(monkeypatch, {
        "https://en.wikipedia.org/w/api.php?action=opensearch&search=mercury": ["mercury", ["Mercury"], [], []],
        "https://en.wikipedia.org/w/api.php?action=opensearch&search=xyzzy": ["xyzzy", [], [], []],
        "https://en.wikipedia.org/api/rest_v1/page/summary/Mercury": {"type": "disambiguation", "extract": "..."},
    })
    assert "several things" in web.wikipedia_summary("mercury")
    assert web.wikipedia_summary("xyzzy") is None


FORECAST = {
    "current": {"time": "2026-10-01T14:00", "temperature_2m": 13.6, "weather_code": 61, "wind_speed_10m": 20.1,
                "precipitation": 0.4},
    "daily": {"time": ["2026-10-01", "2026-10-02"], "weather_code": [61, 3],
              "temperature_2m_max": [15.2, 12.6], "temperature_2m_min": [8.9, 7.1],
              "precipitation_probability_max": [70, 80], "snowfall_sum": [0, 0], "wind_speed_10m_max": [24.4, 12],
              "sunrise": ["2026-10-01T07:12", "2026-10-02T07:14"], "sunset": ["2026-10-01T18:41", None]},
    "hourly": {"time": ["2026-10-01T00:00", "2026-10-01T01:00", "not a time"], "temperature_2m": [9.4, 9.0, 1],
               "weather_code": [3, 61, 0], "precipitation_probability": [20, 35, 0]},
}


def test_weather_sentence(monkeypatch):
    asked = fake_pages(monkeypatch, {
        "https://geocoding-api.open-meteo.com/": {"results": [{"name": "Bristol", "latitude": 51.45, "longitude": -2.59}]},
        "https://api.open-meteo.com/v1/forecast": FORECAST,
    })
    assert web.weather("Bristol") == (
        "Right now in Bristol it's 14 degrees with light rain. "
        "Today's high is 15 and the low is 9. There's a 70 percent chance of rain."
    )
    forecast_url = asked[1][0]
    assert "latitude=51.45" in forecast_url and "current=temperature_2m" in forecast_url
    assert "forecast_days=8" in forecast_url and "wind_speed_unit=mph" in forecast_url


def test_weather_by_day_and_by_hour(monkeypatch):
    fake_pages(monkeypatch, {
        "https://geocoding-api.open-meteo.com/": {"results": [{"name": "Bristol", "latitude": 51.45, "longitude": -2.59}]},
        "https://api.open-meteo.com/v1/forecast": FORECAST,
    })
    data = web.weather_data("Bristol")
    assert (data["now"], data["code"], data["raining"], data["wind"]) == (datetime(2026, 10, 1, 14, 0), 61, True, 20)
    assert data["days"][1] == {"date": date(2026, 10, 2), "code": 3, "sky": "overcast skies", "high": 13, "low": 7,
                               "rain": 80, "snow": 0, "wind": 12, "sunrise": datetime(2026, 10, 2, 7, 14),
                               "sunset": None}
    assert data["days"][0]["sunset"] == datetime(2026, 10, 1, 18, 41)
    assert data["hours"] == [  # the broken time is left out
        {"time": datetime(2026, 10, 1, 0, 0), "code": 3, "temperature": 9, "rain": 20},
        {"time": datetime(2026, 10, 1, 1, 0), "code": 61, "temperature": 9, "rain": 35},
    ]


def test_a_country_after_the_town(monkeypatch):
    asked = fake_pages(monkeypatch, {
        "https://geocoding-api.open-meteo.com/v1/search?name=Bristol+UK": {},
        "https://geocoding-api.open-meteo.com/v1/search?name=Bristol&": {"results": [{"name": "Bristol"}]},
        "https://geocoding-api.open-meteo.com/v1/search?name=the+uk": {},
    })
    assert web._place("Bristol UK") == {"name": "Bristol"}
    assert web._place("the uk") is None  # "the" alone isn't looked up
    assert len(asked) == 3


def test_the_time_in_a_place(monkeypatch):
    fake_pages(monkeypatch, {
        "https://geocoding-api.open-meteo.com/v1/search?name=Tokyo": {"results": [{"name": "Tokyo", "latitude": 35.7,
                                                                                    "longitude": 139.7,
                                                                                    "timezone": "Asia/Tokyo"}]},
        "https://geocoding-api.open-meteo.com/v1/search?name=Nowhere": {},
        "https://geocoding-api.open-meteo.com/v1/search?name=Oddplace": {"results": [{"name": "Oddplace", "latitude": 1,
                                                                                       "longitude": 2}]},
        "https://api.open-meteo.com/v1/forecast": {"utc_offset_seconds": -18000},
    })
    zones = []

    def zone(key):  # no time zone files needed
        zones.append(key)
        return timezone(timedelta(hours=9))

    monkeypatch.setattr(web.zoneinfo, "ZoneInfo", zone)
    now = datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)
    assert web.place_time("Tokyo", now) == ("Tokyo", datetime(2026, 10, 1, 22, 0))
    assert zones == ["Asia/Tokyo"]
    assert web.place_time("Oddplace", now) == ("Oddplace", datetime(2026, 10, 1, 8, 0))  # no zone: Open-Meteo's offset
    assert web.place_time("Nowhere", now) is None


@pytest.mark.parametrize("number, article", [(8, "an"), (11, "an"), (18, "an"), (80, "an"), (89, "an"), (1, "a"),
                                             (70, "a"), (100, "a"), (0, "a"), (90, "a")])
def test_a_or_an(number, article):
    assert web.a_or_an(number) == article


def test_weather_for_an_unknown_place_or_without_data(monkeypatch):
    fake_pages(monkeypatch, {
        "https://geocoding-api.open-meteo.com/v1/search?name=Nowhere": {},
        "https://geocoding-api.open-meteo.com/v1/search?name=Bath": {"results": [{"name": "Bath", "latitude": 1, "longitude": 2}]},
        "https://api.open-meteo.com/v1/forecast": {"current": {}},
    })
    assert web.weather("Nowhere") is None
    with pytest.raises(web.WebError):
        web.weather("Bath")


def test_network_errors_and_bad_replies_become_web_errors(monkeypatch):
    def broken(request, timeout):
        raise urllib.error.URLError("no internet")

    monkeypatch.setattr(web.urllib.request, "urlopen", broken)
    with pytest.raises(web.WebError, match="no internet"):
        web.wikipedia_summary("x")
    monkeypatch.setattr(web, "_get", lambda url, headers=None: "not json")
    with pytest.raises(web.WebError):
        web.weather("x")


def test_search_addresses():
    assert web.google_url("python & qt") == "https://www.google.com/search?q=python+%26+qt"
    assert web.youtube_search_url("lofi beats") == "https://www.youtube.com/results?search_query=lofi+beats"


def test_news_headlines(monkeypatch):
    feed = """<?xml version="1.0" encoding="UTF-8"?><rss><channel><title>BBC News</title>
      <item><title><![CDATA[Storm hits the coast]]></title></item>
      <item><title>  Markets   rise </title></item><item><title></title></item>
      <item><title>Third</title></item></channel></rss>"""
    fake_pages(monkeypatch, {web.NEWS_FEED: feed})
    assert web.news(count=2) == ["Storm hits the coast", "Markets rise"]
    fake_pages(monkeypatch, {web.NEWS_FEED: "<html>not a feed"})
    with pytest.raises(web.WebError):
        web.news()


def test_weather_as_data(monkeypatch):
    fake_pages(monkeypatch, {
        "https://geocoding-api.open-meteo.com/": {"results": [{"name": "Bristol", "latitude": 51.45, "longitude": -2.59}]},
        "https://api.open-meteo.com/v1/forecast": {"current": {"temperature_2m": 13.6, "weather_code": 0}, "daily": {}},
    })
    data = web.weather_data("Bristol")
    assert data == {"place": "Bristol", "temperature": 14, "sky": web.WEATHER_CODES[0], "high": None, "low": None,
                    "rain": None, "code": 0, "raining": False, "wind": None, "now": None, "days": [], "hours": [],
                    "source": "Open-Meteo"}
    assert web.weather_text(data) == f"Right now in Bristol it's 14 degrees with {web.WEATHER_CODES[0]}."
    assert web.weather_text(dict(data, rain=80)).endswith("There's an 80 percent chance of rain.")


def rss(*items):
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link><description>{text}</description>"
        f"<pubDate>Wed, 07 Oct 2026 08:30:00 GMT</pubDate></item>" for title, link, text in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><rss><channel><title>Feed</title>{body}</channel></rss>'


def test_news_from_several_outlets(monkeypatch):
    feeds = [("BBC News", "Top stories", "https://bbc/top"), ("BBC News", "Technology", "https://bbc/tech"),
             ("Sky News", "Top stories", "https://sky/home"), ("Broken", "Top stories", "https://broken/feed"),
             ("Garbled", "Top stories", "https://garbled/feed")]
    fake_pages(monkeypatch, {
        "https://bbc/top": rss(("Storm hits &amp; floods", "https://www.bbc.co.uk/news/uk-1",
                                "<![CDATA[<p>Homes <b>flooded</b>.</p>]]>"),
                               ("New phone out", "https://www.bbc.co.uk/news/technology-2", "")),
        "https://bbc/tech": rss(("New phone out", "https://www.bbc.co.uk/news/technology-2", "<p>Raw <b>tags</b> too</p>"),
                                ("Chip makers grow", "https://www.bbc.co.uk/news/technology-3", "Sales up.")),
        "https://sky/home": rss(("Storm floods homes", "https://news.sky.com/story/storm-4", "Rain."),
                                ("  ", "https://news.sky.com/story/blank", "")),
        "https://garbled/feed": "<rss><channel><item>",
    })
    items = web.news_items(feeds)
    assert [(i.source, i.title, i.section, i.rank) for i in items] == [
        ("BBC News", "Storm hits & floods", "UK", 0),
        ("BBC News", "New phone out", "Technology", 1),
        ("BBC News", "Chip makers grow", "Technology", 1),
        ("Sky News", "Storm floods homes", "News", 0),
    ]
    assert items[0].summary == "Homes flooded." and items[0].published.year == 2026


def test_news_feed_limits_and_total_failure(monkeypatch):
    fake_pages(monkeypatch, {"https://feed": rss(*[(f"Story {n}", f"https://x/{n}", "") for n in range(5)])})
    assert len(web.news_items([("Outlet", "World", "https://feed")], per_feed=3)) == 3
    with pytest.raises(web.WebError):
        web.news_items([("Outlet", "World", "https://nothing")])


def test_when_no_feed_answers_the_error_says_why(monkeypatch):
    def get(url, headers=None):
        raise web.WebError(f"{url}: <urlopen error timed out>", "it took too long to answer")

    monkeypatch.setattr(web, "_get", get)
    with pytest.raises(web.WebError) as error:
        web.news_items([("A", "World", "https://a"), ("B", "World", "https://b")])
    assert error.value.reason == "it took too long to answer"
    assert "https://a: <urlopen error timed out>" in str(error.value) and "https://b" in str(error.value)


class Answer:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.text.encode()


def flaky(monkeypatch, *outcomes):
    """urlopen gives `outcomes` in turn: an exception is raised, text is the page. Returns the calls."""
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        outcome = outcomes[min(len(calls), len(outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return Answer(outcome)

    monkeypatch.setattr(web.urllib.request, "urlopen", urlopen)
    return calls


def http_error(code):
    return urllib.error.HTTPError("https://api.open-meteo.com/v1/forecast", code, "error", {}, None)


def test_a_request_that_times_out_is_tried_once_more(monkeypatch):
    calls = flaky(monkeypatch, urllib.error.URLError(TimeoutError("timed out")), "page")
    assert web._get("https://api.open-meteo.com/v1/forecast") == "page"
    assert len(calls) == 2


@pytest.mark.parametrize("error, tries, reason", [
    (urllib.error.URLError(TimeoutError("timed out")), 2, "it took too long to answer"),
    (TimeoutError("The read operation timed out"), 2, "it took too long to answer"),
    (urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")), 2, "there's no internet connection"),
    (urllib.error.URLError(ConnectionResetError(54, "Connection reset by peer")), 2, "the connection dropped"),
    (http_error(503), 2, "the service is having problems"),
    (http_error(429), 2, "it's had too many requests just now"),
    (http_error(400), 1, "it refused the request (error 400)"),
    (urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed: unable to get local issuer "
                                                            "certificate")), 1,
     "the connection's security certificate wasn't trusted"),
    (urllib.error.URLError("no internet"), 2, "it didn't answer"),
])
def test_failed_requests_say_why(monkeypatch, error, tries, reason):
    calls = flaky(monkeypatch, error)
    with pytest.raises(web.WebError) as failed:
        web._get("https://api.open-meteo.com/v1/forecast?x=1")
    assert len(calls) == tries
    assert failed.value.reason == reason
    assert str(failed.value).startswith("api.open-meteo.com: ")  # the details, for the log


def test_bad_replies_say_why(monkeypatch):
    monkeypatch.setattr(web, "_get", lambda url, headers=None: "<html>")
    with pytest.raises(web.WebError) as failed:
        web.weather_data("Bristol")
    assert failed.value.reason == "it sent a reply Jarvis couldn't read"


@pytest.mark.parametrize("link, default, section", [
    ("https://www.bbc.co.uk/news/world-europe-1", "Top stories", "World"),
    ("https://www.theguardian.com/politics/2026/oct/07/x", "Top stories", "Politics"),
    ("https://www.theguardian.com/uk-news/2026/x", "Top stories", "UK"),
    ("https://www.bbc.co.uk/sport/football/1", "Top stories", "Sport"),
    ("https://example.com/story", "Top stories", "News"),
    ("https://example.com/story", "Science", "Science"),
])
def test_news_sections_come_from_the_address(link, default, section):
    assert web._section(link, default) == section


BRISTOL = {"name": "Bristol", "latitude": 51.45523, "longitude": -2.59661, "elevation": 11.0,
           "timezone": "Europe/London"}


def met_sample(start=datetime(2026, 11, 2, 15, tzinfo=timezone.utc)):
    """MET Norway's forecast as it comes: hourly for two days, then every 6 hours, in UTC."""
    series = []
    for n in range(48):
        when = start + timedelta(hours=n)
        symbol = "lightrain_day" if when.date() == date(2026, 11, 3) and 9 <= when.hour < 15 else "cloudy"
        series.append({"time": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": {
            "instant": {"details": {"air_temperature": 8.0 + n % 6, "wind_speed": 4.0 + n % 3}},
            "next_1_hours": {"summary": {"symbol_code": symbol},
                             "details": {"precipitation_amount": 0.4 if n == 0 else 0.0,
                                         "probability_of_precipitation": 60.4 if symbol.startswith("lightrain") else 3.0}},
            "next_6_hours": {"summary": {"symbol_code": "cloudy"},
                             "details": {"air_temperature_max": 14.2, "air_temperature_min": 7.6}}}})
    for n in range(48, 8 * 24, 6):
        when = start + timedelta(hours=n)
        series.append({"time": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": {
            "instant": {"details": {"air_temperature": 5.0, "wind_speed": 2.0}},
            "next_6_hours": {"summary": {"symbol_code": "heavysnowshowers_night"},
                             "details": {"air_temperature_max": 6.0, "air_temperature_min": 1.0,
                                         "probability_of_precipitation": 80.0}}}})
    return {"type": "Feature", "properties": {"timeseries": series}}


def test_the_weather_comes_from_met_norway_when_open_meteo_is_down(monkeypatch, capsys):
    asked = fake_pages(monkeypatch, {
        "https://geocoding-api.open-meteo.com/": {"results": [BRISTOL]},
        "https://api.met.no/weatherapi/locationforecast/2.0/complete": met_sample(),
    })  # api.open-meteo.com doesn't answer
    data = web.weather_data("Bristol")
    assert data["source"] == "MET Norway" and data["place"] == "Bristol"
    assert data["temperature"] == 8 and data["code"] == 3 and data["sky"] == "overcast skies"
    assert data["raining"] is True and data["wind"] == 9  # 4 m/s
    assert data["now"].tzinfo is None
    met = [(url, headers) for url, headers in asked if "api.met.no" in url]
    assert met == [("https://api.met.no/weatherapi/locationforecast/2.0/complete?lat=51.4552&lon=-2.5966&altitude=11",
                    {"User-Agent": web.MET_USER_AGENT})]
    assert "the weather came from MET Norway" in capsys.readouterr().err

    days = data["days"]
    assert [d["date"] for d in days] == [date(2026, 11, 2) + timedelta(days=n) for n in range(8)]
    tomorrow = days[1]
    assert tomorrow["code"] == 61 and tomorrow["sky"] == "light rain" and tomorrow["rain"] == 60
    assert (tomorrow["low"], tomorrow["high"]) == (8, 14) and tomorrow["wind"] == 13  # 6 m/s
    assert tomorrow["sunrise"] == datetime(2026, 11, 3, 7, 7) and tomorrow["sunset"] == datetime(2026, 11, 3, 16, 40)
    assert days[4]["code"] == 86 and days[4]["rain"] == 80 and (days[4]["low"], days[4]["high"]) == (1, 6)
    assert days[4]["snow"] is None  # MET Norway gives no snowfall; the snow code says it
    hours = [h for h in data["hours"] if h["time"].date() == date(2026, 11, 3)]
    assert hours[0]["time"] == datetime(2026, 11, 3, 0, 0) and len(hours) == 24  # Bristol's time (GMT)
    assert [h["code"] for h in hours if h["rain"] == 60] == [61] * 6


def test_met_norway_times_are_the_places_own(monkeypatch):
    tokyo = dict(BRISTOL, name="Tokyo", latitude=35.6895, longitude=139.6917, timezone="Asia/Tokyo")
    fake_pages(monkeypatch, {"https://geocoding-api.open-meteo.com/": {"results": [tokyo]},
                             "https://api.met.no/": met_sample()})
    data = web.weather_data("Tokyo")
    assert data["days"][0]["date"] == date(2026, 11, 3)  # 15:00 UTC is midnight in Tokyo
    assert data["hours"][0]["time"] == datetime(2026, 11, 3, 0, 0)


def test_when_both_weather_services_fail_the_first_reason_is_given(monkeypatch):
    def get(url, headers=None):
        if url.startswith("https://geocoding-api.open-meteo.com/"):
            return json.dumps({"results": [BRISTOL]})
        if url.startswith("https://api.open-meteo.com/"):
            raise web.WebError("api.open-meteo.com: HTTP Error 503: Service Unavailable", "the service is having problems")
        raise web.WebError("api.met.no: <urlopen error timed out>", "it took too long to answer")

    monkeypatch.setattr(web, "_get", get)
    with pytest.raises(web.WebError) as failed:
        web.weather_data("Bristol")
    assert failed.value.reason == "the service is having problems"
    assert str(failed.value) == ("api.open-meteo.com: HTTP Error 503: Service Unavailable; "
                                 "MET Norway: api.met.no: <urlopen error timed out>")


def test_met_norway_needs_the_places_time_zone(monkeypatch):
    fake_pages(monkeypatch, {"https://geocoding-api.open-meteo.com/": {"results": [dict(BRISTOL, timezone=None)]},
                             "https://api.met.no/": met_sample()})
    with pytest.raises(web.WebError):
        web.weather_data("Bristol")


@pytest.mark.parametrize("reply", [{}, {"properties": {"timeseries": []}}, [],
                                   {"properties": {"timeseries": [{"time": "x"}, {"time": "2026-11-02T15:00:00Z"}]}}])
def test_met_norway_without_data(monkeypatch, reply):
    fake_pages(monkeypatch, {"https://geocoding-api.open-meteo.com/": {"results": [BRISTOL]},
                             "https://api.met.no/": reply})
    with pytest.raises(web.WebError):
        web.weather_data("Bristol")


@pytest.mark.parametrize("symbol, code", [
    ("clearsky_day", 0), ("fair_night", 1), ("partlycloudy_polartwilight", 2), ("cloudy", 3), ("fog", 45),
    ("lightrain", 61), ("heavyrainshowers_day", 82), ("lightsleet", 68), ("sleetshowers_night", 84),
    ("snow", 73), ("heavysnowshowers_day", 86), ("rainandthunder", 95), ("lightssnowshowersandthunder_day", 95),
    ("something_new", None), ("", None), (None, None),
])
def test_met_norway_symbols(symbol, code):
    assert web.met_code(symbol) == code
    assert code is None or code in web.WEATHER_CODES


def test_a_place_found_once_is_remembered(monkeypatch):
    asked = fake_pages(monkeypatch, {"https://geocoding-api.open-meteo.com/": {"results": [BRISTOL]}})
    assert web._place("Bristol") == web._place(" bristol ") == BRISTOL
    assert len(asked) == 1
    fake_pages(monkeypatch, {"https://geocoding-api.open-meteo.com/": {}})
    assert web._place("Nowhere") is None and web._place("Bristol") == BRISTOL


@pytest.mark.parametrize("day, place, zone, sunrise, sunset", [
    (date(2024, 6, 21), (51.5074, -0.1278), "Europe/London", datetime(2024, 6, 21, 4, 43), datetime(2024, 6, 21, 21, 22)),
    (date(2024, 12, 21), (51.5074, -0.1278), "Europe/London", datetime(2024, 12, 21, 8, 4),
     datetime(2024, 12, 21, 15, 54)),
    (date(2026, 11, 2), (35.6895, 139.6917), "Asia/Tokyo", datetime(2026, 11, 2, 6, 3), datetime(2026, 11, 2, 16, 46)),
    (date(2026, 11, 2), (34.0522, -118.2437), "America/Los_Angeles", datetime(2026, 11, 2, 6, 13),
     datetime(2026, 11, 2, 17, 1)),
    (date(2026, 11, 2), (-33.8679, 151.2073), "Australia/Sydney", datetime(2026, 11, 2, 5, 55),
     datetime(2026, 11, 2, 19, 24)),
])
def test_sunrise_and_sunset(day, place, zone, sunrise, sunset):
    rise, set_ = web.sun_times(day, *place, web.zoneinfo.ZoneInfo(zone))
    assert abs((rise - sunrise).total_seconds()) <= 120 and abs((set_ - sunset).total_seconds()) <= 120


def test_no_sunrise_in_the_polar_night_or_sunset_under_the_midnight_sun():
    oslo = web.zoneinfo.ZoneInfo("Europe/Oslo")
    assert web.sun_times(date(2026, 12, 21), 69.6496, 18.956, oslo) == (None, None)
    assert web.sun_times(date(2026, 6, 21), 69.6496, 18.956, oslo) == (None, None)


def test_weather_questions_are_answered_from_met_norway_too(monkeypatch):
    from jarvis import forecast

    fake_pages(monkeypatch, {"https://geocoding-api.open-meteo.com/": {"results": [BRISTOL]},
                             "https://api.met.no/": met_sample()})
    data = web.weather_data("Bristol")
    assert forecast.answer(data, "will it rain tomorrow") == \
        "Yes, probably. There's a 60 percent chance of rain in Bristol tomorrow."
    assert forecast.answer(data, "will it snow on friday") == \
        "Yes, snow is expected in Bristol on Friday: heavy snow showers, between 1 and 6 degrees."
    assert forecast.answer(data, "when is sunset tomorrow").startswith("Tomorrow, the sun sets in Bristol at 4:40")
    assert forecast.answer(data, "how windy is it") == "The wind in Bristol is 9 miles per hour right now."
