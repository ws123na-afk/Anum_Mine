"""Email for the notification switches that are not team work (docs/notifications.md#email).

Phone push (``push.py``) and email follow the same switches. A committed domain event that
push would deliver (an approval waiting, a task finished or failed, a developer request
ready or failed, a store or tyre alert, a failed automation) is also emailed to each
recipient who turned **Email** on, who has the kind's own switch on, and who has an address
ANUM knows (the team profile, else the verified email of the invitation they accepted).

The recipients are the ones push resolves (``push.resolve_recipients``: active members who
may see the item), so email never reaches anyone push would not. The message carries the
same fixed sentence as the push and never a task title, prompt, tool argument or token.
Sending runs on a small worker pool after the commit, with retries for transient failures
(``notifications.send_with_retries``), and each outcome is audited as ``notification.email``
(sent, failed, no_address) so a switch that is on but delivers nothing can be explained.
Team work notifications keep their own path (``notifications.deliver_pending_email``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from .audit import AuditRecord
from .push import PREFERENCE, TEXT, PushIntent, PushKind, intent_for, resolve_recipients
from .schemas import DomainEvent, TenantContext, new_id, utc_now

logger = logging.getLogger("anum_api.notification_email")

# Kinds whose events are emailed here. Team work, the finance report and calls arrive as
# ``notification.created`` and are emailed by their own notification row.
EMAILED_KINDS = frozenset(
    {
        PushKind.APPROVAL_REQUIRED,
        PushKind.TASK_COMPLETED,
        PushKind.RUN_FAILED,
        PushKind.DEVELOPER_READY,
        PushKind.DEVELOPER_FAILED,
        PushKind.COMMERCE_ALERT,
        PushKind.TYRE_ALERTS,
        PushKind.AUTOMATION_FAILED,
    }
)

_pool: ThreadPoolExecutor | None = None


def _executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="anum-notification-email")
    return _pool


def email_text(kind: PushKind) -> tuple[str, str]:
    title, sentence = TEXT[kind]
    body = (
        f"{sentence}\n\nOpen ANUM to see it.\n\n"
        "You get these emails because email notifications are on in your ANUM notification settings."
    )
    return f"ANUM: {title}", body


def _wants_email(intent: PushIntent, user_id: str) -> bool:
    from .onboarding import NotificationPreferences, open_notification_preference_store

    context = TenantContext(tenant_id=intent.tenant_id, workspace_id=intent.workspace_id, user_id=user_id, roles=[])
    with open_notification_preference_store(context) as store:
        preferences = store.get(context) or NotificationPreferences()
    return bool(preferences.email_enabled and getattr(preferences, PREFERENCE[intent.kind]))


def _audit(intent: PushIntent, user_id: str, status: str, attempts: int, error: str | None) -> None:
    from .dependencies import _unit_of_work

    context = TenantContext(tenant_id=intent.tenant_id, workspace_id=intent.workspace_id, user_id=user_id, roles=[])
    with _unit_of_work(context, require_membership=False) as repository:
        repository.record_audit(
            AuditRecord(
                id=new_id("audit"),
                tenant_id=intent.tenant_id,
                workspace_id=intent.workspace_id,
                actor="system",
                action="notification.email",
                target=intent.event_id,
                outcome="success" if status == "sent" else "failure",
                correlation_id=intent.event_id,
                created_at=utc_now(),
                metadata={"kind": intent.kind.value, "recipient": user_id, "status": status, "attempts": attempts, "error": error},
            )
        )


def deliver_intent_email(intent: PushIntent) -> dict[str, int]:
    """Email one event to its recipients (blocking; runs on the worker pool or in tests)."""
    from .notifications import email_configured, recipient_address, send_with_retries

    counts = {"sent": 0, "failed": 0, "no_address": 0, "skipped": 0}
    if not email_configured():
        return counts
    base = TenantContext(tenant_id=intent.tenant_id, workspace_id=intent.workspace_id, user_id="system", roles=[])
    subject, text = email_text(intent.kind)
    for user_id in resolve_recipients(intent):
        try:
            if not _wants_email(intent, user_id):
                counts["skipped"] += 1
                continue
            address = recipient_address(base, user_id)
            if not address:
                counts["no_address"] += 1
                _audit(intent, user_id, "no_address", 0, "The recipient has no email address ANUM knows")
                continue
            outcome = send_with_retries(address, subject, text)
            counts["sent" if outcome.status == "sent" else "failed"] += 1
            _audit(intent, user_id, outcome.status, outcome.attempts, outcome.error)
        except Exception:  # one recipient must never stop the others
            counts["failed"] += 1
            logger.exception("Notification email failed", extra={"anum_event_id": intent.event_id})
    return counts


def submit(events: Sequence[DomainEvent]) -> None:
    """Event listener (API and worker): queue the emails of committed events. Cheap and
    thread-safe; does nothing without a mail server."""
    from .notifications import email_configured

    if not email_configured():
        return
    for event in events:
        intent = intent_for(event)
        if intent is not None and intent.kind in EMAILED_KINDS:
            _executor().submit(_safe_deliver, intent)


def _safe_deliver(intent: PushIntent) -> None:
    try:
        deliver_intent_email(intent)
    except Exception:
        logger.exception("Notification email delivery crashed", extra={"anum_event_id": intent.event_id})


def shutdown() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=False)


__all__ = ["EMAILED_KINDS", "deliver_intent_email", "email_text", "shutdown", "submit"]
