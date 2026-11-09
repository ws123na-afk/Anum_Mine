"""Writing and delivering the weekly money note (docs/finance.md#money-advisor).

``generate`` reads the person's own records in one short owner-scoped unit of work, works the note out
with the pure ``advisor.build_note`` (no transaction open) and writes this advice week's row in another.
"Generate now" in Finance, Money advisor and the daily job both use it.

The daily job (``python -m anum_api.voice_retention``, reported under ``finance_advisor``) finds people
with an active finance account as ``anum_maintenance`` (scope columns only, migration
``0143_finance_money_advisor``), then works as the application role in each person's own tenant,
workspace and user scope. A person whose note is on and whose weekday it is (in their Finance time zone,
up to two days late if the job missed a day) gets this week's note and one in-app notification (kind
``finance_weekly_advice``, which also goes to their phones as the ``finance_advice`` push when push is on).
Never an email: owner rule. ``delivered_at`` is claimed before the notice is sent and released when the
notice fails, so a note is announced once per week and a failure is retried by the next run. Output:
counts only.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings
from .advisor import LOOKBACK_DAYS, AdvisorInputs, build_note
from .advisor_store import KEEP_WEEKS, AdvisorReport, AdvisorSettings, Trigger, open_advisor_store
from .models import Bill, Debt
from .summary import settings_for, today_in

logger = logging.getLogger("anum_api.finance.advisor")

NOTICE_KIND = "finance_weekly_advice"
CATCH_UP_DAYS = 2

_DISCOVER = text(
    "select distinct tenant_id, workspace_id, owner_user_id from finance_accounts where not archived order by 1, 2, 3"
)


@dataclass
class AdvisorJobResult:
    dry_run: bool = False
    people: int = 0
    notes: int = 0
    skipped: int = 0
    failed: int = 0


def week_start_for(today: date, weekday: int) -> date:
    """The advice week this day belongs to: it starts on the person's chosen weekday (Monday is 0)."""
    return today - timedelta(days=(today.weekday() - weekday) % 7)


def gather(context: TenantContext, *, now: datetime | None = None) -> AdvisorInputs:
    """The note's inputs, read in one short owner-scoped unit of work."""
    from . import obligations
    from .sms_store import open_sms_stores

    links = obligations.load(context)  # its own short unit of work first
    with open_sms_stores(context) as (finance, sms):
        finance_settings = settings_for(finance, context)
        today = today_in(finance_settings, now)
        sms_settings = sms.get_settings(context)
        limits = {m.account_id: m.credit_limit for m in (sms_settings.account_map if sms_settings else []) if m.credit_limit and m.credit_limit > 0}
        accounts = finance.list_accounts(context)
        sums = finance.account_sums(context)
        return AdvisorInputs(
            today=today, timezone=finance_settings.timezone, base_currency=finance_settings.base_currency,
            accounts=accounts, balances=sums,
            transactions=finance.transactions_between(context, today - timedelta(days=LOOKBACK_DAYS), today),
            bills=[b for b in finance.list_items(context, "bill") if isinstance(b, Bill)],
            debts=obligations.current_debts([d for d in finance.list_items(context, "debt") if isinstance(d, Debt)], accounts, sums, links),
            bill_links=links.bill_links,
            lending_ids=links.lending_ids,
            card_limits={k: Decimal(v) for k, v in limits.items() if v is not None},
            observations=sms.latest_observations(context),
        )


def advisor_settings(context: TenantContext) -> AdvisorSettings:
    with open_advisor_store(context) as store:
        return store.get_settings(context) or AdvisorSettings()


