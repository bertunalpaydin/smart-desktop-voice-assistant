"""Writing a reply or a new mail: say or type the message, hear it back, and
send it only after an explicit "y"."""
import re
from dataclasses import replace

from . import dictation, speech

_ADDRESS = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")


def valid_address(text):
    return bool(_ADDRESS.fullmatch(text.strip()))


def get_text(question, voice=True, multiline=True, ask=input, listen=None, say=None):
    """Ask for some text: dictated when voice input works, typed otherwise."""
    listen = listen or dictation.listen
    say = say or speech.speak
    if voice:
        say(question)
        print(f"{question} (speak now)")
        try:
            text = listen()
            print(f"Heard: {text}")
            return text
        except dictation.DictationUnavailable as exc:
            print(f"Voice input is not available ({exc}), so type it instead.")
        except dictation.DictationError as exc:
            print(f"{exc}, so type it instead.")

    if not multiline:
        return ask(f"{question} ").strip()
    print(f"{question} Type it below; an empty line finishes.")
    lines = []
    while True:
        line = ask("> ")
        if not line.strip():
            return "\n".join(lines).strip()
        lines.append(line.rstrip())


def write_and_send(client, draft, voice=True, ask=input, listen=None, say=None):
    """Get the message, read it back, and send it only on "y". Returns True if sent."""
    say = say or speech.speak
    question = "What should the message say?"
    draft = replace(draft, body=get_text(question, voice, True, ask, listen, say))
    while True:
        if not draft.body:
            print("The message is empty, so nothing was sent.")
            return False
        print(f"\nTo:      {draft.to}\nSubject: {draft.subject}\n\n{draft.body}\n")
        if voice:
            say(f"To {draft.to}. Subject: {draft.subject}. {draft.body}")
        answer = ask("Send it? [y] send, [r] redo the message, anything else cancels: ")
        answer = answer.strip().lower()
        if answer == "y":
            client.send(draft)
            print("Sent.")
            if voice:
                say("Sent.")
            return True
        if answer == "r":
            draft = replace(draft, body=get_text(question, voice, True, ask, listen, say))
            continue
        print("Cancelled, nothing was sent.")
        return False
