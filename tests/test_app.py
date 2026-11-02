import json
import sys
import types
from datetime import datetime, timedelta

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from jarvis import analysis, app, commands, config, dictation, web  # noqa: E402
from jarvis.web import NewsItem  # noqa: E402
from jarvis.auth import AuthError  # noqa: E402
from jarvis.models import Draft, Mail  # noqa: E402


@pytest.fixture(scope="session")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    monkeypatch.setattr(config, "KEYWORDS", ["interview"])
    monkeypatch.setattr(config, "VIP_DOMAINS", [])


def mail(id, subject):
    return Mail(id, "Ali", "ali@example.com", subject, "preview", datetime(2026, 10, 1, 9, 30))


class FakeGmail:
    def __init__(self, mails=()):
        self.mails = list(mails)
        self.sent = []

    def unread(self, limit=15, skip_ids=()):
        return [m for m in self.mails if m.id not in skip_ids]

    def body(self, mail_id):
        return "Body text."

    def reply_details(self, mail_id):
        return Draft(to="ali@example.com", subject="Re: Interview", thread_id="t1", in_reply_to="<m@x>")

    def send(self, draft):
        self.sent.append(draft)
        return "sent-id"


def recorder(signal):
    calls = []
    signal.connect(lambda *args: calls.append(args))
    return calls


def make_worker(gmail, said=None, toasts=None, heard="See you at ten", recognized=("what time is it",)):
    said = [] if said is None else said
    toasts = [] if toasts is None else toasts
    answers = list(heard) if isinstance(heard, list) else None

    def listen(on_ready=None, **kwargs):
        if on_ready:
            on_ready()  # the real one does this once the room noise is measured
        answer = answers.pop(0) if answers is not None else heard
        if isinstance(answer, Exception):
            raise answer
        return answer

    def recognize(raw):
        if isinstance(recognized, Exception):
            raise recognized
        return list(recognized)

    return app.Worker(
        make_client=lambda: gmail,
        notify_fn=lambda title, message: toasts.append((title, message)),
        speak_fn=lambda text, **kwargs: said.append(text),
        listen_fn=listen,
        recognize_fn=recognize,
    )


# --- the icon ---------------------------------------------------------------------

def test_the_icon_gets_a_red_dot_for_important_mail(qt):
    plain = app.draw_icon().toImage()
    assert plain.pixelColor(52, 12).name() == "#0b3d91"
    assert plain.pixelColor(32, 32).name() == "#4fc3f7"
    assert app.draw_icon(alert=True).toImage().pixelColor(52, 12).name() == "#ea4335"


def test_the_icon_can_be_saved_for_the_desktop_shortcut(qt, tmp_path):
    path = app.save_icon(tmp_path / "icon.ico")
    assert path is not None
    assert path.read_bytes()[:4] == b"\x00\x00\x01\x00"  # the .ico header


# --- the worker -------------------------------------------------------------------

def test_first_check_is_quiet_then_new_important_mail_is_announced(qt):
    gmail = FakeGmail([mail("1", "Interview, old")])
    said, toasts = [], []
    worker = make_worker(gmail, said, toasts)
    checked = recorder(worker.checked)

    worker.check()
    assert toasts == [] and said == []
    gmail.mails.append(mail("2", "Interview, new"))
    worker.check()

    assert toasts == [("Ali", "Interview, new")]
    assert said == ["New important email from Ali. Subject: Interview, new."]
    assert [(count, [m.id for m in important]) for count, important in checked] == [(1, ["1"]), (1, ["2"])]


def test_new_mail_is_not_read_aloud_when_that_is_off(qt, monkeypatch):
    monkeypatch.setattr(config, "READ_NEW_ALOUD", False)
    gmail = FakeGmail([mail("1", "Old")])
    said, toasts = [], []
    worker = make_worker(gmail, said, toasts)
    worker.check()
    gmail.mails.append(mail("2", "Interview"))
    worker.check()
    assert toasts == [("Ali", "Interview")]
    assert said == []


def test_a_login_problem_is_reported_and_tried_again_next_time(qt):
    attempts = []

    def make_client():
        attempts.append(1)
        raise AuthError("credentials.json not found")

    worker = app.Worker(make_client=make_client, notify_fn=None, speak_fn=lambda t, **k: None, listen_fn=None)
    failed, busy = recorder(worker.failed), recorder(worker.busy)
    worker.load_inbox()
    worker.load_inbox()
    assert failed[0][0] == "Login problem: credentials.json not found"
    assert len(attempts) == 2
    assert busy[-1] == ("",)


def test_an_unexpected_error_does_not_stop_the_worker(qt, capsys):
    class Broken(FakeGmail):
        def unread(self, **kwargs):
            raise ZeroDivisionError("boom")

    worker = make_worker(Broken())
    failed = recorder(worker.failed)
    worker.load_inbox()
    worker.load_inbox()
    assert failed == [("Something went wrong: boom",)] * 2
    assert "ZeroDivisionError" in capsys.readouterr().err


def test_reading_the_important_mail_aloud(qt):
    said = []
    make_worker(FakeGmail([mail("1", "Interview"), mail("2", "Sale")]), said).read_important()
    assert said == ["You have 1 important unread email.", "Email from Ali. Subject: Interview. Body text."]


def test_sending_and_dictating(qt):
    gmail = FakeGmail()
    worker = make_worker(gmail)
    sent, dictated = recorder(worker.sent), recorder(worker.dictated)
    draft = Draft(to="ali@example.com", subject="Hi", body="Hello")
    worker.send(draft)
    worker.dictate()
    assert gmail.sent == [draft] and sent == [(draft,)]
    assert dictated == [("See you at ten",)]


