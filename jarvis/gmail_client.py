import base64
import html
import re
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import config
from .auth import get_credentials
from .models import Draft, Mail

RETRIES = 3

# A line where the quoted earlier mail or the signature starts.
_REPLY_START = re.compile(r"On .+ wrote:|-+ ?Original Message ?-+|_{10,}|--", re.IGNORECASE)


def _clean(text):
    return " ".join(text.split())


def _decode(value):
    try:
        return _clean(str(make_header(decode_header(value))))
    except Exception:  # malformed header, keep the raw text
        return _clean(value)


def _parse_date(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone()
    except (TypeError, ValueError):
        return None


def _to_mail(msg):
    headers = {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
    }
    name, address = parseaddr(headers.get("from", ""))
    labels = tuple(msg.get("labelIds", []))
    return Mail(
        id=msg["id"],
        sender_name=_decode(name),
        sender_email=address,
        subject=_decode(headers.get("subject", "")) or "(no subject)",
        snippet=html.unescape(msg.get("snippet", "")),
        received=_parse_date(headers.get("date")),
        important="IMPORTANT" in labels,
        labels=labels,
    )


class _HtmlText(HTMLParser):
    """Collects the visible text of an HTML mail."""

    HIDDEN = {"head", "script", "style", "blockquote"}  # blockquote: quoted earlier mail
    BREAKS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__()
        self.chunks = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self._hidden += 1
        elif tag in self.BREAKS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self._hidden = max(0, self._hidden - 1)
        elif tag in self.BREAKS:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self._hidden:
            self.chunks.append(data)


def _html_to_text(markup):
    parser = _HtmlText()
    parser.feed(markup)
    parser.close()
    return "".join(parser.chunks)


def _find_part(part, mime_type):
    """First part of this type that isn't an attachment, looking depth first."""
    if part.get("mimeType") == mime_type and not part.get("filename"):
        return part
    for child in part.get("parts", []):
        found = _find_part(child, mime_type)
        if found is not None:
            return found
    return None


def _part_text(part):
    data = part.get("body", {}).get("data")
    if not data:
        return ""  # empty, or too big to come inline
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except ValueError:
        return ""
    header = Message()
    for h in part.get("headers", []):
        if h["name"].lower() == "content-type":
            header["Content-Type"] = h["value"]
    charset = header.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:  # a charset Python doesn't know
        return raw.decode("utf-8", errors="replace")


def _own_text(text):
    """Leave out quoted earlier mail and the signature."""
    kept = []
    for line in text.splitlines():
        line = line.strip()
        if _REPLY_START.fullmatch(line):
            break
        if not line.startswith(">"):
            kept.append(line)
    own = "\n".join(kept).strip()
    if own:
        return own
    # The whole mail is a quote: better to read that than nothing.
    return "\n".join(line.strip().lstrip(">").strip() for line in text.splitlines()).strip()


def _body_text(payload):
    plain = _find_part(payload, "text/plain")
    text = _part_text(plain) if plain else ""
    if not text.strip():
        html_part = _find_part(payload, "text/html")
        text = _html_to_text(_part_text(html_part)) if html_part else ""
    return _own_text(text)


class GmailClient:
    def __init__(self, service=None):
        if service is None:
            service = build(
                "gmail", "v1", credentials=get_credentials(), cache_discovery=False
            )
        self._service = service

    def unread(self, limit=config.MAX_UNREAD, skip_ids=()):
        return self._mails(config.UNREAD_QUERY, limit, skip_ids)

    def latest(self, limit=1):
        """The newest mail in the inbox, read or not (newest first)."""
        return self._mails(config.LATEST_QUERY, limit)

    def _mails(self, query, limit, skip_ids=()):
        skip = set(skip_ids)
        messages = self._service.users().messages()
        listing = messages.list(
            userId="me", q=query, maxResults=limit
        ).execute(num_retries=RETRIES)

        mails = []
        for ref in listing.get("messages", []):
            if ref["id"] in skip:
                continue
            try:
                msg = messages.get(
                    userId="me",
                    id=ref["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute(num_retries=RETRIES)
            except HttpError as exc:
                if exc.resp.status == 404:  # deleted after the list call
                    continue
                raise
            mails.append(_to_mail(msg))
        return mails

    def body(self, mail_id):
        """What a mail says, as plain text, without quoted earlier mail. "" if it has no text."""
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=mail_id, format="full")
            .execute(num_retries=RETRIES)
        )
        return _body_text(msg.get("payload", {}))

    def reply_details(self, mail_id):
        """A Draft that answers this mail: right address, "Re:" subject, same conversation."""
        msg = (
            self._service.users()
            .messages()
            .get(
                userId="me",
                id=mail_id,
                format="metadata",
                metadataHeaders=["From", "Reply-To", "Subject", "Message-ID", "References"],
            )
            .execute(num_retries=RETRIES)
        )
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        _, to = parseaddr(headers.get("reply-to") or headers.get("from", ""))
        subject = _decode(headers.get("subject", ""))
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}".strip()
        message_id = _clean(headers.get("message-id", ""))
        references = " ".join(p for p in (_clean(headers.get("references", "")), message_id) if p)
        return Draft(
            to=to,
            subject=subject,
            thread_id=msg.get("threadId"),
            in_reply_to=message_id,
            references=references,
        )

    def send(self, draft):
        """Send a Draft. Returns the id of the sent mail."""
        msg = EmailMessage()
        msg["To"] = draft.to
        msg["Subject"] = draft.subject
        if draft.in_reply_to:
            msg["In-Reply-To"] = draft.in_reply_to
            msg["References"] = draft.references or draft.in_reply_to
        msg.set_content(draft.body)
        body = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")}
        if draft.thread_id:
            body["threadId"] = draft.thread_id
        # No automatic retry here: a retried send could arrive twice.
        sent = self._service.users().messages().send(userId="me", body=body).execute()
        return sent.get("id", "")