def generate(context: TenantContext, *, trigger: Trigger, now: datetime | None = None, weekday: int | None = None) -> AdvisorReport:
    """Work out this week's note and write it (replacing this week's earlier note, keeping its delivery)."""
    moment = now or utc_now()
    inputs = gather(context, now=moment)
    note = build_note(inputs)
    chosen = weekday if weekday is not None else advisor_settings(context).weekday
    report = AdvisorReport(id=new_id("fin_adv"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
                           week_start=week_start_for(inputs.today, chosen), trigger=trigger, generated_at=moment, note=note)
    with open_advisor_store(context) as store:
        saved = store.save_report(report)
        store.prune(context, inputs.today - timedelta(days=KEEP_WEEKS * 7))
    return saved


def _may_read_finance(context: TenantContext) -> bool:
    from ..authorization import ROLE_PERMISSIONS, Permission
    from ..team_work import open_team_unit

    with open_team_unit(context) as unit:
        role = next((m.role for m in unit.repository.list_memberships(context) if m.active and m.user_id == context.user_id), None)
    return role is not None and Permission.FINANCE_READ in ROLE_PERMISSIONS.get(role, frozenset())


def notify(context: TenantContext, title: str, body: str) -> bool:
    """One in-app notification (and its push event). Never an email: ``email=None``."""
    from ..events import CanonicalEventName, create_event
    from ..notifications import create_notification
    from ..team_work import open_team_unit

    try:
        with open_team_unit(context) as unit:
            item = create_notification(unit.store, context, user_id=context.user_id, kind=NOTICE_KIND, title=title, body=body,
                                       work_item_id=None, email=None)
            unit.repository.record_event(create_event(
                CanonicalEventName.NOTIFICATION_CREATED, context, item.id, {"recipient": context.user_id, "kind": NOTICE_KIND, "work_item_id": None},
            ).event)
        return True
    except Exception as exc:  # noqa: BLE001 - one person's failure never stops the run
        logger.warning("finance_advice_notice_failed error_class=%s", type(exc).__name__)
        return False


def run_person(tenant_id: str, workspace_id: str, user_id: str, *, now: datetime | None = None, dry_run: bool = False) -> AdvisorJobResult:
    from .advisor_text import notice_text
    from .sms_store import open_sms_stores

    moment = now or utc_now()
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
    result = AdvisorJobResult(dry_run=dry_run, people=1)
    chosen = advisor_settings(context)
    if not chosen.enabled:
        result.skipped += 1
        return result
    with open_sms_stores(context) as (finance, _sms):
        today = today_in(settings_for(finance, context), moment)
    week_start = week_start_for(today, chosen.weekday)
    if (today - week_start).days > CATCH_UP_DAYS:
        return result
    with open_advisor_store(context) as store:
        existing = store.for_week(context, week_start)
    if existing is not None and existing.delivered_at is not None:
        return result
    if not _may_read_finance(context):
        result.skipped += 1
        return result
    if dry_run:
        result.notes += 1
        return result
    report = generate(context, trigger="schedule", now=moment, weekday=chosen.weekday)
    with open_advisor_store(context) as store:
        claimed = store.claim_delivery(context, report.id, moment)
    if not claimed:
        return result  # another run announced it first
    title, body = notice_text(report.note)
    if not notify(context, title, body):
        with open_advisor_store(context) as store:
            store.release_delivery(context, report.id)  # the next run tries again
        result.failed += 1
        return result
    result.notes += 1
    return result


def discover_people() -> list[tuple[str, str, str]]:
    if settings.repository_backend == "memory":
        from .store import finance_store

        return sorted({(a.tenant_id, a.workspace_id, a.owner_user_id) for a in finance_store.accounts.values() if not a.archived})
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1], row[2]) for row in discover(default_session_factory, _DISCOVER)]


def run(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    total = AdvisorJobResult(dry_run=dry_run)
    for tenant_id, workspace_id, user_id in discover_people():
        try:
            part = run_person(tenant_id, workspace_id, user_id, now=now, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - counts only; the next run tries again
            logger.warning("finance_advice_person_failed error_class=%s", type(exc).__name__)
            total.failed += 1
            continue
        total.people += part.people
        total.notes += part.notes
        total.skipped += part.skipped
        total.failed += part.failed
    return asdict(total)


__all__ = ["AdvisorJobResult", "CATCH_UP_DAYS", "NOTICE_KIND", "discover_people", "gather", "generate", "notify", "run", "run_person", "week_start_for"]
