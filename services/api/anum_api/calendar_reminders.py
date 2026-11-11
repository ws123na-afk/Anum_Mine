"""Calendar reminders (docs/calendar.md#reminders).

An event carries ``reminders``: minutes before its start (0 is at the start). When one falls
due, the event's creator and every workspace member whose team profile email is on the
attendee list get an in-app notification (kind ``calendar_reminder``) and, where the phone
app is set up, a push (``push.PushKind.CALENDAR_REMINDER``). A reminder whose minutes are also
in ``email_reminders`` is emailed to the same people too (``send_reminder_emails``), through the
workspace's mail server (``notifications.send_with_retries``), only to a person whose email
notifications are on, who has an address ANUM knows, and whom the Contacts directory does not
mark do not contact by email; once per event, occurrence, reminder and person
(``calendar_reminder_email_sends``), in English and Arabic. Each in-app reminder is
sent once per event, occurrence, reminder and person: ``calendar_reminder_sends`` records it,
claimed with ``insert ... on conflict do nothing``, so a second pass, a restart or two API
processes never send it twice. A reminder whose time passed while the job was not running is
still sent as long as the occurrence has not started; one for an occurrence already under way
is not. Skipped occurrences, cancelled events and cancelled occurrences get none.

The pass runs with the team reminder sweep (``team_work.run_reminder_pass``). With PostgreSQL
candidate events are discovered as ``anum_maintenance`` (ids, times, the rule and the reminder
minutes, migration ``0167_calendar_recurrence``: never a title or a person) and each is handled
in its own transaction inside its tenant's RLS context. Nothing here changes an event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Protocol

from sqlalchemy import text

from .audit import AuditRecord
from .calendar_api import MAX_REMINDER_MINUTES, CalendarEvent, CalendarStoreProtocol, EventStatus, MemoryCalendarStore, calendar_backend
from .calendar_recurrence import all_day_days, occurrences, zone_of
from .schemas import TenantContext, new_id, utc_now
from .settings import settings

logger = logging.getLogger(__name__)

NOTICE_KIND = "calendar_reminder"
SYSTEM_USER = "anum"
LOOKAHEAD = timedelta(minutes=MAX_REMINDER_MINUTES + 1)


class ReminderSendStore(Protocol):
    def claim(self, context: TenantContext, event_id: str, occurrence_start: datetime, minutes: int, user_id: str, now: datetime) -> bool: ...


class InMemoryReminderSendStore:
    def __init__(self) -> None:
        self.sent: set[tuple[str, str, str, datetime, int, str]] = set()
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.sent.clear()

    def claim(self, context: TenantContext, event_id: str, occurrence_start: datetime, minutes: int, user_id: str, now: datetime) -> bool:
        key = (context.tenant_id, context.workspace_id, event_id, occurrence_start, minutes, user_id)
        with self._lock:
            if key in self.sent:
                return False
            self.sent.add(key)
            return True


memory_send_store = InMemoryReminderSendStore()


class EmailSendStore(Protocol):
    def claim(self, context: TenantContext, event_id: str, occurrence_start: datetime, minutes: int, user_id: str, now: datetime) -> bool: ...
    def finish(self, context: TenantContext, event_id: str, occurrence_start: datetime, minutes: int, user_id: str, status: str, detail: str | None, now: datetime) -> None: ...


class InMemoryEmailSendStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, datetime, int, str], dict[str, Any]] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.rows.clear()

    def claim(self, context: TenantContext, event_id: str, occurrence_start: datetime, minutes: int, user_id: str, now: datetime) -> bool:
        key = (context.tenant_id, context.workspace_id, event_id, occurrence_start, minutes, user_id)
        with self._lock:
            if key in self.rows:
                return False
            self.rows[key] = {"status": "pending", "detail": None, "claimed_at": now}
            return True

    def finish(self, context: TenantContext, event_id: str, occurrence_start: datetime, minutes: int, user_id: str, status: str, detail: str | None, now: datetime) -> None:
        key = (context.tenant_id, context.workspace_id, event_id, occurrence_start, minutes, user_id)
        with self._lock:
            if key in self.rows:
                self.rows[key].update(status=status, detail=detail, finished_at=now)


memory_email_store = InMemoryEmailSendStore()


class SqlEmailSendStore:
    def __init__(self, session: Any) -> None:
        self.session = session

    def claim(self, context: TenantContext, event_id: str, occurrence_start: datetime, minutes: int, user_id: str, now: datetime) -> bool:
        inserted = self.session.execute(text(
            """
            insert into calendar_reminder_email_sends (tenant_id, workspace_id, event_id, occurrence_start, minutes_before, user_id, status, claimed_at)
            values (:t, :w, :e, :o, :m, :u, 'pending', :now) on conflict do nothing returning event_id
            """), {"t": context.tenant_id, "w": context.workspace_id, "e": event_id, "o": occurrence_start, "m": minutes, "u": user_id, "now": now},
        ).scalar_one_or_none()
        return inserted is not None

    def finish(self, context: TenantContext, event_id: str, occurrence_start: datetime, minutes: int, user_id: str, status: str, detail: str | None, now: datetime) -> None:
        self.session.execute(text(
            """
            update calendar_reminder_email_sends set status = :s, detail = :d, finished_at = :now
            where tenant_id = :t and workspace_id = :w and event_id = :e and occurrence_start = :o and minutes_before = :m and user_id = :u
            """), {"s": status, "d": detail, "now": now, "t": context.tenant_id, "w": context.workspace_id, "e": event_id, "o": occurrence_start, "m": minutes, "u": user_id},
        )


@dataclass(frozen=True)
class EmailJob:
    """One reminder email claimed inside the pass's transaction and sent after it committed."""

    event_id: str
    occurrence_start: datetime
    minutes: int
    user_id: str
    subject: str
    body: str


