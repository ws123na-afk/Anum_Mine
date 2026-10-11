"""HTTP routes of the unified inbox (docs/email.md). The logic lives in ``email_inbox.py``.

Permissions: reading needs ``email:read`` (owners and members), acting on messages
``email:act`` (owners and members), connecting and disconnecting mailboxes ``email:manage``
(owners). A mailbox connected as *private* is visible only to the person who connected it.

Actions on a mailbox go through the email tools and ``ToolPolicy``: an allowed call runs
at once and is audited; a call that policy pauses (a workspace that approves medium-risk
actions, an organization approval rule) becomes a task waiting for approval; sending is
high risk and always waits for approval with the payload hash.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .agent_tools import ToolCall, ToolPolicyOutcome, ToolResult
from .audit import AuditRecord
from .authorization import Permission
from .dependencies import has_permission, repository_context, require_permission, tenant_context
from .email_inbox import (
    DRAFT_TOOL,
    MARK_READ_TOOL,
    ARCHIVE_TOOL,
    MAX_DRAFT_BODY,
    MAX_INSTRUCTIONS,
    MAX_LISTED,
    SEND_TOOL,
    AccountStatus,
    Category,
    DraftStatus,
    EmailAccount,
    EmailAccountView,
    EmailDraft,
    EmailError,
    EmailMessage,
    MessageFilters,
    OAuthPending,
    Priority,
    Visibility,
    access_token,
    account_view,
    analyze_message,
    can_see,
    email_settings,
    email_tool_registry,
    fetch_attachment,
    fetch_body,
    load_draft,
    load_message,
    open_email_store,
    provider_client,
    provider_enabled_here,
    provider_problems,
    provider_scopes,
    save_provider_draft,
    send_arguments,
    sync_account,
    valid_addresses,
    visible_accounts,
)
from .email_providers import PROVIDER_NAMES, EmailProvider, MailProviderError, pkce_pair
from .events import CanonicalEventName, create_event
from .model_gateway import MockModelGateway
from .repository import AnumRepository
from .runtime import AgentRuntime
from .schemas import AgentRunStep, Approval, ApprovalStatus, RunPhase, Task, TaskStatus, TenantContext, new_id, utc_now

logger = logging.getLogger("anum.email")

router = APIRouter(prefix="/api/v1/email", tags=["email"])

READ_DENIED = "Reading email needs the owner or member role in this workspace."
ACT_DENIED = "Acting on email needs the owner or member role in this workspace."
MANAGE_DENIED = "Only workspace owners can connect or disconnect mailboxes."


def _require(context: TenantContext, permission: Permission, detail: str) -> None:
    try:
        require_permission(context, permission)
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=detail) from None


def _http(exc: EmailError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _audit(
    repository: AnumRepository,
    context: TenantContext,
    action: str,
    target: str,
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=target,
            outcome=outcome,
            correlation_id=target,
            created_at=utc_now(),
            metadata=metadata or {},
        )
    )


# -- schemas ------------------------------------------------------------------------------


class ProviderSetup(BaseModel):
    provider: EmailProvider
    name: str
    configured: bool
    enabled_here: bool
    missing: list[str]
    scopes: list[str]


class EmailSetup(BaseModel):
    providers: list[ProviderSetup]
    redirect_uri: str
    can_connect: bool
    can_act: bool


class ConnectStart(BaseModel):
    visibility: Visibility = Visibility.WORKSPACE


class ConnectStartResponse(BaseModel):
    authorization_url: str
    expires_at: str


class ConnectComplete(BaseModel):
    connection: str = Field(min_length=8, max_length=80)


class DisconnectResult(BaseModel):
    revoked_at_provider: bool
    detail: str


class MessageView(BaseModel):
    id: str
    account_id: str
    account_address: str
    provider: EmailProvider
    thread_id: str
    subject: str
    from_address: str
    from_name: str | None
    to: list[str]
    cc: list[str]
    snippet: str
    labels: list[str]
    received_at: str
    is_read: bool
    is_archived: bool
    has_attachments: bool
    category: Category | None
    priority: Priority | None
    summary: str | None
    triage_source: str | None
    triage_model: str | None


class MessagePage(BaseModel):
    items: list[MessageView]
    next_cursor: str | None
    accounts: int


class DraftView(BaseModel):
    id: str
    account_id: str
    message_id: str | None
    to: list[str]
    cc: list[str]
    subject: str
    body: str
    status: DraftStatus
    source: Literal["model", "person"]
    model_provider: str | None
    model_name: str | None
    saved_in_provider: bool
    error: str | None
    approval: Approval | None
    approval_note: str | None
    created_by: str
    created_at: str
    updated_at: str
    sent_at: str | None


class AttachmentView(BaseModel):
    id: str
    filename: str
    content_type: str
    size: int


class MessageDetail(BaseModel):
    message: MessageView
    thread: list[MessageView]
    body: str | None
    body_truncated: bool
    body_error: str | None
    attachments: list[AttachmentView]
    drafts: list[DraftView]


class ActionResult(BaseModel):
    status: Literal["done", "waiting_approval", "failed"]
    summary: str
    approval: Approval | None = None
    message: MessageView | None = None


class ReadBody(BaseModel):
    read: bool = True


class DraftCreate(BaseModel):
    body: str | None = Field(default=None, max_length=MAX_DRAFT_BODY)
    instructions: str | None = Field(default=None, max_length=MAX_INSTRUCTIONS)


class DraftCreateResult(BaseModel):
    status: Literal["done", "waiting_approval", "failed"]
    summary: str
    draft: DraftView | None = None
    approval: Approval | None = None
    warning: str | None = None


class DraftUpdate(BaseModel):
    to: list[str] | None = Field(default=None, max_length=50)
    cc: list[str] | None = Field(default=None, max_length=50)
    subject: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=MAX_DRAFT_BODY)


class SendApprovalResponse(BaseModel):
    draft: DraftView
    approval: Approval


class AnalyzeResult(BaseModel):
    message: MessageView
    suggested_reply: str
    model: str


class InboxSummary(BaseModel):
    accounts: int
    unread: int
    high_priority_unread: int
    by_category: dict[str, int]
    needs_attention: list[MessageView]


# -- views --------------------------------------------------------------------------------


def _message_view(message: EmailMessage, account: EmailAccount) -> MessageView:
    return MessageView(
        id=message.id,
        account_id=account.id,
        account_address=account.address,
        provider=account.provider,
        thread_id=message.thread_id,
        subject=message.subject,
        from_address=message.from_address,
        from_name=message.from_name,
        to=message.to,
        cc=message.cc,
        snippet=message.snippet,
        labels=message.labels,
        received_at=message.received_at.isoformat(),
        is_read=message.is_read,
        is_archived=message.is_archived,
        has_attachments=message.has_attachments,
        category=message.category,
        priority=message.priority,
        summary=message.summary,
        triage_source=message.triage_source,
        triage_model=message.triage_model,
    )


def _draft_view(draft: EmailDraft, repository: AnumRepository, context: TenantContext) -> DraftView:
    approval = None
    note = None
    shown = draft.status
    if draft.approval_id and draft.status == DraftStatus.PENDING_APPROVAL:
        found = repository.get_approval(draft.approval_id, context)
        if found is not None and found.status == ApprovalStatus.PENDING and (found.expires_at is None or found.expires_at > utc_now()):
            approval = found
        elif found is not None and found.status in {ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED} or found is None:
            shown = DraftStatus.DRAFT
            note = (
                f"The send approval was {found.status.value}." if found is not None else "The send approval is gone."
            ) + " Edit the reply if needed and ask for approval again."
    return DraftView(
        id=draft.id,
        account_id=draft.account_id,
        message_id=draft.message_id,
        to=draft.to,
        cc=draft.cc,
        subject=draft.subject,
        body=draft.body,
        status=shown,
        source=draft.source,
        model_provider=draft.model_provider,
        model_name=draft.model_name,
        saved_in_provider=bool(draft.provider_draft_id),
        error=draft.error,
        approval=approval,
        approval_note=note,
        created_by=draft.created_by,
        created_at=draft.created_at.isoformat(),
        updated_at=draft.updated_at.isoformat(),
        sent_at=draft.sent_at.isoformat() if draft.sent_at else None,
    )


# -- mediation ----------------------------------------------------------------------------


def _pause_for_approval(
    repository: AnumRepository, context: TenantContext, call: ToolCall, title: str, prompt: str
) -> tuple[Task, Approval]:
    """A task whose run is paused at ``call`` for approval (as developer merges are)."""
    now = utc_now()
    task = Task(
        id=new_id("task"),
        title=title[:160],
        prompt=prompt[:4000],
        status=TaskStatus.CREATED,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        created_at=now,
        updated_at=now,
        created_by=context.user_id,
    )
    repository.create_task(task)
    repository.record_event(
        create_event(CanonicalEventName.TASK_CREATED, context, task.id, {"title": task.title}, correlation_id=task.id, created_at=now).event
    )
    runtime = AgentRuntime(MockModelGateway(), repository, tools=email_tool_registry())
    run = runtime.new_run(task)
    decision = runtime.evaluate(call, context)
    proposal = AgentRunStep(
        id=new_id("step"),
        type="tool_proposal",
        summary=f"Proposed tool call: {call.name}",
        created_at=utc_now(),
        metadata={
            "tool": call.name,
            "risk_level": decision.risk_level.value,
            "policy_outcome": decision.outcome.value,
            "governance_rules": list(decision.governance_rules),
        },
    )
    run.steps.append(proposal)
    run.checkpoint.phase = RunPhase.TOOL_READY
    run.checkpoint.version += 1
    run.checkpoint.tool_call = call.model_dump(mode="json")
    run.checkpoint.last_step_id = proposal.id
    task.status = run.status = TaskStatus.RUNNING
    repository.save_task(task)
    repository.save_run(run)
    if runtime.begin_execution(task, run, context) is not None:  # pragma: no cover - only paused calls come here
        raise RuntimeError("an email action expected to wait for approval was allowed")
    repository.save_task(task)
    repository.save_run(run)
    if run.checkpoint.phase != RunPhase.WAITING_APPROVAL or not run.checkpoint.approval_id:
        reason = run.steps[-1].summary if run.steps else "Blocked by policy."
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Blocked: {reason}")
    approval = repository.get_approval(run.checkpoint.approval_id, context)
    if approval is None:  # pragma: no cover - saved just above
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Approval was not stored")
    return task, approval


async def _mediated(
    repository: AnumRepository, context: TenantContext, call: ToolCall, title: str
) -> tuple[ToolResult | None, Approval | None]:
    """Run an email tool for a person's click under the same policy agents get."""
    registry = email_tool_registry()
    runtime = AgentRuntime(MockModelGateway(), repository, tools=registry)
    decision = runtime.evaluate(call, context)
    if decision.outcome == ToolPolicyOutcome.BLOCK:
        _audit(repository, context, f"email.{call.name}", str(call.arguments.get("message_id") or ""), "denied", {"reason": decision.reason})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Blocked by policy: {decision.reason}")
    if decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
        _, approval = _pause_for_approval(repository, context, call, title, f"{title} ({call.name}).")
        return None, approval
    result = await registry.execute(call, context)
    _audit(
        repository,
        context,
        f"email.{call.name}",
        str(call.arguments.get("message_id") or call.arguments.get("draft_id") or ""),
        "success" if result.status == "succeeded" else "failure",
        {**result.audit_metadata, "summary": result.summary[:200] if result.status != "succeeded" else None},
    )
    return result, None


