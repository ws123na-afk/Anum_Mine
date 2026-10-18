"""Mailbox settings, forwarding rules and the background unread counts (docs/email.md#mailbox-settings).

Routes under ``/api/v1/mail`` next to ``api.py``:

* ``/accounts/{id}/settings``: retention (sent messages, unsent drafts, AI labels) and background
  sync. Everyone with access to the mailbox reads them; only its owner asks to change them, and
  the change is applied only after an approval bound to its payload hash
  (``change_mail_settings``, always asked).
* ``/accounts/{id}/rules``, ``/rules/{id}``: forwarding rules, the owner's only. A new or edited
  rule (and resuming a paused one) waits for an approval of exactly that rule
  (``activate_mail_forward_rule``, always asked); nothing is forwarded before. Pausing and
  removing need no approval: they only stop forwarding.
* ``/unread``: the inbox unread counts the background sync stored, for the sidebar badge.

Every state-changing route writes an audit record with ids and counts, never addresses or mail.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from ..agent_tools import ToolCall, ToolPolicyOutcome
from ..authorization import Permission
from ..dependencies import has_permission, repository_context, tenant_context
from ..email_api import _pause_for_approval, _retire
from ..model_gateway import MockModelGateway
from ..repository import AnumRepository
from ..runtime import AgentRuntime
from ..schemas import Approval, ApprovalStatus, TenantContext, new_id, utc_now
from .api import ACT_DENIED, READ_DENIED, _audit, _available, _require, accounts_for, load_access
from .automation import (
    MAX_FORWARD_TO,
    MAX_RETENTION_DAYS,
    MAX_RULES,
    RETENTION_CHOICES,
    SETTING_FIELDS,
    ForwardRule,
    RuleStatus,
    SyncState,
    default_settings,
    hash_of,
    open_automation_store,
    settings_values,
)
from .automation_tools import RULE_TOOL, SETTINGS_TOOL, forward_problem
from .config import mail_settings
from .service import mail_tool_registry, tenant_allowed
from .store import MailAccount

router = APIRouter(prefix="/api/v1/mail", tags=["mail"])


# -- schemas ------------------------------------------------------------------------------------


class SyncView(BaseModel):
    status: Literal["pending", "ok", "auth_failed", "offline", "error"]
    inbox_unread: int | None
    inbox_total: int | None
    checked_at: str | None
    next_sync_at: str | None
    last_error: str | None


class SettingsView(BaseModel):
    account_id: str
    access: Literal["owner", "send", "read"]
    can_change: bool
    sent_retention_days: int | None
    draft_retention_days: int | None
    label_retention_days: int | None
    background_sync: bool
    version: int
    updated_at: str | None
    retention_choices: list[int]
    max_retention_days: int
    # The server checks mailboxes in the background (ANUM_MAILSERVER_SYNC_POLL_SECONDS > 0).
    sync_running: bool
    sync_interval_seconds: int
    sync: SyncView | None
    # The change waiting for an owner's approval, if any.
    approval: Approval | None
    pending: dict[str, Any] | None


class SettingsChange(BaseModel):
    sent_retention_days: int | None = Field(default=None, ge=1, le=MAX_RETENTION_DAYS)
    draft_retention_days: int | None = Field(default=None, ge=1, le=MAX_RETENTION_DAYS)
    label_retention_days: int | None = Field(default=None, ge=1, le=MAX_RETENTION_DAYS)
    background_sync: bool = True
    # The version the person looked at; a change made in between answers 409.
    base_version: int = Field(ge=0)


class Waiting(BaseModel):
    status: Literal["waiting_approval"]
    summary: str
    approval: Approval


class RuleView(BaseModel):
    id: str
    account_id: str
    name: str
    from_contains: str
    subject_contains: str
    forward_to: list[str]
    status: RuleStatus
    approval: Approval | None
    approval_note: str | None
    approved_at: str | None
    last_run_at: str | None
    last_error: str | None
    forwarded_count: int
    created_at: str
    updated_at: str


class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    from_contains: str = Field(default="", max_length=200)
    subject_contains: str = Field(default="", max_length=200)
    forward_to: list[str] = Field(min_length=1, max_length=MAX_FORWARD_TO)


class RuleChange(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    from_contains: str | None = Field(default=None, max_length=200)
    subject_contains: str | None = Field(default=None, max_length=200)
    forward_to: list[str] | None = Field(default=None, min_length=1, max_length=MAX_FORWARD_TO)


class RuleResult(BaseModel):
    rule: RuleView
    approval: Approval | None


class UnreadMailbox(BaseModel):
    account_id: str
    address: str
    access: Literal["owner", "send", "read"]
    unread: int | None
    checked_at: str | None
    status: str


class UnreadView(BaseModel):
    enabled: bool
    sync_running: bool
    total: int
    checked_at: str | None
    mailboxes: list[UnreadMailbox]


# -- helpers ------------------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _live(repository: AnumRepository, context: TenantContext, approval_id: str | None) -> tuple[Approval | None, str | None]:
    """The approval while it can still be decided, else a sentence about what happened to it."""
    if not approval_id:
        return None, None
    found = repository.get_approval(approval_id, context)
    if found is not None and found.status == ApprovalStatus.PENDING and (found.expires_at is None or found.expires_at > utc_now()):
        return found, None
    if found is None:
        return None, "The approval is gone. Ask again."
    if found.status in {ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED} or found.status == ApprovalStatus.PENDING:
        return None, f"The approval was {'expired' if found.status == ApprovalStatus.PENDING else found.status.value}. Ask again."
    return None, None


def _sync_view(state: SyncState | None) -> SyncView | None:
    if state is None:
        return None
    return SyncView(
        status=state.status, inbox_unread=state.inbox_unread, inbox_total=state.inbox_total, checked_at=_iso(state.checked_at),
        next_sync_at=_iso(state.next_sync_at), last_error=state.last_error,
    )


def _rule_view(repository: AnumRepository, context: TenantContext, rule: ForwardRule) -> RuleView:
    approval, note = _live(repository, context, rule.approval_id) if rule.status == "pending_approval" else (None, None)
    return RuleView(
        id=rule.id, account_id=rule.account_id, name=rule.name, from_contains=rule.from_contains, subject_contains=rule.subject_contains,
        forward_to=rule.forward_to, status=rule.status, approval=approval, approval_note=note, approved_at=_iso(rule.approved_at),
        last_run_at=_iso(rule.last_run_at), last_error=rule.last_error, forwarded_count=rule.forwarded_count,
        created_at=rule.created_at.isoformat(), updated_at=rule.updated_at.isoformat(),
    )


def _ask(repository: AnumRepository, context: TenantContext, call: ToolCall, title: str) -> Approval:
    """Pause ``call`` for an owner's approval under the runtime's tool policy (never runs here)."""
    registry = mail_tool_registry()
    decision = AgentRuntime(MockModelGateway(), repository, tools=registry).evaluate(call, context)
    if decision.outcome == ToolPolicyOutcome.BLOCK:
        _audit(repository, context, f"mail.{call.name}", str(call.arguments.get("account_id") or ""), "denied", {"reason": decision.reason[:200]})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Blocked by policy: {decision.reason}")
    _, approval = _pause_for_approval(repository, context, call, title, f"{title} ({call.name}).", registry=registry)
    return approval


def _owned(context: TenantContext, account_id: str) -> MailAccount:
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    return load_access(context, account_id, "owner")[0]


def _clean_rule(body: dict[str, Any], account: MailAccount) -> dict[str, Any]:
    name = " ".join(str(body["name"]).split())
    from_contains = str(body.get("from_contains") or "").strip()
    subject_contains = str(body.get("subject_contains") or "").strip()
    forward_to: list[str] = []
    for value in body["forward_to"]:
        address = str(value).strip()
        if address and address.lower() not in {item.lower() for item in forward_to}:
            forward_to.append(address)
    if not name:
        raise HTTPException(status_code=422, detail="Give the rule a name.")
    if not from_contains and not subject_contains:
        raise HTTPException(status_code=422, detail="Say which mail the rule forwards: part of the sender, of the subject, or both.")
    problem = forward_problem(forward_to, account.address)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    return {"name": name, "from_contains": from_contains, "subject_contains": subject_contains, "forward_to": forward_to}


def _ask_for_rule(repository: AnumRepository, context: TenantContext, account: MailAccount, rule: ForwardRule) -> ForwardRule:
    """Withdraw any earlier approval of the rule and ask for one of exactly what it does now."""
    if rule.approval_id or rule.task_id:
        _retire(repository, context, rule)  # type: ignore[arg-type]
    digest = hash_of(rule)
    call = ToolCall(name=RULE_TOOL, arguments={
        "rule_id": rule.id, "account_id": account.id, "owner_user_id": account.owner_user_id, "mailbox": account.address, "name": rule.name,
        "from_contains": rule.from_contains, "subject_contains": rule.subject_contains, "forward_to": rule.forward_to, "rule_hash": digest,
        "requested_by": context.user_id,
    })
    approval = _ask(repository, context, call, f"Forward mail from {account.address} to {', '.join(rule.forward_to)} ({rule.name})")
    now = utc_now()
    rule = rule.model_copy(update={
        "status": "pending_approval", "rule_hash": digest, "approved_hash": None, "approval_id": approval.id, "task_id": approval.task_id,
        "last_error": None, "updated_at": now,
    })
    with open_automation_store(context) as store:
        store.save_rule(rule)
    return rule


def _load_rule(context: TenantContext, rule_id: str) -> tuple[ForwardRule, MailAccount]:
    _require(context, Permission.EMAIL_ACT, ACT_DENIED)
    _available(context)
    with open_automation_store(context) as store:
        rule = store.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found.")
    return rule, load_access(context, rule.account_id, "owner")[0]


# -- settings -----------------------------------------------------------------------------------


@router.get("/accounts/{account_id}/settings", response_model=SettingsView)
async def get_settings(account_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> SettingsView:
    """The mailbox's retention and background sync, its last background check and any change waiting."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    _available(context)
    account, access = load_access(context, account_id, "read")
    with open_automation_store(context) as store:
        kept = store.get_settings(account.id)
        state = store.get_sync(account.id)
    shown = kept or default_settings(account.id, account.tenant_id, account.workspace_id, account.owner_user_id)
    approval = None
    pending = None
    if access == "owner" and kept is not None and kept.pending_approval_id:
        approval, _ = _live(repository, context, kept.pending_approval_id)
        if approval is not None:
            asked = approval.arguments.get("settings")
            pending = asked if isinstance(asked, dict) else None
    return SettingsView(
        account_id=account.id, access=access, can_change=access == "owner" and has_permission(context, Permission.EMAIL_ACT),
        **settings_values(shown), version=shown.version, updated_at=_iso(kept.updated_at) if kept else None,
        retention_choices=list(RETENTION_CHOICES), max_retention_days=MAX_RETENTION_DAYS,
        sync_running=mail_settings.sync_poll_seconds > 0, sync_interval_seconds=mail_settings.sync_interval_seconds,
        sync=_sync_view(state), approval=approval, pending=pending,
    )


