from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Mail:
    id: str
    sender_name: str
    sender_email: str
    subject: str
    snippet: str
    received: datetime | None
    important: bool = False  # Gmail's own "important" marker
    labels: tuple[str, ...] = ()

    @property
    def sender(self) -> str:
        return self.sender_name or self.sender_email


@dataclass(frozen=True)
class Draft:
    """A mail waiting to be sent."""

    to: str
    subject: str
    body: str = ""
    thread_id: str | None = None  # set for replies, keeps them in the same conversation
    in_reply_to: str = ""
    references: str = ""
