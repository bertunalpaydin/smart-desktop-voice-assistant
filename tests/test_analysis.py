from datetime import datetime

import pytest

from jarvis import ai, analysis, config
from jarvis.models import Mail
from jarvis.web import NewsItem


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    monkeypatch.setattr(config, "KEYWORDS", ["interview"])
    monkeypatch.setattr(config, "VIP_DOMAINS", ["uwe.ac.uk"])
    monkeypatch.setattr(config, "VIP_SENDERS", [])
    monkeypatch.setattr(config, "MUTED_DOMAINS", [])


class FakeBrain:
    def __init__(self, text="Claude's briefing.", json=None, error=None):
        self.text, self.json, self.error, self.prompts = text, json, error, []

    def complete(self, prompt, max_tokens=400, **kwargs):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.text

    def complete_json(self, prompt, max_tokens=1500, **kwargs):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.json


def item(title, source="BBC News", rank=0, section="News", summary="", link=None):
    return NewsItem(title, summary, link or f"https://{source.split()[0].lower()}/{rank}/{len(title)}",
                    section, source, rank)


NEWS = [
    item("Chancellor sets out budget plans to cut borrowing", rank=1, section="Politics"),
    item("Storm Amy brings 100mph winds to Scotland", rank=2, summary="Homes are without power."),
    item("Storm Amy: 100mph winds batter Scotland", "The Guardian", 0, summary="Gusts topped records."),
    item("Scotland hit by Storm Amy winds", "Sky News", 0),
    item("Budget: Chancellor's borrowing plans explained", "Sky News", 3, "Business"),
    item("Cat elected honorary village mayor", "Sky News", 9),
]


def test_keywords_ignore_small_words_and_endings():
    assert analysis.keywords("Storm Amy's winds batter Scotland as the homes lose power") == {
        "storm", "amy", "wind", "batter", "scotland", "home", "lose", "power"}
    assert analysis.keywords("Glass and gas") == {"glass", "gas"}


def test_the_same_story_from_several_outlets_comes_first():
    stories = analysis.stories(NEWS)
    assert [s.title for s in stories] == [
        "Storm Amy brings 100mph winds to Scotland",  # BBC's wording, though the Guardian ranked it higher
        "Chancellor sets out budget plans to cut borrowing",
        "Cat elected honorary village mayor",
    ]
    storm, budget, cat = stories
    assert storm.sources == ("BBC News", "Sky News", "The Guardian") and storm.reports == 3
    assert storm.summary == "Homes are without power." and storm.section == "News"
    assert budget.section in ("Politics", "Business") and budget.reports == 2
    assert cat.sources == ("Sky News",)


def test_a_story_borrows_a_summary_if_its_lead_has_none():
    stories = analysis.stories([item("Storm Amy hits Scotland hard"),
                                item("Storm Amy hits Scotland", "Sky News", summary="Winds of 100mph.")])
    assert stories[0].title == "Storm Amy hits Scotland hard" and stories[0].summary == "Winds of 100mph."


def test_trending_words():
    assert analysis.topics(NEWS) == ("Storm", "Amy", "Wind", "Scotland", "Chancellor")
    # ties go to the word seen first, so the same headlines always give the same list
    assert {"Storm", "Amy", "Wind", "Scotland"} <= set(analysis.topics(list(reversed(NEWS))))
    assert analysis.topics([item("One thing"), item("Another")]) == ()


def test_the_offline_news_briefing():
    brief = analysis.brief_news(NEWS)
    assert brief.spoken == ("Here's the news. The top story: Storm Amy brings 100mph winds to Scotland. "
                            "Homes are without power. Also today: Chancellor sets out budget plans to cut borrowing; "
                            "Cat elected honorary village mayor.")
    assert not brief.by_claude and len(brief.stories) == 3
    assert analysis.brief_news([]).spoken == "I couldn't find any news just now."


def test_claude_writes_the_news_briefing_when_asked():
    brain = FakeBrain("Good evening, sir. Storm Amy leads the news...")
    brief = analysis.brief_news(NEWS, use_ai=True, brain=brain)
    assert brief.by_claude and brief.spoken.startswith("Good evening, sir.")
    assert "[News, 3 outlet(s)] Storm Amy brings 100mph winds to Scotland: Homes are without power." in brain.prompts[0]
    failing = analysis.brief_news(NEWS, use_ai=True, brain=FakeBrain(error=ai.AIError("down")))
    assert not failing.by_claude and failing.spoken.startswith("Here's the news.")


def mail(id, sender, subject, snippet="", labels=(), important=False):
    name, _, address = sender.partition("|")
    return Mail(str(id), name, address, subject, snippet, datetime(2026, 10, 7, 9), important, tuple(labels))


