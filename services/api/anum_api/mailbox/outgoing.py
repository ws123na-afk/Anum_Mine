"""Outgoing attachments and scheduled send of the Email Center (docs/email.md#attachments,
docs/email.md#scheduled-send).

Attachments: every file passes the same checks as a workspace upload (``files.py``: the
allowed types, the content signature and the :class:`~anum_api.file_policy.FileScanner`
hook), then the size and count caps of ``ANUM_MAILSERVER_OUTBOUND_*``. The bytes go to object
storage under the workspace's prefix; the row (name, type, size, SHA-256) is private to the
person, like the draft. The approved send call names each file's SHA-256, and the bytes are
checked against it again when the message is built, so what goes out is what was approved.

Scheduled send: approving a message with a ``send_at`` in the future only marks it
``scheduled``. :class:`ScheduledSendRunner` (each replica, PostgreSQL or memory) finds due work
as ``anum_maintenance`` (ids, scope, status and times only) and acts in the owner's own scope:

* a ``scheduled`` message whose time has come is claimed (compare-and-set to ``sending`` with a
  lease), checked against the hash of the approved call, and sent once;
* a message still waiting for approval more than :data:`~.service.DUE_GRACE` after its time
  goes back to a draft and its approval is withdrawn: a send is approved before it is due;
* a ``sending`` message whose lease ran out (the replica stopped mid-send) becomes ``unknown``
  and is never sent again by itself: it may have reached the server.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from ..audit import AuditRecord
from ..schemas import TenantContext, new_id, utc_now
from ..settings import settings
from . import monitor
from .config import mail_settings
from .store import SEND_LEASE, DraftStatus, MailAttachment, MailDraft, mail_backend, open_mail_store

logger = logging.getLogger("anum.mail")
BATCH = 50


class OutgoingRefused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class AttachmentGone(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _storage():  # type: ignore[no-untyped-def]
    from ..files import file_store

    return file_store.storage


def _caps_problem(existing: list[MailAttachment], size: int) -> str | None:
    cfg = mail_settings
    from ..files import human_bytes

    if size > cfg.outbound_attachment_max_bytes:
        return f"A file can be at most {human_bytes(cfg.outbound_attachment_max_bytes)}. Share a link to a larger file instead."
    if len(existing) >= cfg.outbound_max_attachments:
        return f"A message can carry at most {cfg.outbound_max_attachments} files."
    total = sum(a.size_bytes for a in existing) + size
    if total > cfg.outbound_total_max_bytes:
        return f"The files of one message can add up to at most {human_bytes(cfg.outbound_total_max_bytes)}."
    return None


def check_file(name: str, declared: str | None, content: bytes) -> tuple[str, str, bool]:
    """The workspace upload checks: the name, the allowed type, the signature and the scan.

    Returns the clean name, the stored content type and whether a scanner saw the bytes;
    raises :class:`OutgoingRefused` with the status and the sentence for the person.
    """
    from fastapi import HTTPException

    from ..files import check_upload_content, resolve_upload_type, valid_file_name

    try:
        clean = valid_file_name(name)
        if len(clean) > 255:
            raise HTTPException(422, "The file name can be at most 255 characters.")
        allowed = resolve_upload_type(clean, declared)
        scanned = check_upload_content(clean, allowed, content)
    except HTTPException as exc:
        raise OutgoingRefused(exc.status_code, str(exc.detail)) from None
    return clean, allowed.content_type, scanned


def add_attachment(context: TenantContext, draft: MailDraft, name: str, declared: str | None, content: bytes) -> MailAttachment:
    """Check, scan and keep one file on ``draft`` (the caller has checked the draft is editable)."""
    from ..files import workspace_object_key

    clean, content_type, scanned = check_file(name, declared, content)
    with open_mail_store(context) as store:
        problem = _caps_problem(store.list_attachments(draft.id), len(content))
        if problem is None and store.pending_attachment_bytes() + len(content) > mail_settings.outbound_storage_per_person_bytes:
            problem = "Your unsent messages already hold as many files as ANUM keeps. Send or discard some drafts first."
    if problem is not None:
        raise OutgoingRefused(413, problem)
    digest = hashlib.sha256(content).hexdigest()
    attachment_id = new_id("mailfile")
    key = workspace_object_key(context.tenant_id, context.workspace_id, "mail", attachment_id, digest)
    attachment = MailAttachment(
        id=attachment_id, tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id,
        draft_id=draft.id, filename=clean, content_type=content_type, size_bytes=len(content), sha256=digest,
        storage_key=key, scanned=scanned, created_at=utc_now(),
    )
    _storage().put(key, content, content_type)
    try:
        with open_mail_store(context) as store:
            if _caps_problem(store.list_attachments(draft.id), len(content)) is not None:
                raise OutgoingRefused(413, "Another file was added at the same time. Check the files and try again.")
            store.save_attachment(attachment)
    except BaseException:
        # No row, no object: never leave unreachable bytes behind.
        _storage().delete(key)
        raise
    return attachment


def read_attachment(attachment: MailAttachment) -> bytes:
    """The bytes as approved: a file that is gone or changed is never sent."""
    try:
        data = _storage().get(attachment.storage_key)
    except (FileNotFoundError, KeyError):
        raise AttachmentGone(f"The attached file {attachment.filename} is no longer stored. Attach it again.") from None
    if hashlib.sha256(data).hexdigest() != attachment.sha256:
        raise AttachmentGone(f"The attached file {attachment.filename} changed after it was attached. Attach it again.")
    return data


def forget_objects(attachments: list[MailAttachment]) -> None:
    """Delete stored bytes after their rows are gone (best effort; never fails the caller)."""
    for attachment in attachments:
        try:
            _storage().delete(attachment.storage_key)
        except Exception:  # noqa: BLE001 - the row is already gone; a stray object is only storage
            logger.warning("mail.attachment_object_delete_failed", extra={"anum_mail": {"attachment_id": attachment.id}})


# -- scheduled send ----------------------------------------------------------------------------

_DUE = text(
    "select tenant_id, workspace_id, owner_user_id, id from mail_drafts "
    "where ((status = 'scheduled' and send_at <= :now) "
    "or (status = 'pending_approval' and send_at is not null and send_at < :unapproved_before) "
    "or (status = 'sending' and send_lease_until is not null and send_lease_until < :now)) "
    # A tenant whose emergency stop is on keeps its due mail held (docs/agent-controls.md).
    "and not exists (select 1 from agent_emergency_stops stop "
    "where stop.tenant_id = mail_drafts.tenant_id and stop.engaged) "
    "order by coalesce(send_at, send_lease_until) limit :limit"
)


def due_work(now: datetime, limit: int = BATCH) -> list[tuple[str, str, str, str]]:
    from .service import DUE_GRACE

    if settings.repository_backend == "memory":
        return mail_backend.due_work(now, now - DUE_GRACE, limit)
    from ..maintenance import default_session_factory, discover

    rows = discover(default_session_factory, _DUE, {"now": now, "unapproved_before": now - DUE_GRACE, "limit": limit})
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def _audit(context: TenantContext, action: str, target: str, outcome: str, metadata: dict[str, Any]) -> None:
    from ..dependencies import _unit_of_work

    with _unit_of_work(context, require_membership=False) as repository:
        repository.record_audit(
            AuditRecord(
                id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor="anum.mail.scheduler",
                action=action, target=target, outcome=outcome, correlation_id=target, created_at=utc_now(), metadata=metadata,
            )
        )


def _still_a_member(context: TenantContext) -> bool:
    """With OIDC a scheduled message goes only while its owner is an active owner or member."""
    if settings.auth_mode != "oidc":
        return True
    from ..dependencies import _unit_of_work

    with _unit_of_work(context, require_membership=False) as repository:
        membership = repository.get_membership(context)
    return membership is not None and membership.active and membership.role.lower() in {"owner", "member"}


def _withdraw(context: TenantContext, previous: MailDraft) -> None:
    from ..dependencies import _unit_of_work
    from ..email_api import _retire

    with _unit_of_work(context, require_membership=False) as repository:
        _retire(repository, context, previous)  # type: ignore[arg-type]


async def handle_due(tenant_id: str, workspace_id: str, owner_user_id: str, draft_id: str, now: datetime | None = None) -> str:
    """One piece of due work, in its owner's scope. Returns what happened (for logs and tests)."""
    from .service import DUE_GRACE, MailError, content_hash, deliver, require_available, send_arguments

    now = now or utc_now()
    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=owner_user_id, roles=["member"])
    from ..agent_controls import agents_stopped

    if await asyncio.to_thread(agents_stopped, context):
        # The organization's emergency stop: the message stays scheduled and goes on a pass
        # after an owner turns the stop off (docs/agent-controls.md).
        return "held"

    def claim() -> str:
        with open_mail_store(context) as store:
            if store.claim_scheduled(draft_id, now, now + SEND_LEASE):
                return "claimed"
            if store.release_interrupted(draft_id, now):
                return "interrupted"
            previous = store.expire_unapproved(draft_id, now - DUE_GRACE)
        if previous is not None:
            _withdraw(context, previous)
            return "not_approved"
        return "skipped"

    outcome = await asyncio.to_thread(claim)
    if outcome in {"interrupted", "not_approved"}:
        await asyncio.to_thread(_audit, context, f"mail.send.{outcome}", draft_id, "failure", {})
    if outcome != "claimed":
        return outcome

    def load() -> tuple[MailDraft | None, Any, str | None, Any, list[MailAttachment]]:
        # The writer's own mailbox, or one its owner shared with them (checked again at send time).
        with open_mail_store(context) as store:
            draft = store.get_draft(draft_id)
            found = store.get_accessible_account(draft.account_id) if draft else None
            identity = store.get_identity(draft.identity_id) if draft and draft.identity_id else None
            return draft, found[0] if found else None, found[1] if found else None, identity, store.list_attachments(draft_id) if draft else []

    draft, account, access, identity, attachments = await asyncio.to_thread(load)
    if draft is None:
        return "gone"

    async def fail(message: str, reason: str) -> str:
        from .service import record_send

        await asyncio.to_thread(record_send, context, draft_id, DraftStatus.FAILED, message)
        await asyncio.to_thread(_audit, context, "mail.send.scheduled", draft_id, "failure", {"reason": reason})
        return "failed"

    if account is None or access not in {"owner", "send"}:
        return await fail("Not sent: the mailbox's owner no longer lets the writer send from it.", "share")
    if draft.identity_id and identity is None:
        return await fail("Not sent: the sender identity was removed. Pick a sender and schedule it again.", "identity")
    if not await asyncio.to_thread(_still_a_member, context):
        return await fail("Not sent: the person who scheduled it is no longer a member of this workspace.", "membership")
    try:
        require_available(context)
    except MailError as exc:
        return await fail(f"Not sent: {exc.message}", exc.kind)
    if draft.approved_hash != content_hash(send_arguments(draft, account, attachments, identity)):
        return await fail("Not sent: the message changed after it was approved. Check it and schedule it again.", "changed")

    def over_limit() -> bool:
        from datetime import timedelta

        with open_mail_store(context) as store:
            # This message is already counted (status sending), so the limit is "more than".
            return store.count_sends_since(account.id, now - timedelta(hours=1)) > mail_settings.send_per_hour

    if await asyncio.to_thread(over_limit):
        await asyncio.to_thread(monitor.bump, context, "send_rate_limited")
        return await fail(f"Not sent: this mailbox already sent {mail_settings.send_per_hour} messages in the last hour. Schedule it again.", "rate_limited")
    result = await deliver(context, draft, account, attachments, identity)
    await asyncio.to_thread(
        _audit, context, "mail.send.scheduled", draft_id, "success" if result.status == "succeeded" else "failure",
        {"recipients": len(draft.to) + len(draft.cc) + len(draft.bcc), "attachments": len(attachments)},
    )
    return "sent" if result.status == "succeeded" else "failed"


