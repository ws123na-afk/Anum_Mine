"""In-app notifications with email delivery (docs/team-work.md#notifications).

A notification is always stored for its recipient (the in-app inbox: web bell, Flutter
Notifications screen, and a ``notification.created`` event on the workspace stream that
desktop and web clients turn into an OS notification when the recipient's
``desktop_enabled`` preference is on). Email follows the recipient's existing
``email_enabled`` preference and needs both an address in their team profile and
``ANUM_SMTP_HOST``: otherwise the row records ``not_requested`` or ``not_configured``.
Phone push goes through the existing FCM path (``anum_api/push.py``): the
``notification.created`` event is the push trigger (fixed text, ids only), so the row
records ``queued`` when push is on for this server and the recipient, ``not_requested``
when the recipient turned phone push off, and ``not_configured`` when the server has no
FCM key. Most kinds push as team work; the post-close trading report notice
(``finance_daily_report``) pushes as its own kind with its own text (``push.FINANCE_REPORT``).

Email is sent after the request commits (``deliver_pending_email``), never inside the
transaction, and retried by the reminder sweep up to three attempts. The message holds
the notification's title and body only: never tokens or links with credentials.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .dependencies import tenant_context
from .identity import is_local_environment
from .onboarding import NotificationPreferences
from .schemas import TenantContext, new_id, utc_now
from .settings import settings
from .team_models import EmailStatus, NotificationPage, PushStatus, UserNotification
from .team_store import TeamStore, UnknownCursorError

logger = logging.getLogger("anum_api.notifications")

MAX_EMAIL_ATTEMPTS = 3


def recipient_preferences(store: TeamStore, context: TenantContext, user_id: str) -> NotificationPreferences:
    """The recipient's saved preferences in this workspace (defaults when never saved)."""
    target = context.model_copy(update={"user_id": user_id})
    session = getattr(store, "session", None)
    if session is not None:
        from .db.workspace_settings_repository import SqlAlchemyNotificationPreferenceStore

        found = SqlAlchemyNotificationPreferenceStore(session).get(target)
    else:
        from .onboarding import _notifications

        found = _notifications.get(target)
    return found or NotificationPreferences()


def email_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def create_notification(
    store: TeamStore,
    context: TenantContext,
    *,
    user_id: str,
    kind: str,
    title: str,
    body: str,
    work_item_id: str | None = None,
    email: str | None = None,
) -> UserNotification:
    """Store one notification for ``user_id`` in the caller's workspace.

    The caller is often someone else (an assignment notifies the assignee); the insert
    policy allows that, and only the recipient can read the row afterwards.
    """
    preferences = recipient_preferences(store, context, user_id)
    from .push import configured_account

    if configured_account() is None:
        push_status = PushStatus.NOT_CONFIGURED
    else:
        push_status = PushStatus.QUEUED if getattr(preferences, "push_enabled", True) else PushStatus.NOT_REQUESTED
    if not preferences.email_enabled or not email:
        email_status = EmailStatus.NOT_REQUESTED
    elif not email_configured():
        email_status = EmailStatus.NOT_CONFIGURED
    else:
        email_status = EmailStatus.PENDING
    notification = UserNotification(
        id=new_id("ntf"),
        user_id=user_id,
        kind=kind,
        title=title[:200],
        body=body[:1000],
        work_item_id=work_item_id,
        email_status=email_status,
        push_status=push_status,
        created_at=utc_now(),
    )
    return store.add_notification(context, notification)


class EmailRefused(RuntimeError):
    """The SMTP server could not be used safely or refused the message.

    ``transient`` is true when trying again later can help: the server was unreachable,
    hung up, or answered with a 4xx code. Configuration problems and 5xx refusals are
    permanent and are not retried.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


def _unreachable(exc: OSError) -> EmailRefused:
    return EmailRefused(f"The mail server could not be reached ({type(exc).__name__})", transient=True)


def _smtp_failure(exc: smtplib.SMTPException) -> EmailRefused:
    """A short sentence safe to show (never the server's raw answer), and whether to retry."""
    name = type(exc).__name__
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return EmailRefused("The mail server refused ANUM's sign-in (check ANUM_SMTP_USERNAME and ANUM_SMTP_PASSWORD)")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return EmailRefused("The mail server refused the recipient address")
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)):
        return EmailRefused(f"The mail server could not be reached ({name})", transient=True)
    code = getattr(exc, "smtp_code", None)
    if isinstance(code, int) and 400 <= code < 500:
        return EmailRefused(f"The mail server asked to try again later ({code})", transient=True)
    if isinstance(code, int):
        return EmailRefused(f"The mail server refused the message ({code})")
    return EmailRefused(f"The mail server refused the message ({name})")


