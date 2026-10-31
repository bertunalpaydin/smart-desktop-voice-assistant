import pytest

from jarvis import compose, dictation
from jarvis.models import Draft

DRAFT = Draft(to="ali@example.com", subject="Re: Interview", thread_id="t1")


class FakeClient:
    def __init__(self):
        self.sent = []

    def send(self, draft):
        self.sent.append(draft)
        return "sent-id"


def answers(*lines):
    """Stands in for input(): returns these lines one by one."""
    it = iter(lines)
    return lambda prompt="": next(it)


def heard(*results):
    """Stands in for dictation.listen(): returns texts or raises errors, in order."""
    it = iter(results)

    def listen():
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    return listen


def quiet(text):
    pass


def test_dictated_message_is_sent_after_y():
    client, said = FakeClient(), []
    assert compose.write_and_send(client, DRAFT, ask=answers("y"), listen=heard("See you at ten"), say=said.append)
    assert client.sent == [Draft(to="ali@example.com", subject="Re: Interview", body="See you at ten", thread_id="t1")]
    assert "To ali@example.com. Subject: Re: Interview. See you at ten" in said
    assert said[-1] == "Sent."


@pytest.mark.parametrize("answer", ["n", "", "no", "yes please", "send"])
def test_nothing_is_sent_without_a_plain_y(answer, capsys):
    client = FakeClient()
    assert not compose.write_and_send(client, DRAFT, ask=answers(answer), listen=heard("Hello"), say=quiet)
    assert client.sent == []
    assert "nothing was sent" in capsys.readouterr().out


def test_redo_replaces_the_message():
    client = FakeClient()
    compose.write_and_send(client, DRAFT, ask=answers("r", " Y "), listen=heard("first try", "second try"), say=quiet)
    assert [d.body for d in client.sent] == ["second try"]


def test_typing_takes_over_when_voice_is_unavailable(capsys):
    client = FakeClient()
    compose.write_and_send(
        client, DRAFT, ask=answers("Line one", "Line two", "", "y"),
        listen=heard(dictation.DictationUnavailable("no microphone")), say=quiet,
    )
    assert client.sent[0].body == "Line one\nLine two"
    assert "Voice input is not available" in capsys.readouterr().out


def test_typing_takes_over_when_nothing_was_understood(capsys):
    client = FakeClient()
    compose.write_and_send(
        client, DRAFT, ask=answers("Typed instead", "", "y"),
        listen=heard(dictation.DictationError("I couldn't make out the words")), say=quiet,
    )
    assert client.sent[0].body == "Typed instead"
    assert "couldn't make out the words, so type it instead" in capsys.readouterr().out


def test_type_mode_never_listens_or_talks():
    def listen():
        raise AssertionError("should not listen")

    said, client = [], FakeClient()
    compose.write_and_send(client, DRAFT, voice=False, ask=answers("Typed", "", "y"), listen=listen, say=said.append)
    assert client.sent[0].body == "Typed"
    assert said == []


def test_an_empty_message_is_not_sent(capsys):
    client = FakeClient()
    assert not compose.write_and_send(client, DRAFT, voice=False, ask=answers(""), say=quiet)
    assert client.sent == []
    assert "message is empty" in capsys.readouterr().out


def test_a_one_line_answer_can_be_typed():
    assert compose.get_text("Subject?", voice=False, multiline=False, ask=answers("  Hello  ")) == "Hello"


@pytest.mark.parametrize("address, ok", [
    ("ali@example.com", True),
    ("first.last+cv@uwe.ac.uk", True),
    (" ali@example.com ", True),
    ("ali@example", False),
    ("ali example.com", False),
    ("a@b.com, c@d.com", False),
    ("", False),
])
def test_address_check(address, ok):
    assert compose.valid_address(address) is ok
