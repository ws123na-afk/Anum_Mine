"""Support tickets: the SLA arithmetic and the ticket rules (docs/business.md#support-tickets).

The SLA counts working minutes only: the policy names one time zone, the working days and
the working hours, and a target in working hours per severity. A ticket reported at night or
on a weekend starts counting when the next working day starts; nothing here promises 24/7.

Time spent waiting on the customer (and the time between resolving and reopening) pauses the
resolution clock: those working minutes are added to the target, so the due time moves later
by exactly the paused working time.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    OPEN_TICKET_STATUSES,
    Severity,
    SupportPolicy,
    SupportTicket,
    TicketStatus,
)

# A target never needs more than this many calendar days to land (5000 working hours at one
# working hour a week is far less). The loop stops there instead of running forever.
MAX_DAYS = 3660
DUE_SOON_MINUTES = 60


def policy_problems(policy: SupportPolicy) -> list[str]:
    """Why a policy cannot be saved (empty when it is fine)."""
    problems: list[str] = []
    try:
        ZoneInfo(policy.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        problems.append(f"Unknown time zone {policy.timezone!r}; use a name such as Asia/Riyadh or Asia/Dubai.")
    if any(day < 0 or day > 6 for day in policy.work_days) or len(set(policy.work_days)) != len(policy.work_days):
        problems.append("Working days are 0 (Monday) to 6 (Sunday), each once.")
    if policy.end_hour <= policy.start_hour:
        problems.append("Working hours must end after they start.")
    missing = [severity.value for severity in Severity if severity.value not in policy.targets]
    if missing:
        problems.append(f"Set targets for every severity (missing: {', '.join(missing)}).")
    extra = sorted(set(policy.targets) - {severity.value for severity in Severity})
    if extra:
        problems.append(f"Unknown severities: {', '.join(extra)}.")
    for name, target in policy.targets.items():
        if target.resolution_hours < target.response_hours:
            problems.append(f"The {name} resolution target must not be shorter than its response target.")
    return problems


def _zone(policy: SupportPolicy) -> ZoneInfo:
    return ZoneInfo(policy.timezone)


def _window(day: date, policy: SupportPolicy, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(policy.start_hour), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone) if policy.end_hour == 24 else datetime.combine(day, time(policy.end_hour), tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def add_working_minutes(start: datetime, minutes: int, policy: SupportPolicy) -> datetime:
    """The moment ``minutes`` working minutes after ``start`` (UTC)."""
    zone = _zone(policy)
    cursor = start.astimezone(UTC)
    remaining = max(0, int(minutes))
    day = cursor.astimezone(zone).date()
    for _ in range(MAX_DAYS):
        if day.weekday() in policy.work_days:
            open_at, close_at = _window(day, policy, zone)
            if cursor < close_at:
                begin = max(cursor, open_at)
                available = int((close_at - begin).total_seconds() // 60)
                if remaining <= available:
                    return begin + timedelta(minutes=remaining)
                remaining -= available
        day += timedelta(days=1)
        cursor = max(cursor, datetime.combine(day, time(0), tzinfo=zone).astimezone(UTC))
    raise ValueError("The support policy has no working time to count a target in.")


def working_minutes_between(start: datetime, end: datetime, policy: SupportPolicy) -> int:
    """Working minutes from ``start`` to ``end`` (0 when ``end`` is not later)."""
    if end <= start:
        return 0
    zone = _zone(policy)
    first, last = start.astimezone(UTC), end.astimezone(UTC)
    day = first.astimezone(zone).date()
    total = 0
    for _ in range(MAX_DAYS):
        open_at, close_at = _window(day, policy, zone)
        if open_at >= last:
            break
        if day.weekday() in policy.work_days:
            overlap = (min(close_at, last) - max(open_at, first)).total_seconds()
            if overlap > 0:
                total += int(overlap // 60)
        day += timedelta(days=1)
    return total


def target_minutes(policy: SupportPolicy, severity: Severity) -> tuple[int, int]:
    """(response, resolution) targets in working minutes."""
    target = policy.targets[severity.value]
    return int(Decimal(target.response_hours) * 60), int(Decimal(target.resolution_hours) * 60)


def apply_due_times(ticket: SupportTicket, policy: SupportPolicy) -> None:
    """Set the response and resolution due times from when the customer asked, the severity's
    targets and the paused working minutes."""
    response, resolution = target_minutes(policy, ticket.severity)
    ticket.response_due_at = add_working_minutes(ticket.reported_at, response, policy)
    ticket.resolution_due_at = add_working_minutes(ticket.reported_at, resolution + ticket.sla_paused_minutes, policy)


def pause(ticket: SupportTicket, now: datetime) -> None:
    if ticket.sla_paused_at is None:
        ticket.sla_paused_at = now


def resume(ticket: SupportTicket, now: datetime, policy: SupportPolicy) -> None:
    """End a pause: the paused working minutes move the resolution due time later."""
    if ticket.sla_paused_at is None:
        return
    ticket.sla_paused_minutes += working_minutes_between(ticket.sla_paused_at, now, policy)
    ticket.sla_paused_at = None
    apply_due_times(ticket, policy)


def _clock(due: datetime | None, done_at: datetime | None, now: datetime, paused: bool) -> str:
    if due is None:
        return "none"
    if done_at is not None:
        return "met" if done_at <= due else "missed"
    if paused:
        return "paused"
    if now > due:
        return "breached"
    if (due - now).total_seconds() <= DUE_SOON_MINUTES * 60:
        return "due_soon"
    return "on_track"


def sla_state(ticket: SupportTicket, now: datetime) -> dict[str, Any]:
    """Where the ticket stands against its targets right now.

    ``response``: met or missed once someone responded (or the ticket was resolved), else
    on_track, due_soon or breached. ``resolution``: the same against resolving, or paused
    while waiting on the customer. ``breached`` is true when either open clock is overdue."""
    responded = ticket.first_responded_at or ticket.resolved_at
    response = _clock(ticket.response_due_at, responded, now, False)
    resolution = _clock(ticket.resolution_due_at, ticket.resolved_at, now, ticket.sla_paused_at is not None)
    open_ticket = ticket.status in OPEN_TICKET_STATUSES
    return {
        "response": response,
        "resolution": resolution,
        "breached": open_ticket and (response == "breached" or resolution == "breached"),
    }


def move(ticket: SupportTicket, target: TicketStatus, now: datetime, actor: str, policy: SupportPolicy) -> None:
    """Apply a status change already allowed by ``TICKET_TRANSITIONS`` (the caller checks
    the resolution fields and permissions)."""
    current = ticket.status
    if current == TicketStatus.WAITING_ON_CUSTOMER:
        resume(ticket, now, policy)
    if current in {TicketStatus.RESOLVED, TicketStatus.CLOSED} and target == TicketStatus.IN_PROGRESS:
        # Reopened: the time it sat resolved does not count against the team.
        if ticket.resolved_at is not None:
            ticket.sla_paused_minutes += working_minutes_between(ticket.resolved_at, now, policy)
        ticket.reopened_count += 1
        ticket.resolved_at = ticket.resolved_by = None
        ticket.resolution_code = None
        ticket.resolution_summary = ""
        ticket.closed_at = ticket.closed_by = None
        apply_due_times(ticket, policy)
    if target == TicketStatus.WAITING_ON_CUSTOMER:
        # Asking the customer something is a response.
        ticket.first_responded_at = ticket.first_responded_at or now
        pause(ticket, now)
    if target == TicketStatus.RESOLVED:
        ticket.resolved_at = now
        ticket.resolved_by = actor
    if target == TicketStatus.CLOSED:
        ticket.closed_at = now
        ticket.closed_by = actor
    ticket.status = target
    ticket.updated_at = now
