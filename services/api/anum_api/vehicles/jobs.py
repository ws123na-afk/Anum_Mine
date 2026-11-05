"""The daily vehicle reminders (docs/vehicles.md#reminders).

Run by the daily retention job (``python -m anum_api.voice_retention``, reported under ``vehicles``)
and testable on its own. For each vehicle owner: one in-app notification (kind ``vehicle_reminder``,
never a push or e-mail, since a registration is personal data) when insurance, registration or
inspection expires, or a service falls due by date, at the person's reminder days, at 7 days, at
1 day and on the day; and once when a service is within 500 km by the odometer. Each step is sent
once per due date (``vehicle_reminders``); a new date starts the steps again; an archived vehicle,
a deleted record or the person's "Reminders" switch turned off stops them.

Owners are discovered as ``anum_maintenance`` (scope and date columns only); the work runs as the
application role in each owner's own tenant, workspace and user scope. The output is counts only.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings
from . import analytics as A
from . import service as S
from .models import ReminderSent
from .store import open_vehicle_store, vehicle_memory_store

logger = logging.getLogger("anum_api.vehicles.jobs")

NOTICE_KIND = "vehicle_reminder"
FIXED_STEPS = (7, 1, 0)
KM_STEP = 366  # the one reminder of a service due by the odometer
LABELS = {"insurance": "insurance", "registration": "registration", "inspection": "inspection"}

_DISCOVER = text(
    """
    select tenant_id, workspace_id, owner_user_id from vehicles
    where archived_at is null and (insurance_expires_on <= current_date + 120 or registration_expires_on <= current_date + 120
          or inspection_expires_on <= current_date + 120)
    union
    select tenant_id, workspace_id, vehicle_owner_id from vehicle_service_records
    where next_due_km is not null or next_due_on <= current_date + 120
    order by 1, 2, 3
    """
)


@dataclass
class VehicleJobResult:
    dry_run: bool = False
    people: int = 0
    reminders: int = 0
    failed: int = 0


def step_for(days_left: int, reminder_days: int) -> int | None:
    """The reminder step due today, or None (overdue items are not repeated every day)."""
    if days_left < 0:
        return None
    candidates = [step for step in sorted({reminder_days, *FIXED_STEPS}, reverse=True) if days_left <= step]
    return min(candidates) if candidates else None


def notice(item: dict[str, Any]) -> tuple[str, str]:
    name = item["registration"]
    if item["kind"] == "service":
        what = f"{name}: {item.get('service_kind', 'service').replace('_', ' ')} service"
        if item.get("km_left") is not None and (item["days_left"] is None or item["days_left"] > 7):
            return "A service is due soon", f"{what} is due in {item['km_left']} km. Book it and log it in Vehicles."
    else:
        what = f"{name}: {LABELS[item['kind']]}"
    days = item["days_left"]
    if days == 0:
        return "A vehicle date is today", f"{what} is due today ({item['due_on']}). Renew it and update the vehicle in ANUM."
    return "A vehicle date is coming up", f"{what} is due on {item['due_on']}, in {'1 day' if days == 1 else f'{days} days'}."


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
        logger.warning("vehicle_reminder_failed error_class=%s", type(exc).__name__)
        return False


def run_person(tenant_id: str, workspace_id: str, user_id: str, *, now: datetime | None = None, dry_run: bool = False) -> VehicleJobResult:
    moment = now or utc_now()
    today = moment.date()
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
    result = VehicleJobResult(dry_run=dry_run, people=1)
    due: list[tuple[dict[str, Any], int, Any]] = []
    with open_vehicle_store(context) as store:
        prefs = S.preferences(store, context)
        if not prefs.reminders:
            return result
        vehicles, _ = store.list_vehicles(context, query=None, archived=False, limit=500, offset=0)
        mine = [v for v in vehicles if v.owner_user_id == user_id]
        service = store.records_for(context, "service", [v.id for v in mine])
        for vehicle in mine:
            odometer = A.current_odometer(store.readings(context, vehicle.id))
            for item in S.due_items(vehicle, service, odometer, today=today, window_days=prefs.reminder_days):
                by_date = item["days_left"] is not None and step_for(item["days_left"], prefs.reminder_days) is not None
                step = step_for(item["days_left"], prefs.reminder_days) if by_date else (KM_STEP if item.get("km_left") is not None else None)
                if step is None:
                    continue
                due_on = date.fromisoformat(item["due_on"]) if item["due_on"] else date(1970, 1, 1)
                if step == KM_STEP:
                    due_on = date.fromordinal(int(_km_key(item["due_km"])))  # one reminder per due odometer value
                sent = ReminderSent(id=new_id("vrem"), tenant_id=tenant_id, workspace_id=workspace_id, owner_user_id=user_id, vehicle_id=vehicle.id,
                                    visibility=vehicle.ownership, vehicle_owner_id=vehicle.owner_user_id, kind=item["kind"], source_id=item["source_id"],
                                    due_on=due_on, step=step, sent_at=moment)
                if not store.reminder_sent(sent):
                    due.append((item, step, sent))
    if dry_run:
        result.reminders = len(due)
        return result
    for item, _step, sent in due:
        title, body = notice(item)
        if not _notify(context, title, body):
            result.failed += 1  # not marked: the next run tries again
            continue
        with open_vehicle_store(context) as store:
            store.add_reminder(sent)
        result.reminders += 1
    return result


def _km_key(value: str | None) -> int:
    """A due odometer value as a stable whole number for the once-only key (1 to 3,000,000)."""
    try:
        return max(1, min(int(float(value or 1)), 3_000_000))
    except ValueError:
        return 1


def discover_people() -> list[tuple[str, str, str]]:
    if settings.repository_backend == "memory":
        store = vehicle_memory_store
        people = {(v.tenant_id, v.workspace_id, v.owner_user_id) for v in store.vehicles.values() if v.archived_at is None}
        people |= {(r.tenant_id, r.workspace_id, r.vehicle_owner_id) for r in store.records["service"].values()}
        return sorted(people)
    from ..maintenance import default_session_factory, discover

    return [(row[0], row[1], row[2]) for row in discover(default_session_factory, _DISCOVER)]


def run(*, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    total = VehicleJobResult(dry_run=dry_run)
    for tenant_id, workspace_id, user_id in discover_people():
        try:
            part = run_person(tenant_id, workspace_id, user_id, now=now, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - counts only; the next run tries again
            logger.warning("vehicle_job_person_failed error_class=%s", type(exc).__name__)
            total.failed += 1
            continue
        total.people += part.people
        total.reminders += part.reminders
        total.failed += part.failed
    return asdict(total)


__all__ = ["NOTICE_KIND", "VehicleJobResult", "notice", "run", "run_person", "step_for"]
