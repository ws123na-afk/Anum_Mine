"""Mail health counters and the platform operator's view of them (docs/email.md#operator-view).

The Email Center bumps a per-day counter inside the caller's own tenant context whenever a
send succeeds, fails or ends unknown, a rate limit refuses a send or a connection check, or
the mail server refuses a saved password or cannot be reached (``mail_ops_counters``,
migration ``0064_mail_operator_view``). Counters carry the tenant, workspace, day, metric and
a number: never a person, address, subject or body.

The platform operator reads, for the organisations they run (``platform_organizations``),
how many mailboxes are connected and in which state, the oldest and newest health check,
and those counters. With PostgreSQL that read goes through ``anum_platform_reader``, which
sees only those columns of those organisations. A counter write that fails is logged and
never breaks the mail action that caused it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel

from ..schemas import TenantContext, utc_now
from ..settings import settings
from .store import AccountStatus, mail_backend

logger = logging.getLogger(__name__)

Metric = Literal["sent", "send_failed", "send_unknown", "send_rate_limited", "connect_rate_limited", "auth_failed", "server_offline"]
METRICS: tuple[Metric, ...] = (
    "sent", "send_failed", "send_unknown", "send_rate_limited", "connect_rate_limited", "auth_failed", "server_offline",
)
# A mailbox whose last health check is older than this counts as stale.
STALE_AFTER = timedelta(days=2)


class _MemoryCounters:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, date, str], int] = {}
        self.lock = RLock()

    def clear(self) -> None:
        with self.lock:
            self.items.clear()


memory_counters = _MemoryCounters()


def bump(context: TenantContext, metric: Metric) -> None:
    """Add one to today's ``metric`` for the caller's workspace. Never raises."""
    if metric not in METRICS:
        raise ValueError(metric)
    day = utc_now().date()
    try:
        if settings.repository_backend == "postgresql":
            _bump_sql(context, metric, day)
        else:
            with memory_counters.lock:
                key = (context.tenant_id, context.workspace_id, day, metric)
                memory_counters.items[key] = memory_counters.items.get(key, 0) + 1
    except Exception as exc:  # noqa: BLE001 - a counter must never break the mail action
        logger.warning("mail.ops_counter_failed", extra={"anum_metric": metric, "anum_error": type(exc).__name__})


def _bump_sql(context: TenantContext, metric: str, day: date) -> None:
    from sqlalchemy import text

    from ..db import session as db_session

    session = db_session.SessionLocal()
    try:
        db_session.set_tenant_context(session, context.tenant_id, context.workspace_id)
        session.execute(
            text(
                """
                insert into mail_ops_counters (tenant_id, workspace_id, day, metric, count)
                values (:tenant_id, :workspace_id, :day, :metric, 1)
                on conflict (tenant_id, workspace_id, day, metric)
                do update set count = mail_ops_counters.count + 1
                """
            ),
            {"tenant_id": context.tenant_id, "workspace_id": context.workspace_id, "day": day, "metric": metric},
        )
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


# ----------------------------------------------------------------------------- operator view


class MailHealthCounts(BaseModel):
    sent: int = 0
    send_failed: int = 0
    send_unknown: int = 0
    send_rate_limited: int = 0
    connect_rate_limited: int = 0
    auth_failed: int = 0
    server_offline: int = 0


class OrganisationMailHealth(BaseModel):
    """One organisation's mail health: counts and timestamps, never content or addresses."""

    tenant_id: str
    display_name: str
    mailboxes: int = 0
    connected: int = 0
    auth_failed: int = 0
    offline: int = 0
    error: int = 0
    stale: int = 0
    last_sync_at: datetime | None = None
    oldest_sync_at: datetime | None = None
    counts: MailHealthCounts
    # quiet: no mailbox; ok; attention: a mailbox is not connected, a send ended unknown or
    # failed, or a check is older than two days.
    health: str


class MailOperatorView(BaseModel):
    enabled: bool
    days: int
    since: date
    organisations: list[OrganisationMailHealth]
    totals: MailHealthCounts
    mailboxes: int


