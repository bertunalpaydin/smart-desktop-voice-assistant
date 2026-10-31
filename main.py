import argparse
import sqlite3
import sys

from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error

from jarvis import alarms, commands, compose, config, dictation, importance, powershell, settings, speech, windows
from jarvis.auth import AuthError
from jarvis.gmail_client import GmailClient
from jarvis.models import Draft
from jarvis.notifier import notify
from jarvis.store import Store
from jarvis.watcher import watch


def show(mails):
    if not mails:
        print("No unread mail in the inbox.")
        return
    print(f"Latest {len(mails)} unread ( ! = rated important, rules are in jarvis/config.py )\n")
    for i, m in enumerate(mails, 1):
        points, reasons = importance.score(m)
        mark = "!" if points >= config.IMPORTANT_THRESHOLD else " "
        when = m.received.strftime("%d %b %H:%M") if m.received else ""
        why = f"  [{points}: {', '.join(reasons)}]" if reasons else ""
        print(f"{i:>2}.{mark} {m.sender}  {when}{why}")
        print(f"       {m.subject}")


def pick(mails, number):
    """Mail `number` from the list (counting from 1), or None after saying why."""
    if 1 <= number <= len(mails):
        return mails[number - 1]
    print(f"There is no mail number {number}. Run 'python main.py' to see the list.")
    return None


def read(client, number=None):
    """Read one mail from the list aloud, or all the important ones."""
    mails = client.unread()
    if number is not None:
        mail = pick(mails, number)
        if mail is None:
            return 1
        chosen = [mail]
    else:
        chosen = [m for m in mails if importance.is_important(m)]
        summary = speech.summary(len(chosen))
        print(summary)
        speech.speak(summary)

    for m in chosen:
        print(f"Reading: {m.sender} - {m.subject}")
        body = client.body(m.id) or m.snippet
        speech.speak(speech.mail_text(m, body))
    return 0


def reply(client, number, voice=True):
    mail = pick(client.unread(), number)
    if mail is None:
        return 1
    print(f"Replying to {mail.sender}: {mail.subject}")
    compose.write_and_send(client, client.reply_details(mail.id), voice=voice)
    return 0


def send_new(to, make_client, voice=True):
    if not compose.valid_address(to):
        print(f"'{to}' does not look like an email address.")
        return 1
    client = make_client()
    subject = compose.get_text("What is the subject?", voice=voice, multiline=False)
    draft = Draft(to=to.strip(), subject=" ".join(subject.split()) or "(no subject)")
    compose.write_and_send(client, draft, voice=voice)
    return 0


def make_shortcut():
    if sys.platform == "darwin":
        print("On a Mac, run  bash macos/install.sh  instead: it builds Jarvis.app in your Applications folder.")
        return 1
    if not windows.available():
        print("The desktop shortcut can only be made on Windows.")
        return 1
    icon = None
    try:
        from jarvis.app import save_icon
        icon = save_icon(config.APP_DIR / "jarvis.ico")
    except ImportError:
        pass  # without PyQt6 the shortcut just gets Python's icon
    try:
        path = windows.create_shortcut(icon)
    except powershell.PowerShellError as exc:
        print(f"Could not make the shortcut: {exc}")
        return 1
    print(f"Shortcut created: {path}")
    return 0


def start_app():
    try:
        from jarvis.app import run
    except ImportError as exc:
        print(f"Jarvis needs PyQt6, which isn't installed ({exc}). Run install.bat first.")
        return 1
    return run()


def ask(text, voice=False):
    """Give Jarvis one command from the terminal."""
    clients = []

    def gmail():
        if not clients:
            clients.append(GmailClient())
        return clients[0]

    # follow-up questions (like the text of an email) are asked by send_new below
    assistant = commands.Assistant(gmail=gmail, speak=speech.speak, alarm_store=alarms.AlarmStore(config.ALARMS_FILE))
    reply = assistant.handle(text)
    print(f"Jarvis: {reply.say}")
    speech.speak(reply.say)
    for more in reply.more:
        speech.speak(more)
    if reply.action == "compose":
        to = reply.data.to or input("Their email address: ").strip()
        return send_new(to, gmail, voice=voice)
    if reply.action == "inbox":
        show(gmail().unread())
    elif reply.action in ("screenshot", "settings"):
        print("(That part works in the Jarvis app.)")
    return 0