def test_dictation_problems_are_reported(qt):
    worker = make_worker(FakeGmail(), heard=dictation.DictationError("I didn't hear anything"))
    failed = recorder(worker.failed)
    worker.dictate()
    assert failed == [("Dictation: I didn't hear anything",)]


# --- the tray app and its windows -------------------------------------------------

@pytest.fixture
def sounds(monkeypatch):
    played = []
    monkeypatch.setattr(app.sound, "play_alarm", lambda: played.append("alarm"))
    monkeypatch.setattr(app.sound, "stop", lambda: played.append("stop"))
    monkeypatch.setattr(app.sound, "chime", lambda: played.append("chime"))
    monkeypatch.setattr(app.sound, "listening", lambda: played.append("listening"))
    return played


NEWS = [NewsItem("Storm Amy hits Scotland", "Winds of 100mph.", "https://bbc/1", "UK", "BBC News", 0),
        NewsItem("Budget plans unveiled", "", "https://bbc/2", "Politics", "BBC News", 1)]
WEATHER = {"place": "Bristol", "temperature": 14, "sky": "light rain", "high": 15, "low": 9, "rain": 70}


@pytest.fixture
def fetched(monkeypatch):
    """The HUD's weather and news come from here instead of the internet; counts the fetches."""
    count = {"weather": 0, "news": 0}

    def weather_data(city):
        count["weather"] += 1
        return dict(WEATHER, place=city)

    def news_items():
        count["news"] += 1
        return list(NEWS)

    monkeypatch.setattr(app.web, "weather_data", weather_data)
    monkeypatch.setattr(app.web, "news_items", news_items)
    return count


@pytest.fixture
def tray(qt, monkeypatch, sounds, fetched):
    monkeypatch.setattr(app.windows, "available", lambda: False)  # no Windows registry here
    monkeypatch.setattr(app.speech, "stop", lambda: False)
    gmail = FakeGmail([mail("1", "Interview"), mail("2", "Newsletter")])
    said = []
    controller = app.TrayApp(worker=make_worker(gmail, said), threaded=False)
    messages = []
    monkeypatch.setattr(controller.tray, "showMessage", lambda title, text, *rest: messages.append(text))
    controller.gmail, controller.said, controller.messages, controller.sounds = gmail, said, messages, sounds
    controller.fetched = fetched
    yield controller
    controller.timer.stop()
    controller.alarm_timer.stop()
    controller.hide_timer.stop()
    controller.dashboard_timer.stop()
    controller.dashboard_retry.stop()
    for window in (controller.inbox_window, controller.compose_dialog, controller.settings_dialog,
                   controller.alarms_window, controller.jarvis_window, controller.hud, *controller.alarm_dialogs):
        if window:
            window.close()


def test_the_menu_has_everything(tray):
    texts = [a.text() for a in tray.menu.actions() if a.text()]
    assert "Open the HUD" in texts
    for expected in ["Talk to Jarvis   (Ctrl+Alt+J)", "Jarvis window", "Open inbox", "Check mail now",
                     "Read important mail aloud", "New mail...", "Alarms and reminders...", "Stop speaking",
                     "Read new mail aloud", 'Listen for "Hey Jarvis"', "Pause mail checking", "Settings...", "Quit"]:
        assert expected in texts


def test_the_inbox_lists_mail_with_its_rating(tray):
    tray.open_inbox()
    table = tray.inbox_window.table
    assert table.rowCount() == 2
    assert [table.item(row, 0).text() for row in range(2)] == ["!", ""]
    assert table.item(0, 2).text() == "Interview"
    assert '"interview" in subject' in table.item(0, 4).text()
    assert tray.inbox_window.status.text() == "2 unread"
    assert not tray.inbox_window.read_button.isEnabled()  # nothing selected yet


def test_read_aloud_from_the_inbox(tray):
    tray.open_inbox()
    tray.inbox_window.table.selectRow(0)
    tray.inbox_window.read_button.click()
    assert tray.said == ["Email from Ali. Subject: Interview. Body text."]


def test_reply_opens_a_filled_in_window(tray):
    tray.open_inbox()
    tray.inbox_window.table.selectRow(0)
    tray.inbox_window.reply_button.click()
    dialog = tray.compose_dialog
    assert dialog.to.text() == "ali@example.com"
    assert dialog.subject.text() == "Re: Interview"
    assert "reply" in dialog.windowTitle()


def test_a_mail_is_sent_only_after_yes(tray, monkeypatch):
    tray.open_compose(Draft(to="", subject=""))
    dialog = tray.compose_dialog
    dialog.to.setText(" ali@example.com ")
    dialog.subject.setText("  Coffee  on Friday ")
    dialog.body.setPlainText("Are you free?\n")

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    dialog.send_button.click()
    assert tray.gmail.sent == []

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    dialog.send_button.click()
    assert tray.gmail.sent == [Draft(to="ali@example.com", subject="Coffee on Friday", body="Are you free?")]
    assert "Sent to ali@example.com." in tray.messages
    assert not dialog.isVisible()


def test_a_reply_keeps_its_conversation(tray, monkeypatch):
    tray.open_compose(FakeGmail().reply_details("1"))
    tray.compose_dialog.body.setPlainText("Thanks!")
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    tray.compose_dialog.send_button.click()
    sent = tray.gmail.sent[0]
    assert (sent.thread_id, sent.in_reply_to, sent.body) == ("t1", "<m@x>", "Thanks!")


