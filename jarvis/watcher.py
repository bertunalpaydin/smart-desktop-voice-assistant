import time
from datetime import datetime

from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error

from . import config, importance
from .notifier import notify
from .speech import speak

TRANSIENT = (HttpError, OSError, HttpLib2Error, TransportError)


def check_once(client, store, announce=True, notify_fn=notify, speak_fn=None):
    """Look at unread mail we haven't seen yet. Returns (new_mails, important_mails)."""
    mails = client.unread(skip_ids=store.seen_ids())
    important = [m for m in mails if importance.is_important(m)]
    if announce:
        for m in important:
            notify_fn(m.sender, m.subject)
            if speak_fn:
                speak_fn(f"New important email from {m.sender}. Subject: {m.subject}.")
    store.mark_seen(m.id for m in mails)
    return mails, important


def watch(make_client, store, interval=None, sleep=time.sleep,
          rounds=None, notify_fn=notify, speak_fn=speak, out=print):
    if interval is None:
        interval = config.POLL_SECONDS
    client = make_client()
    baseline = store.is_empty()
    if baseline:
        out("First run: mail that is already unread is marked as seen and not announced.")
    out(f"Checking the inbox every {interval}s. Ctrl+C to stop.")
    if speak_fn:
        out("New important mail is also read aloud (--quiet turns this off).")

    done = 0
    while rounds is None or done < rounds:
        done += 1
        stamp = f"{datetime.now():%H:%M}"
        try:
            mails, important = check_once(
                client, store, announce=not baseline, notify_fn=notify_fn, speak_fn=speak_fn
            )
            note = " (first run, not announced)" if baseline else ""
            baseline = False
            out(f"[{stamp}] {len(mails)} new, {len(important)} important{note}")
            for m in important:
                out(f"    ! {m.sender}: {m.subject}")
        except RefreshError:
            out(f"[{stamp}] Login expired, logging in again.")
            client = make_client()
            continue
        except TRANSIENT as exc:
            out(f"[{stamp}] Check failed ({exc}); will try again.")
        sleep(interval)
