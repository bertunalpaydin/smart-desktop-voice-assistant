import base64
import email
import email.policy
from email.header import Header
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from jarvis import config
from jarvis.gmail_client import GmailClient
from jarvis.models import Draft


def http_error(status):
    return HttpError(httplib2.Response({"status": str(status)}), b"{}")


def make_service(messages, ids=None):
    """Fake Gmail service. `messages` maps message id -> message dict."""
    ids = list(messages) if ids is None else ids
    service = MagicMock()
    api = service.users.return_value.messages.return_value
    api.list.return_value.execute.return_value = (
        {"messages": [{"id": i} for i in ids]} if ids else {"resultSizeEstimate": 0}
    )

    def get(userId, id, **kwargs):
        request = MagicMock()
        if id in messages:
            request.execute.return_value = messages[id]
        else:
            request.execute.side_effect = http_error(404)
        return request

    api.get.side_effect = get
    return service, api


def message(id="1", sender="Ali <ali@example.com>", subject="Hi",
            date="Tue, 29 Sep 2026 09:30:00 +0100", snippet="hello", labels=None):
    headers = [
        {"name": "From", "value": sender},
        {"name": "Subject", "value": subject},
    ]
    if date is not None:
        headers.append({"name": "Date", "value": date})
    return {
        "id": id,
        "snippet": snippet,
        "labelIds": labels or ["INBOX", "UNREAD"],
        "payload": {"headers": headers},
    }


def only_mail(msg):
    service, _ = make_service({msg["id"]: msg})
    [mail] = GmailClient(service).unread()
    return mail


def test_parses_basic_fields():
    mail = only_mail(message(labels=["INBOX", "UNREAD", "IMPORTANT"]))
    assert mail.sender_name == "Ali"
    assert mail.sender_email == "ali@example.com"
    assert mail.subject == "Hi"
    assert mail.snippet == "hello"
    assert mail.important is True
    assert mail.received is not None


def test_not_important_without_the_label():
    assert only_mail(message()).important is False


def test_decodes_encoded_headers():
    mail = only_mail(message(
        sender=Header("José García", "utf-8").encode() + " <jose@example.com>",
        subject=Header("Résumé review – 10:00 ☕", "utf-8").encode(),
    ))
    assert mail.sender_name == "José García"
    assert mail.sender_email == "jose@example.com"
    assert mail.subject == "Résumé review – 10:00 ☕"


def test_folded_subject_is_flattened():
    assert only_mail(message(subject="Very long\r\n subject line")).subject == "Very long subject line"


def test_snippet_html_entities_are_unescaped():
    mail = only_mail(message(snippet="Don&#39;t forget &amp; reply"))
    assert mail.snippet == "Don't forget & reply"


def test_missing_subject_gets_placeholder():
    assert only_mail(message(subject="")).subject == "(no subject)"


@pytest.mark.parametrize("date", ["not a date", "", None])
def test_bad_or_missing_date_is_none(date):
    assert only_mail(message(date=date)).received is None


def test_sender_without_name_falls_back_to_address():
    mail = only_mail(message(sender="ali@example.com"))
    assert mail.sender == "ali@example.com"


def test_empty_inbox_returns_empty_list():
    service, _ = make_service({})
    assert GmailClient(service).unread() == []


def test_list_uses_unread_query_and_limit():
    service, api = make_service({"1": message()})
    GmailClient(service).unread(limit=5)
    api.list.assert_called_once_with(userId="me", q=config.UNREAD_QUERY, maxResults=5)


def test_message_deleted_between_list_and_get_is_skipped():
    service, _ = make_service(
        {"1": message(id="1"), "3": message(id="3")}, ids=["1", "2", "3"]
    )
    assert [m.id for m in GmailClient(service).unread()] == ["1", "3"]


def test_other_http_errors_are_not_swallowed():
    service, api = make_service({}, ids=["1"])
    api.get.side_effect = None
    api.get.return_value.execute.side_effect = http_error(403)
    with pytest.raises(HttpError):
        GmailClient(service).unread()


