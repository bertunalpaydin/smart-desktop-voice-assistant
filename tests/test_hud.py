from datetime import datetime, timedelta

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from jarvis import alarms, analysis, config, hud
from jarvis.models import Mail
from jarvis.web import NewsItem


@pytest.fixture(scope="session")
def qt():
    return QApplication.instance() or QApplication([])


class FakeController:
    def __init__(self, path):
        self.alarms = alarms.AlarmStore(path)
        self.commands, self.talked, self.inbox_opened = [], 0, 0

    def run_command(self, text):
        self.commands.append(text)

    def talk(self):
        self.talked += 1

    def open_inbox(self):
        self.inbox_opened += 1


@pytest.fixture
def window(qt, tmp_path, monkeypatch):
    monkeypatch.setattr(hud.sysinfo, "snapshot",
                        lambda: {"cpu": 23, "memory": 61, "battery": 84, "plugged_in": True, "disk": 47})
    opened = []
    monkeypatch.setattr(hud.webbrowser, "open", opened.append)
    controller = FakeController(tmp_path / "alarms.json")
    w = hud.HudWindow(controller)
    w.fake, w.opened = controller, opened
    yield w
    w.close()


def rows(layout):
    return [layout.itemAt(i).widget() for i in range(layout.count() - 1)]  # the last item is the stretch


MAILS = [
    Mail("1", "Sarah", "sarah@gmail.com", "Group project", "Hi, could you send your part by Friday?", None),
    Mail("2", "Shop", "offers@shop.com", "Sale", "50% off", None, labels=("CATEGORY_PROMOTIONS",)),
    Mail("3", "Amazon", "ship@amazon.co.uk", "Your order has shipped", "Arriving tomorrow.", None),
]
NEWS = [NewsItem("Storm Amy hits Scotland", "Winds of 100mph.", "https://bbc/1", "UK", "BBC News", 0),
        NewsItem("Storm Amy batters Scotland", "", "https://sky/1", "News", "Sky News", 0),
        NewsItem("Budget plans unveiled", "", "https://bbc/2", "Politics", "BBC News", 1)]


def test_the_conversation_shows_in_the_middle(window):
    window.add("You", "what's the news")
    window.add("Jarvis", "Here's the news.")
    assert window.you.text() == "You: what's the news" and window.line.text() == "Here's the news."
    for i in range(100):
        window.add("You", str(i))
    assert len(window.messages) == 80 and window.transcript().endswith("You: 99")


def test_states(window):
    for state, text in (("Listening", "LISTENING"), ("Thinking", "THINKING"), ("Speaking", "SPEAKING"),
                        ("Checking for new mail", "ONLINE"), ("", "ONLINE")):
        window.set_state(state)
        assert window.status.text() == text
        assert window.reactor.state == (state if text != "ONLINE" else "")


def test_a_note_then_the_greeting_again(window, monkeypatch):
    window.flash("I didn't hear a command.")
    assert window.line.text() == "I didn't hear a command." and window.flash_timer.isActive()
    window._show_idle()
    assert ", sir. All systems online." in window.line.text() and "Ctrl+Alt+J" in window.line.text()
    monkeypatch.setattr(config, "ADDRESS", "")
    window._show_idle()
    assert ", sir" not in window.line.text() and ". All systems online." in window.line.text()


def test_weather(window):
    window.update_data({"weather": {"place": "Bristol", "temperature": 14, "sky": "light rain", "high": 15, "low": 9,
                                    "rain": 70}})
    assert window.temperature.text() == "14\u00b0" and window.sky.text() == "Light rain in Bristol"
    assert window.weather_more.text() == "High 15\u00b0  Low 9\u00b0    Rain 70%"
    window.update_data({"weather": None})
    assert window.temperature.text() == "--" and config.HOME_CITY in window.sky.text()
    window.update_data({"weather_error": "The weather service didn't answer."})
    assert window.sky.text() == "The weather service didn't answer."


def test_the_inbox(window):
    window.update_data({"mail": analysis.brief_mail(MAILS)})
    assert window.mail_counts.text() == "1 to reply    2 other"
    shown = rows(window.mail_rows)
    assert len(shown) == 3
    QTest.mouseClick(shown[0], Qt.MouseButton.LeftButton)
    assert window.fake.inbox_opened == 1
    window.set_mail(analysis.MailBrief((), "", by_claude=False))
    assert window.mail_counts.text() == "Inbox clear. No unread email." and rows(window.mail_rows) == []
    claude = analysis.MailBrief(analysis.brief_mail(MAILS).notes, "x", by_claude=True)
    window.set_mail(claude)
    assert window.mail_counts.text().endswith("analysed by Claude")
    window.update_data({"mail_error": "Couldn't load the inbox: offline"})
    assert window.mail_counts.text() == "Couldn't load the inbox: offline"


