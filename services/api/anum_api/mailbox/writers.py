"""Every writer of a mailbox, by ids only (migration ``0069_mail_writers_uidvalidity``).

Messages written in a shared mailbox are private to each writer (the restrictive owner policy of
``mail_drafts``), so the mailbox's owner cannot see the others' rows. Two things need all of them:

* :func:`recent_sends`: the hourly send limit of forwarding rules counts every writer's sends of
  the mailbox (sent, sending or unknown), not only the owner's;
* :func:`writers_of`: disconnecting a mailbox collects every writer's stored attachment files, so
  none is left in object storage when the rows go with the mailbox.

With PostgreSQL both ask as ``anum_maintenance`` (``maintenance.discover``), which sees only the
writer, the mailbox, the status and the time of those rows; the files themselves are then read
in each writer's own scope as the application role.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from ..settings import settings
from .store import DraftStatus, mail_backend

SENT_LIKE = (DraftStatus.SENT, DraftStatus.SENDING, DraftStatus.UNKNOWN)

_RECENT = text(
    "select count(*) from mail_drafts where account_id = :account_id "
    "and status in ('sent', 'sending', 'unknown') and updated_at >= :since"
)
# Visible to the maintenance role only while the mailbox is marked disconnecting.
_WRITERS = text("select distinct owner_user_id from mail_drafts where account_id = :account_id order by owner_user_id")


def recent_sends(account_id: str, since: datetime) -> int:
    """Messages of this mailbox sent (or maybe sent) since ``since``, by every writer."""
    if settings.repository_backend == "memory":
        with mail_backend.lock:
            return sum(1 for d in mail_backend.drafts.values() if d.account_id == account_id and d.status in SENT_LIKE and d.updated_at >= since)
    from ..maintenance import default_session_factory, discover

    rows = discover(default_session_factory, _RECENT, {"account_id": account_id, "since": since})
    return int(rows[0][0]) if rows else 0


def writers_of(account_id: str) -> list[str]:
    """Everyone who wrote a message in this mailbox (it must be marked disconnecting first)."""
    if settings.repository_backend == "memory":
        with mail_backend.lock:
            return sorted({d.owner_user_id for d in mail_backend.drafts.values() if d.account_id == account_id})
    from ..maintenance import default_session_factory, discover

    return [row[0] for row in discover(default_session_factory, _WRITERS, {"account_id": account_id})]