class SqlReminderSendStore:
    def __init__(self, session: Any) -> None:
        self.session = session

    def claim(self, context: TenantContext, event_id: str, occurrence_start: datetime, minutes: int, user_id: str, now: datetime) -> bool:
        inserted = self.session.execute(text(
            """
            insert into calendar_reminder_sends (tenant_id, workspace_id, event_id, occurrence_start, minutes_before, user_id, sent_at)
            values (:t, :w, :e, :o, :m, :u, :now) on conflict do nothing returning event_id
            """), {"t": context.tenant_id, "w": context.workspace_id, "e": event_id, "o": occurrence_start, "m": minutes, "u": user_id, "now": now},
        ).scalar_one_or_none()
        return inserted is not None


# --------------------------------------------------------------------------- what is due


def due_reminders(event: CalendarEvent, overrides: list[CalendarEvent], now: datetime) -> list[tuple[datetime, datetime, int]]:
    """``(occurrence_start, actual_start, minutes)`` of every reminder of ``event`` that is due at
    ``now``: its time has come and the occurrence has not started. A series contributes its
    occurrences in the look-ahead window minus the skipped and the overridden ones (a changed
    occurrence is its own row and brings its own reminders)."""
    if not event.reminders or event.status != EventStatus.SCHEDULED:
        return []
    if event.series_id is not None:
        starts = [(event.recurrence_start or event.starts_at, event.starts_at)]
    elif event.recurrence is not None:
        taken = set(event.skipped) | {o.recurrence_start for o in overrides if o.recurrence_start is not None}
        starts = [(s, s) for s in occurrences(event.recurrence, event.starts_at, event.timezone, now, now + LOOKAHEAD, limit=200) if s not in taken]
    else:
        starts = [(event.starts_at, event.starts_at)]
    due: list[tuple[datetime, datetime, int]] = []
    for original, actual in starts:
        if actual <= now:
            continue
        for minutes in event.reminders:
            if actual - timedelta(minutes=minutes) <= now:
                due.append((original, actual, minutes))
    return due


