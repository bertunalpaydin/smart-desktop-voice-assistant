"""Reads the news and your inbox for you, and says what matters in a few sentences.

Simple rules do it offline. With a Claude API key (Settings > AI), Claude writes the
briefings instead, in Jarvis's voice.
"""
import re
from collections import Counter
from dataclasses import dataclass

from . import ai, importance
from .models import Mail

# --- news ------------------------------------------------------------------------------

STOPWORDS = set("""
a about after again against ahead all also amid an and any are around as at back be been before being between
but by can could day days did do does down during each few for from get gets go going had has have he her here
his how i if in into is it its just last least less like make makes man may me more most much my new news no
not now of off on one only or other our out over own says say said see she should so some still take than that
the their them then there these they this those three through to too two under until up us very want was we
week were what when where which while who why will with woman would year years yet you your
""".split())


@dataclass(frozen=True)
class Story:
    title: str
    summary: str
    link: str
    section: str
    sources: tuple  # the outlets that report it
    reports: int  # how many items in the feeds are about it


@dataclass(frozen=True)
class NewsBrief:
    stories: tuple
    topics: tuple  # words that keep coming up
    spoken: str
    by_claude: bool = False


def _words(title):
    """The meaningful words of a headline, in order, each once."""
    words = []
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\u2019-]+", title):  # "100mph" stays one word
        word = word.casefold().removesuffix("'s").removesuffix("\u2019s").strip("'-")
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]  # strikes -> strike
        if len(word) > 2 and word not in STOPWORDS and word not in words:
            words.append(word)
    return words


def keywords(title):
    return set(_words(title))


def _same_story(a, b):
    shared = a & b
    return len(shared) >= 2 and len(shared) >= 0.4 * min(len(a), len(b))


def stories(items):
    """Group items about the same thing and order the groups by how big the story is."""
    groups = []  # [keywords, items]
    for item in items:
        words = keywords(item.title)
        for group in groups:
            if _same_story(words, group[0]):
                group[0] |= words
                group[1].append(item)
                break
        else:
            groups.append([set(words), [item]])

    def weight(group):
        members = group[1]
        outlets = {i.source for i in members}
        top = min(i.rank for i in members)
        on_front_page = any(i.rank < 5 and i.section in ("News", "Top stories") for i in members)
        return 3 * len(outlets) + len(members) + 2 * on_front_page + max(0, 10 - top) * 0.3

    result = []
    for group in sorted(groups, key=weight, reverse=True):
        members = sorted(group[1], key=lambda i: (i.source != "BBC News", i.rank))  # BBC's wording is shortest
        lead = members[0]
        sections = Counter(i.section for i in members if i.section not in ("News", "Top stories"))
        result.append(Story(
            title=lead.title,
            summary=next((i.summary for i in members if i.summary), ""),
            link=lead.link,
            section=sections.most_common(1)[0][0] if sections else "News",
            sources=tuple(sorted({i.source for i in members})),
            reports=len(members),
        ))
    return result


def topics(items, count=5):
    """Words that keep coming up in today's headlines."""
    counts = Counter(word for item in items for word in _words(item.title))  # ties: first seen first
    common = [word for word, n in counts.most_common() if n >= 2 and not any(ch.isdigit() for ch in word)]
    return tuple(word.title() for word in common[:count])


def _sentence(text):
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def news_spoken(top, label="news"):
    if not top:
        return f"I couldn't find any {label} just now."
    lead = top[0]
    parts = [f"Here's the {label}. The top story: {_sentence(lead.title)}"]
    if lead.summary:
        parts.append(_sentence(lead.summary))
    if len(top) > 1:
        parts.append("Also today: " + "; ".join(s.title.rstrip(".") for s in top[1:4]) + ".")
    return " ".join(parts)


NEWS_PROMPT = """Here are today's top news stories, the biggest first. Each line has the section, how many \
outlets report it, the headline and a summary.

{stories}

Write my spoken news briefing, about 110 words. Start with the biggest story and say in one sentence why it \
matters. Then cover three or four of the other stories, linking them naturally. You may end with one light, \
tasteful remark about a light story, but never joke about deaths, disasters, war or anyone's suffering."""


def brief_news(items, use_ai=False, count=8, brain=ai, label="news"):
    ranked = stories(items)
    top = tuple(ranked[:count])
    spoken, by_claude = news_spoken(top, label), False
    if use_ai and top:
        lines = "\n".join(f"- [{s.section}, {len(s.sources)} outlet(s)] {s.title}: {s.summary}" for s in top)
        try:
            spoken, by_claude = brain.complete(NEWS_PROMPT.format(stories=lines), max_tokens=500), True
        except ai.AIError:
            pass  # the offline briefing is still there
    return NewsBrief(top, topics(items), spoken, by_claude)


# --- mail ------------------------------------------------------------------------------

GROUPS = ("reply", "important", "update", "low")
GROUP_NAMES = {"reply": "Needs a reply", "important": "Important", "update": "Update", "low": "Low priority"}

ASKS = re.compile(r"\?|\b(could you|can you|would you|will you|please (reply|respond|confirm|let me know|send|review|"
                  r"check|fill|sign|complete)|let me know|are you (available|free)|get back to me|your thoughts|rsvp|"
                  r"action required|awaiting your|reply by)\b", re.I)
AUTOMATIC = re.compile(r"no-?reply|do-?not-?reply|notifications?@|alerts?@|mailer|newsletter|news@|marketing|"
                       r"offers?@|promo|digest@", re.I)
PROMOTION = re.compile(r"\bunsubscribe\b|\d+% off|\bsale\b|\bdeals?\b|\bdiscount|\boffer ends\b|\bfree delivery\b",
                       re.I)
