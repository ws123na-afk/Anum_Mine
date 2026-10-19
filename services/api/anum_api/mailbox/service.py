"""Email Center logic shared by the routes and the runtime tools (docs/email.md#email-center).

Two tools act on mail only after an approval bound to the exact call (payload hash):

* ``send_mail`` (high risk): submits the approved message once over SMTP, then saves the
  same bytes to the Sent folder. The draft is claimed atomically (``pending_approval`` to
  ``sending``), so two approvals or a retried run can never send it twice; a send whose
  connection broke after the message data started is marked ``unknown`` and never retried.
  A message with a ``send_at`` in the future is only marked ``scheduled`` by the approval;
  the scheduler (:mod:`.outgoing`) sends it at that time, after checking it still matches
  the approved call. The approved call covers the attachments' names, sizes and SHA-256.
* ``delete_mail`` (high risk): deletes messages for good from Trash or Junk.

Mail is untrusted data: nothing in a message can choose a tool, an address or a folder.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, TypeVar

from ..agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from ..schemas import RiskLevel, TenantContext, utc_now
from ..settings import settings
from .config import MailServerSettings, mail_settings
from .mime import Outgoing, OutgoingFile, build_message, valid_address
from .server import Folder, ImapSmtpServer, MailServer, MailServerError
from . import monitor
from .store import (
    NOT_APPROVED_IN_TIME,
    SEND_LEASE,
    AccountStatus,
    DraftStatus,
    MailAccount,
    MailAttachment,
    MailDraft,
    MailIdentity,
    allows,
    open_mail_store,
)

logger = logging.getLogger("anum.mail")

SEND_TOOL = "send_mail"
DELETE_TOOL = "delete_mail"
_PEOPLE = frozenset({"owner", "member"})
T = TypeVar("T")
# An approval that lands this soon after the scheduled time still sends (now); later it does not.
DUE_GRACE = timedelta(minutes=5)

# Tests replace this with a fake server; production builds the IMAP and SMTP client.
server_factory: Callable[[MailServerSettings, str, str], MailServer] = ImapSmtpServer


class MailError(Exception):
    def __init__(self, status: int, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.kind = kind


HTTP_STATUS = {"uncertain": 503, "auth": 409, "offline": 503, "tls": 503, "protocol": 502, "not_found": 404, "too_large": 413, "rejected": 422}


def cfg() -> MailServerSettings:
    return mail_settings


def tenant_allowed(context: TenantContext, config: MailServerSettings | None = None) -> bool:
    config = config or cfg()
    if not config.tenants:
        # Without a list only local development and tests may use it (production refuses to start).
        return settings.environment in {"local", "test"}
    return context.tenant_id in config.tenants


def domain_allowed(address: str, config: MailServerSettings | None = None) -> bool:
    domain = address.rsplit("@", 1)[-1].strip().lower()
    return domain in (config or cfg()).domains


def require_available(context: TenantContext) -> None:
    config = cfg()
    if not config.enabled:
        raise MailError(409, "The Email Center is turned off on this server (ANUM_MAILSERVER_ENABLED).", "disabled")
    if not config.configured:
        raise MailError(409, "The mail server is not set up on this server (ANUM_MAILSERVER_IMAP_HOST and SMTP_HOST).", "not_configured")
    if not tenant_allowed(context, config):
        raise MailError(403, "This organisation is not allowed to use the mail server. The operator lists allowed organisations in ANUM_MAILSERVER_ALLOWED_TENANTS.", "tenant")


def _cipher():  # type: ignore[no-untyped-def]
    from ..secret_box import secret_cipher

    return secret_cipher()


def encrypt_password(password: str) -> str:
    return _cipher().encrypt(password)


def server_for(account: MailAccount) -> MailServer:
    from ..secret_box import SecretDecryptionError

    try:
        password = _cipher().decrypt(account.password_enc)
    except SecretDecryptionError:
        raise MailError(409, "ANUM cannot read the saved password (the secrets key changed). Enter the password again in Mailbox settings.", "auth") from None
    return server_factory(cfg(), account.address, password)


def _status_for(error: MailServerError) -> AccountStatus:
    if error.kind == "auth":
        return AccountStatus.AUTH_FAILED
    if error.kind in {"offline", "tls"}:
        return AccountStatus.OFFLINE
    return AccountStatus.ERROR


def _record_health(context: TenantContext, account: MailAccount, status: AccountStatus, error: str | None) -> MailAccount:
    if account.owner_user_id != context.user_id:
        # A shared mailbox: only its owner may change the row (the row policy says the same).
        return account
    now = utc_now()
    fresh = account.last_checked_at is not None and now - account.last_checked_at < timedelta(seconds=60)
    if account.status == status and account.last_error == error and fresh:
        return account
    with open_mail_store(context) as store:
        stored = store.get_account(account.id)
        if stored is None:
            return account
        stored.status, stored.last_error, stored.last_checked_at, stored.updated_at = status, error, now, now
        store.save_account(stored)
        return stored


async def on_server(context: TenantContext, account: MailAccount, job: Callable[[MailServer], T]) -> T:
    """Run ``job`` against the mailbox's server in a thread; keep the mailbox's health current."""
    server = server_for(account)
    try:
        result = await asyncio.to_thread(job, server)
    except MailServerError as exc:
        if exc.kind in {"auth", "offline", "tls"}:
            _record_health(context, account, _status_for(exc), exc.message)
            monitor.bump(context, "auth_failed" if exc.kind == "auth" else "server_offline")
        status = HTTP_STATUS.get(exc.kind, 502)
        message = "The mail server refused the saved password. Enter it again in Mailbox settings." if exc.kind == "auth" else exc.message
        raise MailError(status, message, exc.kind) from None
    if account.status != AccountStatus.CONNECTED or account.last_checked_at is None:
        _record_health(context, account, AccountStatus.CONNECTED, None)
    return result


def folder_by_role(folders: list[Folder], role: str) -> Folder | None:
    return next((folder for folder in folders if folder.role == role and folder.selectable), None)


# -- connection attempts (a password oracle otherwise) ----------------------------------------

_attempts: dict[tuple[str, str], deque[float]] = {}
_attempts_lock = Lock()


def take_connect_attempt(context: TenantContext) -> bool:
    """At most ``connect_attempts_per_hour`` login checks per person (per API replica)."""
    now = utc_now().timestamp()
    key = (context.tenant_id, context.user_id)
    with _attempts_lock:
        window = _attempts.setdefault(key, deque())
        while window and now - window[0] > 3600:
            window.popleft()
        if len(window) >= cfg().connect_attempts_per_hour:
            return False
        window.append(now)
        return True


def reset_connect_attempts() -> None:
    with _attempts_lock:
        _attempts.clear()


# -- validation -------------------------------------------------------------------------------


def clean_addresses(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        address = value.strip()
        if not address:
            continue
        if not valid_address(address):
            raise MailError(422, f"'{address[:80]}' is not an email address.", "address")
        if address.lower() not in {item.lower() for item in seen}:
            seen.append(address)
    return seen


def check_ready_to_send(draft: MailDraft) -> None:
    recipients = len(draft.to) + len(draft.cc) + len(draft.bcc)
    if not draft.to:
        raise MailError(422, "Add at least one recipient in To.", "recipients")
    if recipients > cfg().max_recipients:
        raise MailError(422, f"A message can go to at most {cfg().max_recipients} recipients.", "recipients")
    if not draft.subject.strip() and not draft.body.strip():
        raise MailError(422, "The message is empty. Add a subject or some text.", "empty")


SIGNATURE_MARK = "\n\n-- \n"


def with_signature(body: str, signature: str) -> str:
    """``body`` with ``signature`` after the person's text and before any quoted original."""
    if not signature.strip():
        return body
    block = SIGNATURE_MARK + signature.strip("\n")
    # A reply or forward starts with an empty line above the quoted original: sign above it.
    return block + body if body.startswith("\n\n") else body + block