async def run_once(now: datetime | None = None, limit: int = BATCH) -> dict[str, int]:
    """Handle every piece of due work once; one message's failure never stops the others."""
    now = now or utc_now()
    counts: dict[str, int] = {}
    for tenant_id, workspace_id, owner, draft_id in await asyncio.to_thread(due_work, now, limit):
        try:
            outcome = await handle_due(tenant_id, workspace_id, owner, draft_id, now)
        except Exception:  # noqa: BLE001 - logged with ids only; the next pass looks again
            logger.exception("mail.scheduled_send_failed", extra={"anum_mail": {"draft_id": draft_id}})
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


class ScheduledSendRunner:
    """Every ``ANUM_MAILSERVER_SCHEDULE_POLL_SECONDS``: send what is due, safe on every replica."""

    def __init__(self, poll_interval: float) -> None:
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None

    def start(self) -> None:
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="anum-mail-schedule")

    async def stop(self, timeout: float = 60.0) -> None:
        # A send in progress finishes (its lease covers it); the loop does not start another.
        task, self._task = self._task, None
        if self._stop is not None:
            self._stop.set()
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except Exception:
                logger.exception("Mail schedule runner stopped with an error")

    async def _run(self) -> None:
        stop = self._stop
        if stop is None:
            raise RuntimeError("Mail schedule runner started without a stop event")
        while not stop.is_set():
            try:
                await run_once()
            except Exception:
                logger.exception("Mail schedule pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass


def build_schedule_runner() -> ScheduledSendRunner | None:
    if not mail_settings.enabled or mail_settings.schedule_poll_seconds <= 0:
        return None
    return ScheduledSendRunner(mail_settings.schedule_poll_seconds)


def parse_send_at(value: datetime, now: datetime | None = None) -> datetime:
    """A requested send time: with a zone, at least a minute ahead, at most the configured days."""
    from datetime import timedelta

    if value.tzinfo is None:
        raise OutgoingRefused(422, "Give the send time with its time zone.")
    now = now or utc_now()
    when = value.astimezone(timezone.utc).replace(microsecond=0)
    if when < now + timedelta(minutes=1):
        raise OutgoingRefused(422, "Pick a send time at least a minute from now, or send it now.")
    if when > now + timedelta(days=mail_settings.schedule_max_days):
        raise OutgoingRefused(422, f"A message can be scheduled at most {mail_settings.schedule_max_days} days ahead.")
    return when
