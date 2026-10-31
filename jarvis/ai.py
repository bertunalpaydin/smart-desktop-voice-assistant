"""Optional: Claude, through Anthropic's API, as Jarvis's brain.

Off until an API key is set in Settings > AI. Everything else in Jarvis works without it.
"""
import json
import re
import urllib.error
import urllib.request

from . import config

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODELS = {
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5 (fast, costs least)",
    "claude-sonnet-5-5": "Claude Sonnet 5.5 (smarter)",
}
PERSONA = (
    "You are JARVIS, the user's personal AI assistant on their computer, in the spirit of Tony Stark's AI: "
    "calm, polite, British, dry and quietly witty, never servile. You speak through a speaker, so answer in plain "
    "spoken sentences: no lists, headings, markdown or emoji. Keep answers short, one to three sentences, unless "
    "asked for more. Always answer in English."
)


class AIError(Exception):
    pass


def enabled():
    return bool(config.AI_API_KEY.strip())


def model_name():
    return MODELS.get(config.AI_MODEL, config.AI_MODEL).split(" (")[0]


def complete(prompt, system=PERSONA, history=(), max_tokens=400, timeout=40):
    """Claude's answer to the prompt, as text."""
    if not enabled():
        raise AIError("no API key is set")
    body = json.dumps({
        "model": config.AI_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [*history, {"role": "user", "content": prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": config.AI_API_KEY.strip(),
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AIError(_explain(exc)) from exc
    except (OSError, ValueError) as exc:  # no internet, timeout, garbled reply
        raise AIError(f"couldn't reach Claude ({exc})") from exc
    blocks = data.get("content") if isinstance(data, dict) else None
    text = "".join(b.get("text", "") for b in blocks or [] if isinstance(b, dict) and b.get("type") == "text").strip()
    if not text:
        raise AIError("Claude sent an empty answer")
    return text


def complete_json(prompt, system=PERSONA, max_tokens=1500):
    """Claude's answer as a JSON object (the prompt must ask for one)."""
    text = complete(prompt, system=system, max_tokens=max_tokens)
    match = re.search(r"\{.*\}", text, re.DOTALL)  # tolerate ```json fences or a sentence around it
    try:
        value = json.loads(match.group(0)) if match else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise AIError("Claude's answer wasn't in the expected form")
    return value


def _explain(exc):
    try:
        message = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
    except (OSError, ValueError, AttributeError):
        message = ""
    if exc.code == 401:
        return "the API key was rejected; check it in Settings"
    if exc.code == 429:
        return "too many requests just now; try again in a minute"
    if "credit balance" in message.lower():
        return "the API account has no credit left"
    if exc.code >= 500:
        return "Claude is busy or down at the moment"
    return f"Claude refused the request ({exc.code}{': ' + message if message else ''})"
