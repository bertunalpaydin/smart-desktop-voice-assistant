import json
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
                    "rain": None, "code": 0, "raining": False, "wind": None, "now": None, "days": [], "hours": []}
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
