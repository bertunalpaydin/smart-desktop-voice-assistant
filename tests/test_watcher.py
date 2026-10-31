from datetime import datetime

import pytest
from google.auth.exceptions import RefreshError

from jarvis import config, watcher
from jarvis.models import Mail
from jarvis.store import Store


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    monkeypatch.setattr(config, "IMPORTANT_THRESHOLD", 2)
    monkeypatch.setattr(config, "VIP_DOMAINS", [])
    monkeypatch.setattr(config, "VIP_SENDERS", [])
    monkeypatch.setattr(config, "MUTED_DOMAINS", [])
    monkeypatch.setattr(config, "KEYWORDS", ["interview"])


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def mail(id, subject="Hello"):
    return Mail(id, "Ali", "ali@example.com", subject, "", datetime(2026, 9, 29))


class FakeClient:
    """Each round is a list of mails, or an exception to raise."""

    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.skips = []

    def unread(self, limit=15, skip_ids=()):
        self.skips.append(set(skip_ids))
        item = self.rounds.pop(0) if self.rounds else []
        if isinstance(item, Exception):
            raise item
        return [m for m in item if m.id not in skip_ids]


def run_watch(clients, store, rounds, speak_fn=None):
    lines, shown = [], []
    it = iter(clients)
    watcher.watch(
        lambda: next(it), store, interval=0, sleep=lambda s: None, rounds=rounds,
        notify_fn=lambda title, msg: shown.append(msg), speak_fn=speak_fn, out=lines.append,
    )
    return lines, shown


def test_only_important_unseen_mail_is_announced(store):
    shown = []
    client = FakeClient([mail("1", "Interview invite"), mail("2", "Newsletter")])
    new, important = watcher.check_once(client, store, notify_fn=lambda t, m: shown.append((t, m)))
    assert [m.id for m in new] == ["1", "2"]
    assert [m.id for m in important] == ["1"]
    assert shown == [("Ali", "Interview invite")]
    assert store.seen_ids() == {"1", "2"}


def test_seen_mail_is_not_announced_again(store):
    mails = [mail("1", "Interview invite")]
    client = FakeClient(mails, mails)
    shown = []
    watcher.check_once(client, store, notify_fn=lambda t, m: shown.append(m))
    new, _ = watcher.check_once(client, store, notify_fn=lambda t, m: shown.append(m))
    assert new == []
    assert shown == ["Interview invite"]
    assert client.skips[1] == {"1"}


def test_baseline_marks_mail_without_announcing(store):
    shown = []
    client = FakeClient([mail("1", "Interview invite")])
    watcher.check_once(client, store, announce=False, notify_fn=lambda t, m: shown.append(m))
    assert shown == []
    assert store.seen_ids() == {"1"}


def test_first_run_is_silent_then_new_mail_is_announced(store):
    client = FakeClient(
        [mail("1", "Interview old")],
        [mail("1", "Interview old"), mail("2", "Interview new")],
    )
    lines, shown = run_watch([client], store, rounds=2)
    assert shown == ["Interview new"]
    assert any("First run" in line for line in lines)


def test_transient_errors_are_retried(store):
    store.mark_seen(["x"])  # not a first run
    client = FakeClient(OSError("network down"), [mail("1", "Interview")])
    lines, shown = run_watch([client], store, rounds=2)
    assert shown == ["Interview"]
    assert any("Check failed" in line for line in lines)


def test_expired_login_triggers_a_new_login(store):
    store.mark_seen(["x"])
    expired = FakeClient(RefreshError("invalid_grant"))
    fresh = FakeClient([mail("1", "Interview")])
    lines, shown = run_watch([expired, fresh], store, rounds=2)
    assert shown == ["Interview"]
    assert any("logging in again" in line for line in lines)


def test_important_mail_is_also_read_aloud(store):
    spoken = []
    client = FakeClient([mail("1", "Interview invite"), mail("2", "Newsletter")])
    watcher.check_once(client, store, notify_fn=lambda t, m: None, speak_fn=spoken.append)
    assert spoken == ["New important email from Ali. Subject: Interview invite."]


def test_nothing_is_read_aloud_on_the_first_run(store):
    spoken = []
    lines, _ = run_watch([FakeClient([mail("1", "Interview invite")])], store, rounds=1, speak_fn=spoken.append)
    assert spoken == []
    assert any("read aloud" in line for line in lines)


def test_quiet_watch_does_not_mention_reading_aloud(store):
    lines, _ = run_watch([FakeClient()], store, rounds=1)
    assert not any("read aloud" in line for line in lines)