def _health(row: OrganisationMailHealth) -> str:
    if row.mailboxes == 0:
        return "quiet"
    if row.auth_failed or row.offline or row.error or row.stale or row.counts.send_unknown or row.counts.send_failed:
        return "attention"
    return "ok"


def _operator_rows_sql(operator: str, since: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from sqlalchemy import text

    from ..db import session as db_session

    session = db_session.SessionLocal()
    try:
        session.execute(text("set local role anum_platform_reader"))
        session.execute(text("select set_config('anum.user_id', :user_id, true)"), {"user_id": operator})
        orgs = session.execute(
            text(
                "select tenant_id, display_name from platform_organizations where operator_user_id = :operator "
                "order by lower(display_name), tenant_id"
            ),
            {"operator": operator},
        ).all()
        accounts = session.execute(text("select tenant_id, status, last_checked_at from mail_accounts")).all()
        counters = session.execute(
            text("select tenant_id, metric, sum(count) as count from mail_ops_counters where day >= :since group by tenant_id, metric"),
            {"since": since},
        ).all()
        return [dict(r._mapping) for r in orgs], [dict(r._mapping) for r in accounts], [dict(r._mapping) for r in counters]
    finally:
        session.rollback()
        session.close()


def _operator_rows_memory(operator: str, since: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from ..organisations import memory_branding

    orgs = sorted(
        ({"tenant_id": t, "display_name": o["display_name"]} for t, o in memory_branding.platform.items() if o["operator_user_id"] == operator),
        key=lambda r: (r["display_name"].casefold(), r["tenant_id"]),
    )
    tenants = {r["tenant_id"] for r in orgs}
    with mail_backend.lock:
        accounts = [
            {"tenant_id": a.tenant_id, "status": a.status.value, "last_checked_at": a.last_checked_at}
            for a in mail_backend.accounts.values() if a.tenant_id in tenants
        ]
    summed: dict[tuple[str, str], int] = {}
    with memory_counters.lock:
        for (tenant_id, _workspace, day, metric), count in memory_counters.items.items():
            if tenant_id in tenants and day >= since:
                summed[(tenant_id, metric)] = summed.get((tenant_id, metric), 0) + count
    counters = [{"tenant_id": t, "metric": m, "count": c} for (t, m), c in summed.items()]
    return orgs, accounts, counters


def operator_view(operator: str, days: int, enabled: bool) -> MailOperatorView:
    now = utc_now()
    since = (now - timedelta(days=days - 1)).date()
    reader = _operator_rows_sql if settings.repository_backend == "postgresql" else _operator_rows_memory
    orgs, accounts, counters = reader(operator, since)
    out: list[OrganisationMailHealth] = []
    totals = MailHealthCounts()
    for org in orgs:
        mine = [a for a in accounts if a["tenant_id"] == org["tenant_id"]]
        checks = [a["last_checked_at"] for a in mine if a["last_checked_at"] is not None]
        counts = MailHealthCounts(**{
            c["metric"]: int(c["count"] or 0) for c in counters if c["tenant_id"] == org["tenant_id"] and c["metric"] in METRICS
        })
        row = OrganisationMailHealth(
            tenant_id=org["tenant_id"], display_name=org["display_name"], mailboxes=len(mine),
            connected=sum(1 for a in mine if a["status"] == AccountStatus.CONNECTED.value),
            auth_failed=sum(1 for a in mine if a["status"] == AccountStatus.AUTH_FAILED.value),
            offline=sum(1 for a in mine if a["status"] == AccountStatus.OFFLINE.value),
            error=sum(1 for a in mine if a["status"] == AccountStatus.ERROR.value),
            stale=sum(1 for a in mine if a["last_checked_at"] is None or now - a["last_checked_at"] > STALE_AFTER),
            last_sync_at=max(checks) if checks else None, oldest_sync_at=min(checks) if checks else None,
            counts=counts, health="quiet",
        )
        row.health = _health(row)
        for metric in METRICS:
            setattr(totals, metric, getattr(totals, metric) + getattr(counts, metric))
        out.append(row)
    return MailOperatorView(
        enabled=enabled, days=days, since=since, organisations=out, totals=totals, mailboxes=sum(r.mailboxes for r in out),
    )
