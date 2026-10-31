import sys
import types
from datetime import datetime

import pytest

import main
from jarvis import compose, config, speech
from jarvis.auth import AuthError
from jarvis.models import Draft, Mail


def mail(subject, id="1", snippet=""):
    return Mail(id, "Ali", "ali@example.com", subject, snippet, datetime(2026, 9, 29, 9, 30))


class FakeClient:
    def __init__(self, mails, body="Body text."):
        self.mails = mails
        self._body = body

    def unread(self):
        return self.mails

    def body(self, mail_id):
        return self._body

    def reply_details(self, mail_id):
        subject = next(m.subject for m in self.mails if m.id == mail_id)
        return Draft(to="ali@example.com", subject=f"Re: {subject}", thread_id=f"thread-{mail_id}")


@pytest.fixture
def spoken(monkeypatch):
    said = []
    monkeypatch.setattr(speech, "speak", said.append)
    return said


def use_client(monkeypatch, client):
    monkeypatch.setattr(main, "GmailClient", lambda: client)


def test_show_marks_mail_rated_important(capsys, monkeypatch):
    monkeypatch.setattr(config, "KEYWORDS", ["interview"])
    main.show([mail("Interview invite"), mail("Newsletter")])
    out = capsys.readouterr().out
    assert " 1.! Ali" in out
    assert " 2.  Ali" in out
    assert '"interview" in subject' in out


def test_show_handles_empty_inbox(capsys):
    main.show([])
    assert "No unread mail" in capsys.readouterr().out


def test_auth_problem_is_reported_not_raised(monkeypatch, capsys):
    def boom():
        raise AuthError("nope")

    monkeypatch.setattr(main, "GmailClient", boom)
    assert main.main([]) == 1
    assert "Login problem: nope" in capsys.readouterr().out


def test_read_a_mail_by_its_number(monkeypatch, spoken):
    use_client(monkeypatch, FakeClient([mail("First"), mail("Second", id="2")]))
    assert main.main(["read", "2"]) == 0
    assert spoken == ["Email from Ali. Subject: Second. Body text."]


def test_read_without_a_number_reads_only_important_mail(monkeypatch, spoken):
    monkeypatch.setattr(config, "KEYWORDS", ["interview"])
    use_client(monkeypatch, FakeClient([mail("Interview invite"), mail("Newsletter", id="2")]))
    assert main.main(["read"]) == 0
    assert spoken == [
        "You have 1 important unread email.",
        "Email from Ali. Subject: Interview invite. Body text.",
    ]


def test_read_says_so_when_nothing_is_important(monkeypatch, spoken):
    monkeypatch.setattr(config, "KEYWORDS", ["interview"])
    use_client(monkeypatch, FakeClient([mail("Newsletter")]))
    assert main.main(["read"]) == 0
    assert spoken == ["You have no important unread emails."]


def test_read_uses_the_snippet_when_there_is_no_body(monkeypatch, spoken):
    use_client(monkeypatch, FakeClient([mail("Hi", snippet="Short preview")], body=""))
    assert main.main(["read", "1"]) == 0
    assert spoken == ["Email from Ali. Subject: Hi. Short preview"]


@pytest.mark.parametrize("number", ["0", "3"])
def test_read_rejects_a_number_not_on_the_list(monkeypatch, spoken, capsys, number):
    use_client(monkeypatch, FakeClient([mail("First"), mail("Second", id="2")]))
    assert main.main(["read", number]) == 1
    assert "There is no mail number" in capsys.readouterr().out
    assert spoken == []


def test_voices_are_listed_with_the_one_in_use_marked(monkeypatch, spoken, capsys):
    installed = [
        {"name": "Microsoft Hazel Desktop", "culture": "en-GB"},
        {"name": "Microsoft Zira Desktop", "culture": "en-US"},
    ]
    monkeypatch.setattr(speech, "voices", lambda: (installed, "Microsoft Hazel Desktop"))
    assert main.main(["voices"]) == 0
    out = capsys.readouterr().out
    assert " * Microsoft Hazel Desktop (en-GB)" in out
    assert "   Microsoft Zira Desktop (en-US)" in out
    assert len(spoken) == 1  # a test sentence


def test_voices_problem_is_reported(monkeypatch, capsys):
    def fail():
        raise speech.SpeechError("reading aloud only works on Windows")

    monkeypatch.setattr(speech, "voices", fail)
    assert main.main(["voices"]) == 1
    assert "Speech problem: reading aloud only works on Windows" in capsys.readouterr().out


def test_toast_command_reports_success_and_failure(monkeypatch):
    monkeypatch.setattr(main, "notify", lambda title, message: True)
    assert main.main(["toast"]) == 0
    monkeypatch.setattr(main, "notify", lambda title, message: False)
    assert main.main(["toast"]) == 1


@pytest.mark.parametrize("args, reads_aloud", [(["watch"], True), (["watch", "--quiet"], False)])
def test_watch_command_uses_the_store_and_a_client_factory(monkeypatch, tmp_path, spoken, args, reads_aloud):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "w.db")
    seen = {}

    def fake_watch(make_client, store, speak_fn):
        seen.update(factory=make_client, empty=store.is_empty(), speak_fn=speak_fn)

    monkeypatch.setattr(main, "watch", fake_watch)
    assert main.main(args) == 0
    assert seen["factory"] is main.GmailClient
    assert seen["empty"] is True
    assert seen["speak_fn"] == (speech.speak if reads_aloud else None)