def test_labels_are_kept():
    mail = only_mail(message(labels=["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"]))
    assert "CATEGORY_PROMOTIONS" in mail.labels


def test_skip_ids_are_not_fetched():
    service, api = make_service(
        {"1": message(id="1"), "2": message(id="2"), "3": message(id="3")}
    )
    mails = GmailClient(service).unread(skip_ids={"2"})
    assert [m.id for m in mails] == ["1", "3"]
    assert [c.kwargs["id"] for c in api.get.call_args_list] == ["1", "3"]


def encoded(text, charset="utf-8"):
    return base64.urlsafe_b64encode(text.encode(charset)).decode("ascii").rstrip("=")


def text_part(mime, text, charset="utf-8", filename=""):
    return {
        "mimeType": mime,
        "filename": filename,
        "headers": [{"name": "Content-Type", "value": f'{mime}; charset="{charset}"'}],
        "body": {"size": len(text), "data": encoded(text, charset)},
    }


def multipart(kind, *parts):
    return {"mimeType": f"multipart/{kind}", "filename": "", "parts": list(parts)}


def body_of(payload):
    service = MagicMock()
    api = service.users.return_value.messages.return_value
    api.get.return_value.execute.return_value = {"id": "1", "payload": payload}
    text = GmailClient(service).body("1")
    api.get.assert_called_once_with(userId="me", id="1", format="full")
    return text


def test_body_of_a_plain_text_mail():
    assert body_of(text_part("text/plain", "Hello\r\nSee you at 10.")) == "Hello\nSee you at 10."


def test_body_prefers_the_plain_text_version():
    payload = multipart(
        "alternative", text_part("text/plain", "Plain version"), text_part("text/html", "<p>HTML version</p>")
    )
    assert body_of(payload) == "Plain version"


def test_body_of_an_html_only_mail_is_its_visible_text():
    page = (
        "<html><head><title>Ignore</title><style>p {color: red}</style></head>"
        "<body><p>Hi&nbsp;Bertun,</p><script>alert(1)</script>"
        "<div>Tuesday at 10:00 &amp; online</div>"
        "<blockquote>older quoted mail</blockquote></body></html>"
    )
    text = body_of(text_part("text/html", page))
    assert text.split() == ["Hi", "Bertun,", "Tuesday", "at", "10:00", "&", "online"]


def test_body_skips_attachments_and_finds_nested_text():
    payload = multipart(
        "mixed",
        text_part("text/plain", "attached notes", filename="notes.txt"),
        multipart("alternative", text_part("text/plain", "The real message"), text_part("text/html", "<p>x</p>")),
    )
    assert body_of(payload) == "The real message"


def test_body_uses_html_when_the_plain_part_is_not_inline():
    plain = {"mimeType": "text/plain", "filename": "", "body": {"attachmentId": "abc", "size": 90000}}
    payload = multipart("alternative", plain, text_part("text/html", "<p>From the HTML part</p>"))
    assert body_of(payload) == "From the HTML part"


def test_body_uses_the_charset_of_the_part():
    assert body_of(text_part("text/plain", "Café résumé", charset="iso-8859-1")) == "Café résumé"


def test_body_with_an_unknown_charset_is_read_as_utf8():
    part = text_part("text/plain", "Hello there")
    part["headers"] = [{"name": "Content-Type", "value": 'text/plain; charset="x-made-up"'}]
    assert body_of(part) == "Hello there"


@pytest.mark.parametrize("text", [
    "Thanks, see you then.\n\nOn Tue, 30 Sep 2026 at 21:00, Ali <ali@example.com> wrote:\n> Are you free?\n> Ali",
    "Thanks, see you then.\n-- \nBertun\nUWE Bristol",
    "Thanks, see you then.\n-----Original Message-----\nFrom: Ali",
    "Thanks, see you then.\n________________________________\nFrom: Ali",
])
def test_body_leaves_out_quoted_mail_and_the_signature(text):
    assert body_of(text_part("text/plain", text)) == "Thanks, see you then."


