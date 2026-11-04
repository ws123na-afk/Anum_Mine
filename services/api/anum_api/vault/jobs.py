"""The vault's daily work (docs/documents.md#expiry-reminders-trash-and-retention).

Run by the daily retention job (``python -m anum_api.voice_retention``, reported under
``documents``) and testable on its own:

* **Expiry reminders.** For each active document with an expiry date, one in-app notification to
  its owner at the document's reminder days (else the workspace's, 30 by default), at 7 days, at
  1 day and on the expiry day, each once per expiry date (``reminders_sent``). Changing the expiry
  date starts them afresh; deleting the document stops them.
* **Retention.** A document whose ``retention_until`` date has passed moves to the trash.
* **Trash.** A document in the trash past its ``purge_after`` is deleted for good: its rows, its
  file rows and its bytes in object storage.

Workspaces are discovered as ``anum_maintenance`` (scope and date columns only); the work runs as
the application role inside each workspace's RLS context with ``anum.vault_job`` set, so private
documents are included. The output is counts only.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from ..schemas import TenantContext, utc_now
from ..settings import settings
from .models import DocumentRecord
from .store import memory_vault_store, open_vault_unit

logger = logging.getLogger("anum_api.vault.jobs")

JOB_USER = "system:vault"
NOTICE_KIND = "document_expiry"
FIXED_STEPS = (7, 1, 0)

_DISCOVER = text(
    """
    select distinct tenant_id, workspace_id from vault_documents
    where (status = 'active' and expiry_date is not null and expiry_date <= current_date + 366)
       or (status = 'active' and retention_until is not null and retention_until <= current_date)
       or (status = 'deleted' and purge_after <= now())
    order by tenant_id, workspace_id
    """
)


@dataclass
class VaultJobResult:
    dry_run: bool = False
    workspaces: int = 0
    reminders: int = 0
    retention_trashed: int = 0
    purged: int = 0
    failed: int = 0


def steps(reminder_days: int) -> list[int]:
    return sorted({reminder_days, *FIXED_STEPS}, reverse=True)


def due_step(record: DocumentRecord, default_days: int, today: date) -> int | None:
    """The reminder step due today for this document, or None (each step once per expiry date)."""
    if record.expiry_date is None:
        return None
    left = (record.expiry_date - today).days
    if left < 0:
        return None
    candidates = [step for step in steps(record.reminder_days or default_days) if left <= step]
    if not candidates:
        return None
    step = min(candidates)
    key = f"{record.expiry_date.isoformat()}:{step}"
    return None if key in record.reminders_sent else step


def notice(record: DocumentRecord, today: date) -> tuple[str, str]:
    left = (record.expiry_date - today).days  # type: ignore[operator]
    when = record.expiry_date.isoformat()  # type: ignore[union-attr]
    if left == 0:
        return "A document expires today", f"{record.title} expires today ({when}). Renew it and add the new version in the Document vault."
    days = "1 day" if left == 1 else f"{left} days"
    return "A document expires soon", f"{record.title} expires on {when}, in {days}. Renew it and add the new version in the Document vault."


def _notify(context: TenantContext, user_id: str, title: str, body: str) -> bool:
    from ..events import CanonicalEventName, create_event
    from ..notifications import create_notification
    from ..team_work import open_team_unit

    recipient = context.model_copy(update={"user_id": user_id})
    try:
        with open_team_unit(recipient) as unit:
            members = {m.user_id for m in unit.repository.list_memberships(recipient) if m.active}
            if user_id not in members:
                return False
            item = create_notification(unit.store, recipient, user_id=user_id, kind=NOTICE_KIND, title=title, body=body,
                                       work_item_id=None, in_app_only=True)
            unit.repository.record_event(create_event(
                CanonicalEventName.NOTIFICATION_CREATED, recipient, item.id, {"recipient": user_id, "kind": NOTICE_KIND, "work_item_id": None},
            ).event)
        return True
    except Exception as exc:  # noqa: BLE001 - one workspace's failure never stops the run
        logger.warning("vault_reminder_failed error_class=%s", type(exc).__name__)
        return False


def run_workspace(tenant_id: str, workspace_id: str, *, now: datetime | None = None, dry_run: bool = False) -> VaultJobResult:
    from .service import delete_objects, purge_document

    moment = now or utc_now()
    today = moment.date()
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=JOB_USER)
    result = VaultJobResult(dry_run=dry_run, workspaces=1)
    with open_vault_unit(context, job=True) as unit:
        vault_settings = unit.store.get_settings(context)
        expiring = unit.store.expiring(context, date.fromordinal(today.toordinal() + 366))
        retention = unit.store.retention_due(context, today)
        trash = unit.store.trash_due(context, moment)
    due = [(record, step) for record in expiring if (step := due_step(record, vault_settings.reminder_days, today)) is not None]
    if dry_run:
        result.reminders, result.retention_trashed, result.purged = len(due), len(retention), len(trash)
        return result
    for record, step in due:
        title, body = notice(record, today)
        if not _notify(context, record.owner_user_id, title, body):
            result.failed += 1
            continue
        with open_vault_unit(context, job=True) as unit:
            current = unit.store.get_document(context, record.id)
            if current is None or current.expiry_date != record.expiry_date:
                continue
            key = f"{record.expiry_date.isoformat()}:{step}"  # type: ignore[union-attr]
            # Steps above this one are covered too: a reminder that came late never sends the earlier ones afterwards.
            keys = {key, *(f"{record.expiry_date.isoformat()}:{s}" for s in steps(record.reminder_days or vault_settings.reminder_days) if s > step)}  # type: ignore[union-attr]
            unit.store.save_document(context, current.model_copy(update={"reminders_sent": sorted(set(current.reminders_sent) | keys)}), current.row_version)
            from .service import log_event

            log_event(unit, context, record.id, "reminder_sent", {"days_before": step})
        result.reminders += 1
    from datetime import timedelta

    for record in retention:
        with open_vault_unit(context, job=True) as unit:
            current = unit.store.get_document(context, record.id)
            if current is None or current.status != "active":
                continue
            unit.store.save_document(context, current.model_copy(update={
                "status": "deleted", "deleted_at": moment, "deleted_by": "system:vault-retention",
                "purge_after": moment + timedelta(days=vault_settings.trash_days), "assistant_may_read": False,
                "updated_by": JOB_USER, "updated_at": moment}), current.row_version)
            if current.assistant_may_read:
                unit.store.set_file_vault(context, current.file_id, "hidden")
            from .service import log_event

            log_event(unit, context, record.id, "retention_trash")
        result.retention_trashed += 1
    for record in trash:
        with open_vault_unit(context, job=True) as unit:
            current = unit.store.get_document(context, record.id)
            if current is None or current.status != "deleted" or current.purge_after is None or current.purge_after > moment:
                continue
            keys = purge_document(unit, context, current)
        delete_objects(context, current.file_id, keys)
        result.purged += 1
    return result


def discover_workspaces() -> list[tuple[str, str]]:
    if settings.repository_backend == "memory":
        return memory_vault_store.workspaces_with_work()
    from ..maintenance import default_session_factory, discover

    return [(row.tenant_id, row.workspace_id) for row in discover(default_session_factory, _DISCOVER)]


def run(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    total = VaultJobResult(dry_run=dry_run)
    for tenant_id, workspace_id in discover_workspaces():
        try:
            part = run_workspace(tenant_id, workspace_id, now=now, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - counts only; the next run tries again
            logger.warning("vault_job_workspace_failed error_class=%s", type(exc).__name__)
            total.failed += 1
            continue
        total.workspaces += 1
        total.reminders += part.reminders
        total.retention_trashed += part.retention_trashed
        total.purged += part.purged
        total.failed += part.failed
    return asdict(total)


__all__ = ["NOTICE_KIND", "VaultJobResult", "due_step", "notice", "run", "run_workspace", "steps"]
