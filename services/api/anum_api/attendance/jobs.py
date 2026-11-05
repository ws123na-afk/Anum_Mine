"""The daily attendance job (docs/attendance.md#daily-job): reduce old events, send missed check alerts.

Run by the daily retention job (``python -m anum_api.voice_retention``, reported under
``attendance``) or alone with ``python -m anum_api.attendance.jobs [--dry-run]``.

Migration 0011's pattern, never BYPASSRLS:

1. **Discover** as ``anum_maintenance``: the tenant, workspace and person (nothing else) of
   events older than 90 days and of profiles whose missed check alerts are on and not yet sent
   today.
2. **Act** per person as the application role inside that person's own tenant, workspace and
   user context, where the ordinary owner policies apply.

Alerts: for the person's previous local day, a missing checkout or a scheduled day with no
arrival produces one in-app notification (never email or push: ``push.IN_APP_ONLY_KINDS``), at
most once a day per person (a compare and set on ``last_alert_day``). The alert names the day
and what is missing, never a place.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from ..schemas import TenantContext, utc_now
from . import service as S
from .store import AttendanceStore

ALERT_KIND = "attendance_missed_check"

_DISCOVER = text(
    """
    select tenant_id, workspace_id, owner_user_id from (
      select tenant_id, workspace_id, owner_user_id from attendance_events
      union
      select tenant_id, workspace_id, owner_user_id from attendance_profiles
    ) found
    where (tenant_id, workspace_id, owner_user_id) > (:after_tenant, :after_workspace, :after_owner)
    order by tenant_id, workspace_id, owner_user_id
    limit :limit
    """
)
_ACTIVE = text(
    "select 1 from workspace_memberships where tenant_id = :t and workspace_id = :w and user_id = :u and active"
)


def alert_text(day: date, codes: list[str]) -> tuple[str, str]:
    parts = []
    if "missing_checkout" in codes:
        parts.append("a departure (check out) is missing")
    if "no_arrival" in codes:
        parts.append("no arrival was recorded on a scheduled day")
    body = (f"On {day.isoformat()} " + " and ".join(parts) + ". Open Attendance to add a correction with a reason, "
            "or leave it if you did not work.")
    return f"Attendance: check {day.isoformat()}", body


def due_alert(store: AttendanceStore, context: TenantContext, now: datetime) -> tuple[date, list[str]] | None:
    """The previous local day and its missing checks, when an alert is due (and claims it)."""
    profile = store.get_profile(context, context.user_id)
    if profile is None or not profile.missed_check_alerts:
        return None
    yesterday = S.today_for(profile, now) - timedelta(days=1)
    if profile.last_alert_day is not None and profile.last_alert_day >= yesterday:
        return None
    days = S.compute_days(store, context, context.user_id, yesterday, yesterday, now, profile=profile)
    codes = sorted({code for d in days for code in d.flags if code in ("missing_checkout", "no_arrival")})
    # Claim the day whether or not something is missing, so each day is looked at once.
    if not store.save_profile(profile.model_copy(update={"last_alert_day": yesterday}), profile.version):
        return None
    return (yesterday, codes) if codes else None


@dataclass
class JobResult:
    dry_run: bool = False
    people: int = 0
    days_reduced: int = 0
    events_deleted: int = 0
    corrections_deleted: int = 0
    alerts: int = 0
    stopped: bool = False


def run_person(store: AttendanceStore, context: TenantContext, now: datetime, notify: Callable[[str, str], None],
               result: JobResult) -> None:
    reduced = S.reduce_old_events(store, context, now)
    result.days_reduced += reduced["days"]
    result.events_deleted += reduced["events"]
    result.corrections_deleted += reduced["corrections"]
    alert = due_alert(store, context, now)
    if alert is not None:
        title, body = alert_text(*alert)
        notify(title, body)
        result.alerts += 1


def run_memory(now: datetime | None = None) -> JobResult:
    """The in-memory backend (local and tests): every person with attendance records."""
    from ..notifications import create_notification
    from ..team_store import memory_team_store as team_store
    from .store import attendance_store

    now = now or utc_now()
    result = JobResult()
    people = {(p.tenant_id, p.workspace_id, p.owner_user_id) for p in attendance_store.profiles.values()}
    people |= {(e.tenant_id, e.workspace_id, e.owner_user_id) for e in attendance_store.events.values()}
    for tenant_id, workspace_id, user_id in sorted(people):
        context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
        result.people += 1

        def notify(title: str, body: str, ctx: TenantContext = context) -> None:
            create_notification(team_store, ctx, user_id=ctx.user_id, kind=ALERT_KIND, title=title, body=body, in_app_only=True)

        run_person(attendance_store, context, now, notify, result)
    return result


def run_sql(*, now: datetime | None = None, dry_run: bool = False, batch_size: int = 500,
            session_factory: Any = None, maintenance_session_factory: Any = None,
            should_stop: Callable[[], bool] = lambda: False) -> JobResult:
    from ..db.attendance_repository import SqlAttendanceStore
    from ..db.repository import SqlAlchemyRepository
    from ..db.team_repository import SqlAlchemyTeamStore
    from ..events import CanonicalEventName, create_event
    from ..maintenance import default_session_factory, discover, scoped_unit
    from ..notifications import create_notification

    now = now or utc_now()
    session_factory = session_factory or default_session_factory
    result = JobResult(dry_run=dry_run)
    after = ("", "", "")
    while True:
        rows = discover(maintenance_session_factory or session_factory, _DISCOVER,
                        {"after_tenant": after[0], "after_workspace": after[1], "after_owner": after[2], "limit": batch_size})
        for row in rows:
            if should_stop():
                result.stopped = True
                return result
            result.people += 1
            context = TenantContext(tenant_id=row.tenant_id, workspace_id=row.workspace_id, user_id=row.owner_user_id)
            with scoped_unit(session_factory, row.tenant_id, row.workspace_id, user_id=row.owner_user_id) as session:
                member = session.execute(_ACTIVE, {"t": row.tenant_id, "w": row.workspace_id, "u": row.owner_user_id}).first()

                def notify(title: str, body: str, session: Any = session, ctx: TenantContext = context, active: bool = member is not None) -> None:
                    if not active:
                        return
                    notification = create_notification(SqlAlchemyTeamStore(session), ctx, user_id=ctx.user_id, kind=ALERT_KIND,
                                                       title=title, body=body, in_app_only=True)
                    SqlAlchemyRepository(session).record_event(create_event(
                        CanonicalEventName.NOTIFICATION_CREATED, ctx, notification.id,
                        {"recipient": ctx.user_id, "kind": ALERT_KIND, "work_item_id": None}).event)

                run_person(SqlAttendanceStore(session, context), context, now, notify, result)
                if dry_run:
                    session.rollback()
        if len(rows) < batch_size:
            return result
        last = rows[-1]
        after = (last.tenant_id, last.workspace_id, last.owner_user_id)


def run(*, dry_run: bool = False, batch_size: int = 500) -> dict[str, object]:
    from ..record_retention import finish_on_sigterm
    from ..settings import settings

    if settings.repository_backend != "postgresql":
        return asdict(run_memory())
    with finish_on_sigterm() as stop:
        return asdict(run_sql(dry_run=dry_run, batch_size=batch_size, should_stop=stop.is_set))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m anum_api.attendance.jobs",
                                     description="Reduce attendance events older than 90 days and send missed check alerts.")
    parser.add_argument("--dry-run", action="store_true", help="count what would change")
    parser.add_argument("--batch-size", type=int, default=500, help="people discovered per query")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    print(json.dumps(run(dry_run=args.dry_run, batch_size=args.batch_size), indent=2), file=sys.stdout)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
