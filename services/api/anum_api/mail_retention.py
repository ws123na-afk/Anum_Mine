"""Delete what ANUM keeps of a mailbox past that mailbox's retention (docs/email.md#retention).

    python -m anum_api.mail_retention --dry-run   # count only
    python -m anum_api.mail_retention             # delete

Each mailbox's owner chooses, through an approved settings change, how many days ANUM keeps
the messages written in ANUM once **sent** (``sent_retention_days``, counted from when they were
sent), **unsent drafts** (``draft_retention_days``: drafts, failed and unknown sends, counted from
their last change) and **AI labels** (``label_retention_days``, from their last change). Empty
keeps them while the mailbox is connected (the default). A message waiting for approval, scheduled
or being sent is never deleted. Mail on the mail server is not ANUM's copy and is never touched.

``python -m anum_api.voice_retention`` (the daily retention CronJob) runs this purge too and
reports it under ``mail``, so a deployment needs no extra schedule.

Work is discovered as ``anum_maintenance``: per mailbox with a retention, the retention days and
the people who wrote something in it (``account_id`` and ``owner_user_id`` of drafts and labels,
migration ``0068_mail_rules_retention``), never content. Each person's rows of that mailbox are
then deleted in one transaction as the application role in their own tenant, workspace and user
RLS context; the stored attachment files go after the commit. The output is counts only.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import text

from .maintenance import SessionFactory, default_session_factory, discover, scoped_unit
from .schemas import utc_now
from .settings import settings

logger = logging.getLogger("anum.mail")

SENT = ("sent",)
UNSENT = ("draft", "failed", "unknown")

# As anum_maintenance: mailboxes with a retention and each person who wrote in them.
_DISCOVER = text(
    """
    select s.tenant_id, s.workspace_id, s.account_id, w.writer,
           s.sent_retention_days, s.draft_retention_days, s.label_retention_days
    from mail_account_settings s
    join lateral (
      select d.owner_user_id as writer from mail_drafts d where d.account_id = s.account_id
      union
      select l.owner_user_id from mail_labels l where l.account_id = s.account_id
    ) w on true
    where (s.sent_retention_days is not null or s.draft_retention_days is not null or s.label_retention_days is not null)
      and (s.account_id, w.writer) > (:after_account, :after_writer)
    order by s.account_id, w.writer
    limit :limit
    """
)
# As the application role in the writer's own scope.
_DOOMED = (
    "from mail_drafts where tenant_id = :tenant_id and workspace_id = :workspace_id and owner_user_id = :user_id "
    "and account_id = :account_id and ("
    "(status = 'sent' and coalesce(sent_at, updated_at) < cast(:sent_cutoff as timestamptz)) "
    "or (status in ('draft', 'failed', 'unknown') and updated_at < cast(:draft_cutoff as timestamptz)))"
)
_FILES = text(f"select id, storage_key from mail_attachments where draft_id in (select id {_DOOMED})")  # nosec B608 - constant text, values are bound
_DELETE_DRAFTS = text(f"delete {_DOOMED}")  # nosec B608 - constant text, values are bound
_DELETE_LABELS = text(
    "delete from mail_labels where tenant_id = :tenant_id and workspace_id = :workspace_id and owner_user_id = :user_id "
    "and account_id = :account_id and updated_at < cast(:label_cutoff as timestamptz)"
)


@dataclass
class MailPurgeResult:
    dry_run: bool
    mailboxes_scanned: int = 0
    sent: int = 0
    drafts: int = 0
    attachments: int = 0
    labels: int = 0


def _cutoff(now: datetime, days: int | None) -> datetime | None:
    return None if days is None else now - timedelta(days=days)


def _forget(keys: list[tuple[str, str]]) -> None:
    from .files import file_store

    for attachment_id, key in keys:
        try:
            file_store.storage.delete(key)
        except Exception:  # noqa: BLE001 - the row is already gone; a stray object is only storage
            logger.warning("mail.attachment_object_delete_failed", extra={"anum_mail": {"attachment_id": attachment_id}})


def purge_mail_retention(
    session_factory: SessionFactory = default_session_factory,
    *,
    maintenance_session_factory: SessionFactory | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> MailPurgeResult:
    now = now or utc_now()
    result = MailPurgeResult(dry_run=dry_run)
    after = ("", "")
    seen: set[str] = set()
    while True:
        rows = discover(maintenance_session_factory or session_factory, _DISCOVER, {"after_account": after[0], "after_writer": after[1], "limit": batch_size})
        for row in rows:
            if row.account_id not in seen:
                seen.add(row.account_id)
                result.mailboxes_scanned += 1
            params = {
                "tenant_id": row.tenant_id, "workspace_id": row.workspace_id, "user_id": row.writer, "account_id": row.account_id,
                "sent_cutoff": _cutoff(now, row.sent_retention_days), "draft_cutoff": _cutoff(now, row.draft_retention_days),
                "label_cutoff": _cutoff(now, row.label_retention_days),
            }
            with scoped_unit(session_factory, row.tenant_id, row.workspace_id, user_id=row.writer) as session:
                sent = session.execute(text(f"select count(*) {_DOOMED} and status = 'sent'"), params).scalar_one()  # nosec B608 - constant text
                files = [(r.id, r.storage_key) for r in session.execute(_FILES, params)]
                deleted = session.execute(_DELETE_DRAFTS, params).rowcount or 0
                labels = session.execute(_DELETE_LABELS, params).rowcount or 0
                if dry_run:
                    session.rollback()
            if not dry_run:
                _forget(files)
            result.sent += int(sent)
            result.drafts += deleted - int(sent)
            result.attachments += len(files)
            result.labels += labels
        if len(rows) < batch_size:
            return result
        after = (rows[-1].account_id, rows[-1].writer)


def purge_memory(now: datetime | None = None, dry_run: bool = False) -> MailPurgeResult:
    """The same purge for ``ANUM_REPOSITORY_BACKEND=memory`` (local and tests)."""
    from .mailbox.automation import automation_backend
    from .mailbox.labels import label_backend
    from .mailbox.outgoing import forget_objects
    from .mailbox.store import DraftStatus, mail_backend

    now = now or utc_now()
    result = MailPurgeResult(dry_run=dry_run)
    with mail_backend.lock, automation_backend.lock, label_backend.lock:
        for kept in list(automation_backend.settings.values()):
            if kept.account_id not in mail_backend.accounts:
                continue
            if kept.sent_retention_days is None and kept.draft_retention_days is None and kept.label_retention_days is None:
                continue
            result.mailboxes_scanned += 1
            sent_cutoff, draft_cutoff, label_cutoff = (_cutoff(now, kept.sent_retention_days), _cutoff(now, kept.draft_retention_days), _cutoff(now, kept.label_retention_days))
            doomed = [
                d for d in mail_backend.drafts.values()
                if d.account_id == kept.account_id and (
                    (sent_cutoff is not None and d.status == DraftStatus.SENT and (d.sent_at or d.updated_at) < sent_cutoff)
                    or (draft_cutoff is not None and d.status.value in UNSENT and d.updated_at < draft_cutoff)
                )
            ]
            files = [a for a in mail_backend.attachments.values() if a.draft_id in {d.id for d in doomed}]
            labels = [key for key, label in label_backend.labels.items() if label.account_id == kept.account_id and label_cutoff is not None and label.updated_at < label_cutoff]
            result.sent += sum(1 for d in doomed if d.status == DraftStatus.SENT)
            result.drafts += sum(1 for d in doomed if d.status != DraftStatus.SENT)
            result.attachments += len(files)
            result.labels += len(labels)
            if dry_run:
                continue
            for draft in doomed:
                del mail_backend.drafts[draft.id]
            for attachment in files:
                del mail_backend.attachments[attachment.id]
            for key in labels:
                del label_backend.labels[key]
            forget_objects(files)
    return result


def purge(
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict[str, object]:
    if settings.repository_backend == "memory":
        return asdict(purge_memory(dry_run=dry_run))
    return asdict(purge_mail_retention(session_factory, maintenance_session_factory=maintenance_session_factory, dry_run=dry_run, batch_size=batch_size))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m anum_api.mail_retention", description="Delete what ANUM keeps of each mailbox past its retention.")
    parser.add_argument("--dry-run", action="store_true", help="count what would be deleted")
    parser.add_argument("--batch-size", type=int, default=500, help="rows discovered per query")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if settings.repository_backend != "postgresql":
        print(json.dumps({"error": "mail_retention needs ANUM_REPOSITORY_BACKEND=postgresql"}), file=sys.stderr)
        return 2
    print(json.dumps(purge(dry_run=args.dry_run, batch_size=args.batch_size), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
