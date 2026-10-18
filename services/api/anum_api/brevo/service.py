"""Brevo rules shared by the routes and the tools (docs/brevo.md).

* The API key is stored only as a Fernet ciphertext (``secret_box``) in
  ``brevo_connections.api_key_ciphertext`` and decrypted only to build a
  :class:`~anum_api.brevo.client.BrevoClient`; views show the last four characters.
* A draft's ``content_hash`` binds everything that reaches the recipients: name, subject,
  preview text, sender, HTML and lists. The send approval carries it, and the send tool
  refuses to run when the draft no longer hashes to it.
* Database work runs in short units of work off the event loop (``asyncio.to_thread``)
  and never stays open across a Brevo call (docs/realtime.md#event-loop).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, SecretStr

from ..agent_tools import ToolCall, ToolRegistry
from ..audit import AuditRecord
from ..dependencies import _unit_of_work
from ..events import CanonicalEventName, create_event
from ..mailbox.html_safe import html_to_text, sanitize_html
from ..model_gateway import MockModelGateway
from ..repository import AnumRepository
from ..runtime import AgentRuntime
from ..schemas import (
    AgentRunStep,
    Approval,
    ApprovalStatus,
    RunPhase,
    Task,
    TaskStatus,
    TenantContext,
    new_id,
    utc_now,
)
from ..secret_box import SecretDecryptionError, secret_cipher
from .client import HOST, BrevoClient, BrevoError
from .models import BrevoConnection, CampaignDraft, ConnectionStatus, DraftStatus, SendError
from .store import open_brevo_store

logger = logging.getLogger("anum.brevo")

SEND_TOOL = "brevo.send_campaign"
DRAFT_TOOL = "brevo.draft_campaign"
LISTS_TOOL = "brevo.list_lists"
CAMPAIGNS_TOOL = "brevo.list_campaigns"
REPORT_TOOL = "brevo.campaign_report"
# A send claim older than this with no answer is shown as interrupted (it may have gone out).
STALE_SEND_MINUTES = 15
PREVIEW_TEXT_CHARS = 1500

T = TypeVar("T")


def http_error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def in_thread(fn: Callable[[], T]) -> asyncio.Future[T]:
    """Run blocking database work off the event loop (docs/realtime.md#event-loop)."""
    return asyncio.to_thread(fn)  # type: ignore[return-value]


# -- secrets ------------------------------------------------------------------------------------


def encrypt_key(value: str) -> str:
    return secret_cipher().encrypt(value)


def client_for(connection: BrevoConnection) -> BrevoClient:
    try:
        key = secret_cipher().decrypt(connection.api_key_ciphertext)
    except SecretDecryptionError as exc:
        raise http_error(409, "The saved Brevo key cannot be read with the current secrets key. Paste the API key again.") from exc
    return BrevoClient(SecretStr(key))


def last4(value: str) -> str:
    return value.strip()[-4:]


# -- audit --------------------------------------------------------------------------------------


def audit(context: TenantContext, action: str, target: str, *, repository: AnumRepository | None = None,
          outcome: str = "success", metadata: dict[str, Any] | None = None) -> None:
    """One audit record without the key, the HTML or any recipient (ids and counts only)."""
    record = AuditRecord(
        id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, actor=context.user_id, action=action,
        target=target, outcome=outcome, correlation_id=target, created_at=utc_now(), metadata=metadata or {},
    )
    if repository is not None:
        repository.record_audit(record)
        return
    try:
        with _unit_of_work(context, require_membership=False) as detached:
            detached.record_audit(record)
    except Exception:  # an audit failure must not undo a send that already happened
        logger.warning("brevo_audit_failed", extra={"anum_brevo": {"event": "audit_failed", "action": action}})


# -- views --------------------------------------------------------------------------------------


class ConnectionView(BaseModel):
    status: ConnectionStatus
    key_last4: str
    account_email: str
    account_name: str
    company: str
    plan: str
    last_checked_at: str | None
    last_error: str | None
    connected_by: str
    updated_at: str


class StatusView(BaseModel):
    connected: bool
    connection: ConnectionView | None
    can_read: bool
    can_write: bool
    can_manage: bool
    host: str = HOST


def connection_view(connection: BrevoConnection | None) -> ConnectionView | None:
    if connection is None:
        return None
    return ConnectionView(
        status=connection.status, key_last4=connection.key_last4, account_email=connection.account_email,
        account_name=connection.account_name, company=connection.company, plan=connection.plan,
        last_checked_at=connection.last_checked_at.isoformat() if connection.last_checked_at else None,
        last_error=connection.last_error, connected_by=connection.connected_by, updated_at=connection.updated_at.isoformat(),
    )


class DraftView(BaseModel):
    id: str
    name: str
    subject: str
    preview_text: str
    sender_id: int
    sender_name: str
    sender_email: str
    list_ids: list[int]
    list_names: list[str]
    status: DraftStatus
    scheduled_at: str | None
    content_hash: str
    approved_hash: str | None
    brevo_campaign_id: int | None
    last_error: SendError | None
    test_sent_at: str | None
    sent_at: str | None
    source: str
    created_by: str
    created_at: str
    updated_at: str
    html_chars: int
    approval: Approval | None = None
    approval_state: str = "none"
    # Only on the detail read: the HTML (as written) and its sanitized preview.
    html: str | None = None
    preview_html: str | None = None
    remote_images: int = 0


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def draft_view(draft: CampaignDraft, approval: Approval | None = None, *, detail: bool = False) -> DraftView:
    state = "none"
    if approval is not None:
        state = {ApprovalStatus.PENDING: "pending", ApprovalStatus.APPROVED: "approved", ApprovalStatus.REJECTED: "rejected",
                 ApprovalStatus.EXPIRED: "expired"}.get(approval.status, "none")
    view = DraftView(
        id=draft.id, name=draft.name, subject=draft.subject, preview_text=draft.preview_text, sender_id=draft.sender_id,
        sender_name=draft.sender_name, sender_email=draft.sender_email, list_ids=draft.list_ids, list_names=draft.list_names,
        status=draft.status, scheduled_at=_iso(draft.scheduled_at), content_hash=draft.content_hash, approved_hash=draft.approved_hash,
        brevo_campaign_id=draft.brevo_campaign_id, last_error=draft.last_error, test_sent_at=_iso(draft.test_sent_at),
        sent_at=_iso(draft.sent_at), source=draft.source, created_by=draft.created_by, created_at=draft.created_at.isoformat(),
        updated_at=draft.updated_at.isoformat(), html_chars=len(draft.html),
        approval=approval if state == "pending" else None, approval_state=state,
    )
    if detail:
        safe = sanitize_html(draft.html)
        view.html = draft.html
        view.preview_html = safe.html
        view.remote_images = safe.remote_images_blocked
    return view


def reconcile(draft: CampaignDraft, approval: Approval | None) -> bool:
    """A draft whose approval was rejected or lapsed goes back to draft with the reason; a
    send left claimed for too long is shown as interrupted. True when it changed."""
    now = utc_now()
    if draft.status == DraftStatus.PENDING_APPROVAL and approval is not None:
        lapsed = approval.status == ApprovalStatus.EXPIRED or (
            approval.status == ApprovalStatus.PENDING and approval.expires_at is not None and approval.expires_at <= now
        )
        if approval.status == ApprovalStatus.REJECTED or lapsed:
            draft.status = DraftStatus.DRAFT
            draft.last_error = SendError(
                code="approval_rejected" if approval.status == ApprovalStatus.REJECTED else "approval_expired",
                message=(f"Rejected by {approval.decided_by or 'an approver'}" + (f": {approval.decision_reason}" if approval.decision_reason else "."))
                if approval.status == ApprovalStatus.REJECTED else "The approval request lapsed before anyone decided.",
                fix="Edit the campaign if needed and ask to send it again.",
            )
            draft.updated_at = now
            return True
    if draft.status == DraftStatus.SENDING and (now - draft.updated_at).total_seconds() > STALE_SEND_MINUTES * 60:
        draft.status = DraftStatus.UNKNOWN
        draft.last_error = SendError(
            code="interrupted", message="ANUM was interrupted while sending to Brevo; the campaign may have gone out.",
            fix="Open Email campaigns in Brevo and check this campaign before doing anything else.",
        )
        draft.updated_at = now
        return True
    return False


# -- content ------------------------------------------------------------------------------------


def content_hash(*, name: str, subject: str, preview_text: str, sender_id: int, sender_email: str, html: str, list_ids: list[int]) -> str:
    canonical = json.dumps(
        {"name": name, "subject": subject, "preview_text": preview_text, "sender_id": int(sender_id), "sender_email": sender_email.lower(),
         "html": html, "list_ids": sorted(int(item) for item in list_ids)},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def draft_hash(draft: CampaignDraft) -> str:
    return content_hash(name=draft.name, subject=draft.subject, preview_text=draft.preview_text, sender_id=draft.sender_id,
                        sender_email=draft.sender_email, html=draft.html, list_ids=draft.list_ids)


def campaign_tag(draft_id: str) -> str:
    """The tag on the Brevo campaign: finds it again after an interrupted send."""
    return f"anum:{draft_id}"[:120]


def plain_text(html: str, limit: int = PREVIEW_TEXT_CHARS) -> str:
    text = " ".join(html_to_text(sanitize_html(html).html).split())
    return text if len(text) <= limit else text[:limit] + " [...]"


def send_arguments(draft: CampaignDraft, *, recipients: int, list_names: list[str], account_email: str) -> dict[str, Any]:
    """What the approver sees and the payload hash binds: who sends what to whom, and when."""
    return {
        "draft_id": draft.id,
        "campaign": draft.name,
        "subject": draft.subject,
        "preview_text": draft.preview_text,
        "sender": f"{draft.sender_name} <{draft.sender_email}>",
        "sender_id": draft.sender_id,
        "sender_email": draft.sender_email,
        "list_ids": list(draft.list_ids),
        "lists": list_names,
        "recipients": recipients,
        "scheduled_at": draft.scheduled_at.isoformat() if draft.scheduled_at else "as soon as approved",
        "brevo_account": account_email,
        "text": plain_text(draft.html),
        "content_hash": draft.content_hash,
        "written_by": "assistant" if draft.source == "assistant" else "person",
    }


# -- approvals ----------------------------------------------------------------------------------


def request_approval(repository: AnumRepository, context: TenantContext, arguments: dict[str, Any], title: str,
                     registry: Callable[[ToolRegistry], ToolRegistry]) -> tuple[Task, Approval]:
    """A paused ``brevo.send_campaign`` call waiting for an owner (as marketing and business do)."""
    call = ToolCall(name=SEND_TOOL, arguments=arguments)
    now = utc_now()
    task = Task(
        id=new_id("task"), title=title[:160], prompt=f"Send the Brevo email campaign {title}.", status=TaskStatus.CREATED,
        tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=now, updated_at=now, created_by=context.user_id,
    )
    repository.create_task(task)
    repository.record_event(create_event(CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title}, correlation_id=task.id, created_at=now).event)
    runtime = AgentRuntime(MockModelGateway(), repository, tools=registry(ToolRegistry()))
    run = runtime.new_run(task)
    decision = runtime.evaluate(call, context)
    step = AgentRunStep(
        id=new_id("step"), type="tool_proposal", summary=f"Proposed tool call: {SEND_TOOL}", created_at=utc_now(),
        metadata={"tool": SEND_TOOL, "risk_level": decision.risk_level.value, "policy_outcome": decision.outcome.value,
                  "governance_rules": list(decision.governance_rules), "draft_id": arguments["draft_id"]},
    )
    run.steps.append(step)
    run.checkpoint.phase = RunPhase.TOOL_READY
    run.checkpoint.version += 1
    run.checkpoint.tool_call = call.model_dump(mode="json")
    run.checkpoint.last_step_id = step.id
    task.status = run.status = TaskStatus.RUNNING
    repository.save_task(task)
    repository.save_run(run)
    # The send always asks; a governance deny (or a missing role) fails the run instead.
    if runtime.begin_execution(task, run, context) is not None:  # pragma: no cover - always_ask never runs unapproved
        raise RuntimeError("the Brevo send was not paused for approval")
    repository.save_task(task)
    repository.save_run(run)
    if run.checkpoint.phase != RunPhase.WAITING_APPROVAL or not run.checkpoint.approval_id:
        reason = run.steps[-1].summary if run.steps else "Blocked by policy."
        raise http_error(403, f"Sending is blocked: {reason}")
    approval = repository.get_approval(run.checkpoint.approval_id, context)
    if approval is None:  # pragma: no cover - saved just above
        raise http_error(500, "Approval was not stored")
    return task, approval


def retire_approval(repository: AnumRepository, context: TenantContext, task_id: str | None, approval_id: str | None) -> None:
    from ..marketing.service import retire_approval as retire

    retire(repository, context, task_id, approval_id)


# -- connection ---------------------------------------------------------------------------------


def load_connection(context: TenantContext) -> BrevoConnection | None:
    with open_brevo_store(context) as store:
        return store.get_connection()


def require_connection(context: TenantContext) -> BrevoConnection:
    connection = load_connection(context)
    if connection is None:
        raise http_error(409, "Brevo is not connected. An owner pastes the Brevo API key under Email campaigns, Connection.")
    return connection


def mark_connection(context: TenantContext, error: BrevoError | None, *, account: Any = None) -> BrevoConnection | None:
    """Record a check's outcome: a refused key needs a new one, other errors are kept."""
    with open_brevo_store(context) as store:
        connection = store.get_connection(for_update=True)
        if connection is None:
            return None
        now = utc_now()
        connection.last_checked_at = now
        connection.updated_at = now
        if error is None:
            connection.status = ConnectionStatus.CONNECTED
            connection.last_error = None
            if account is not None:
                connection.account_email = account.email
                connection.account_name = " ".join(part for part in (account.first_name, account.last_name) if part)
                connection.company = account.company
                connection.plan = account.plan
        else:
            connection.status = ConnectionStatus.NEEDS_KEY if error.auth_failed else ConnectionStatus.ERROR
            connection.last_error = error.message
        store.save_connection(connection)
        return connection


def brevo_failure(error: BrevoError) -> HTTPException:
    """A Brevo refusal as an API error: 502 when Brevo failed, 409 when the key needs work."""
    if error.auth_failed:
        return http_error(409, error.message)
    if error.status == 404:
        return http_error(404, error.message)
    if error.status in {400, 402}:
        return http_error(422, error.message)
    return http_error(502, error.message)