def talk():
    """Listen for one spoken command, then do it."""
    print("Listening... speak now.")
    try:
        text = dictation.listen(timeout=6, phrase_limit=15)
    except (dictation.DictationError, dictation.DictationUnavailable) as exc:
        print(f"Didn't work: {exc}")
        return 1
    print(f"You: {text}")
    return ask(text, voice=True)


def show_voices():
    try:
        installed, selected = speech.voices()
    except speech.SpeechError as exc:
        print(f"Speech problem: {exc}")
        return 1
    if not installed:
        print("No voices are installed.")
        return 1
    for v in installed:
        mark = "*" if v.get("name") == selected else " "
        print(f" {mark} {v.get('name')} ({v.get('culture')})")
    print("\n* = in use. To pick another one, set VOICE in jarvis/config.py.")
    speech.speak("This is how your emails will be read.")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="main.py", description="Jarvis, a desktop assistant")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("app", help="start Jarvis in the system tray")
    ask_cmd = commands.add_parser("ask", help='give Jarvis a command, e.g. ask "set a timer for 10 minutes"')
    ask_cmd.add_argument("text", nargs="+")
    commands.add_parser("talk", help="say one command to Jarvis")
    commands.add_parser("list", help="show unread mail and how it is rated (default)")
    read_cmd = commands.add_parser("read", help="read important unread mail aloud")
    read_cmd.add_argument("number", nargs="?", type=int, help="read this mail from the list instead")
    watch_cmd = commands.add_parser("watch", help="check the inbox regularly, announce important mail")
    watch_cmd.add_argument("--quiet", action="store_true", help="notify only, don't read aloud")
    reply_cmd = commands.add_parser("reply", help="answer a mail from the list, by voice or typing")
    reply_cmd.add_argument("number", type=int, help="the mail's number in the list")
    reply_cmd.add_argument("--type", action="store_true", help="type instead of speaking")
    send_cmd = commands.add_parser("send", help="write a new mail, by voice or typing")
    send_cmd.add_argument("to", help="the address to send to")
    send_cmd.add_argument("--type", action="store_true", help="type instead of speaking")
    commands.add_parser("voices", help="list the installed voices and play a test")
    commands.add_parser("toast", help="show a test notification")
    commands.add_parser("shortcut", help="put a Jarvis shortcut on the desktop (Windows)")
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # odd characters must not crash printing
    args = build_parser().parse_args(argv)
    command = args.command or "list"
    config.adopt_old_app_dir()
    settings.load()
    try:
        if command == "app":
            return start_app()
        if command == "ask":
            return ask(" ".join(args.text))
        if command == "talk":
            return talk()
        if command == "shortcut":
            return make_shortcut()
        if command == "toast":
            shown = notify("Jarvis", "Test notification. If you can see this, toasts work.")
            return 0 if shown else 1
        if command == "voices":
            return show_voices()
        if command == "read":
            return read(GmailClient(), args.number)
        if command == "reply":
            return reply(GmailClient(), args.number, voice=not args.type)
        if command == "send":
            return send_new(args.to, GmailClient, voice=not args.type)
        if command == "watch":
            store = Store(config.DB_FILE)
            try:
                quiet = args.quiet or not config.READ_NEW_ALOUD
                watch(GmailClient, store, speak_fn=None if quiet else speech.speak)
            finally:
                store.close()
        else:
            show(GmailClient().unread())
    except AuthError as exc:
        print(f"Login problem: {exc}")
        return 1
    except HttpError as exc:
        print(f"Gmail API error: {exc}")
        return 1
    except (OSError, HttpLib2Error, TransportError) as exc:
        print(f"Connection problem: {exc}")
        return 1
    except sqlite3.Error as exc:
        print(f"Database problem ({config.DB_FILE}): {exc}")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nStopped.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