def send_email(to: str, subject: str, text: str) -> None:
    """Send one plain-text email through the configured SMTP server."""
    host, sender = settings.smtp_host, settings.smtp_from
    if not host or not sender:
        raise EmailRefused("SMTP is not configured")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    timeout = settings.smtp_timeout_seconds
    context = ssl.create_default_context()
    security = settings.smtp_security
    if security == "none" and not is_local_environment(settings.environment):
        raise EmailRefused("Unencrypted SMTP is allowed only in local and test")
    client: smtplib.SMTP
    try:
        if security == "tls":
            client = smtplib.SMTP_SSL(host, settings.smtp_port, timeout=timeout, context=context)
        else:
            client = smtplib.SMTP(host, settings.smtp_port, timeout=timeout)
    except smtplib.SMTPException as exc:
        raise _smtp_failure(exc) from exc
    except OSError as exc:
        raise _unreachable(exc) from exc
    try:
        if security == "starttls":
            client.ehlo()
            if not client.has_extn("starttls"):
                raise EmailRefused("The SMTP server does not offer STARTTLS")
            client.starttls(context=context)
            client.ehlo()
        login = settings.smtp_username
        secret = settings.smtp_password.get_secret_value() if settings.smtp_password else None
        if login and secret:
            client.login(login, secret)
        client.send_message(message)
    except EmailRefused:
        raise
    except smtplib.SMTPException as exc:
        raise _smtp_failure(exc) from exc
    except OSError as exc:
        raise _unreachable(exc) from exc
    finally:
        try:
            client.quit()
        except (smtplib.SMTPException, OSError):
            pass


@dataclass(frozen=True)
class MailOutcome:
    """What happened to one system email (an invitation, a notice, a test message).

    ``status``: ``sent``; ``failed`` (refused, or still failing after ``attempts`` tries);
    ``not_configured`` (no ``ANUM_SMTP_HOST``/``ANUM_SMTP_FROM``); ``no_address`` (the
    recipient has no email address ANUM knows); ``not_requested`` (nobody asked for it).
    ``error`` is a sentence safe to show, never the server's raw answer or a credential.
    """

    status: str
    attempts: int = 0
    error: str | None = None