def test_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        main.main(["nope"])


@pytest.fixture
def composed(monkeypatch):
    """Record what would be written and sent instead of doing it."""
    calls = []
    monkeypatch.setattr(compose, "write_and_send", lambda client, draft, voice=True: calls.append((draft, voice)))
    return calls


def test_reply_answers_the_chosen_mail(monkeypatch, composed):
    use_client(monkeypatch, FakeClient([mail("First"), mail("Interview", id="2")]))
    assert main.main(["reply", "2"]) == 0
    assert composed == [(Draft(to="ali@example.com", subject="Re: Interview", thread_id="thread-2"), True)]


def test_reply_type_flag_turns_voice_off(monkeypatch, composed):
    use_client(monkeypatch, FakeClient([mail("First")]))
    main.main(["reply", "1", "--type"])
    assert composed[0][1] is False


def test_reply_rejects_a_number_not_on_the_list(monkeypatch, composed, capsys):
    use_client(monkeypatch, FakeClient([mail("First")]))
    assert main.main(["reply", "4"]) == 1
    assert composed == []
    assert "There is no mail number 4" in capsys.readouterr().out


def test_send_checks_the_address_before_logging_in(monkeypatch, composed, capsys):
    def no_login():
        raise AssertionError("should not log in for a bad address")

    monkeypatch.setattr(main, "GmailClient", no_login)
    assert main.main(["send", "not-an-address"]) == 1
    assert "does not look like an email address" in capsys.readouterr().out


def test_send_asks_for_a_subject_then_writes_the_mail(monkeypatch, composed):
    use_client(monkeypatch, FakeClient([]))
    monkeypatch.setattr(compose, "get_text", lambda question, voice=True, multiline=True: "  Coffee   on Friday ")
    assert main.main(["send", "ali@example.com", "--type"]) == 0
    assert composed == [(Draft(to="ali@example.com", subject="Coffee on Friday"), False)]


def test_watch_stays_quiet_when_reading_aloud_is_off_in_the_settings(monkeypatch, tmp_path):
    config.SETTINGS_FILE.write_text('{"read_new_aloud": false}', encoding="utf-8")
    seen = {}
    monkeypatch.setattr(main, "watch", lambda make_client, store, speak_fn: seen.update(speak_fn=speak_fn))
    assert main.main(["watch"]) == 0
    assert seen["speak_fn"] is None


def test_app_command_starts_the_tray_app(monkeypatch):
    fake = types.ModuleType("jarvis.app")
    fake.run = lambda: 7
    monkeypatch.setitem(sys.modules, "jarvis.app", fake)
    assert main.main(["app"]) == 7


def test_app_command_without_pyqt_says_what_to_do(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "jarvis.app", None)  # as if PyQt6 were missing
    assert main.main(["app"]) == 1
    assert "install.bat" in capsys.readouterr().out


def test_shortcut_command(monkeypatch, capsys):
    monkeypatch.setattr(main.windows, "available", lambda: True)
    monkeypatch.setitem(sys.modules, "jarvis.app", None)  # no PyQt6: the shortcut gets no custom icon
    made = []
    monkeypatch.setattr(main.windows, "create_shortcut", lambda icon=None: made.append(icon) or "C:\\Desktop\\Jarvis.lnk")
    assert main.main(["shortcut"]) == 0
    assert made == [None]
    assert "Shortcut created" in capsys.readouterr().out


def test_shortcut_needs_windows(monkeypatch, capsys):
    monkeypatch.setattr(main.windows, "available", lambda: False)
    assert main.main(["shortcut"]) == 1
    assert "only be made on Windows" in capsys.readouterr().out


# --- Jarvis from the terminal -------------------------------------------------------

def test_ask_answers_and_speaks(spoken, capsys):
    assert main.main(["ask", "set", "a", "timer", "for", "5", "minutes"]) == 0
    assert "Jarvis: Timer set for 5 minutes." in capsys.readouterr().out
    assert spoken == ["Timer set for 5 minutes."]


def test_ask_to_send_an_email_uses_the_send_flow(monkeypatch, spoken, composed):
    monkeypatch.setattr(config, "CONTACTS", {"Mum": "mum@example.com"})
    use_client(monkeypatch, FakeClient([]))
    monkeypatch.setattr(compose, "get_text", lambda question, voice=True, multiline=True: "Lunch")
    assert main.main(["ask", "send an email to mum"]) == 0
    assert composed == [(Draft(to="mum@example.com", subject="Lunch"), False)]


def test_talk_listens_once_then_answers(monkeypatch, spoken, capsys):
    monkeypatch.setattr(main.dictation, "listen", lambda timeout, phrase_limit: "what's the date")
    assert main.main(["talk"]) == 0
    out = capsys.readouterr().out
    assert "You: what's the date" in out and "Jarvis: Today is" in out


def test_talk_reports_when_nothing_was_heard(monkeypatch, spoken, capsys):
    def deaf(timeout, phrase_limit):
        raise main.dictation.DictationError("I didn't hear anything")

    monkeypatch.setattr(main.dictation, "listen", deaf)
    assert main.main(["talk"]) == 1
    assert "I didn't hear anything" in capsys.readouterr().out