def swap_signature(body: str, old: str, new: str) -> str:
    """Replace the old identity's signature block with the new one (or add the new one)."""
    if old.strip():
        block = SIGNATURE_MARK + old.strip("\n")
        if block in body:
            replacement = SIGNATURE_MARK + new.strip("\n") if new.strip() else ""
            return body.replace(block, replacement, 1)
    return with_signature(body, new) if new.strip() and (SIGNATURE_MARK + new.strip("\n")) not in body else body


def sender_of(account: MailAccount, identity: MailIdentity | None) -> tuple[str, str]:
    """The From address and name a message goes out with."""
    if identity is not None:
        return identity.address, identity.display_name or account.display_name or ""
    return account.address, account.display_name or ""


def utc_iso(value: datetime | None) -> str | None:
    """One spelling of a time in UTC whole seconds, whatever the database session's zone."""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat() if value else None


def attachment_arguments(attachments: list[MailAttachment]) -> list[dict[str, Any]]:
    return [
        {"id": a.id, "filename": a.filename, "content_type": a.content_type, "size": a.size_bytes, "sha256": a.sha256}
        for a in attachments
    ]


def send_arguments(
    draft: MailDraft, account: MailAccount, attachments: list[MailAttachment] | None = None, identity: MailIdentity | None = None
) -> dict[str, Any]:
    """Exactly what the approval shows and binds (the payload hash covers all of it)."""
    address, name = sender_of(account, identity)
    return {
        "draft_id": draft.id,
        "account_id": account.id,
        "owner_user_id": draft.owner_user_id,
        "mailbox": account.address,
        "identity_id": identity.id if identity else None,
        "from_name": name,
        "from": address,
        "to": list(draft.to),
        "cc": list(draft.cc),
        "bcc": list(draft.bcc),
        "subject": draft.subject,
        "body": draft.body,
        "message_id": draft.message_id,
        "in_reply_to": draft.in_reply_to,
        "send_at": utc_iso(draft.send_at),
        "attachments": attachment_arguments(attachments or []),
    }