def send_with_retries(
    to: str,
    subject: str,
    text: str,
    *,
    attempts: int = MAX_EMAIL_ATTEMPTS,
    backoff_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> MailOutcome:
    """Send one system email now, retrying transient failures with exponential backoff.

    Blocking (SMTP is a network call): run it in a worker thread, never on the event loop,
    and never while a database transaction is open (docs/notifications.md#email).
    """
    if not email_configured():
        return MailOutcome("not_configured", 0, "No mail server is set up on this ANUM server (ANUM_SMTP_HOST and ANUM_SMTP_FROM)")
    delay = settings.smtp_retry_backoff_seconds if backoff_seconds is None else backoff_seconds
    error = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            # Looked up on the module so tests can replace ``send_email``.
            sys.modules[__name__].send_email(to, subject, text)
        except EmailRefused as exc:
            error = str(exc)[:300]
            if not exc.transient or attempt >= attempts:
                return MailOutcome("failed", attempt, error)
        except OSError as exc:
            error = _unreachable(exc).args[0]
            if attempt >= attempts:
                return MailOutcome("failed", attempt, error)
        else:
            return MailOutcome("sent", attempt, None)
        sleep(delay * (2 ** (attempt - 1)))
    return MailOutcome("failed", attempts, error)  # pragma: no cover - the loop always returns


def recipient_address(context: TenantContext, user_id: str) -> str | None:
    """The address ANUM emails ``user_id`` at in this workspace: the email in their team
    profile, else the verified email of the invitation they accepted. Read in the
    recipient's own RLS context, in its own short unit of work (call it in a thread)."""
    from .team_work import open_team_unit

    recipient = context.model_copy(update={"user_id": user_id})
    with open_team_unit(recipient) as unit:
        profile = unit.store.list_profiles(recipient).get(user_id)
        if profile is not None and profile.email:
            return profile.email
        from .schemas import InvitationStatus

        for invitation in unit.repository.list_invitations(recipient):
            if invitation.status == InvitationStatus.ACCEPTED and invitation.accepted_by_user_id == user_id and invitation.invitee_email:
                return invitation.invitee_email
    return None


def _email_text(notification: UserNotification) -> str:
    return f"{notification.body}\n\nOpen ANUM to see it and reply.\n\nYou get these emails because email notifications are on in your ANUM notification settings."


def deliver_email(store: TeamStore, recipient_context: TenantContext, notification_id: str, address: str | None) -> UserNotification | None:
    """Try the email of one pending notification (the store is opened as the recipient)."""
    notification = store.get_notification(recipient_context, notification_id)
    if notification is None or notification.email_status != EmailStatus.PENDING:
        return notification
    notification.email_attempts += 1
    if not address:
        notification.email_status = EmailStatus.FAILED
        notification.email_error = "The recipient has no email address in their team profile"
    else:
        try:
            send_email(address, f"ANUM: {notification.title}", _email_text(notification))
        except (EmailRefused, OSError) as exc:
            message = str(exc) if isinstance(exc, EmailRefused) else type(exc).__name__
            notification.email_error = message[:300]
            if notification.email_attempts >= MAX_EMAIL_ATTEMPTS:
                notification.email_status = EmailStatus.FAILED
            logger.warning("Notification email failed", extra={"anum_notification_id": notification.id, "anum_error": message[:120]})
        else:
            notification.email_status = EmailStatus.SENT
            notification.email_error = None
    return store.save_notification(recipient_context, notification)


def deliver_pending_email(context: TenantContext, pending: Iterable[tuple[str, str]]) -> None:
    """After commit: send the email of notifications created by this request.

    ``pending`` holds (recipient user id, notification id). Each runs in its own unit of
    work as the recipient (their RLS context); a failure is recorded on the row and the
    reminder sweep retries it.
    """
    from .team_work import open_team_unit

    for user_id, notification_id in pending:
        recipient = context.model_copy(update={"user_id": user_id})
        try:
            with open_team_unit(recipient) as unit:
                profile = unit.store.list_profiles(recipient).get(user_id)
                deliver_email(unit.store, recipient, notification_id, profile.email if profile else None)
        except Exception:  # never fail the caller's request after it committed
            logger.exception("Notification email delivery failed", extra={"anum_notification_id": notification_id})


# ----------------------------------------------------------------------------- routes

router = APIRouter(prefix="/api/v1", tags=["notifications"])


def _store_for_caller(context: TenantContext) -> Any:
    from .team_work import open_team_unit

    return open_team_unit(context, require_membership=True)


@router.get("/notifications", response_model=NotificationPage)
async def list_my_notifications(
    unread_only: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
) -> NotificationPage:
    """The caller's own notifications in this workspace, newest first. Every member may read theirs."""
    with _store_for_caller(context) as unit:
        store = unit.store
        try:
            items = store.list_notifications(context, limit=limit + 1, before=cursor, unread_only=unread_only)
        except UnknownCursorError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown cursor; reload the list") from exc
        unread = store.count_unread(context)
    more = len(items) > limit
    items = items[:limit]
    return NotificationPage(items=items, unread=unread, next_cursor=items[-1].id if more and items else None)


@router.post("/notifications/{notification_id}/read", response_model=UserNotification)
async def mark_notification_read(notification_id: str, context: TenantContext = Depends(tenant_context)) -> UserNotification:
    with _store_for_caller(context) as unit:
        store = unit.store
        notification = store.get_notification(context, notification_id)
        if notification is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
        if notification.read_at is None:
            notification.read_at = utc_now()
            store.save_notification(context, notification)
        return notification


@router.post("/notifications/read-all")
async def mark_all_notifications_read(context: TenantContext = Depends(tenant_context)) -> dict[str, int]:
    with _store_for_caller(context) as unit:
        return {"updated": unit.store.mark_all_read(context, utc_now())}