@pytest.mark.parametrize("to, body, warning", [
    ("not an address", "Hi", "does not look like an email address"),
    ("ali@example.com", "   ", "empty"),
])
def test_bad_input_is_refused_before_asking(tray, monkeypatch, to, body, warning):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append(text))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: pytest.fail("should not ask"))
    tray.open_compose(Draft(to="", subject=""))
    tray.compose_dialog.to.setText(to)
    tray.compose_dialog.body.setPlainText(body)
    tray.compose_dialog.send_button.click()
    assert tray.gmail.sent == []
    assert warning in warnings[0]


def test_dictation_adds_to_the_message(tray):
    tray.open_compose(Draft(to="ali@example.com", subject="Hi", body="Hello."))
    tray.compose_dialog.dictate_button.click()
    assert tray.compose_dialog.body.toPlainText() == "Hello. See you at ten"
    assert tray.compose_dialog.send_button.isEnabled()


def test_settings_are_saved_and_used_straight_away(tray):
    tray.open_settings()
    dialog = tray.settings_dialog
    dialog.poll.setValue(5)
    dialog.read_aloud.setChecked(False)
    dialog.rate.setValue(3)
    dialog.lists["keywords"].setPlainText("interview\noffer\n\n")
    dialog.accept()
    assert config.POLL_SECONDS == 300
    assert config.READ_NEW_ALOUD is False
    assert config.SPEECH_RATE == 3
    assert config.KEYWORDS == ["interview", "offer"]
    assert tray.timer.interval() == 300_000
    assert not tray.aloud_action.isChecked()
    assert json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))["poll_minutes"] == 5


def test_the_voice_list_and_the_test_button(tray):
    tray.open_settings()
    dialog = tray.settings_dialog
    dialog.fill_voices([{"name": "Microsoft Hazel Desktop", "culture": "en-GB"}], "Microsoft Hazel Desktop")
    dialog.voice.setCurrentIndex(dialog.voice.findData("Microsoft Hazel Desktop"))
    dialog.rate.setValue(-2)
    tested = recorder(tray.requests.test_voice)
    dialog.test_button.click()
    assert tested == [("Microsoft Hazel Desktop", -2)]
    assert tray.said[-1] == app.VOICE_SAMPLE


def test_important_mail_shows_on_the_icon_until_the_inbox_is_opened(tray):
    tray._on_checked(1, [mail("1", "Interview")])
    assert tray.alert
    assert "1 important" in tray.tray.toolTip()
    tray.open_inbox()
    assert not tray.alert


def test_pausing_skips_timed_checks_but_not_check_now(tray):
    checks = recorder(tray.requests.check)
    tray.pause_action.setChecked(True)
    tray._timed_check()
    assert checks == []
    tray.check_now()
    assert len(checks) == 1
    assert "(mail checking paused)" in tray.tray.toolTip()


def test_the_read_aloud_menu_switch_is_saved(tray):
    tray.aloud_action.setChecked(False)
    assert config.READ_NEW_ALOUD is False
    assert json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))["read_new_aloud"] is False


def test_problems_show_up_as_tray_messages(tray):
    tray.worker.failed.emit("Connection problem: offline")
    assert "Connection problem: offline" in tray.messages


# --- talking to Jarvis --------------------------------------------------------------

def test_a_typed_command_is_answered_in_the_window_and_out_loud(tray):
    tray.run_command("What time is it?")
    log = tray.jarvis_window.transcript()
    assert "You: What time is it?" in log
    assert "Jarvis: It's " in log
    assert tray.said[-1].startswith("It's ")
    assert tray.jarvis_window.state.text() == app.IDLE_TEXT


def test_talking_listens_for_a_command_and_does_it(qt, monkeypatch, sounds):
    monkeypatch.setattr(app.windows, "available", lambda: False)
    stopped = []
    monkeypatch.setattr(app.speech, "stop", lambda: stopped.append(1))
    said = []
    controller = app.TrayApp(worker=make_worker(FakeGmail(), said, heard="set a timer for 2 minutes"), threaded=False)
    controller.talk()
    assert controller.jarvis_window.isVisible()
    assert sounds == ["listening"] and stopped == [1]
    assert "You: set a timer for 2 minutes" in controller.jarvis_window.transcript()
    assert said == ["Timer set for 2 minutes."]
    assert [a.kind for a in controller.alarms.all()] == ["timer"]
    controller.jarvis_window.close()
    controller.alarm_timer.stop()


def test_nothing_heard(qt, monkeypatch, sounds):
    monkeypatch.setattr(app.windows, "available", lambda: False)
    monkeypatch.setattr(app.speech, "stop", lambda: False)
    said = []
    worker = make_worker(FakeGmail(), said, heard=dictation.DictationError("I didn't hear anything"))
    failed = recorder(worker.failed)
    controller = app.TrayApp(worker=worker, threaded=False)
    controller.talk()
    assert said == ["Sorry, I didn't hear anything."] and failed == []
    controller.jarvis_window.close()
    controller.alarm_timer.stop()


def test_cutting_jarvis_short_skips_the_rest_of_the_reading(qt):
    said = []
    worker = make_worker(FakeGmail(), said)
    worker.cancelled.set()
    assert worker._say("a long email") is False
    worker.say("hello")  # a new job starts fresh
    assert said == ["hello"]


class FakeEars(app.QObject):
    """Stands in for the "Hey Jarvis" listener."""

    heard = app.pyqtSignal()
    command = app.pyqtSignal(object)
    nothing_said = app.pyqtSignal()
    failed = app.pyqtSignal(str)

    def __init__(self, log=None):
        super().__init__()
        self.log = [] if log is None else log
        self.paused = False
        self.recording_requested = False
        self.running = True

    def start(self):
        self.log.append("ears")

    def stop(self):
        self.log.append("ears stopped")

    def isRunning(self):  # noqa: N802 - Qt's name
        return self.running

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def record_now(self):
        self.recording_requested = True


