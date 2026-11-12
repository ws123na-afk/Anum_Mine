"""Reminders for money lent and borrowed with people (docs/finance.md#lent-and-borrowed).

Part of the daily job (``python -m anum_api.voice_retention``, reported under ``finance_loan_reminders``, counts
only). People are found the way the weekly money note finds them (``advisor_jobs.discover_people``: an active
finance account) and also by an open loan with a due date and reminders on, so someone who records loans but never
added an account is reminded too (``discover_loan_people``, migration ``0179_loan_reminder_discovery``). Both read
as ``anum_maintenance`` from scope and reminder columns only, never BYPASSRLS; each person is then
handled as the application role in their own scope. A loan with a due date and reminders on gets one in-app
notification ``remind_days_before`` days before the due date and one more once it is overdue (kind
``finance_loan_due``, in-app only: never an email and never a phone push, ``push.IN_APP_ONLY_KINDS``). The
reminder is claimed in a short transaction before the notice is sent (compare and set on the due date it is
for) and released when the notice fails, so each is sent once and a failure is retried by the next run. A
viewer (no ``finance:read``) gets none. The notice carries the person's name and the amount left only for the
owner's own inbox, never in logs or audit records.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from ..schemas import TenantContext, utc_now
from . import personal as P

logger = logging.getLogger("anum_api.finance.loans")

NOTICE_KIND = "finance_loan_due"

# Scope columns of open loans with reminders on, never a name, amount or note (migration 0179_loan_reminder_discovery).
_DISCOVER_LOANS = text(
    "select distinct tenant_id, workspace_id, owner_user_id from finance_person_loans "
    "where remind and due_on is not null and closed_on is null order by 1, 2, 3"
)


@dataclass
class LoanReminderResult:
    dry_run: bool = False
    people: int = 0
    reminders: int = 0
    skipped: int = 0
    failed: int = 0


def notice_text(loan: P.PersonLoan, figures: P.LoanFigures, which: str) -> tuple[str, str]:
    """The reminder names the next repayment: with a schedule what is left of it (overdue ones included), else what is left."""
    scheduled = bool(loan.installments)
    owed = (figures.overdue_amount or figures.next_due_amount) if scheduled else figures.outstanding
    amount = f"{loan.currency} {owed:,}"
    due_on = figures.next_due_on or loan.due_on
    when = due_on.isoformat() if due_on else "-"
    if loan.direction == P.LoanDirection.LENT:
        title = "Money owed to you is due" if which == "due" else "Money owed to you is overdue"
        body = f"{loan.person} owes you {amount}, due {when}. ANUM only reminds you: it never moves money."
    else:
        title = "A loan you owe is due" if which == "due" else "A loan you owe is overdue"
        body = f"You owe {loan.person} {amount}, due {when}. ANUM only reminds you: it never moves money."
    return title, body


def notify(context: TenantContext, title: str, body: str, *, kind: str = NOTICE_KIND) -> bool:
    """One in-app notification. Never an email (``email=None``) and never a phone push (in-app only kind)."""
    from ..events import CanonicalEventName, create_event
    from ..notifications import create_notification
    from ..team_work import open_team_unit

    try:
        with open_team_unit(context) as unit:
            item = create_notification(unit.store, context, user_id=context.user_id, kind=kind, title=title, body=body,
                                       work_item_id=None, email=None)
            unit.repository.record_event(create_event(
                CanonicalEventName.NOTIFICATION_CREATED, context, item.id, {"recipient": context.user_id, "kind": kind, "work_item_id": None},
            ).event)
        return True
    except Exception as exc:  # noqa: BLE001 - one person's failure never stops the run
        logger.warning("finance_loan_notice_failed error_class=%s", type(exc).__name__)
        return False


def run_person(tenant_id: str, workspace_id: str, user_id: str, *, now: datetime | None = None, dry_run: bool = False) -> LoanReminderResult:
    from .advisor_jobs import _may_read_finance
    from .links_store import LinkStatus, open_link_stores
    from .personal_store import open_personal_store
    from .summary import settings_for, today_in

    moment = now or utc_now()
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
    result = LoanReminderResult(dry_run=dry_run, people=1)
    with open_personal_store(context) as store:
        loans = [l for l in store.list(context, "person_loan") if l.remind and l.due_on is not None and l.closed_on is None]
    if not loans:
        return result
    with open_link_stores(context) as (finance, _sms, links):
        today = today_in(settings_for(finance, context), moment)
        rows, _ = links.list_links(context, canonical_id=None, dimension=P.PERSON_LOAN_DIMENSION, target_type=None, target_id=None,
                                   status=LinkStatus.ACTIVE.value, limit=10_000, offset=0)
    due: list[tuple[P.PersonLoan, P.LoanFigures, str]] = []
    for loan in loans:
        figures = P.loan_figures(loan, rows, today)
        which = P.reminder_due(loan, figures, today)
        if which is not None:
            due.append((loan, figures, which))
    if not due:
        return result
    if not _may_read_finance(context):
        result.skipped += len(due)
        return result
    for loan, figures, which in due:
        if dry_run:
            result.reminders += 1
            continue
        overdue = which == "overdue"
        with open_personal_store(context) as store:
            claimed = store.claim_reminder(context, loan.id, figures.next_due_on, overdue)  # type: ignore[arg-type]
        if not claimed:
            continue  # another run sent it first
        title, body = notice_text(loan, figures, which)
        if not notify(context, title, body):
            with open_personal_store(context) as store:
                store.release_reminder(context, loan.id, overdue)
            result.failed += 1
            continue
        result.reminders += 1
    return result


def discover_loan_people() -> list[tuple[str, str, str]]:
    """Everyone with an open loan that has a due date and reminders on, whether or not they have a finance account."""
    from ..settings import settings

    if settings.repository_backend == "memory":
        from .personal_store import personal_memory_store

        loans = [l for l in personal_memory_store.rows["person_loan"].values() if isinstance(l, P.PersonLoan)]
        return sorted({(l.tenant_id, l.workspace_id, l.owner_user_id) for l in loans if l.remind and l.due_on is not None and l.closed_on is None})
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1], row[2]) for row in discover(default_session_factory, _DISCOVER_LOANS)]


def people_to_remind() -> list[tuple[str, str, str]]:
    """Finance account holders and loan owners, each once."""
    from .advisor_jobs import discover_people

    return sorted(set(discover_people()) | set(discover_loan_people()))


def run(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    total = LoanReminderResult(dry_run=dry_run)
    for tenant_id, workspace_id, user_id in people_to_remind():
        try:
            part = run_person(tenant_id, workspace_id, user_id, now=now, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - counts only; the next run tries again
            logger.warning("finance_loan_person_failed error_class=%s", type(exc).__name__)
            total.failed += 1
            continue
        total.people += part.people
        total.reminders += part.reminders
        total.skipped += part.skipped
        total.failed += part.failed
    return asdict(total)


__all__ = ["LoanReminderResult", "NOTICE_KIND", "discover_loan_people", "notice_text", "notify", "people_to_remind", "run", "run_person"]
