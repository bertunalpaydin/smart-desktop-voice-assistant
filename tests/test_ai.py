import io
import json
import urllib.error

import pytest

from jarvis import ai, config


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def answer_with(monkeypatch, *, data=None, error=None):
    """Make the API answer with `data` (a dict) or fail with `error`; returns the requests sent."""
    sent = []

    def urlopen(request, timeout):
        sent.append((request, json.loads(request.data.decode("utf-8")), timeout))
        if error is not None:
            raise error
        return FakeResponse(json.dumps(data).encode("utf-8"))

    monkeypatch.setattr(ai.urllib.request, "urlopen", urlopen)
    return sent


def http_error(code, message=""):
    body = io.BytesIO(json.dumps({"type": "error", "error": {"message": message}}).encode("utf-8"))
    return urllib.error.HTTPError(ai.API_URL, code, "error", {}, body)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "  sk-ant-test  ")


def test_off_without_a_key(monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "")
    assert ai.enabled() is False
    with pytest.raises(ai.AIError, match="no API key"):
        ai.complete("hello")


def test_a_request_and_its_answer(monkeypatch, key):
    sent = answer_with(monkeypatch, data={"content": [{"type": "text", "text": "Good evening, "},
                                                      {"type": "thinking", "thinking": "..."},
                                                      {"type": "text", "text": "sir."}]})
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello."}]
    assert ai.complete("How are you?", history=history, max_tokens=50) == "Good evening, sir."
    request, body, timeout = sent[0]
    assert request.full_url == ai.API_URL and request.get_method() == "POST"
    assert request.get_header("X-api-key") == "sk-ant-test"
    assert request.get_header("Anthropic-version") == ai.API_VERSION
    assert request.get_header("Content-type") == "application/json"
    assert body == {"model": config.AI_MODEL, "max_tokens": 50, "system": ai.PERSONA,
                    "messages": [*history, {"role": "user", "content": "How are you?"}]}
    assert timeout > 0


@pytest.mark.parametrize("error, message", [
    (http_error(401), "rejected"),
    (http_error(429), "too many requests"),
    (http_error(400, "Your credit balance is too low to access the API."), "no credit left"),
    (http_error(529, "Overloaded"), "busy or down"),
    (http_error(400, "max_tokens: too big"), "refused the request (400: max_tokens: too big)"),
    (urllib.error.URLError("no route"), "couldn't reach Claude"),
    (TimeoutError("timed out"), "couldn't reach Claude"),
])
def test_problems_become_clear_messages(monkeypatch, key, error, message):
    answer_with(monkeypatch, error=error)
    with pytest.raises(ai.AIError, match=message.replace("(", r"\(").replace(")", r"\)")):
        ai.complete("hello")


@pytest.mark.parametrize("data", [{"content": []}, {"content": [{"type": "text", "text": "  "}]}, ["not", "a", "dict"]])
def test_an_empty_answer_is_an_error(monkeypatch, key, data):
    answer_with(monkeypatch, data=data)
    with pytest.raises(ai.AIError, match="empty"):
        ai.complete("hello")


def test_json_answers(monkeypatch, key):
    answer_with(monkeypatch, data={"content": [{"type": "text", "text": 'Here:\n```json\n{"a": [1, 2]}\n```'}]})
    assert ai.complete_json("give me json") == {"a": [1, 2]}
    for text in ("no json here", "[1, 2]", "{broken: json}"):
        answer_with(monkeypatch, data={"content": [{"type": "text", "text": text}]})
        with pytest.raises(ai.AIError, match="expected form"):
            ai.complete_json("give me json")


def test_model_names(monkeypatch):
    monkeypatch.setattr(config, "AI_MODEL", "claude-haiku-4-5-20251001")
    assert ai.model_name() == "Claude Haiku 4.5"
    monkeypatch.setattr(config, "AI_MODEL", "some-new-model")
    assert ai.model_name() == "some-new-model"