def test_the_wake_word_lets_go_of_the_microphone_while_jarvis_talks(tray):
    tray.wake = FakeEars()
    tray.worker.audio_busy.emit(True)
    assert tray.wake.paused
    tray.worker.audio_busy.emit(False)
    tray._resume_wake()  # (normally a moment later)
    assert not tray.wake.paused
    tray.worker.say("Hello")  # speaking pauses it and lets it go again
    assert tray.said[-1] == "Hello" and tray.audio_busy is False


def test_hey_jarvis_then_a_recorded_command(tray):
    tray.wake = FakeEars()
    tray._on_wake_heard()
    assert tray.jarvis_window.isVisible()
    assert tray.jarvis_window.state.text() == "Listening..." and tray.sounds[-1] == "listening"
    tray._on_command_audio(b"\x00\x00" * 1600)
    assert "You: what time is it" in tray.jarvis_window.transcript()
    assert tray.said[-1].startswith("It's ")


def test_a_recorded_command_that_could_not_be_understood(qt, monkeypatch, sounds):
    monkeypatch.setattr(app.windows, "available", lambda: False)
    said = []
    worker = make_worker(FakeGmail(), said, recognized=dictation.DictationError("I couldn't make out the words"))
    controller = app.TrayApp(worker=worker, threaded=False)
    controller._on_command_audio(b"\x00\x00")
    assert said == ["Sorry, I couldn't make out the words."]
    controller.jarvis_window.close()
    controller.alarm_timer.stop()


def test_woken_up_but_nothing_said(tray):
    tray._on_nothing_said()
    assert tray.jarvis_window.state.text() == "I didn't hear a command."
    assert tray.said == []  # no talking back after a false wake-up


def test_the_hotkey_uses_the_open_microphone_when_listening_for_hey_jarvis(tray):
    tray.wake = FakeEars()
    tray.wake.paused = True
    tray.talk()
    assert tray.wake.recording_requested and not tray.wake.paused
    assert tray.jarvis_window.isVisible()


def test_answers_with_several_parts_all_show_in_the_window(tray):
    tray.worker.replied.emit("news", commands.Reply("Here are the top stories.", more=("One", "Two")))
    assert tray.jarvis_window.transcript().endswith("Jarvis: Here are the top stories.\nJarvis: One\nJarvis: Two")


def test_the_window_keeps_only_the_latest_messages(tray):
    for i in range(app.JarvisWindow.MAX_MESSAGES + 5):
        tray.jarvis_window.add("You", f"message {i}")
    assert len(tray.jarvis_window.messages) == app.JarvisWindow.MAX_MESSAGES
    assert tray.jarvis_window.messages[0] == ("You", "message 5")


def test_the_theme_and_the_orb_draw(qt, tray):
    app.apply_theme(qt)
    try:
        for state in ("", "Listening", "Thinking", "Speaking", "Checking for new mail"):
            tray.jarvis_window.set_state(state)
            tray.jarvis_window.orb.phase = 0.5
            assert not tray.jarvis_window.orb.grab().isNull()
        assert tray.jarvis_window.orb.state == ""  # a background job doesn't animate it
        assert not app.draw_symbol("mic").isNull() and not app.draw_symbol("send").isNull()
    finally:
        qt.setStyleSheet("")


def test_stopping_and_snoozing_alarms_by_voice(tray):
    tray.alarms.add("alarm", datetime.now() - timedelta(seconds=1))
    tray.alarms.add("timer", datetime.now() - timedelta(seconds=1), "5 minutes")
    tray.check_alarms()
    assert len(tray.alarm_dialogs) == 2 and tray.worker.ringing()
    tray.run_command("snooze")
    assert tray.alarm_dialogs == [] and len(tray.alarms.all()) == 2
    tray.alarms.clear()
    tray.alarms.add("alarm", datetime.now() - timedelta(seconds=1))
    tray.check_alarms()
    tray.run_command("stop")
    assert tray.alarm_dialogs == [] and tray.alarms.all() == []
    assert tray.said[-1] == "Okay, alarm off."


def test_an_alarm_keeps_ringing_after_jarvis_answers(tray):
    tray.alarms.add("alarm", datetime.now() - timedelta(seconds=1))
    tray.check_alarms()
    tray.run_command("what time is it")  # the answer interrupted the ringing
    assert tray.sounds.count("alarm") == 2


@pytest.mark.parametrize("action, window", [
    ("inbox", "inbox_window"), ("alarms", "alarms_window"), ("settings", "settings_dialog"),
])
def test_replies_can_open_windows(tray, action, window):
    tray.worker.replied.emit("x", commands.Reply("Okay.", action))
    assert getattr(tray, window).isVisible()


def test_writing_an_email_by_voice_ends_in_the_compose_window(tray):
    draft = Draft(to="mum@example.com", subject="Lunch", body="See you at one")
    tray.worker.replied.emit("send an email to mum", commands.Reply("Here it is.", "compose", draft))
    dialog = tray.compose_dialog
    assert (dialog.to.text(), dialog.subject.text(), dialog.body.toPlainText()) == \
        ("mum@example.com", "Lunch", "See you at one")
    assert tray.gmail.sent == []  # nothing goes out without Send and Yes


def test_a_screenshot_is_saved_or_reported(tray, monkeypatch, tmp_path):
    monkeypatch.setattr(app.QStandardPaths, "writableLocation", lambda location: str(tmp_path))
    tray.take_screenshot()
    tray._grab_screen(tmp_path / "Screenshots" / "shot.png", show_again=False)
    assert "Screenshot saved" in tray.messages[-1] or "couldn't take" in tray.messages[-1]


