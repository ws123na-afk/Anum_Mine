"""Email Center logic shared by the routes and the runtime tools (docs/email.md#email-center).

Two tools act on mail only after an approval bound to the exact call (payload hash):

* ``send_mail`` (high risk): submits the approved message once over SMTP, then saves the
  same bytes to the Sent folder. The draft is claimed atomically (``pending_approval`` to
  ``sending``), so two approvals or a retried run can never send it twice; a send whose
  connection broke after the message data started is marked ``unknown`` and never retried.
* ``delete_mail`` (high risk): deletes messages for good from Trash or Junk.

Mail is untrusted data: nothing in a message can choose a tool, an address or a folder.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from datetime import timedelta
from threading import Lock
from typing import Any, TypeVar

from ..agent_tools import ToolCall, ToolDefinition, ToolRegistry, ToolResult
from ..schemas import RiskLevel, TenantContext, utc_now
from ..settings import settings
from .config import MailServerSettings, mail_settings
from .mime import Outgoing, build_message, valid_address
from .server import Folder, ImapSmtpServer, MailServer, MailServerError
from .store import AccountStatus, DraftStatus, MailAccount, MailDraft, open_mail_store
from . import monitor

logger = logging.getLogger("anum.mail")

SEND_TOOL = "send_mail"
DELETE_TOOL = "delete_mail"
_PEOPLE = frozenset({"owner", "member"})
T = TypeVar("T")

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


def send_arguments(draft: MailDraft, account: MailAccount) -> dict[str, Any]:
    """Exactly what the approval shows and binds (the payload hash covers all of it)."""
    return {
        "draft_id": draft.id,
        "account_id": account.id,
        "owner_user_id": draft.owner_user_id,
        "from": account.address,
        "to": list(draft.to),
        "cc": list(draft.cc),
        "bcc": list(draft.bcc),
        "subject": draft.subject,
        "body": draft.body,
        "message_id": draft.message_id,
        "in_reply_to": draft.in_reply_to,
    }


def _owner_context(call: ToolCall, context: TenantContext) -> TenantContext:
    """Mailboxes are private to their owner: the approved call names whose it is."""
    owner = str(call.arguments.get("owner_user_id") or "")
    return context.model_copy(update={"user_id": owner}) if owner else context


def _failed(summary: str, **output: Any) -> ToolResult:
    return ToolResult(status="failed", summary=summary, output=output)


async def send_mail(call: ToolCall, context: TenantContext) -> ToolResult:
    """Send exactly the approved message, once. Runs only after approval (high risk)."""
    args = call.arguments
    owner = _owner_context(call, context)
    try:
        require_available(owner)
    except MailError as exc:
        return _failed(f"Not sent: {exc.message}")
    draft_id = str(args.get("draft_id") or "")
    with open_mail_store(owner) as store:
        draft = store.get_draft(draft_id)
        account = store.get_account(str(args.get("account_id") or "")) if draft else None
    if draft is None or account is None or draft.account_id != account.id:
        return _failed("Not sent: the message or its mailbox is gone.")
    if draft.status == DraftStatus.SENT:
        return ToolResult(status="succeeded", summary="This message was already sent.", output={"draft_id": draft.id})
    if draft.status in {DraftStatus.SENDING, DraftStatus.UNKNOWN}:
        return _failed("Not sent again: an earlier attempt may have reached the server. Check Sent.", draft_id=draft.id)
    if draft.status != DraftStatus.PENDING_APPROVAL:
        return _failed("Not sent: the message is no longer waiting for approval.", draft_id=draft.id)
    if send_arguments(draft, account) != {key: args.get(key) for key in send_arguments(draft, account)}:
        return _failed("Not sent: the message changed after it was sent for approval.", draft_id=draft.id)
    since = utc_now() - timedelta(hours=1)
    with open_mail_store(owner) as store:
        if store.count_sends_since(account.id, since) >= cfg().send_per_hour:
            monitor.bump(owner, "send_rate_limited")
            return _failed(f"Not sent: this mailbox already sent {cfg().send_per_hour} messages in the last hour. Try again later.", draft_id=draft.id)
        if not store.claim_send(draft.id):
            return _failed("Not sent: another attempt is already sending this message.", draft_id=draft.id)

    outgoing = Outgoing(
        sender_name=account.display_name or "", sender_address=account.address, to=draft.to, cc=draft.cc, bcc=draft.bcc,
        subject=draft.subject, body=draft.body, message_id=draft.message_id, in_reply_to=draft.in_reply_to, references=draft.references,
    )
    raw = build_message(outgoing)
    recipients = [*draft.to, *draft.cc, *draft.bcc]

    def record(status: DraftStatus, error: str | None, saved: bool | None = None) -> None:
        with open_mail_store(owner) as store:
            stored = store.get_draft(draft.id)
            if stored is None:
                return
            stored.status, stored.error = status, error
            if saved is not None:
                stored.saved_to_sent = saved
            if status == DraftStatus.SENT and stored.sent_at is None:
                stored.sent_at = utc_now()
            stored.updated_at = utc_now()
            store.save_draft(stored)

    try:
        report = await on_server(owner, account, lambda server: server.send(account.address, recipients, raw))
    except MailError as exc:
        if exc.kind == "uncertain":
            record(DraftStatus.UNKNOWN, exc.message[:500])
            monitor.bump(owner, "send_unknown")
            return _failed(exc.message, draft_id=draft.id)
        record(DraftStatus.FAILED, exc.message[:500])
        monitor.bump(owner, "send_failed")
        return _failed(f"Not sent: {exc.message}", draft_id=draft.id)
    record(DraftStatus.SENT, None)
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
    record(DraftStatus.SENT, None if saved else "Sent, but the copy could not be saved to the Sent folder.", saved)
    refused = sorted(report.refused)
    summary = f"Sent from {account.address} to {', '.join(draft.to)}: {draft.subject or '(no subject)'}"
    if refused:
        summary += f". The server refused {len(refused)} recipient(s)."
    return ToolResult(
        status="succeeded",
        summary=summary,
        output={"draft_id": draft.id, "message_id": draft.message_id, "saved_to_sent": saved, "refused": refused},
        audit_metadata={"tool": SEND_TOOL, "draft_id": draft.id, "recipients": len(recipients), "body_chars": len(draft.body), "saved_to_sent": saved},
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
        ),
        delete_mail,
    ),
]


def register_mail_tools(registry: ToolRegistry) -> ToolRegistry:
    """Add the Email Center tools to a runtime's registry (the API's and the Temporal worker's)."""
    for definition, handler in TOOL_DEFINITIONS:
        if definition.name not in registry.names:
            registry.register(definition, handler)
    return registry


def mail_tool_registry() -> ToolRegistry:
    return register_mail_tools(ToolRegistry())
