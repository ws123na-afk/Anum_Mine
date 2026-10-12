"""Delete WhatsApp messages older than the workspace's retention (docs/whatsapp.md#retention).

    python -m anum_api.whatsapp_retention --dry-run   # count only
    python -m anum_api.whatsapp_retention             # delete

Each workspace chooses how long its WhatsApp history is kept
(``whatsapp_accounts.message_retention_days``, set by owners in the WhatsApp settings);
when it has not chosen, or its number was disconnected and the history stayed, the
deployment default ``ANUM_WHATSAPP_MESSAGE_RETENTION_DAYS`` applies. 0 keeps messages
until they are deleted (the default).

In a workspace whose retention is N days the purge deletes:

- messages whose ``timestamp`` is more than N days old, except those still awaiting
  approval or being sent (an approval in flight keeps its message);
- conversations with no message left whose last message is older than N days (their
  preview, summary and suggestion go with them);
- contacts with no conversation left that were last updated more than N days ago.

``python -m anum_api.voice_retention`` (the daily retention CronJob) runs this purge too
and reports it under ``whatsapp``, so a deployment needs no extra schedule.

Workspaces are discovered as ``anum_maintenance``: the scope of conversations at least a
day old, nothing else (migration ``0050_rotation_retention``). Each workspace is then
purged in one transaction as the application role inside its tenant and workspace RLS
context, where the retention setting is read. The output is counts only, never text.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import text

from .maintenance import SessionFactory, default_session_factory, discover, scoped_unit
from .schemas import utc_now
from .settings import settings

# As anum_maintenance: workspaces holding a conversation at least a day old.
_DISCOVER = text(
    """
    select distinct tenant_id, workspace_id
    from whatsapp_conversations
    where (tenant_id, workspace_id) > (:after_tenant, :after_workspace)
    order by tenant_id, workspace_id
    limit :limit
    """
)
# As the application role inside the workspace's RLS context.
_RETENTION = text(
    "select message_retention_days from whatsapp_accounts where tenant_id = :tenant_id and workspace_id = :workspace_id"
)
_DELETE_MESSAGES = text(
    """
    delete from whatsapp_messages
    where tenant_id = :tenant_id and workspace_id = :workspace_id
      and "timestamp" < :cutoff
      and status not in ('awaiting_approval', 'sending')
    """
)
_DELETE_CONVERSATIONS = text(
    """
    delete from whatsapp_conversations c
    where c.tenant_id = :tenant_id and c.workspace_id = :workspace_id
      and c.last_message_at < :cutoff
      and not exists (
        select 1 from whatsapp_messages m
        where m.tenant_id = c.tenant_id and m.workspace_id = c.workspace_id and m.conversation_id = c.id
      )
    """
)
_DELETE_CONTACTS = text(
    """
    delete from whatsapp_contacts ct
    where ct.tenant_id = :tenant_id and ct.workspace_id = :workspace_id
      and ct.updated_at < :cutoff
      and not exists (
        select 1 from whatsapp_conversations c
        where c.tenant_id = ct.tenant_id and c.workspace_id = ct.workspace_id and c.contact_id = ct.id
      )
    """
)


@dataclass
class WhatsAppPurgeResult:
    dry_run: bool
    default_retention_days: int
    workspaces_scanned: int = 0
    workspaces_purged: int = 0
    messages: int = 0
    conversations: int = 0
    contacts: int = 0


def retention_for(stored: int | None, default_days: int) -> int:
    """The days a workspace keeps its history: its own choice, else the deployment's."""
    return default_days if stored is None else stored


def purge_whatsapp_history(
    session_factory: SessionFactory = default_session_factory,
    *,
    maintenance_session_factory: SessionFactory | None = None,
    default_days: int | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> WhatsAppPurgeResult:
    from .whatsapp import whatsapp_settings

    default_days = whatsapp_settings.message_retention_days if default_days is None else default_days
    if default_days < 0:
        raise ValueError("default_days must be 0 (keep) or a positive number of days")
    now = now or utc_now()
    result = WhatsAppPurgeResult(dry_run=dry_run, default_retention_days=default_days)
    after = ("", "")
    while True:
        rows = discover(
            maintenance_session_factory or session_factory,
            _DISCOVER,
            {"after_tenant": after[0], "after_workspace": after[1], "limit": batch_size},
        )
        for row in rows:
            result.workspaces_scanned += 1
            scope = {"tenant_id": row.tenant_id, "workspace_id": row.workspace_id}
            with scoped_unit(session_factory, row.tenant_id, row.workspace_id) as session:
                days = retention_for(session.execute(_RETENTION, scope).scalar_one_or_none(), default_days)
                if days <= 0:
                    continue
                params = {**scope, "cutoff": now - timedelta(days=days)}
                messages = session.execute(_DELETE_MESSAGES, params).rowcount or 0
                conversations = session.execute(_DELETE_CONVERSATIONS, params).rowcount or 0
                contacts = session.execute(_DELETE_CONTACTS, params).rowcount or 0
                if dry_run:
                    session.rollback()
            if messages or conversations or contacts:
                result.workspaces_purged += 1
            result.messages += messages
            result.conversations += conversations
            result.contacts += contacts
        if len(rows) < batch_size:
            return result
        after = (rows[-1].tenant_id, rows[-1].workspace_id)


def purge(
    *,
    session_factory: SessionFactory = default_session_factory,
    maintenance_session_factory: SessionFactory | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict[str, object]:
    return asdict(
        purge_whatsapp_history(
            session_factory,
            maintenance_session_factory=maintenance_session_factory,
            dry_run=dry_run,
            batch_size=batch_size,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m anum_api.whatsapp_retention",
        description="Delete WhatsApp messages older than each workspace's retention.",
    )
    parser.add_argument("--dry-run", action="store_true", help="count what would be deleted")
    parser.add_argument("--batch-size", type=int, default=500, help="workspaces discovered per query")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if settings.repository_backend != "postgresql":
        print(json.dumps({"error": "whatsapp_retention needs ANUM_REPOSITORY_BACKEND=postgresql"}), file=sys.stderr)
        return 2
    print(json.dumps(purge(dry_run=args.dry_run, batch_size=args.batch_size), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