NOTICE = re.compile(r"\b(receipt|order|shipped|dispatched|delivered|delivery|confirmation|confirmed|invoice|"
                    r"statement|verification code|security alert|sign-in|password|your account|booking)\b", re.I)
_DAY = (r"(?:this |next )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tonight|tomorrow|"
        r"end of (?:the )?(?:day|week|month)|noon|midnight)")
_DATE = (r"\d{1,2}(?:st|nd|rd|th)?(?: of)? (?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*|"
         r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]* \d{1,2}(?:st|nd|rd|th)?")
DUE = re.compile(rf"\b(?:by|before|due(?: on| by)?|deadline(?: is| of)?|no later than)\s+({_DAY}|{_DATE}|"
                 rf"\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm))", re.I)
GREETING = re.compile(r"^\s*(hi|hello|hey|dear|good (morning|afternoon|evening))\b[^,.!\n]{0,40}[,.!]?\s*", re.I)


@dataclass(frozen=True)
class MailNote:
    mail: Mail
    group: str
    summary: str
    due: str = ""


@dataclass(frozen=True)
class MailBrief:
    notes: tuple
    spoken: str
    by_claude: bool = False

    def count(self, group):
        return sum(1 for note in self.notes if note.group == group)


def summarise(text, limit=110):
    """The first sentence of a mail, without the greeting."""
    text = GREETING.sub("", " ".join((text or "").split()))
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0] if text else ""
    return first if len(first) <= limit else first[: limit - 3].rsplit(" ", 1)[0] + "..."


def classify(mail):
    text = f"{mail.subject} {mail.snippet}"
    labels = set(mail.labels)
    automatic = bool(AUTOMATIC.search(mail.sender_email))
    flagged = importance.is_important(mail)
    if not flagged and (labels & {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"} or PROMOTION.search(text)):
        return "low"
    if ASKS.search(text) and not automatic:
        return "reply"
    if flagged or DUE.search(text):
        return "important"
    if automatic or labels & {"CATEGORY_UPDATES", "CATEGORY_FORUMS"} or NOTICE.search(text):
        return "update"
    return "important" if "CATEGORY_PERSONAL" in labels else "update"


def due_date(mail):
    match = DUE.search(f"{mail.subject} {mail.snippet}")
    return match.group(1) if match else ""


def _about(note):
    text = f'{note.mail.sender} about "{note.mail.subject}"' if note.mail.subject else f"{note.mail.sender}"
    return text + (f", due {note.due}" if note.due else "")


def _join(parts):
    return parts[0] if len(parts) == 1 else "; ".join(parts[:-1]) + "; and " + parts[-1]


def mail_spoken(notes):
    if not notes:
        return "Your inbox is clear: no unread email."
    parts = [f"You have {len(notes)} unread email{'' if len(notes) == 1 else 's'}."]
    for group, verb in (("reply", "need{s} a reply"), ("important", "look{s} important")):
        chosen = [n for n in notes if n.group == group]
        if chosen:
            listed = _join([_about(n) for n in chosen[:3]]) + (", and more" if len(chosen) > 3 else "")
            parts.append(f"{len(chosen)} {verb.format(s='s' if len(chosen) == 1 else '')}: {listed}.")
    rest = sum(1 for n in notes if n.group in ("update", "low"))
    if rest == len(notes):
        parts.append("None of them need you: they're updates and newsletters.")
    elif rest:
        parts.append("The other one is an update or a newsletter." if rest == 1 else
                     f"The other {rest} are updates and newsletters.")
    return " ".join(parts)


MAIL_PROMPT = """These are my unread emails. Each has an id, the sender, the subject and its first lines.

{mails}

Answer with JSON only, in this shape:
{{"mails": [{{"id": "...", "group": "reply", "summary": "what it is about, at most 15 words", "due": ""}}],
 "briefing": "..."}}
group is one of: reply (someone expects an answer or action from me), important (worth reading soon), \
update (automatic notices, receipts, notifications), low (promotions, newsletters, social media).
due is a deadline if the email gives one, otherwise empty.
briefing is a spoken summary for me of about 70 words: what needs my attention first and why. \
Mention updates and newsletters only as a count. Plain sentences, no lists."""


def _with_ai(notes, brain):
    lines = "\n".join(f'- id {n.mail.id}: from {n.mail.sender} <{n.mail.sender_email}>, subject "{n.mail.subject}": '
                      f"{n.mail.snippet}" for n in notes)
    answer = brain.complete_json(MAIL_PROMPT.format(mails=lines), max_tokens=1500)
    found = {str(m.get("id")): m for m in answer.get("mails", []) if isinstance(m, dict)}
    better = []
    for note in notes:
        m = found.get(note.mail.id, {})
        group = m.get("group") if m.get("group") in GROUPS else note.group
        summary = str(m.get("summary") or note.summary).strip()
        better.append(MailNote(note.mail, group, summary, str(m.get("due") or note.due).strip()))
    better.sort(key=lambda n: GROUPS.index(n.group))
    spoken = str(answer.get("briefing") or "").strip() or mail_spoken(better)
    return MailBrief(tuple(better), spoken, by_claude=True)


def brief_mail(mails, use_ai=False, brain=ai):
    notes = sorted((MailNote(m, classify(m), summarise(m.snippet) or m.subject, due_date(m)) for m in mails),
                   key=lambda n: GROUPS.index(n.group))
    if use_ai and notes:
        try:
            return _with_ai(notes, brain)
        except ai.AIError:
            pass  # the rules' version is still there
    return MailBrief(tuple(notes), mail_spoken(notes))