def test_clicking_the_tray_icon_opens_the_hud(tray):
    tray.jarvis_window.pop_up()
    tray._on_activated(app.QSystemTrayIcon.ActivationReason.Trigger)
    assert tray.hud.isVisible() and not tray.jarvis_window.isVisible()


def test_the_hud_fills_itself_and_does_not_fetch_too_often(tray):
    tray.open_hud()
    hud = tray.hud
    assert hud.temperature.text() == "14\u00b0" and hud.sky.text() == "Light rain in Bristol"
    assert len(hud.ticker.items) == 2 and hud.news.stories[0].title == "Storm Amy hits Scotland"
    assert hud.mail is not None and len(hud.mail.notes) == 2  # the inbox too
    tray.open_hud()  # again, a minute later: what it has is fresh enough
    assert tray.fetched == {"weather": 1, "news": 1}
    tray.dashboard_loaded -= app.timedelta(minutes=11)
    tray.hud.hide()
    tray.open_hud()
    assert tray.fetched == {"weather": 2, "news": 2}


def test_the_hud_leaves_the_inbox_alone_while_mail_checking_is_paused(tray):
    tray.paused = True
    tray.open_hud()
    assert tray.hud.mail is None and tray.hud.temperature.text() == "14\u00b0"


def test_the_hud_says_what_failed_and_tries_again_soon(tray, monkeypatch, capsys):
    def offline(*args):
        raise web.WebError("api.open-meteo.com: <urlopen error timed out>", "it took too long to answer")

    online = app.web.weather_data, app.web.news_items
    monkeypatch.setattr(app.web, "weather_data", offline)
    monkeypatch.setattr(app.web, "news_items", offline)
    tray.gmail.unread = offline
    tray.open_hud()
    assert tray.hud.sky.text() == "Couldn't get the weather: it took too long to answer. Jarvis will try again shortly."
    assert tray.hud.news_topics.text() == "Couldn't get the news: it took too long to answer. Jarvis will try again shortly."
    assert tray.hud.mail_counts.text().startswith("Couldn't load the inbox: api.open-meteo.com")
    assert "The weather didn't load: api.open-meteo.com: <urlopen error timed out>" in capsys.readouterr().err  # app.log
    assert tray.dashboard_retry.isActive() and tray.dashboard_retry.interval() == 60 * 1000
    assert tray.dashboard_loaded is None  # opening the HUD again tries again too

    tray.dashboard_retry.stop()
    tray.dashboard_retry.timeout.emit()  # a minute later: still offline, so the next try is 2 minutes later
    assert tray.dashboard_retry.interval() == 2 * 60 * 1000
    tray.dashboard_retry.stop()
    tray.dashboard_retry.timeout.emit()
    assert tray.dashboard_retry.interval() == 5 * 60 * 1000
    tray.dashboard_retry.stop()
    tray.dashboard_retry.timeout.emit()  # then only the usual update every 15 minutes
    assert not tray.dashboard_retry.isActive()
    tray.dashboard_timer.timeout.emit()
    assert tray.dashboard_retry.isActive() and tray.dashboard_retry.interval() == 60 * 1000
    tray.dashboard_retry.stop()

    monkeypatch.setattr(app.web, "weather_data", online[0])  # back online
    monkeypatch.setattr(app.web, "news_items", online[1])
    tray.dashboard_timer.timeout.emit()
    assert tray.hud.temperature.text() == "14\u00b0" and tray.dashboard_retries == 0
    assert not tray.dashboard_retry.isActive()


def test_the_conversation_shows_in_both_windows(tray):
    tray.run_command("what time is it")
    for window in (tray.jarvis_window, tray.hud):
        assert "You: what time is it" in window.transcript() and "Jarvis: It's " in window.transcript()
    assert tray.hud.line.text().startswith("It's ")


def test_with_the_hud_open_jarvis_answers_there(tray):
    tray.open_hud()
    tray.show_jarvis()
    assert not tray.jarvis_window.isVisible()
    tray.hud.showMinimized()
    tray.show_jarvis()
    assert tray.jarvis_window.isVisible()


def test_a_briefing_opens_the_hud_and_fills_it(tray):
    tray.paused = True  # (so opening the HUD fetches nothing from the inbox)
    brief = analysis.brief_news(NEWS)
    tray.worker.replied.emit("brief me", commands.Reply("Good evening, sir.", "dashboard",
                                                        {"news": brief, "weather": dict(WEATHER, temperature=3)}))
    assert tray.hud.isVisible() and tray.hud.temperature.text() == "3\u00b0"
    assert tray.fetched == {"weather": 0, "news": 0}  # the briefing had just fetched them
    assert "Jarvis: Good evening, sir." in tray.hud.transcript()


def test_mail_checks_keep_the_open_hud_up_to_date(tray):
    tray.check_now()
    assert tray.hud.mail is None  # the HUD isn't open: no extra work
    tray.open_hud()
    tray.hud.mail = None
    tray.check_now()
    assert tray.hud.mail is not None and len(tray.hud.mail.notes) == 2


def test_settings_has_an_ai_tab_and_the_hud_follows_it(tray):
    tray.open_settings()
    dialog = tray.settings_dialog
    tabs = dialog.findChild(app.QTabWidget)
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["General", "Jarvis", "AI", "Mail rules"]
    dialog.ai_key.setText("sk-ant-test")
    dialog.address.setText("Bertun")
    dialog.hud_at_start.setChecked(False)
    dialog.accept()
    assert (config.AI_API_KEY, config.ADDRESS, config.HUD_AT_START) == ("sk-ant-test", "Bertun", False)
    assert tray.hud.brain.text().startswith("Brain: Claude")