def content_hash(arguments: dict[str, Any]) -> str:
    """SHA-256 of the canonical send call, kept on a scheduled message and checked when it goes."""
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _owner_context(call: ToolCall, context: TenantContext) -> TenantContext:
    """Mailboxes are private to their owner: the approved call names whose it is."""
    owner = str(call.arguments.get("owner_user_id") or "")
    return context.model_copy(update={"user_id": owner}) if owner else context


def _failed(summary: str, **output: Any) -> ToolResult:
    return ToolResult(status="failed", summary=summary, output=output)


async def send_mail(call: ToolCall, context: TenantContext) -> ToolResult:
    """Send exactly the approved message, once, or schedule it. Runs only after approval (high risk)."""
    args = call.arguments
    owner = _owner_context(call, context)
    try:
        require_available(owner)
    except MailError as exc:
        return _failed(f"Not sent: {exc.message}")
    draft_id = str(args.get("draft_id") or "")
    identity: MailIdentity | None = None
    with open_mail_store(owner) as store:
        draft = store.get_draft(draft_id)
        found = store.get_accessible_account(str(args.get("account_id") or "")) if draft else None
        attachments = store.list_attachments(draft.id) if draft else []
        if draft is not None and draft.identity_id:
            identity = store.get_identity(draft.identity_id)
    account = found[0] if found else None
    if draft is None or account is None or draft.account_id != account.id:
        return _failed("Not sent: the message or its mailbox is gone, or the mailbox is no longer shared with the writer.")
    if not allows(found[1] if found else None, "send"):
        return _failed("Not sent: the mailbox's owner no longer lets the writer send from it.", draft_id=draft.id)
    if draft.identity_id and (identity is None or identity.account_id != account.id):
        return _failed("Not sent: the sender identity was removed. Pick a sender and send again.", draft_id=draft.id)
    if draft.status == DraftStatus.SENT:
        return ToolResult(status="succeeded", summary="This message was already sent.", output={"draft_id": draft.id})
    if draft.status == DraftStatus.SCHEDULED:
        return ToolResult(status="succeeded", summary="This message is already scheduled.", output={"draft_id": draft.id, "send_at": utc_iso(draft.send_at)})
    if draft.status in {DraftStatus.SENDING, DraftStatus.UNKNOWN}:
        return _failed("Not sent again: an earlier attempt may have reached the server. Check Sent.", draft_id=draft.id)
    if draft.status != DraftStatus.PENDING_APPROVAL:
        return _failed("Not sent: the message is no longer waiting for approval.", draft_id=draft.id)
    expected = send_arguments(draft, account, attachments, identity)
    if expected != {key: args.get(key) for key in expected}:
        return _failed("Not sent: the message changed after it was sent for approval.", draft_id=draft.id)
    now = utc_now()
    if draft.send_at is not None and draft.send_at > now:
        with open_mail_store(owner) as store:
            if not store.mark_scheduled(draft.id, content_hash(expected)):
                return _failed("Not scheduled: the message is no longer waiting for approval.", draft_id=draft.id)
        when = utc_iso(draft.send_at)
        return ToolResult(
            status="succeeded",
            summary=f"Scheduled for {when} from {expected['from']} to {', '.join(draft.to)}: {draft.subject or '(no subject)'}",
            output={"draft_id": draft.id, "message_id": draft.message_id, "send_at": when, "scheduled": True},
            audit_metadata={"tool": SEND_TOOL, "draft_id": draft.id, "scheduled": True, "attachments": len(attachments)},
        )
    if draft.send_at is not None and now - draft.send_at > DUE_GRACE:
        with open_mail_store(owner) as store:
            store.expire_unapproved(draft.id, now - DUE_GRACE)
        return _failed(NOT_APPROVED_IN_TIME, draft_id=draft.id)
    since = now - timedelta(hours=1)
    with open_mail_store(owner) as store:
        if store.count_sends_since(account.id, since) >= cfg().send_per_hour:
            monitor.bump(owner, "send_rate_limited")
            return _failed(f"Not sent: this mailbox already sent {cfg().send_per_hour} messages in the last hour. Try again later.", draft_id=draft.id)
        if not store.claim_send(draft.id, now + SEND_LEASE):
            return _failed("Not sent: another attempt is already sending this message.", draft_id=draft.id)
    return await deliver(owner, draft, account, attachments, identity)