def _in_text(actual: datetime, now: datetime) -> str:
    minutes = max(0, int((actual - now).total_seconds() // 60))
    if minutes == 0:
        return "now"
    if minutes < 60:
        return f"in {minutes} minute" + ("" if minutes == 1 else "s")
    hours = minutes // 60
    if hours < 48:
        return f"in {hours} hour" + ("" if hours == 1 else "s")
    days = hours // 24
    return f"in {days} day" + ("" if days == 1 else "s")


def reminder_text(event: CalendarEvent, actual: datetime, now: datetime) -> tuple[str, str]:
    """The notification's title and body: when it starts in the event's zone, where, and how to join."""
    zone = zone_of(event.timezone)
    local = actual.astimezone(zone)
    if event.all_day:
        first, last = all_day_days(event.starts_at, event.ends_at, event.timezone)
        when = f"All day {first:%a %d %b}" if first == last else f"All day {first:%a %d %b} to {last:%a %d %b}"
    else:
        ends = (actual + (event.ends_at - event.starts_at)).astimezone(zone)
        when = f"{local:%a %d %b %H:%M} to {ends:%H:%M} ({zone.key})"
    parts = [when]
    if event.location:
        parts.append(f"At {event.location}")
    if event.conference_url:
        parts.append("Join from the event in Calendar")
    elif event.phone:
        parts.append(f"Call {event.phone}")
    title = f"{event.title} starts {_in_text(actual, now)}" if not event.all_day else f"{event.title}: {'today' if local.date() == now.astimezone(zone).date() else 'coming up'}"
    return title[:200], ". ".join(parts)[:1000]


def _in_text_ar(actual: datetime, now: datetime) -> str:
    minutes = max(0, int((actual - now).total_seconds() // 60))
    if minutes == 0:
        return "الآن"
    if minutes < 60:
        return f"بعد {minutes} دقيقة"
    hours = minutes // 60
    if hours < 48:
        return f"بعد {hours} ساعة"
    return f"بعد {hours // 24} يوم"


def reminder_email(event: CalendarEvent, actual: datetime, now: datetime) -> tuple[str, str]:
    """The reminder email's subject and plain-text body: English, then the same in Arabic. The
    event's own title, time in its zone and place; the meeting link stays in ANUM."""
    zone = zone_of(event.timezone)
    local = actual.astimezone(zone)
    stamp = f"{local:%Y-%m-%d}" if event.all_day else f"{local:%Y-%m-%d %H:%M} ({zone.key})"
    english_title, english_body = reminder_text(event, actual, now)
    arabic_when = "طوال اليوم" if event.all_day else _in_text_ar(actual, now)
    arabic = [f"تذكير: {event.title}، {arabic_when}.", f"الموعد: {stamp}"]
    if event.location:
        arabic.append(f"المكان: {event.location}")
    if event.conference_url:
        arabic.append("انضم من الحدث في التقويم داخل ANUM.")
    elif event.phone:
        arabic.append(f"اتصل على {event.phone}")
    body = (
        f"{english_body}.\n\nOpen Calendar in ANUM to see the event.\n\n"
        f"{chr(10).join(arabic)}\n\nافتح التقويم في ANUM لرؤية الحدث.\n\n"
        "You get this email because the event has an email reminder and email notifications are on in your ANUM notification settings.\n"
        "تصلك هذه الرسالة لأن للحدث تذكيرًا بالبريد ولأن إشعارات البريد مفعّلة في إعدادات الإشعارات في ANUM."
    )
    return f"ANUM: {english_title}"[:200], body[:4000]


def recipients_of(event: CalendarEvent, profiles: dict[str, Any]) -> list[str]:
    """The creator, plus members whose team profile email is on the attendee list."""
    emails = {a.email.lower() for a in event.attendees if a.email}
    people = {event.created_by}
    for user_id, profile in profiles.items():
        email = getattr(profile, "email", None)
        if email and email.lower() in emails:
            people.add(user_id)
    return sorted(p for p in people if p and p != SYSTEM_USER)


def _calendar_store(repository: Any, context: TenantContext) -> CalendarStoreProtocol:
    if settings.repository_backend == "memory":
        return MemoryCalendarStore(calendar_backend, context)
    from .db.calendar_repository import SqlAlchemyCalendarStore

    return SqlAlchemyCalendarStore(repository.session, context)


def _send_store(repository: Any) -> ReminderSendStore:
    if settings.repository_backend == "memory":
        return memory_send_store
    return SqlReminderSendStore(repository.session)


def _email_store(repository: Any) -> EmailSendStore:
    if settings.repository_backend == "memory":
        return memory_email_store
    return SqlEmailSendStore(repository.session)


def remind_event(context: TenantContext, event_id: str, now: datetime | None = None) -> int:
    """Send every due reminder of one event to its people; returns how many in-app notices were
    sent. Due email reminders are claimed in the same transaction and sent after it committed."""
    from .notifications import email_configured

    jobs: list[EmailJob] = []
    sent = _remind_in_app(context, event_id, now or utc_now(), jobs if email_configured() else None)
    if jobs:
        send_reminder_emails(context, jobs, now or utc_now())
    return sent


def _remind_in_app(context: TenantContext, event_id: str, now: datetime, jobs: list[EmailJob] | None) -> int:
    from .events import CanonicalEventName, create_event
    from .notifications import create_notification
    from .team_work import open_team_unit

    sent = 0
    with open_team_unit(context) as unit:
        store = _calendar_store(unit.repository, context)
        event = store.get(event_id)
        if event is None:
            return 0
        due = due_reminders(event, store.overrides(event.id) if event.recurrence is not None else [], now)
        if not due:
            return 0
        sends = _send_store(unit.repository)
        emails = _email_store(unit.repository)
        people = recipients_of(event, unit.store.list_profiles(context))
        claimed_for: dict[datetime, int] = {}
        for original, actual, minutes in due:
            title, body = reminder_text(event, actual, now)
            if jobs is not None and minutes in event.email_reminders:
                subject, text_body = reminder_email(event, actual, now)
                for user_id in people:
                    if emails.claim(context, event.series_id or event.id, original, minutes, user_id, now):
                        jobs.append(EmailJob(event.series_id or event.id, original, minutes, user_id, subject, text_body))
            for user_id in people:
                if not sends.claim(context, event.series_id or event.id, original, minutes, user_id, now):
                    continue
                notification = create_notification(unit.store, context, user_id=user_id, kind=NOTICE_KIND, title=title, body=body, work_item_id=None)
                unit.repository.record_event(create_event(
                    CanonicalEventName.NOTIFICATION_CREATED, context, notification.id,
                    {"recipient": user_id, "kind": NOTICE_KIND, "work_item_id": None, "calendar_event_id": event.series_id or event.id},
                ).event)
                sent += 1
                claimed_for[original] = claimed_for.get(original, 0) + 1
        for original, count in claimed_for.items():
            unit.repository.record_audit(AuditRecord(
                id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=SYSTEM_USER,
                action="calendar.reminder_sent", target=event.series_id or event.id, outcome="success",
                correlation_id=new_id("correlation"), created_at=now,
                metadata={"occurrence_start": original.isoformat(), "recipients": count},
            ))
    return sent


def _email_outcome(context: TenantContext, user_id: str) -> tuple[str | None, str | None]:
    """(address, None) when ``user_id`` may get the email, else (None, why not)."""
    from .contacts.models import Channel
    from .contacts.service import do_not_contact_block
    from .notifications import recipient_address
    from .onboarding import NotificationPreferences, open_notification_preference_store

    recipient = context.model_copy(update={"user_id": user_id})
    with open_notification_preference_store(recipient) as store:
        preferences = store.get(recipient) or NotificationPreferences()
    if not preferences.email_enabled:
        return None, "Email notifications are off in this person's notification settings"
    address = recipient_address(context, user_id)
    if not address:
        return None, "This person has no email address ANUM knows"
    if do_not_contact_block(context, Channel.EMAIL, address):
        return None, "The Contacts directory marks this address do not contact by email"
    return address, None


def send_reminder_emails(context: TenantContext, jobs: list[EmailJob], now: datetime) -> dict[str, int]:
    """Send claimed reminder emails, outside any transaction (SMTP is a network call), and keep
    each outcome on its claim and in the audit log (``calendar.reminder_emailed``: the occurrence,
    the person and the outcome; never the address or the title)."""
    from .dependencies import _unit_of_work
    from .notifications import send_with_retries

    counts = {"sent": 0, "failed": 0, "skipped": 0}
    for job in jobs:
        try:
            address, why_not = _email_outcome(context, job.user_id)
            if address is None:
                status, detail, attempts = "skipped", why_not, 0
            else:
                outcome = send_with_retries(address, job.subject, job.body)
                status = "sent" if outcome.status == "sent" else "failed"
                detail, attempts = outcome.error, outcome.attempts
        except Exception:  # one person's email must never stop the others
            logger.exception("calendar_reminder_email_failed", extra={"anum_calendar_event_id": job.event_id})
            status, detail, attempts = "failed", "The email could not be prepared", 0
        counts[status] += 1
        try:
            with _unit_of_work(context, require_membership=False) as repository:
                _email_store(repository).finish(context, job.event_id, job.occurrence_start, job.minutes, job.user_id, status, (detail or None) and detail[:300], utc_now())
                repository.record_audit(AuditRecord(
                    id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=SYSTEM_USER,
                    action="calendar.reminder_emailed", target=job.event_id, outcome="success" if status == "sent" else ("denied" if status == "skipped" else "failure"),
                    correlation_id=new_id("correlation"), created_at=now,
                    metadata={"occurrence_start": job.occurrence_start.isoformat(), "minutes": job.minutes, "recipient": job.user_id,
                              "status": status, "attempts": attempts, "detail": detail},
                ))
        except Exception:
            logger.exception("calendar_reminder_email_record_failed", extra={"anum_calendar_event_id": job.event_id})
    return counts


def _due_sql(batch: int, now: datetime) -> list[tuple[str, str, str]]:
    from .maintenance import default_session_factory, discover

    rows = discover(default_session_factory, text(
        """
        select tenant_id, workspace_id, id
        from calendar_events
        where status = 'scheduled'
          and jsonb_array_length(reminders) > 0
          and starts_at <= :horizon
          and (
            (recurrence is null and starts_at > :now)
            or (recurrence is not null and (recurrence_until is null or recurrence_until > :now))
          )
        order by starts_at, id
        limit :batch
        """), {"now": now, "horizon": now + LOOKAHEAD, "batch": batch})
    return [(row.tenant_id, row.workspace_id, row.id) for row in rows]


def _due_memory(now: datetime, batch: int) -> list[tuple[str, str, str]]:
    horizon = now + LOOKAHEAD
    with calendar_backend.lock:
        rows = sorted(
            (
                (e.starts_at, e.tenant_id, e.workspace_id, e.id) for e in calendar_backend.events.values()
                if e.status == EventStatus.SCHEDULED and e.reminders and e.starts_at <= horizon
                and ((e.recurrence is None and e.starts_at > now) or (e.recurrence is not None and (e.recurrence_until is None or e.recurrence_until > now)))
            )
        )
    return [(t, w, i) for _, t, w, i in rows[:batch]]


def run_calendar_reminder_pass(batch: int = 500, now: datetime | None = None) -> int:
    """One sweep over every tenant: send the reminders that are due. Returns how many were sent."""
    now = now or utc_now()
    due = _due_sql(batch, now) if settings.repository_backend == "postgresql" else _due_memory(now, batch)
    sent = 0
    for tenant_id, workspace_id, event_id in due:
        ctx = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=SYSTEM_USER, roles=[])
        try:
            sent += remind_event(ctx, event_id, now)
        except Exception:
            logger.exception("calendar_reminder_failed", extra={"anum_calendar_event_id": event_id})
    return sent