def test_the_hotkey_and_wake_word_follow_the_settings(tray, monkeypatch):
    started = []

    class FakeHotkey:
        def __init__(self, on_press):
            self.on_press, self.registered = on_press, True
            self.ready = type("Ready", (), {"wait": lambda self, timeout: True})()

        def start(self):
            started.append("hotkey")

        def stop(self):
            started.append("hotkey stopped")

    monkeypatch.setattr(app.hotkey, "available", lambda: True)
    monkeypatch.setattr(app.wakeword, "available", lambda: True)
    monkeypatch.setattr(app.hotkey, "create", FakeHotkey)
    monkeypatch.setattr(app.wakeword, "WakeWordListener", lambda: FakeEars(started))
    tray.start_listening()
    assert started == ["hotkey", "ears"] and tray.wake_action.isChecked()

    talks = []
    monkeypatch.setattr(tray, "talk", lambda: talks.append(1))
    tray.hotkey.on_press()  # what the hotkey thread calls
    assert tray.hotkey is not None

    tray.wake_action.setChecked(False)  # the menu switch
    assert config.WAKE_WORD_ENABLED is False and tray.wake is None
    assert json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))["wake_word"] is False
    tray.quit()
    assert started[-2:] == ["ears stopped", "hotkey stopped"]


def test_a_hotkey_taken_by_another_program_is_reported(tray, monkeypatch):
    class TakenHotkey:
        def __init__(self, on_press):
            self.registered = False
            self.ready = type("Ready", (), {"wait": lambda self, timeout: True})()

        def start(self):
            pass

    monkeypatch.setattr(app.hotkey, "available", lambda: True)
    monkeypatch.setattr(app.hotkey, "create", TakenHotkey)
    tray._update_hotkey()
    assert tray.hotkey is None
    assert f"{config.HOTKEY_LABEL} is already used by another program" in tray.messages[-1]


# --- alarms, timers and reminders -----------------------------------------------------

def test_a_due_alarm_rings_until_stopped(tray):
    tray.alarms.add("alarm", datetime.now() - timedelta(seconds=1))
    tray.check_alarms()
    assert tray.sounds == ["alarm"]
    dialog = tray.alarm_dialogs[0]
    assert dialog.isVisible() and "This is your alarm" in tray.messages[-1]
    dialog.stop_button.click()
    assert tray.sounds[-1] == "stop" and tray.alarm_dialogs == []
    assert tray.alarms.all() == []


def test_snoozing_sets_it_again_in_five_minutes(tray):
    tray.alarms.add("timer", datetime.now() - timedelta(seconds=1), "10 minutes")
    tray.check_alarms()
    tray.alarm_dialogs[0].snooze_button.click()
    again = tray.alarms.all()
    assert [(a.kind, a.text) for a in again] == [("timer", "10 minutes")]
    assert timedelta(minutes=4) < again[0].due - datetime.now() <= timedelta(minutes=5)


def test_a_reminder_chimes_and_is_read_out(tray):
    tray.alarms.add("reminder", datetime.now() - timedelta(seconds=1), "call Mum")
    tray.check_alarms()
    assert tray.sounds == ["chime"]
    assert tray.said[-1] == "Reminder: call Mum."


def test_one_missed_while_jarvis_was_off_says_so(tray):
    tray.alarms.add("reminder", datetime.now() - timedelta(hours=3), "take the bins out")
    tray.check_alarms()
    assert "went off while Jarvis wasn't running" in tray.messages[-1]


def test_the_alarms_window_lists_and_deletes(tray, monkeypatch):
    tray.alarms.add("reminder", datetime.now() + timedelta(hours=1), "call Mum")
    tray.alarms.add("alarm", datetime.now() + timedelta(hours=2))
    tray.open_alarms()
    window = tray.alarms_window
    assert window.table.rowCount() == 2
    assert window.table.item(0, 1).text() == "call Mum"
    window.table.selectRow(0)
    window.delete_button.click()
    assert [a.kind for a in tray.alarms.all()] == ["alarm"]
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window.delete_all_button.click()
    assert tray.alarms.all() == [] and window.table.rowCount() == 0


def test_jarvis_settings_tab(tray):
    tray.open_settings()
    dialog = tray.settings_dialog
    dialog.city.setText("London")
    dialog.contacts.setPlainText("Mum = mum@example.com\nnot a contact line\n")
    dialog.websites.setPlainText("Uni = https://www.uwe.ac.uk/")
    dialog.accept()
    assert config.HOME_CITY == "London"
    assert config.CONTACTS == {"Mum": "mum@example.com"}
    assert config.WEBSITES == {"uni": "https://www.uwe.ac.uk/"}


def test_pairs_text_round_trip():
    pairs = {"Mum": "mum@example.com", "Dad": "dad@example.com"}
    assert app._parse_pairs(app._pairs_text(pairs)) == pairs
    assert app._parse_pairs("no equals sign\n = nothing\nname = ") == {}


# --- on a Mac ----------------------------------------------------------------------------

def test_style_sheet_sizes_scale_for_a_mac():
    assert app.scaled_style("a { font-size: 10pt; } b { font-size: 40pt; } c { padding: 7px 16px; }", 4 / 3) == \
        "a { font-size: 13.3333pt; } b { font-size: 53.3333pt; } c { padding: 7px 16px; }"
    assert app.scaled_style(app.STYLE, 1) == app.STYLE


def test_the_menu_bar_icon_is_a_template_on_a_mac(tray, monkeypatch):
    monkeypatch.setattr(app.macos, "available", lambda: True)
    icon = app.tray_icon(alert=True)
    assert icon.isMask() and not icon.isNull()
    tray.set_alert(False)
    assert tray.tray.icon().isMask()