def test_body_that_is_only_a_quote_is_still_read():
    assert body_of(text_part("text/plain", "> Are you free?\n> Ali")) == "Are you free?\nAli"


def test_body_without_any_text_is_empty():
    image = {"mimeType": "image/png", "filename": "a.png", "body": {"attachmentId": "x", "size": 10}}
    assert body_of(multipart("mixed", image)) == ""


@pytest.mark.parametrize("data", ["@@@", "a"])
def test_body_with_broken_data_is_empty(data):
    part = text_part("text/plain", "x")
    part["body"]["data"] = data
    assert body_of(part) == ""


def details_of(headers, thread_id="t1"):
    service = MagicMock()
    api = service.users.return_value.messages.return_value
    api.get.return_value.execute.return_value = {"id": "1", "threadId": thread_id, "payload": {"headers": headers}}
    draft = GmailClient(service).reply_details("1")
    asked = api.get.call_args.kwargs
    assert asked["format"] == "metadata" and "Message-ID" in asked["metadataHeaders"]
    return draft


def test_a_reply_goes_to_the_sender_in_the_same_conversation():
    draft = details_of([
        {"name": "From", "value": "Ali <ali@example.com>"},
        {"name": "Subject", "value": "Interview"},
        {"name": "Message-ID", "value": "<abc@mail>"},
    ])
    assert draft == Draft(to="ali@example.com", subject="Re: Interview", thread_id="t1",
                          in_reply_to="<abc@mail>", references="<abc@mail>")


def test_reply_to_wins_and_re_is_not_doubled():
    draft = details_of([
        {"name": "From", "value": "noreply@firm.com"},
        {"name": "Reply-To", "value": "Team <team@firm.com>"},
        {"name": "Subject", "value": "RE: Offer"},
        {"name": "Message-ID", "value": "<m2@firm>"},
        {"name": "References", "value": "<m0@firm>\r\n <m1@firm>"},
    ])
    assert draft.to == "team@firm.com"
    assert draft.subject == "RE: Offer"
    assert draft.references == "<m0@firm> <m1@firm> <m2@firm>"


def sent_mail(draft):
    service = MagicMock()
    api = service.users.return_value.messages.return_value
    api.send.return_value.execute.return_value = {"id": "sent1"}
    assert GmailClient(service).send(draft) == "sent1"
    api.send.return_value.execute.assert_called_once_with()  # no automatic retry for sending
    body = api.send.call_args.kwargs["body"]
    assert api.send.call_args.kwargs["userId"] == "me"
    raw = base64.urlsafe_b64decode(body["raw"])
    return body, email.message_from_bytes(raw, policy=email.policy.default)


def test_send_builds_a_proper_reply():
    body, msg = sent_mail(Draft(to="ali@example.com", subject="Re: Interview", body="See you at ten.\nBertun",
                                thread_id="t1", in_reply_to="<abc@mail>", references="<abc@mail>"))
    assert body["threadId"] == "t1"
    assert msg["To"] == "ali@example.com"
    assert msg["Subject"] == "Re: Interview"
    assert msg["In-Reply-To"] == "<abc@mail>"
    assert msg["References"] == "<abc@mail>"
    assert msg.get_content().strip() == "See you at ten.\nBertun"


def test_a_new_mail_has_no_thread_and_keeps_accents_and_emoji():
    body, msg = sent_mail(Draft(to="ali@example.com", subject="Café – résumé", body="Merci 🎉"))
    assert "threadId" not in body
    assert msg["In-Reply-To"] is None
    assert msg["Subject"] == "Café – résumé"
    assert msg.get_content().strip() == "Merci 🎉"



def test_latest_lists_the_inbox_read_or_not():
    service, api = make_service({"1": message(id="1")})
    assert [m.id for m in GmailClient(service).latest(3)] == ["1"]
    api.list.assert_called_once_with(userId="me", q=config.LATEST_QUERY, maxResults=3)