def test_the_news(window):
    brief = analysis.brief_news(NEWS)
    window.update_data({"news": brief})
    assert window.news_topics.text() == "Trending: Storm, Amy, Scotland"
    shown = rows(window.news_rows)
    assert len(shown) == 2 and len(window.ticker.items) == 2
    QTest.mouseClick(shown[0], Qt.MouseButton.LeftButton)
    assert window.opened == ["https://bbc/1"]
    window.set_news(analysis.NewsBrief((), (), ""))
    assert window.news_topics.text() == "No news just now." and window.ticker.items == []


def test_the_ticker_runs_and_opens_headlines(window, qt):
    window.resize(1366, 800)
    window.show()
    window.set_news(analysis.brief_news(NEWS))
    ticker = window.ticker
    qt.processEvents()
    ticker.grab()  # paints it, which records where each headline is
    left, right, link = ticker.spans[0]
    assert link == "https://bbc/1" and ticker.link_at((left + right) / 2) == "https://bbc/1"
    assert ticker.link_at(-50) is None
    QTest.mouseClick(ticker, Qt.MouseButton.LeftButton, pos=ticker.rect().center().__class__(int(left) + 5, 15))
    assert window.opened[-1] == "https://bbc/1"
    before = ticker.offset
    ticker._tick()
    assert ticker.offset > before
    ticker.hovered = True  # the mouse is over it: it waits
    before = ticker.offset
    ticker._tick()
    assert ticker.offset == before
    ticker.set_stories([])
    assert not ticker.grab().isNull() and ticker.spans == []


def test_today_lists_the_next_day_only(window):
    now = datetime.now()
    window.fake.alarms.add("reminder", now + timedelta(hours=2), "call Mum")
    window.fake.alarms.add("timer", now + timedelta(minutes=5), "5 minutes")
    window.fake.alarms.add("alarm", now + timedelta(days=2))
    window.set_alarms(window.fake.alarms.all())
    first = rows(window.today_rows)
    assert len(first) == 2
    window.set_alarms(window.fake.alarms.all())
    assert rows(window.today_rows) == first  # nothing changed: the same rows, not rebuilt
    window.fake.alarms.clear()
    window.set_alarms([])
    empty = rows(window.today_rows)
    assert len(empty) == 1 and "Nothing in the next 24 hours" in empty[0].text()


def test_every_second(window):
    window._readings = 0
    window._every_second()
    assert window.clock.text() == datetime.now().strftime("%H:%M")
    assert window.meters["Processor"].value == 23 and window.meters["Battery"].note == "charging"


def test_the_reactor_and_meters_draw_in_every_state(window):
    reactor = window.reactor
    reactor.resize(300, 300)
    for state in ("", "Listening", "Thinking", "Speaking"):
        reactor.set_state(state)
        angle = reactor.angle
        reactor._tick()
        assert reactor.angle != angle and not reactor.grab().isNull()
    assert any(level > 0 for level in reactor.levels)  # speaking moved the voice ring
    meter = window.meters["Battery"]
    for value in (None, 10, 95):
        meter.set_value(value)
        assert not meter.grab().isNull()


def test_typing_and_talking(window):
    window.input.setText("  what time is it ")
    QTest.keyClick(window.input, Qt.Key.Key_Return)
    window.send_button.click()  # empty now: nothing is sent
    window.talk_button.click()
    assert window.fake.commands == ["what time is it"] and window.fake.talked == 1


def test_the_brain_label(window, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "sk-ant-x")
    window.update_brain()
    assert window.brain.text() == "Brain: Claude Haiku 4.5"
    monkeypatch.setattr(config, "AI_API_KEY", "")
    window.update_brain()
    assert window.brain.text() == "Brain: offline rules"


def test_animations_only_run_on_screen(window, qt):
    window.show()
    qt.processEvents()
    assert window.timer.isActive() and window.reactor.timer.isActive() and window.ticker.timer.isActive()
    window.hide()
    qt.processEvents()
    assert not (window.timer.isActive() or window.reactor.timer.isActive() or window.ticker.timer.isActive())


def test_full_screen_with_f11(window, qt):
    window.show()
    qt.processEvents()
    QTest.keyClick(window, Qt.Key.Key_F11)
    assert window.isFullScreen()
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert not window.isFullScreen()