def test_opening_jarvis_again_shows_the_hud(tray):
    tray.reopened(app.Qt.ApplicationState.ApplicationInactive)
    assert not tray.hud.isVisible()
    tray.reopened(app.Qt.ApplicationState.ApplicationActive)
    assert tray.hud.isVisible()
    tray.hud.hide()
    tray.open_settings()  # a window is already up: leave it be
    tray.reopened(app.Qt.ApplicationState.ApplicationActive)
    assert not tray.hud.isVisible()


def test_the_jarvis_window_opens_near_the_menu_bar_on_a_mac(tray, monkeypatch):
    monkeypatch.setattr(app.macos, "available", lambda: True)
    area = tray.jarvis_window.screen().availableGeometry()
    tray.jarvis_window.pop_up()
    assert tray.jarvis_window.y() == area.top() + 8


def test_jarvis_app_logs_to_a_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(app.sys, "frozen", "macosx_app", raising=False)
    out, err = sys.stdout, sys.stderr
    try:
        app._log_to_file_if_needed()
        print("hello from the app")
        sys.stdout.flush()
    finally:
        log, sys.stdout, sys.stderr = sys.stdout, out, err
    log.close()
    assert "hello from the app" in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_mac_screenshots_use_macos_own_tool(tray, monkeypatch, tmp_path):
    shots = []
    monkeypatch.setattr(app.macos, "available", lambda: True)
    monkeypatch.setattr(app.macos, "screenshot", lambda path: shots.append(path) or True)
    tray._grab_screen(tmp_path / "Screenshots" / "shot.png", show_again=False)
    assert shots == [tmp_path / "Screenshots" / "shot.png"]
    assert tray.views.windows[0].transcript().endswith(f"Jarvis: Screenshot saved to {shots[0]}")
    monkeypatch.setattr(app.macos, "screenshot", lambda path: (_ for _ in ()).throw(app.macos.MacError("denied")))
    tray._grab_screen(tmp_path / "x.png", show_again=False)
    assert tray.views.windows[0].transcript().endswith("Jarvis: Sorry, I couldn't take the screenshot.")


def test_on_a_mac_the_jarvis_window_stays_visible_and_pops_up_over_other_apps(tray, monkeypatch):
    monkeypatch.setattr(app.macos, "available", lambda: True)
    window = app.JarvisWindow(tray)
    assert window.testAttribute(app.Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
    window.close()
    tray.open_hud()
    monkeypatch.setattr(tray.hud, "isActiveWindow", lambda: False)  # another app is in front
    tray.show_jarvis()
    assert tray.jarvis_window.isVisible()  # "Hey Jarvis" from another app: the small window
    tray.jarvis_window.hide()
    monkeypatch.setattr(tray.hud, "isActiveWindow", lambda: True)  # the HUD is in front
    tray.show_jarvis()
    assert not tray.jarvis_window.isVisible()


def test_elsewhere_the_jarvis_window_is_an_ordinary_tool_window(tray):
    assert not tray.jarvis_window.testAttribute(app.Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)


def test_a_polite_request_to_stop_quits_like_the_menu(tray, qt, monkeypatch):
    import signal

    quits = []
    monkeypatch.setattr(tray, "quit", lambda: quits.append(1))
    old = signal.getsignal(signal.SIGTERM)
    try:
        handler = app.quit_on_terminate(tray)
        assert signal.getsignal(signal.SIGTERM) is handler
        handler(signal.SIGTERM, None)
        qt.processEvents()
        assert quits == [1]
    finally:
        signal.signal(signal.SIGTERM, old)


def test_cmd_q_in_jarvis_windows_quits(tray, monkeypatch):
    quits = []
    monkeypatch.setattr(tray, "quit", lambda: quits.append(1))
    window, hud_window = app.JarvisWindow(tray), app.hud.HudWindow(tray)
    for widget in (window, hud_window):
        shortcuts = widget.findChildren(app.QShortcut)
        quit_keys = [s for s in shortcuts if s.key() == app.QKeySequence(app.QKeySequence.StandardKey.Quit)]
        assert len(quit_keys) == 1
        quit_keys[0].activated.emit()
        widget.close()
    assert quits == [1, 1]


# --- the human voice --------------------------------------------------------------

class FakeVoiceEngine:
    def __init__(self, ready=True, error=""):
        self.calls, self._ready, self.error, self.state = [], ready, error, "off"

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def wait_ready(self, timeout):
        self.calls.append("wait")
        return self._ready

    def interrupt(self):
        return False

    def warm(self):
        self.calls.append("warm")
        return True


@pytest.fixture
def human(monkeypatch, tmp_path):
    """A Mac with the human voice installed (or not: human.installed = False)."""
    state = types.SimpleNamespace(installed=True, engine=FakeVoiceEngine())
    monkeypatch.setattr(app.macos, "available", lambda: True)
    monkeypatch.setattr(app.human_voice, "installed", lambda: state.installed)
    speeds = {"nano": {"device": "cpu", "speed": 4.2}, "turbo": {"device": "mps", "speed": 1.6}}
    monkeypatch.setattr(app.human_voice, "install_info", lambda name=None: speeds[name or "nano"])
    monkeypatch.setattr(app.human_voice, "models_installed", lambda: ["nano", "turbo"])
    monkeypatch.setattr(app.human_voice, "ENGINE", state.engine)
    return state


def test_the_human_voice_tab(tray, human, monkeypatch):
    sample = app.human_voice.samples_dir() / "my-voice.wav"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"")
    tray.open_settings()
    dialog = tray.settings_dialog
    tabs = dialog.findChild(app.QTabWidget)
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["General", "Human voice", "Jarvis", "AI", "Mail rules"]
    assert "come out at once" in dialog.human_status.text()
    assert [dialog.voice_model.itemText(i) for i in range(dialog.voice_model.count())] == [
        "Automatic: the best one this Mac can say without waiting",
        "Best quality: Chatterbox Turbo (1.6x as fast as speech here)",
        "Fastest: Chatterbox Nano (4.2x as fast as speech here)"]
    dialog.voice_model.setCurrentIndex(dialog.voice_model.findData("turbo"))
    assert dialog.engine.isEnabled() and not dialog.file_button.isEnabled()
    dialog.consent.setChecked(True)
    assert dialog.file_button.isEnabled()
    dialog.engine.setCurrentIndex(dialog.engine.findData("chatterbox"))
    dialog.sample.setCurrentIndex(dialog.sample.findData(str(sample)))
    tested = recorder(tray.requests.test_human_voice)
    dialog.human_test.click()
    assert tested == [(str(sample), "")]
    dialog.voice_effect.setCurrentIndex(dialog.voice_effect.findData("helmet"))
    dialog.human_test.click()
    assert tested[-1] == (str(sample), "helmet")
    dialog.accept()
    assert (config.VOICE_ENGINE, config.VOICE_SAMPLE, config.VOICE_MODEL) == ("chatterbox", str(sample), "turbo")
    assert config.VOICE_EFFECT == "helmet"
    assert human.engine.calls[-2:] == ["start", "warm"]  # loads straight away, and makes the usual replies
    assert "Yes, sir? What can I do for you?" in human.engine.phrases()
    tray.open_settings()
    tray.settings_dialog.engine.setCurrentIndex(0)
    tray.settings_dialog.accept()
    assert config.VOICE_ENGINE == "mac" and human.engine.calls[-1] == "stop"  # frees the memory