@pytest.mark.parametrize("message, group", [
    (mail(1, "Sarah|sarah@gmail.com", "Project", "Could you send your part?"), "reply"),
    (mail(2, "Shop|offers@shop.com", "Big sale", "50% off. Can you resist?", ["CATEGORY_PROMOTIONS"]), "low"),
    (mail(3, "Friends|friend@social.com", "Photos", "New photos", ["CATEGORY_SOCIAL"]), "low"),
    (mail(4, "GitHub|noreply@github.com", "Review requested", "Can you review this?"), "update"),
    (mail(5, "UWE|registry@uwe.ac.uk", "Enrolment", "Your enrolment is complete."), "important"),
    (mail(6, "Landlord|bob@mail.com", "Rent", "The rent is due by Friday."), "important"),
    (mail(7, "Amazon|ship@amazon.co.uk", "Your order has shipped", "Arriving tomorrow."), "update"),
    (mail(8, "Mum|mum@mail.com", "Sunday", "See you on Sunday.", ["CATEGORY_PERSONAL"]), "important"),
    (mail(9, "Someone|x@mail.com", "Hello", "Just saying hello."), "update"),
    (mail(10, "Recruiter|jobs@firm.com", "Interview next steps", "We'd like to see you again."), "important"),
    (mail(11, "Recruiter|jobs@firm.com", "Interview next steps", "We'd like to see you again.",
          ["CATEGORY_PROMOTIONS"]), "low"),  # Gmail filed it under promotions: the importance rules count that
])
def test_what_each_mail_needs(message, group):
    assert analysis.classify(message) == group


@pytest.mark.parametrize("text, due", [
    ("Please reply by Friday.", "Friday"), ("The deadline is 14th October.", "14th October"),
    ("Due by 5pm, thanks", "5pm"), ("Send it before end of the week", "end of the week"),
    ("Book by Oct 20th", "Oct 20th"), ("No rush at all.", ""),
])
def test_deadlines(text, due):
    assert analysis.due_date(mail(1, "A|a@b.com", "Subject", text)) == due


def test_summaries_skip_the_greeting_and_stay_short():
    assert analysis.summarise("Hi Bertun, can you send it? Thanks a lot.") == "can you send it?"
    assert analysis.summarise("Dear team. The meeting moved.") == "The meeting moved."
    long = analysis.summarise("word " * 60)
    assert len(long) <= 110 and long.endswith("...")
    assert analysis.summarise("") == ""


INBOX = [
    mail(1, "Sarah|sarah@gmail.com", "Group project", "Hi Bertun, could you send your part by Friday?"),
    mail(2, "Amazon|ship@amazon.co.uk", "Your order has shipped", "Arriving tomorrow."),
    mail(3, "UWE|registry@uwe.ac.uk", "Enrolment", "Your enrolment is complete."),
    mail(4, "Shop|offers@shop.com", "Big sale", "Unsubscribe", ["CATEGORY_PROMOTIONS"]),
]


def test_the_offline_mail_briefing():
    brief = analysis.brief_mail(INBOX)
    assert [n.group for n in brief.notes] == ["reply", "important", "update", "low"]
    assert brief.notes[0].due == "Friday" and brief.notes[0].summary == "could you send your part by Friday?"
    assert brief.spoken == ('You have 4 unread emails. 1 needs a reply: Sarah about "Group project", due Friday. '
                            '1 looks important: UWE about "Enrolment". The other 2 are updates and newsletters.')
    assert brief.count("reply") == 1 and brief.count("low") == 1 and not brief.by_claude


def test_mail_briefing_wording():
    assert analysis.brief_mail([]).spoken == "Your inbox is clear: no unread email."
    updates = analysis.brief_mail([INBOX[1]])
    assert updates.spoken == "You have 1 unread email. None of them need you: they're updates and newsletters."
    asks = [mail(n, f"P{n}|p{n}@mail.com", f"Q{n}", "Can you help?") for n in range(5)]
    spoken = analysis.brief_mail(asks).spoken
    assert spoken.startswith('You have 5 unread emails. 5 need a reply: P0 about "Q0"; P1 about "Q1"; '
                             'and P2 about "Q2", and more.')
    no_subject = analysis.brief_mail([mail(9, "Ann|ann@mail.com", "", "Can you call me?")]).spoken
    assert no_subject == "You have 1 unread email. 1 needs a reply: Ann."


def test_claude_analyses_the_mail_when_allowed():
    brain = FakeBrain(json={
        "mails": [{"id": "2", "group": "important", "summary": "Parcel arrives tomorrow", "due": "tomorrow"},
                  {"id": "4", "group": "nonsense", "summary": ""},
                  "not a dict"],
        "briefing": "Sarah needs your part by Friday, sir.",
    })
    brief = analysis.brief_mail(INBOX, use_ai=True, brain=brain)
    assert brief.by_claude and brief.spoken == "Sarah needs your part by Friday, sir."
    notes = {n.mail.id: n for n in brief.notes}
    assert (notes["2"].group, notes["2"].summary, notes["2"].due) == ("important", "Parcel arrives tomorrow", "tomorrow")
    assert notes["4"].group == "low"  # an unknown group keeps the rules' answer
    assert notes["1"].group == "reply"  # missing from Claude's answer: the rules' answer
    assert 'id 1: from Sarah <sarah@gmail.com>, subject "Group project"' in brain.prompts[0]


def test_mail_analysis_falls_back_to_the_rules():
    failing = analysis.brief_mail(INBOX, use_ai=True, brain=FakeBrain(error=ai.AIError("no credit")))
    assert not failing.by_claude and failing.spoken.startswith("You have 4 unread emails.")
    no_briefing = analysis.brief_mail(INBOX, use_ai=True, brain=FakeBrain(json={"mails": []}))
    assert no_briefing.by_claude and no_briefing.spoken.startswith("You have 4 unread emails.")
    brain = FakeBrain(json={})
    analysis.brief_mail([], use_ai=True, brain=brain)
    assert brain.prompts == []  # nothing to analyse, nothing sent