def record_send(owner: TenantContext, draft_id: str, status: DraftStatus, error: str | None, saved: bool | None = None) -> None:
    """The outcome of a claimed send; the claim's lease ends with it."""
    with open_mail_store(owner) as store:
        stored = store.get_draft(draft_id)
        if stored is None:
            return
        stored.status, stored.error, stored.send_lease_until = status, error, None
        if saved is not None:
            stored.saved_to_sent = saved
        if status == DraftStatus.SENT and stored.sent_at is None:
            stored.sent_at = utc_now()
        stored.updated_at = utc_now()
        store.save_draft(stored)


async def deliver(
    owner: TenantContext, draft: MailDraft, account: MailAccount, attachments: list[MailAttachment], identity: MailIdentity | None = None
) -> ToolResult:
    """Submit a draft this caller has claimed (status ``sending``), then save it to Sent.

    ``identity`` is the sender identity the approval named (None: the mailbox address).
    """
    from ..demo import send_refusal

    refusal = send_refusal(owner)  # nothing leaves a demo workspace (docs/demo-workspace.md)
    if refusal:
        record_send(owner, draft.id, DraftStatus.FAILED, refusal)
        return _failed(refusal, draft_id=draft.id)
    from_address, from_name = sender_of(account, identity)
    from .outgoing import AttachmentGone, read_attachment

    try:
        files = tuple([OutgoingFile(a.filename, a.content_type, await asyncio.to_thread(read_attachment, a)) for a in attachments])
    except AttachmentGone as exc:
        record_send(owner, draft.id, DraftStatus.FAILED, exc.message)
        return _failed(f"Not sent: {exc.message}", draft_id=draft.id)
    outgoing = Outgoing(
        sender_name=from_name, sender_address=from_address, to=draft.to, cc=draft.cc, bcc=draft.bcc,
        subject=draft.subject, body=draft.body, message_id=draft.message_id, in_reply_to=draft.in_reply_to, references=draft.references,
        attachments=files,
    )
    raw = build_message(outgoing)
    recipients = [*draft.to, *draft.cc, *draft.bcc]

    try:
        report = await on_server(owner, account, lambda server: server.send(from_address, recipients, raw))
    except MailError as exc:
        if exc.kind == "uncertain":
            record_send(owner, draft.id, DraftStatus.UNKNOWN, exc.message[:500])
            monitor.bump(owner, "send_unknown")
            return _failed(exc.message, draft_id=draft.id)
        record_send(owner, draft.id, DraftStatus.FAILED, exc.message[:500])
        monitor.bump(owner, "send_failed")
        return _failed(f"Not sent: {exc.message}", draft_id=draft.id)
    record_send(owner, draft.id, DraftStatus.SENT, None)
    monitor.bump(owner, "sent")

    def save_copy(server: MailServer) -> bool:
        sent = folder_by_role(server.folders(), "sent")
        if sent is None:
            return False
        if server.find_message_id(sent.id, draft.message_id) is None:
            server.append(sent.id, raw, seen=True)
        if draft.kind in {"reply", "reply_all"} and draft.source_folder and draft.source_uid:
            try:
                server.set_flag(draft.source_folder, [draft.source_uid], "answered", True)
            except MailServerError:
                pass
        return True

    saved = False
    try:
        saved = await on_server(owner, account, save_copy)
    except MailError as exc:
        logger.warning("mail.sent_copy_failed", extra={"anum_kind": exc.kind})
    record_send(owner, draft.id, DraftStatus.SENT, None if saved else "Sent, but the copy could not be saved to the Sent folder.", saved)
    refused = sorted(report.refused)
    summary = f"Sent from {from_address} to {', '.join(draft.to)}: {draft.subject or '(no subject)'}"
    if refused:
        summary += f". The server refused {len(refused)} recipient(s)."
    return ToolResult(
        status="succeeded",
        summary=summary,
        output={"draft_id": draft.id, "message_id": draft.message_id, "saved_to_sent": saved, "refused": refused},
        audit_metadata={
            "tool": SEND_TOOL, "draft_id": draft.id, "recipients": len(recipients), "body_chars": len(draft.body),
            "attachments": len(files), "saved_to_sent": saved,
        },
    )


