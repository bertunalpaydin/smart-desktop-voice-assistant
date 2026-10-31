from datetime import datetime

import pytest

from jarvis import config, importance
from jarvis.models import Mail


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    monkeypatch.setattr(config, "IMPORTANT_THRESHOLD", 2)
    monkeypatch.setattr(config, "VIP_DOMAINS", ["uwe.ac.uk"])
    monkeypatch.setattr(config, "VIP_SENDERS", ["boss@firm.com"])
    monkeypatch.setattr(config, "MUTED_DOMAINS", ["noisy.com"])
    monkeypatch.setattr(config, "KEYWORDS", ["interview", "security alert"])


def mail(subject="Hello", sender="someone@example.com", snippet="", important=False, labels=()):
    return Mail("1", "Someone", sender, subject, snippet, datetime(2026, 9, 29), important, tuple(labels))


def test_plain_mail_is_not_important():
    assert importance.score(mail()) == (0, [])
    assert not importance.is_important(mail())


def test_gmail_marker_alone_is_not_enough():
    assert not importance.is_important(mail(important=True))


def test_keyword_in_subject_is_important():
    assert importance.is_important(mail(subject="Interview invitation"))


def test_keyword_only_in_text_needs_help():
    m = mail(snippet="about your interview")
    assert importance.score(m)[0] == 1
    assert not importance.is_important(m)
    assert importance.is_important(mail(snippet="about your interview", important=True))


def test_vip_domain_matches_subdomains_only_on_a_dot_boundary():
    assert importance.is_important(mail(sender="tutor@uwe.ac.uk"))
    assert importance.is_important(mail(sender="student@live.uwe.ac.uk"))
    assert not importance.is_important(mail(sender="x@notuwe.ac.uk"))


def test_vip_sender_by_address_ignores_case():
    assert importance.is_important(mail(sender="Boss@Firm.com"))


def test_promotions_tab_cancels_a_keyword():
    m = mail(subject="Interview tips you'll love", labels=["CATEGORY_PROMOTIONS"])
    assert not importance.is_important(m)


def test_muted_sender_is_never_important():
    m = mail(subject="Interview", sender="a@mail.noisy.com", important=True)
    assert not importance.is_important(m)


@pytest.mark.parametrize("subject", ["INTERVIEW tomorrow", "Your Interview slot", "re: interview"])
def test_keywords_ignore_case(subject):
    assert importance.is_important(mail(subject=subject))


def test_reasons_explain_the_score():
    points, reasons = importance.score(mail(subject="Security alert", important=True))
    assert points == 3
    assert any("security alert" in r for r in reasons)
    assert "Gmail important" in reasons