def test_the_human_voice_tab_before_it_is_installed(tray, human, monkeypatch):
    human.installed = False
    monkeypatch.setattr(config, "VOICE_ENGINE", "chatterbox")
    tray.open_settings()
    dialog = tray.settings_dialog
    assert "install_voice.sh" in dialog.human_status.text()
    assert not dialog.engine.isEnabled() and not dialog.record_button.isEnabled()
    assert dialog.values()["voice_engine"] == "chatterbox"  # left as it was


def test_no_human_voice_tab_off_a_mac(tray):
    tray.open_settings()
    tabs = tray.settings_dialog.findChild(app.QTabWidget)
    assert "Human voice" not in [tabs.tabText(i) for i in range(tabs.count())]
    assert tray.settings_dialog.values()["voice_engine"] == config.VOICE_ENGINE


def test_recording_my_voice_from_the_settings(tray, human, monkeypatch):
    monkeypatch.setattr(app.QMessageBox, "information", lambda *args: app.QMessageBox.StandardButton.Ok)
    warnings = []
    monkeypatch.setattr(app.QMessageBox, "warning", lambda parent, title, text: warnings.append(text))
    path = app.human_voice.samples_dir() / "my-voice.wav"
    results = [path, app.human_voice.VoiceError("I couldn't hear you.")]

    def record():
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(app.human_voice, "record_sample", record)
    tray.open_settings()
    dialog = tray.settings_dialog
    dialog.record_voice()
    assert dialog.sample.currentData() == str(path) and dialog.sample.currentText() == "My recording"
    assert "listening" in tray.sounds and dialog.record_button.isEnabled()
    dialog.record_voice()
    assert warnings == ["I couldn't hear you."]


def test_using_a_recording_from_the_settings(tray, human, monkeypatch, tmp_path):
    imported = app.human_voice.samples_dir() / "voice-sample.wav"
    monkeypatch.setattr(app.QFileDialog, "getOpenFileName", lambda *args: (str(tmp_path / "memo.m4a"), ""))
    monkeypatch.setattr(app.human_voice, "import_sample", lambda source: imported)
    tray.open_settings()
    dialog = tray.settings_dialog
    dialog.consent.setChecked(True)
    dialog.use_recording()
    assert dialog.sample.currentData() == str(imported) and dialog.sample.currentText() == "Imported recording"


def test_testing_the_human_voice(qt, human):
    said, failures = [], []
    worker = app.Worker(make_client=lambda: None, speak_fn=lambda text, **kwargs: said.append((text, kwargs)))
    worker.failed.connect(failures.append)
    worker.test_human_voice("/voices/me.wav", "helmet")
    assert human.engine.calls == ["start", "wait"]
    assert said == [(app.VOICE_SAMPLE, {"engine": "chatterbox", "sample": "/voices/me.wav", "effect": "helmet"})]
    human.engine._ready, human.engine.error = False, "MemoryError: not enough memory"
    worker.test_human_voice("", "")
    assert failures == ["The human voice didn't start: MemoryError: not enough memory."]
    assert len(said) == 1


def test_the_hud_says_which_voice(tray, human, monkeypatch):
    monkeypatch.setattr(config, "VOICE_ENGINE", "chatterbox")
    human.engine.state = "loading"
    tray.hud.update_brain()
    assert tray.hud.brain.text().endswith("Voice: human (loading)")
    human.engine.state = "ready"
    tray.hud.update_brain()
    assert tray.hud.brain.text() == "Brain: offline rules    Voice: human"


def test_quitting_ends_the_human_voice(tray, human, monkeypatch):
    monkeypatch.setattr(app.QApplication, "quit", lambda: None)
    tray.quit()
    assert "stop" in human.engine.calls