async def delete_mail(call: ToolCall, context: TenantContext) -> ToolResult:
    """Delete messages for good from Trash or Junk. Runs only after approval (high risk)."""
    args = call.arguments
    owner = _owner_context(call, context)
    try:
        require_available(owner)
    except MailError as exc:
        return _failed(f"Not deleted: {exc.message}")
    with open_mail_store(owner) as store:
        account = store.get_account(str(args.get("account_id") or ""))
    if account is None:
        return _failed("Not deleted: the mailbox is gone.")
    folder = str(args.get("folder") or "")
    uids = [int(uid) for uid in args.get("uids") or [] if isinstance(uid, int) or str(uid).isdigit()]
    if not uids:
        return _failed("Not deleted: no messages were named.")

    def job(server: MailServer) -> None:
        found = next((item for item in server.folders() if item.id == folder), None)
        if found is None or found.role not in {"trash", "junk"}:
            raise MailServerError("rejected", "Messages are deleted for good only from Trash or Junk.")
        server.delete(folder, uids)

    try:
        await on_server(owner, account, job)
    except MailError as exc:
        return _failed(f"Not deleted: {exc.message}")
    return ToolResult(
        status="succeeded",
        summary=f"Deleted {len(uids)} message(s) for good from {folder} in {account.address}.",
        output={"account_id": account.id, "folder": folder, "deleted": len(uids)},
        audit_metadata={"tool": DELETE_TOOL, "account_id": account.id, "deleted": len(uids)},
    )


TOOL_DEFINITIONS: list[tuple[ToolDefinition, Any]] = [
    (
        ToolDefinition(
            name=SEND_TOOL,
            description="Send an approved message from the person's own mailbox on the workspace mail server.",
            risk_level=RiskLevel.HIGH,
            required_roles=_PEOPLE,
            timeout_seconds=90,
            category="email",
            action_kind="send",
            always_ask=True,
        ),
        send_mail,
    ),
    (
        ToolDefinition(
            name=DELETE_TOOL,
            description="Delete messages for good from Trash or Junk in the person's own mailbox.",
            risk_level=RiskLevel.HIGH,
            required_roles=_PEOPLE,
            timeout_seconds=60,
            category="email",
            action_kind="delete",
            always_ask=True,
        ),
        delete_mail,
    ),
]


def register_mail_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the Email Center tools to a runtime's registry (the API's and the Temporal worker's)."""
    from .ai import AI_TOOL_DEFINITIONS
    from .automation_tools import AUTOMATION_TOOL_DEFINITIONS

    for definition, handler in [*TOOL_DEFINITIONS, *AI_TOOL_DEFINITIONS, *AUTOMATION_TOOL_DEFINITIONS]:
        if definition.name not in registry.names:
            registry.register(definition, handler)
    return registry


def mail_tool_registry() -> ToolRegistry:
    return register_mail_tools(ToolRegistry())