@router.post("/accounts/{account_id}/settings", response_model=Waiting)
async def ask_settings_change(
    account_id: str, body: SettingsChange, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)
) -> Waiting:
    """Ask to change the mailbox's settings: applied only after an owner approves exactly this change."""
    account = _owned(context, account_id)
    wanted = body.model_dump(include=set(SETTING_FIELDS))
    with open_automation_store(context) as store:
        kept = store.get_settings(account.id)
        if kept is None:
            kept = default_settings(account.id, context.tenant_id, context.workspace_id, context.user_id)
            store.save_settings(kept, None)
            kept = store.get_settings(account.id) or kept
    if kept.version != body.base_version:
        raise HTTPException(status_code=409, detail="The settings changed since you opened them. Look at them again and ask again.")
    if wanted == settings_values(kept):
        raise HTTPException(status_code=422, detail="Nothing changed: these are the settings already.")
    if kept.pending_approval_id or kept.pending_task_id:
        _retire(repository, context, kept)  # type: ignore[arg-type]
    call = ToolCall(name=SETTINGS_TOOL, arguments={
        "account_id": account.id, "owner_user_id": account.owner_user_id, "mailbox": account.address, "base_version": kept.version,
        "settings": wanted, "previous": settings_values(kept), "requested_by": context.user_id,
    })
    approval = _ask(repository, context, call, f"Change the settings of {account.address}")
    with open_automation_store(context) as store:
        store.set_pending(account.id, approval.id, approval.task_id)
    _audit(repository, context, "mail.settings.requested", account.id, metadata={"approval_id": approval.id, "payload_hash": approval.payload_hash, "base_version": kept.version})
    return Waiting(status="waiting_approval", summary=f"Waiting for approval to change the settings of {account.address}.", approval=approval)


