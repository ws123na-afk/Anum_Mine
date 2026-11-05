"""The daily purchase reminders (docs/purchases.md#reminders).

Run by the daily retention job (``python -m anum_api.voice_retention``, reported under ``purchases``)
and testable on its own. For each person with open purchases: one in-app notification (kind
``purchase_reminder``, never a push or e-mail, since orders and addresses are personal data) when a
delivery is expected or late, a return window or return deadline is closing, a pickup is due, a
refund is overdue, a warranty is expiring, or a confirmation is missing. Each step is sent once
per due date (``purchase_reminders``); a new date starts the steps again; a refunded, cancelled or
archived purchase, a closed return or the person's "Reminders" switch turned off stops them.

People are discovered as ``anum_maintenance`` (scope and status columns only); the work runs as the
application role in each person's own tenant, workspace and user scope. The output is counts only.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings
from . import service as S
from .models import CLOSED, REMINDER_KINDS, ReminderSent
from .store import open_purchase_store, purchase_memory_store

logger = logging.getLogger("anum_api.purchases.jobs")

NOTICE_KIND = "purchase_reminder"
FIXED_STEPS = (1, 0)
# Kinds sent once per due date (an overdue or missing item); the others step down to the day.
ONCE = frozenset({"delivery_late", "delivery_expected", "pickup_due", "refund_overdue", "missing_confirmation"})

_DISCOVER = text(
    """
    select distinct tenant_id, workspace_id, owner_user_id from purchases
    where archived_at is null and status not in ('refunded', 'cancelled')
    order by 1, 2, 3
    """
)


@dataclass
class PurchaseJobResult:
    dry_run: bool = False
    people: int = 0
    reminders: int = 0
    failed: int = 0


def step_for(kind: str, days_left: int, lead_days: int, warranty_days: int) -> int | None:
    """The reminder step due today, or None. Overdue kinds are sent once; date windows step down to 1 day and the day."""
    if kind in ONCE:
        return 0
    if days_left < 0:
        return None
    first = warranty_days if kind == "warranty" else lead_days
    candidates = [step for step in sorted({first, 7 if kind == "warranty" else first, *FIXED_STEPS}, reverse=True) if days_left <= step]
    return min(candidates) if candidates else None


TITLES = {
    "delivery_expected": "A delivery is due", "delivery_late": "A delivery is late", "return_window": "A return window is closing",
    "pickup_due": "A return pickup is due", "refund_overdue": "A refund is overdue", "warranty": "A warranty is ending",
    "missing_confirmation": "A confirmation is missing",
}


def notice(item: dict[str, Any]) -> tuple[str, str]:
    name = item["merchant"] + (f" {item['order_ref']}" if item.get("order_ref") else "")
    days = item["days_left"]
    when = "today" if days == 0 else ("tomorrow" if days == 1 else (f"in {days} days" if days > 1 else f"{-days} days ago"))
    bodies = {
        "delivery_expected": f"{name} is expected {when} ({item['due_on']}).",
        "delivery_late": f"{name} was expected on {item['due_on']} and has not arrived. Check the tracking or contact the shop.",
        "return_window": f"The return window for {name} closes {when} ({item['due_on']}).",
        "pickup_due": f"The courier pickup for the return of {name} is {when} ({item['due_on']}).",
        "refund_overdue": f"The refund for the return of {name} was due on {item['due_on']}. Contact the shop, or link the refund in ANUM if it arrived.",
        "warranty": f"The warranty for {name} ends {when} ({item['due_on']}).",
        "missing_confirmation": f"{name} has no confirmation yet. Add the shop's confirmation or update its status in ANUM.",
    }
    return TITLES[item["kind"]], bodies[item["kind"]]


def _notify(context: TenantContext, title: str, body: str) -> bool:
    from ..events import CanonicalEventName, create_event
    from ..notifications import create_notification
    from ..team_work import open_team_unit

    try:
        with open_team_unit(context) as unit:
            members = {m.user_id for m in unit.repository.list_memberships(context) if m.active}
            if context.user_id not in members:
                return False
            item = create_notification(unit.store, context, user_id=context.user_id, kind=NOTICE_KIND, title=title, body=body,
                                       work_item_id=None, in_app_only=True)
            unit.repository.record_event(create_event(
                CanonicalEventName.NOTIFICATION_CREATED, context, item.id, {"recipient": context.user_id, "kind": NOTICE_KIND, "work_item_id": None},
            ).event)
        return True
    except Exception as exc:  # noqa: BLE001 - one person's failure never stops the run
        logger.warning("purchase_reminder_failed error_class=%s", type(exc).__name__)
        return False


def run_person(tenant_id: str, workspace_id: str, user_id: str, *, now: datetime | None = None, dry_run: bool = False) -> PurchaseJobResult:
    moment = now or utc_now()
    today = moment.date()
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
    result = PurchaseJobResult(dry_run=dry_run, people=1)
    due: list[tuple[dict[str, Any], ReminderSent]] = []
    with open_purchase_store(context) as store:
        prefs = S.preferences(store, context)
        if not prefs.reminders:
            return result
        mine = [p for p in store.all_purchases(context) if p.owner_user_id == user_id and p.status not in CLOSED]
        ids = [p.id for p in mine]
        returns = store.returns(context, ids)
        items = store.items(context, ids)
        for purchase in mine:
            for item in S.due_items(purchase, [r for r in returns if r.purchase_id == purchase.id], [i for i in items if i.purchase_id == purchase.id],
                                    today=today, lead_days=prefs.lead_days, warranty_days=prefs.warranty_days):
                if item["kind"] not in REMINDER_KINDS:
                    continue  # shown on the page's task queue only (proof_needed), never a notification
                step = step_for(item["kind"], item["days_left"], prefs.lead_days, prefs.warranty_days)
                if step is None:
                    continue
                sent = ReminderSent(id=new_id("purm"), tenant_id=tenant_id, workspace_id=workspace_id, owner_user_id=user_id, purchase_id=purchase.id,
                                    visibility=purchase.ownership, purchase_owner_id=purchase.owner_user_id, kind=item["kind"], source_id=item["source_id"],
                                    due_on=date.fromisoformat(item["due_on"]), step=step, sent_at=moment)
                if not store.reminder_sent(sent):
                    due.append((item, sent))
    if dry_run:
        result.reminders = len(due)
        return result
    for item, sent in due:
        title, body = notice(item)
        if not _notify(context, title, body):
            result.failed += 1  # not marked: the next run tries again
            continue
        with open_purchase_store(context) as store:
            store.add_reminder(sent)
        result.reminders += 1
    return result


def discover_people() -> list[tuple[str, str, str]]:
    if settings.repository_backend == "memory":
        store = purchase_memory_store
        return sorted({(p.tenant_id, p.workspace_id, p.owner_user_id) for p in store.purchases.values()
                       if p.archived_at is None and p.status not in CLOSED})
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1], row[2]) for row in discover(default_session_factory, _DISCOVER)]


def run(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    total = PurchaseJobResult(dry_run=dry_run)
    for tenant_id, workspace_id, user_id in discover_people():
        try:
            part = run_person(tenant_id, workspace_id, user_id, now=now, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - counts only; the next run tries again
            logger.warning("purchase_job_person_failed error_class=%s", type(exc).__name__)
            total.failed += 1
            continue
        total.people += part.people
        total.reminders += part.reminders
        total.failed += part.failed
    return asdict(total)


__all__ = ["NOTICE_KIND", "PurchaseJobResult", "notice", "run", "run_person", "step_for"]