def _retire(repository: AnumRepository, context: TenantContext, draft: EmailDraft) -> None:
    """Cancel an earlier send task still waiting for approval (the draft changed)."""
    if not draft.task_id:
        return
    task = repository.get_task_for_update(draft.task_id, context)
    if task is None or task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        return
    now = utc_now()
    task.status = TaskStatus.CANCELLED
    task.updated_at = now
    repository.save_task(task)
    run = repository.find_run_for_task(task.id, context)
    if run is not None and run.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        run.status = TaskStatus.CANCELLED
        run.checkpoint.phase = RunPhase.CANCELLED
        run.checkpoint.version += 1
        run.updated_at = now
        repository.save_run(run)
    if draft.approval_id:
        approval = repository.get_approval_for_update(draft.approval_id, context)
        if approval is not None and approval.status == ApprovalStatus.PENDING:
            approval.status = ApprovalStatus.EXPIRED
            approval.decided_at = now
            repository.save_approval(approval)
    repository.record_event(
        create_event(CanonicalEventName.TASK_CANCELLED, context, task.id, {"task_id": task.id}, correlation_id=task.id).event
    )


# -- setup and connecting -------------------------------------------------------------------


@router.get("/setup", response_model=EmailSetup)
async def get_setup(context: TenantContext = Depends(tenant_context)) -> EmailSetup:
    """Which providers the operator set up (never a secret) and what is missing."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    providers = []
    for provider in EmailProvider:
        problems = provider_problems(provider)
        providers.append(
            ProviderSetup(
                provider=provider,
                name=PROVIDER_NAMES[provider],
                configured=not problems,
                enabled_here=provider_enabled_here(provider, context),
                missing=problems,
                scopes=provider_scopes(provider),
            )
        )
    return EmailSetup(
        providers=providers,
        redirect_uri=email_settings.redirect_uri,
        can_connect=has_permission(context, Permission.EMAIL_MANAGE),
        can_act=has_permission(context, Permission.EMAIL_ACT),
    )


def _state_token(pending: OAuthPending) -> str:
    from .secret_box import secret_cipher

    payload = {"t": pending.tenant_id, "w": pending.workspace_id, "u": pending.user_id, "n": pending.nonce}
    return secret_cipher().encrypt(json.dumps(payload, separators=(",", ":")))


@router.post("/oauth/{provider}/start", response_model=ConnectStartResponse)
async def start_connect(
    provider: EmailProvider,
    payload: ConnectStart,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ConnectStartResponse:
    """The provider's consent URL. The browser goes there; the provider comes back to the callback."""
    _require(context, Permission.EMAIL_MANAGE, MANAGE_DENIED)
    problems = provider_problems(provider)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{PROVIDER_NAMES[provider]} is not set up on this server. Missing: {', '.join(problems)} (docs/email.md).",
        )
    if not provider_enabled_here(provider, context):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{PROVIDER_NAMES[provider]} is turned off for this workspace under Integrations.",
        )
    from .secret_box import secret_cipher

    client = provider_client(provider)
    verifier, challenge = pkce_pair() if client.uses_pkce else (None, None)
    now = utc_now()
    pending = OAuthPending(
        nonce=new_id("eoc"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        provider=provider,
        visibility=payload.visibility,
        code_verifier_enc=secret_cipher().encrypt(verifier) if verifier else None,
        created_at=now,
        expires_at=now + timedelta(seconds=email_settings.oauth_state_ttl_seconds),
    )
    with open_email_store(context) as store:
        store.add_pending(pending)
    _audit(repository, context, "email.connect.started", pending.nonce, metadata={"provider": provider.value, "visibility": payload.visibility.value})
    return ConnectStartResponse(
        authorization_url=client.authorization_url(_state_token(pending), challenge),
        expires_at=pending.expires_at.isoformat(),
    )


def _web_return(**params: str) -> RedirectResponse:
    from urllib.parse import urlencode

    base = email_settings.web_url.rstrip("/")
    return RedirectResponse(f"{base}/?{urlencode(params)}#inbox", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/oauth/callback", include_in_schema=True)
async def oauth_callback(
    state: str = Query(default="", max_length=2000),
    code: str | None = Query(default=None, max_length=4000),
    error: str | None = Query(default=None, max_length=200),
    error_description: str | None = Query(default=None, max_length=500),
) -> RedirectResponse:
    """Where the provider sends the browser back. Unauthenticated by design: it only stores
    the code (encrypted) on the pending request named by the encrypted ``state`` and sends
    the browser to the web app, which finishes the connection as the signed-in person
    (``POST /oauth/complete``). A code delivered to someone else's browser is useless.
    """
    from .secret_box import SecretDecryptionError, secret_cipher

    try:
        data = json.loads(secret_cipher().decrypt_fresh(state, email_settings.oauth_state_ttl_seconds))
        context = TenantContext(tenant_id=str(data["t"]), workspace_id=str(data["w"]), user_id=str(data["u"]), roles=[])
        nonce = str(data["n"])
    except (SecretDecryptionError, ValueError, KeyError, TypeError):
        return _web_return(email_connect_error="invalid_state")
    with open_email_store(context) as store:
        pending = store.get_pending(nonce)
        if pending is None or pending.code_enc is not None or pending.error is not None:
            return _web_return(email_connect_error="unknown_request")
        if error or not code:
            pending.error = (error_description or error or "no authorization code")[:300]
        else:
            pending.code_enc = secret_cipher().encrypt(code)
        store.save_pending(pending)
    return _web_return(email_connect=nonce)


@router.post("/oauth/complete", response_model=EmailAccountView, status_code=status.HTTP_201_CREATED)
async def complete_connect(
    payload: ConnectComplete,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> EmailAccountView:
    _require(context, Permission.EMAIL_MANAGE, MANAGE_DENIED)
    from .secret_box import secret_cipher

    cipher = secret_cipher()
    with open_email_store(context) as store:
        pending = store.get_pending(payload.connection)
        if pending is None:
            raise HTTPException(status_code=404, detail="This connection request was not found or was already used. Start again.")
        if pending.user_id != context.user_id:
            raise HTTPException(status_code=403, detail="This connection was started by someone else; start your own.")
        if pending.code_enc is None and pending.error is None:
            raise HTTPException(status_code=409, detail="The provider has not sent you back yet. Finish the sign-in in the provider's window.")
        # Single use: consumed before the code is exchanged.
        store.delete_pending(pending.nonce)
    provider = pending.provider
    name = PROVIDER_NAMES[provider]
    if pending.expires_at < utc_now():
        raise HTTPException(status_code=410, detail="The connection request expired. Start again.")
    if pending.error:
        _audit(repository, context, "email.connect.denied", pending.nonce, "denied", {"provider": provider.value})
        raise HTTPException(status_code=400, detail=f"{name} did not grant access: {pending.error}")
    client = provider_client(provider)
    try:
        grant = await client.exchange_code(
            cipher.decrypt(pending.code_enc or ""),
            cipher.decrypt(pending.code_verifier_enc) if pending.code_verifier_enc else None,
        )
        profile = await client.profile(grant.access_token)
    except MailProviderError as exc:
        _audit(repository, context, "email.connect.failed", pending.nonce, "failure", {"provider": provider.value, "status": exc.status})
        raise HTTPException(status_code=502, detail=f"Connecting failed: {exc.message}") from None
    if not grant.refresh_token:
        raise HTTPException(
            status_code=502,
            detail=f"{name} did not return a refresh token, so ANUM could not keep the mailbox in sync. Remove ANUM's access in your account's security settings and connect again.",
        )
    now = utc_now()
    with open_email_store(context) as store:
        account = store.find_account(provider, profile.address)
        if account is not None and not can_see(account, context):
            raise HTTPException(status_code=409, detail="Someone else connected this mailbox privately in this workspace.")
        if account is None:
            account = EmailAccount(
                id=new_id("emb"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                provider=provider,
                address=profile.address,
                connected_by=context.user_id,
                visibility=pending.visibility,
                created_at=now,
                updated_at=now,
            )
            store.add_account(account)
            created = True
        else:
            created = False
        account.display_name = profile.display_name
        account.provider_account_id = profile.provider_account_id or account.provider_account_id
        account.scopes = list(grant.scopes or client.scopes)
        account.access_token_enc = cipher.encrypt(grant.access_token)
        account.refresh_token_enc = cipher.encrypt(grant.refresh_token)
        account.access_expires_at = grant.expires_at
        account.status = AccountStatus.CONNECTED
        account.last_error = None
        account.updated_at = now
        store.save_account(account)
    _audit(
        repository,
        context,
        "email.account.connected" if created else "email.account.reconnected",
        account.id,
        metadata={"provider": provider.value, "visibility": account.visibility.value},
    )
    try:
        account = await sync_account(account.id, context, force=True)
    except EmailError:
        pass
    with open_email_store(context) as store:
        counts = store.counts([account.id]).get(account.id, (0, 0))
    return account_view(account, counts, context, True)


# -- accounts -------------------------------------------------------------------------------


def _get_account(context: TenantContext, account_id: str) -> EmailAccount:
    with open_email_store(context) as store:
        account = store.get_account(account_id)
    if account is None or not can_see(account, context):
        raise HTTPException(status_code=404, detail="Mailbox not found")
    return account


@router.get("/accounts", response_model=list[EmailAccountView])
async def list_accounts(context: TenantContext = Depends(tenant_context)) -> list[EmailAccountView]:
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    manage = has_permission(context, Permission.EMAIL_MANAGE)
    with open_email_store(context) as store:
        accounts = visible_accounts(store, context)
        counts = store.counts([a.id for a in accounts])
    return [account_view(a, counts.get(a.id, (0, 0)), context, manage) for a in accounts]


@router.post("/accounts/{account_id}/sync", response_model=EmailAccountView)
async def sync(account_id: str, context: TenantContext = Depends(tenant_context)) -> EmailAccountView:
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    _get_account(context, account_id)
    try:
        account = await sync_account(account_id, context, force=True)
    except EmailError as exc:
        raise _http(exc) from None
    with open_email_store(context) as store:
        counts = store.counts([account.id]).get(account.id, (0, 0))
    return account_view(account, counts, context, has_permission(context, Permission.EMAIL_MANAGE))


@router.delete("/accounts/{account_id}", response_model=DisconnectResult)
async def disconnect(
    account_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DisconnectResult:
    """Revoke ANUM's grant at the provider (where it has an endpoint) and delete the mailbox's
    tokens, synced messages and drafts from ANUM."""
    _require(context, Permission.EMAIL_MANAGE, MANAGE_DENIED)
    account = _get_account(context, account_id)
    if account.visibility == Visibility.PRIVATE and account.connected_by != context.user_id:  # pragma: no cover - hidden above
        raise HTTPException(status_code=404, detail="Mailbox not found")
    revoked = False
    detail = "Disconnected. ANUM deleted the mailbox's tokens, synced messages and drafts."
    from .secret_box import SecretDecryptionError, secret_cipher

    try:
        if account.refresh_token_enc:
            revoked = await provider_client(account.provider).revoke(secret_cipher().decrypt(account.refresh_token_enc))
    except (MailProviderError, SecretDecryptionError) as exc:
        detail += f" Revoking at the provider failed ({getattr(exc, 'message', 'token unreadable')}); remove ANUM's access in your account's security settings."
    if not revoked and account.provider == EmailProvider.OUTLOOK:
        detail += " Microsoft has no per-app revoke call: remove ANUM under myapps.microsoft.com (or the Azure admin centre) to withdraw consent."
    with open_email_store(context) as store:
        store.delete_account(account.id)
    _audit(repository, context, "email.account.disconnected", account.id, metadata={"provider": account.provider.value, "revoked": revoked})
    return DisconnectResult(revoked_at_provider=revoked, detail=detail)


# -- messages -------------------------------------------------------------------------------


@router.get("/messages", response_model=MessagePage)
async def list_messages(
    account_id: str | None = Query(default=None, max_length=80),
    category: Category | None = None,
    priority: Priority | None = None,
    unread: bool | None = None,
    archived: bool = False,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=25, ge=1, le=MAX_LISTED),
    before: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
) -> MessagePage:
    """Newest first across the visible mailboxes; ``before`` is the last id of the previous page."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    filters = MessageFilters(
        account_id=account_id, category=category, priority=priority, unread=unread, archived=archived,
        query=" ".join((q or "").split()) or None,
    )
    with open_email_store(context) as store:
        accounts = {a.id: a for a in visible_accounts(store, context)}
        cursor = store.get_message(before) if before else None
        if before and (cursor is None or cursor.account_id not in accounts):
            raise HTTPException(status_code=400, detail="Unknown cursor")
        ids = [account_id] if account_id else list(accounts)
        if account_id and account_id not in accounts:
            raise HTTPException(status_code=404, detail="Mailbox not found")
        items = store.list_messages(ids, filters, limit=limit, before=cursor)
    return MessagePage(
        items=[_message_view(m, accounts[m.account_id]) for m in items],
        next_cursor=items[-1].id if len(items) == limit else None,
        accounts=len(accounts),
    )


@router.get("/summary", response_model=InboxSummary)
async def inbox_summary(context: TenantContext = Depends(tenant_context)) -> InboxSummary:
    """Counts for the assistant and voice ("any urgent customer emails?")."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    return summary_for(context)


def summary_for(context: TenantContext) -> InboxSummary:
    with open_email_store(context) as store:
        accounts = {a.id: a for a in visible_accounts(store, context)}
        unread = store.list_messages(list(accounts), MessageFilters(unread=True), limit=500, before=None)
    by_category: dict[str, int] = {}
    for message in unread:
        key = (message.category or Category.OTHER).value
        by_category[key] = by_category.get(key, 0) + 1
    high = [m for m in unread if m.priority == Priority.HIGH]
    return InboxSummary(
        accounts=len(accounts),
        unread=len(unread),
        high_priority_unread=len(high),
        by_category=by_category,
        needs_attention=[_message_view(m, accounts[m.account_id]) for m in high[:5]],
    )


@router.get("/messages/{message_id}", response_model=MessageDetail)
async def get_message(
    message_id: str,
    body: bool = Query(default=True),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> MessageDetail:
    """The message with its thread and drafts. The body is fetched from the provider (capped);
    a provider failure is reported in ``body_error`` and the metadata still comes back."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    try:
        account, message = load_message(context, message_id)
    except EmailError as exc:
        raise _http(exc) from None
    with open_email_store(context) as store:
        thread = store.thread(account.id, message.thread_id, 50)
        drafts = store.list_drafts([account.id], message_id=message.id, limit=20)
    text = None
    truncated = False
    error = None
    attachments: list[AttachmentView] = []
    if body:
        try:
            fetched = await fetch_body(context, account, message)
            text, truncated = fetched.text, fetched.truncated
            attachments = [AttachmentView(**item) for item in fetched.attachments]
        except EmailError as exc:
            error = exc.message
    return MessageDetail(
        message=_message_view(message, account),
        thread=[_message_view(m, account) for m in thread],
        body=text,
        body_truncated=truncated,
        body_error=error,
        attachments=attachments,
        drafts=[_draft_view(d, repository, context) for d in drafts],
    )


@router.get("/messages/{message_id}/attachments/{attachment_id}")
async def download_attachment(message_id: str, attachment_id: str, context: TenantContext = Depends(tenant_context)) -> Response:
    """The attachment as a download: never rendered inline, never sniffed, size-capped."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    try:
        data, filename, content_type = await fetch_attachment(context, message_id, attachment_id)
    except EmailError as exc:
        raise _http(exc) from None
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode("ascii").replace('"', "") or "attachment"
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "content-disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
            "x-content-type-options": "nosniff",
            "cache-control": "private, no-store",
            "content-security-policy": "default-src 'none'; sandbox",
        },
    )


async def _message_action(
    message_id: str, tool: str, arguments: dict[str, Any], title: str, context: TenantContext, repository: AnumRepository
) -> ActionResult:
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    try:
        account, message = load_message(context, message_id)
    except EmailError as exc:
        raise _http(exc) from None
    result, approval = await _mediated(repository, context, ToolCall(name=tool, arguments={"message_id": message_id, **arguments}), title)
    if approval is not None:
        return ActionResult(status="waiting_approval", summary="Waiting for approval (workspace or organization policy).", approval=approval)
    if result is None:  # pragma: no cover - _mediated returns one of the two
        raise HTTPException(status_code=500, detail="The action returned nothing")
    with open_email_store(context) as store:
        fresh = store.get_message(message_id) or message
    return ActionResult(
        status="done" if result.status == "succeeded" else "failed",
        summary=result.summary,
        message=_message_view(fresh, account),
    )


@router.post("/messages/{message_id}/read", response_model=ActionResult)
async def mark_read(
    message_id: str,
    payload: ReadBody,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ActionResult:
    return await _message_action(
        message_id, MARK_READ_TOOL, {"read": payload.read}, "Mark email read" if payload.read else "Mark email unread", context, repository
    )


@router.post("/messages/{message_id}/archive", response_model=ActionResult)
async def archive(
    message_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ActionResult:
    return await _message_action(message_id, ARCHIVE_TOOL, {}, "Archive email", context, repository)


@router.post("/messages/{message_id}/analyze", response_model=AnalyzeResult)
async def analyze(message_id: str, context: TenantContext = Depends(tenant_context)) -> AnalyzeResult:
    """AI triage: category, priority, summary and a suggested reply (one budgeted model call)."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    try:
        message, result = await analyze_message(context, message_id)
        account, _ = load_message(context, message_id)
    except EmailError as exc:
        raise _http(exc) from None
    return AnalyzeResult(
        message=_message_view(message, account),
        suggested_reply=result.reply,
        model=f"{result.model_provider}/{result.model_name}",
    )


@router.post("/messages/{message_id}/drafts", response_model=DraftCreateResult, status_code=status.HTTP_201_CREATED)
async def create_reply_draft(
    message_id: str,
    payload: DraftCreate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DraftCreateResult:
    """A reply draft: ``body`` as written, or drafted by the model (following ``instructions``)."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    try:
        load_message(context, message_id)
    except EmailError as exc:
        raise _http(exc) from None
    arguments: dict[str, Any] = {"message_id": message_id}
    if payload.body and payload.body.strip():
        arguments["body"] = payload.body
    if payload.instructions and payload.instructions.strip():
        arguments["instructions"] = payload.instructions.strip()
    result, approval = await _mediated(repository, context, ToolCall(name=DRAFT_TOOL, arguments=arguments), "Draft an email reply")
    if approval is not None:
        return DraftCreateResult(status="waiting_approval", summary="Drafting waits for approval (workspace policy).", approval=approval)
    if result is None:  # pragma: no cover - _mediated returns one of the two
        raise HTTPException(status_code=500, detail="The action returned nothing")
    if result.status != "succeeded":
        raise HTTPException(status_code=409, detail=result.summary)
    _, draft, _ = load_draft(context, str(result.output.get("draft_id")))
    warning = result.output.get("warning") or None
    return DraftCreateResult(status="done", summary=result.summary, draft=_draft_view(draft, repository, context), warning=warning)


# -- drafts ---------------------------------------------------------------------------------


EDITABLE = frozenset({DraftStatus.DRAFT, DraftStatus.PENDING_APPROVAL, DraftStatus.FAILED})


@router.patch("/drafts/{draft_id}", response_model=DraftView)
async def update_draft(
    draft_id: str,
    payload: DraftUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DraftView:
    """Edit a draft. A pending send approval for the old text is withdrawn."""
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    try:
        account, draft, original = load_draft(context, draft_id)
        if draft.status not in EDITABLE:
            raise EmailError(409, f"This reply is {draft.status.value}; it can no longer be edited.")
        if payload.to is not None:
            draft.to = valid_addresses(payload.to)
        if payload.cc is not None:
            draft.cc = valid_addresses(payload.cc)
    except EmailError as exc:
        raise _http(exc) from None
    if payload.subject is not None:
        draft.subject = " ".join(payload.subject.split())[:500]
    if payload.body is not None:
        body = payload.body.strip()[:MAX_DRAFT_BODY]
        if body != draft.body:
            # Edited by a person: the model (if any) stays named as the first drafter.
            draft.source = "person"
        draft.body = body
    _retire(repository, context, draft)
    draft.status = DraftStatus.DRAFT
    draft.approval_id = draft.task_id = None
    draft.error = None
    draft.updated_at = utc_now()
    draft.provider_draft_id, _warning = await save_provider_draft(context, account, draft, original)
    with open_email_store(context) as store:
        store.save_draft(draft)
    return _draft_view(draft, repository, context)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_draft(
    draft_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    try:
        account, draft, _ = load_draft(context, draft_id)
    except EmailError as exc:
        raise _http(exc) from None
    if draft.status == DraftStatus.SENT:
        raise HTTPException(status_code=409, detail="This reply was already sent.")
    _retire(repository, context, draft)
    if draft.provider_draft_id:
        try:
            token = await access_token(account, context)
            await provider_client(account.provider).delete_draft(token, draft.provider_draft_id, account_ref=account.provider_account_id)
        except (EmailError, MailProviderError):
            pass  # the ANUM copy is discarded either way
    draft.status = DraftStatus.DISCARDED
    draft.updated_at = utc_now()
    with open_email_store(context) as store:
        store.save_draft(draft)
    _audit(repository, context, "email.draft.discarded", draft.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/drafts/{draft_id}/send-approval", response_model=SendApprovalResponse)
async def request_send_approval(
    draft_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> SendApprovalResponse:
    """Ask to send: a paused ``send_email`` call with exactly this draft's text.

    Approving goes through ``POST /api/v1/approvals/{id}/approve`` with the payload hash
    shown; only then does the runtime send, and only that text to those recipients.
    """
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    try:
        account, draft, original = load_draft(context, draft_id)
    except EmailError as exc:
        raise _http(exc) from None
    if draft.status not in EDITABLE:
        raise HTTPException(status_code=409, detail=f"This reply is {draft.status.value}.")
    if not draft.to:
        raise HTTPException(status_code=422, detail="Add at least one recipient first.")
    if not draft.body.strip():
        raise HTTPException(status_code=422, detail="The reply is empty.")
    if account.status == AccountStatus.REAUTH_REQUIRED:
        raise HTTPException(status_code=409, detail="Reconnect this mailbox before sending from it.")
    arguments = send_arguments(draft, account, original)
    if draft.approval_id and draft.status == DraftStatus.PENDING_APPROVAL:
        existing = repository.get_approval(draft.approval_id, context)
        if (
            existing is not None
            and existing.status == ApprovalStatus.PENDING
            and (existing.expires_at is None or existing.expires_at > utc_now())
            and existing.arguments.get("body") == arguments["body"]
            and existing.arguments.get("to") == arguments["to"]
        ):
            return SendApprovalResponse(draft=_draft_view(draft, repository, context), approval=existing)
        _retire(repository, context, draft)
    call = ToolCall(name=SEND_TOOL, arguments=arguments)
    recipients = ", ".join(draft.to)
    task, approval = _pause_for_approval(
        repository,
        context,
        call,
        f"Send email to {recipients}"[:160],
        f"Send the reply '{draft.subject}' from {account.address} to {recipients}.",
    )
    draft.status = DraftStatus.PENDING_APPROVAL
    draft.approval_id, draft.task_id = approval.id, task.id
    draft.error = None
    draft.updated_at = utc_now()
    with open_email_store(context) as store:
        store.save_draft(draft)
    _audit(
        repository, context, "email.send.requested", draft.id,
        metadata={"approval_id": approval.id, "task_id": task.id, "recipients": len(draft.to) + len(draft.cc), "payload_hash": approval.payload_hash},
    )
    return SendApprovalResponse(draft=_draft_view(draft, repository, context), approval=approval)


@router.get("/drafts", response_model=list[DraftView])
async def list_drafts(
    limit: int = Query(default=25, ge=1, le=MAX_LISTED),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[DraftView]:
    """Recent reply drafts across the visible mailboxes (newest first)."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    with open_email_store(context) as store:
        accounts = visible_accounts(store, context)
        drafts = store.list_drafts([a.id for a in accounts], message_id=None, limit=limit)
    return [_draft_view(d, repository, context) for d in drafts]