@router.delete("/accounts/{account_id}/settings/pending", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw_settings_change(account_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Withdraw a settings change still waiting for approval (nothing changes)."""
    account = _owned(context, account_id)
    with open_automation_store(context) as store:
        kept = store.get_settings(account.id)
        if kept is None or not (kept.pending_approval_id or kept.pending_task_id):
            raise HTTPException(status_code=404, detail="No settings change is waiting for approval.")
        store.set_pending(account.id, None, None)
    _retire(repository, context, kept)  # type: ignore[arg-type]
    _audit(repository, context, "mail.settings.withdrawn", account.id, metadata={"approval_id": kept.pending_approval_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- forwarding rules -----------------------------------------------------------------------------


@router.get("/accounts/{account_id}/rules", response_model=list[RuleView])
async def list_rules(account_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> list[RuleView]:
    """The mailbox's forwarding rules (its owner's only)."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    _available(context)
    account = load_access(context, account_id, "owner")[0]
    with open_automation_store(context) as store:
        rules = store.list_rules(account.id)
    return [_rule_view(repository, context, rule) for rule in rules]


@router.post("/accounts/{account_id}/rules", response_model=RuleResult, status_code=status.HTTP_201_CREATED)
async def create_rule(account_id: str, body: RuleBody, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> RuleResult:
    """A new forwarding rule. It forwards nothing until an owner approves exactly this rule."""
    account = _owned(context, account_id)
    values = _clean_rule(body.model_dump(), account)
    with open_automation_store(context) as store:
        if len(store.list_rules(account.id)) >= MAX_RULES:
            raise HTTPException(status_code=409, detail=f"A mailbox has at most {MAX_RULES} forwarding rules. Remove one first.")
    rule = ForwardRule(id=new_id("mrule"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, owner_user_id=context.user_id, account_id=account.id, **values)
    rule = rule.model_copy(update={"rule_hash": hash_of(rule)})
    with open_automation_store(context) as store:
        store.save_rule(rule)
    rule = _ask_for_rule(repository, context, account, rule)
    view = _rule_view(repository, context, rule)
    _audit(repository, context, "mail.rule.created", rule.id, metadata={"account_id": account.id, "recipients": len(rule.forward_to), "approval_id": rule.approval_id})
    return RuleResult(rule=view, approval=view.approval)


@router.patch("/rules/{rule_id}", response_model=RuleResult)
async def change_rule(rule_id: str, body: RuleChange, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> RuleResult:
    """Edit a rule: it stops forwarding at once and waits for a new approval of what it does now."""
    rule, account = _load_rule(context, rule_id)
    merged = {**rule.model_dump(include={"name", "from_contains", "subject_contains", "forward_to"}), **body.model_dump(exclude_none=True)}
    values = _clean_rule(merged, account)
    rule = rule.model_copy(update={**values, "status": "pending_approval", "approved_hash": None})
    with open_automation_store(context) as store:
        store.save_rule(rule.model_copy(update={"rule_hash": hash_of(rule), "updated_at": utc_now()}))
    rule = _ask_for_rule(repository, context, account, rule)
    view = _rule_view(repository, context, rule)
    _audit(repository, context, "mail.rule.changed", rule.id, metadata={"account_id": account.id, "recipients": len(rule.forward_to), "approval_id": rule.approval_id})
    return RuleResult(rule=view, approval=view.approval)


@router.post("/rules/{rule_id}/pause", response_model=RuleView)
async def pause_rule(rule_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> RuleView:
    """Stop a rule (no approval needed: it only stops forwarding). Resuming asks for approval again."""
    rule, account = _load_rule(context, rule_id)
    if rule.approval_id or rule.task_id:
        _retire(repository, context, rule)  # type: ignore[arg-type]
    now = utc_now()
    rule = rule.model_copy(update={"status": "paused", "approved_hash": None, "approval_id": None, "task_id": None, "updated_at": now})
    with open_automation_store(context) as store:
        store.save_rule(rule)
    _audit(repository, context, "mail.rule.paused", rule.id, metadata={"account_id": account.id})
    return _rule_view(repository, context, rule)


@router.post("/rules/{rule_id}/resume", response_model=RuleResult)
async def resume_rule(rule_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> RuleResult:
    """Ask for approval to forward again with this rule (it starts from mail that arrives after)."""
    rule, account = _load_rule(context, rule_id)
    if rule.status == "active":
        raise HTTPException(status_code=409, detail="This rule is already forwarding.")
    problem = forward_problem(rule.forward_to, account.address)
    if problem:
        raise HTTPException(status_code=422, detail=f"{problem} Edit the rule first.")
    rule = _ask_for_rule(repository, context, account, rule)
    view = _rule_view(repository, context, rule)
    _audit(repository, context, "mail.rule.resume_requested", rule.id, metadata={"account_id": account.id, "approval_id": rule.approval_id})
    return RuleResult(rule=view, approval=view.approval)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: str, context: TenantContext = Depends(tenant_context), repository: AnumRepository = Depends(repository_context)) -> Response:
    """Remove a rule and withdraw its approval if one is waiting."""
    rule, account = _load_rule(context, rule_id)
    if rule.approval_id or rule.task_id:
        _retire(repository, context, rule)  # type: ignore[arg-type]
    with open_automation_store(context) as store:
        store.delete_rule(rule.id)
    _audit(repository, context, "mail.rule.deleted", rule.id, metadata={"account_id": account.id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- unread counts for the sidebar ----------------------------------------------------------------


@router.get("/unread", response_model=UnreadView)
async def unread(context: TenantContext = Depends(tenant_context)) -> UnreadView:
    """The inbox unread counts the background sync stored for the caller's mailboxes (no mail server call)."""
    _require(context, Permission.EMAIL_READ, READ_DENIED)
    cfg = mail_settings
    running = cfg.sync_poll_seconds > 0
    if not (cfg.enabled and cfg.configured and tenant_allowed(context, cfg)):
        return UnreadView(enabled=False, sync_running=running, total=0, checked_at=None, mailboxes=[])
    accounts = accounts_for(context)
    with open_automation_store(context) as store:
        states = store.sync_for([a.id for a in accounts])
    rows = []
    for account in accounts:
        state = states.get(account.id)
        rows.append(UnreadMailbox(
            account_id=account.id, address=account.address, access=account.access, unread=state.inbox_unread if state else None,
            checked_at=_iso(state.checked_at) if state else None, status=state.status if state else "pending",
        ))
    checked = [s.checked_at for s in states.values() if s.checked_at]
    return UnreadView(
        enabled=True, sync_running=running, total=sum(row.unread or 0 for row in rows),
        checked_at=_iso(max(checked)) if checked else None, mailboxes=rows,
    )

